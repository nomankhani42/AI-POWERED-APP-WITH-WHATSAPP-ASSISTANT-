# Contract: Media Bridge (outbound framing + barge-in)

**Module**: `src/app/services/media/webrtc.py` (`MediaBridge`, `_OutboundTrack`)
**Consumers**: `src/app/services/media/session.py`
**Related**: FR-001, FR-002, FR-009, FR-013; research.md §1, §2

The ONLY module that touches `aiortc`/`av`. This contract adds fixed-frame outbound audio and
a playback-stop capability; the SDP answer / inbound PCM behavior from 003 is unchanged.

## Outbound framing (the core distortion fix)

- **Input**: `play(chunks: AsyncIterator[SpeechChunk])` — each `SpeechChunk.audio` is
  `pcm_s16le` mono at `SpeechChunk.sample_rate` (now 48000).
- **Behavior**: The bridge MUST buffer incoming PCM and emit to the encoder **only** fixed
  frames of exactly **20 ms** = `sample_rate * 0.02` samples (960 @ 48 kHz; 1920 bytes s16),
  mono, with:
  - a persistent `av.AudioResampler(format="s16", layout="mono", rate=48000)` applied once so
    the wire rate always matches the negotiated Opus codec;
  - monotonic `pts` accumulated in output-rate sample units and `time_base = 1/rate`;
  - real-time pacing (sleep until wall-clock reaches the cumulative timestamp).
- **Leftover** PCM shorter than one 20 ms frame is retained in the buffer for the next chunk;
  at end of a reply a final partial frame MAY be zero-padded to 20 ms.
- **MUST NOT**: hand the encoder a frame whose sample count ≠ the 20 ms frame size, or a frame
  whose `sample_rate` ≠ 48000. (This is the exact defect being fixed.)

**Acceptance**: With a mocked/loopback encoder, every frame delivered to the encoder has
identical sample count and `sample_rate == 48000`; `pts` is strictly increasing by the frame
size; reconstructed audio matches the input PCM (no dropped/duplicated samples). Satisfies
SC-001, SC-002.

## Playback stop (barge-in)

- **New**: `stop_playback()` (a.k.a. `_OutboundTrack.flush()`) — clears the pending frame
  queue and the leftover buffer, resetting playback so no already-queued audio continues.
- **Guarantee**: after `stop_playback()` returns, at most one in-flight 20 ms frame may still
  be emitted (≤ ~20 ms of tail); the queue is empty. Well under the 500 ms budget (SC-008).
- **Idempotent**: safe to call when nothing is playing.
- The track stays `live` after a stop (the caller's next turn reuses it).

**Acceptance**: While `play()` is draining a long reply, calling `stop_playback()` empties the
queue; a subsequent `play()` of a new reply starts cleanly with monotonic `pts` continuing.

## Unchanged from 003
`answer(offer_sdp)`, `inbound_pcm()` (yields linear16 PCM at `stt_sample_rate`), `close()`.
