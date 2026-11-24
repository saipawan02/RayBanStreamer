"""Audio on the video timeline (TECH-05 section 4)."""
from __future__ import annotations

import logging

import numpy as np

from .data import segment_start_ms
from .timeline import read_video_pts
from .types import Session

log = logging.getLogger("glasses_reader")


def _decode_segment(path, sample_rate: int) -> list[tuple[float, np.ndarray]]:
    """[(raw start time in the segment's clock, mono float32 samples)] per decoded chunk."""
    import av

    chunks: list[tuple[float, np.ndarray]] = []
    try:
        with av.open(str(path)) as c:
            if not c.streams.audio:
                return chunks
            a = c.streams.audio[0]
            rs = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
            end_s: float | None = None
            frames = (f for fr in c.decode(a) for f in rs.resample(fr))
            for f in frames:
                chunks.append(_chunk(f, sample_rate, end_s))
                end_s = chunks[-1][0] + len(chunks[-1][1]) / sample_rate
            for f in rs.resample(None):
                chunks.append(_chunk(f, sample_rate, end_s))
    except av.error.FFmpegError as exc:
        log.warning("%s: audio decode stopped early (%s)", path, exc)
    return chunks


def _chunk(frame, sample_rate: int, expected_start: float | None) -> tuple[float, np.ndarray]:
    start = float(frame.pts * frame.time_base) if frame.pts is not None else (expected_start or 0.0)
    return start, frame.to_ndarray()[0].astype(np.float32, copy=False)


def load_audio_raw(s: Session, sample_rate: int = 16000,
                   apply_offset: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Mono float32 samples and the video-timeline time of every sample.

    Gaps are never filled: a gap is a jump in ``t`` (see ``find_gaps``). Sessions without audio
    return two empty arrays. ``apply_offset=False`` gives the pre-calibration times that
    ``estimate_offset`` needs.
    """
    if not s.segments:
        return np.array([], np.float32), np.array([], np.float64)
    starts = [segment_start_ms(p) for p in s.segments]
    t0_ms = starts[0]
    off = s.offsets.audio_to_video_s if apply_offset else 0.0
    xs: list[np.ndarray] = []
    ts: list[np.ndarray] = []
    for path, start_ms in zip(s.segments, starts, strict=True):
        chunks = _decode_segment(path, sample_rate)
        if not chunks:
            continue
        vpts = read_video_pts(path)
        # Audio and video share the container clock inside a segment (spike S3 [A]).
        ref = float(vpts[0]) if len(vpts) else chunks[0][0]
        base = (start_ms - t0_ms) / 1000.0 - ref + off
        for raw_start, x in chunks:
            xs.append(x)
            ts.append(base + raw_start + np.arange(len(x)) / sample_rate)
    if not xs:
        return np.array([], np.float32), np.array([], np.float64)
    return np.concatenate(xs), np.concatenate(ts)


def load_audio(s: Session, sample_rate: int = 16000) -> tuple[np.ndarray, np.ndarray]:
    """(samples float32 mono, t_video_s per sample). Gaps appear as jumps in ``t``."""
    return load_audio_raw(s, sample_rate, apply_offset=True)


def find_gaps(t: np.ndarray, sample_rate: int, min_gap_s: float = 0.05) -> list[tuple[float, float]]:
    """(start_s, end_s) intervals where consecutive samples are further apart than ``min_gap_s``."""
    if len(t) < 2:
        return []
    dt = np.diff(t)
    idx = np.nonzero(dt > max(min_gap_s, 2.0 / sample_rate))[0]
    return [(float(t[i] + 1.0 / sample_rate), float(t[i + 1])) for i in idx]
