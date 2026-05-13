# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**The Grand Dine** — an AI-powered restaurant/event-venue booking assistant served over WhatsApp, a mobile app, and a web chat interface. The backend is a FastAPI application with a single AI agent that handles reservations, cancellations, and customer management. External integrations: Meta WhatsApp Cloud API, OpenAI (GPT-4o-mini + TTS), Groq Whisper (STT), Upstash Redis (session memory), MongoDB (persistent data), Expo Push Notifications (admin alerts).

## Tech Stack

- Python 3.11+ / FastAPI + Uvicorn
- Pydantic v2, Motor (async MongoDB), httpx
- OpenAI Agents SDK (`openai-agents[voice,litellm]`)
- Package manager: `uv`

## Commands

```bash
# Install dependencies
uv sync

# Run dev server (must run from src/ so relative imports resolve)
cd src && uvicorn main:app --reload

# Seed the initial admin user (MongoDB must be running first)
cd src && python ../scripts/seed_admin.py
```

There are no automated tests in this repo.

## Environment Variables

`.env` lives in the project root (one level above `src/`). `config.py` loads it at import time and exposes everything as module-level constants — **never call `os.getenv()` elsewhere**.

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=support_agent

WHATSAPP_VERIFY_TOKEN=<user-chosen-secret>
WHATSAPP_ACCESS_TOKEN=<from-meta-developer-portal>
WHATSAPP_PHONE_NUMBER_ID=<from-meta-business-settings>
WHATSAPP_BUSINESS_ACCOUNT_ID=<from-meta-business-settings>

OPENAI_API_KEY=<for GPT-4o-mini agent + TTS>
GROQ_API_KEY=<for Whisper STT>
UPSTASH_REDIS_REST_URL=<upstash redis endpoint>
UPSTASH_REDIS_REST_TOKEN=<upstash redis token>

# Optional
OPENROUTER_API_KEY=
GEMINI_API_KEY=
SQLITE_SESSION_DB=conversations.db
```

## Architecture

### Request flow (WhatsApp)

```
Meta webhook POST /  or  POST /whatsapp/webhook
  → services/webhook_handler.py :: handle_webhook()
      ├─ deduplication (in-memory OrderedDict, 5-min TTL)
      ├─ voice message → services/voice_whatsapp.py :: transcribe_voice_message()
      │     download WhatsApp media → pydub OGG→numpy → Groq Whisper STT
      └─ asyncio.create_task(_process_message())
            → services/ai_services/agent.py :: get_agent_response()
                  ├─ upsert customer in MongoDB
                  ├─ load UpstashSession (Redis, keyed by phone number, 1-hr TTL)
                  ├─ topic guardrail check (gpt-4o-mini, structured output)
                  └─ Runner.run(grand_dine_agent, session=..., context=UserContext)
                        → tools in services/ai_services/tools.py
            → if reply starts with [SEND_VOICE]:
                  services/whatsapp.py :: send_voice_note()  (OpenAI TTS → OGG → Meta)
              else:
                  services/whatsapp.py :: send_text_message()
```

### Request flow (mobile app / web)

```
POST /chat          → get_agent_response()          → {"reply": "..."}
WS   /chat/ws       → get_agent_response_stream()   → token-by-token JSON frames
POST /chat/voice    → Groq STT → agent → OpenAI TTS → {"transcript", "reply", "audio": base64 WAV}
POST /calling/turn  → same as /chat/voice (single HTTP turn)
WS   /calling/ws    → WebRTC-style audio chunk protocol → same STT→agent→TTS pipeline
```

### Agent design

The single `grand_dine_agent` (GPT-4o-mini, `services/ai_services/agent.py`) receives every message enriched with a `[Context: channel=X, Customer Name: ..., ...]` prefix injected by `get_agent_response()`. It decides whether to prefix its reply with `[SEND_VOICE]` based on the channel.

All agent tools (`services/ai_services/tools.py`) are `@function_tool(strict_mode=False)` async functions that receive a `RunContextWrapper[UserContext]`. `UserContext` carries `whatsapp_number`, `customer_id`, `channel`, `full_name`, `email`.

Session memory uses `UpstashSession` (`services/ai_services/upstash_memory.py`), a custom `SessionABC` implementation backed by Upstash Redis. History is capped at 20 items per turn and orphaned `function_call_output` items are pruned to avoid OpenAI 400 errors.

### Data layer

`database.py` holds a single Motor client in module globals; `get_db()` returns it. MongoDB collections and their primary keys:

| Collection | Key field | Notes |
|---|---|---|
| `hotels` | `hotel_id` | Restaurant/venue records |
| `rooms` | `room_id` | Linked to hotel via `hotel_id` |
| `customers` | `customer_id`, `whatsapp_number` | Upserted on every message |
| `bookings` | `booking_id` (`BK-YYYY-XXXX`) | Auto-incremented via `counters` collection |
| `conversations` | `whatsapp_number`, `conversation_id` | |
| `admins` | `admin_id`, `email` | bcrypt-hashed passwords |
| `push_tokens` | `token` | Expo push tokens for admin devices |

`crud/` has one module per collection (`hotel`, `room`, `customer`, `booking`, `conversation`, `admin`). All CRUD functions take `db: AsyncIOMotorDatabase` as their first argument.

`models/` has one Pydantic v2 file per resource. `MongoBaseModel` in `models/common.py` is the base class for all DB documents.

### Conventions

- New route modules: add `APIRouter` in `src/endpoints/`, register in `main.py`.
- New integrations: add a service file in `src/services/`.
- New agent tools: add `@function_tool(strict_mode=False)` functions in `services/ai_services/tools.py` and register them in the `tools=[...]` list inside `agent.py`.
- `_save_message()` in `services/whatsapp.py` currently writes to an **in-memory list** (`messages_store`), not MongoDB.
- Times are always stored/returned in PKT (UTC+5). Use `_now_pkt()` from `tools.py` for the current time.
- `booking_id` auto-increment uses the `counters` MongoDB collection (atomic `$inc` in `crud/booking.py`).

### Voice pipeline

Audio format throughout: 24 kHz, 16-bit, mono (int16 numpy). `pydub` + `ffmpeg` handles decoding any format the client sends. STT is Groq Whisper via the OpenAI Agents SDK voice layer (`services/ai_services/part2_stt.py`). TTS is `gpt-4o-mini-tts` outputting raw PCM, wrapped in a WAV header by `services/ai_services/tts_openai.py`. For WhatsApp voice notes the PCM is re-encoded to OGG/Opus by `services/whatsapp.py :: send_voice_note()`.

`services/wa_calling.py` implements live WebRTC voice calls via `aiortc` (Meta WhatsApp Business Calling API). This is separate from the chat voice endpoints.

### Push notifications

`services/push_notifications.py` sends Expo push notifications to all tokens stored in the `push_tokens` collection. Triggered on new booking and cancellation events inside `tools.py`. Admin devices register tokens via `POST /notifications/register`.
