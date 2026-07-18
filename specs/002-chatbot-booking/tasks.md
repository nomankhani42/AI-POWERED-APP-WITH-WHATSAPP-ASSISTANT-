---
description: "Task list for Conversational Booking Assistant (Chat + Tools)"
---

# Tasks: Conversational Booking Assistant (Chat + Tools)

**Input**: Design documents from `/specs/002-chatbot-booking/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/chat-api.md, contracts/agent-tools.md, quickstart.md

**Tests**: Included — plan.md and quickstart.md specify pytest coverage (tools, chat endpoint, Redis session).

**Organization**: Grouped by user story. Each of the four tools maps to one user story; the chat endpoint, agent, Redis session, and MongoDB layer are foundational (shared by all stories).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 for user-story tasks only
- Exact file paths included

## Path Conventions

- Single-project web service extending feature 001: source under `src/app/`, tests under `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Add runtime dependencies with `uv add openai-agents redis beanie` and regenerate the lockfile with `uv sync` (updates `pyproject.toml`, `uv.lock`)
- [X] T002 [P] Extend `.env.example` with `OPENAI_API_KEY=`, `AGENT_MODEL=gpt-4.1`, `MONGODB_URI=mongodb://localhost:27017`, `MONGODB_DB=voice_agent`, `REDIS_URL=redis://localhost:6379/0`, `SESSION_TTL_SECONDS=3600`
- [X] T003 [P] Create package skeletons with `__init__.py`: `src/app/agent/__init__.py`, `src/app/db/__init__.py`

**Checkpoint**: Dependencies installed; new packages importable.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: Every user story depends on this phase. The chat loop must work end to end before any tool is added.

- [X] T004 Extend `src/app/core/config.py` `Settings` with `openai_api_key: str` and `mongodb_uri: str` (required, no default → fail-fast), and `agent_model` (default `gpt-4.1`), `mongodb_db` (default `voice_agent`), `redis_url` (default `redis://localhost:6379/0`), `session_ttl_seconds: int` (default 3600)
- [X] T005 [P] Define Beanie documents in `src/app/db/documents.py`: `Room` and `Booking` per data-model.md, with indexes on `Booking.reference` (unique), `Booking.phone_number`, `Booking.room_id`
- [X] T006 Implement MongoDB lifecycle in `src/app/db/mongo.py`: async `init_db()` (Motor client + `beanie.init_beanie` with Room/Booking) and `close_db()` (depends on T005)
- [X] T007 [P] Add shared helpers in `src/app/db/bookings.py`: date-range overlap predicate (`a.check_in < b.check_out and b.check_in < a.check_out`) and unique `reference` generator (no story-specific queries yet)
- [X] T008 [P] Define `RunContext` dataclass carrying `phone_number: str` in `src/app/agent/context.py`
- [X] T009 [P] Implement `RedisSession(SessionABC)` in `src/app/agent/session.py` using `redis.asyncio`: `get_items`, `add_items`, `pop_item`, `clear_session`, storing the item list under a conversation-id key with `SESSION_TTL_SECONDS` TTL
- [X] T010 Create `src/app/agent/tools.py` with imports and an empty `TOOLS: list = []` registry (tools appended per story)
- [X] T011 Implement `build_agent()` in `src/app/agent/assistant.py`: construct `Agent(name, instructions, model=settings.agent_model, tools=TOOLS)` with booking-assistant instructions (scope, ask for missing details, confirm actions) importing `TOOLS` from tools.py
- [X] T012 Implement `run_turn(message, phone_number, conversation_id)` in `src/app/agent/service.py`: build `RedisSession`, call `await Runner.run(build_agent(), message, session=session, context=RunContext(phone_number=...))`, return `result.final_output` (default `conversation_id` to `phone_number`)
- [X] T013 Implement `POST /chat` in `src/app/api/routes/chat.py`: `ChatRequest{message, phone_number, conversation_id?}` / `ChatResponse{reply, conversation_id}` per contracts/chat-api.md, delegating to `run_turn`
- [X] T014 Wire `src/app/main.py`: include the chat router and add async startup/shutdown lifespan calling `init_db()` / `close_db()` and initializing the Redis client
- [X] T015 [P] Add a room seed script `scripts/seed_rooms.py` inserting a few sample `Room`s (used by quickstart)

