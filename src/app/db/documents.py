"""Beanie document models for durable rooms and bookings (data-model.md)."""

from datetime import date, datetime, timezone
from enum import Enum

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class BookingStatus(str, Enum):
    active = "active"
    cancelled = "cancelled"


class RoomType(str, Enum):
    """Canonical room-type catalog (007 FR-009). Declaration order is presentation order.

    Evolving the set is a deliberate catalog change here — seed data, write-time
    validation, and guest-facing options all derive from this enum.
    """

    single = "single"
    twin = "twin"
    double = "double"
    deluxe = "deluxe"
    accessible = "accessible"
    family = "family"
    executive = "executive"
    suite = "suite"


class CallStatus(str, Enum):
    ringing = "ringing"
    connecting = "connecting"
    connected = "connected"
    ended = "ended"
    failed = "failed"


class Room(Document):
    """A bookable room."""

    name: str
    room_type: RoomType
    capacity: int
    description: str | None = None
    # Public HTTPS JPEG/PNG ≤5 MB — Meta fetches it for card headers, so it must be
    # reachable without auth (007 carousel extension).
    image_url: str | None = None
    is_active: bool = True

    @field_validator("room_type", mode="before")
    @classmethod
    def _normalize_room_type(cls, value: object) -> object:
        # Casing/spacing variants normalize to the canonical value; anything else is
        # rejected by the enum so free-form types never persist (007 FR-009).
        if isinstance(value, str):
            return value.strip().lower()
        return value

    class Settings:
        name = "rooms"
        indexes = [
            pymongo.IndexModel([("name", pymongo.ASCENDING)], unique=True, name="uq_room_name"),
        ]


class Booking(Document):
    """A guest's reservation of a room for a stay (check_in .. check_out)."""

    reference: str
    room_id: PydanticObjectId
    room_name: str
    phone_number: str
    guest_name: str | None = None
    check_in: date
    check_out: date
    status: BookingStatus = BookingStatus.active
    created_at: datetime = Field(default_factory=_utcnow)
    cancelled_at: datetime | None = None

    class Settings:
        name = "bookings"
        indexes = [
            pymongo.IndexModel([("reference", pymongo.ASCENDING)], unique=True, name="uq_booking_ref"),
            pymongo.IndexModel([("phone_number", pymongo.ASCENDING)], name="ix_booking_phone"),
            pymongo.IndexModel([("room_id", pymongo.ASCENDING)], name="ix_booking_room"),
        ]


class Call(Document):
    """A single voice interaction with a caller, from first event to termination.

    State transitions are monotonic (data-model.md): ``ringing -> connecting -> connected ->
    ended``, or any state ``-> failed``. A call already ``ended``/``failed`` never regresses.
    """

    call_id: str
    wa_call_from: str
    display_phone_number: str
    status: CallStatus = CallStatus.ringing
    conversation_id: str
    started_at: datetime = Field(default_factory=_utcnow)
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "calls"
        indexes = [
            pymongo.IndexModel([("call_id", pymongo.ASCENDING)], unique=True, name="uq_call_id"),
            pymongo.IndexModel([("wa_call_from", pymongo.ASCENDING)], name="ix_call_from"),
        ]


class InboundMessage(Document):
    """One inbound WhatsApp chat message — audit trail + durable idempotency backstop.

    Insertion is guarded by the unique ``wamid`` index so a redelivered webhook never
    triggers a second agent turn (007 FR-005a). The Redis ``wa:msg:<wamid>`` key is the
    fast-path dedupe; this document is the durable backstop.
    """

    wamid: str
    sender: str
    message_type: str
    received_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "inbound_messages"
        indexes = [
            pymongo.IndexModel([("wamid", pymongo.ASCENDING)], unique=True, name="uq_wamid"),
        ]


class CallEvent(Document):
    """One notification Meta sent about a call — audit trail + durable idempotency backstop.

    Insertion is guarded by the unique ``event_id`` index so a duplicate delivery never
    creates a second record (FR-006). The Redis ``call:event:<event_id>`` key is the fast-path
    dedupe; this document is the durable backstop.
    """

    event_id: str
    call_id: str
    event_type: str
    payload: dict
    received_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        name = "call_events"
        indexes = [
            pymongo.IndexModel(
                [("event_id", pymongo.ASCENDING)], unique=True, name="uq_call_event_id"
            ),
            pymongo.IndexModel([("call_id", pymongo.ASCENDING)], name="ix_event_call"),
        ]
