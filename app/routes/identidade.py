from fastapi import APIRouter, Request, Response

from app.identity import obter_ou_criar_user_id


router = APIRouter(tags=["identidade"])


@router.get("/identidade")
def identidade(request: Request, response: Response) -> dict[str, str]:
    return {"user_id": obter_ou_criar_user_id(request, response)}
