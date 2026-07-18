# Quickstart & Validation: Fix Voice Call Flow

How to validate that the greeting, turn-taking, tool fillers, and backend logging all work
end-to-end. Details live in [data-model.md](./data-model.md) and [contracts/](./contracts/); this is
the run/validate guide.

## Prerequisites

- `uv` installed; dependencies synced: `uv sync`
- `.env` populated (see `.env.example`) with `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`,
  `CARTESIA_API_KEY` (rollback only), `MONGODB_URI`, WhatsApp calling vars, and a running Redis (`REDIS_URL`).
- Optional TTS tuning: `DEEPGRAM_TTS_MODEL`, `DEEPGRAM_TTS_SPEED`, `TTS_FIRST_AUDIO_TIMEOUT_S`, and `TTS_EVENT_TIMEOUT_S`.
- Optional filler overrides: `FILLER_BOOK_ROOM`, `FILLER_CHECK_AVAILABILITY`, etc. (defaults ship).
- Seeded rooms so lookups return data: `uv run python scripts/seed_rooms.py`.

## 1. Automated tests (no live providers)

The behaviors are provable without placing a real call by driving the session with a fake agent
event stream and a fake media bridge (existing 003/004 test style).

```bash
uv run pytest tests/unit/test_fillers.py \
              tests/unit/test_observability.py \
              tests/integration/test_session_tool_filler.py \
              tests/integration/test_session_welcome.py \
              tests/integration/test_session_logging.py -v
```

Expected — each maps to a spec requirement / success criterion:

| Test | Proves |
|------|--------|
| `test_fillers` | Every tool name → its tailored phrase; unknown/`None` → non-empty generic (FR-009). |
| `test_session_tool_filler` | A `tool_call` event makes the session speak the tailored filler **before** the reply; a no-tool turn speaks **no** filler (US3, SC-003/SC-004). |
| `test_session_welcome` | Welcome plays fully and is **not** interrupted by caller audio during it (US1, FR-020, SC-001). |
| `test_session_logging` | Full timeline (`call_attended`→`call_welcome`→`call_turn`→`call_tool_call`/`call_filler`/`call_tool_result`→`call_ended`) is logged, each with `call_id`; two concurrent fake calls stay separable (US4, SC-005/SC-007). |
| `test_observability` | Each helper emits `call_id`; no secret/token/tool-arg appears in any record (FR-017, SC-006). |

Full suite (regression — 003/004 must still pass):

```bash
uv run pytest -q
```

## 2. Manual live-call validation

1. Start the service and expose the webhook:
   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   (Point the WhatsApp Business calling webhook at `/whatsapp/webhook`.)
2. Tail the backend logs and place a call from the configured WhatsApp number.
3. Walk the flow and confirm against Success Criteria:

| Step | Expected caller experience | Expected log line(s) |
|------|----------------------------|----------------------|
| Call accepted | Hears the full welcome before any listening (SC-001) | `call_attended`, `call_welcome`, `call_turn turn=0` |
| Talk over the welcome | Greeting is **not** cut off; the interruption is ignored (FR-020) | welcome completes; no barge-in during turn 0 |
| Ask a no-lookup question ("what can you do?") | Hears a spoken reply, **no** filler (SC-004) | `call_turn` with no `call_tool_call`/`call_filler` |
| Ask a lookup ("what rooms are free next weekend?") | Hears "Let me find that for you…" within ~2 s, then the answer (SC-003) | `call_tool_call tool=check_availability` → `call_filler` → `call_tool_result ok=true` → `call_turn` |
| Book ("book the Deluxe for those dates") | Hears "One moment, I'm booking that…" then the confirmation | `call_tool_call tool=book_room` → `call_filler` → `call_tool_result` |
| Stay silent | One "Are you still there?", then graceful hang-up | `call_reprompt`, then `call_ended` |

## 3. Reconstruct a call from logs (SC-005)

Filter the logs by a single `call_id` and confirm the ordered timeline is complete
(accept → welcome → each transcript/reply → each tool call + outcome → filler → end), with **no**
token, API key, or tool-argument value present (SC-006):

```bash
grep 'call_id=<THE_CALL_ID>' <logfile>   # or your JSON log query, keyed on extra.call_id
```

## Done when

- All tests in step 1 pass and the full suite is green (no 003/004 regressions).
- Manual walkthrough matches every row in step 2.
- A call's full timeline is reconstructable from logs alone (step 3) with no secrets present.
