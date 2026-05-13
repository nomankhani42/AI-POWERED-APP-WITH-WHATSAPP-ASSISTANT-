# The Grand Dine — Backend

**FastAPI backend** for The Grand Dine (F-7 Markaz, Islamabad) — an AI-powered restaurant & event-venue booking assistant. This repository contains **server-side code only**; mobile and web clients live in separate repositories.

A single GPT-4o-mini agent handles reservations, cancellations, room/venue questions, and customer management across four channels served by this backend:

- **WhatsApp** text and voice messages (Meta Cloud API webhook)
- **Live WhatsApp voice calls** (Meta WhatsApp Business Calling API + WebRTC)
- **REST + WebSocket chat** consumed by an external mobile app
- **REST + WebSocket voice calling** consumed by an external mobile / web client

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + Uvicorn |
| AI agent | OpenAI Agents SDK (GPT-4o-mini) |
| Session memory | Upstash Redis (HTTP REST, 1 h TTL) |
| Persistent data | MongoDB (Motor async driver) |
| WhatsApp messaging | WhatsApp Cloud API (Graph v21.0) |
| WhatsApp live calls | WhatsApp Business Calling API (Graph v23.0) + `aiortc` WebRTC |
| Speech-to-text | Groq Whisper |
| Text-to-speech | OpenAI `gpt-4o-mini-tts` (PCM → WAV / OGG-Opus) |
| Admin auth | bcrypt password hashing |
| Admin alerts | Expo Push Notifications |
| Package manager | uv |
| Language | Python 3.11+ |

---

## Architecture

### WhatsApp text / voice-note flow

```
Meta webhook  POST /  or  POST /whatsapp/webhook
    → services/webhook_handler.py
        ├─ dedup (5-min OrderedDict)
        ├─ voice note → services/voice_whatsapp.py (Groq Whisper STT)
        └─ asyncio.create_task → agent
            → services/ai_services/agent.py :: get_agent_response()
                ├─ upsert customer in Mongo
                ├─ load Upstash session (per phone number, 1 h TTL)
                ├─ topic guardrail (gpt-4o-mini structured output)
                └─ Runner.run(grand_dine_agent)
        → reply.startswith("[SEND_VOICE]") ?
              services/whatsapp.py :: send_voice_note()  (OpenAI TTS → OGG/Opus)
            : services/whatsapp.py :: send_text_message()
```

### Live WhatsApp voice call flow (`services/wa_calling.py`)

```
Meta webhook  field=calls  event=connect  →  SDP offer
    → aiortc createAnswer + setLocalDescription
    → POST /{phone_number_id}/calls  action=pre_accept
    → wait for ICE connected
    → POST /{phone_number_id}/calls  action=accept
    → audio flows over the negotiated WebRTC path
        ├─ inbound: 48 kHz Opus → resample 24 kHz → VAD → Groq Whisper STT
        ├─ agent stream → per-sentence TTS → ordered feeder → outbound Opus
        └─ tool-call hooks play cached "one moment…" filler clips
```

### Client-facing chat & calling endpoints

These endpoints are consumed by external mobile / web clients — this repo does not contain the client code.

```
POST /chat              → get_agent_response()              → {"reply": "..."}
WS   /chat/ws           → get_agent_response_stream()       → token-by-token JSON frames
POST /chat/voice        → STT → agent → TTS                 → {"transcript", "reply", "audio": base64 WAV}
POST /calling/turn      → single HTTP voice turn (same pipeline as /chat/voice)
WS   /calling/ws        → audio_chunk protocol (STT → agent → TTS), sentence-streamed WAVs
```

---

## Project Layout

