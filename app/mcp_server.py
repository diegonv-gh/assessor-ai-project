"""MCP stdio server exposing the Assessor's finance and local calendar tools."""

import logging
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from app.tools.financeiro import (
    add_transaction as _add_transaction,
    saldo_diario as _saldo_diario,
    saldo_total as _saldo_total,
    search_transactions as _search_transactions,
    update_transaction as _update_transaction,
)
from app.tools.agenda import add_event as _add_event, query_events as _query_events

logger = logging.getLogger("assessor.mcp")

mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions=(
        "Ferramentas financeiras e da agenda PostgreSQL do Assessor. Datas de filtro são locais em "
        "America/Sao_Paulo (YYYY-MM-DD); timestamps de transações usam ISO 8601. "
        "Timestamps de eventos usam ISO 8601 com fuso. Saldo exclui transferências. "
        "As tools add_event e query_events operam somente na agenda local PostgreSQL."
    ),
)

LEITURA = ToolAnnotations(readOnlyHint=True)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


@mcp.tool(
    name="query_transactions",
    title="Consultar transações",
    description=(
        "Lista transações com filtros opcionais por texto, intervalo de datas local "
        "(America/Sao_Paulo), tipo (INCOME, EXPENSES ou TRANSFER), categoria, forma "
        "de pagamento e limite de resultados (1 a 100; padrão 10). Datas em YYYY-MM-DD."
    ),
    annotations=LEITURA,
    structured_output=True,
)
def query_transactions(
    query: Annotated[Optional[str], Field(description="Texto parcial para procurar na origem ou descrição.")] = None,
    start_date: Annotated[Optional[str], Field(description="Data local inicial inclusiva, YYYY-MM-DD.")] = None,
    end_date: Annotated[Optional[str], Field(description="Data local final inclusiva, YYYY-MM-DD.")] = None,
    type_name: Annotated[Optional[str], Field(description="Tipo: INCOME, EXPENSES ou TRANSFER.")] = None,
    category_name: Annotated[Optional[str], Field(description="Nome da categoria, por exemplo comida ou transporte.")] = None,
    payment_method: Annotated[Optional[str], Field(description="Forma de pagamento.")] = None,
    limit: Annotated[Optional[int], Field(description="Máximo de resultados, entre 1 e 100.")] = 10,
) -> dict[str, Any]:
    """Consulta as transações usando os filtros fornecidos."""
    return _search_transactions.invoke(
        {
            "query": query,
            "start_date": start_date,
            "end_date": end_date,
            "type_name": type_name,
            "category_name": category_name,
            "payment_method": payment_method,
            "limit": limit,
        }
    )


@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Calcula as receitas menos despesas de todo o histórico; transferências são excluídas.",
    annotations=LEITURA,
    structured_output=True,
)
def total_balance() -> dict[str, Any]:
    """Calcula o saldo total sem contar transferências."""
    return _saldo_total.invoke({})


@mcp.tool(
    name="daily_balance",
    title="Saldo diário ou por período",
    description=(
        "Calcula receitas, despesas e saldo de um dia ou período. Informe first_date e, "
        "opcionalmente, last_date em YYYY-MM-DD; as datas são dias locais de America/Sao_Paulo. "
        "Transferências são excluídas."
    ),
    annotations=LEITURA,
    structured_output=True,
)
def daily_balance(
    first_date: Annotated[str, Field(description="Data inicial local, YYYY-MM-DD.")],
    last_date: Annotated[Optional[str], Field(description="Data final local inclusiva, YYYY-MM-DD.")] = None,
) -> dict[str, Any]:
    """Calcula o saldo do dia ou período informado."""
    return _saldo_diario.invoke({"first_date": first_date, "last_date": last_date})


@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description=(
        "Registra uma transação financeira. Valor positivo e texto de origem são obrigatórios. "
        "Use nomes de tipo e categoria, sem IDs. occurred_at, se informado, deve ser timestamp ISO 8601."
    ),
    annotations=ESCRITA,
    structured_output=True,
)
def add_transaction(
    amount: Annotated[float, Field(description="Valor positivo da transação.")],
    source_text: Annotated[str, Field(description="Texto original que identifica a transação.")],
    occurred_at: Annotated[Optional[str], Field(description="Timestamp ISO 8601; omitido, usa o horário atual.")] = None,
    type_name: Annotated[Optional[str], Field(description="Tipo: INCOME, EXPENSES ou TRANSFER; omitido, usa despesa.")] = None,
    category_name: Annotated[Optional[str], Field(description="Categoria pelo nome, por exemplo comida ou moradia.")] = None,
    description: Annotated[Optional[str], Field(description="Descrição opcional.")] = None,
    payment_method: Annotated[Optional[str], Field(description="Forma de pagamento opcional.")] = None,
) -> dict[str, Any]:
    """Registra uma transação reutilizando a tool financeira existente."""
    return _add_transaction.invoke(
        {
            "amount": amount,
            "source_text": source_text,
            "occurred_at": occurred_at,
            "type_name": type_name,
            "category_name": category_name,
            "description": description,
            "payment_method": payment_method,
        }
    )


