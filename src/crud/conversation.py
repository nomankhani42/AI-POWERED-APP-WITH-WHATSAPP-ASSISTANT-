"""CRUD operations for the ``conversations`` MongoDB collection.

Every function is async and takes the Motor database handle as its
first parameter.

Conversations are keyed by ``whatsapp_number`` and store the full
chat history that the AI agent uses for context.
"""

from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.common import utc_now
from models.conversation import (
    ConversationInDB,
    ConversationIntent,
    ConversationMessage,
    MessageRole,
)

COLLECTION = "conversations"


async def create_or_get_conversation(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
) -> ConversationInDB:
    """Return the active conversation for a WhatsApp number, creating
    one if it doesn't exist.

    This is the entry-point called on every incoming WhatsApp message.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number in international format.

    Returns:
        The existing or newly created conversation document.
    """
    number = whatsapp_number.strip().lstrip("+")
    now = utc_now()

    result = await db[COLLECTION].find_one_and_update(
        {"whatsapp_number": number},
        {
            "$set": {"last_active": now},
            "$setOnInsert": {
                "conversation_id": uuid4().hex[:12],
                "whatsapp_number": number,
                "messages": [],
                "current_intent": ConversationIntent.GENERAL,
                "booking_id": None,
                "created_at": now,
            },
        },
        upsert=True,
        return_document=True,
    )
    return ConversationInDB(**result)


async def append_message(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
    role: MessageRole,
    content: str,
) -> ConversationInDB | None:
    """Append a message to the conversation history.

    Also updates ``last_active`` to the current UTC time.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number identifying the conversation.
        role: Who sent the message (user / assistant / system).
        content: The textual content of the message.

    Returns:
        The updated conversation document, or ``None`` if the
        conversation does not exist.
    """
    number = whatsapp_number.strip().lstrip("+")
    now = utc_now()
    message = ConversationMessage(role=role, content=content, timestamp=now)

    result = await db[COLLECTION].find_one_and_update(
        {"whatsapp_number": number},
        {
            "$push": {"messages": message.model_dump()},
            "$set": {"last_active": now},
        },
        return_document=True,
    )
    return ConversationInDB(**result) if result else None


async def get_conversation_history(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
    last_n: int = 20,
) -> list[ConversationMessage]:
    """Return the last *N* messages from a conversation.

    Uses MongoDB's ``$slice`` projection to fetch only the tail of
    the ``messages`` array, keeping the query efficient even for
    long conversations.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number identifying the conversation.
        last_n: Number of most-recent messages to return.

    Returns:
        List of conversation messages (may be empty if the
        conversation doesn't exist or has no messages).
    """
    number = whatsapp_number.strip().lstrip("+")
    doc = await db[COLLECTION].find_one(
        {"whatsapp_number": number},
        {"messages": {"$slice": -last_n}},
    )
    if not doc or not doc.get("messages"):
        return []
    return [ConversationMessage(**m) for m in doc["messages"]]


async def update_intent(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
    intent: ConversationIntent,
    booking_id: str | None = None,
) -> ConversationInDB | None:
    """Update the current conversation intent and optionally link a
    booking.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number identifying the conversation.
        intent: The new detected intent.
        booking_id: Optional booking reference if a booking flow is
            in progress.

    Returns:
        The updated conversation document, or ``None`` if not found.
    """
    number = whatsapp_number.strip().lstrip("+")
    update: dict[str, Any] = {
        "current_intent": intent,
        "last_active": utc_now(),
    }
    if booking_id is not None:
        update["booking_id"] = booking_id

    result = await db[COLLECTION].find_one_and_update(
        {"whatsapp_number": number},
        {"$set": update},
        return_document=True,
    )
    return ConversationInDB(**result) if result else None


async def clear_conversation(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
) -> bool:
    """Clear the message history and reset intent for a conversation.

    The conversation document itself is preserved (not deleted).

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number identifying the conversation.

    Returns:
        ``True`` if the conversation was found and cleared, ``False``
        otherwise.
    """
    number = whatsapp_number.strip().lstrip("+")
    result = await db[COLLECTION].update_one(
        {"whatsapp_number": number},
        {
            "$set": {
                "messages": [],
                "current_intent": ConversationIntent.GENERAL,
                "booking_id": None,
                "last_active": utc_now(),
            },
        },
    )
    return result.modified_count > 0
