# Quickstart: Conversational Booking Assistant

Validates the chat assistant end-to-end: availability → booking → listing → cancellation,
with short-term context in Redis and durable bookings in MongoDB.

## Prerequisites

- Feature 001 skeleton set up (`uv sync` works).
- A running **Redis** (`redis://localhost:6379/0`) and **MongoDB** (`mongodb://localhost:27017`).
  Local option: `docker run -p 6379:6379 redis` and `docker run -p 27017:27017 mongo`.
- An OpenAI API key.

## Setup

```bash
uv sync                      # installs openai-agents, redis, beanie (+ motor)
cp .env.example .env         # then set OPENAI_API_KEY and, if not default, MONGODB_URI/REDIS_URL
```

Required env (fail-fast if missing): `OPENAI_API_KEY`, `MONGODB_URI`. Defaulted:
`MONGODB_DB`, `REDIS_URL`, `AGENT_MODEL=gpt-4.1`, `SESSION_TTL_SECONDS`.

## Seed a few rooms

Seed sample rooms into MongoDB (a seed script/task is created during implementation), e.g.
Room 12 (double), Room 5 (single). See [data-model.md](./data-model.md#room-mongodb-collection-rooms).

## Run

```bash
uv run uvicorn app.main:app --reload --app-dir src
```

Startup connects to MongoDB (Beanie init) and Redis; a missing required secret aborts startup
with a clear message.

## Validate the conversation (contract: [chat-api.md](./contracts/chat-api.md))

```bash
PHONE="+15551234567"

# 1) Availability (US1)
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d "{
  \"message\": \"What rooms are free from 2026-08-10 to 2026-08-12?\", \"phone_number\": \"$PHONE\" }"

# 2) Book (US2) — expect a booking reference in the reply
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d "{
  \"message\": \"Book Room 12 from 2026-08-10 to 2026-08-12\", \"phone_number\": \"$PHONE\" }"

# 3) List my bookings (US4)
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d "{
  \"message\": \"Show my bookings\", \"phone_number\": \"$PHONE\" }"

# 4) Cancel (US3) — use the reference from step 2
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d "{
  \"message\": \"Cancel booking <REFERENCE>\", \"phone_number\": \"$PHONE\" }"
```

## Success checks

- [ ] Availability reply lists only rooms free for the whole range (US1, SC-001).
- [ ] Booking returns a reference; the same room no longer shows as available for those
      nights (US2, SC-002).
- [ ] A second guest (different `phone_number`) cannot see or cancel this booking (SC-005).
- [ ] Booking an overlapping stay for the same room is refused (SC-003).
- [ ] Cancellation frees the room and the booking shows `cancelled` (US3, SC-004).
- [ ] Context threads within a conversation: a follow-up like "book the first one" resolves
      using prior turns (FR-002); after `SESSION_TTL_SECONDS` idle, context expires.

## Tests

```bash
uv run pytest tests/test_chat.py tests/test_tools.py tests/test_session.py
```

Tool logic runs against a test MongoDB (or fakes); the model call is stubbed where a
deterministic result is needed. See [agent-tools.md](./contracts/agent-tools.md).
