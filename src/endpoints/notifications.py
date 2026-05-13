"""Push notification token registration endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from services.push_notifications import register_token, remove_token

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class TokenRequest(BaseModel):
    token: str


@router.post("/register")
async def register_push_token(body: TokenRequest):
    """Register an Expo push token for an admin device."""
    await register_token(body.token)
    return {"status": "registered", "token": body.token}


@router.post("/unregister")
async def unregister_push_token(body: TokenRequest):
    """Remove a previously registered push token."""
    await remove_token(body.token)
    return {"status": "unregistered", "token": body.token}