**Checkpoint**: `POST /chat` runs a full agent turn (no tools yet) with Redis-backed context; app starts with Mongo + Redis connected.

---

## Phase 3: User Story 1 - Ask About Room Availability (Priority: P1) 🎯 MVP

**Goal**: The assistant answers availability questions for a stay using live data.

**Independent Test**: Ask availability for a date range; reply lists only rooms free for every night.

### Tests for User Story 1

- [X] T016 [P] [US1] Test `check_availability` repository logic in `tests/test_tools.py` (free vs overlapping rooms, invalid range rejected) against a test MongoDB / mock
- [X] T017 [P] [US1] Test `POST /chat` availability path in `tests/test_chat.py` with the model/tool stubbed to assert the endpoint contract

### Implementation for User Story 1

- [X] T018 [US1] Implement `check_availability(check_in, check_out, room_type=None, phone_number)` in `src/app/db/bookings.py` returning active-booking-free rooms for the range (uses T007 overlap helper)
- [X] T019 [US1] Add `@function_tool check_availability(...)` in `src/app/agent/tools.py` (reads `phone_number` from run context, validates dates, calls the repository) and append it to `TOOLS`

**Checkpoint**: Availability works end to end via chat. MVP deliverable.

---

## Phase 4: User Story 2 - Book a Room for a Date (Priority: P2)

**Goal**: The assistant books a room for a stay and returns a reference.

**Independent Test**: Book an available room; a booking is created with a reference and the room is no longer available for those nights.

### Tests for User Story 2

- [X] T020 [P] [US2] Test `create_booking` in `tests/test_tools.py`: success, overlap refusal (FR-005/SC-003), invalid range refusal (FR-010)

### Implementation for User Story 2

- [X] T021 [US2] Implement `create_booking(room_name, check_in, check_out, phone_number, guest_name=None)` in `src/app/db/bookings.py` with an overlap guard at write time and unique reference generation
- [X] T022 [US2] Add `@function_tool book_room(...)` in `src/app/agent/tools.py` (context phone number, validation, unavailable/overlap messaging) and append to `TOOLS`

**Checkpoint**: Guests can find and book rooms; double-booking is prevented.

---

## Phase 5: User Story 3 - Cancel a Booking (Priority: P3)

**Goal**: The assistant cancels a guest's booking and frees the room.

**Independent Test**: Cancel an existing booking by reference; it becomes `cancelled` and the room is available again.

### Tests for User Story 3

- [X] T023 [P] [US3] Test `cancel_booking` in `tests/test_tools.py`: owner cancel succeeds; not-owned/not-found refused (FR-008); already-cancelled reports status

### Implementation for User Story 3

- [X] T024 [US3] Implement `cancel_booking(reference, phone_number)` in `src/app/db/bookings.py` (owner + active check, set `status=cancelled`, `cancelled_at`)
- [X] T025 [US3] Add `@function_tool cancel_booking(reference)` in `src/app/agent/tools.py` (context-scoped, clear not-found/permission messaging) and append to `TOOLS`

**Checkpoint**: Bookings can be cancelled safely and scoped to the owner.

---

## Phase 6: User Story 4 - View My Bookings (Priority: P3)

**Goal**: The assistant lists the requesting guest's bookings.

**Independent Test**: Ask "show my bookings"; only that phone number's bookings are listed with correct details.

### Tests for User Story 4

- [X] T026 [P] [US4] Test `list_bookings` in `tests/test_tools.py`: returns only the caller's bookings (SC-005); empty case handled

