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


class SessionResponse(BaseModel):
    """Resposta das rotas de início e encerramento de sessão."""
    session_id: str
    resumo: str | None = None
