"""Shared pytest fixtures and test environment setup.

Required secrets (`OPENAI_API_KEY`, `MONGODB_URI`, and the feature-003 Meta/speech secrets)
are set here BEFORE the app is imported so `get_settings()` validates without needing real
credentials, a live DB, or network access. Tests never call the real OpenAI/Deepgram/Cartesia/
Meta APIs or a live MongoDB unless explicitly opted in — the chat endpoint test stubs the
agent, and DB tests skip when no MongoDB is reachable.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
os.environ.setdefault(
    "MONGODB_URI", "mongodb://localhost:27017/?serverSelectionTimeoutMS=800"
)
os.environ.setdefault("MONGODB_DB", "voice_agent_test")

# --- Feature 003 (voice call webhook & speech services) required secrets ---
# Dummy, deterministic values so Settings() validates and contract tests can compute a
# matching X-Hub-Signature-256 without any real Meta/Deepgram/Cartesia credentials.
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")
os.environ.setdefault("CARTESIA_API_KEY", "test-cartesia-key")
os.environ.setdefault("WHATSAPP_TOKEN", "test-whatsapp-token")
os.environ.setdefault("WHATSAPP_PHONE_ID", "test-phone-id")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test-app-secret")
# Pin signature verification ON for tests, independent of the developer's local .env
# (which may set WHATSAPP_SKIP_SIGNATURE=true for manual debugging). An OS env var takes
# precedence over the .env file, so this keeps the signature contract tests deterministic.
os.environ.setdefault("WHATSAPP_SKIP_SIGNATURE", "false")

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

# Ensure settings pick up the env above rather than any cached instance.
get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """In-process HTTP client for the FastAPI app (lifespan not started → no DB needed)."""

    return TestClient(create_app())
