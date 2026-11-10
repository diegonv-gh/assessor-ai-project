import json
import logging
import operator
import re
from typing import Annotated
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.checkpoint.memory import MemorySaver
from app.agents import (
    _indice_ultima_mensagem_usuario,
    _resultados_tool,
    agenda_app,
    faq_app,
    financeiro_app,
    orquestrador_app,
    router_model,
)
from app.tools.financeiro import add_transaction, update_transaction, saldo_total
from app.tools.agenda import add_event
from app.tools.calendario_google import add_google_event
from app.prompts import ROUTER_PROMPT_COMPLETO
from app.guardrail import (
    anonimizar_entrada,
    guardrail_entrada,
    guardrail_saida,
    limpar_raciocinio,
    normalizar_resposta_usuario,
)
from langchain_core.messages import RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from app.memory import salvar_mensagem
from app.observabilidade import abrir_turno, fechar_turno, status_do_estado
from app.juiz import no_juiz

logger = logging.getLogger(__name__)

# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(MessagesState):
    agentes_chamados:   Annotated[list[str], operator.add]  # acumula entre nós
    rota: str                                  # decisão do roteador
    mapa_pii: dict #tokens -> valores originais gerados na anonimização
    status_turno: str
    evidencia_turno: str
    pending_writes: list[dict]
    juiz_aprovou: bool


# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado, config: RunnableConfig) -> dict:
    saida = router_model.invoke(
        [SystemMessage(content=ROUTER_PROMPT_COMPLETO), *list(estado["messages"])],
        config=config,
    )
    texto = _texto_mensagem(saida)
    rota = None
    for linha in texto.splitlines():
        correspondencia = re.fullmatch(r"\s*ROUTE\s*=\s*(financeiro|agenda|faq)\s*", linha, re.IGNORECASE)
        if correspondencia:
            rota = correspondencia.group(1).casefold()
            break

    if rota is None:
        pergunta = ""
        for mensagem in reversed(estado["messages"]):
            tipo = mensagem.get("role", "") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
            if tipo in ("human", "user"):
                pergunta = _texto_mensagem(mensagem)
                break
        rota = _rota_especialista_por_intencao(pergunta)
        if rota:
            logger.warning("Roteador sem protocolo válido; encaminhando por intenção reconhecível para %s", rota)
            return {"agentes_chamados": ["roteador"], "rota": rota}

        # Mensagem vazia ou protocolo malformado nunca deve encerrar o turno
        # com texto vazio nem expor instruções internas ao usuário.
        resposta = texto.strip()
        if not resposta or "ROUTE=" in resposta:
            resposta = "Não consegui encaminhar sua solicitação agora. Pode reformulá-la?"
        return {
            "agentes_chamados": ["roteador"],
            "rota": "fim",
            "messages": [{"role": "assistant", "content": resposta}],
        }

    return {
        "agentes_chamados": ["roteador"],
        "rota":             rota,
        # Não adiciona nada ao messages — especialista lê histórico limpo
    }


def _texto_mensagem(mensagem) -> str:
    """Extrai texto de mensagens LangChain, inclusive conteúdo multimodal."""
    if isinstance(mensagem, dict):
        conteudo = mensagem.get("content", "")
    else:
        texto = getattr(mensagem, "text", None)
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
        conteudo = getattr(mensagem, "content", "")

    if isinstance(conteudo, str):
        return conteudo.strip()
    if isinstance(conteudo, list):
        partes = []
        for parte in conteudo:
            if isinstance(parte, dict):
                valor = parte.get("text", "")
            else:
                valor = getattr(parte, "text", "")
            if isinstance(valor, str):
                partes.append(valor)
        return "\n".join(partes).strip()
    return ""


def _eh_solicitacao_de_saldo_total(texto: str) -> bool:
    texto = texto.casefold()
    return (
        "saldo" in texto
        and not any(palavra in texto for palavra in ("hoje", "ontem", "dia", "semana", "mês", "mes", "período", "periodo"))
        and any(palavra in texto for palavra in ("total", "momento", "agora", "atual"))
    )


