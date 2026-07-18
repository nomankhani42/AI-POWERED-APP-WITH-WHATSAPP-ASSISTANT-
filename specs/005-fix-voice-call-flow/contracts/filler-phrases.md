# Contract: Tool-tailored filler phrases

**Module**: `src/app/services/media/fillers.py` (new, small helper)
**Consumer**: `src/app/services/media/session.py`

Resolves the tool being called into a natural, action-specific spoken filler (Q3/FR-006/FR-009).

## Interface

```python
def filler_for(tool_name: str | None) -> str: ...
```

- **Input**: the tool name from a `tool_call` event (`AgentStreamEvent.tool_name`), possibly `None`.
- **Output**: a **non-empty** phrase to speak. Never returns `""` (silence during a lookup is the
  defect being fixed).
- Phrases are read from settings (`filler_*`), so wording is configurable without touching the loop.

## Mapping

| `tool_name` | Returned phrase (default) | Setting |
|-------------|---------------------------|---------|
| `"check_availability"` | "Let me find that for you…" | `filler_check_availability` |
| `"book_room"` | "One moment, I'm booking that…" | `filler_book_room` |
| `"cancel_booking"` | "Let me cancel that…" | `filler_cancel_booking` |
| `"list_bookings"` | "Let me pull up your bookings…" | `filler_list_bookings` |
| any other / `None` | "Let me check that for you…" | `filler_generic` |

## Rules

- **Total**: every input resolves to a phrase (known tools → tailored; unknown/`None` → generic).
- **Pure**: no I/O beyond reading cached settings; deterministic for a given name + settings.
- **One filler per tool call** (FR-006). If a turn triggers several tools, each `tool_call` event
  gets its own tailored filler (data-model / spec edge case "Multiple tools in one turn").
