# Voice Calling Agent 🎙️

An AI hotel-booking support agent you can **call** or **message on WhatsApp**. It answers a
WhatsApp Business call, greets the caller, and holds a real-time spoken conversation —
listening, reasoning, and replying in a natural voice — to check room availability, make a
booking, review bookings, or cancel one. The same agent also answers WhatsApp text messages
and a REST chat endpoint.

Built with **FastAPI**, the **OpenAI Agents SDK** (GPT‑4.1) for reasoning, **Deepgram** for
streaming speech‑to‑text and Aura text‑to‑speech, **aiortc** for the WebRTC media bridge, and
the **Meta WhatsApp Cloud API** for call transport and chat. Short‑term conversation memory
lives in **Redis**; rooms and bookings are stored durably in **MongoDB**.

---

## ✨ Features

- **Real-time voice calls** — a full listen → transcribe → reason → speak loop over WebRTC.
  Audio streams both ways so the agent starts speaking before it has finished thinking.
- **Natural turn-taking** — Deepgram endpointing finalizes a turn on a ~0.8 s pause, with a
  forced-finalize backstop so DTX silence never leaves a turn hanging.
- **Spoken fillers** — the caller hears "One moment, I'm booking that…" the instant a tool
  runs, so there's no dead air during a lookup.
- **Barge-in** (optional) — the caller can talk over the assistant to interrupt it.
- **Smooth playback** — outbound audio is repacketized into fixed 20 ms / 48 kHz Opus frames
  with a jitter buffer that re-anchors on underrun, preventing garbled "sped-up" speech.
- **WhatsApp chat channel** — the same webhook answers inbound text with interactive room-type
  lists, a swipeable **room-photo carousel**, and automatic cancellation notices.
- **REST chat API** — `POST /chat` for testing the booking agent without any telephony.
- **Booking tools** — check availability, book, cancel, and list bookings, all scoped to the
  guest's phone number and persisted in MongoDB.
- **Provider-swappable** — Deepgram is the active TTS; a Cartesia path is retained for rollback.

## 🧠 How it works

```
   WhatsApp call ──► Meta Cloud API (SDP/webhook)
                          │
                          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  FastAPI webhook  →  WebRTC media bridge (aiortc)          │
   │                                                            │
   │   caller audio ─► Deepgram STT ─► OpenAI Agents (GPT-4.1)  │
   │                                     │   ▲                  │
   │                          booking tools │   │ Redis session │
   │                                     ▼   │                  │
   │   caller  ◄── Deepgram Aura TTS ◄── reply text (streamed)  │
   └──────────────────────────────────────────────────────────┘
                          │
                       MongoDB  (rooms + bookings, via Beanie)
```

Every call-flow milestone — attended, welcome, each transcript/reply, tool calls and
outcomes, fillers, playback, barge-in, re-prompt, fallback, and call end — is logged at INFO
on the `app.call` logger, correlated by `call_id`, so a whole call can be reconstructed from
the logs.

## 🛠️ Tech stack

| Layer | Technology |
|-------|-----------|
| API / server | FastAPI, Uvicorn |
| Reasoning | OpenAI Agents SDK (GPT‑4.1) |
| Speech-to-text | Deepgram streaming (`nova-3`) |
| Text-to-speech | Deepgram Aura (`aura-2-thalia-en`); Cartesia rollback |
| Real-time media | aiortc (WebRTC / Opus RTP) |
| Telephony & chat | Meta WhatsApp Cloud API |
| Short-term memory | Redis |
| Durable storage | MongoDB (Beanie ODM) |
| Tooling | `uv`, pytest |

## 📋 Prerequisites

- [`uv`](https://docs.astral.sh/uv/) for dependency & environment management
- Python 3.12 (`uv python install 3.12` if needed)
- A running **MongoDB** and **Redis** (local or hosted, e.g. MongoDB Atlas)
- API keys: **OpenAI**, **Deepgram**, and — for the WhatsApp voice/chat channels — a
  configured **Meta WhatsApp Business** app

## 🚀 Quick start

```bash
# 1. Install locked dependencies
uv sync

# 2. Configure environment
cp .env.example .env
#    then set OPENAI_API_KEY, DEEPGRAM_API_KEY, MONGODB_URI, and the WhatsApp secrets

# 3. Seed sample rooms (requires MongoDB reachable)
uv run python scripts/seed_rooms.py          # add --reset to wipe first

# 4. Run the service
uv run uvicorn app.main:app --reload --app-dir src
```

The service listens on `http://localhost:8000` by default.

> **Docker:** a `Dockerfile` and `docker-compose.yml` are included if you prefer containers.

## 🔌 API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Liveness — `{"message": "hello world", "success": true}` |
| `GET`  | `/health` | Health — `{"status": "ok", "service": "voice-agent"}` |
| `POST` | `/chat` | Talk to the booking agent over REST |
| `GET`  | `/whatsapp/webhook` | Meta verification handshake (echoes `hub.challenge`) |
| `POST` | `/whatsapp/webhook` | Call lifecycle events **and** inbound chat messages (always `200`) |

`POST /chat` accepts `{"message", "phone_number", "conversation_id?"}`. The `phone_number`
identifies the guest and scopes all booking actions to them:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "What rooms are free from 2026-08-10 to 2026-08-12?", "phone_number": "+15551234567"}'
```

## 📱 WhatsApp setup

Point your Meta app's webhook at `https://<your-host>/whatsapp/webhook` and subscribe to the
**calls** and **messages** fields. The webhook always returns `200` so a single bad event never
triggers Meta's retry floods. One webhook URL serves both channels — no extra configuration is
needed for chat beyond subscribing to `messages`.

