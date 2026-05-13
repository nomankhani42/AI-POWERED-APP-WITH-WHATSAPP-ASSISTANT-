"""Application configuration loaded from environment variables.

All settings are read once at import time from a ``.env`` file located
in the project root (one directory above ``src/``).  Other modules
should ``import`` the constants they need from this module rather than
calling ``os.getenv()`` directly.

Environment Variables:
    MONGO_URI (str): MongoDB connection string.
        Default: ``"mongodb://localhost:27017"``.
    MONGO_DB_NAME (str): Name of the MongoDB database to use.
        Default: ``"support_agent"``.
    WHATSAPP_VERIFY_TOKEN (str): Secret token used to verify Meta
        webhook registration requests.
    WHATSAPP_ACCESS_TOKEN (str): Bearer token for the WhatsApp Cloud
        API, obtained from the Meta Developer Portal.
    WHATSAPP_PHONE_NUMBER_ID (str): The Phone-Number ID of your
        WhatsApp Business number (from Meta Business Settings).
    WHATSAPP_BUSINESS_ACCOUNT_ID (str): The WhatsApp Business Account
        ID (from Meta Business Settings).

Module Constants:
    WHATSAPP_API_BASE (str): Base URL for Meta Graph API v21.0
        (``"https://graph.facebook.com/v21.0"``).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (one level up from src/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# MongoDB
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "support_agent")

# WhatsApp Meta Cloud API
WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")

# OpenRouter
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# Google Gemini
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# OpenAI
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Groq
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# Upstash Redis (session memory)
UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

# SQLite session DB for agent conversation memory
SQLITE_SESSION_DB: str = os.getenv("SQLITE_SESSION_DB", "conversations.db")

# WebRTC TURN/ICE — required for WhatsApp Business Calling when the server
# is behind NAT. STUN alone is not enough for symmetric NATs. Use a public
# TURN server (e.g. openrelay.metered.ca, Cloudflare TURN, or your own coturn).
TURN_URL: str = os.getenv("TURN_URL", "")
TURN_USERNAME: str = os.getenv("TURN_USERNAME", "")
TURN_CREDENTIAL: str = os.getenv("TURN_CREDENTIAL", "")

# Optional fixed UDP port range for aiortc media (helps with firewall rules).
# Format: "min-max" (e.g. "50000-50100"). Empty = let aiortc pick at random.
WEBRTC_UDP_PORT_RANGE: str = os.getenv("WEBRTC_UDP_PORT_RANGE", "")

WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"
