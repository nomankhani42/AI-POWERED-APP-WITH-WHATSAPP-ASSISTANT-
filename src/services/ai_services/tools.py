"""Function tools for the restaurant booking WhatsApp agent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from agents import RunContextWrapper, function_tool

from database import get_db
from crud import hotel as hotel_crud
from crud import room as room_crud
from crud import customer as customer_crud
from crud import booking as booking_crud
from models.booking import BookingCreate, BookedVia, BookingStatus, BookingType
from models.room import RoomType, RoomInDB
from services.ai_services.context import UserContext

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PKT = timezone(timedelta(hours=5))

_TIME_SLOT_HOURS = {
    "breakfast": 8, "lunch": 12, "dinner": 19,
    "night": 19, "morning": 10, "afternoon": 14, "evening": 18,
}

_BOOKING_DURATION_HOURS = {
    BookingType.SESSION: 3,
    BookingType.HALF_DAY: 6,
    BookingType.FULL_DAY: 12,
}

_BOOKING_TYPE_LABELS = {
    BookingType.SESSION: "Session (~3 hrs)",
    BookingType.HALF_DAY: "Half-Day (~6 hrs)",
    BookingType.FULL_DAY: "Full-Day (~12 hrs)",
    BookingType.MULTI_DAY: "Multi-Day",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_pkt() -> datetime:
    return datetime.now(PKT)


def _parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD or YYYY-MM-DD HH:MM into timezone-aware PKT datetime."""
    s = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=PKT)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _apply_slot(dt: datetime, slot: str | None) -> datetime:
    if not slot:
        return dt
    hour = _TIME_SLOT_HOURS.get(slot.strip().lower())
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0) if hour else dt


def _checkout(ci: datetime, bt: BookingType, days: int = 1) -> datetime:
    if bt == BookingType.MULTI_DAY:
        return ci + timedelta(days=max(days, 1))
    return ci + timedelta(hours=_BOOKING_DURATION_HOURS[bt])


def _price(room: RoomInDB, bt: BookingType, days: int = 1) -> float:
    if bt == BookingType.SESSION:   return room.price_per_session
    if bt == BookingType.HALF_DAY:  return room.price_half_day
    if bt == BookingType.FULL_DAY:  return room.price_full_day
    if bt == BookingType.MULTI_DAY: return room.price_full_day * max(days, 1)
    return room.price_per_session


def _fmt(dt: datetime) -> str:
    return dt.astimezone(PKT).strftime("%Y-%m-%d %I:%M %p")


async def _find_hotel(name: str):
    """Return first hotel matching name (case-insensitive partial match)."""
    db = get_db()
    hotels = await hotel_crud.get_all_hotels(db, limit=50)
    q = name.strip().lower()
    return next((h for h in hotels if q in h.name.lower()), None)


def _parse_bt(bt_str: str) -> BookingType | None:
    try:
        return BookingType(bt_str.strip().lower())
    except ValueError:
        return None


_STATUS_EMOJI = {"pending": "🟡", "confirmed": "🟢", "cancelled": "🔴", "completed": "✅"}

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@function_tool(strict_mode=False)
async def get_current_datetime(ctx: RunContextWrapper[UserContext]) -> str:
    """Return current date/time in PKT. Call FIRST for any relative date (today/tomorrow/tonight)."""
    now = _now_pkt()
    return f"PKT now: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')})"


@function_tool(strict_mode=False)
async def search_hotels(
    ctx: RunContextWrapper[UserContext],
    location: Annotated[str, "City/area to search. Use 'all' for everything."],
) -> str:
    """List restaurants by location."""
    db = get_db()
    hotels = await hotel_crud.get_all_hotels(db, limit=20)
    if location.strip().lower() != "all":
        hotels = [h for h in hotels if location.strip().lower() in h.location.lower()]
    if not hotels:
        return "No restaurants found. Try 'all' to see everything."
    lines = [
        f"• {h.name} ({h.location}) {h.star_rating}★ | ID: {h.hotel_id}"
        for h in hotels
    ]
    return f"{len(hotels)} restaurant(s):\n" + "\n".join(lines)


