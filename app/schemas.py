from typing import Literal

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat"""
    user_id: str = Field(default="usuario_teste", examples=["usuario_teste"])
    session_id: str = Field(..., examples=["id_usuario"])
    pergunta: str = Field(..., min_length=1, examples=["gastei 50 reais no mercado"])


class ChatResponse(BaseModel):
    """O que a API devolve no POST /chat"""
    resposta: str
    agentes_chamados: list[str] = Field(default_factory=list)


class PerfilRequest(BaseModel):
    """Contrato enviado pela tela ``frontend/perfil.html``."""

    user_id: str = Field(..., min_length=1, examples=["usuario_teste"])
    renda_mensal: float = Field(..., gt=0, examples=[4200])
    objetivo: str = Field(..., min_length=1, max_length=120)
    tolerancia_risco: Literal["baixa", "media", "alta"]
    preferencias: str = Field(..., min_length=1)


class PerfilResponse(PerfilRequest):
    """Perfil devolvido depois de ser gravado nos dois índices."""


class SessionResponse(BaseModel):
    """Resposta das rotas de início e encerramento de sessão."""
    session_id: str
    resumo: str | None = None


class SessionUserRequest(BaseModel):
    """Identidade opcional enviada nas rotas de início/encerramento."""

    user_id: str = Field(default="usuario_teste", examples=["usuario_teste"])
