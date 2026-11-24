"""Video timeline: frame timestamps, wall time and gap detection (TECH-05 section 3).

The timeline places segments by their UTC file-name start time, so a publisher reconnect
shows up as a real gap. ``pts_s = (seg_start - first_seg_start) / 1000 + (raw - first_raw)``.
"""
from __future__ import annotations

import logging
import shutil
import statistics
import subprocess
from pathlib import Path

import numpy as np

log = logging.getLogger("glasses_reader")

DEFAULT_FPS = 24.0


class FrameClock:
    """Stateful stamping of frames in decode order across segments."""

    def __init__(self, nominal_fps: float, first_segment_start_ms: int):
        self.fps = nominal_fps
        self.gap_threshold_s = 3.0 / nominal_fps
        self.frame_interval_s = 1.0 / nominal_fps
        self._t0_ms = first_segment_start_ms
        self._seg_start_ms = first_segment_start_ms
        self._seg_first_raw: float | None = None
        self._prev_pts_s: float | None = None
        self._segment = -1

    def new_segment(self, start_ms: int) -> None:
        self._segment += 1
        self._seg_start_ms = start_ms
        self._seg_first_raw = None

    def stamp(self, raw_pts_s: float) -> tuple[float, int, float]:
        """Returns (pts_s on the session timeline, wall_ms, gap_before_s)."""
        if self._seg_first_raw is None:
            self._seg_first_raw = raw_pts_s
        rel = raw_pts_s - self._seg_first_raw
        pts_s = (self._seg_start_ms - self._t0_ms) / 1000.0 + rel
        wall_ms = self._seg_start_ms + round(rel * 1000)
        gap = 0.0
        if self._prev_pts_s is not None:
            dt = pts_s - self._prev_pts_s
            if dt <= 0:
                log.warning("segment %d: pts_s %.3f not after previous %.3f (overlap)",
                            self._segment, pts_s, self._prev_pts_s)
            elif dt > self.gap_threshold_s:
                gap = dt - self.frame_interval_s
        self._prev_pts_s = pts_s
        return pts_s, wall_ms, gap


# ---- timestamps without decoding --------------------------------------------------------
def read_video_pts(path: str | Path) -> np.ndarray:
    """Presentation times (seconds, ascending) of every video packet, without decoding.

    Uses PyAV when installed, else ``ffprobe`` (the field is ``pts_time``, or ``pkt_pts_time``
    on some versions). A truncated final segment returns what could be read.
    """
    try:
        import av
    except ImportError:
        return _pts_ffprobe(path)
    vals: list[float] = []
    try:
        with av.open(str(path)) as c:
            if not c.streams.video:
                return np.array([])
            s = c.streams.video[0]
            for p in c.demux(s):
                if p.pts is not None:
                    vals.append(float(p.pts * s.time_base))
    except av.error.FFmpegError as exc:
        log.warning("%s: truncated or unreadable after %d packets (%s)", path, len(vals), exc)
    return np.sort(np.array(vals, dtype=np.float64))


def _pts_ffprobe(path: str | Path) -> np.ndarray:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("need PyAV (pip install av) or ffprobe to read segment timestamps")
    for field in ("pts_time", "pkt_pts_time"):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             f"frame={field}", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=False).stdout
        vals = [float(x) for x in out.split() if x.replace(".", "", 1).replace("-", "", 1).isdigit()]
        if vals:
            return np.sort(np.array(vals))
    return np.array([])


def measured_fps(pts: np.ndarray) -> float | None:
    if len(pts) < 3:
        return None
    dt = float(statistics.median(np.diff(pts)))
    return 1.0 / dt if dt > 0 else None


def nominal_fps(meta: dict, segments: list[Path]) -> float:
    """``settings_applied`` frame rate if recorded, else the median measured rate, else 24."""
    fr = (meta.get("settings") or {}).get("frame_rate")
    if fr:
        return float(fr)
    for seg in segments:
        m = measured_fps(read_video_pts(seg))
        if m:
            return m
    return DEFAULT_FPS
