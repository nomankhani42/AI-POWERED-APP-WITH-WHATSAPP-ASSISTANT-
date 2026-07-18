"""Unit tests for the outbound audio framing fix (T006, US1).

Proves the fix for the garbled "old TV / toun toush" playback (research.md §1): the
``_OutboundTrack`` now emits **only** fixed 20 ms / 48 kHz frames with monotonic ``pts`` and
reconstructs the pushed PCM losslessly, instead of one arbitrary-length frame per Cartesia
chunk. No aiortc peer / network is involved — the track is exercised directly.
"""

from __future__ import annotations

import asyncio
import time

import av
import pytest

from app.services.media.webrtc import (
    _OutboundTrack,
    _PLAYOUT_DRAIN_BUFFER_S,
    _PLAYOUT_PREROLL_S,
    _packed_mono_pcm,
)

_RATE = 48000
_FRAME_BYTES = _RATE * 20 // 1000 * 2  # 1920 bytes = 960 samples s16 mono


def _pattern(n: int) -> bytes:
    """Deterministic, non-repeating-per-byte PCM so slicing errors are visible."""

    return bytes((i * 7) % 256 for i in range(n))


def _drain_frames(track: _OutboundTrack) -> list[bytes]:
    frames: list[bytes] = []
    while not track._frames.empty():
        frames.append(track._frames.get_nowait())
    return frames


def test_packed_mono_pcm_strips_alignment_padding() -> None:
    frame = av.AudioFrame(format="s16", layout="mono", samples=10)
    frame.planes[0].update(b"\x01\x02" * 10)

    pcm = _packed_mono_pcm(frame)

    assert pcm == b"\x01\x02" * 10
    assert len(bytes(frame.planes[0])) >= len(pcm)


def test_push_slices_only_full_20ms_frames() -> None:
    """Every frame handed to the encoder is exactly one 20 ms frame; remainder is retained."""

    track = _OutboundTrack(_RATE)
    track.push(_pattern(3000), _RATE)  # 1 full frame (1920) + 1080 remainder

    frames = _drain_frames(track)
    assert len(frames) == 1
    assert all(len(f) == _FRAME_BYTES for f in frames)
    assert len(track._buffer) == 3000 - _FRAME_BYTES  # remainder carried for next push

    track.push(_pattern(1000), _RATE)  # remainder 1080 + 1000 = 2080 -> 1 more frame
    frames += _drain_frames(track)
    assert len(frames) == 2
    assert all(len(f) == _FRAME_BYTES for f in frames)


def test_flush_tail_zero_pads_final_partial_frame() -> None:
    """End-of-reply remainder is emitted once, zero-padded to a full 20 ms frame."""

    track = _OutboundTrack(_RATE)
    data = _pattern(4000)  # 2 full frames (3840) + 160 remainder
    track.push(data, _RATE)
    track.flush_tail()

    frames = _drain_frames(track)
    assert len(frames) == 3
    assert all(len(f) == _FRAME_BYTES for f in frames)

    # Lossless: the pushed bytes are the prefix of the concatenated frames; the tail is padded.
    joined = b"".join(frames)
    assert joined[:4000] == data
    assert joined[4000:] == b"\x00" * (len(joined) - 4000)


def test_flush_discards_pending_playback_for_barge_in() -> None:
    """Barge-in flush drops queued frames and buffered remainder immediately."""

    track = _OutboundTrack(_RATE)
    track.push(_pattern(5000), _RATE)  # queues 2 frames + remainder
    assert not track._frames.empty()

    track.flush()

    assert track._frames.empty()
    assert len(track._buffer) == 0


