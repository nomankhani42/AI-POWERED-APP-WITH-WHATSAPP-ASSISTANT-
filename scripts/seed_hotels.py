"""Seed script — 100 Pakistani hotels with rooms.

Usage:
    cd src && python ../scripts/seed_hotels.py

Requires MongoDB to be running. Reads connection settings from .env
via the project's config module.
"""

import asyncio
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, MONGO_DB_NAME

# ---------------------------------------------------------------------------
# Pakistani cities with typical hotel-name prefixes
# ---------------------------------------------------------------------------

CITIES = [
    # (city, province/area)
    ("Islamabad", "Islamabad Capital Territory"),
    ("Rawalpindi", "Punjab"),
    ("Lahore", "Punjab"),
    ("Karachi", "Sindh"),
    ("Faisalabad", "Punjab"),
    ("Multan", "Punjab"),
    ("Peshawar", "Khyber Pakhtunkhwa"),
    ("Quetta", "Balochistan"),
    ("Sialkot", "Punjab"),
    ("Hyderabad", "Sindh"),
    ("Abbottabad", "Khyber Pakhtunkhwa"),
    ("Murree", "Punjab"),
    ("Swat", "Khyber Pakhtunkhwa"),
    ("Hunza", "Gilgit-Baltistan"),
    ("Skardu", "Gilgit-Baltistan"),
    ("Gilgit", "Gilgit-Baltistan"),
    ("Naran", "Khyber Pakhtunkhwa"),
    ("Chitral", "Khyber Pakhtunkhwa"),
    ("Bahawalpur", "Punjab"),
    ("Gwadar", "Balochistan"),
    ("Muzaffarabad", "Azad Kashmir"),
    ("Bhurban", "Punjab"),
    ("Taxila", "Punjab"),
    ("Nathia Gali", "Khyber Pakhtunkhwa"),
    ("Ziarat", "Balochistan"),
]

HOTEL_PREFIXES = [
    "Grand", "Royal", "Pearl", "Shangrila", "Serena", "Marriott", "Avari",
    "Luxus", "Falcon", "Dreamland", "Shelton", "Envoy", "Regent", "Crown",
    "Heritage", "Riviera", "Panorama", "Pine", "Sapphire", "Oasis",
    "Hilltop", "Valley", "Paradise", "Elysium", "Margalla", "Indus",
    "Summit", "Crescent", "Golden", "Silver Star",
]

HOTEL_SUFFIXES = [
    "Hotel", "Hotel & Suites", "Continental", "Inn", "Resort",
    "Residency", "Lodge", "Guest House", "Boutique Hotel", "Plaza",
]

# ---------------------------------------------------------------------------
# Amenities pools
# ---------------------------------------------------------------------------

HOTEL_AMENITIES = [
    "free wifi", "swimming pool", "gym", "spa", "restaurant",
    "parking", "room service", "laundry", "airport shuttle",
    "conference hall", "business center", "rooftop terrace",
    "bar", "garden", "kids play area", "concierge", "valet parking",
    "cctv security", "power backup", "ev charging",
]

ROOM_AMENITIES_MAP = {
    "single": [
        "free wifi", "air conditioning", "tv", "mini fridge",
        "attached bathroom", "work desk",
    ],
    "double": [
        "free wifi", "air conditioning", "tv", "mini fridge",
        "attached bathroom", "tea/coffee maker", "work desk", "wardrobe",
    ],
    "suite": [
        "free wifi", "air conditioning", "smart tv", "mini bar",
        "attached bathroom", "jacuzzi", "living area", "work desk",
        "balcony", "tea/coffee maker", "bathrobe",
    ],
    "deluxe": [
        "free wifi", "air conditioning", "smart tv", "mini bar",
        "attached bathroom", "rain shower", "living area", "work desk",
        "balcony", "tea/coffee maker", "bathrobe", "premium toiletries",
    ],
}

# Price ranges in PKR per night  (min, max)
PRICE_RANGES = {
    "single": (3_000, 10_000),
    "double": (6_000, 18_000),
    "suite":  (15_000, 50_000),
    "deluxe": (25_000, 80_000),
}

MAX_OCCUPANCY = {
    "single": 1,
    "double": 2,
    "suite": 4,
    "deluxe": 3,
}

ROOM_TYPES = ["single", "double", "suite", "deluxe"]

# ---------------------------------------------------------------------------
# Hotel descriptions (templates filled per-hotel)
# ---------------------------------------------------------------------------

DESCRIPTIONS = [
    "A {star}-star property in the heart of {city} offering comfortable stays and warm Pakistani hospitality.",
    "Located in {city}, this {star}-star hotel combines modern amenities with traditional charm.",
    "Experience luxury and convenience at this {star}-star hotel in {city}, {province}.",
    "Nestled in beautiful {city}, this {star}-star establishment is perfect for both business and leisure travellers.",
    "A premier {star}-star accommodation in {city} known for exceptional service and stunning views.",
    "Enjoy world-class hospitality at this {star}-star hotel in {city}, featuring top-notch facilities.",
    "This {star}-star gem in {city} offers a serene retreat with all modern comforts.",
    "A well-appointed {star}-star property in {city} ideal for families, couples, and solo travellers.",
]


