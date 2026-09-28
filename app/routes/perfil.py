from fastapi import APIRouter, HTTPException

from app.perfil import salvar_perfil
from app.schemas import PerfilRequest, PerfilResponse


router = APIRouter(tags=["perfil"])


@router.post("/perfil", response_model=PerfilResponse)
def criar_ou_atualizar_perfil(requisicao: PerfilRequest) -> PerfilResponse:
    """Grava ou substitui o perfil enviado pela tela Perfil."""
    try:
        dados = salvar_perfil(requisicao)
    except Exception as erro:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível salvar o perfil nos bancos configurados.",
        ) from erro
    return PerfilResponse(**dados)
