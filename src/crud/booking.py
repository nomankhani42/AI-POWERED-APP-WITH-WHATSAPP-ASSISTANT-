"""CRUD operations for the ``bookings`` MongoDB collection.

Every function is async and takes the Motor database handle as its
first parameter.

The ``booking_id`` is auto-generated in the format ``BK-YYYY-XXXX``
using an atomic counter stored in the ``counters`` collection.
"""

from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.booking import BookedVia, BookingCreate, BookingInDB, BookingStatus, BookingUpdate
from models.common import utc_now

COLLECTION = "bookings"
COUNTERS_COLLECTION = "counters"
CUSTOMERS_COLLECTION = "customers"


async def _next_booking_id(db: AsyncIOMotorDatabase) -> str:
    """Generate the next ``BK-YYYY-XXXX`` booking ID atomically.

    Uses MongoDB's ``findAndModify`` (via ``find_one_and_update``) on a
    ``counters`` document to guarantee uniqueness even under concurrent
    requests.

    Args:
        db: Motor database handle.

    Returns:
        A string like ``"BK-2024-0001"``.
    """
    year = datetime.now().year
    counter_id = f"booking_{year}"
    result = await db[COUNTERS_COLLECTION].find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = result["seq"]
    return f"BK-{year}-{seq:04d}"


async def create_booking(
    db: AsyncIOMotorDatabase,
    data: BookingCreate,
    total_price: float,
) -> BookingInDB:
    """Insert a new booking document and increment the customer's
    ``total_bookings`` counter.

    Args:
        db: Motor database handle.
        data: Validated booking creation payload.
        total_price: Pre-calculated total price for the stay.

    Returns:
        The newly created booking document.
    """
    now = utc_now()
    booking_id = await _next_booking_id(db)

    doc: dict[str, Any] = {
        "booking_id": booking_id,
        **data.model_dump(),
        "total_price": total_price,
        "status": BookingStatus.PENDING,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id

    # Increment customer's total_bookings counter
    await db[CUSTOMERS_COLLECTION].update_one(
        {"customer_id": data.customer_id},
        {"$inc": {"total_bookings": 1}},
    )

    return BookingInDB(**doc)


async def get_booking_by_id(
    db: AsyncIOMotorDatabase,
    booking_oid: str,
) -> BookingInDB | None:
    """Fetch a booking by its MongoDB ``_id`` (as hex string).

    Args:
        db: Motor database handle.
        booking_oid: The ``_id`` hex string.

    Returns:
        The booking document, or ``None`` if not found.
    """
    from bson import ObjectId

    if not ObjectId.is_valid(booking_oid):
        return None
    doc = await db[COLLECTION].find_one({"_id": ObjectId(booking_oid)})
    return BookingInDB(**doc) if doc else None


async def get_booking_by_booking_id(
    db: AsyncIOMotorDatabase,
    booking_id: str,
) -> BookingInDB | None:
    """Fetch a booking by its human-readable ``booking_id``
    (e.g. ``BK-2024-0001``).

    Args:
        db: Motor database handle.
        booking_id: The human-readable booking code.

    Returns:
        The booking document, or ``None`` if not found.
    """
    doc = await db[COLLECTION].find_one({"booking_id": booking_id})
    return BookingInDB(**doc) if doc else None


async def get_bookings_by_customer(
    db: AsyncIOMotorDatabase,
    customer_id: str,
    skip: int = 0,
    limit: int = 50,
) -> list[BookingInDB]:
    """Return all bookings for a given customer.

    Args:
        db: Motor database handle.
        customer_id: The customer identifier.
        skip: Number of documents to skip.
        limit: Maximum documents to return.

    Returns:
        List of booking documents, newest first.
    """
    cursor = (
        db[COLLECTION]
        .find({"customer_id": customer_id})
        .skip(skip)
        .limit(limit)
        .sort("created_at", -1)
    )
    return [BookingInDB(**doc) async for doc in cursor]


async def get_bookings_by_hotel(
    db: AsyncIOMotorDatabase,
    hotel_id: str,
    skip: int = 0,
    limit: int = 50,
) -> list[BookingInDB]:
    """Return all bookings for a given hotel.

    Args:
        db: Motor database handle.
        hotel_id: The hotel identifier.
        skip: Number of documents to skip.
        limit: Maximum documents to return.

    Returns:
        List of booking documents, newest first.
    """
    cursor = (
        db[COLLECTION]
        .find({"hotel_id": hotel_id})
        .skip(skip)
        .limit(limit)
        .sort("created_at", -1)
    )
    return [BookingInDB(**doc) async for doc in cursor]


async def update_booking_status(
    db: AsyncIOMotorDatabase,
    booking_id: str,
    status: BookingStatus,
) -> BookingInDB | None:
    """Update the status of a booking by its human-readable
    ``booking_id``.

    Args:
        db: Motor database handle.
        booking_id: The human-readable booking code.
        status: The new status value.

    Returns:
        The updated booking document, or ``None`` if not found.
    """
    result = await db[COLLECTION].find_one_and_update(
        {"booking_id": booking_id},
        {"$set": {"status": status, "updated_at": utc_now()}},
        return_document=True,
    )
    return BookingInDB(**result) if result else None


async def cancel_booking(
    db: AsyncIOMotorDatabase,
    booking_id: str,
) -> BookingInDB | None:
    """Cancel a booking (set status to ``cancelled``).

    Only bookings with status ``pending`` or ``confirmed`` can be
    cancelled.

    Args:
        db: Motor database handle.
        booking_id: The human-readable booking code.

    Returns:
        The cancelled booking document, or ``None`` if not found or
        not cancellable.
    """
    result = await db[COLLECTION].find_one_and_update(
        {
            "booking_id": booking_id,
            "status": {"$in": [BookingStatus.PENDING, BookingStatus.CONFIRMED]},
        },
        {"$set": {"status": BookingStatus.CANCELLED, "updated_at": utc_now()}},
        return_document=True,
    )
    return BookingInDB(**result) if result else None


async def get_active_bookings(
    db: AsyncIOMotorDatabase,
    skip: int = 0,
    limit: int = 50,
) -> list[BookingInDB]:
    """Return all bookings with status ``pending`` or ``confirmed``.

    Args:
        db: Motor database handle.
        skip: Number of documents to skip.
        limit: Maximum documents to return.

    Returns:
        List of active booking documents, newest first.
    """
    cursor = (
        db[COLLECTION]
        .find({"status": {"$in": [BookingStatus.PENDING, BookingStatus.CONFIRMED]}})
        .skip(skip)
        .limit(limit)
        .sort("created_at", -1)
    )
    return [BookingInDB(**doc) async for doc in cursor]


async def get_all_bookings(
    db: AsyncIOMotorDatabase,
    status: BookingStatus | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[BookingInDB]:
    """Return all bookings, optionally filtered by status.

    Args:
        db: Motor database handle.
        status: If provided, only return bookings with this status.
        skip: Number of documents to skip.
        limit: Maximum documents to return.

    Returns:
        List of booking documents, newest first.
    """
    query: dict[str, Any] = {}
    if status is not None:
        query["status"] = status
    cursor = (
        db[COLLECTION]
        .find(query)
        .skip(skip)
        .limit(limit)
        .sort("created_at", -1)
    )
    return [BookingInDB(**doc) async for doc in cursor]
