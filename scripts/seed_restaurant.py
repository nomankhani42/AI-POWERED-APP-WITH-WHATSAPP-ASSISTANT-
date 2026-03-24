"""Seed script — Single large restaurant with rooms across floors & sections.

Populates MongoDB with:
  - 1 restaurant (stored in the ``hotels`` collection)
  - ~34 bookable private dining rooms / event spaces across 5 floors
  - 15 sample customers
  - 10 sample bookings (mix of statuses & booking types)

Usage:
    cd src && python ../scripts/seed_restaurant.py

Requires MongoDB to be running. Reads connection settings from .env
via the project's config module.
"""

import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, MONGO_DB_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Booking type durations (hours)
BOOKING_DURATIONS = {
    "session": 3,
    "half_day": 6,
    "full_day": 12,
}

BOOKING_TYPE_LABELS = {
    "session": "Session (~3 hrs)",
    "half_day": "Half-Day (~6 hrs)",
    "full_day": "Full-Day (~12 hrs)",
    "multi_day": "Multi-Day",
}


# ---------------------------------------------------------------------------
# Restaurant definition
# ---------------------------------------------------------------------------

RESTAURANT_ID = _uid()

RESTAURANT = {
    "hotel_id": RESTAURANT_ID,
    "name": "The Grand Dine — Islamabad",
    "location": "F-7 Markaz, Jinnah Super, Islamabad, Pakistan",
    "description": (
        "Islamabad's premier fine-dining destination spread across 5 floors. "
        "From intimate private dining rooms to grand banquet halls, The Grand "
        "Dine offers spaces for every occasion — family gatherings, corporate "
        "events, weddings, birthday celebrations, and exclusive VIP experiences. "
        "Our rooftop terrace features stunning Margalla Hills views, while the "
        "ground-floor café is perfect for casual meetups. Each floor has its "
        "own kitchen brigade ensuring fresh, made-to-order Pakistani, Chinese, "
        "and Continental cuisine."
    ),
    "star_rating": 5,
    "amenities": [
        "free wifi",
        "valet parking",
        "wheelchair accessible",
        "live music",
        "prayer area",
        "kids play area",
        "cctv security",
        "power backup",
        "air conditioning",
        "projector & screen",
        "sound system",
        "dedicated waiter service",
        "customizable menus",
        "halal certified",
    ],
    "contact_email": "reservations@thegranddine.pk",
    "contact_phone": "+92-51-2876543",
    "is_active": True,
    "created_at": _now(),
    "updated_at": _now(),
}


# ---------------------------------------------------------------------------
# Floor & section layout
# ---------------------------------------------------------------------------
# Each room tuple:
#   (suffix, display_name, room_type, max_occ,
#    price_per_session, price_half_day, price_full_day,
#    amenities)
#
# Pricing rules:
#   half_day  ≈ 1.6× session price
#   full_day  ≈ 2.5× session price
#   multi_day = full_day × number of days
# ---------------------------------------------------------------------------

