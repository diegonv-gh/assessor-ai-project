from fastapi import APIRouter
from app.schemas import ChatRequest, ChatResponse
from app.graph import executar_fluxo_assessor_detalhado

router = APIRouter(tags=["chat"])

@router.post("/chat", response_model=ChatResponse)
def chat(requisition: ChatRequest) -> ChatResponse:
    result, agentes_chamados = executar_fluxo_assessor_detalhado(
        requisition.pergunta,
        requisition.session_id,
        requisition.user_id
    )

    return ChatResponse(
        resposta=result,
        agentes_chamados=agentes_chamados,
    )