```
src/
├── main.py                              FastAPI app, lifespan, router registration
├── config.py                            Single source of truth for env vars
├── database.py                          Single Motor client / get_db()
├── models/                              Pydantic v2 models (one file per resource)
│   ├── admin.py  booking.py  common.py  conversation.py
│   ├── customer.py  hotel.py  room.py   whatsapp.py
├── crud/                                One module per Mongo collection
│   ├── admin.py  booking.py  conversation.py
│   ├── customer.py  hotel.py  room.py
├── endpoints/                           API routers (registered in main.py)
│   ├── admin.py             /admin       — bcrypt login, registration, listing
│   ├── bookings.py          /bookings    — list / filter / status update
│   ├── calling.py           /calling     — voice call HTTP + WebSocket
│   ├── chat.py              /chat        — chat HTTP + WebSocket + /chat/voice + /chat/tts
│   ├── customers.py         /customers   — list / lookup
│   ├── hotels.py            /hotels      — list / detail / rooms
│   ├── notifications.py     /notifications — Expo push token register/unregister
│   ├── users.py             /users       — demo endpoints
│   └── whatsapp.py          /whatsapp    — webhook, send, templates, media, messages
└── services/
    ├── whatsapp.py                       Meta Cloud API wrapper (send text/voice/media)
    ├── webhook_handler.py                Inbound webhook dispatch + dedup
    ├── voice_whatsapp.py                 Voice-note download + Groq Whisper STT
    ├── wa_calling.py                     Live WebRTC voice call session manager
    ├── push_notifications.py             Expo push send helper
    └── ai_services/
        ├── agent.py                      Grand Dine agent definition + runner
        ├── context.py                    Per-request UserContext dataclass
        ├── tools.py                      @function_tool implementations
        ├── upstash_memory.py             SessionABC backed by Upstash Redis
        ├── tts_openai.py                 OpenAI gpt-4o-mini-tts wrapper
        ├── part2_stt.py                  Groq Whisper STT model factory
        └── (legacy part1/3/4/5 voice-pipeline parts)
scripts/
├── seed_admin.py                         Seed the initial bcrypt admin user
├── seed_hotels.py                        Seed sample hotel data
└── seed_restaurant.py                    Seed The Grand Dine restaurant + rooms
```

---

## MongoDB Collections

| Collection      | Primary key(s)                       | Notes |
|-----------------|--------------------------------------|-------|
| `hotels`        | `hotel_id`                           | Restaurant / venue records |
| `rooms`         | `room_id` (`hotel_id` foreign key)   | Section / room records |
| `customers`     | `customer_id`, `whatsapp_number`     | Upserted on every inbound message |
| `bookings`      | `booking_id` (`BK-YYYY-XXXX`)        | Auto-incremented via `counters` collection |
| `conversations` | `whatsapp_number`, `conversation_id` | Persistent message log |
| `admins`        | `admin_id`, `email`                  | bcrypt-hashed passwords |
| `push_tokens`   | `token`                              | Expo push tokens for admin devices |
| `counters`      | `_id`                                | Atomic `$inc` for `booking_id` sequence |

