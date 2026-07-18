"""Greeting endpoint — the walking-skeleton hello world response (US1, FR-002)."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["greeting"])


class GreetingResponse(BaseModel):
    """Payload returned by the greeting endpoint."""

    message: str
    success: bool


@router.get("/", response_model=GreetingResponse)
async def greet() -> GreetingResponse:
    """Return the hello world greeting."""

    return GreetingResponse(message="hello world", success=True)