@mcp.tool(
    name="update_transaction",
    title="Atualizar transação",
    description=(
        "Atualiza uma transação identificada pelo id da transação ou por match_text junto com "
        "date_local. Forneça ao menos um campo para alterar. Tipos e categorias usam nomes, não IDs. "
        "Datas locais em YYYY-MM-DD; occurred_at em timestamp ISO 8601."
    ),
    annotations=ESCRITA,
    structured_output=True,
)
def update_transaction(
    id: Annotated[Optional[int], Field(description="ID da transação; alternativa à busca por texto e data.")] = None,
    match_text: Annotated[Optional[str], Field(description="Texto para identificar a transação se id não for informado.")] = None,
    date_local: Annotated[Optional[str], Field(description="Data local da transação, YYYY-MM-DD; use com match_text.")] = None,
    amount: Annotated[Optional[float], Field(description="Novo valor.")] = None,
    type_name: Annotated[Optional[str], Field(description="Novo tipo: INCOME, EXPENSES ou TRANSFER.")] = None,
    category_name: Annotated[Optional[str], Field(description="Nova categoria pelo nome.")] = None,
    description: Annotated[Optional[str], Field(description="Nova descrição.")] = None,
    payment_method: Annotated[Optional[str], Field(description="Nova forma de pagamento.")] = None,
    occurred_at: Annotated[Optional[str], Field(description="Novo timestamp ISO 8601.")] = None,
) -> dict[str, Any]:
    """Atualiza uma transação reutilizando a tool financeira existente."""
    return _update_transaction.invoke(
        {
            "id": id,
            "match_text": match_text,
            "date_local": date_local,
            "amount": amount,
            "type_name": type_name,
            "category_name": category_name,
            "description": description,
            "payment_method": payment_method,
            "occurred_at": occurred_at,
        }
    )


@mcp.tool(
    name="query_events",
    title="Consultar agenda local",
    description=(
        "Consulta eventos do PostgreSQL por texto e datas locais de America/Sao_Paulo. "
        "Use date_local para um dia ou date_from_local/date_to_local para um intervalo "
        "inclusivo. O limite padrão é 20, máximo 200."
    ),
    annotations=LEITURA,
    structured_output=True,
)
def query_events(
    text: Annotated[Optional[str], Field(description="Texto parcial em título, local, notas ou pedido original.")] = None,
    date_local: Annotated[Optional[str], Field(description="Dia local YYYY-MM-DD.")] = None,
    date_from_local: Annotated[Optional[str], Field(description="Data inicial local inclusiva YYYY-MM-DD.")] = None,
    date_to_local: Annotated[Optional[str], Field(description="Data final local inclusiva YYYY-MM-DD.")] = None,
    limit: Annotated[int, Field(description="Máximo de resultados entre 1 e 200.")] = 20,
) -> dict[str, Any]:
    """Consulta os compromissos da agenda PostgreSQL do Assessor."""
    return _query_events.invoke({
        "text": text,
        "date_local": date_local,
        "date_from_local": date_from_local,
        "date_to_local": date_to_local,
        "limit": limit,
    })


@mcp.tool(
    name="add_event",
    title="Registrar evento na agenda local",
    description=(
        "Registra um evento no PostgreSQL após validar que não há sobreposição. "
        "Título, texto original e início/fim ISO 8601 com fuso são obrigatórios. "
        "Esta tool grava na agenda local; não cria no Google Calendar."
    ),
    annotations=ESCRITA,
    structured_output=True,
)
def add_event(
    title: Annotated[str, Field(description="Título do evento.")],
    source_text: Annotated[str, Field(description="Pedido original do usuário.")],
    start_time: Annotated[str, Field(description="Início ISO 8601 com offset de fuso horário.")],
    end_time: Annotated[str, Field(description="Fim ISO 8601 com offset; obrigatório, posterior ao início.")],
    location: Annotated[Optional[str], Field(description="Local opcional.")] = None,
    notes: Annotated[Optional[str], Field(description="Observações opcionais.")] = None,
) -> dict[str, Any]:
    """Grava o evento local pela tool de domínio da agenda."""
    return _add_event.invoke({
        "title": title,
        "source_text": source_text,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "notes": notes,
    })


def main() -> None:
    """Run the stdio server after validating its database configuration."""
    from app.config import DATABASE_URL

    if not DATABASE_URL or not DATABASE_URL.strip():
        print("[assessor-financeiro] DATABASE_URL ausente no .env", file=sys.stderr)
        raise SystemExit(1)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