@pytest.mark.asyncio
async def test_recv_realigns_clock_on_first_frame_after_idle_without_resetting_pts() -> None:
    """After silence the first frame re-anchors the clock (no burst) and pts stays monotonic."""

    track = _OutboundTrack(_RATE)
    # A prior reply advanced pts and left a stale playout clock far in the past.
    track._timestamp = _RATE * 3
    track._start = time.monotonic() - 60.0
    before_pts = track._timestamp

    track.begin_playback()  # marks the clock to re-anchor on the next frame, not now
    track.push(_pattern(_FRAME_BYTES), _RATE)

    started = time.monotonic()
    frame = await track.recv()
    elapsed = time.monotonic() - started

    # pts must not reset (monotonic RTP timestamps) ...
    assert frame.pts == before_pts
    # ... and the frame must not burst out against the 60 s-stale clock: it waits ~one pre-roll,
    # re-anchored to now rather than catching up 60 s of backlog.
    assert elapsed < _PLAYOUT_PREROLL_S + 0.1
    assert abs((track._start + (before_pts / _RATE)) - time.monotonic()) < 0.1


@pytest.mark.asyncio
async def test_recv_reanchors_on_underrun_instead_of_bursting() -> None:
    """A mid-reply synthesis stall (the frame queue drained past the pre-roll cushion) must
    re-anchor the playout clock, not burst a catch-up backlog into the encoder — bursting is the
    warbly 'old TV' distortion. The frame plays now and the clock tracks real time again."""

    track = _OutboundTrack(_RATE)
    track.push(_pattern(_FRAME_BYTES), _RATE)
    # Reply already in progress; a long stall left the clock 5 s behind real time (pts at 1 s).
    track._timestamp = _RATE
    track._start = time.monotonic() - 5.0

    started = time.monotonic()
    frame = await track.recv()
    elapsed = time.monotonic() - started

    assert frame.pts == _RATE  # pts stays monotonic — not reset
    assert elapsed < 0.05  # did not sleep; but must not have bursted a 5 s backlog either
    # Re-anchored: the clock now maps the current pts to ~now, so the next frame paces normally
    # from here instead of firing out a burst.
    assert abs((track._start + (track._timestamp / _RATE)) - time.monotonic()) < 0.05


@pytest.mark.asyncio
async def test_recv_emits_48khz_frames_with_monotonic_pts() -> None:
    """``recv()`` returns 960-sample / 48 kHz frames with strictly increasing pts, lossless."""

    track = _OutboundTrack(_RATE)
    data = _pattern(_FRAME_BYTES * 2)
    track.push(data, _RATE)

    frame0 = await track.recv()
    frame1 = await track.recv()

    for frame in (frame0, frame1):
        assert frame.sample_rate == _RATE
        assert frame.samples == 960
    assert frame0.pts == 0
    assert frame1.pts == 960  # monotonic, advanced by exactly one frame

    reconstructed = bytes(frame0.planes[0]) + bytes(frame1.planes[0])
    assert reconstructed == data


@pytest.mark.asyncio
async def test_drain_waits_until_queued_frames_are_sent() -> None:
    """Playback completion waits for aiortc to pull queued audio, not just enqueue it."""

    track = _OutboundTrack(_RATE)
    track.push(_pattern(_FRAME_BYTES), _RATE)

    drain_task = asyncio.create_task(track.drain())
    await asyncio.sleep(0)
    assert drain_task.done() is False

    await track.recv()
    await asyncio.sleep(0)
    assert drain_task.done() is False
    await asyncio.wait_for(
        drain_task, timeout=_PLAYOUT_DRAIN_BUFFER_S + _PLAYOUT_PREROLL_S + 0.2
    )


@pytest.mark.asyncio
async def test_flush_unblocks_pending_drain() -> None:
    """Barge-in discards queued frames and marks them done so playback tasks can exit."""

    track = _OutboundTrack(_RATE)
    track.push(_pattern(_FRAME_BYTES * 2), _RATE)

    drain_task = asyncio.create_task(track.drain())
    await asyncio.sleep(0)
    assert drain_task.done() is False

    track.flush()
    await asyncio.wait_for(drain_task, timeout=0.1)