@function_tool(strict_mode=False)
async def get_hotel_details(
    ctx: RunContextWrapper[UserContext],
    hotel_name: Annotated[str, "Restaurant name (partial match ok)"],
) -> str:
    """Get full details of a restaurant."""
    hotel = await _find_hotel(hotel_name)
    if not hotel:
        return f"Restaurant '{hotel_name}' not found."
    amenities = ", ".join(hotel.amenities) if hotel.amenities else "N/A"
    return (
        f"🍽️ {hotel.name} | 📍 {hotel.location} | ⭐ {hotel.star_rating}/5\n"
        f"📝 {hotel.description or 'N/A'}\n"
        f"🏷️ {amenities}\n"
        f"📧 {hotel.contact_email or 'N/A'} | 📞 {hotel.contact_phone or 'N/A'}\n"
        f"🔑 ID: {hotel.hotel_id}"
    )


@function_tool(strict_mode=False)
async def search_available_rooms(
    ctx: RunContextWrapper[UserContext],
    hotel_name: Annotated[str, "Restaurant name"],
    date: Annotated[str, "Date YYYY-MM-DD"],
    booking_type: Annotated[str, "session | half_day | full_day | multi_day"] = "session",
    time_slot: Annotated[str, "breakfast | lunch | dinner | morning | afternoon | evening"] = "dinner",
    num_days: Annotated[int, "Days for multi_day only"] = 1,
    room_type: Annotated[str | None, "single | double | suite | deluxe (optional)"] = None,
) -> str:
    """Search available rooms. Call get_current_datetime first for relative dates."""
    db = get_db()
    hotel = await _find_hotel(hotel_name)
    if not hotel:
        return f"Restaurant '{hotel_name}' not found."

    try:
        ci = _parse_date(date)
    except ValueError:
        return "Invalid date. Use YYYY-MM-DD."

    bt = _parse_bt(booking_type)
    if not bt:
        return f"Invalid booking_type '{booking_type}'. Use: session, half_day, full_day, multi_day."

    ci = _apply_slot(ci, time_slot)
    co = _checkout(ci, bt, num_days)

    if ci < _now_pkt():
        return f"That time ({_fmt(ci)}) is in the past. Current PKT: {_fmt(_now_pkt())}."

    rooms = await room_crud.get_available_rooms(db, hotel.hotel_id, ci, co)
    if room_type:
        rooms = [r for r in rooms if r.room_type == room_type.strip().lower()]

    if not rooms:
        return (
            f"No rooms available at {hotel.name} on {_fmt(ci)} "
            f"({_BOOKING_TYPE_LABELS.get(bt, booking_type)}). Try another date or time."
        )

    lines = []
    for r in rooms:
        total = _price(r, bt, num_days)
        floor_sec = f"{r.floor} — {r.section}" if r.floor and r.section else ""
        lines.append(
            f"• {r.display_name or r.room_number} [{r.room_number}] "
            f"{r.room_type.value.title()} | 👥 {r.max_occupancy} | "
            f"💰 PKR {total:,.0f}"
            + (f" | 📍 {floor_sec}" if floor_sec else "")
            + f" | ID: {r.room_id}"
        )

    label = _BOOKING_TYPE_LABELS.get(bt, booking_type)
    header = f"{hotel.name} | {_fmt(ci)} → {_fmt(co)} | {label}"
    if bt == BookingType.MULTI_DAY:
        header += f" ({num_days}d)"
    return header + f" | {len(rooms)} available\n\n" + "\n".join(lines)


