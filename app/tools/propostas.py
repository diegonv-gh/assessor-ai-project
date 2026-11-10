"""Operações propostas pelos especialistas, sem efeitos no banco."""
from langchain.tools import tool
from app.tools.financeiro import AddTransactionArgs, UpdateTransactionArgs
from app.tools.agenda import AddEventArgs


def _proposta(operacao: str, argumentos: dict) -> dict:
    return {"status": "pending", "operation": operacao, "arguments": argumentos}


@tool("add_transaction", args_schema=AddTransactionArgs)
def preparar_add_transaction(**kwargs) -> dict:
    """Prepara uma transação para avaliação do juiz, sem gravá-la."""
    return _proposta("add_transaction", kwargs)


@tool("update_transaction", args_schema=UpdateTransactionArgs)
def preparar_update_transaction(**kwargs) -> dict:
    """Prepara uma atualização para avaliação do juiz, sem gravá-la."""
    return _proposta("update_transaction", kwargs)


@tool("add_event", args_schema=AddEventArgs)
def preparar_add_event(**kwargs) -> dict:
    """Prepara um compromisso para avaliação do juiz, sem gravá-lo."""
    return _proposta("add_event", kwargs)
