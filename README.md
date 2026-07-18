# Voice Calling Agent

A voice calling agent built on the Meta API (call transport), Deepgram (STT + Aura TTS),
and the OpenAI Agents SDK with GPT-4.1 (reasoning), served by FastAPI +
Uvicorn, with Redis (short-term cache) and MongoDB (long-term memory).

Implemented so far:

- **`001-init-hello-world`** — runnable FastAPI skeleton (greeting + health).
- **`002-chatbot-booking`** — a conversational booking assistant at `POST /chat`, powered by
  an OpenAI Agents SDK agent (GPT-4.1) with function tools to check availability, book,
  cancel, and list hotel-room bookings. Short-term conversation context lives in Redis;
  rooms and bookings are stored durably in MongoDB.

The voice pipeline uses Meta calling and Deepgram speech services; the previous Cartesia
TTS path remains available for rollback.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (dependency & environment manager)
- Python 3.12 (uv can provision it: `uv python install 3.12`)
- For the chat feature: a running **Redis** and **MongoDB**, and an `OPENAI_API_KEY`.

## Setup

```bash
uv sync                 # install locked dependencies reproducibly
cp .env.example .env    # then set OPENAI_API_KEY (MONGODB_URI/REDIS_URL default to localhost)

# Seed sample rooms (requires MongoDB running):
PYTHONPATH=src uv run python scripts/seed_rooms.py
```

## Run

```bash
uv run uvicorn app.main:app --reload --app-dir src
```

The service listens on `http://localhost:8000` by default.

## Endpoints

| Method | Path                         | Response                                                     |
|--------|------------------------------|--------------------------------------------------------------|
| GET    | `/`                          | `{"message": "hello world", "success": true}`                |
| GET    | `/health`                    | `{"status": "ok", "service": "voice-agent"}`                 |
| POST   | `/chat`                      | `{"reply": "...", "conversation_id": "..."}`                 |
| GET    | `/whatsapp/webhook`   | Meta verification handshake — echoes `hub.challenge`         |
| POST   | `/whatsapp/webhook`   | `{"status": "received"}` (always 200; call events **and** chat messages) |

Unknown paths return `404` with `{"detail": "Not Found"}`.

`POST /chat` accepts `{"message", "phone_number", "conversation_id?"}`. The `phone_number`
identifies the guest and scopes all booking actions to them. Example:

```bash
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "What rooms are free from 2026-08-10 to 2026-08-12?", "phone_number": "+15551234567"}'
```

### WhatsApp chat channel (feature 007)

The same webhook URL that receives call events also receives the `messages` field, so no
extra Meta configuration is needed beyond subscribing the app to **messages**. Inbound text
(and interactive list replies) are deduped by `wamid`, run through the booking agent as the
`whatsapp` channel, and answered with freeform text — always inside the 24-hour
customer-service window because replies follow an inbound message. **No new environment
variables were added for this feature.**

- **Room-type selection**: when the agent needs a room type, guests on WhatsApp get a
  tappable interactive list (rows carry `room_type:<type>` ids); a tap continues the flow
  as if the guest had typed the type. Voice callers hear the types spoken; the REST chat
  API gets them as text. Room types are a fixed canonical set (`single, twin, double,
  deluxe, accessible, family, executive, suite`) enforced at write time.
- **Room photo carousel**: when a guest on WhatsApp asks what's available and at least
  two matching rooms have photos, the reply is a swipeable card carousel (photo + room
  details + a **Book** button that deep-links back into the chat with "Book Room 204"
  pre-typed via `wa.me`). Room photos are verified public Unsplash URLs seeded by
  `scripts/seed_rooms.py` (`Room.image_url`). Carousel support is WABA-tier dependent —
  send one 2-card test to a real number before relying on it; on failure (e.g. error
  131051) the assistant automatically falls back to a plain text room list.
- **Cancellation notices**: every successful cancellation (chat, WhatsApp, or voice)
  automatically sends exactly one WhatsApp message with the reference, room, and dates.
  Delivery failures never block the cancellation — they are logged as
  `booking.cancellation_notice_failed` entries.
- **Known limitation**: a guest who cancels on a voice call but has never sent a WhatsApp
  text may be outside Meta's 24-hour window; the freeform notice is then rejected (error
  131047) and logged. The fix would be an approved template message (out of scope for 007).

## Test

```bash
uv run pytest
```

## Project layout

```text
src/app/
├── main.py            # FastAPI app factory + entrypoint (wires routers, Mongo lifespan)
├── core/config.py     # typed settings (pydantic-settings), fail-fast
├── api/routes/        # one router per concern
│   ├── greeting.py    # GET /
│   ├── health.py      # GET /health
│   └── chat.py        # POST /chat
├── agent/             # OpenAI Agents SDK layer
│   ├── assistant.py   # build_agent() (GPT-4.1 + tools)
│   ├── tools.py       # @function_tool: availability / book / cancel / list
│   ├── context.py     # RunContext (trusted phone number for tools)
│   ├── session.py     # RedisSession — short-term conversation memory
│   └── service.py     # run_turn() orchestration
└── db/                # durable persistence (MongoDB via Beanie)
    ├── documents.py   # Room, Booking
    ├── mongo.py       # init/close
    └── bookings.py    # availability / create / cancel / list
scripts/seed_rooms.py  # seed sample rooms
tests/                 # pytest + FastAPI TestClient
```

