# Contract: Agent Turn (streaming reply)

**Module**: `src/app/agent/service.py` (`run_turn`, new `run_turn_stream`)
**Consumers**: `src/app/services/media/session.py`
**Related**: FR-007, FR-008, FR-010; research.md §4

## New: `run_turn_stream`

```
async def run_turn_stream(
    message: str, phone_number: str, conversation_id: str | None = None,
) -> AsyncIterator[str]
```

- Uses `Runner.run_streamed(build_agent(), message, session=RedisSession(conv_id),
  context=RunContext(phone_number=phone_number))`.
- Iterates `result.stream_events()`; for events where `event.type == "raw_response_event"`
  and `isinstance(event.data, ResponseTextDeltaEvent)`, yields `event.data.delta`.
- MUST consume the stream to completion (even after the last visible token) so Redis session
  persistence / history completes (per Agents SDK streaming docs).
- The phone number is passed via trusted `RunContext`, never as a model argument (unchanged
  from `run_turn`).

**Behavior**: yields reply text incrementally in order; the caller (`session._handle_turn`)
pipes it straight into `synthesize_stream(...)` and separately joins the deltas to form the
full reply text for `log_turn` (ConversationTurn.reply).

## Retained: `run_turn`
The existing non-streaming `run_turn(...) -> (reply, conversation_id)` stays for the chat
route and tests. Voice loop switches to `run_turn_stream`.

**Acceptance**:
- With a mocked `Runner.run_streamed` emitting deltas `["Hel", "lo", " there"]`,
  `run_turn_stream` yields exactly those three strings in order.
- Joining the yielded deltas reproduces the full reply.
- Non-text events (tool-call args, reasoning) are ignored by the text generator.
- Session/history side effects run after the last delta (stream fully consumed).
