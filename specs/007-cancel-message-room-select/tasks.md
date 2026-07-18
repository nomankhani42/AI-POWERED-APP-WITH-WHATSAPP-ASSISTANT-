# Tasks: Cancellation Message Automation & Room Type Selection

**Input**: Design documents from `/specs/007-cancel-message-room-select/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the constitution requires independently testable modules, research
R8 defines the strategy, and quickstart.md's primary gate is the test suite. Write each
story's tests first and watch them fail before implementing.

**Organization**: Tasks are grouped by user story so each story is an independently
testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 (cancellation automation), US2 (room-type selection), US3 (governed set)

## Phase 1: Setup

**Purpose**: Confirm the existing environment supports the feature — 007 adds no new
dependencies, settings, or services.

- [X] T001 Verify dev environment: `uv sync` passes, MongoDB + Redis are up via `backend/docker-compose.yml`, existing suite is green (`uv run pytest -q`) as the pre-change baseline

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared model and context changes that US2 and US3 both build on, plus the
dedupe store the webhook path requires. US1 depends only on T001.

**⚠️ CRITICAL**: Complete before starting US2/US3 work.

- [X] T002 [P] Add `RoomType` str-enum (single, twin, double, deluxe, accessible, family, executive, suite), change `Room.room_type` to `RoomType` with a strip/lowercase before-validator, and add the `InboundMessage` document (unique index on `wamid`; fields per data-model.md) in `src/app/db/documents.py`; register `InboundMessage` in the Beanie init in `src/app/db/mongo.py`
- [X] T003 [P] Add `channel: Literal["api", "whatsapp", "voice"] = "api"` to `RunContext` in `src/app/agent/context.py`; thread a `channel` parameter through `run_turn` / `run_turn_events` in `src/app/agent/service.py` (default `"api"`); pass `"voice"` from the media session call site in `src/app/services/media/session.py`
- [X] T004 Create `src/app/db/inbound_messages.py` with `is_duplicate(wamid)` / `record(...)` — Redis `SET NX EX` hot path + Mongo unique-index backstop, mirroring `src/app/db/calls.py` (research R3)
- [X] T005 Add `list_active_room_types() -> list[RoomType]` (distinct types of active rooms, enum declaration order) and normalize/validate the `room_type` filter in `check_availability` in `src/app/db/bookings.py`

**Checkpoint**: Enum, context channel, and dedupe store in place — user stories can begin.

---

## Phase 3: User Story 1 — Automated Cancellation Message on Every Cancellation (Priority: P1) 🎯 MVP

**Goal**: Every successful cancellation, from any channel, sends exactly one WhatsApp
cancellation message (reference, room, dates); failures are logged, never blocking.

**Independent Test**: Cancel an active booking through the chat flow with a mocked Graph
transport — exactly one send with the four content elements; repeat cancel and transport
failure send nothing extra and never fail the cancellation (quickstart §4–5).

### Tests for User Story 1 (write first, must fail)

- [X] T006 [P] [US1] Integration test in `tests/integration/test_cancellation_notice.py`: successful cancel → exactly one send containing reference/room/check-in/check-out; repeat cancel → zero sends; unknown reference / other guest's reference → zero sends; transport raising `WhatsAppMessageError` → cancellation still succeeds and one structured ERROR record `booking.cancellation_notice_failed` with reference + recipient (assert via `caplog`); contract: `contracts/cancellation-notification.md`

### Implementation for User Story 1

- [X] T007 [US1] Make `cancel_booking` an atomic active→cancelled find-and-modify conditioned on `status == active` and the guest's phone number in `src/app/db/bookings.py`, returning the booking only when this call performed the transition (FR-003 exactly-once as a data guarantee)
- [X] T008 [US1] Harden `_safe_send_booking_cancellation` in `src/app/agent/tools.py`: catch all send errors, emit one structured ERROR log entry (`booking.cancellation_notice_failed`, booking reference, recipient, error class), never propagate (FR-004)
- [X] T009 [US1] Extend `tests/test_tools.py`: cancel tool sends the notice exactly once on success and not at all when the repo returns `None`

**Checkpoint**: US1 fully functional — deliverable MVP.

---

## Phase 4: User Story 2 — Choose Room Type from a Selection List (Priority: P2)

**Goal**: WhatsApp chat becomes a working assistant channel; when a room type is needed
there, the guest gets a tappable interactive list whose tap continues the flow; typed
input still works; voice/API get enumerated text.

**Independent Test**: POST text and `list_reply` webhook envelopes (signature skip on)
with a mocked Graph transport — one agent turn per unique wamid, interactive-list payload
matches the contract, tap of `room_type:deluxe` continues the flow as "deluxe"
(quickstart §2–3).

### Tests for User Story 2 (write first, must fail)

- [X] T010 [P] [US2] Contract test in `tests/contract/test_messages_webhook.py`: text envelope → 200 + one turn dispatched with sender number; same wamid twice → one turn; `list_reply` id `room_type:deluxe` → turn input `deluxe`; unsupported type (e.g. image) → 200 + brief text-only reply; malformed payload → 200, no crash; envelope with both `calls` and `messages` → both paths dispatched; contract: `contracts/whatsapp-messages-webhook.md`
- [X] T011 [P] [US2] Integration test in `tests/integration/test_whatsapp_chat_flow.py` with mocked Graph transport and stubbed agent turn: inbound text → outbound `send_text` reply to sender; interactive-list send payload matches `contracts/room-type-selection.md` (rows `room_type:<type>`, ≤10 rows, title ≤24)

### Implementation for User Story 2

- [X] T012 [P] [US2] Add `send_room_type_list(to, room_types)` interactive-list sender in `src/app/services/whatsapp_messages.py` using `_post_message`, honoring list limits (button ≤20 chars, row title ≤24, description ≤72, id `room_type:<canonical>`)
- [X] T013 [US2] Add `offer_room_types` tool in `src/app/agent/tools.py` (depends on T003, T005, T012): reads `list_active_room_types()`; `whatsapp` channel → send list + return sentinel ("options shown as tappable list — don't re-enumerate"), with enumerated-text fallback + log on send failure; `voice`/`api` → enumerated types with capacity; empty catalog → "no room types currently available"; register in `TOOLS`
- [X] T014 [US2] Create `src/app/services/whatsapp_chat.py` (depends on T004): per-message processing — dedupe by wamid, extract guest text (`text.body`; `list_reply.id` `room_type:<canonical>` → `<canonical>`, else `list_reply.title`; unsupported types → polite text-only notice), run `run_turn(..., channel="whatsapp")` with `from` as phone/conversation id, reply via `send_text`; catch and log all errors (webhook already acked)
- [X] T015 [US2] Extend `_process_payload` in `src/app/api/routes/whatsapp_calling.py`: dispatch `value["messages"]` to `whatsapp_chat` via `_fire_and_forget` after dedupe, ignore `value["statuses"]`, keep calls path and always-200 behavior untouched
- [X] T016 [US2] Update the system prompt in `src/app/agent/assistant.py`: remove the hardcoded room-type/price list (fixes 5-vs-8 drift), instruct calling `offer_room_types` whenever the guest must pick/narrow by type, map close-match typed names to canonical types, re-offer options on unknown type (FR-008)
- [X] T017 [US2] Extend `tests/test_tools.py` for `offer_room_types`: whatsapp → Graph payload shape + sentinel return; voice/api → enumerated text, no send; type with no active rooms excluded; send failure → text fallback

**Checkpoint**: US1 and US2 work independently; WhatsApp chat channel live end-to-end.

---

## Phase 5: User Story 3 — Room Types Are a Governed Set (Priority: P3)

**Goal**: Write-time enforcement of the canonical set everywhere room types are written,
and guest-facing options that provably track the active catalog.

**Independent Test**: Insert a room with a junk/variant type → rejected/normalized;
deactivate all rooms of a type → that type disappears from offered options
(quickstart §6).

### Tests for User Story 3 (write first, must fail)

- [X] T018 [P] [US3] Unit test in `tests/unit/test_room_type.py`: `" Deluxe "` normalizes to `deluxe`; `"penthouse"` rejected at validation; `list_active_room_types` returns enum-declaration order and excludes types whose rooms are all inactive

### Implementation for User Story 3

- [X] T019 [US3] Rekey `scripts/seed_rooms.py` off the `RoomType` enum (iterate enum members; drop the "keep in sync" drift warning comment) so seed data and schema share one source of truth
- [X] T020 [US3] In `src/app/agent/tools.py` `check_availability`, surface the repo's unknown-type validation as a tool message naming the valid types (so the agent re-offers options instead of querying with a junk filter — pairs with T016)

**Checkpoint**: All three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T021 [P] Update `README.md`: document the WhatsApp chat channel (same webhook URL, `messages` field), the room-type selection flow, and the known 24h-window limitation for voice-only guests (no new env vars — state that explicitly)
- [X] T022 Run the full suite (`uv run pytest -q`) including the existing voice integration tests (tool-filler flow must be unaffected by the new tool), then walk `specs/007-cancel-message-room-select/quickstart.md` §2–6 against a running server

---

## Phase 7: Extension — Room Photo Carousel (added 2026-07-16, FR-011..FR-013)

- [X] T023 Add `Room.image_url: str | None` in `src/app/db/documents.py`; add `business_number` to `RunContext` (`src/app/agent/context.py`) and thread it through `run_turn` (`src/app/agent/service.py`) from the webhook's `metadata.display_phone_number` in `src/app/services/whatsapp_chat.py`
- [X] T024 Seed one verified public Unsplash photo per room type in `scripts/seed_rooms.py` (each URL checked live: HTTP 200, `image/jpeg`, ≪5 MB) and refresh existing rooms
- [X] T025 Add `send_room_carousel(to, rooms, bot_display_number)` in `src/app/services/whatsapp_messages.py` — verified `interactive type: carousel` shape, 2–10 `cta_url` cards, image headers, wa.me tap-back deep links (`Book <room>` pre-filled)
- [X] T026 Extend `check_availability` (`src/app/agent/tools.py` → `_check_availability_impl`): on the whatsapp channel with ≥2 photographed matches, send the carousel and return a don't-re-list sentinel (naming any rooms left out); text fallback below 2 cards, on send failure (logged `room_carousel.send_failed`), and on voice/api
- [X] T027 Tests: carousel payload shape/cap/min-cards in `tests/integration/test_whatsapp_chat_flow.py`; per-channel carousel behavior, failure fallback in `tests/test_tools.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after T001. T002/T003 parallel; T004 after T002; T005 after T002.
- **US1 (Phase 3)**: only needs T001 — can start immediately, in parallel with Phase 2.
- **US2 (Phase 4)**: needs T002–T005 (enum, channel, dedupe, repo read).
- **US3 (Phase 5)**: needs T002 + T005; independent of US1/US2 (T020 pairs naturally with T016 but does not require it).
- **Polish (Phase 6)**: after all desired stories.

