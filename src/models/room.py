"""Pydantic v2 models for the Room entity.

Each room document lives in the ``rooms`` MongoDB collection and
references a parent restaurant via ``hotel_id``.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from models.common import MongoBaseModel, PyObjectId, utc_now


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoomType(StrEnum):
    """Allowed room type values.

    Mapping for restaurant context:
        single  → Small private dining room (2-4 guests)
        double  → Family / medium dining room (6-8 guests)
        suite   → VIP lounge / executive dining (8-12 guests)
        deluxe  → Banquet hall / event space (15-20 guests)
    """
    SINGLE = "single"
    DOUBLE = "double"
    SUITE = "suite"
    DELUXE = "deluxe"


# ---------------------------------------------------------------------------
# Core document model
# ---------------------------------------------------------------------------

class RoomInDB(MongoBaseModel):
    """Full room document as persisted in MongoDB.

    Attributes:
        room_id: Unique room identifier.
        hotel_id: Reference to the parent restaurant's ``hotel_id``.
        room_number: Human-readable room code (e.g. ``"2-VL01"``).
        room_type: Category — single, double, suite, or deluxe.
        price_per_session: Price for a single meal session (~3 hours).
        price_half_day: Price for a half-day booking (~5-6 hours).
        price_full_day: Price for a full-day booking (~12 hours).
        max_occupancy: Maximum guests allowed.
        amenities: List of amenity tags.
        floor: Floor name (e.g. ``"Ground Floor"``).
        section: Section name (e.g. ``"VIP Lounge"``).
        display_name: Friendly room name (e.g. ``"VIP Sapphire"``).
        is_available: Whether the room can currently be booked.
        created_at: UTC creation timestamp.
        updated_at: UTC last-update timestamp.
    """

    room_id: str = Field(..., description="Unique room identifier")
    hotel_id: str = Field(..., description="Parent restaurant reference")
    room_number: str = Field(..., min_length=1, max_length=20)
    room_type: RoomType = Field(...)
    price_per_session: float = Field(..., gt=0, description="Price for a single meal session (~3 hrs)")
    price_half_day: float = Field(..., gt=0, description="Price for a half-day booking (~5-6 hrs)")
    price_full_day: float = Field(..., gt=0, description="Price for a full-day booking (~12 hrs)")
    max_occupancy: int = Field(..., ge=1, le=20)
    amenities: list[str] = Field(default_factory=list)
    floor: str = Field(default="", max_length=50)
    section: str = Field(default="", max_length=100)
    display_name: str = Field(default="", max_length=100)
    is_available: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip().lower() for a in v]


# ---------------------------------------------------------------------------
# Create / Update schemas
# ---------------------------------------------------------------------------

class RoomCreate(BaseModel):
    """Payload accepted when creating a new room."""

    hotel_id: str = Field(..., description="Parent restaurant reference")
    room_number: str = Field(..., min_length=1, max_length=20)
    room_type: RoomType = Field(...)
    price_per_session: float = Field(..., gt=0)
    price_half_day: float = Field(..., gt=0)
    price_full_day: float = Field(..., gt=0)
    max_occupancy: int = Field(..., ge=1, le=20)
    amenities: list[str] = Field(default_factory=list)
    floor: str = Field(default="", max_length=50)
    section: str = Field(default="", max_length=100)
    display_name: str = Field(default="", max_length=100)

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip().lower() for a in v]


class RoomUpdate(BaseModel):
    """Payload accepted when updating an existing room.

    All fields are optional — only supplied fields are written.
    """

    room_number: str | None = Field(default=None, min_length=1, max_length=20)
    room_type: RoomType | None = None
    price_per_session: float | None = Field(default=None, gt=0)
    price_half_day: float | None = Field(default=None, gt=0)
    price_full_day: float | None = Field(default=None, gt=0)
    max_occupancy: int | None = Field(default=None, ge=1, le=20)
    amenities: list[str] | None = None
    floor: str | None = Field(default=None, max_length=50)
    section: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    is_available: bool | None = None

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [a.strip().lower() for a in v]
