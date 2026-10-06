"""Rotas do ciclo de vida das sessões de conversa."""

import uuid

from fastapi import APIRouter, Query

from app.memory import (
    encerrar_sessao,
    iniciar_sessao,
    recuperar_mensagens_sessao,
)
from app.schemas import SessionResponse, SessionUserRequest


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
def criar(requisicao: SessionUserRequest) -> SessionResponse:
    """Cria uma sessão e devolve o identificador que o cliente deve reutilizar."""
    session_id = str(uuid.uuid4())
    iniciar_sessao(session_id, user_id=requisicao.user_id)
    return SessionResponse(session_id=session_id, resumo=None)


@router.delete("/{session_id}", response_model=SessionResponse)
def encerrar_rest(
    session_id: str,
    user_id: str = Query(..., min_length=1),
) -> SessionResponse:
    """Encerra uma sessão pelo endpoint REST definido no guia."""
    resumo = encerrar_sessao(session_id, user_id=user_id)
    return SessionResponse(session_id=session_id, resumo=resumo or None)


@router.get("/{session_id}/historico")
def historico(
    session_id: str,
    user_id: str = Query(..., min_length=1),
) -> list[dict]:
    """Devolve as mensagens da sessão pertencente ao usuário informado."""
    return recuperar_mensagens_sessao(session_id, user_id)


@router.post("/{session_id}/iniciar", response_model=SessionResponse)
def iniciar(session_id: str, requisicao: SessionUserRequest | None = None) -> SessionResponse:
    """
    Abre uma sessão explicitamente.

    Opcional na prática — salvar_mensagem() já abre a sessão sozinho na primeira
    mensagem. Existe para o caso de você querer registrar o acesso mesmo que o
    usuário não chegue a perguntar nada.
    """
    user_id = requisicao.user_id if requisicao else "usuario_teste"
    iniciar_sessao(session_id, user_id=user_id)
    return SessionResponse(session_id=session_id, resumo=None)


@router.post("/{session_id}/encerrar", response_model=SessionResponse)
def encerrar(session_id: str, requisicao: SessionUserRequest | None = None) -> SessionResponse:
    """
    Encerra a sessão: gera o resumo via LLM e grava no documento.

    É este resumo que a tool `buscar_historico` vai encontrar depois. Sem passar
    por aqui, a conversa fica guardada no Mongo mas invisível para a memória de
    longo prazo — porque recuperar_historico() filtra por resumo não-vazio.

    Devolve resumo=None (e não erro) quando não havia nada a encerrar: sessão
    inexistente ou sem nenhuma mensagem. Encerrar duas vezes é inofensivo.

    Atenção ao custo: esta rota faz uma chamada de LLM para gerar o resumo.
    Não a acione a cada mensagem — só ao fim da conversa.
    """
    user_id = requisicao.user_id if requisicao else None
    resumo = encerrar_sessao(session_id, user_id=user_id)
    return SessionResponse(session_id=session_id, resumo=resumo or None)
