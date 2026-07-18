"""Function tools the booking agent may call.

Each tool reads the guest's `phone_number` from the trusted run context
(`wrapper.context`), never from a model-supplied argument, so booking actions are always
scoped to the caller (FR-008 / SC-005). Tools return concise strings and never raise to the
model — invalid input yields a clear message the agent relays.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import RunContextWrapper, function_tool

from app.agent.context import RunContext
from app.core.config import get_settings
from app.db import bookings as repo
from app.services.whatsapp_messages import (
    send_booking_cancellation,
    send_booking_confirmation,
    send_room_carousel,
    send_room_type_list,
)

logger = logging.getLogger(__name__)


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_current_datetime(now: datetime, timezone_name: str) -> str:
    local = now.astimezone(_timezone(timezone_name))
    return (
        "Current business date/time: "
        f"{local:%Y-%m-%d %H:%M:%S %Z} ({timezone_name}). "
        f"Today is {local:%A, %B %d, %Y}."
    )


@function_tool
async def get_current_datetime(wrapper: RunContextWrapper[RunContext]) -> str:
    """Get the current business-local date and time for resolving relative dates."""

    settings = get_settings()
    return _format_current_datetime(datetime.now(tz=timezone.utc), settings.business_timezone)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise repo.BookingError(
            f"'{value}' is not a valid date. Use YYYY-MM-DD."
        ) from exc


def _rooms_text(rooms: list) -> str:
    lines = [f"- {r.name} ({r.room_type}, sleeps {r.capacity})" for r in rooms]
    return "Available rooms:\n" + "\n".join(lines)


async def _check_availability_impl(
    channel: str,
    phone_number: str,
    business_number: str | None,
    check_in: str,
    check_out: str,
    room_type: str | None = None,
) -> str:
    try:
        rooms = await repo.check_availability(
            _parse_date(check_in), _parse_date(check_out), room_type
        )
    except repo.BookingError as exc:
        return str(exc)
    if not rooms:
        return "No rooms are available for those dates."

    # WhatsApp chat gets the rooms as a swipeable photo carousel (007 extension). Meta
    # rejects 1-card carousels, so below 2 imaged rooms we fall through to plain text.
    if channel == "whatsapp" and business_number:
        with_images = [r for r in rooms if r.image_url][:10]
        if len(with_images) >= 2:
            try:
                await send_room_carousel(phone_number, with_images, business_number)
            except Exception as exc:
                logger.error(
                    "room_carousel.send_failed recipient=%s error=%s: %s",
                    phone_number,
                    type(exc).__name__,
                    exc,
                )
            else:
                shown = ", ".join(f"{r.name} ({r.room_type.value})" for r in with_images)
                result = (
                    "The available rooms were just shown to the guest as a swipeable photo "
                    f"carousel: {shown}. Do not re-list them — briefly ask which room "
                    "they'd like; tapping Book on a card pre-fills a booking message."
                )
                missing = [r for r in rooms if r not in with_images]
                if missing:
                    names = ", ".join(f"{r.name} ({r.room_type.value})" for r in missing)
                    result += f" Also available but not in the carousel: {names}."
                return result
    return _rooms_text(rooms)


@function_tool
async def check_availability(
    wrapper: RunContextWrapper[RunContext],
    check_in: str,
    check_out: str,
    room_type: str | None = None,
) -> str:
    """List rooms available for a stay. Dates are YYYY-MM-DD; check_out is the departure day."""
    return await _check_availability_impl(
        wrapper.context.channel,
        wrapper.context.phone_number,
        wrapper.context.business_number,
        check_in,
        check_out,
        room_type,
    )


_LIST_SENT_SENTINEL = (
    "The room-type options were just shown to the guest as a tappable list. "
    "Do not list the types again — briefly ask them to pick one from the list."
)


def _room_types_text(capacities: dict) -> str:
    lines = ", ".join(f"{t.value} (sleeps {cap})" for t, cap in capacities.items())
    return "Available room types: " + lines + "."


async def _offer_room_types_impl(channel: str, phone_number: str) -> str:
    capacities = await repo.active_room_capacities()
    if not capacities:
        return "No room types are currently available."

    if channel == "whatsapp":
        try:
            await send_room_type_list(phone_number, capacities)
            return _LIST_SENT_SENTINEL
        except Exception as exc:
            # Fall back to enumerated text so the conversation still works (007 R4).
            logger.error(
                "room_type_list.send_failed recipient=%s error=%s: %s",
                phone_number,
                type(exc).__name__,
                exc,
            )
    # voice/api (and whatsapp send-failure fallback): enumerate for speech/text (FR-010).
    return _room_types_text(capacities)


@function_tool
async def offer_room_types(wrapper: RunContextWrapper[RunContext]) -> str:
    """Show the guest the room types they can choose from. Call this whenever the guest
    needs to pick or narrow by room type and hasn't named a valid one."""

    return await _offer_room_types_impl(
        wrapper.context.channel, wrapper.context.phone_number
    )