@function_tool(strict_mode=False)
async def get_room_types_info(ctx: RunContextWrapper[UserContext]) -> str:
    """Return room types and booking options info."""
    return (
        "*Rooms:* Single 2-4 | Double 6-8 | Suite 8-12 | Deluxe 15-20\n\n"
        "*Booking types:*\n"
        "• Session ~3h | Half-Day ~6h | Full-Day ~12h | Multi-Day\n\n"
        "*Slots:* Breakfast 8AM | Morning 10AM | Lunch 12PM | "
        "Afternoon 2PM | Evening 6PM | Dinner 7PM\n\n"
        "Tell me your date and I'll check availability!"
    )


@function_tool(strict_mode=False)
async def book_room(
    ctx: RunContextWrapper[UserContext],
    hotel_name: Annotated[str, "Restaurant name"],
    room_id: Annotated[str, "Room ID from search results"],
    date: Annotated[str, "Date YYYY-MM-DD"],
    booking_type: Annotated[str, "session | half_day | full_day | multi_day"],
    num_guests: Annotated[int, "Number of guests"],
    time_slot: Annotated[str, "breakfast | lunch | dinner | morning | afternoon | evening"] = "dinner",
    num_days: Annotated[int, "Days for multi_day only"] = 1,
    special_requests: Annotated[str, "Special requests (optional)"] = "",
) -> str:
    """Book a room. Always confirm details with customer first."""
    db = get_db()

    hotel = await _find_hotel(hotel_name)
    if not hotel:
        return f"Restaurant '{hotel_name}' not found."

    room = await room_crud.get_room_by_id(db, room_id)
    if not room:
        return f"Room ID '{room_id}' not found. Search for rooms first."
    if room.hotel_id != hotel.hotel_id:
        return "This room doesn't belong to the selected restaurant."

    try:
        ci = _parse_date(date)
    except ValueError:
        return "Invalid date. Use YYYY-MM-DD."

    bt = _parse_bt(booking_type)
    if not bt:
        return f"Invalid booking_type '{booking_type}'."

    ci = _apply_slot(ci, time_slot)
    co = _checkout(ci, bt, num_days)

    if ci < _now_pkt():
        return f"Cannot book in the past. Current PKT: {_fmt(_now_pkt())}."

    if num_guests > room.max_occupancy:
        return f"Room max capacity is {room.max_occupancy}. You requested {num_guests}."

    # Availability double-check
    available = await room_crud.get_available_rooms(db, hotel.hotel_id, ci, co)
    if room.room_id not in {r.room_id for r in available}:
        return (
            f"*{room.display_name or room.room_number}* is already booked "
            f"for {_fmt(ci)} → {_fmt(co)}. Try another room or time."
        )

    customer = await customer_crud.upsert_customer(db, whatsapp_number=ctx.context.whatsapp_number)
    total = _price(room, bt, num_days)

    booking = await booking_crud.create_booking(
        db,
        BookingCreate(
            customer_id=customer.customer_id,
            hotel_id=hotel.hotel_id,
            room_id=room.room_id,
            booking_type=bt,
            check_in_date=ci,
            check_out_date=co,
            num_guests=num_guests,
            special_requests=special_requests,
            booked_via=BookedVia.WHATSAPP,
        ),
        total,
    )

    room_display = room.display_name or room.room_number
    floor_sec = f"{room.floor} — {room.section}" if room.floor and room.section else ""
    label = _BOOKING_TYPE_LABELS.get(bt, booking_type)

    return (
        f"✅ Booking confirmed!\n\n"
        f"📋 ID: *{booking.booking_id}*\n"
        f"🍽️ {hotel.name}\n"
        f"🚪 {room_display} [{room.room_number}] ({room.room_type.value.title()})"
        + (f" | 📍 {floor_sec}" if floor_sec else "") + "\n"
        f"📅 {_fmt(ci)} → {_fmt(co)}\n"
        f"⏱️ {label}" + (f" ({num_days}d)" if bt == BookingType.MULTI_DAY else "") + "\n"
        f"👥 {num_guests} guests | 💰 PKR {total:,.0f}\n"
        f"📌 Status: Pending"
        + (f"\n📝 {special_requests}" if special_requests else "") + "\n\n"
        f"Save your ID *{booking.booking_id}* for status checks."
    )


