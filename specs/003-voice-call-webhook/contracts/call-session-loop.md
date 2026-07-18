# Contract: Call Session Loop (attended log, welcome, turn-taking)

Module: `src/app/services/media/session.py`. Started by the webhook when a call is accepted
(`connect` → `meta_calling.accept`) and driven over the `media/webrtc.py` audio bridge. Covers
User Story 4 (FR-015–FR-023). Reuses `stt.py`, `tts.py`, and the Agents-SDK `run_turn` (gpt-4.1)
unchanged.

## Entry point

`async def run_call_session(call_id: str, wa_call_from: str, media: MediaBridge) -> None`

Runs for the lifetime of one call. Cancelled on `terminate`/hangup.

## Sequence

1. **On attended** (media established / call `connected`):
   - Emit structured log `call_attended` `{call_id, from: wa_call_from, at: <utc>}` (FR-015).
     Never includes tokens/secrets.
   - Set `Call.connected_at`, status → `connected`.

2. **Welcome (opening turn, `turn = 0`)** (FR-016/FR-022):
   - `await tts.synthesize_stream(settings.welcome_message)` → forward `SpeechChunk`s to the
     outbound track. Playback begins effectively immediately on connect (SC-010).
   - Log `ConversationTurn` `{call_id, turn: 0, transcript: "", reply: welcome_message}`.

3. **Loop** for `turn = 1, 2, …` until the call ends (FR-017–FR-020):
   1. Open `stt.transcribe_stream(...)`; forward inbound audio chunks in real time (FR-017).
   2. Deepgram endpointing signals end-of-turn → collect final transcript (FR-018).
      - If inactivity exceeds `settings.caller_silence_timeout_s`, re-prompt once then, if still
        silent, terminate gracefully (edge: silent caller).
   3. `reply = await run_turn(conversation_id, transcript)` (gpt-4.1) (FR-019).
      - Empty/errored reply → speak a fixed fallback prompt; loop continues (edge: no reply).
   4. `await tts.synthesize_stream(reply)` → outbound track; then return to listening (FR-020).
   5. Log `ConversationTurn` `{call_id, turn, transcript, reply}` (FR-023).

4. **Teardown** (FR-021): on `terminate`/hangup, cancel the loop task, close STT/TTS streams and
   the media bridge, set `Call.status = ended` + `ended_at`. No audio processed after this point.

## Invariants

- Half-duplex turn-taking: the session listens only after finishing playback (documented
  assumption); caller overlap during playback does not lose the next turn's capture.
- Fully isolated per `call_id` — no shared mutable state across concurrent calls (FR-013).
- Every stage failure (STT/agent/TTS) is caught and produces a graceful spoken/loop outcome —
  never a hang or silent drop (FR-009).

## Configuration used

`welcome_message` (FR-022), `caller_silence_timeout_s`, `deepgram_tts_model`,
`deepgram_tts_speed`, `tts_first_audio_timeout_s`, `tts_event_timeout_s`, `deepgram_model`,
`stt_sample_rate`. Cartesia model/voice settings remain rollback-only. See research.md §6.
