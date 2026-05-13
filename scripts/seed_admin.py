"""Seed script — Create the initial admin user.

Usage:
    cd src && python ../scripts/seed_admin.py

Requires MongoDB to be running. Reads connection settings from .env.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, MONGO_DB_NAME

COLLECTION = "admins"

# ── Admin account to create ─────────────────────────────────────────────────
ADMIN_USER = {
    "full_name": "Noman Khan",
    "email": "admin@granddine.com",
    "password": "your-password",        # will be hashed
    "phone": "+923001234567",
    "role": "admin",                     # super_admin | admin | manager | user
}
# ────────────────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def main():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB_NAME]

    # Check if admin already exists
    existing = await db[COLLECTION].find_one({"email": ADMIN_USER["email"]})
    if existing:
        print(f"⚠️  Admin with email '{ADMIN_USER['email']}' already exists (ID: {existing['admin_id']})")
        print("   Skipping creation. Delete the document first if you want to recreate.")
        client.close()
        return

    now = datetime.now(timezone.utc)
    doc = {
        "admin_id": uuid4().hex[:12],
        "full_name": ADMIN_USER["full_name"],
        "email": ADMIN_USER["email"].strip().lower(),
        "password_hash": hash_password(ADMIN_USER["password"]),
        "phone": ADMIN_USER["phone"],
        "role": ADMIN_USER["role"],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    result = await db[COLLECTION].insert_one(doc)

    # Create unique index on email
    await db[COLLECTION].create_index("email", unique=True)
    await db[COLLECTION].create_index("admin_id", unique=True)

    print("✅ Admin user created successfully!")
    print(f"   Name:     {doc['full_name']}")
    print(f"   Email:    {doc['email']}")
    print(f"   Phone:    {doc['phone']}")
    print(f"   Role:     {doc['role']}")
    print(f"   Admin ID: {doc['admin_id']}")
    print(f"   Mongo _id: {result.inserted_id}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
