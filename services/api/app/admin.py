import asyncio
import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def require_admin(request: Request, authorization: str = Header(default="")) -> None:
    expected = f"Bearer {request.app.state.admin_token}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "Нужен токен администратора", headers={"WWW-Authenticate": "Bearer"})


class LoginIn(BaseModel):
    password: str


@router.post("/admin/login")
async def login(payload: LoginIn, request: Request) -> dict:
    if not hmac.compare_digest(payload.password.encode(), request.app.state.admin_password.encode()):
        await asyncio.sleep(1)
        raise HTTPException(401, "Неверный пароль")
    return {"token": request.app.state.admin_token}
