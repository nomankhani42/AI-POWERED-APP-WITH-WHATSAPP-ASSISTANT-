# Implementation Plan: Conversational Booking Assistant (Chat + Tools)

**Branch**: `002-chatbot-booking` | **Date**: 2026-07-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-chatbot-booking/spec.md`

## Summary

Add a `POST /chat` endpoint backed by an OpenAI Agents SDK agent (GPT-4.1) that answers
guest questions and performs booking actions through function tools: check room
availability for a stay, book a room (check-in → check-out), cancel a booking, and list a
guest's bookings. The guest is identified by the phone number carried with each request and
all booking actions are scoped to it. Short-term conversation context lives in Redis via a
custom Agents SDK `Session`; rooms and bookings are stored durably in MongoDB. New code is
added as sibling packages under `src/app/` without disturbing the existing skeleton.

## Technical Context

**Language/Version**: Python 3.12 (managed by `uv`)

**Primary Dependencies**: FastAPI, Uvicorn, pydantic-settings (existing); `openai-agents`
(Agents SDK, import `agents`), `redis` (asyncio client, short-term Session store), `beanie`
(async MongoDB ODM over Motor, durable rooms/bookings)

**Storage**: MongoDB — durable Rooms and Bookings (Principle III long-term). Redis —
short-term conversation context keyed by conversation/session id (Principle III short-term).

**Testing**: pytest + FastAPI `TestClient`; agent tool logic tested against a test MongoDB
(or fakes) with the LLM call stubbed where needed.

**Target Platform**: Linux server; local dev via Uvicorn + local Redis + local MongoDB.

**Project Type**: Web service (single project) — extends feature 001 skeleton.

**Performance Goals**: Conversational; a turn should feel responsive. Availability/booking
tool operations target well under 1s excluding model latency.

**Constraints**: Async-first end to end (async endpoint, `Runner.run`, `redis.asyncio`,
Motor). Required secrets (`OPENAI_API_KEY`, `MONGODB_URI`, `REDIS_URL`) fail fast at startup.
No double-booking on overlapping stays (enforced at the data layer).

**Scale/Scope**: One chat endpoint, one agent, four tools, two durable entities.

**Context7 grounding**: Agents SDK patterns (`@function_tool`, `Agent`, `Runner.run`,
`SessionABC` custom session) verified against `/openai/openai-agents-python` (Principle VI).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Modular Architecture | Agent, tools, session, persistence, and routing in separate modules | PASS — `agent/`, `db/`, `api/routes/chat.py` split below |
| II. Async-First FastAPI Service | Async endpoint + async agent run + async Redis/Mongo, `uv`-locked | PASS |
| III. Layered Memory | Redis short-term, MongoDB durable | PASS — Redis `Session` for context; Beanie/Mongo for rooms+bookings |
| IV. Voice Pipeline Integrity | STT→Agent→TTS contract | N/A — text chat feature; uses the same GPT-4.1 agent layer, no audio stages |
| V. Configuration & Secrets Discipline | Env typed settings, fail-fast, `.env` ignored | PASS — new required secrets added to `Settings` without defaults |
| VI. Documentation-Driven Development | Context7 before SDK integration; use skills | PASS — Context7 consulted for Agents SDK; `fastapi` skill patterns applied |

**Result**: PASS. Principle IV is not applicable to a text-chat feature and is intentionally
deferred; no Complexity Tracking entry required.

## Project Structure

### Documentation (this feature)

```text
specs/002-chatbot-booking/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── chat-api.md      # POST /chat request/response
│   └── agent-tools.md   # tool signatures the agent may call
└── tasks.md             # /speckit-tasks output (later)
```

### Source Code (repository root)

```text
src/app/
├── main.py              # extend: include chat router; startup/shutdown for Mongo + Redis
├── core/
│   └── config.py        # extend: OPENAI_API_KEY, AGENT_MODEL, MONGODB_URI, MONGODB_DB, REDIS_URL, SESSION_TTL_SECONDS
├── api/routes/
│   ├── greeting.py      # (existing)
│   ├── health.py        # (existing)
│   └── chat.py          # POST /chat — validates request, calls agent service, returns reply
├── agent/               # OpenAI Agents SDK layer
│   ├── __init__.py
│   ├── assistant.py     # build_agent() -> Agent(model=gpt-4.1, tools=[...], instructions=...)
│   ├── tools.py         # @function_tool: check_availability, book_room, cancel_booking, list_bookings
│   ├── context.py       # RunContext dataclass carrying the guest phone number for tool scoping
│   ├── session.py       # RedisSession(SessionABC): get/add/pop/clear items with TTL
│   └── service.py       # run_turn(message, phone, conversation_id) -> reply (wraps Runner.run)
└── db/                  # durable persistence
    ├── __init__.py
    ├── mongo.py         # Beanie/Motor init + lifecycle
    ├── documents.py     # Beanie Documents: Room, Booking
    └── bookings.py      # repository fns used by tools (availability, create, cancel, list)

tests/
├── test_chat.py         # endpoint contract + happy path (agent stubbed where needed)
├── test_tools.py        # availability/book/cancel/list logic against test DB
└── test_session.py      # RedisSession round-trip (fakeredis or test instance)
```

**Structure Decision**: Extends the feature-001 single-project skeleton. The Agents SDK
concerns live in `src/app/agent/` (agent, tools, session, orchestration) and durable data in
`src/app/db/`, keeping routing thin (`chat.py`). This isolates each integration (Principle I)
so the later voice pipeline reuses the same `agent/service.py` behind speech stages.

## Complexity Tracking

> No constitution violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _(none)_  | —          | —                                   |