### Task-level notes

- T007 and T005 both edit `src/app/db/bookings.py` — do sequentially if one person, or land T005 (Phase 2) first.
- T013 and T008/T020 all edit `src/app/agent/tools.py` — sequence within/across stories accordingly.

### Parallel Opportunities

- T002 ∥ T003 (different files).
- T006 ∥ T010 ∥ T011 ∥ T018 — all test files are new and independent.
- T012 ∥ T014-prep: T012 (messages service) is independent of T014 until wired.
- With two people: A takes US1 (T006–T009) while B does Phase 2 then US2.

## Parallel Example: after Phase 2

```bash
# Write all remaining story tests together (they must fail first):
Task: "Integration test in tests/integration/test_cancellation_notice.py"      # T006
Task: "Contract test in tests/contract/test_messages_webhook.py"               # T010
Task: "Integration test in tests/integration/test_whatsapp_chat_flow.py"       # T011
Task: "Unit test in tests/unit/test_room_type.py"                              # T018
```

---

## Implementation Strategy

### MVP First (US1 only)

1. T001 baseline → T006 failing test → T007–T009.
2. **STOP and VALIDATE**: quickstart §4–5. This alone delivers the guaranteed
   cancellation notice — deployable value with zero webhook/enum risk.

### Incremental Delivery

1. US1 (MVP) → validate → 2. Phase 2 foundations → 3. US2 (WhatsApp chat + select)
→ validate quickstart §2–3 → 4. US3 (governed set) → validate quickstart §6
→ 5. Polish (T021–T022).

Each story lands green without breaking the previous ones; the voice pipeline is never
touched except for the additive `channel` argument (T003) and one new read-only tool.