Concerns are separated into modules (constitution Principle I) so future features —
Meta calling, STT, agent, TTS, Redis/MongoDB memory — are added as sibling packages
under `src/app/` without reorganizing this skeleton.

## Configuration

Settings load from environment variables / `.env` (see `.env.example`). `.env` is
git-ignored and MUST NOT be committed. Required secrets fail fast at startup if missing.

The voice call webhook & speech services (feature 003) add these variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DEEPGRAM_API_KEY` | yes | Deepgram streaming speech-to-text and Aura text-to-speech |
| `CARTESIA_API_KEY` | yes | Cartesia Sonic rollback configuration |
| `WHATSAPP_TOKEN` | yes | Bearer token for Meta Graph call actions |
| `WHATSAPP_PHONE_ID` | yes | WhatsApp Business phone number id |
| `WHATSAPP_VERIFY_TOKEN` | yes | Webhook verification handshake token |
| `WHATSAPP_APP_SECRET` | yes | Verifies the `X-Hub-Signature-256` on inbound events |
| `DEEPGRAM_MODEL` | no (default `nova-3`) | STT model |
| `DEEPGRAM_TTS_MODEL` | no (default `aura-2-thalia-en`) | Active Deepgram Aura TTS voice/model |
| `DEEPGRAM_TTS_SPEED` | no (default `1.0`) | Aura-2 speaking rate from `0.7` to `1.5` |
| `CARTESIA_MODEL` | no (default `sonic-3.5`) | TTS model |
| `CARTESIA_MAX_BUFFER_DELAY_MS` | no (default `3000`) | Cartesia managed buffering: buffers streamed LLM tokens until there's enough context for natural, clear speech instead of synthesizing each fragment. `0` disables; lower if replies feel laggy |
| `CARTESIA_VOICE_ID` | no | Cartesia voice id for the spoken reply |
| `STT_SAMPLE_RATE` | no (default `16000`) | Inbound PCM sample rate |
| `GRAPH_API_VERSION` | no (default `v21.0`) | Meta Graph API version |

Clear playback & real-time streaming loop (feature 004) add these variables:

| Variable | Required | Purpose |
|----------|----------|---------|
| `TTS_OUTPUT_SAMPLE_RATE` | no (default `48000`) | TTS + outbound frame rate; matches the Opus wire rate so the media bridge can repacketize into fixed 20 ms frames — the fix for the garbled "old TV" playback |
| `TTS_FIRST_AUDIO_TIMEOUT_S` | no (default `10`) | Maximum wait for Deepgram's first audio chunk |
| `TTS_EVENT_TIMEOUT_S` | no (default `10`) | Maximum gap between Deepgram stream events |
| `STT_ENDPOINTING_MS` | no (default `800`) | Silence before a caller turn is finalized (~0.8 s), so a brief mid-sentence pause doesn't cut the caller off |
| `STT_UTTERANCE_END_MS` | no (default `1000`) | `UtteranceEnd` backstop for noisy lines |
| `PROVIDER_RETRY_ATTEMPTS` | no (default `1`) | Silent retries of an STT/TTS operation before a spoken apology, then continue |
| `BARGE_IN_ENABLED` | no (default `false`) | Optional barge-in: stop playback when the caller speaks over the assistant (replies only, never the welcome) |
| `CALLER_SILENCE_TIMEOUT_S` | no (default `10`) | No-input window: re-prompt once with "Are you still there?", then end the call gracefully |
| `FILLER_GENERIC` | no (default `Let me check that for you…`) | Fallback filler spoken when a tool without a specific phrase runs |
| `FILLER_CHECK_AVAILABILITY` / `FILLER_BOOK_ROOM` / `FILLER_CANCEL_BOOKING` / `FILLER_LIST_BOOKINGS` | no (defaults shown in `.env.example`) | Tool-tailored fillers spoken the instant that tool is called, so the caller isn't left in silence during a lookup |

The voice loop streams the caller's speech to Deepgram in chunks, finalizes a turn on a
~0.8 s pause, and reuses one Deepgram Aura Speak WebSocket for the full call. Agent token
deltas are buffered only to natural sentence/clause boundaries (about 50-100 characters), so
playback starts while the rest of the answer is still generating without producing choppy
word fragments. Outbound audio is repacketized into fixed 20 ms / 48 kHz frames, and caller
barge-in is supported. When a turn needs a lookup or booking, the session
detects the tool call in the agent stream and speaks a tool-tailored filler (e.g. "One
moment, I'm booking that…") before the answer. Every call-flow milestone — attended,
welcome, each transcript/reply, each tool call and outcome, fillers, playback, barge-in,
re-prompt, fallback, and call end — is logged at INFO on the `app.call` logger, correlated by
`call_id` so a whole call can be reconstructed from the backend logs.