---

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- MongoDB (local or Atlas)
- `ffmpeg` (`sudo apt install ffmpeg`)
- Meta WhatsApp Business account (Cloud API access)
- Upstash Redis database ([free tier](https://console.upstash.com))
- (Optional) TURN server — required for WhatsApp Business Calling behind NAT

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

Create a `.env` file in the project root (one level above `src/`):

```env
# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=support_agent

# WhatsApp Cloud API
WHATSAPP_VERIFY_TOKEN=<your-webhook-verify-secret>
WHATSAPP_ACCESS_TOKEN=<from Meta Developer Portal>
WHATSAPP_PHONE_NUMBER_ID=<from Meta Business Settings>
WHATSAPP_BUSINESS_ACCOUNT_ID=<from Meta Business Settings>

# AI providers
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...

# Upstash Redis (session memory)
UPSTASH_REDIS_REST_URL=https://your-db.upstash.io
UPSTASH_REDIS_REST_TOKEN=AXxxxxxxxxxxxx

# Optional — WhatsApp Business Calling (WebRTC) behind NAT
TURN_URL=turn:turn.example.com:3478
TURN_USERNAME=
TURN_CREDENTIAL=
WEBRTC_UDP_PORT_RANGE=50000-50100

# Optional
OPENROUTER_API_KEY=
GEMINI_API_KEY=
SQLITE_SESSION_DB=conversations.db
```

`config.py` loads `.env` at import time and exposes every value as a module-level constant. **Never call `os.getenv()` elsewhere in the codebase.**

### 3. Seed data

```bash
cd src && python ../scripts/seed_restaurant.py   # The Grand Dine + rooms
cd src && python ../scripts/seed_admin.py        # initial admin user
```

### 4. Run the dev server

```bash
cd src && uvicorn main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `/docs`.

### 5. Expose webhook (dev)

```bash
ngrok http 8000
```

Set the resulting public URL in the [Meta Developer Portal](https://developers.facebook.com) as your webhook target (`/whatsapp/webhook` or `/`). Use `WHATSAPP_VERIFY_TOKEN` as the verify token.

---

## Docker

```bash
docker compose up -d
```

Brings up MongoDB and the app container. Environment variables are read from `.env`.

---

## API Endpoints

### WhatsApp (`/whatsapp`)

| Method | Path                              | Purpose |
|--------|-----------------------------------|---------|
| `GET`  | `/whatsapp/webhook`               | Meta webhook verification |
| `POST` | `/whatsapp/webhook`               | Receive incoming messages / call events |
| `POST` | `/whatsapp/send`                  | Send text message |
| `POST` | `/whatsapp/send-template`         | Send template message |
| `POST` | `/whatsapp/send-media`            | Send media message |
| `POST` | `/whatsapp/upload-media`          | Upload media to Meta |
| `GET`  | `/whatsapp/templates`             | List message templates |
| `POST` | `/whatsapp/templates`             | Create a template |
| `DELETE` | `/whatsapp/templates/{name}`    | Delete a template |
| `GET`  | `/whatsapp/messages`              | List stored messages |
| `GET`  | `/whatsapp/messages/{id}`         | Get one message |

### Chat (`/chat`)

| Method | Path             | Purpose |
|--------|------------------|---------|
| `POST` | `/chat`          | Single-turn chat (HTTP) |
| `WS`   | `/chat/ws`       | Streaming chat (token deltas) |
| `POST` | `/chat/voice`    | Audio in → STT → agent → TTS → audio out |
| `POST` | `/chat/tts`      | Text → TTS WAV |

### Voice calling (`/calling`)

| Method | Path             | Purpose |
|--------|------------------|---------|
| `POST` | `/calling/turn`  | Single voice turn (multipart audio) |
| `WS`   | `/calling/ws`    | Persistent voice-call WebSocket |

### Bookings (`/bookings`)

| Method | Path                              | Purpose |
|--------|-----------------------------------|---------|
| `GET`   | `/bookings/`                     | List / filter by status |
| `GET`   | `/bookings/stats`                | Counts by status |
| `GET`   | `/bookings/{booking_id}`         | Get one booking |
| `PATCH` | `/bookings/{booking_id}/status`  | Update booking status |

### Customers / Hotels (read-only)

| Method | Path                              | Purpose |
|--------|-----------------------------------|---------|
| `GET`  | `/customers/`                     | List customers |
| `GET`  | `/customers/stats`                | Customer counts |
| `GET`  | `/customers/{customer_id}`        | Get customer |
| `GET`  | `/hotels/`                        | List hotels |
| `GET`  | `/hotels/stats`                   | Hotel counts |
| `GET`  | `/hotels/{hotel_id}`              | Hotel detail |
| `GET`  | `/hotels/{hotel_id}/rooms`        | Rooms for a hotel |

### Admin auth (`/admin`)

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| `POST` | `/admin/register`             | Create an admin (bcrypt-hashed password) |
| `POST` | `/admin/login`                | Verify credentials, return profile |
| `GET`  | `/admin/profile/{admin_id}`   | Profile by id |
| `GET`  | `/admin/list`                 | List admins |

### Notifications (`/notifications`)

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| `POST` | `/notifications/register`     | Register an Expo push token |
| `POST` | `/notifications/unregister`   | Remove an Expo push token |

---

## Agent Design

The single `grand_dine_agent` (`src/services/ai_services/agent.py`) gets every user message prefixed with `[Context: channel=..., Customer Name: ..., ...]` so it can adapt its formatting per channel (voice vs WhatsApp text vs external app/web client). It prefixes voice-eligible WhatsApp replies with `[SEND_VOICE]`; the dispatcher strips the tag and routes to TTS.

### Agent tools (`src/services/ai_services/tools.py`)

| Tool                       | Purpose |
|----------------------------|---------|
| `get_current_datetime`     | Current PKT date/time (call FIRST for any relative date) |
| `search_hotels`            | Search restaurants by location |
| `get_hotel_details`        | Full restaurant details |
| `search_available_rooms`   | Available rooms for a date |
| `get_room_types_info`      | Room types, capacities, prices |
| `book_room`                | Create a booking (fires Expo push to admins) |
| `check_booking_status`     | Look up booking by id |
| `cancel_my_booking`        | Cancel a booking (fires Expo push to admins) |
| `get_my_bookings`          | List a customer's bookings |
| `update_customer_info`     | Save name / email to the customer record |

All tools are `@function_tool(strict_mode=False)` async functions that receive a `RunContextWrapper[UserContext]` carrying `whatsapp_number`, `customer_id`, `channel`, `full_name`, `email`.

### Session memory

`UpstashSession` (`src/services/ai_services/upstash_memory.py`) is a custom `SessionABC` implementation backed by Upstash Redis over HTTP REST — no persistent TCP connection. History is capped at 20 items per turn and orphaned `function_call_output` items are pruned to avoid OpenAI 400 errors. WhatsApp keys are `session:{phone_number}` (1 h TTL); voice-call keys are `session:call:{call_id}` (cleared on hangup).

---

## Voice Pipeline

All in-process audio is **24 kHz, 16-bit, mono int16 numpy**. `pydub` + `ffmpeg` decode whatever format the client sends.

- **STT** — Groq Whisper via the OpenAI Agents SDK voice layer (`services/ai_services/part2_stt.py`). Preprocessing: downsample 24 → 16 kHz, normalise, high-pass 80 Hz, strip ≥300 ms edge silence.
- **TTS** — `gpt-4o-mini-tts` outputs raw PCM, repackaged to WAV (HTTP) or OGG/Opus (WhatsApp voice notes).
- **Live WhatsApp calls** — `services/wa_calling.py` runs WebRTC via `aiortc`. 48 kHz inbound Opus is resampled to 24 kHz for VAD + STT; outbound PCM is upsampled to 48 kHz for Opus. Tool-call hooks play cached "one moment…" filler clips so the caller never hears dead air during Mongo/Meta/OpenAI round-trips. End-of-utterance is detected by RMS-based VAD (default thresholds tuned for phone audio).

### Tunable constants in `wa_calling.py`

| Constant                     | Default  | Purpose |
|------------------------------|----------|---------|
| `_SILENCE_RMS`               | 400      | RMS below this = silence |
| `_SILENCE_FRAMES`            | 25       | ~500 ms silence → end-of-utterance |
| `_MIN_SPEECH_FRAMES`         | 10       | Min speech to bother sending to STT |
| `_MAX_BUFFER_SECS`           | 10       | Force-flush regardless |
| `_TTS_TIMEOUT_SECS`          | 15       | Per-sentence TTS hard timeout |
| `_AGENT_STREAM_TIMEOUT_SECS` | 60       | End-to-end agent stream ceiling |
| `_MAX_PROCESSING_SECS`       | 45       | Force-clear a stuck `_processing` flag |

---

## Booking Flow

The agent asks for one thing at a time, in this exact order:

1. Name (skip if already on file)
2. Email (skip if known — clearly optional)
3. Date (calls `get_current_datetime` first for relative dates)
4. Room type + guest count
5. Time slot (breakfast / lunch / dinner) if missing
6. Read-back summary + "Should I confirm this booking?"
7. Only after explicit yes → `book_room`

### Rooms

| Room    | Capacity     | Use case |
|---------|--------------|----------|
| Single  | 2–4 guests   | Intimate dining |
| Double  | 6–8 guests   | Family meals |
| Suite   | 8–12 guests  | Private events |
| Deluxe  | 15–20 guests | Banquets, celebrations |

### Durations

| Type      | Length |
|-----------|--------|
| Session   | ~3 h |
| Half-Day  | ~6 h |
| Full-Day  | ~12 h |
| Multi-Day | full-day × days |

### Slots

Breakfast 8 AM · Morning 10 AM · Lunch 12 PM · Afternoon 2 PM · Evening 6 PM · Dinner 7 PM

All times stored / returned in **PKT (UTC+5)**. Use `_now_pkt()` from `tools.py` for current time.

---

## Conventions

- **New route module** — add `APIRouter` in `src/endpoints/`, register in `main.py`.
- **New integration** — add a service file in `src/services/`.
- **New agent tool** — add a `@function_tool(strict_mode=False)` function in `services/ai_services/tools.py` and register it in the `tools=[...]` list inside `agent.py`.
- **`booking_id`** auto-increment uses the `counters` MongoDB collection (atomic `$inc` in `crud/booking.py`).
- All CRUD functions take `db: AsyncIOMotorDatabase` as their first argument.

---

## License

MIT
