"""Pydantic v2 models for the Conversation entity.

Each conversation document lives in the ``conversations`` MongoDB
collection and stores the full WhatsApp chat history for AI context.
Conversations are keyed by ``whatsapp_number``.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from models.common import MongoBaseModel, PyObjectId, utc_now


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MessageRole(StrEnum):
    """Role of a message in the conversation."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationIntent(StrEnum):
    """Current detected intent for the conversation."""
    BOOKING = "booking"
    TRACKING = "tracking"
    CANCELLATION = "cancellation"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Embedded sub-document
# ---------------------------------------------------------------------------

class ConversationMessage(BaseModel):
    """A single message within a conversation history.

    Attributes:
        role: Who sent the message (user / assistant / system).
        content: The textual content of the message.
        timestamp: UTC time the message was recorded.
    """

    role: MessageRole = Field(...)
    content: str = Field(..., max_length=5000)
    timestamp: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Core document model
# ---------------------------------------------------------------------------

class ConversationInDB(MongoBaseModel):
    """Full conversation document as persisted in MongoDB.

    Attributes:
        conversation_id: Unique conversation identifier.
        whatsapp_number: The customer's WhatsApp number that owns
            this conversation thread.
        messages: Ordered list of messages exchanged.
        current_intent: AI-detected intent for the active exchange.
        booking_id: Reference to an in-progress booking (if any).
        last_active: UTC time of the most recent activity.
        created_at: UTC creation timestamp.
    """

    conversation_id: str = Field(..., description="Unique conversation identifier")
    whatsapp_number: str = Field(
        ...,
        min_length=7,
        max_length=20,
        description="WhatsApp phone number (international format)",
    )
    messages: list[ConversationMessage] = Field(default_factory=list)
    current_intent: ConversationIntent = Field(default=ConversationIntent.GENERAL)
    booking_id: str | None = Field(
        default=None,
        description="In-progress booking reference (if any)",
    )
    last_active: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
