"""Booking repository: availability, create, cancel, list.

These functions are the durable-data operations invoked by the agent tools. They raise
``BookingError`` (with a user-friendly message) for domain failures so the tool layer can
relay a clear message to the guest. All booking access is scoped by ``phone_number``.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

from pymongo import ReturnDocument

from app.db.documents import Booking, BookingStatus, Room, RoomType


class BookingError(Exception):
    """Domain error carrying a user-friendly message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_room_type(value: str) -> RoomType:
    """Map a guest-supplied string onto the canonical ``RoomType`` (007 FR-008/FR-009).

    Casing/spacing variants normalize; anything else raises ``BookingError`` naming the
    valid types so the agent can re-offer the options instead of querying with junk.
    """

    try:
        return RoomType(value.strip().lower())
    except ValueError:
        valid = ", ".join(t.value for t in RoomType)
        raise BookingError(
            f"'{value}' is not a room type we offer. Valid types: {valid}."
        ) from None


async def list_active_room_types() -> list[RoomType]:
    """Distinct canonical types among active rooms, in enum declaration order (007 FR-006)."""

    values = await Room.get_pymongo_collection().distinct("room_type", {"is_active": True})
    present = set(values)
    return [t for t in RoomType if t.value in present]


async def active_room_capacities() -> dict[RoomType, int]:
    """Max sleeps-capacity per active canonical type, in enum declaration order.

    Feeds the guest-facing room-type options (007 FR-006): the keys are exactly the types
    a guest can currently book.
    """

    rooms = await Room.find(Room.is_active == True).to_list()  # noqa: E712
    caps: dict[RoomType, int] = {}
    for room in rooms:
        caps[room.room_type] = max(caps.get(room.room_type, 0), room.capacity)
    return {t: caps[t] for t in RoomType if t in caps}


def _overlaps(a_in: date, a_out: date, b_in: date, b_out: date) -> bool:
    """True when stay [a_in, a_out) overlaps stay [b_in, b_out)."""

    return a_in < b_out and b_in < a_out


def generate_reference() -> str:
    """Short, human-friendly, unique-ish booking reference."""

    return "BK-" + uuid4().hex[:8].upper()


def _validate_range(check_in: date, check_out: date) -> None:
    if check_out <= check_in:
        raise BookingError("Check-out must be after check-in.")
    if check_in < date.today():
        raise BookingError("Check-in date cannot be in the past.")


async def _active_bookings_for_room(room_id) -> list[Booking]:
    return await Booking.find(
        Booking.room_id == room_id,
        Booking.status == BookingStatus.active,
    ).to_list()


async def check_availability(
    check_in: date, check_out: date, room_type: str | None = None
) -> list[Room]:
    """Rooms with no active booking overlapping the requested stay."""

    _validate_range(check_in, check_out)

    query = Room.find(Room.is_active == True)  # noqa: E712 (Beanie needs ==)
    if room_type:
        query = query.find(Room.room_type == normalize_room_type(room_type))
    rooms = await query.to_list()

    free: list[Room] = []
    for room in rooms:
        taken = any(
            _overlaps(check_in, check_out, b.check_in, b.check_out)
            for b in await _active_bookings_for_room(room.id)
        )
        if not taken:
            free.append(room)
    return free


async def create_booking(
    room_name: str,
    check_in: date,
    check_out: date,
    phone_number: str,
    guest_name: str | None = None,
) -> Booking:
    """Create an active booking, guarding against overlaps (no double-booking)."""

    _validate_range(check_in, check_out)

    room = await Room.find_one(Room.name == room_name, Room.is_active == True)  # noqa: E712
    if room is None:
        raise BookingError(f"No active room named '{room_name}'.")

    for existing in await _active_bookings_for_room(room.id):
        if _overlaps(check_in, check_out, existing.check_in, existing.check_out):
            raise BookingError(
                f"'{room_name}' is not available for those dates."
            )

    booking = Booking(
        reference=generate_reference(),
        room_id=room.id,
        room_name=room.name,
        phone_number=phone_number,
        guest_name=guest_name,
        check_in=check_in,
        check_out=check_out,
        status=BookingStatus.active,
    )
    await booking.insert()
    return booking


async def cancel_booking(reference: str, phone_number: str) -> Booking | None:
    """Atomically cancel an ACTIVE booking owned by ``phone_number``.

    Returns the booking only when THIS call performed the active→cancelled transition, so
    exactly one caller ever gets it back (007 FR-003 — the cancellation notice is sent only
    to that caller). Unknown references, other guests' bookings, and already-cancelled
    bookings all return ``None``.
    """

    raw = await Booking.get_pymongo_collection().find_one_and_update(
        {
            "reference": reference,
            "phone_number": phone_number,
            "status": BookingStatus.active.value,
        },
        {
            "$set": {
                "status": BookingStatus.cancelled.value,
                "cancelled_at": datetime.now(tz=timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return Booking.model_validate(raw) if raw is not None else None


async def list_bookings(phone_number: str, status: str = "all") -> list[Booking]:
    """Bookings owned by ``phone_number``, optionally filtered by status."""

    query = Booking.find(Booking.phone_number == phone_number)
    if status in (BookingStatus.active.value, BookingStatus.cancelled.value):
        query = query.find(Booking.status == BookingStatus(status))
    return await query.to_list()
