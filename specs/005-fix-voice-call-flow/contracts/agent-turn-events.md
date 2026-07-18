# Contract: Agent turn event stream

**Module**: `src/app/agent/service.py`
**Consumer**: `src/app/services/media/session.py` (`_CallSession._handle_turn`)

Replaces the media loop's dependence on a text-only stream with a structured event stream so the
session can react to tool calls (FR-006) while still streaming reply text to TTS (FR-004/007).

## Interface

```python
async def run_turn_events(
    message: str,
    phone_number: str,
    conversation_id: str | None = None,
) -> AsyncIterator[AgentStreamEvent]: ...
```

- Runs **one** agent turn via `Runner.run_streamed(build_agent(), message, session=RedisSession(conv_id),
  context=RunContext(phone_number=phone_number))` and yields `AgentStreamEvent`s as they arrive.
- Same trusted-context guarantee as `run_turn`: `phone_number` is passed via run context, never as a
  model argument.
- The stream MUST be consumed to completion by the caller so Redis session persistence finishes
  (Agents SDK streaming requirement).

## Event mapping (from `stream_events()`)

| SDK event | Yielded `AgentStreamEvent` |
|-----------|----------------------------|
| `raw_response_event` where `data` is `ResponseTextDeltaEvent` with non-empty `delta` | `kind="text_delta"`, `text=delta` |
| `run_item_stream_event` where `item.type == "tool_call_item"` | `kind="tool_call"`, `tool_name=getattr(item.raw_item, "name", None)` |
| `run_item_stream_event` where `item.type == "tool_call_output_item"` | `kind="tool_output"`, `tool_name=<name>`, `ok=<True unless the output signals an error>` |
| anything else | ignored (not yielded) |

## Ordering contract

Within one turn, for each tool the model uses: a `tool_call` event precedes its `tool_output`
event, and all reply `text_delta` events follow the last `tool_output`. (This is what lets the
session speak the filler *before* the answer.)

## Backward compatibility

- The existing text-only `run_turn` (non-streaming, used by the WhatsApp chat endpoint) is
  **unchanged**.
- `run_turn_stream` (text-delta only) is either kept for the chat/streaming text path or trivially
  reimplemented as `async for e in run_turn_events(...): if e.kind == "text_delta": yield e.text`.
  Either is acceptable; the WhatsApp/chat behavior MUST NOT change.

## Errors

Provider/model errors propagate to the caller (the session already wraps the turn in try/except and
degrades to a spoken apology + logged failure, FR-019). `run_turn_events` MUST NOT swallow errors
silently.
