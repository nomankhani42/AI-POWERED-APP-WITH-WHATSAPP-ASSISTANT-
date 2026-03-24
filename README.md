# 🍽️ The Grand Dine — AI Restaurant Booking Agent

An AI-powered WhatsApp concierge for **The Grand Dine**, a premium restaurant in Islamabad. Customers can browse rooms, check availability, and make bookings entirely through WhatsApp — via text or voice messages.

Built with **FastAPI**, the **OpenAI Agents SDK**, and the **WhatsApp Cloud API**.

---

## Features

- **AI Booking Agent** — Multi-turn conversational agent (GPT-4o-mini) that guides customers through the full booking flow
- **WhatsApp Integration** — Receives and sends messages via the Meta WhatsApp Cloud API
- **Voice Message Support** — Transcribes WhatsApp voice notes (Groq Whisper STT) and responds with text
- **Short-Term Memory** — Conversation history stored in **Upstash Redis** with 1-hour TTL auto-expiry
- **Hotel & Room Management** — MongoDB-backed CRUD for hotels, rooms, customers, and bookings
- **Booking Management API** — REST endpoints for listing, filtering, and updating bookings
- **Template Messages** — Create, list, and delete WhatsApp message templates
- **Media Support** — Upload and send images, videos, and documents via WhatsApp
- **Dockerized** — Production-ready `Dockerfile` + `docker-compose.yml` with MongoDB

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **API Framework** | FastAPI + Uvicorn |
| **AI Agent** | OpenAI Agents SDK (GPT-4o-mini) |
| **Session Memory** | Upstash Redis (HTTP-based, serverless) |
| **Database** | MongoDB (via Motor async driver) |
| **Messaging** | WhatsApp Cloud API (Meta Graph API v21.0) |
| **Voice STT** | Groq Whisper |
| **Package Manager** | uv |
| **Language** | Python 3.12+ |

---

## Project Structure

```
src/
├── main.py                          # FastAPI app, lifespan, router registration
├── config.py                        # Environment variable loader
├── database.py                      # MongoDB connection management
├── models/                          # Pydantic v2 models
│   ├── booking.py                   # Booking, BookingStatus, BookingType
│   ├── common.py                    # Shared base model, PyObjectId
│   ├── conversation.py             # Conversation model
│   ├── customer.py                  # Customer model
│   ├── hotel.py                     # Hotel model
│   ├── room.py                      # Room model & RoomType enum
│   └── whatsapp.py                  # WhatsApp request/response models
├── crud/                            # Database CRUD operations
│   ├── booking.py                   # Booking CRUD (auto-generated BK-YYYY-XXXX IDs)
│   ├── conversation.py             # Conversation CRUD
│   ├── customer.py                  # Customer upsert & lookup
│   ├── hotel.py                     # Hotel CRUD
│   └── room.py                      # Room CRUD & availability search
├── endpoints/                       # API route modules
│   ├── bookings.py                  # Bookings REST API
│   ├── users.py                     # Users demo router
│   └── whatsapp.py                  # WhatsApp webhook & messaging routes
└── services/                        # Business logic & integrations
    ├── whatsapp.py                  # Meta WhatsApp Cloud API wrapper
    ├── webhook_handler.py           # Webhook parsing & message dispatch
    ├── voice_whatsapp.py            # Voice message pipeline
    └── ai_services/                 # AI agent components
        ├── agent.py                 # Grand Dine agent definition & runner
        ├── context.py               # Per-request UserContext dataclass
        ├── tools.py                 # Agent function tools (search, book, cancel…)
        ├── upstash_memory.py        # Upstash Redis session (SessionABC impl)
        ├── part1_groq_client.py     # Groq OpenAI-compatible client
        ├── part2_stt.py             # Speech-to-text model
        ├── part3_agent.py           # Voice agent (Groq LLaMA)
        ├── part4_tts.py             # Text-to-speech
        └── part5_pipeline.py        # Voice pipeline orchestrator
```

