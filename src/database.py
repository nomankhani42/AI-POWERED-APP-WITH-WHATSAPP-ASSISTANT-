"""MongoDB connection management module.

Provides helpers to open, close, and retrieve the async Motor database
client.  The connection is stored in module-level globals so that a
single client is shared across the entire application lifetime.

Typical usage (inside ``main.py`` lifespan)::

    await connect_db()   # on startup
    await create_indexes()  # ensure indexes exist
    ...
    await close_db()     # on shutdown

In endpoints / services::

    db = get_db()
    await db.messages.find_one(...)
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

from config import MONGO_URI, MONGO_DB_NAME

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_db() -> None:
    """Open a connection to MongoDB and select the application database.

    Reads ``MONGO_URI`` and ``MONGO_DB_NAME`` from the ``config`` module
    (ultimately sourced from environment variables / ``.env``).

    After this coroutine completes, :func:`get_db` will return a usable
    database handle.

    Side Effects:
        Sets the module-level ``_client`` and ``_db`` globals.

    Returns:
        None
    """
    global _client, _db
    _client = AsyncIOMotorClient(MONGO_URI)
    _db = _client[MONGO_DB_NAME]


async def close_db() -> None:
    """Close the active MongoDB connection and reset module globals.

    Safe to call even if no connection was ever opened (will no-op in
    that case).

    Side Effects:
        Closes ``_client`` and sets both ``_client`` and ``_db`` to
        ``None``.

    Returns:
        None
    """
    global _client, _db
    if _client:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    """Return the current async Motor database handle.

    Must be called **after** :func:`connect_db` has been awaited; otherwise
    a ``RuntimeError`` is raised.

    Returns:
        AsyncIOMotorDatabase: The Motor database object pointing at the
            database named by ``MONGO_DB_NAME``.

    Raises:
        RuntimeError: If the database connection has not been established
            yet (i.e. ``connect_db()`` was never called or ``close_db()``
            was already called).
    """
    if _db is None:
        raise RuntimeError("Database not connected. Call connect_db() first.")
    return _db


async def create_indexes() -> None:
    """Create MongoDB indexes for frequently queried fields.

    Should be called once during application startup (after
    :func:`connect_db`).  ``create_index`` is idempotent — calling it
    when the index already exists is a no-op.

    Indexes created:

    - **hotels**: ``hotel_id`` (unique)
    - **rooms**: ``room_id`` (unique), ``hotel_id``, compound
      ``(hotel_id, is_available)``
    - **customers**: ``customer_id`` (unique), ``whatsapp_number``
      (unique)
    - **bookings**: ``booking_id`` (unique), ``customer_id``,
      ``hotel_id``, ``status``, compound ``(hotel_id, status)``
    - **conversations**: ``whatsapp_number`` (unique),
      ``conversation_id`` (unique)
    - **counters**: uses ``_id`` as key (natural index)
    """
    db = get_db()

    # Hotels
    await db.hotels.create_index("hotel_id", unique=True)

    # Rooms
    await db.rooms.create_index("room_id", unique=True)
    await db.rooms.create_index("hotel_id")
    await db.rooms.create_index([("hotel_id", ASCENDING), ("is_available", ASCENDING)])

    # Customers
    await db.customers.create_index("customer_id", unique=True)
    await db.customers.create_index("whatsapp_number", unique=True)

    # Bookings
    await db.bookings.create_index("booking_id", unique=True)
    await db.bookings.create_index("customer_id")
    await db.bookings.create_index("hotel_id")
    await db.bookings.create_index("status")
    await db.bookings.create_index([("hotel_id", ASCENDING), ("status", ASCENDING)])

    # Conversations
    await db.conversations.create_index("whatsapp_number", unique=True)
    await db.conversations.create_index("conversation_id", unique=True)