### Implementation for User Story 4

- [X] T027 [US4] Implement `list_bookings(phone_number, status="all")` in `src/app/db/bookings.py` scoped to the phone number
- [X] T028 [US4] Add `@function_tool list_bookings(status="all")` in `src/app/agent/tools.py` (context-scoped) and append to `TOOLS`

**Checkpoint**: All four tools live; every user story functional independently.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T029 [P] Test `RedisSession` round-trip in `tests/test_session.py` (add/get/pop/clear, TTL set) using fakeredis or a test Redis
- [X] T030 [P] Update `README.md` with the chat feature: new env vars, Redis + MongoDB prerequisites, `POST /chat` usage, and the seed step
- [X] T031 Verify guest-scoping hardening: confirm `phone_number` is passed only via run context, never as a model-visible tool argument (FR-008/SC-005), across all four tools in `src/app/agent/tools.py`
- [ ] T032 Run the full `quickstart.md` flow (seed → run → availability → book → list → cancel → cross-guest isolation) and confirm every success check passes — PARTIAL: data layer (availability/book/cancel/list/overlap/owner-scoping) verified against live MongoDB via `tests/test_tools.py`; the end-to-end LLM conversation was NOT run (needs a real `OPENAI_API_KEY`). Run this manually once a key is set.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories. The chat loop (T010–T014) must work first.
- **User Stories (Phase 3–6)**: all depend on Foundational. Once it is complete they are independent — each adds one repository function + one tool to shared files (`db/bookings.py`, `agent/tools.py`), so they serialize on those two files but do not depend on each other's behavior.
- **Polish (Phase 7)**: depends on the tools from the stories being present.

### Story Independence

- **US1** (availability) — read-only; MVP.
- **US2** (book) — independent; adds create path.
- **US3** (cancel) — independent; needs a booking to exist to demo (create via US2 or a fixture).
- **US4** (list) — independent; read-only over the caller's bookings.

### Within Each User Story

- Tests first (fail) → repository function → tool wiring (append to `TOOLS`).

## Parallel Opportunities

- Setup: T002, T003 in parallel.
- Foundational: T005, T007, T008, T009 in parallel (distinct files); T006 after T005; T010→T011→T012→T013→T014 chain; T015 in parallel with the agent chain.
- Within each story: the test task [P] can be written alongside; repository fn before tool.
- Cross-story: US1–US4 test tasks (T016/T017, T020, T023, T026) touch distinct test files and can be written in parallel; implementation serializes on `db/bookings.py` and `agent/tools.py`.
- Polish: T029, T030 in parallel.

### Parallel Example: Foundational

```bash
Task: "Beanie Room/Booking documents in src/app/db/documents.py"   # T005
Task: "overlap + reference helpers in src/app/db/bookings.py"       # T007
Task: "RunContext dataclass in src/app/agent/context.py"           # T008
Task: "RedisSession in src/app/agent/session.py"                   # T009
```

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) + Phase 2 (Foundational) — the working chat loop.
2. Phase 3 (US1) — availability answered via chat.
3. **STOP and VALIDATE**: seed rooms, ask availability, confirm correct rooms returned. Demonstrable MVP (chat + one tool + Redis context + Mongo data).

### Incremental Delivery

1. Setup + Foundational → chat loop ready.
2. US1 → availability (MVP) → demo.
3. US2 → booking → demo.
4. US3 → cancellation → demo.
5. US4 → list bookings → demo.
6. Polish → session tests, README, scoping hardening, full quickstart.

## Notes

- Requires a running Redis and MongoDB and an `OPENAI_API_KEY` for live end-to-end runs; unit tests stub the model and use a test DB / fakes.
- `phone_number` is trusted run-context data, never a model tool argument (security).
- Each tool is appended to `TOOLS` in `agent/tools.py`; `build_agent()` reads that registry, so `assistant.py` does not change per story.
- Verify tests fail before implementing; commit after each task or logical group.
