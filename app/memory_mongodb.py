"""Compatibilidade para imports antigos.

A implementação oficial da memória é ``app.memory``. Este módulo existia
antes da migração para ``user_id`` + Qdrant e mantê-lo como uma segunda
implementação permitia que consumidores antigos gravassem sessões em um
formato diferente do usado pelo grafo atual.
"""

from app.memory import (
    col_sessoes,
    encerrar_sessao,
    iniciar_sessao,
    recuperar_historico,
    recuperar_mensagens,
    salvar_mensagem,
    salvar_turno,
)

__all__ = [
    "col_sessoes",
    "encerrar_sessao",
    "iniciar_sessao",
    "recuperar_historico",
    "recuperar_mensagens",
    "salvar_mensagem",
    "salvar_turno",
]
