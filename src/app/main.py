"""Application entry point.

Defines the FastAPI app factory and wires routers from the ``api.routes`` package. Route
logic lives in the route modules, not here (constitution Principle I). A lifespan initializes
the durable MongoDB connection on startup and closes it on shutdown. Run locally with::

    uv run uvicorn app.main:app --reload --app-dir src
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import chat, greeting, health, whatsapp_calling
from app.core.config import get_settings


def _configure_logging(level: str) -> None:
    """Send the app's own ``app.*`` loggers to stdout at ``level``.

    Without this the call-flow INFO logs (attended, transcripts, replies, tool calls, etc.)
    never print: under uvicorn the root logger has no INFO handler, so ``app.*`` records fall
    through to Python's WARNING-only last-resort handler. Configuring the ``app`` parent logger
    directly keeps this isolated from uvicorn's own handlers and is idempotent across the
    repeated ``create_app()`` calls tests make.
    """

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level.upper())
    if not any(getattr(h, "_app_stdout", False) for h in app_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        handler._app_stdout = True  # type: ignore[attr-defined]  # marker for the idempotency guard
        app_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.mongo import close_db, init_db

    await init_db()
    try:
        yield
    finally:
        await close_db()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""

    settings = get_settings()
    _configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.include_router(greeting.router)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(whatsapp_calling.router)

    return app


app = create_app()