def _uid() -> str:
    return uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pick_amenities(pool: list[str], lo: int = 4, hi: int = 8) -> list[str]:
    return sorted(random.sample(pool, k=min(random.randint(lo, hi), len(pool))))


def _round_price(val: float) -> float:
    """Round to nearest 500 PKR."""
    return round(val / 500) * 500


# ---------------------------------------------------------------------------
# Generate data
# ---------------------------------------------------------------------------

def generate_hotels(n: int = 100) -> tuple[list[dict], list[dict]]:
    hotels: list[dict] = []
    rooms: list[dict] = []
    used_names: set[str] = set()

    for i in range(n):
        city, province = random.choice(CITIES)

        # Build a unique hotel name
        while True:
            prefix = random.choice(HOTEL_PREFIXES)
            suffix = random.choice(HOTEL_SUFFIXES)
            name = f"{prefix} {suffix} {city}"
            if name not in used_names:
                used_names.add(name)
                break

        star = random.choices([2, 3, 4, 5], weights=[10, 30, 35, 25])[0]
        now = _now()
        hotel_id = _uid()

        desc = random.choice(DESCRIPTIONS).format(
            star=star, city=city, province=province,
        )

        hotel = {
            "hotel_id": hotel_id,
            "name": name,
            "location": f"{city}, {province}, Pakistan",
            "description": desc,
            "star_rating": star,
            "amenities": _pick_amenities(HOTEL_AMENITIES),
            "contact_email": f"info@{prefix.lower().replace(' ', '')}{city.lower().replace(' ', '')}.pk",
            "contact_phone": f"+92-{random.randint(300,345)}-{random.randint(1000000,9999999)}",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        hotels.append(hotel)

        # Decide which room types this hotel has
        if star <= 2:
            available_types = random.sample(["single", "double"], k=2)
        elif star == 3:
            available_types = random.sample(["single", "double", "suite"], k=random.randint(2, 3))
        else:
            available_types = random.sample(ROOM_TYPES, k=random.randint(3, 4))

        floor = 1
        room_counter = 0
        for rtype in available_types:
            count = random.randint(3, 8)  # rooms of this type
            lo, hi = PRICE_RANGES[rtype]

            # Scale price with star rating
            factor = 0.6 + (star - 1) * 0.2  # 2-star=0.8x … 5-star=1.4x
            base_price = _round_price(random.uniform(lo, hi) * factor)

            for j in range(1, count + 1):
                room_number = f"{floor}{j:02d}"
                room_counter += 1

                room_amenities = list(ROOM_AMENITIES_MAP[rtype])
                # Occasionally add an extra amenity
                extras = ["mountain view", "city view", "sound proofing", "prayer mat", "ironing board"]
                if random.random() < 0.3:
                    room_amenities.append(random.choice(extras))

                room = {
                    "room_id": _uid(),
                    "hotel_id": hotel_id,
                    "room_number": room_number,
                    "room_type": rtype,
                    "price_per_night": base_price + _round_price(random.uniform(-500, 1500)),
                    "max_occupancy": MAX_OCCUPANCY[rtype],
                    "amenities": sorted(set(room_amenities)),
                    "is_available": random.random() < 0.85,  # 85 % available
                    "created_at": now,
                    "updated_at": now,
                }
                rooms.append(room)
            floor += 1

    return hotels, rooms


# ---------------------------------------------------------------------------
# Insert into MongoDB
# ---------------------------------------------------------------------------

async def seed():
    print("Generating 100 Pakistani hotels with rooms...")
    hotels, rooms = generate_hotels(100)

    print(f"  Hotels : {len(hotels)}")
    print(f"  Rooms  : {len(rooms)}")

    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB_NAME]

    # Drop old data so the script is idempotent
    await db.hotels.delete_many({})
    await db.rooms.delete_many({})

    await db.hotels.insert_many(hotels)
    await db.rooms.insert_many(rooms)

    # Quick summary
    print(f"\nInserted into MongoDB ({MONGO_URI} / {MONGO_DB_NAME}):")
    print(f"  hotels collection : {await db.hotels.count_documents({})}")
    print(f"  rooms  collection : {await db.rooms.count_documents({})}")

    # Print a few samples
    print("\nSample hotels:")
    async for h in db.hotels.find().limit(5):
        room_count = await db.rooms.count_documents({"hotel_id": h["hotel_id"]})
        print(f"  [{h['star_rating']}*] {h['name']} — {h['location']} ({room_count} rooms)")

    client.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(seed())