def _parece_operacao_financeira(texto: str) -> bool:
    texto = texto.casefold()
    return any(palavra in texto for palavra in (
        "saldo", "extrato", "gasto", "gastos", "despesa", "receita", "entrada",
        "transação", "transacao", "lançamento", "lancamento", "registrar",
        "adicionar", "atualizar", "corrigir", "excluir", "apagar", "remover",
    ))


def _rota_especialista_por_intencao(texto: str) -> str | None:
    """Fallback restrito para recuperar o encaminhamento quando o protocolo falhar."""
    texto = texto.casefold()
    termos_agenda = (
        "agenda", "evento", "eventos", "compromisso", "compromissos",
        "reunião", "reuniao", "lembrete", "lembretes", "calendário", "calendario",
    )
    if any(termo in texto for termo in termos_agenda):
        return "agenda"

    termos_financeiros = (
        "perfil", "renda", "objetivo", "meta financeira", "investimento",
        "tolerância a risco", "tolerancia a risco", "planejamento financeiro",
        "conversa anterior", "conversas anteriores", "histórico", "historico",
        "memória", "memoria",
    )
    if _parece_operacao_financeira(texto) or any(termo in texto for termo in termos_financeiros):
        return "financeiro"
    return None


def _eh_pedido_de_insercao(texto: str) -> bool:
    texto = texto.casefold()
    if any(palavra in texto for palavra in ("quanto", "qual", "mostre", "listar", "extrato")):
        return False
    return any(palavra in texto for palavra in (
        "gastei", "comprei", "paguei", "ganhei", "recebi", "registrar",
        "registre", "registrei", "lançar", "lancar", "adicionar", "adicionei",
        "anotei", "cadastrei", "lance",
    ))


def _evidencias_tools(mensagens) -> str:
    """Preserva no grafo principal os argumentos e resultados das tools financeiras."""
    evidencias = []
    for mensagem in mensagens:
        tipo = mensagem.get("role", "") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("ai", "assistant"):
            chamadas = mensagem.get("tool_calls", []) if isinstance(mensagem, dict) else getattr(mensagem, "tool_calls", [])
            for chamada in chamadas or []:
                nome = chamada.get("name", "tool")
                argumentos = chamada.get("args", {})
                if nome not in {"add_transaction", "update_transaction", "add_event"}:
                    evidencias.append(f"[chamada {nome}]\n{json.dumps(argumentos, ensure_ascii=False)}")
        elif tipo == "tool":
            nome = mensagem.get("name", "tool") if isinstance(mensagem, dict) else getattr(mensagem, "name", "tool")
            conteudo = mensagem.get("content", "") if isinstance(mensagem, dict) else getattr(mensagem, "content", "")
            if nome not in {"add_transaction", "update_transaction", "add_event"}:
                evidencias.append(f"[{nome}]\n{_texto_mensagem(mensagem) or conteudo}")
    return "\n\n".join(evidencias)


def _json_especialista_valido(texto: str) -> bool:
    texto = limpar_raciocinio(texto).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        valor = json.loads(texto)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(valor, dict) and isinstance(valor.get("resposta"), str)


def _extrair_json_especialista(texto: str) -> dict | None:
    texto = limpar_raciocinio(texto).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        valor = json.loads(texto)
    except (TypeError, json.JSONDecodeError):
        inicio = texto.find("{")
        fim = texto.rfind("}")
        if inicio < 0 or fim <= inicio:
            return None
        try:
            valor = json.loads(texto[inicio:fim + 1])
        except json.JSONDecodeError:
            return None
    return valor if isinstance(valor, dict) else None


def _formatar_resposta_especialista(texto: str) -> str | None:
    """Converte o contrato interno JSON para uma resposta natural simples."""
    dados = _extrair_json_especialista(texto)
    if not dados or not isinstance(dados.get("resposta"), str):
        return None

    partes = [dados["resposta"].strip()]
    recomendacao = dados.get("recomendacao")
    acompanhamento = dados.get("esclarecer") or dados.get("acompanhamento")
    if isinstance(recomendacao, str) and recomendacao.strip():
        partes.append(recomendacao.strip())
    if isinstance(acompanhamento, str) and acompanhamento.strip():
        partes.append(acompanhamento.strip())
    return normalizar_resposta_usuario(" ".join(parte for parte in partes if parte))


