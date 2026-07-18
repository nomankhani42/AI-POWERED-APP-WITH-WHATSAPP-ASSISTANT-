# Quickstart: Meta Voice Call Webhook & Speech Services

Validation guide proving the three stages work — webhook, STT, TTS — independently and together.
Design details live in [plan.md](./plan.md), [data-model.md](./data-model.md), and
[contracts/](./contracts/).

## Prerequisites

- `uv` installed; MongoDB and Redis reachable (see existing local setup).
- New dependencies added: `uv add deepgram-sdk cartesia aiortc httpx`
  (`httpx` promoted to a runtime dependency).
- `.env` populated (see `.env.example`) with the new keys:
  `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID`, `WHATSAPP_TOKEN`,
  `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`
  (plus existing `OPENAI_API_KEY`, `MONGODB_URI`, `REDIS_URL`). Startup fails fast if any
  required secret is missing (Principle V).
- Optional tunables (have defaults): `WELCOME_MESSAGE` (auto greeting text),
  `CALLER_SILENCE_TIMEOUT_S`, `DEEPGRAM_MODEL`, `DEEPGRAM_TTS_MODEL`, `DEEPGRAM_TTS_SPEED`, `TTS_FIRST_AUDIO_TIMEOUT_S`, `TTS_EVENT_TIMEOUT_S`, `STT_SAMPLE_RATE`. Cartesia model/voice settings remain for rollback.

## Run the service

```bash
uv run uvicorn app.main:app --reload --app-dir src
```

## Scenario 1 — Webhook verification (US1, FR-002)

```bash
curl -s "http://localhost:8000/webhooks/whatsapp/calls?hub.mode=subscribe&hub.verify_token=$WHATSAPP_VERIFY_TOKEN&hub.challenge=1234567"
```

**Expected**: body is exactly `1234567`, HTTP 200. A wrong token returns HTTP 403 with no body.

## Scenario 2 — Call event handling & idempotency (US1, FR-003/005/006)

Send a signed `connect` event (helper computes the `X-Hub-Signature-256` HMAC from
`WHATSAPP_APP_SECRET`), then send the **same** event again.

**Expected**:
- First POST → 200 `{"status":"received"}`; a `Call` (status advancing to `connecting`/`connected`)
  and one `CallEvent` exist in MongoDB.
- Duplicate POST → 200; **no** second `CallEvent`, no duplicate `Call` (idempotency).
- An event with a bad/missing signature → 200 but **no** record created (rejected silently).
- A `terminate` event → `Call.status == "ended"` with `ended_at` set.

## Scenario 3 — STT service in isolation (US2, FR-007/010/011)

Drive `services/stt.py` directly with a fixture audio chunk iterator (real or mocked Deepgram):

**Expected**: clear audio yields final `TranscriptSegment`s matching the speech; a silent chunk
stream yields an empty/flagged result (no exception); a simulated provider outage raises a typed
`SttError` rather than hanging.

## Scenario 4 — TTS service in isolation (US3, FR-008/010/012)

Drive `services/tts.py` directly with sample text (real or mocked Deepgram Aura):

**Expected**: non-empty text yields streamed `SpeechChunk` audio that, saved to a file and played,
clearly speaks the text; empty text yields no audio with a reported outcome; a simulated outage
raises a typed `TtsError`.

## Scenario 5 — End-to-end call loop (integration, mocked media)

Run `tests/integration/test_call_pipeline.py`: a fake media source feeds audio → STT →
`run_turn` (agent reply) → TTS → outbound chunks, for two concurrent `call_id`s.

**Expected**: each call produces a spoken reply for its own transcript with **no cross-talk**
between sessions (FR-013); a provider failure in any stage produces an explicit, graceful outcome
(FR-009) — no hang, no silent drop.

## Scenario 6 — Attended log, auto-welcome & turn loop (US4, FR-015–021,023)

Run `tests/integration/test_call_session.py`: a fake attended call drives
`session.run_call_session(...)` with a mocked media bridge, STT, TTS, and `run_turn`.

**Expected**:
- A structured `call_attended` log line is emitted with the caller's number and `call_id` (FR-015).
- The welcome message is synthesized and played as turn 0 **before** any caller audio (FR-016);
  changing `WELCOME_MESSAGE` changes the spoken greeting (FR-022).
- Feeding caller speech produces streamed transcripts → an agent reply → spoken TTS, then the
  loop returns to listening; a 5-turn exchange completes with a `ConversationTurn` logged per turn
  (FR-017–020, 023, SC-011).
- Simulated hangup cancels the loop cleanly: no audio processed afterward, `Call.status == "ended"`
  (FR-021). A silent caller past `CALLER_SILENCE_TIMEOUT_S` re-prompts then ends gracefully.

## Automated checks

```bash
uv run pytest tests/contract/test_calling_webhook.py \
              tests/contract/test_stt_service.py \
              tests/contract/test_deepgram_tts_service.py \
              tests/contract/test_tts_service.py \
              tests/integration/test_call_pipeline.py \
              tests/integration/test_call_session.py \
              tests/unit/test_call_records.py
```

All pass with providers mocked at their module boundaries — no live API keys required for tests.
