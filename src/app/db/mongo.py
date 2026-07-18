"""MongoDB lifecycle: connect + Beanie init, and clean shutdown."""

from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.core.config import get_settings
from app.db.documents import Booking, Call, CallEvent, InboundMessage, Room

_client: AsyncMongoClient | None = None


async def init_db() -> None:
    """Connect to MongoDB and initialize Beanie document models."""

    global _client
    settings = get_settings()
    _client = AsyncMongoClient(settings.mongodb_uri)
    await init_beanie(
        database=_client[settings.mongodb_db],
        document_models=[Room, Booking, Call, CallEvent, InboundMessage],
    )


async def close_db() -> None:
    """Close the MongoDB client if open."""

    global _client
    if _client is not None:
        await _client.close()
        _client = None
