"""Tool de memória de longo prazo: consulta conversas anteriores."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.memory import recuperar_historico


@tool
def buscar_historico(busca: str, config: RunnableConfig) -> str:
    """Consulta conversas ANTERIORES do usuário (sessões já encerradas).

    Use SOMENTE quando a resposta depende de algo dito numa conversa passada:
    preferências, decisões ou planos. NÃO use para gastos, saldos, extratos ou
    eventos, pois esses dados devem ser consultados pelas tools do domínio.

    Args:
        busca: substantivo do assunto a procurar nos resumos anteriores.
    """
    configuravel = (config or {}).get("configurable", {})
    user_id = configuravel.get("user_id") or configuravel.get("thread_id")

    if not user_id:
        return "Não foi possível identificar o usuário para buscar o histórico."

    historico = recuperar_historico(user_id, busca=busca, limite=3)
    if not historico:
        return "Nenhuma conversa anterior relevante encontrada."

    linhas = []
    for h in historico:
        data = h["iniciada_em"]
        if hasattr(data, "strftime"):
            data_fmt = data.strftime("%d/%m/%Y")
        else:
            data_fmt = str(data)[:10]
        linhas.append(f"[{data_fmt}] {h['resumo']}")
    return "\n\n".join(linhas)


TOOLS_MEMORIA = [buscar_historico]