@function_tool(strict_mode=False)
async def check_booking_status(
    ctx: RunContextWrapper[UserContext],
    booking_id: Annotated[str, "Booking ID e.g. BK-2026-0001"],
) -> str:
    """Check status and details of a booking."""
    db = get_db()
    b = await booking_crud.get_booking_by_booking_id(db, booking_id.strip().upper())
    if not b:
        return f"No booking found with ID '{booking_id}'."

    hotel = await hotel_crud.get_hotel_by_id(db, b.hotel_id)
    room = await room_crud.get_room_by_id(db, b.room_id)

    room_info = "Unknown"
    floor_sec = ""
    if room:
        room_info = f"{room.display_name or room.room_number} [{room.room_number}] ({room.room_type.value.title()})"
        if room.floor and room.section:
            floor_sec = f"{room.floor} — {room.section}"

    emoji = _STATUS_EMOJI.get(b.status, "❓")
    label = _BOOKING_TYPE_LABELS.get(b.booking_type, b.booking_type)

    return (
        f"📋 *{b.booking_id}*\n"
        f"🍽️ {hotel.name if hotel else 'Unknown'}\n"
        f"🚪 {room_info}" + (f" | 📍 {floor_sec}" if floor_sec else "") + "\n"
        f"📅 {_fmt(b.check_in_date)} → {_fmt(b.check_out_date)}\n"
        f"⏱️ {label} | 👥 {b.num_guests} | 💰 PKR {b.total_price:,.0f}\n"
        f"{emoji} *{b.status.value.title()}*"
        + (f"\n📝 {b.special_requests}" if b.special_requests else "")
    )


@function_tool(strict_mode=False)
async def cancel_my_booking(
    ctx: RunContextWrapper[UserContext],
    booking_id: Annotated[str, "Booking ID to cancel e.g. BK-2026-0001"],
) -> str:
    """Cancel a booking. Confirm with customer first."""
    db = get_db()
    bid = booking_id.strip().upper()

    b = await booking_crud.get_booking_by_booking_id(db, bid)
    if not b:
        return f"No booking found with ID '{booking_id}'."

    customer = await customer_crud.get_customer_by_whatsapp_number(db, ctx.context.whatsapp_number)
    if not customer or b.customer_id != customer.customer_id:
        return "This booking doesn't belong to your account."

    if not await booking_crud.cancel_booking(db, bid):
        return f"Cannot cancel {bid}. Status: *{b.status.value.title()}*. Only pending/confirmed can be cancelled."

    return f"Booking *{bid}* cancelled. 🔴\nNeed a new booking? I'm happy to help!"


@function_tool(strict_mode=False)
async def get_my_bookings(ctx: RunContextWrapper[UserContext]) -> str:
    """Get all bookings for the current customer."""
    db = get_db()
    customer = await customer_crud.get_customer_by_whatsapp_number(db, ctx.context.whatsapp_number)
    if not customer:
        return "No bookings yet. Want to search for available rooms?"

    bookings = await booking_crud.get_bookings_by_customer(db, customer.customer_id, limit=10)
    if not bookings:
        return "No bookings yet. Want to search for available rooms?"

    lines = [
        f"{_STATUS_EMOJI.get(b.status, '❓')} *{b.booking_id}* | "
        f"{_fmt(b.check_in_date)} | "
        f"{_BOOKING_TYPE_LABELS.get(b.booking_type, b.booking_type)} | "
        f"PKR {b.total_price:,.0f} | {b.status.value.title()}"
        for b in bookings
    ]
    return f"Your bookings ({len(bookings)}):\n\n" + "\n".join(lines)