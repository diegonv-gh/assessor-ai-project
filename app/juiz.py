"""
Juiz de fidelidade. Na ordem desta aula ele fala por último.
Quem decide é o JEV: choice e noul, sem redação.
A frase fixa, quando entra, é deste arquivo.
"""
import json
import urllib.error
import urllib.request

from app.config import TYPESAFE_API_KEY

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODELO = "jev-latest"
LIMIAR = 0.70
FRASE_SEGURA = "Não tenho evidência para afirmar isso."

REGRAS = (
    "A pergunta do usuário é a fonte da intenção e dos dados solicitados. Avalie as afirmações da resposta e a correspondência material dos argumentos da proposta com o pedido e com as consultas; rejeite divergências e fatos adicionados sem fonte. "
    "Não exija que a resposta repita todo o contexto: avalie somente as afirmações que ela faz. Em propostas, ignore argumentos opcionais com valor null e metadados de protocolo como status e operation. Avalie os argumentos com valor que alteram materialmente a operação. "
    "A proposta não é prova independente da intenção do usuário: compare source_text com a pergunta original. Aceite normalizações semânticas necessárias ao contrato das tools quando forem inequívocas, como type_name=EXPENSES para um pedido explícito de despesa. "
    "Uma proposta com status pending significa que a operação foi preparada, mas ainda não foi gravada; dizer isso é fiel. Nunca trate a proposta como gravação concluída. Para transações em USD, compare amount com o valor original em dólares informado pelo usuário; a conversão para BRL ocorre somente na tool de persistência após a aprovação. Para transações em BRL, compare amount ao valor em reais solicitado. "
    "Somas, contagens, datas convertidas para o horário de Brasília e "
    "reformatação de valores tirados da evidência contam como apoiada. "
    "Esclarecimento, saudação, oferta de ajuda, sugestão de próximo passo "
    "e ausência de registro contam como apoiada. "
    "Opinião do usuário pode ser citada como fala dele. "
    "A moeda de cada valor é a que aparece no source_text ou na fala do usuário. "
    "Moeda trocada, valores de moedas diferentes somados num total só, "
    "opinião dita como fato do sistema, número, data, telefone ou regra "
    "que não saiam da evidência contam como sem_apoio."
)


def _texto(mensagem) -> str:
    conteudo = mensagem.get("content", "") if isinstance(mensagem, dict) else getattr(mensagem, "content", "")
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        pedacos = []
        for parte in conteudo:
            if isinstance(parte, str):
                pedacos.append(parte)
            elif isinstance(parte, dict):
                pedacos.append(str(parte.get("text") or ""))
        return "\n".join(p for p in pedacos if p)
    return str(conteudo or "")


def _coleta(estado):
    pergunta = ""
    resposta = ""
    evidencias = []
    for mensagem in estado["messages"]:
        tipo = mensagem.get("role", "") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        texto = _texto(mensagem).strip()
        if tipo in ("human", "user") and texto:
            pergunta = texto
            resposta = ""
            evidencias = []
        elif tipo == "tool" and texto:
            nome = (mensagem.get("name", "") if isinstance(mensagem, dict) else getattr(mensagem, "name", "")) or "tool"
            evidencias.append(f"[{nome}]\n{texto}")
        elif tipo in ("ai", "assistant"):
            chamadas = mensagem.get("tool_calls") if isinstance(mensagem, dict) else getattr(mensagem, "tool_calls", None)
            for chamada in chamadas or []:
                argumentos = json.dumps(chamada.get("args", {}), ensure_ascii=False)
                evidencias.append(f"[chamada {chamada.get('name')}]\n{argumentos}")
            if texto:
                resposta = texto
    evidencias_agente = estado.get("evidencia_turno", "")
    if evidencias_agente:
        evidencias.append(f"[evidências do agente financeiro]\n{evidencias_agente}")
    evidencia = "\n\n".join(evidencias).strip()
    if not evidencia:
        evidencia = "(nenhuma tool devolveu conteúdo neste turno)"
    return pergunta, resposta, evidencia[-6000:]


def _publicar(texto, rotulo, resposta_original):
    print(f"[juiz] {rotulo}")
    if texto.strip() == (resposta_original or "").strip():
        return {"agentes_chamados": [f"juiz:{rotulo}"], "juiz_aprovou": False}
    return {
        "messages": [{"role": "assistant", "content": texto.strip()}],
        "agentes_chamados": [f"juiz:{rotulo}"],
        "juiz_aprovou": False,
    }


def _julgar_jev(pergunta, resposta, evidencia, propostas=None):
    if not TYPESAFE_API_KEY:
        return _publicar(FRASE_SEGURA, "sem_chave", resposta)
    corpo = json.dumps({
        "model": JEV_MODELO,
        "state": {
            "pergunta": pergunta,
            "resposta": resposta,
            "propostas": propostas or [],
            "evidencia": evidencia,
            "regras": REGRAS,
        },
        "questions": {
            "veredito": {
                "type": "choice",
                "instructions": "A resposta é fiel ao que afirma e cada argumento material preenchido da proposta corresponde ao pedido e às evidências? Ignore valores null, metadados de protocolo e normalizações semânticas inequívocas. Rejeite divergências ou valores, datas, tipos, identificadores e alterações materiais sem sustentação.",
                "criteria": {
                    "apoiada": "as afirmações e os argumentos materiais preenchidos correspondem ao pedido e às evidências, ignorando campos null, metadados e normalizações semânticas inequívocas",
                    "parcial": "há divergência ou afirmação material sem apoio, mas o restante corresponde ao pedido e às evidências",
                    "sem_apoio": "a resposta ou um argumento material contradiz o pedido ou introduz dado sem evidência",
                },
            },
            "apoiada": {
                "type": "noul",
                "instructions": (
                    "Seguindo as regras, avalie apenas as afirmações da resposta e os argumentos materiais preenchidos das propostas. "
                    "Ignore argumentos null, metadados e normalizações semânticas inequívocas. Cada valor, moeda, data e fato afirmado "
                    "deve corresponder à fala do usuário ou a evidências de consulta; uma proposta pendente não significa que houve gravação."
                ),
            },
        },
    }).encode()
    pedido = urllib.request.Request(
        JEV_URL,
        data=corpo,
        headers={
            "Authorization": f"Bearer {TYPESAFE_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resp:
            dados = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return _publicar(FRASE_SEGURA, f"erro http {exc.code}", resposta)
    respostas = dados["answers"]
    choice = respostas["veredito"]["choice"]
    noul = float(respostas["apoiada"]["noul"])
    if choice == "apoiada" and noul >= LIMIAR:
        return {**_publicar(resposta, f"apoiada noul={noul:.2f}", resposta), "juiz_aprovou": True}
    rotulo = "apoiada:abaixo_do_limiar" if choice == "apoiada" else choice
    return _publicar(FRASE_SEGURA, f"{rotulo} noul={noul:.2f}", resposta)


def no_juiz(estado: dict) -> dict:
    pergunta, resposta, evidencia = _coleta(estado)
    if not resposta:
        return {"agentes_chamados": ["juiz:sem_resposta"], "juiz_aprovou": False}
    try:
        return _julgar_jev(pergunta, resposta, evidencia, estado.get("pending_writes"))
    except Exception as exc:
        print(f"[juiz] erro {exc}")
        return _publicar(FRASE_SEGURA, "erro", resposta)
