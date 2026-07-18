"""Chat endpoint — POST /chat (US1-US4). See contracts/chat-api.md."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent.service import run_turn

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    phone_number: str = Field(min_length=1)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    reply, conversation_id = await run_turn(
        request.message, request.phone_number, request.conversation_id
    )
    return ChatResponse(reply=reply, conversation_id=conversation_id)