def _ultimo_resultado_tool(mensagens, nome_tool: str) -> dict | None:
    resultados = _resultados_tool(mensagens, nome_tool)
    return resultados[-1] if resultados else None


def _propostas_pendentes(mensagens) -> list[dict]:
    propostas = []
    nomes = {"add_transaction", "update_transaction", "add_event"}
    for mensagem in mensagens:
        nome = mensagem.get("name", "") if isinstance(mensagem, dict) else getattr(mensagem, "name", "")
        if nome in nomes:
            for resultado in _resultados_tool([mensagem], nome):
                if resultado.get("status") == "pending" and resultado.get("operation") == nome:
                    propostas.append(resultado)
    return propostas


def _resposta_propostas(dominio: str, mensagens) -> dict | None:
    propostas = _propostas_pendentes(mensagens)
    if not propostas:
        return None
    return {
        "agentes_chamados": [dominio],
        "pending_writes": propostas,
        "evidencia_turno": _evidencias_tools(mensagens),
        "messages": [{"role": "assistant", "content": json.dumps({
            "dominio": dominio, "intencao": "criar",
            "resposta": "Preparei a operação para avaliação; ainda não foi registrada.",
            "recomendacao": "",
        }, ensure_ascii=False)}],
    }


def no_agenda(estado: Estado, config: RunnableConfig) -> dict:
    """Executa agenda e só confirma consultas/escritas verificadas nas tools."""
    try:
        saida = agenda_app.invoke({"messages": list(estado["messages"])}, config=config)
    except Exception:
        logger.exception("Falha no especialista de agenda")
        return {
            "agentes_chamados": ["agenda"],
            "messages": [{"role": "assistant", "content": json.dumps({
                "dominio": "agenda",
                "intencao": "consultar",
                "resposta": "Não consegui consultar ou alterar a agenda agora.",
                "recomendacao": "Tente novamente em instantes.",
            }, ensure_ascii=False)}],
        }

    mensagens = saida.get("messages", [])
    indice_usuario = _indice_ultima_mensagem_usuario(mensagens)
    mensagens_turno = mensagens[indice_usuario + 1:] if indice_usuario >= 0 else mensagens
    proposta = _resposta_propostas("agenda", mensagens_turno)
    if proposta:
        return proposta
    resultado_consulta = _ultimo_resultado_tool(mensagens_turno, "query_events")
    if resultado_consulta and resultado_consulta.get("status") == "error":
        dados = {
            "dominio": "agenda",
            "intencao": "consultar",
            "resposta": "Não consegui consultar os compromissos, então não posso confirmar disponibilidade.",
            "recomendacao": "Tente novamente em instantes.",
        }
        return {
            "agentes_chamados": ["agenda"],
            "messages": [{"role": "assistant", "content": json.dumps(dados, ensure_ascii=False)}],
        }

    resposta_modelo = ""
    dados_modelo = None
    for mensagem in reversed(mensagens_turno):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo not in ("ai", "assistant"):
            continue
        candidata = _texto_mensagem(mensagem)
        if _json_especialista_valido(candidata):
            resposta_modelo = candidata
            dados_modelo = _extrair_json_especialista(candidata)
            break

    if dados_modelo and dados_modelo.get("intencao") == "criar":
        if dados_modelo.get("esclarecer"):
            dados_modelo.pop("escrita", None)
            dados_modelo["resposta"] = "Ainda não registrei o compromisso."
        else:
            dados_modelo = {
                "dominio": "agenda",
                "intencao": "criar",
                "resposta": "Não consegui confirmar a gravação local; nenhum evento foi confirmado.",
                "recomendacao": "Tente novamente em instantes.",
            }
        return {
            "agentes_chamados": ["agenda"],
            "messages": [{"role": "assistant", "content": json.dumps(dados_modelo, ensure_ascii=False)}],
        }

    if resultado_consulta is None and dados_modelo and dados_modelo.get("intencao") in (
        "consultar", "listar", "disponibilidade", "conflitos"
    ):
        dados_modelo = {
            "dominio": "agenda",
            "intencao": "consultar",
            "resposta": "Não consegui confirmar os compromissos atuais porque a consulta da agenda não foi concluída.",
            "recomendacao": "Tente novamente em instantes.",
        }
        return {
            "agentes_chamados": ["agenda"],
            "messages": [{"role": "assistant", "content": json.dumps(dados_modelo, ensure_ascii=False)}],
        }
    if resposta_modelo:
        return saida
    return {
        "agentes_chamados": ["agenda"],
        "messages": [{"role": "assistant", "content": json.dumps({
            "dominio": "agenda",
            "intencao": "consultar",
            "resposta": "Não consegui confirmar essa operação da agenda.",
            "recomendacao": "Tente novamente em instantes.",
        }, ensure_ascii=False)}],
    }


