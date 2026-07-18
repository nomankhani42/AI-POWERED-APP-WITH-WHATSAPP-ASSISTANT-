# Phase 0 Research: Conversational Booking Assistant

The stack was largely fixed by the user (OpenAI Agents SDK + tools, Redis short-term
memory) and the constitution (MongoDB durable). No blocking `NEEDS CLARIFICATION` remained.
Agents SDK specifics were verified via Context7 (`/openai/openai-agents-python`).

## Decision 1: Agent + function tools (OpenAI Agents SDK)

- **Decision**: One `Agent` (model `gpt-4.1`) with four `@function_tool` functions:
  `check_availability`, `book_room`, `cancel_booking`, `list_bookings`. The endpoint calls
  `await Runner.run(agent, message, session=..., context=...)` and returns
  `result.final_output`.
- **Rationale**: Matches the Agents SDK's documented pattern (Context7): tools are plain
  decorated functions; the Runner handles the tool-calling loop. GPT-4.1 is the
  constitution's mandated model (Principle IV).
- **Alternatives considered**: Hand-rolled OpenAI tool-calling loop (reinvents the Runner);
  LangChain agents (not the constitution's chosen SDK).

## Decision 2: Redis-backed short-term Session

- **Decision**: Implement `RedisSession(SessionABC)` with `get_items`, `add_items`,
  `pop_item`, `clear_session`, storing the item list in Redis under a key derived from the
  conversation id, with a TTL (`SESSION_TTL_SECONDS`) so context is short-term only.
- **Rationale**: The clarified requirement is short-term context for the active conversation
  (spec Clarifications). The Agents SDK exposes exactly this extension point (`SessionABC`,
  per Context7), and Redis with TTL is the constitution's short-term store (Principle III).
- **Alternatives considered**: Built-in `SQLiteSession` (persists to disk, not the
  constitution's cache tier, and not short-term/TTL); in-process dict (lost on restart, not
  shared across workers).

## Decision 3: Guest scoping via run context (phone number)

- **Decision**: Pass a `RunContext` (dataclass holding `phone_number`) into `Runner.run`;
  tools read it via the context parameter to scope all booking reads/writes. The phone
  number and conversation id come from the `POST /chat` request and are trusted (spec
  Clarifications).
- **Rationale**: Keeps the guest identity out of the LLM-visible arguments (the model cannot
  spoof another guest's number), satisfying FR-008 / SC-005. This is the SDK's intended way
  to give tools trusted, non-model data.
- **Alternatives considered**: Passing phone number as a tool argument (LLM could fabricate
  it → cross-guest access risk); global request state (not concurrency-safe).

## Decision 4: Durable data — MongoDB via Beanie (async ODM on Motor)

- **Decision**: `Room` and `Booking` as Beanie `Document`s; a `bookings` repository exposes
  availability/create/cancel/list used by the tools. Motor provides async I/O.
- **Rationale**: Constitution Principle III (MongoDB durable) and Principle II (async). Beanie
  gives typed pydantic-v2 documents and indexes with minimal boilerplate (per the `fastapi`
  skill). Bookings must survive restarts (spec Clarifications).
- **Alternatives considered**: Raw Motor without ODM (more boilerplate for validation/
  indexes); SQL/SQLite (contradicts the constitution's MongoDB mandate).

## Decision 5: Preventing double-booking on overlapping stays

- **Decision**: Availability = no active booking for the room whose `[check_in, check_out)`
  interval overlaps the requested stay. Enforce at write time with a guarded create
  (re-check inside the create path) and a supporting index; the tool returns "unavailable"
  when the guard fails.
- **Rationale**: FR-005 / SC-003 require zero double-bookings even under concurrent attempts.
  Overlap check is `existing.check_in < requested.check_out AND requested.check_in <
  existing.check_out`.
- **Alternatives considered**: Per-date row locking (heavier); optimistic-only check before
  insert (race window between check and insert).

## Decision 6: Chat request/response contract

- **Decision**: `POST /chat` accepts `{ message, phone_number, conversation_id? }` and
  returns `{ reply, conversation_id }`. If `conversation_id` is omitted, the service derives
  a stable one (e.g., from phone number) so context threads correctly.
- **Rationale**: Minimal surface that carries the trusted identity and the session key the
  Redis Session needs; leaves room for the voice pipeline to supply the same fields later.
- **Alternatives considered**: Streaming/SSE responses (deferred; not required by the spec).

## Configuration additions (Principle V)

New required settings (no defaults → fail fast): `OPENAI_API_KEY`, `MONGODB_URI`. Settings
with safe defaults: `MONGODB_DB` (e.g. `voice_agent`), `REDIS_URL`
(`redis://localhost:6379/0`), `AGENT_MODEL` (`gpt-4.1`), `SESSION_TTL_SECONDS` (e.g. 3600).
All added to the existing `Settings` and `.env.example`.
