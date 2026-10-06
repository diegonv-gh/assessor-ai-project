import ast
import json
from datetime import datetime

from langchain.agents import create_agent
from langchain.agents.middleware import dynamic_prompt, wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.llms import llm_especialista, llm_gemini, llm_rapido
from app.perfil import TOOLS_PERFIL
from app.prompts import (
    FAQ_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    ROUTER_PROMPT_COMPLETO,
    agenda_prompt_atual,
)
from app.tools.agenda import TOOLS_AGENDA
from app.tools.calendario_google import TOOLS_GOOGLE
from app.tools.faq import faq_retriever
from app.tools.financeiro import TOOLS
from app.tools.memoria import TOOLS_MEMORIA


def _resultados_tool(mensagens, nome_tool: str) -> list[dict]:
    resultados = []
    for mensagem in mensagens:
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        nome = mensagem.get("name", "") if isinstance(mensagem, dict) else getattr(mensagem, "name", "")
        if tipo != "tool" or nome != nome_tool:
            continue
        conteudo = mensagem.get("content", "") if isinstance(mensagem, dict) else getattr(mensagem, "content", "")
        partes = []
        if isinstance(conteudo, dict):
            resultados.append(conteudo)
            continue
        if isinstance(conteudo, list):
            partes = [
                parte.get("text", "") if isinstance(parte, dict) else getattr(parte, "text", "")
                for parte in conteudo
            ]
        elif isinstance(conteudo, str):
            partes = [conteudo]
        for parte in partes:
            if not isinstance(parte, str):
                continue
            try:
                valor = json.loads(parte)
            except json.JSONDecodeError:
                try:
                    valor = ast.literal_eval(parte)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(valor, dict):
                resultados.append(valor)
                break
    return resultados


def _indice_ultima_mensagem_usuario(mensagens) -> int:
    for indice in range(len(mensagens) - 1, -1, -1):
        mensagem = mensagens[indice]
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("human", "user"):
            return indice
    return -1


@dynamic_prompt
def _prompt_agenda_dinamico(request) -> str:
    """Atualiza a data local do sistema antes de cada chamada ao modelo de agenda."""
    return agenda_prompt_atual()


@wrap_tool_call
def _ordenar_gravacoes_agenda(request, handler):
    """Só chama Google após sucesso local e nunca repete uma tentativa incerta."""
    nome = request.tool_call.get("name")
    if nome != "add_google_event":
        return handler(request)

    mensagens = request.state.get("messages", [])
    indice_usuario = _indice_ultima_mensagem_usuario(mensagens)
    mensagens_turno = mensagens[indice_usuario + 1:] if indice_usuario >= 0 else mensagens
    locais = _resultados_tool(mensagens_turno, "add_event")
    evento_local = next(
        (resultado for resultado in reversed(locais) if resultado.get("status") == "ok" and resultado.get("id") is not None),
        None,
    )
    if evento_local is None:
        return ToolMessage(
            name="add_google_event",
            tool_call_id=request.tool_call["id"],
            content=json.dumps({
                "status": "error",
                "code": "google_requires_local_success",
                "message": "Aguarde a confirmação do registro local antes de criar o evento Google.",
            }, ensure_ascii=False),
        )

    tentativas_google = _resultados_tool(mensagens_turno, "add_google_event")
    if any(resultado.get("code") != "google_requires_local_success" for resultado in tentativas_google):
        return ToolMessage(
            name="add_google_event",
            tool_call_id=request.tool_call["id"],
            content=json.dumps({
                "status": "error",
                "code": "google_creation_already_attempted",
                "message": "A criação Google já foi tentada neste turno e não pode ser repetida automaticamente.",
            }, ensure_ascii=False),
        )

    argumentos = request.tool_call.get("args", {})
    titulo_diverge = str(argumentos.get("title") or "").strip() != evento_local.get("title")
    local_diverge = (argumentos.get("location") or None) != (evento_local.get("location") or None)
    descricao_diverge = (argumentos.get("description") or None) != (evento_local.get("notes") or None)
    if titulo_diverge:
        motivo = "o título enviado ao Google difere do título gravado localmente"
    else:
        try:
            inicio_google = datetime.fromisoformat(argumentos["start_time"].replace("Z", "+00:00"))
            fim_google = datetime.fromisoformat(argumentos["end_time"].replace("Z", "+00:00"))
            inicio_local = datetime.fromisoformat(evento_local["start_time"].replace("Z", "+00:00"))
            fim_local = datetime.fromisoformat(evento_local["end_time"].replace("Z", "+00:00"))
            horarios_iguais = inicio_google == inicio_local and fim_google == fim_local
        except (KeyError, TypeError, ValueError):
            horarios_iguais = False
        motivo = "os horários enviados ao Google diferem dos horários gravados localmente"
    if titulo_diverge or local_diverge or descricao_diverge or not horarios_iguais:
        if local_diverge:
            motivo = "o local enviado ao Google difere do local gravado localmente"
        elif descricao_diverge:
            motivo = "a descrição enviada ao Google difere das observações gravadas localmente"
        return ToolMessage(
            name="add_google_event",
            tool_call_id=request.tool_call["id"],
            content=json.dumps({
                "status": "error",
                "code": "google_event_mismatch",
                "message": f"Não criei no Google porque {motivo}.",
            }, ensure_ascii=False),
        )
    return handler(request)


router_memory = MemorySaver()

router_app = create_agent(
    # O roteador usa um protocolo textual (ROUTE=...) e só possui tools de
    # memória. O fallback evita que uma chamada de tool inválida do modelo
    # rápido (por exemplo, tentar chamar "agenda") derrube o turno inteiro.
    model=llm_rapido.with_fallbacks([llm_gemini]),
    tools=TOOLS_MEMORIA,
    system_prompt=ROUTER_PROMPT_COMPLETO,
    checkpointer=router_memory,
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS + TOOLS_MEMORIA + TOOLS_PERFIL,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    tools=TOOLS_AGENDA + TOOLS_GOOGLE + TOOLS_MEMORIA,
    middleware=[_prompt_agenda_dinamico, _ordenar_gravacoes_agenda],
)

orquestrador_app = create_agent(
    model=llm_rapido,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

faq_app = create_agent(
    model=llm_rapido,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT_COMPLETO,
)
