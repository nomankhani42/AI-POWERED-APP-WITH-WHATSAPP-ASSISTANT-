"""FastAPI application entry point."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from agents import set_tracing_disabled

from config import WHATSAPP_VERIFY_TOKEN
from database import connect_db, close_db, create_indexes
from services.webhook_handler import handle_webhook
from endpoints.users import router as users_router
from endpoints.whatsapp import router as whatsapp_router
from endpoints.bookings import router as bookings_router
from endpoints.customers import router as customers_router
from endpoints.hotels import router as hotels_router
from endpoints.notifications import router as notifications_router
from endpoints.admin import router as admin_router
from endpoints.chat import router as chat_router

set_tracing_disabled(disabled=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown: connect to MongoDB and create indexes."""
    await connect_db()
    await create_indexes()
    print(">>> MongoDB connected & indexes created")

    # Pre-warm the WhatsApp Calling greeting so the first incoming call doesn't
    # pay TTS synthesis latency on top of ICE handshake time.
    try:
        from services.wa_calling import _get_greeting_pcm
        import asyncio as _asyncio
        _asyncio.create_task(_get_greeting_pcm())
    except Exception as e:
        print(f">>> Greeting pre-warm skipped: {e}")

    yield
    await close_db()
    print(">>> MongoDB connection closed")


app = FastAPI(title="The Grand Dine — Restaurant Booking Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)
app.include_router(whatsapp_router)
app.include_router(bookings_router)
app.include_router(customers_router)
app.include_router(hotels_router)
app.include_router(notifications_router)
app.include_router(admin_router)
app.include_router(chat_router)


# -- Root webhook (Meta may hit this path) ----------------------------------

@app.get("/", response_class=PlainTextResponse)
async def verify_webhook_root(request: Request):
    params = dict(request.query_params)
    hub_mode = params.get("hub.mode")
    hub_token = params.get("hub.verify_token")
    hub_challenge = params.get("hub.challenge")

    if hub_mode == "subscribe" and hub_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge, status_code=200)

    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/")
async def receive_webhook_root(request: Request):
    print(">>> HIT: POST / (root)")
    try:
        payload = await request.json()
    except Exception:
        print(">>> Invalid JSON at POST /")
        return {"status": "error"}

    await handle_webhook(payload, source="ROOT /")
    return {"status": "ok"}
