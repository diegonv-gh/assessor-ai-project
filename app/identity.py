import uuid

from fastapi import Request, Response


COOKIE_USER_ID = "assessor_user_id"


def obter_ou_criar_user_id(request: Request, response: Response) -> str:
    user_id = request.cookies.get(COOKIE_USER_ID)
    if not user_id:
        user_id = str(uuid.uuid4())
        response.set_cookie(key=COOKIE_USER_ID, value=user_id, httponly=False, samesite="lax")
    return user_id
