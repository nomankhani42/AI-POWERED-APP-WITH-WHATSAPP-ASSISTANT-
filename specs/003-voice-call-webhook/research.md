# Phase 0 Research: Meta Voice Call Webhook & Speech Services

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

All decisions below were verified against current documentation (Context7: Deepgram
Python SDK `/deepgram/deepgram-python-sdk`, Cartesia Python `/cartesia-ai/cartesia-python`)
and the `meta-whatsapp-api` skill, per constitution Principle VI.

## 1. Meta WhatsApp Business Calling — webhook & call control

- **Decision**: Reuse the standard Graph webhook pattern for both verification and events.
  `GET /webhooks/whatsapp/calls` answers the `hub.mode`/`hub.verify_token`/`hub.challenge`
  handshake (echo the challenge only when the verify token matches). `POST` receives call
  lifecycle events (`connect` with the caller's SDP offer, `terminate`, and status changes)
  under `entry[].changes[].value.calls[]`. The handler **always returns HTTP 200**, catching
  every exception internally, and validates the `X-Hub-Signature-256` HMAC-SHA256 header
  against the app secret before acting (FR-003).
- **Call actions** run against `POST graph.facebook.com/<version>/<phone_id>/calls` with
  actions `pre_accept`, `accept` (returns our SDP answer), `reject`, `terminate` — invoked
  from `services/meta_calling.py` via `httpx.AsyncClient`.
- **Rationale**: WhatsApp Business Calling shares the same webhook + Graph infrastructure the
  project already uses for messaging; the always-200 + signature-verify rules are hard
  requirements (skill hard-rules #1, #4). Keeps transport isolated per Principle IV.
- **Alternatives considered**: A generic SIP/PSTN provider (Twilio) — rejected: constitution
  fixes Meta API as call transport. Skipping signature verification — rejected: FR-003.

## 2. Live call media transport (audio in/out)

- **Decision**: Terminate call media in-process with `aiortc`. The caller's SDP offer (from the
  `connect` event) is answered with an `aiortc` `RTCPeerConnection`; the inbound Opus audio
  track is decoded/resampled to PCM (linear16, 16 kHz) and streamed to Deepgram; Cartesia's
  synthesized PCM is encoded to an outbound Opus track. Media orchestration lives in
  `services/media/webrtc.py`; the per-call STT→agent→TTS loop lives in `services/media/session.py`.
- **Rationale**: The Graph API conveys only call *control* + SDP; real-time audio is Opus RTP
  over WebRTC. `aiortc` is the standard async Python WebRTC/RTP stack and keeps media in-process
  for lowest latency (SC-005). Documented as a justified dependency in plan Complexity Tracking.
- **Alternatives considered**: External media server / SFU — rejected: extra service + latency
  hop, violates YAGNI. Pure httpx — rejected: cannot carry RTP media.

## 3. Speech-to-text — Deepgram streaming (chunked)

- **Decision**: Use `AsyncDeepgramClient.listen.v2.connect(model=..., encoding="linear16",
  sample_rate=16000)` as an async context manager. Register handlers via
  `connection.on(EventType.MESSAGE, ...)`, call `await connection.start_listening()`, push audio
  frames with `await connection.send_media(chunk_bytes)` as they arrive from the media bridge,
  and use `send_finalize()` / `send_close_stream()` to flush and close a turn. End-of-turn is
  driven by Deepgram's turn/endpointing events (`eot_threshold`, turn-info messages) so the agent
  is invoked once the caller finishes speaking. Wrapped behind a stable `stt.transcribe_stream()`
  interface in `services/stt.py`.
- **Rationale**: Chunked streaming with server-side endpointing is exactly the "chunking STT"
  the user asked for and gives interim + finalized transcripts at conversational latency
  (SC-005/SC-006). Async client keeps Principle II.
- **Alternatives considered**: Batch `transcribe_file` — rejected: adds whole-utterance latency,
  no live interim results. Client-side VAD only — rejected: Deepgram endpointing is more robust.
- **Failure handling (FR-009/FR-011)**: connection errors and timeouts surface a typed failure so
  the call degrades gracefully; silent/empty audio yields an empty (not error) transcript.

## 4. Text-to-speech — Cartesia Sonic streaming (real voice)

- **Decision**: Use the async Cartesia client's TTS websocket (`tts.websocket_connect()` →
  `ws.context(model_id="sonic-3.5", voice={"mode":"id","id":<voice_uuid>}, output_format={
  "container":"raw","encoding":"pcm_s16le"|"pcm_f32le","sample_rate":...}, language="en")`).
  Push the agent's reply text incrementally with `ctx.push(text)` and `ctx.no_more_inputs()`, and
  stream audio out by iterating `ctx.receive()` for `event.type == "chunk"` / `event.audio`,
  forwarding each chunk straight to the outbound media track. Wrapped behind `tts.synthesize_stream()`
  in `services/tts.py`. Voice id + model are configurable (`cartesia_voice_id`, `cartesia_model`).