def no_financeiro(estado: Estado, config: RunnableConfig) -> dict:
    """Executa o especialista e impede uma resposta sem consulta em saldo total."""
    try:
        saida = financeiro_app.invoke(
            {"messages": list(estado["messages"])},
            config=config,
        )
    except Exception:
        logger.exception("Falha no especialista financeiro")
        return {
            "agentes_chamados": ["financeiro"],
            "evidencia_turno": "",
            "messages": [{
                "role": "assistant",
                "content": json.dumps({
                    "dominio": "financeiro",
                    "intencao": "consultar",
                    "resposta": "Não consegui concluir a operação financeira agora.",
                    "recomendacao": "Tente novamente em instantes.",
                }, ensure_ascii=False),
            }],
        }
    mensagens = saida.get("messages", [])
    indice_usuario = _indice_ultima_mensagem_usuario(mensagens)
    mensagens_do_turno = mensagens[indice_usuario + 1:] if indice_usuario >= 0 else mensagens
    proposta = _resposta_propostas("financeiro", mensagens_do_turno)
    if proposta:
        return proposta
    evidencia_turno = _evidencias_tools(mensagens_do_turno)
    chamou_tool = any(
        (mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")) == "tool"
        for mensagem in mensagens_do_turno
    )
    chamou_add_transaction = any(
        (mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")) == "tool"
        and (mensagem.get("name", "") if isinstance(mensagem, dict) else getattr(mensagem, "name", "")) == "add_transaction"
        for mensagem in mensagens_do_turno
    )
    indice_pergunta = _indice_ultima_mensagem_usuario(estado["messages"])
    pergunta = _texto_mensagem(estado["messages"][indice_pergunta]) if indice_pergunta >= 0 else ""
    texto_financeiro = ""
    for mensagem in reversed(mensagens_do_turno):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("ai", "assistant"):
            texto_financeiro = _texto_mensagem(mensagem)
            if texto_financeiro:
                break

    # Saldo total é uma operação determinística. Se o LLM falhar em emitir a
    # chamada da tool, fazemos a consulta explicitamente para nunca responder
    # com um texto operacional/hallucinado como se fosse o resultado.
    if _eh_solicitacao_de_saldo_total(pergunta) and not chamou_tool:
        try:
            resultado = saldo_total.invoke({})
        except Exception:
            resultado = {
                "status": "error",
                "message": "Não foi possível consultar o saldo agora.",
            }
        if resultado.get("status") == "ok":
            valor = resultado["saldo"]
            resposta = (
                "Seu saldo total é R$ "
                f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                + "."
            )
            especialista = {
                "dominio": "financeiro",
                "intencao": "consultar",
                "resposta": resposta,
                "recomendacao": "",
                "indicadores": {
                    "saldo": resultado["saldo"],
                    "total_income": resultado["total_income"],
                    "total_expenses": resultado["total_expenses"],
                },
            }
        else:
            especialista = {
                "dominio": "financeiro",
                "intencao": "consultar",
                "resposta": resultado.get("message", "Não foi possível consultar o saldo agora."),
                "recomendacao": "Tente novamente em instantes.",
            }
        return {
            "agentes_chamados": ["financeiro"],
            "evidencia_turno": evidencia_turno + "\n\n[saldo_total]\n" + json.dumps(resultado, ensure_ascii=False),
            "messages": [{"role": "assistant", "content": json.dumps(especialista, ensure_ascii=False)}],
        }

    if _eh_pedido_de_insercao(pergunta) or chamou_add_transaction:
        dados = _extrair_json_especialista(texto_financeiro)
        if not dados or not dados.get("esclarecer"):
            dados = {"dominio": "financeiro", "intencao": "inserir",
                     "resposta": "Não consegui preparar a transação; nenhum registro foi feito.",
                     "recomendacao": "Tente novamente."}
        else:
            dados.pop("escrita", None)
            dados["resposta"] = "Ainda não registrei a transação."
        return {"agentes_chamados": ["financeiro"], "evidencia_turno": evidencia_turno,
                "messages": [{"role": "assistant", "content": json.dumps(dados, ensure_ascii=False)}]}

    # Uma resposta do modelo que afirma inserção sem sucesso confirmado da tool
    # não pode ser apresentada como gravação concluída.
    dados_financeiros = _extrair_json_especialista(texto_financeiro)
    if dados_financeiros and dados_financeiros.get("intencao") == "inserir":
        return {
            "agentes_chamados": ["financeiro"],
            "evidencia_turno": evidencia_turno,
            "messages": [{"role": "assistant", "content": json.dumps({
                "dominio": "financeiro",
                "intencao": "inserir",
                "resposta": "Não consegui confirmar a gravação. Verifique o extrato antes de tentar novamente.",
                "recomendacao": "",
            }, ensure_ascii=False)}],
        }

    # Uma operação financeira não pode terminar com texto de prontidão,
    # instruções internas ou qualquer saída que não seja o JSON contratado.
    # Nesse caso nenhuma escrita é considerada confirmada.
    if _parece_operacao_financeira(pergunta) and texto_financeiro and not _json_especialista_valido(texto_financeiro):
        return {
            "agentes_chamados": ["financeiro"],
            "evidencia_turno": evidencia_turno,
            "messages": [{
                "role": "assistant",
                "content": json.dumps({
                    "dominio": "financeiro",
                    "intencao": "consultar",
                    "resposta": "Não consegui confirmar essa operação no banco de dados.",
                    "recomendacao": "Tente novamente; nenhuma alteração foi confirmada.",
                }, ensure_ascii=False),
            }],
        }

    return {**saida, "evidencia_turno": evidencia_turno}


def no_orquestrador(estado: Estado, config: RunnableConfig) -> dict:
    # O estado do LangGraph contém o histórico de vários turnos. Portanto,
    # procurar simplesmente o último AIMessage pode reutilizar a resposta de
    # uma pergunta anterior quando o especialista atual falhar. Delimitamos o
    # turno pela última mensagem do usuário e só aceitamos uma resposta gerada
    # depois dela.
    mensagens = estado["messages"]
    indice_usuario = -1
    for indice, mensagem in enumerate(mensagens):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("human", "user"):
            indice_usuario = indice

    especialista_texto = ""
    for mensagem in reversed(mensagens[indice_usuario + 1:]):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("ai", "assistant"):
            especialista_texto = _texto_mensagem(mensagem)
            if especialista_texto:
                break

    if not especialista_texto:
        raise RuntimeError("O agente especialista não produziu um resultado textual.")

    especialista_texto = limpar_raciocinio(especialista_texto)
    # O JSON do especialista é um contrato interno. Quando ele está válido,
    # formatamos localmente para eliminar de forma determinística chaves,
    # valores, rótulos e Markdown. O LLM fica apenas como fallback para
    # respostas antigas que ainda não estejam no contrato.
    resposta_orquestrada = _formatar_resposta_especialista(especialista_texto)
    if resposta_orquestrada is None:
        saida = orquestrador_app.invoke({
            "messages": [
                {
                    "role": "user",
                    "content": f"ESPECIALISTA_JSON:\n{especialista_texto}",
                }
            ]
        }, config=config)
        resposta_orquestrada = normalizar_resposta_usuario(_texto_mensagem(saida["messages"][-1]))
    if not resposta_orquestrada:
        raise RuntimeError("O orquestrador não produziu uma resposta textual.")
    return {
        "agentes_chamados": [estado["rota"], "orquestrador"],
        "messages":         [{"role": "assistant", "content": resposta_orquestrada}],
    }


def no_efetivar_escritas(estado: Estado) -> dict:
    propostas = estado.get("pending_writes") or []
    if not propostas:
        return {"pending_writes": []}
    if not estado.get("juiz_aprovou"):
        resposta = _texto_mensagem(estado["messages"][-1])
        return {"pending_writes": [], "messages": [{"role": "assistant", "content": resposta + " Nenhuma proposta foi gravada."}]}

    executores = {"add_transaction": add_transaction, "update_transaction": update_transaction, "add_event": add_event}
    respostas = []
    resultados = []
    for proposta in propostas:
        nome = proposta.get("operation")
        executor = executores.get(nome)
        try:
            resultado = executor.invoke(proposta["arguments"]) if executor else {"status": "error"}
        except Exception:
            logger.exception("Falha ao executar a operação aprovada %s", nome)
            resultado = {"status": "error", "code": "execution_error"}
        if not isinstance(resultado, dict):
            resultado = {"status": "error", "code": "invalid_result"}
        resultados.append({"operation": nome, "result": resultado})
        if resultado.get("status") != "ok" or resultado.get("id") is None:
            mensagem = resultado.get("message") or "Não consegui confirmar a gravação. Confira os registros antes de tentar novamente."
            respostas.append(mensagem)
            continue
        if nome == "add_event":
            try:
                google = add_google_event.invoke({"title": resultado["title"], "start_time": resultado["start_time"],
                    "end_time": resultado["end_time"], "location": resultado.get("location"),
                    "description": resultado.get("notes")})
            except Exception:
                logger.exception("Falha ao sincronizar o evento aprovado com o Google")
                google = {"status": "error"}
            resultados.append({"operation": "add_google_event", "result": google})
            if isinstance(google, dict) and google.get("status") == "ok" and google.get("id"):
                respostas.append(f"Registrei '{resultado['title']}' na agenda local e no Google Calendar.")
            else:
                respostas.append(f"Registrei '{resultado['title']}' na agenda local, mas não confirmei a criação no Google Calendar. Confira o calendário antes de tentar novamente.")
        elif nome == "add_transaction":
            valor = f"{resultado['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            conversao = resultado.get("conversion")
            detalhe = ""
            if conversao:
                original = f"{conversao['amount_original']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                detalhe = f", convertidos de US$ {original} pela taxa fixa de R$ 5,40 por dólar"
            respostas.append(f"Registrei R$ {valor}{detalhe}.")
        else:
            respostas.append(f"Atualizei a transação de ID {resultado['id']}.")
    return {"messages": [{"role": "assistant", "content": " ".join(respostas)}],
            "pending_writes": [], "juiz_aprovou": False,
            "evidencia_turno": estado.get("evidencia_turno", "") + "\n[resultados das gravações]\n" + json.dumps(resultados, ensure_ascii=False),
            "agentes_chamados": ["persistencia_pos_juiz"]}


# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    rota = estado.get("rota", "fim")

    if rota in ("financeiro", "agenda", "faq"):
        return rota

    return "fim"

def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    return "fim" if estado["rota"] == "fim" else "roteador"

# ==============================================================================
# FUNÇÕES DE GUARDRAIL
# ==============================================================================

def no_guardrail_entrada(estado: Estado, config: RunnableConfig) -> dict:
    """Executa o guardrail de entrada e decide se o fluxo continua ou é bloqueado."""

    puser = estado["messages"][-1]

    mensagem_anonimizada, mapa_pii = anonimizar_entrada(_texto_mensagem(puser))
    entrada = guardrail_entrada(mensagem_anonimizada)

    if entrada["bloqueado"]:
        return {
            "agentes_chamados": ["guardrail_entrada"],
            "rota": "fim",
            "status_turno": "bloqueado",
            "messages": [
                {
                    "role": "assistant",
                    "content": entrada["mensagem"],
                }
            ],
        }

    configuravel = (config or {}).get("configurable", {})
    session_id = configuravel.get("thread_id")
    user_id = configuravel.get("user_id")
    if session_id and user_id:
        # A pergunta só é persistida depois da anonimização.
        salvar_mensagem(
            session_id,
            "user",
            mensagem_anonimizada,
            user_id=user_id,
        )

    return {
        "agentes_chamados": ["guardrail_entrada"],
        "rota": "roteador",
        "status_turno": "ok",
        "evidencia_turno": "",
        "mapa_pii": mapa_pii,
        "messages": [
            RemoveMessage(id=puser.id),
            {
                "role": "human",
                "content": mensagem_anonimizada,
            },
        ],
    }
    
def no_guardrail_saida(estado: Estado) -> dict:
    """Executa o guardrail de saída e devolve a resposta final (analisada e desanonimizada)."""
    resultado = guardrail_saida(_texto_mensagem(estado["messages"][-1]), estado["mapa_pii"])

    if resultado["bloqueado"]:
        return {
            "messages": [{"role": "assistant", "content": resultado["mensagem"]}],
            "pending_writes": [],
        }

    mensagem_atual = estado["messages"][-1]
    return {
        "messages": [
            RemoveMessage(id=mensagem_atual.id),
            {"role": "assistant", "content": resultado["conteudo"]},
        ],
    }


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro",   no_financeiro)
grafo.add_node("agenda",       no_agenda)
grafo.add_node("faq",          faq_app)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("guardrail_saida", no_guardrail_saida)
grafo.add_node("juiz", no_juiz)
grafo.add_node("efetivar_escritas", no_efetivar_escritas)

grafo.set_entry_point("guardrail_entrada")

grafo.add_conditional_edges(
    "guardrail_entrada",
    decidir_pos_guardrail_entrada,
    {
        "roteador": "roteador",
        "fim":      END,       # bloqueado pelo guardrail de entrada
    },
)

grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "agenda":     "agenda",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro",      "orquestrador")
grafo.add_edge("agenda",          "orquestrador")
grafo.add_edge("orquestrador",    "guardrail_saida")
grafo.add_edge("guardrail_saida", "juiz")
grafo.add_edge("faq",             "juiz")
grafo.add_edge("juiz", "efetivar_escritas")
grafo.add_edge("efetivar_escritas", END)

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)

# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo_assessor_detalhado(
    pergunta_usuario: str,
    session_id: str,
    user_id: str = "usuario_teste",
) -> tuple[str, list[str]]:
    estado_inicial = {
        "messages": [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota": "",
        "mapa_pii": {},
        "session_id": session_id,
        "status_turno": "ok",
        "evidencia_turno": "",
        "pending_writes": [],
        "juiz_aprovou": False,
    }

    turno_id = abrir_turno()
    status = "erro"
    rota = ""
    try:
        estado_final = fluxo_agentes.invoke(
            estado_inicial,
            config={
                "configurable": {
                    "thread_id": session_id,
                    "user_id": user_id,
                }
            },
        )

        resposta_bruta = _texto_mensagem(estado_final["messages"][-1])
        resposta = _formatar_resposta_especialista(resposta_bruta) or normalizar_resposta_usuario(resposta_bruta)
        # A entrada já foi persistida de forma anonimizada no guardrail de entrada.
        salvar_mensagem(
            session_id,
            "assistant",
            resposta,
            user_id=user_id,
        )

        status = status_do_estado(estado_final)
        rota = estado_final.get("rota") or ""
        return resposta, estado_final["agentes_chamados"]
    finally:
        fechar_turno(turno_id, session_id=session_id, rota=rota, status=status)


def executar_fluxo_assessor(
    pergunta_usuario: str,
    session_id: str,
    user_id: str = "usuario_teste",
) -> str:
    """Mantém a API antiga, devolvendo somente o texto da resposta."""
    resposta, _ = executar_fluxo_assessor_detalhado(
        pergunta_usuario,
        session_id,
        user_id=user_id,
    )
    return resposta
