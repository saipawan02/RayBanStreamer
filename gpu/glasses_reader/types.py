"""Public data types (TECH-05 section 1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Offsets:
    """Values to ADD to a stream's own time to land on the video timeline (TECH-05 section 5).

    ``imu_run_offsets_s`` holds one offset per run (continuous span between pauses), indexed by
    run number; ``imu_to_video_s`` is run 0's. ``source`` is ``tap_v1``, ``anchors`` or ``none``.
    """

    imu_to_video_s: float = 0.0
    audio_to_video_s: float = 0.0
    spread_ms: float | None = None
    source: str = "none"
    imu_run_offsets_s: tuple[float, ...] = ()
    run_starts_elapsed_ns: tuple[int, ...] = ()   # phone_elapsed_ns of each session_resumed


@dataclass
class Frame:
    tensor: Any          # torch.Tensor uint8 (H, W, 3) RGB on `device`
    pts_s: float         # seconds on the video timeline (0 = first frame of the session)
    wall_ms: int         # estimated UTC capture time, ms (accurate to BT + mux + SRT delay only)
    session_id: str
    gap_before_s: float  # 0.0 if contiguous, else seconds of missing video before this frame
    width: int
    height: int


@dataclass
class TimedFrame:
    """A decoded frame on the CPU with its timeline stamps; ``iter_frames`` uploads it."""

    image: Any           # numpy uint8 (H, W, 3) RGB
    pts_s: float
    wall_ms: int
    gap_before_s: float
    segment: int         # index into Session.segments

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


@dataclass
class Session:
    session_id: str
    meta: dict
    imu: pd.DataFrame        # TECH-01 6.2 columns plus `t_video_s`
    events: pd.DataFrame
    segments: list[Path]     # recording files in time order
    offsets: Offsets = field(default_factory=Offsets)
    data_root: Path = Path("/srv/glasses")