- **Rationale**: Sonic's streaming websocket yields the low-latency, natural ("feel real") voice
  the user asked for and lets playback begin before the full reply text exists. Output format is
  chosen to match the outbound Opus/RTP encoder (PCM → Opus) in the media bridge.
- **Alternatives considered**: One-shot `tts.generate()` (bytes) — kept only as a fallback/test
  path; rejected for live calls because it waits for the whole reply before any audio plays.
- **Failure handling (FR-009/FR-012)**: provider/websocket errors surface a typed failure for
  graceful fallback; empty input returns no audio with a reported outcome (never invalid audio).

## 4a. Call-attended logging, auto welcome & the conversation loop (US4)

- **Decision**: The per-call orchestrator (`services/media/session.py`) owns User Story 4:
  1. **Attended log (FR-015)** — when media is established (call reaches `connected`), emit one
     structured log record `call attended` with `call_id` and the caller's number
     (`wa_call_from`) via the app logger. Never logs tokens/secrets (Principle V).
  2. **Auto welcome (FR-016/FR-022)** — immediately synthesize a configurable greeting
     (`settings.welcome_message`) through `tts.synthesize_stream()` and play it to the caller
     before any listening begins. This is the loop's opening turn.
  3. **Loop (FR-017–FR-021)** — then repeat: open a Deepgram stream, forward inbound audio chunks
     in real time, let Deepgram endpointing signal end-of-turn, pass the final transcript to
     `run_turn` (gpt-4.1), stream the reply back through Cartesia to the caller, and return to
     listening. Each turn logs `{call_id, turn, transcript, reply}` for observability (FR-023).
  4. **Teardown (FR-021)** — on `terminate`/hangup, cancel the loop task, close STT/TTS streams,
     and finalize the `Call` record.
- **Rationale**: Keeps orchestration in one cohesive module (Principle I) while reusing the
  already-designed STT/TTS/agent stages unchanged. The welcome-as-opening-turn model means one
  code path drives both the greeting and every subsequent reply. Half-duplex turn-taking (listen
  only after finishing playback) matches the spec's documented assumption and avoids barge-in
  complexity in v1.
- **Edge handling**: silent caller after welcome → configurable inactivity timeout re-prompts or
  ends gracefully; empty/errored agent output → a fallback spoken prompt keeps the loop alive;
  overlap (caller talks during playback) → handled per the half-duplex assumption without losing
  the next-turn capture.
- **Alternatives considered**: Playing the welcome only after first caller speech — rejected:
  spec requires an automatic greeting on connect (FR-016). A separate `logging`-only module for
  attended/turn events — rejected as premature; structured logs from `session.py` satisfy FR-015/023
  (YAGNI). Persisting each turn as a Mongo document — rejected: durable conversation content already
  lives in the agent session; the spec asks only that turns be recorded/logged (SHOULD).

## 5. Memory layering for calls

- **Decision**: Redis (`redis.asyncio`, existing `redis_url`) stores in-flight per-call state and
  event idempotency keys (`call:event:<event_id>` with TTL); MongoDB (Beanie) stores durable
  `Call` and `CallEvent` records. Conversation turn context continues to use the existing
  `RedisSession` keyed by the call/conversation id, so `run_turn` is reused unchanged.
- **Rationale**: Principle III — volatile call state in Redis, durable history in Mongo, each via
  its own module. Idempotency (FR-006) is a natural fit for a Redis SET-NX with TTL, backed by the
  unique `event_id` index on `CallEvent` for durable dedupe.
- **Alternatives considered**: Mongo-only idempotency — rejected for hot-path latency. Redis-only
  call records — rejected: must survive restart (Principle III).

## 6. Configuration additions (Principle V)

- **Decision**: Extend the typed `Settings` with required (no-default, fail-fast) secrets —
  `deepgram_api_key`, `cartesia_api_key`, `whatsapp_token`, `whatsapp_phone_id`,
  `whatsapp_verify_token`, `whatsapp_app_secret` — and defaulted tunables — `cartesia_model`
  (`"sonic-3.5"`), `cartesia_voice_id`, `deepgram_model`, `stt_sample_rate` (16000),
  `graph_api_version`, `welcome_message` (the auto greeting text, FR-022), and
  `caller_silence_timeout_s` (inactivity re-prompt/hangup guard). Documented in `.env.example`
  in the same change.
- **Rationale**: Centralized, validated, env-based config; startup fails fast on any missing secret.

## Open items

None blocking. Exact Meta Graph API version string and the specific SDP/codec parameters
required by WhatsApp Business Calling are confirmed at implementation time against the live
Graph docs; the module boundaries above isolate that detail to `meta_calling.py` /
`media/webrtc.py`.