FLOOR_LAYOUT = [
    # ── Ground Floor ────────────────────────────────────────────────
    {
        "floor": "Ground Floor",
        "floor_code": "G",
        "sections": [
            {
                "section": "Café Lounge",
                "code": "CL",
                "rooms": [
                    ("01", "Café Corner A", "single", 4, 2_000, 3_200, 5_000, ["free wifi", "air conditioning", "charging ports"]),
                    ("02", "Café Corner B", "single", 4, 2_000, 3_200, 5_000, ["free wifi", "air conditioning", "charging ports"]),
                    ("03", "Café Window Seat", "single", 2, 1_500, 2_400, 3_500, ["free wifi", "air conditioning", "street view"]),
                    ("04", "Café Garden Booth", "single", 4, 2_500, 4_000, 6_000, ["free wifi", "air conditioning", "garden view"]),
                ],
            },
            {
                "section": "Outdoor Patio",
                "code": "OP",
                "rooms": [
                    ("01", "Patio Table A", "single", 4, 3_000, 4_800, 7_500, ["open air", "heaters", "garden view"]),
                    ("02", "Patio Table B", "single", 4, 3_000, 4_800, 7_500, ["open air", "heaters", "garden view"]),
                    ("03", "Patio Large", "double", 8, 5_000, 8_000, 12_000, ["open air", "heaters", "garden view", "fairy lights"]),
                ],
            },
        ],
    },
    # ── 1st Floor ───────────────────────────────────────────────────
    {
        "floor": "1st Floor",
        "floor_code": "1",
        "sections": [
            {
                "section": "Family Dining",
                "code": "FD",
                "rooms": [
                    ("01", "Family Room Tulip", "double", 6, 5_000, 8_000, 12_000, ["air conditioning", "kids menu", "high chair", "tv"]),
                    ("02", "Family Room Jasmine", "double", 6, 5_000, 8_000, 12_000, ["air conditioning", "kids menu", "high chair", "tv"]),
                    ("03", "Family Room Rose", "double", 8, 6_000, 9_500, 14_500, ["air conditioning", "kids menu", "high chair", "tv", "sofa seating"]),
                    ("04", "Family Room Lily", "double", 8, 6_500, 10_000, 15_500, ["air conditioning", "kids menu", "high chair", "tv", "sofa seating", "window view"]),
                ],
            },
            {
                "section": "Private Dining",
                "code": "PD",
                "rooms": [
                    ("01", "Private Room Noor", "single", 4, 4_000, 6_500, 10_000, ["air conditioning", "sound proof", "dedicated waiter", "mood lighting"]),
                    ("02", "Private Room Iqbal", "single", 4, 4_000, 6_500, 10_000, ["air conditioning", "sound proof", "dedicated waiter", "mood lighting"]),
                    ("03", "Private Room Rumi", "double", 6, 6_000, 9_500, 14_500, ["air conditioning", "sound proof", "dedicated waiter", "mood lighting", "tv"]),
                    ("04", "Private Room Ghalib", "double", 8, 7_500, 12_000, 18_000, ["air conditioning", "sound proof", "dedicated waiter", "mood lighting", "tv", "mini fridge"]),
                ],
            },
        ],
    },
    # ── 2nd Floor ───────────────────────────────────────────────────
    {
        "floor": "2nd Floor",
        "floor_code": "2",
        "sections": [
            {
                "section": "VIP Lounge",
                "code": "VL",
                "rooms": [
                    ("01", "VIP Sapphire", "suite", 10, 15_000, 24_000, 35_000, ["air conditioning", "premium decor", "dedicated waiter", "sound system", "mood lighting", "mini bar", "tv"]),
                    ("02", "VIP Emerald", "suite", 10, 15_000, 24_000, 35_000, ["air conditioning", "premium decor", "dedicated waiter", "sound system", "mood lighting", "mini bar", "tv"]),
                    ("03", "VIP Ruby", "suite", 12, 18_000, 28_000, 42_000, ["air conditioning", "premium decor", "dedicated waiter", "sound system", "mood lighting", "mini bar", "tv", "projector"]),
                    ("04", "VIP Diamond", "suite", 12, 20_000, 32_000, 48_000, ["air conditioning", "premium decor", "dedicated waiter", "sound system", "mood lighting", "mini bar", "tv", "projector", "private washroom"]),
                ],
            },
            {
                "section": "Executive Dining",
                "code": "ED",
                "rooms": [
                    ("01", "Executive Boardroom", "suite", 10, 18_000, 28_000, 42_000, ["air conditioning", "whiteboard", "projector", "conference phone", "dedicated waiter", "sound proof"]),
                    ("02", "Executive Meeting Room", "suite", 8, 14_000, 22_000, 33_000, ["air conditioning", "whiteboard", "projector", "conference phone", "dedicated waiter"]),
                    ("03", "Executive Corner Suite", "deluxe", 15, 25_000, 40_000, 60_000, ["air conditioning", "whiteboard", "projector", "conference phone", "dedicated waiter", "sound proof", "mini bar", "private washroom"]),
                ],
            },
        ],
    },
    # ── 3rd Floor ───────────────────────────────────────────────────
    {
        "floor": "3rd Floor",
        "floor_code": "3",
        "sections": [
            {
                "section": "Banquet Hall",
                "code": "BH",
                "rooms": [
                    ("01", "Banquet Hall Mughal", "deluxe", 20, 35_000, 55_000, 85_000, ["air conditioning", "stage", "sound system", "projector", "dance floor", "dedicated staff", "customizable decor"]),
                    ("02", "Banquet Hall Heritage", "deluxe", 20, 35_000, 55_000, 85_000, ["air conditioning", "stage", "sound system", "projector", "dance floor", "dedicated staff", "customizable decor"]),
                    ("03", "Mini Banquet Silk Road", "deluxe", 15, 22_000, 35_000, 52_000, ["air conditioning", "sound system", "projector", "dedicated staff", "customizable decor"]),
                ],
            },
            {
                "section": "Celebration Rooms",
                "code": "CR",
                "rooms": [
                    ("01", "Birthday Room Sparkle", "double", 8, 8_000, 12_500, 19_000, ["air conditioning", "party decor", "sound system", "mood lighting", "cake table"]),
                    ("02", "Birthday Room Confetti", "double", 8, 8_000, 12_500, 19_000, ["air conditioning", "party decor", "sound system", "mood lighting", "cake table"]),
                    ("03", "Anniversary Suite", "suite", 10, 12_000, 19_000, 28_000, ["air conditioning", "premium decor", "candle setup", "sound system", "mood lighting", "flower arrangement"]),
                ],
            },
        ],
    },
    # ── Rooftop (4th Floor) ─────────────────────────────────────────
    {
        "floor": "Rooftop",
        "floor_code": "R",
        "sections": [
            {
                "section": "Rooftop Terrace",
                "code": "RT",
                "rooms": [
                    ("01", "Terrace Table Margalla View", "single", 4, 4_000, 6_500, 10_000, ["open air", "heaters", "margalla hills view", "fairy lights"]),
                    ("02", "Terrace Table Sunset", "single", 4, 4_000, 6_500, 10_000, ["open air", "heaters", "sunset view", "fairy lights"]),
                    ("03", "Terrace Group Area", "double", 8, 7_000, 11_000, 17_000, ["open air", "heaters", "margalla hills view", "fairy lights", "bonfire"]),
                    ("04", "Terrace BBQ Zone", "double", 8, 8_500, 13_500, 20_000, ["open air", "live bbq grill", "margalla hills view", "fairy lights", "bonfire"]),
                ],
            },
            {
                "section": "Rooftop Lounge",
                "code": "RL",
                "rooms": [
                    ("01", "Sky Lounge A", "suite", 10, 16_000, 25_000, 38_000, ["enclosed glass", "margalla hills view", "sound system", "mood lighting", "mini bar", "shisha"]),
                    ("02", "Sky Lounge B", "suite", 10, 16_000, 25_000, 38_000, ["enclosed glass", "margalla hills view", "sound system", "mood lighting", "mini bar", "shisha"]),
                ],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Generate room documents
# ---------------------------------------------------------------------------

def generate_rooms() -> list[dict]:
    rooms = []
    now = _now()

    for floor_info in FLOOR_LAYOUT:
        floor_code = floor_info["floor_code"]
        floor_name = floor_info["floor"]

        for section in floor_info["sections"]:
            section_code = section["code"]
            section_name = section["section"]

            for suffix, display_name, room_type, max_occ, p_session, p_half, p_full, amenities in section["rooms"]:
                room_number = f"{floor_code}-{section_code}{suffix}"

                room = {
                    "room_id": _uid(),
                    "hotel_id": RESTAURANT_ID,
                    "room_number": room_number,
                    "room_type": room_type,
                    "price_per_session": float(p_session),
                    "price_half_day": float(p_half),
                    "price_full_day": float(p_full),
                    "max_occupancy": max_occ,
                    "amenities": sorted(set(a.lower() for a in amenities)),
                    "floor": floor_name,
                    "section": section_name,
                    "display_name": display_name,
                    "is_available": True,
                    "created_at": now,
                    "updated_at": now,
                }
                rooms.append(room)

    return rooms


# ---------------------------------------------------------------------------
# Sample customers
# ---------------------------------------------------------------------------

SAMPLE_CUSTOMERS = [
    {"full_name": "Ahmed Raza", "whatsapp_number": "923001234567", "email": "ahmed.raza@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Fatima Zahra", "whatsapp_number": "923009876543", "email": "fatima.zahra@yahoo.com", "nationality": "Pakistani"},
    {"full_name": "Usman Ali Khan", "whatsapp_number": "923331112233", "email": "usman.khan@hotmail.com", "nationality": "Pakistani"},
    {"full_name": "Ayesha Siddiqui", "whatsapp_number": "923214567890", "email": "ayesha.s@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Bilal Hussain", "whatsapp_number": "923451239876", "email": "bilal.h@outlook.com", "nationality": "Pakistani"},
    {"full_name": "Sana Malik", "whatsapp_number": "923007654321", "email": "sana.malik@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Hamza Sheikh", "whatsapp_number": "923111223344", "email": "hamza.sheikh@company.pk", "nationality": "Pakistani"},
    {"full_name": "Zainab Noor", "whatsapp_number": "923339988776", "email": "zainab.noor@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Imran Qureshi", "whatsapp_number": "923025551234", "email": "imran.q@business.pk", "nationality": "Pakistani"},
    {"full_name": "Hira Batool", "whatsapp_number": "923468765432", "email": "hira.b@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Omar Farooq", "whatsapp_number": "923155556789", "email": "omar.farooq@corp.pk", "nationality": "Pakistani"},
    {"full_name": "Maryam Akhtar", "whatsapp_number": "923229871234", "email": "maryam.a@yahoo.com", "nationality": "Pakistani"},
    {"full_name": "Ali Hassan", "whatsapp_number": "923361234567", "email": "ali.hassan@gmail.com", "nationality": "Pakistani"},
    {"full_name": "Nadia Parveen", "whatsapp_number": "923087776655", "email": "nadia.p@outlook.com", "nationality": "Pakistani"},
    {"full_name": "Tariq Mehmood", "whatsapp_number": "923419876512", "email": "tariq.m@business.pk", "nationality": "Pakistani"},
]


def generate_customers() -> list[dict]:
    now = _now()
    customers = []
    for c in SAMPLE_CUSTOMERS:
        customers.append({
            "customer_id": _uid(),
            "whatsapp_number": c["whatsapp_number"],
            "full_name": c["full_name"],
            "email": c["email"],
            "nationality": c["nationality"],
            "total_bookings": 0,
            "created_at": now,
            "updated_at": now,
        })
    return customers


# ---------------------------------------------------------------------------
# Sample bookings
# ---------------------------------------------------------------------------

def _get_price(room: dict, booking_type: str, num_days: int = 1) -> float:
    """Get total price for a room based on booking type."""
    if booking_type == "session":
        return room["price_per_session"]
    elif booking_type == "half_day":
        return room["price_half_day"]
    elif booking_type == "full_day":
        return room["price_full_day"]
    elif booking_type == "multi_day":
        return room["price_full_day"] * max(num_days, 1)
    return room["price_per_session"]


def generate_bookings(customers: list[dict], rooms: list[dict]) -> list[dict]:
    """Create 10 sample bookings with various statuses and booking types."""
    now = _now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    bookings = []
    year = today.year

    booking_scenarios = [
        # (customer_idx, room_name, status, days_offset, booking_type, num_days, num_guests, special_requests, booked_via)
        (0, "VIP Sapphire", "confirmed", 2, "session", 1, 8,
         "Birthday celebration for my wife. Please arrange a cake and flowers.", "whatsapp"),
        (1, "Family Room Tulip", "pending", 5, "session", 1, 5,
         "Kids aged 3 and 7. Need high chairs please.", "whatsapp"),
        (2, "Executive Boardroom", "confirmed", 1, "half_day", 1, 10,
         "Corporate lunch meeting. Need projector and whiteboard ready.", "api"),
        (3, "Terrace BBQ Zone", "pending", 7, "session", 1, 6,
         "Friends reunion BBQ. Vegetarian options needed for 2 guests.", "whatsapp"),
        (4, "Banquet Hall Mughal", "confirmed", 14, "full_day", 1, 20,
         "Engagement ceremony. Need stage, sound system, and floral decor.", "web"),
        (5, "Private Room Noor", "completed", -3, "session", 1, 3,
         "Quiet dinner.", "whatsapp"),
        (6, "Sky Lounge A", "completed", -7, "half_day", 1, 8,
         "Team celebration dinner with awards ceremony.", "api"),
        (7, "Birthday Room Sparkle", "cancelled", 4, "half_day", 1, 7,
         "Birthday party — cancelled due to change of plans.", "whatsapp"),
        (8, "Anniversary Suite", "confirmed", 3, "session", 1, 6,
         "25th wedding anniversary. Please arrange candles and special menu.", "whatsapp"),
        (9, "Banquet Hall Heritage", "pending", 10, "multi_day", 3, 20,
         "3-day wedding event. Day 1: Mehndi, Day 2: Baraat, Day 3: Walima.", "whatsapp"),
    ]

    for i, (cust_idx, room_name, status, day_offset, bt, num_days, guests, requests, via) in enumerate(booking_scenarios):
        # Find the matching room
        room = next((r for r in rooms if r["display_name"] == room_name), None)
        if room is None:
            continue

        customer = customers[cust_idx]
        checkin = today + timedelta(days=day_offset, hours=12)  # noon

        # Calculate checkout based on booking type
        if bt == "multi_day":
            checkout = checkin + timedelta(days=num_days)
        else:
            checkout = checkin + timedelta(hours=BOOKING_DURATIONS[bt])

        booking_id = f"BK-{year}-{i + 1:04d}"
        total_price = _get_price(room, bt, num_days)

        bookings.append({
            "booking_id": booking_id,
            "customer_id": customer["customer_id"],
            "hotel_id": RESTAURANT_ID,
            "room_id": room["room_id"],
            "booking_type": bt,
            "check_in_date": checkin,
            "check_out_date": checkout,
            "num_guests": guests,
            "total_price": total_price,
            "status": status,
            "special_requests": requests,
            "booked_via": via,
            "created_at": now - timedelta(days=max(0, -day_offset + 1)),
            "updated_at": now,
        })

    return bookings


# ---------------------------------------------------------------------------
# Insert into MongoDB
# ---------------------------------------------------------------------------

async def seed():
    print("=" * 65)
    print("  The Grand Dine — Restaurant Seed Script")
    print("=" * 65)

    # Generate data
    rooms = generate_rooms()
    customers = generate_customers()
    bookings = generate_bookings(customers, rooms)

    print(f"\nGenerated:")
    print(f"  Restaurant : 1 (The Grand Dine — Islamabad)")
    print(f"  Rooms      : {len(rooms)}")
    print(f"  Customers  : {len(customers)}")
    print(f"  Bookings   : {len(bookings)}")

    # Connect
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB_NAME]

    # Drop old data (idempotent)
    print("\nClearing existing data...")
    await db.hotels.delete_many({})
    await db.rooms.delete_many({})
    await db.customers.delete_many({})
    await db.bookings.delete_many({})
    await db.counters.delete_many({})

    # Insert
    await db.hotels.insert_one(RESTAURANT)
    await db.rooms.insert_many(rooms)
    await db.customers.insert_many(customers)
    if bookings:
        await db.bookings.insert_many(bookings)

    # Set the booking counter so new bookings continue from where we left off
    year = _now().year
    await db.counters.update_one(
        {"_id": f"booking_{year}"},
        {"$set": {"seq": len(bookings)}},
        upsert=True,
    )

    # Update customer total_bookings counts
    for b in bookings:
        if b["status"] != "cancelled":
            await db.customers.update_one(
                {"customer_id": b["customer_id"]},
                {"$inc": {"total_bookings": 1}},
            )

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print(f"\nInserted into MongoDB ({MONGO_URI} / {MONGO_DB_NAME}):")
    print(f"  hotels     : {await db.hotels.count_documents({})}")
    print(f"  rooms      : {await db.rooms.count_documents({})}")
    print(f"  customers  : {await db.customers.count_documents({})}")
    print(f"  bookings   : {await db.bookings.count_documents({})}")

    print("\n" + "-" * 65)
    print("RESTAURANT")
    print("-" * 65)
    h = await db.hotels.find_one({"hotel_id": RESTAURANT_ID})
    print(f"  {h['name']}")
    print(f"  Location: {h['location']}")
    print(f"  Rating: {h['star_rating']} stars")

    print("\n" + "-" * 65)
    print("FLOORS & ROOMS (with tiered pricing)")
    print("-" * 65)
    for floor_info in FLOOR_LAYOUT:
        floor_name = floor_info["floor"]
        floor_rooms = [r for r in rooms if r["floor"] == floor_name]
        print(f"\n  {floor_name} ({len(floor_rooms)} rooms)")
        for section in floor_info["sections"]:
            section_name = section["section"]
            sec_rooms = [r for r in floor_rooms if r["section"] == section_name]
            print(f"    {section_name}:")
            for r in sec_rooms:
                print(f"      [{r['room_number']}] {r['display_name']:<30} "
                      f"({r['room_type']:<7}) max {r['max_occupancy']:>2} guests")
                print(f"        Session: PKR {r['price_per_session']:>7,.0f}  |  "
                      f"Half-Day: PKR {r['price_half_day']:>7,.0f}  |  "
                      f"Full-Day: PKR {r['price_full_day']:>7,.0f}")

    print("\n" + "-" * 65)
    print("SAMPLE CUSTOMERS")
    print("-" * 65)
    async for c in db.customers.find().limit(5):
        print(f"  {c['full_name']:<20} {c['whatsapp_number']}  bookings: {c['total_bookings']}")
    print(f"  ... and {len(customers) - 5} more")

    print("\n" + "-" * 65)
    print("SAMPLE BOOKINGS")
    print("-" * 65)
    for b in bookings:
        cust = next(c for c in customers if c["customer_id"] == b["customer_id"])
        room = next(r for r in rooms if r["room_id"] == b["room_id"])
        bt_label = BOOKING_TYPE_LABELS.get(b["booking_type"], b["booking_type"])
        print(f"  {b['booking_id']}  {cust['full_name']:<18} -> {room['display_name']:<28} "
              f"[{b['status']:<10}]  {bt_label:<20}  "
              f"{b['num_guests']:>2} guests  PKR {b['total_price']:>8,.0f}")

    client.close()
    print("\n" + "=" * 65)
    print("  Seed complete!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(seed())
