"""Offsets between streams and the IMU time on the video timeline (TECH-05 sections 5 and 6).

Definitions (all in seconds):
  t_imu_s   = (timestamp_ns - first_timestamp_ns) / 1e9, first sample of the session
  t_video_s = t_imu_s + offset(run)

Uncalibrated (anchors): a phone-time event P has t_video = P - A_video0 and, inside run r,
t_imu = (P - A_imu_r) + (T_r - T0), so
  offset_r = (A_imu_r - A_video0) + (T0 - T_r)
where A_* are phone_elapsed_ns of the clock_anchor events, T_r is the first IMU timestamp of run r,
and video time zero is the first video anchor of the session. For run 0 (T_r = T0) this is
exactly TECH-05 section 5. For later runs it keeps the session-wide video timeline (segments are
placed by wall clock, so a pause is a real gap) and also holds if the IMU clock jumps across a
pause [A: spike S6].

Calibrated (tap test): the stored offset overrides the estimate. Later runs keep their anchor
differences relative to run 0: offset_r = anchor_r + (calibrated - anchor_0).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .data import event_detail
from .types import Offsets

log = logging.getLogger("glasses_reader")


def _resume_times(events: pd.DataFrame) -> np.ndarray:
    r = events.loc[events["event"] == "session_resumed", "phone_elapsed_ns"]
    return np.sort(r.to_numpy(dtype=np.int64))


def _anchors(events: pd.DataFrame) -> list[tuple[int, str, dict]]:
    out = []
    for _, row in events[events["event"] == "clock_anchor"].iterrows():
        d = event_detail(row["detail_json"])
        if d.get("stream") in ("video", "audio", "imu"):
            out.append((int(row["phone_elapsed_ns"]), d["stream"], d))
    return sorted(out, key=lambda a: a[0])


def resolve_offsets(meta: dict, events: pd.DataFrame, imu: pd.DataFrame) -> Offsets:
    resumes = _resume_times(events)
    n_runs = len(resumes) + 1
    cal = meta.get("calibration") or {}
    audio_s = (cal.get("audio_to_video_ms") or 0.0) / 1000.0
    anchors = _anchors(events)

    run_offsets: list[float] | None = None
    video0 = next((a for a in anchors if a[1] == "video"), None)
    if video0 is not None and len(imu):
        t0 = int(imu["timestamp_ns"].iloc[0])
        per_run: list[float | None] = [None] * n_runs
        for p, stream, d in anchors:
            if stream != "imu":
                continue
            r = int(np.searchsorted(resumes, p, side="right"))
            if per_run[r] is None:
                t_r = d.get("first_timestamp_ns", t0 if r == 0 else None)
                if t_r is None:
                    continue
                per_run[r] = (p - video0[0]) / 1e9 + (t0 - int(t_r)) / 1e9
        if per_run[0] is not None:
            for r in range(1, n_runs):
                if per_run[r] is None:
                    log.warning("run %d has no imu clock_anchor; using run 0 offset", r)
            run_offsets = [per_run[0] if o is None else o for o in per_run]

    cal_s = cal.get("imu_to_video_ms")
    if cal_s is not None:
        cal_s = cal_s / 1000.0
        base = run_offsets if run_offsets is not None else [0.0] * n_runs
        delta = cal_s - base[0]
        runs = tuple(o + delta for o in base)
        return Offsets(cal_s, audio_s, cal.get("spread_ms"), cal.get("method") or "tap_v1",
                       runs, tuple(int(x) for x in resumes))
    if run_offsets is not None:
        return Offsets(run_offsets[0], audio_s, None, "anchors", tuple(run_offsets),
                       tuple(int(x) for x in resumes))
    log.warning("no clock anchors and no calibration: IMU time is NOT aligned to video")
    return Offsets(0.0, audio_s, None, "none", (0.0,) * n_runs, tuple(int(x) for x in resumes))


def add_video_time(imu: pd.DataFrame, offsets: Offsets) -> pd.DataFrame:
    """Return a copy of ``imu`` with ``t_video_s`` (NaN everywhere if there are no samples)."""
    out = imu.copy()
    if out.empty:
        out["t_video_s"] = np.array([], dtype=np.float64)
        return out
    t_imu = (out["timestamp_ns"].to_numpy(dtype=np.int64) - int(out["timestamp_ns"].iloc[0])) / 1e9
    runs = np.searchsorted(np.array(offsets.run_starts_elapsed_ns, dtype=np.int64),
                           out["phone_elapsed_ns"].to_numpy(dtype=np.int64), side="right")
    table = np.array(offsets.imu_run_offsets_s or (offsets.imu_to_video_s,), dtype=np.float64)
    out["t_video_s"] = t_imu + table[np.minimum(runs, len(table) - 1)]
    return out
