# Implementation Plan: Cancellation Message Automation & Room Type Selection

**Branch**: `007-cancel-message-room-select` | **Date**: 2026-07-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-cancel-message-room-select/spec.md`

## Summary

Two coupled improvements to the booking assistant: (1) guarantee that every successful
booking cancellation — from the chat API, WhatsApp chat, or a voice call — automatically
sends exactly one WhatsApp cancellation message, with delivery failures logged for
operators and never blocking the cancellation; (2) make room type a first-class
selection: a fixed canonical `RoomType` enum enforced at write time, a channel-aware
agent tool that offers the distinct types of active rooms, and — on the WhatsApp chat
channel — a tappable interactive list message whose `list_reply` is handled by a new
inbound-message path on the existing webhook. Voice calls fall back to a spoken
enumeration of the same live types.

Technical approach: extend the existing `/whatsapp/webhook` dispatcher to route
`messages[]` (text + `list_reply`) alongside `calls[]`, dedupe by `wamid`
(Redis-first, Mongo backstop, mirroring `db/calls.py`), and run the agent turn as a
fire-and-forget background task so Meta always gets an immediate 200. Room-type
presentation is an agent tool that reads the live catalog and, when the run context
says the channel is WhatsApp chat, sends an `interactive type: list` message
(verified shape from the meta-whatsapp-api skill) instead of returning prose.

## Technical Context

**Language/Version**: Python ≥3.12, managed exclusively with `uv` (locked via `uv.lock`)

**Primary Dependencies**: FastAPI + Uvicorn, OpenAI Agents SDK (`openai-agents`, GPT-4.1),
httpx (Graph API), Beanie/Motor (MongoDB ODM), redis-py asyncio; Deepgram voice path
untouched by this feature

**Storage**: MongoDB — `rooms`, `bookings`, new `inbound_messages` dedupe collection;
Redis — agent sessions (existing `RedisSession`), short-term webhook dedupe keys

**Testing**: pytest + pytest-asyncio, fakeredis, FastAPI TestClient, httpx transport
mocking for Graph API sends

**Target Platform**: Linux server (single FastAPI service)

**Project Type**: Single web service (`src/app`), no frontend

**Performance Goals**: Webhook acknowledges 200 immediately (agent turn runs in a
background task — Meta retries slow webhooks); cancellation notice dispatched in the
same request as the cancellation (SC-003: ≤1 min)

**Constraints**: Webhook must ALWAYS return 200 (existing hard rule, FR-004 of 003);
freeform sends only inside the 24-hour customer-service window; interactive list limits
(≤10 rows total, row title ≤24 chars, row id ≤200 chars) — 8 canonical types fit one
section; no new secrets (reuses `WHATSAPP_TOKEN` / `WHATSAPP_PHONE_ID` / verify token /
app secret)

**Scale/Scope**: Single hotel, 8 room types, low message volume; no horizontal-scale
work needed

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| I | Modular Architecture | PASS | New concerns get their own modules: `services/whatsapp_chat.py` (inbound message processing), a new sender in `services/whatsapp_messages.py`, `db/inbound_messages.py` (dedupe store). Webhook route stays a thin dispatcher. |
| II | Async-First FastAPI | PASS | All new I/O uses existing async clients (httpx, Beanie, redis asyncio); agent turns run as background asyncio tasks via the existing `_fire_and_forget` pattern. No new dependencies. |
| III | Layered Memory | PASS | Message dedupe: Redis SETNX (hot path) + Mongo unique-index backstop, mirroring `db/calls.py`. Conversation context stays in the existing Redis-backed session; durable rooms/bookings stay in MongoDB behind `db/` modules. |
| IV | Voice Pipeline Integrity | PASS | STT→Agent→TTS contract untouched. The new `offer_room_types` tool is channel-aware: on voice it returns text for the agent to speak (existing tool-filler flow from feature 005 keeps working); no media-path changes. |
| V | Configuration & Secrets | PASS | No new settings or secrets; Graph credentials come from the existing typed `Settings`. |
| VI | Documentation-Driven Dev | PASS | Interactive-list payload, inbound `text`/`list_reply` shapes, and 24h-window rules verified against the meta-whatsapp-api skill references during planning; Agents SDK tool patterns follow the existing feature-005 contracts. |

**Post-Phase-1 re-check**: PASS — design artifacts introduce no new dependencies, no
cross-layer leaks (webhook → service → db), and no voice-pipeline changes.

## Project Structure

### Documentation (this feature)

```text
specs/007-cancel-message-room-select/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── whatsapp-messages-webhook.md
│   ├── room-type-selection.md
│   └── cancellation-notification.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/app/
├── api/routes/
│   └── whatsapp_calling.py      # MODIFY: _process_payload also dispatches value["messages"]
├── services/
│   ├── whatsapp_messages.py     # MODIFY: + send_room_type_list() interactive-list sender;
│   │                            #   structured failure logging for cancellation sends
│   └── whatsapp_chat.py         # NEW: inbound message processing — extract text/list_reply,
│                                #   dedupe, run agent turn, reply via send_text/list
├── agent/
│   ├── context.py               # MODIFY: RunContext gains channel: "api" | "whatsapp" | "voice"
│   ├── tools.py                 # MODIFY: + offer_room_types tool (channel-aware)
│   └── assistant.py             # MODIFY: prompt stops hardcoding types; directs to the tool
├── db/
│   ├── documents.py             # MODIFY: RoomType str-enum; Room.room_type: RoomType with
│   │                            #   normalization; InboundMessage dedupe document
│   ├── bookings.py              # MODIFY: + list_active_room_types(); normalize type filter
│   └── inbound_messages.py      # NEW: is_duplicate/record for wamid dedupe (calls.py pattern)
scripts/
└── seed_rooms.py                # MODIFY: seed via RoomType enum (single source of truth)

tests/
├── contract/
│   └── test_messages_webhook.py     # NEW: envelope routing, dedupe, always-200
├── unit/
│   └── test_room_type.py            # NEW: enum normalization/rejection at write time
├── integration/
│   ├── test_whatsapp_chat_flow.py   # NEW: text + list_reply → agent turn → outbound reply
│   └── test_cancellation_notice.py  # NEW: exactly-once, failure isolation, structured log
└── test_tools.py                    # EXTEND: offer_room_types per channel; cancel notice path
```

**Structure Decision**: Single-service layout already in place (`src/app` with
`api/routes`, `services`, `agent`, `db`; tests split contract/unit/integration). This
feature only adds two modules and modifies six, all within existing concerns.

## Complexity Tracking

No constitution violations — table intentionally empty.

## Known Limitation (recorded, accepted)

A cancellation initiated on a **voice call** by a guest who has never sent a WhatsApp
text message may fall outside Meta's 24-hour customer-service window; the freeform
notice can then be rejected (Graph error 131047 re-engagement). Per clarification, the
failure is logged as a structured operator-visible entry and the cancellation stands.
The durable fix — an approved template message — is explicitly out of scope for 007
(would require Meta template approval; see research.md R7).
