"""Seed dummy rooms into MongoDB — every room type, fully populated.

Run with:
    uv run python scripts/seed_rooms.py            # insert missing / refresh existing
    uv run python scripts/seed_rooms.py --reset    # wipe the rooms collection first

Requires MONGODB_URI (and the other settings secrets, e.g. OPENAI_API_KEY) in the
environment / .env so ``get_settings()`` validates.

The ``Room`` schema (see app/db/documents.py) is: name, room_type, capacity, description,
is_active. ``room_type`` is the canonical ``RoomType`` enum — the single source of truth for
the catalog (007 FR-009); the catalogs below are keyed off it and checked for exhaustiveness
so seed data and schema cannot drift. Bed setup, nightly price, and amenities are folded into
``description`` since the schema has no dedicated fields for them. Each type carries a
verified public Unsplash photo URL (``image_url``) used for WhatsApp carousel cards —
Meta must be able to fetch it (HTTPS, no auth, JPEG ≤5 MB).
"""

from __future__ import annotations

import asyncio
import sys

from app.db.documents import Room, RoomType
from app.db.mongo import close_db, init_db

# Baseline amenities every room includes; appended to each description.
BASE_AMENITIES = "free Wi-Fi, smart TV, air conditioning, private bathroom, tea/coffee"

# Canonical room-type catalog: type -> attributes. ``capacity`` is authoritative here and is
# what the booking logic uses; ``price_per_night`` and ``beds`` enrich the description.
ROOM_TYPES: dict[RoomType, dict] = {
    RoomType.single: {
        "image": "https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 1,
        "price_per_night": 79,
        "beds": "1 twin bed",
        "summary": "Cosy room for solo travellers.",
    },
    RoomType.twin: {
        "image": "https://images.unsplash.com/photo-1618773928121-c32242e63f39?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 2,
        "price_per_night": 119,
        "beds": "2 single beds",
        "summary": "Two separate beds, ideal for friends or colleagues.",
    },
    RoomType.double: {
        "image": "https://images.unsplash.com/photo-1611892440504-42a792e24d32?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 2,
        "price_per_night": 119,
        "beds": "1 queen bed",
        "summary": "Comfortable room with a queen bed.",
    },
    RoomType.deluxe: {
        "image": "https://images.unsplash.com/photo-1590490360182-c33d57733427?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 2,
        "price_per_night": 169,
        "beds": "1 king bed",
        "summary": "Spacious king room with a city view and a seating area.",
    },
    RoomType.accessible: {
        "image": "https://images.unsplash.com/photo-1566665797739-1674de7a421a?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 2,
        "price_per_night": 129,
        "beds": "1 queen bed",
        "summary": "Step-free, wheelchair-accessible room with a roll-in shower and grab rails.",
    },
    RoomType.family: {
        "image": "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 4,
        "price_per_night": 219,
        "beds": "1 king bed + 2 single beds",
        "summary": "Roomy family option that sleeps up to four.",
    },
    RoomType.executive: {
        "image": "https://images.unsplash.com/photo-1598928506311-c55ded91a20c?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 2,
        "price_per_night": 259,
        "beds": "1 king bed",
        "summary": "King room with a work desk and executive lounge access.",
    },
    RoomType.suite: {
        "image": "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80&fm=jpg&fit=crop",
        "capacity": 4,
        "price_per_night": 299,
        "beds": "1 king bed + sofa bed",
        "summary": "Two-room suite with a separate living area and premium amenities.",
    },
}

# Room numbers grouped by type (unique ``name`` per room — enforced by a unique index).
ROOMS_BY_TYPE: dict[RoomType, list[str]] = {
    RoomType.single: ["101", "102", "103"],
    RoomType.twin: ["104", "105"],
    RoomType.double: ["201", "202", "203"],
    RoomType.deluxe: ["204", "205"],
    RoomType.accessible: ["G01", "G02"],
    RoomType.family: ["301", "302"],
    RoomType.executive: ["401", "402"],
    RoomType.suite: ["501", "502"],
}

# Every canonical type must be seeded — a new RoomType member fails fast here until the
# catalogs above cover it.
assert set(ROOM_TYPES) == set(RoomType), "seed catalog out of sync with RoomType enum"
assert set(ROOMS_BY_TYPE) == set(RoomType), "room numbers out of sync with RoomType enum"

# One out-of-service room so the ``is_active`` filter has something to exclude in demos.
INACTIVE_ROOMS: list[tuple[str, RoomType]] = [("206", RoomType.double)]


def _describe(room_type: RoomType) -> str:
    spec = ROOM_TYPES[room_type]
    return (
        f"{spec['summary']} {spec['beds']}. Sleeps {spec['capacity']}. "
        f"${spec['price_per_night']}/night. Amenities: {BASE_AMENITIES}."
    )


def _build_rooms() -> list[dict]:
    rooms: list[dict] = []
    for room_type, numbers in ROOMS_BY_TYPE.items():
        spec = ROOM_TYPES[room_type]
        for number in numbers:
            rooms.append(
                {
                    "name": f"Room {number}",
                    "room_type": room_type,
                    "capacity": spec["capacity"],
                    "description": _describe(room_type),
                    "image_url": spec["image"],
                    "is_active": True,
                }
            )
    for number, room_type in INACTIVE_ROOMS:
        rooms.append(
            {
                "name": f"Room {number}",
                "room_type": room_type,
                "capacity": ROOM_TYPES[room_type]["capacity"],
                "description": _describe(room_type) + " (Currently under renovation.)",
                "image_url": ROOM_TYPES[room_type]["image"],
                "is_active": False,
            }
        )
    return rooms


async def main(reset: bool) -> None:
    await init_db()
    try:
        if reset:
            result = await Room.find_all().delete()
            count = getattr(result, "deleted_count", 0)
            print(f"reset: cleared {count} existing room(s)")

        rooms = _build_rooms()
        inserted = updated = 0
        for data in rooms:
            existing = await Room.find_one(Room.name == data["name"])
            if existing is None:
                await Room(**data).insert()
                inserted += 1
                print(f"  + inserted {data['name']:<9} ({data['room_type'].value})")
            else:
                # Refresh fields so re-running keeps existing rooms in sync with the catalog.
                existing.room_type = data["room_type"]
                existing.capacity = data["capacity"]
                existing.description = data["description"]
                existing.image_url = data["image_url"]
                existing.is_active = data["is_active"]
                await existing.save()
                updated += 1
                print(f"  ~ updated  {data['name']:<9} ({data['room_type'].value})")

        by_type = ", ".join(f"{rt.value}×{len(nums)}" for rt, nums in ROOMS_BY_TYPE.items())
        print(
            f"\nDone: {inserted} inserted, {updated} updated, "
            f"{len(rooms)} total across {len(ROOM_TYPES)} room types."
        )
        print("Room types: " + by_type)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main(reset="--reset" in sys.argv[1:]))