- **Room-type selection** — guests get a tappable interactive list; a tap continues the flow
  as if they had typed the room type.
- **Room-photo carousel** — when ≥2 matching rooms have photos, availability is answered with a
  swipeable card carousel (photo + details + a **Book** deep link). Carousel support is
  WABA-tier dependent; on failure it falls back to a plain text list automatically.
- **Cancellation notices** — every successful cancellation (voice, chat, or WhatsApp) sends
  exactly one WhatsApp confirmation with the reference, room, and dates.

> **Note:** freeform WhatsApp replies must fall inside Meta's 24-hour customer-service window.
> A guest who cancels on a voice call but has never texted may be outside it — the notice is
> then rejected and logged. A production fix would use an approved template message.

## ⚙️ Configuration

Settings load from environment variables / `.env` (see **`.env.example`** for the complete,
annotated list). `.env` is git-ignored and must never be committed. Required secrets fail fast
at startup if missing.

**Required**

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Reasoning (OpenAI Agents SDK) |
| `DEEPGRAM_API_KEY` | Streaming STT + Aura TTS |
| `MONGODB_URI` | Rooms & bookings store |
| `WHATSAPP_TOKEN` | Meta Graph call/chat actions |
| `WHATSAPP_PHONE_ID` | WhatsApp Business phone number id |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification handshake |
| `WHATSAPP_APP_SECRET` | Verifies `X-Hub-Signature-256` on inbound events |
| `CARTESIA_API_KEY` | Present for the TTS rollback path |

**Commonly tuned (defaults shown)**

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_MODEL` | `gpt-4.1` | Reasoning model |
| `REDIS_URL` | `redis://localhost:6379/0` | Short-term conversation memory |
| `MONGODB_DB` | `voice_agent` | Database name |
| `BUSINESS_TIMEZONE` | `Asia/Karachi` | Resolves "today/tomorrow" booking dates |
| `DEEPGRAM_TTS_MODEL` | `aura-2-thalia-en` | Active voice |
| `DEEPGRAM_TTS_SPEED` | `1.0` | Speaking rate (0.7–1.5) |
| `STT_MIN_CONFIDENCE` | `0.5` | Reject transcripts below this mean confidence |
| `WELCOME_MESSAGE` | _(greeting)_ | Spoken on connect; empty disables it |
| `BARGE_IN_ENABLED` | `false` | Let the caller interrupt the assistant |
| `CALLER_SILENCE_TIMEOUT_S` | `10` | Re-prompt once, then end the call gracefully |
| `TTS_OUTPUT_SAMPLE_RATE` | `48000` | Outbound audio rate (matches the Opus wire rate) |
| `WHATSAPP_SKIP_SIGNATURE` | `false` | **Local debug only** — bypass signature checks |

See `.env.example` for the full set (endpointing, fillers, retry, Graph API version, etc.).

## 🧪 Testing

```bash
uv run pytest
```

## 🗂️ Project structure

```text
src/app/
├── main.py             # FastAPI app factory (routers + Mongo lifespan)
├── core/config.py      # typed settings (pydantic-settings), fail-fast
├── api/routes/         # one router per concern
│   ├── greeting.py     # GET /
│   ├── health.py       # GET /health
│   ├── chat.py         # POST /chat
│   └── whatsapp_calling.py   # GET/POST /whatsapp/webhook (calls + messages)
├── agent/              # OpenAI Agents SDK layer
│   ├── assistant.py    # build_agent() (GPT-4.1 + tools)
│   ├── tools.py        # availability / book / cancel / list
│   ├── session.py      # Redis short-term memory
│   └── service.py      # run_turn orchestration
├── services/           # speech + media + WhatsApp
│   ├── stt.py          # Deepgram streaming STT
│   ├── tts.py          # Deepgram Aura TTS (Cartesia rollback)
│   ├── meta_calling.py # Meta Graph call actions
│   ├── whatsapp_chat.py / whatsapp_messages.py
│   └── media/          # WebRTC bridge, per-call session loop, fillers, logging
└── db/                 # MongoDB via Beanie (documents, bookings, calls)
scripts/seed_rooms.py   # seed sample rooms with photos
tests/                  # pytest + FastAPI TestClient
```

## 📄 License

No license is currently declared. Add a `LICENSE` file before distributing publicly.
