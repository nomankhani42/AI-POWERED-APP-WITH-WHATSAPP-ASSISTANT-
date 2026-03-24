"""Pydantic v2 models for the Hotel entity.

Each hotel document lives in the ``hotels`` MongoDB collection.
"""

from datetime import datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from models.common import MongoBaseModel, PyObjectId, utc_now


# ---------------------------------------------------------------------------
# Enums / Literals
# ---------------------------------------------------------------------------

class StarRating(IntEnum):
    """Allowed star-rating values (1–5)."""
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


# ---------------------------------------------------------------------------
# Core document model
# ---------------------------------------------------------------------------

class HotelInDB(MongoBaseModel):
    """Full hotel document as persisted in MongoDB.

    Attributes:
        hotel_id: Unique human-friendly identifier (auto-generated UUID
            hex or short code).
        name: Display name of the hotel.
        location: City / address string.
        description: Free-text marketing blurb.
        star_rating: 1–5 star classification.
        amenities: List of amenity tags (e.g. ``["pool", "wifi"]``).
        contact_email: Hotel front-desk e-mail.
        contact_phone: Hotel front-desk phone number.
        is_active: Soft-delete flag — ``False`` hides the hotel from
            searches.
        created_at: UTC creation timestamp.
        updated_at: UTC last-update timestamp.
    """

    hotel_id: str = Field(..., description="Unique hotel identifier")
    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    star_rating: int = Field(..., ge=1, le=5)
    amenities: list[str] = Field(default_factory=list)
    contact_email: str = Field(default="")
    contact_phone: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("star_rating")
    @classmethod
    def validate_star_rating(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("star_rating must be between 1 and 5")
        return v

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip().lower() for a in v]


# ---------------------------------------------------------------------------
# Create / Update schemas
# ---------------------------------------------------------------------------

class HotelCreate(BaseModel):
    """Payload accepted when creating a new hotel."""

    name: str = Field(..., min_length=1, max_length=200)
    location: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    star_rating: int = Field(..., ge=1, le=5)
    amenities: list[str] = Field(default_factory=list)
    contact_email: str = Field(default="")
    contact_phone: str = Field(default="")

    @field_validator("star_rating")
    @classmethod
    def validate_star_rating(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("star_rating must be between 1 and 5")
        return v

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip().lower() for a in v]


class HotelUpdate(BaseModel):
    """Payload accepted when updating an existing hotel.

    All fields are optional — only supplied fields are written.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    star_rating: int | None = Field(default=None, ge=1, le=5)
    amenities: list[str] | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    is_active: bool | None = None

    @field_validator("amenities", mode="before")
    @classmethod
    def lowercase_amenities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [a.strip().lower() for a in v]