async def _safe_send_booking_confirmation(phone_number: str, booking: object) -> None:
    try:
        await send_booking_confirmation(phone_number, booking)
    except Exception:
        logger.warning(
            "booking tool: failed to send WhatsApp booking confirmation for reference=%s",
            getattr(booking, "reference", ""),
            exc_info=True,
        )


async def _safe_send_booking_cancellation(phone_number: str, booking: object) -> None:
    """Send the cancellation notice; a delivery failure never blocks the cancellation.

    Each failure emits one structured, operator-visible ERROR entry (007 FR-004) — no
    persisted delivery status, no automatic retries (clarification Q3).
    """
    try:
        await send_booking_cancellation(phone_number, booking)
    except Exception as exc:
        logger.error(
            "booking.cancellation_notice_failed reference=%s recipient=%s error=%s: %s",
            getattr(booking, "reference", ""),
            phone_number,
            type(exc).__name__,
            exc,
        )


async def _book_room_impl(
    phone_number: str,
    room_name: str,
    check_in: str,
    check_out: str,
    guest_name: str | None = None,
) -> str:
    try:
        booking = await repo.create_booking(
            room_name,
            _parse_date(check_in),
            _parse_date(check_out),
            phone_number,
            guest_name,
        )
    except repo.BookingError as exc:
        return str(exc)
    await _safe_send_booking_confirmation(phone_number, booking)
    return (
        f"Booked {room_name} from {booking.check_in} to {booking.check_out}. "
        f"Your booking reference is {booking.reference}."
    )


async def _cancel_booking_impl(phone_number: str, reference: str) -> str:
    booking = await repo.cancel_booking(reference, phone_number)
    if booking is None:
        # Unknown reference, someone else's booking, or already cancelled — no notice sent.
        return (
            f"I couldn't find an active booking with reference {reference} under your "
            "number. It may already be cancelled."
        )
    await _safe_send_booking_cancellation(phone_number, booking)
    return f"Booking {booking.reference} is now {booking.status.value}."


@function_tool
async def book_room(
    wrapper: RunContextWrapper[RunContext],
    room_name: str,
    check_in: str,
    check_out: str,
    guest_name: str | None = None,
) -> str:
    """Book a room for a stay (YYYY-MM-DD). Confirm details with the guest before calling."""
    return await _book_room_impl(
        wrapper.context.phone_number,
        room_name,
        check_in,
        check_out,
        guest_name,
    )


@function_tool
async def cancel_booking(
    wrapper: RunContextWrapper[RunContext],
    reference: str,
) -> str:
    """Cancel one of the guest's bookings by its reference."""
    return await _cancel_booking_impl(wrapper.context.phone_number, reference)


@function_tool
async def list_bookings(
    wrapper: RunContextWrapper[RunContext],
    status: str = "all",
) -> str:
    """List the guest's bookings. status: 'active', 'cancelled', or 'all'."""
    rows = await repo.list_bookings(wrapper.context.phone_number, status)
    if not rows:
        return "You have no bookings."
    lines = [
        f"- {b.reference}: {b.room_name} {b.check_in} to {b.check_out} ({b.status.value})"
        for b in rows
    ]
    return "Your bookings:\n" + "\n".join(lines)


TOOLS = [
    get_current_datetime,
    check_availability,
    offer_room_types,
    book_room,
    cancel_booking,
    list_bookings,
]
