"""Pydantic v2 models for the Booking entity.

Each booking document lives in the ``bookings`` MongoDB collection.
The ``booking_id`` field is a human-readable code in the format
``BK-YYYY-XXXX`` (e.g. ``BK-2024-0001``), auto-generated via an
atomic counter in the ``counters`` collection.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from models.common import MongoBaseModel, PyObjectId, utc_now


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BookingStatus(StrEnum):
    """Allowed booking status values.

    Flow: pending → confirmed → completed  **or**  pending → cancelled.
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class BookedVia(StrEnum):
    """Channel through which the booking was made."""
    WHATSAPP = "whatsapp"
    WEB = "web"
    API = "api"


class BookingType(StrEnum):
    """Duration type for a restaurant room booking.

    - ``session``   — Single meal session (~3 hours: breakfast, lunch, or dinner).
    - ``half_day``  — Half-day booking (~5-6 hours).
    - ``full_day``  — Full-day booking (~12 hours, events/celebrations).
    - ``multi_day`` — Multi-day booking (conferences, wedding events, etc.).
    """
    SESSION = "session"
    HALF_DAY = "half_day"
    FULL_DAY = "full_day"
    MULTI_DAY = "multi_day"


# ---------------------------------------------------------------------------
# Core document model
# ---------------------------------------------------------------------------

class BookingInDB(MongoBaseModel):
    """Full booking document as persisted in MongoDB.

    Attributes:
        booking_id: Human-readable code (``BK-YYYY-XXXX``).
        customer_id: Reference to ``customers.customer_id``.
        hotel_id: Reference to ``hotels.hotel_id`` (restaurant).
        room_id: Reference to ``rooms.room_id``.
        booking_type: Duration type — session, half_day, full_day, or multi_day.
        check_in_date: Reservation start date/time.
        check_out_date: Reservation end date/time (must be after check-in).
        num_guests: Number of guests for the booking.
        total_price: Calculated total price.
        status: Current booking status.
        special_requests: Free-text notes from the customer.
        booked_via: Channel used to make the booking.
        created_at: UTC creation timestamp.
        updated_at: UTC last-update timestamp.
    """

    booking_id: str = Field(..., description="Human-readable ID (BK-YYYY-XXXX)")
    customer_id: str = Field(..., description="Customer reference")
    hotel_id: str = Field(..., description="Restaurant reference")
    room_id: str = Field(..., description="Room reference")
    booking_type: BookingType = Field(default=BookingType.SESSION, description="Duration type")
    check_in_date: datetime = Field(...)
    check_out_date: datetime = Field(...)
    num_guests: int = Field(..., ge=1)
    total_price: float = Field(..., ge=0)
    status: BookingStatus = Field(default=BookingStatus.PENDING)
    special_requests: str = Field(default="", max_length=1000)
    booked_via: BookedVia = Field(default=BookedVia.WHATSAPP)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_dates(self) -> "BookingInDB":
        if self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date must be after check_in_date")
        return self


# ---------------------------------------------------------------------------
# Create / Update schemas
# ---------------------------------------------------------------------------

class BookingCreate(BaseModel):
    """Payload accepted when creating a new booking.

    ``booking_id`` is **not** included — it is auto-generated.
    ``total_price`` is **not** included — it is calculated from the room
    rate and booking type.
    """

    customer_id: str = Field(...)
    hotel_id: str = Field(...)
    room_id: str = Field(...)
    booking_type: BookingType = Field(default=BookingType.SESSION)
    check_in_date: datetime = Field(...)
    check_out_date: datetime = Field(...)
    num_guests: int = Field(..., ge=1)
    special_requests: str = Field(default="", max_length=1000)
    booked_via: BookedVia = Field(default=BookedVia.WHATSAPP)

    @model_validator(mode="after")
    def check_dates(self) -> "BookingCreate":
        if self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date must be after check_in_date")
        return self


class BookingUpdate(BaseModel):
    """Payload accepted when updating an existing booking.

    Only mutable fields are exposed; ``booking_id`` and references are
    immutable after creation.
    """

    booking_type: BookingType | None = None
    check_in_date: datetime | None = None
    check_out_date: datetime | None = None
    num_guests: int | None = Field(default=None, ge=1)
    total_price: float | None = Field(default=None, ge=0)
    status: BookingStatus | None = None
    special_requests: str | None = Field(default=None, max_length=1000)
