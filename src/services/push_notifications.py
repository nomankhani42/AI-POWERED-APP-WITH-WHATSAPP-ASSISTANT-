"""Expo Push Notification service.

Sends push notifications to registered admin devices via the Expo
Push Notification API.  Tokens are stored in a MongoDB collection
so that notifications reach *all* registered admin devices.

The Expo push API is free and does not require an access token for
basic usage — we just POST JSON to https://exp.host/--/api/v2/push/send.
"""

from __future__ import annotations

import httpx

from database import get_db

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
COLLECTION = "push_tokens"


async def register_token(token: str) -> None:
    """Store an Expo push token (upsert — no duplicates)."""
    db = get_db()
    await db[COLLECTION].update_one(
        {"token": token},
        {"$set": {"token": token}},
        upsert=True,
    )


async def remove_token(token: str) -> None:
    """Remove a previously registered token."""
    db = get_db()
    await db[COLLECTION].delete_one({"token": token})


async def _get_all_tokens() -> list[str]:
    """Return all registered Expo push tokens."""
    db = get_db()
    docs = await db[COLLECTION].find({}, {"token": 1}).to_list(length=500)
    return [d["token"] for d in docs if d.get("token")]


async def send_push_notification(
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to **all** registered admin devices.

    Args:
        title: Notification title.
        body: Notification body text.
        data: Optional JSON-serialisable dict attached to the notification.
    """
    tokens = await _get_all_tokens()
    if not tokens:
        print(">>> [Push] No registered push tokens — skipping")
        return

    messages = [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data or {},
            "channelId": "bookings",
        }
        for token in tokens
    ]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            print(f">>> [Push] Sent to {len(tokens)} device(s): {resp.status_code}")
    except Exception as e:
        print(f">>> [Push] Failed to send push notification: {e}")
