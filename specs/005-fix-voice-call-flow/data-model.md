# Phase 1 Data Model: Fix Voice Call Flow

This feature introduces **no persisted entities** — nothing new is written to MongoDB or Redis. The
"data" here is transient in-memory values that flow through one call's turn, plus the log-record
vocabulary. Durable `Call`/`CallEvent` documents (feature 003) are unchanged.

## Transient types (`src/app/services/media/types.py`)

### AgentStreamEvent *(new)*

One event emitted by the agent turn while streaming, so the media session can react to tool calls
distinctly from reply text. A tagged union over `kind`.

| Field | Type | Notes |
|-------|------|-------|
| `kind` | enum: `text_delta` \| `tool_call` \| `tool_output` | Discriminator. |
| `text` | `str \| None` | Set for `text_delta` — an incremental reply chunk (as `run_turn_stream` yields today). |
| `tool_name` | `str \| None` | Set for `tool_call` / `tool_output` — the function tool name (e.g. `book_room`); read from `item.raw_item.name`, may be `None` if unavailable. |
| `ok` | `bool \| None` | Set for `tool_output` — whether the tool returned a usable result (`True`) or an error/failure (`False`). |

- **Source events** (Agents SDK, research §1): `tool_call` ← `run_item_stream_event` /
  `item.type == "tool_call_item"`; `tool_output` ← `item.type == "tool_call_output_item"`;
  `text_delta` ← `raw_response_event` / `ResponseTextDeltaEvent`.
- **Ordering guarantee within a turn**: zero or more (`tool_call` → `tool_output`) pairs, then the
  reply `text_delta`s. The session speaks a filler on each `tool_call`, logs each `tool_output`,
  and streams the `text_delta`s to TTS as the answer.
- **Validation**: exactly the fields for the given `kind` are populated; consumers branch on `kind`
  and ignore irrelevant fields.

### ConversationTurn *(existing — unchanged)*

Already defined; turn 0 = welcome. Reused as-is for `log_turn`.

### TranscriptSegment / SpeechChunk *(existing — unchanged)*

Reused as-is.

## Value object (`src/app/services/media/fillers.py`) *(new)*

### FillerPhrase mapping

Not a stored entity — a pure function `filler_for(tool_name: str | None) -> str`.

| Tool name | Tailored filler (default; overridable via settings) |
|-----------|------------------------------------------------------|
| `check_availability` | "Let me find that for you…" |
| `book_room` | "One moment, I'm booking that…" |
| `cancel_booking` | "Let me cancel that…" |
| `list_bookings` | "Let me pull up your bookings…" |
| unknown / `None` | "Let me check that for you…" (generic fallback) |

- **Rule**: every known tool maps to a phrase; any unknown/absent name yields the generic fallback
  (never an empty string — silence during a lookup is the bug being fixed).

## Configuration additions (`src/app/core/config.py`)

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `filler_generic` | `str` | "Let me check that for you…" | Fallback filler (FR-006). |
| `filler_check_availability` | `str` | "Let me find that for you…" | Per-tool filler (FR-009). |
| `filler_book_room` | `str` | "One moment, I'm booking that…" | Per-tool filler. |
| `filler_cancel_booking` | `str` | "Let me cancel that…" | Per-tool filler. |
| `filler_list_bookings` | `str` | "Let me pull up your bookings…" | Per-tool filler. |

`welcome_message`, `barge_in_enabled`, `caller_silence_timeout_s`, `provider_retry_attempts` already
exist and are reused (no change). All new settings have defaults, so no new required secrets and no
fail-fast impact (Principle V). *(If a single mapping setting is preferred at implementation time, a
`dict[str,str]` field is an acceptable equivalent — the contract is "each tool resolves to a
non-empty phrase.")*

## Log record vocabulary (`src/app/services/media/observability.py`)

Each is a structured `INFO` log line carrying `call_id` (FR-011–017). Not persisted.

| Record | Emitted when | Key fields (never secrets) |
|--------|-------------|----------------------------|
| `call_attended` *(exists)* | Call accepted | `call_id`, `from` |
| `call_welcome` *(new)* | Welcome starts playing | `call_id` |
| `call_turn` *(exists)* | A turn completes | `call_id`, `turn`, `transcript`, `reply`, `duration_s` |
| `call_tool_call` *(new)* | `tool_call_item` seen | `call_id`, `turn`, `tool` |
| `call_tool_result` *(new)* | `tool_call_output_item` seen | `call_id`, `turn`, `tool`, `ok` |
| `call_filler` *(new)* | Filler spoken | `call_id`, `turn`, `tool` |
| `call_playback` *(new)* | Reply playback start/stop | `call_id`, `turn`, `state` (start/stop) |
| `call_barge_in` *(new)* | Caller interrupts a reply | `call_id`, `turn` |
| `call_reprompt` *(new)* | Silence re-prompt spoken | `call_id` |
| `call_fallback` *(new)* | Apology/fallback spoken | `call_id`, `turn` |
| `call_ended` *(new)* | Call teardown | `call_id`, `reason` |

**Secret-safety rule (FR-017/SC-006)**: log the tool *name* and a boolean `ok`, never tool argument
values or provider tokens; transcript/reply text is caller conversational content (already logged by
the existing `call_turn`) — no credentials appear in any record.
