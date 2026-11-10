import ast
import json

from langchain.agents import create_agent
from langchain.agents.middleware import dynamic_prompt

from app.llms import llm_especialista, llm_gemini, llm_rapido
from app.perfil import TOOLS_PERFIL
from app.prompts import (
    FAQ_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    agenda_prompt_atual,
)
from app.tools.agenda import TOOLS_AGENDA
from app.tools.faq import faq_retriever
from app.tools.financeiro import TOOLS
from app.tools.propostas import preparar_add_transaction, preparar_update_transaction, preparar_add_event
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


# O roteador é uma chamada de modelo sem tool binding: ele classifica e emite
# ROUTE=..., enquanto os especialistas executam as tools depois.
router_model = llm_rapido.with_fallbacks([llm_gemini])

financeiro_app = create_agent(
    model=llm_especialista,
    tools=[t for t in TOOLS if t.name not in {"add_transaction", "update_transaction"}]
    + [preparar_add_transaction, preparar_update_transaction] + TOOLS_MEMORIA + TOOLS_PERFIL,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    tools=[t for t in TOOLS_AGENDA if t.name != "add_event"] + [preparar_add_event] + TOOLS_MEMORIA,
    middleware=[_prompt_agenda_dinamico],
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
