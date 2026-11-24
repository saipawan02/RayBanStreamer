"""open_session and IMU access (TECH-05 sections 1 and 5)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .data import read_session_files
from .offsets import add_video_time, resolve_offsets
from .types import Session

log = logging.getLogger("glasses_reader")


def open_session(session_id: str, data_root: str = "/srv/glasses") -> Session:
    meta, imu, events, segments = read_session_files(session_id, data_root)
    offsets = resolve_offsets(meta, events, imu)
    if not segments:
        log.warning("session %s: no recording segments found", session_id)
    if imu.empty:
        log.warning("session %s: no IMU rows", session_id)
    return Session(session_id=session_id, meta=meta, imu=add_video_time(imu, offsets),
                   events=events, segments=segments, offsets=offsets, data_root=Path(data_root))


def imu_window(s: Session, t0_s: float, t1_s: float) -> pd.DataFrame:
    """IMU rows with ``t0_s <= t_video_s < t1_s``, ordered by ``t_video_s``."""
    m = (s.imu["t_video_s"] >= t0_s) & (s.imu["t_video_s"] < t1_s)
    return s.imu[m].sort_values("t_video_s", kind="stable").reset_index(drop=True)
