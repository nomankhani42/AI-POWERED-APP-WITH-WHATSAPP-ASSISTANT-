# Contract: Call observability log vocabulary

**Module**: `src/app/services/media/observability.py`
**Consumer**: `src/app/services/media/session.py`
**Logger**: `logging.getLogger("app.call")`

Every meaningful call-flow milestone is emitted as a structured `INFO` record correlated by
`call_id` so an operator can reconstruct a single call's timeline from backend logs alone
(FR-011–017, SC-005). No record ever contains tokens, secrets, or tool argument values
(FR-017, SC-006).

## Helpers

Each helper logs at `INFO` with a human-readable message plus an `extra` dict carrying `event` and
`call_id` (mirroring the existing `log_call_attended` / `log_turn` style).

| Helper | Signature | `event` | Extra fields |
|--------|-----------|---------|--------------|
| `log_call_attended` *(exists)* | `(call_id, wa_call_from)` | `call_attended` | `from` |
| `log_welcome` *(new)* | `(call_id)` | `call_welcome` | — |
| `log_turn` *(exists)* | `(ConversationTurn)` | `call_turn` | `turn`, `duration_s` |
| `log_tool_call` *(new)* | `(call_id, turn, tool)` | `call_tool_call` | `turn`, `tool` |
| `log_tool_result` *(new)* | `(call_id, turn, tool, ok)` | `call_tool_result` | `turn`, `tool`, `ok` |
| `log_filler` *(new)* | `(call_id, turn, tool)` | `call_filler` | `turn`, `tool` |
| `log_playback` *(new)* | `(call_id, turn, state)` | `call_playback` | `turn`, `state` (`"start"`/`"stop"`) |
| `log_barge_in` *(new)* | `(call_id, turn)` | `call_barge_in` | `turn` |
| `log_reprompt` *(new)* | `(call_id)` | `call_reprompt` | — |
| `log_fallback` *(new)* | `(call_id, turn)` | `call_fallback` | `turn` |
| `log_call_ended` *(new)* | `(call_id, reason)` | `call_ended` | `reason` |

## Guarantees

- **Correlation**: every record includes `call_id` (message and `extra`). (FR-016)
- **Visibility**: milestones are `INFO`, not `DEBUG`, so they appear in normal backend logs. (FR-017)
- **Secret-safety**: only tool *names*, booleans, durations, turn numbers, the caller number, and
  conversational transcript/reply are logged — never provider tokens, API keys, or tool argument
  payloads. (FR-017, SC-006)
- **Per-call isolation**: helpers are stateless; concurrency isolation comes from each record
  carrying its own `call_id`, so interleaved concurrent-call logs remain separable. (FR-018, SC-007)
- **Failure logging**: on any stage failure the session logs via the relevant helper plus the
  existing `logger.exception`, with `call_id` + stage context. (FR-019)

## Expected timeline for a lookup turn (reconstruction example, SC-005)

```
call_attended → call_welcome → call_turn(0)
→ call_turn(n) driven by: call_tool_call(book_room) → call_filler(book_room)
   → call_tool_result(book_room, ok=true) → call_playback(start) → call_playback(stop)
→ … → call_ended(reason)
```
