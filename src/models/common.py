"""Shared Pydantic v2 utilities for MongoDB integration.

Provides:
    - ``PyObjectId``: Annotated type that serialises ``bson.ObjectId`` ↔
      plain ``str`` so Pydantic models work seamlessly with Motor documents.
    - ``MongoBaseModel``: Optional base class with ``id`` field mapped to
      ``_id``.
"""

from datetime import datetime, timezone
from typing import Annotated, Any

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _validate_object_id(v: Any) -> str:
    """Accept an ObjectId or a 24-char hex string and return ``str``."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str) and ObjectId.is_valid(v):
        return v
    raise ValueError(f"Invalid ObjectId: {v}")


PyObjectId = Annotated[str, BeforeValidator(_validate_object_id)]
"""Annotated type: accepts ``bson.ObjectId`` or 24-char hex string,
always stored as ``str`` in the Pydantic model."""


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class MongoBaseModel(BaseModel):
    """Convenience base model that maps MongoDB ``_id`` → ``id``."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
    )

    id: PyObjectId | None = Field(default=None, alias="_id")