---

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- MongoDB (local or Atlas)
- ffmpeg (`sudo apt install ffmpeg`)
- Meta WhatsApp Business account
- Upstash Redis database ([free tier](https://console.upstash.com))

### 1. Clone & Install

```bash
git clone https://github.com/your-username/support-agent.git
cd support-agent
uv sync
```

### 2. Configure Environment

Create a `.env` file in the project root:

```env
# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=support_agent

# WhatsApp Cloud API
WHATSAPP_VERIFY_TOKEN=your-webhook-verify-secret
WHATSAPP_ACCESS_TOKEN=your-meta-access-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_BUSINESS_ACCOUNT_ID=your-business-account-id

# OpenAI
OPENAI_API_KEY=sk-...

# Groq (for voice STT)
GROQ_API_KEY=gsk_...

# Upstash Redis (session memory)
UPSTASH_REDIS_REST_URL=https://your-db.upstash.io
UPSTASH_REDIS_REST_TOKEN=AXxxxxxxxxxxxx
```

### 3. Seed Data (Optional)

```bash
python scripts/seed_hotels.py
python scripts/seed_restaurant.py
```

### 4. Run

```bash
cd src && uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

### 5. Expose Webhook (Development)

Use [ngrok](https://ngrok.com) to expose your local server for the Meta webhook:

```bash
ngrok http 8000
```

Then set the webhook URL in your [Meta Developer Portal](https://developers.facebook.com) to `https://your-ngrok-url/whatsapp/webhook`.

---

## Docker

```bash
docker compose up -d
```

This starts both **MongoDB** and the **app** container. Environment variables are loaded from `.env`.

---

## API Endpoints

### WhatsApp

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/whatsapp/webhook` | Meta webhook verification |
| `POST` | `/whatsapp/webhook` | Receive incoming messages |
| `POST` | `/whatsapp/send` | Send text message |
| `POST` | `/whatsapp/send-template` | Send template message |
| `POST` | `/whatsapp/send-media` | Send media message |
| `POST` | `/whatsapp/upload-media` | Upload media file to Meta |
| `GET` | `/whatsapp/templates` | List message templates |
| `POST` | `/whatsapp/templates` | Create a template |
| `DELETE` | `/whatsapp/templates/{name}` | Delete a template |
| `GET` | `/whatsapp/messages` | List stored messages (filterable) |
| `GET` | `/whatsapp/messages/{id}` | Get single message by ID |

### Bookings

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/bookings/` | List bookings (filterable by status) |
| `GET` | `/bookings/stats` | Booking counts by status |
| `GET` | `/bookings/{booking_id}` | Get booking details |
| `PATCH` | `/bookings/{booking_id}/status` | Update booking status |

### Users (Demo)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/users/` | List all users |
| `GET` | `/users/{id}` | Get user by ID |
| `POST` | `/users/` | Create a user |

---

## How It Works

### Booking Flow

The AI agent guides customers through a structured booking conversation:

1. **Date** → 2. **Time slot** → 3. **Booking type** → 4. **Guest count** → 5. **Room preference** (optional) → **Summary** → **Confirm**

### Agent Tools

The agent has access to these function tools:

| Tool | Purpose |
|------|---------|
| `get_current_datetime` | Get current PKT date/time |
| `search_hotels` | Search restaurants by location |
| `get_hotel_details` | Get full restaurant details |
| `search_available_rooms` | Find available rooms for a date |
| `get_room_types_info` | Room types, capacities & prices |
| `book_room` | Create a booking |
| `check_booking_status` | Look up booking by ID |
| `cancel_my_booking` | Cancel an existing booking |
| `get_my_bookings` | List customer's bookings |

### Session Memory

Conversation history is stored in **Upstash Redis** using a custom `UpstashSession` class that implements the OpenAI Agents SDK `SessionABC` interface:

- Each WhatsApp number gets a Redis key (`session:{phone_number}`)
- History is automatically prepended before each agent run
- New messages are persisted after each run
- Sessions auto-expire after **1 hour** of inactivity (configurable TTL)
- No persistent TCP connections — Upstash uses HTTP REST API

---

## Room Types & Pricing

| Room | Capacity | Use Case |
|------|----------|----------|
| **Single** | 2–4 guests | Intimate dining |
| **Double** | 6–8 guests | Family meals |
| **Suite** | 8–12 guests | Private events |
| **Deluxe** | 15–20 guests | Banquets & celebrations |

### Booking Durations

| Type | Duration |
|------|----------|
| Session | ~3 hours |
| Half-Day | ~5–6 hours |
| Full-Day | ~12 hours |
| Multi-Day | Full-day rate × number of days |

---

## License

MIT
