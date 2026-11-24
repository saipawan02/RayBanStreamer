"""One-line session loader for analytics code (TECH-04 section 8)."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from .writer import EVENTS_SCHEMA, IMU_SCHEMA

_SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6})\.mp4$")
_MARGIN_MS = 60_000


def _read(base: Path, name: str, schema, sort_by: list[str]) -> pd.DataFrame:
    single = base / f"{name}.parquet"
    src = single if single.exists() else base / name
    if not src.exists():
        return schema.empty_table().to_pandas()
    table = ds.dataset(str(src), format="parquet", schema=schema).to_table()
    return table.to_pandas().sort_values(sort_by, kind="stable").reset_index(drop=True)


def segment_start_ms(path: Path) -> int | None:
    """Start time from a MediaMTX file name, interpreted as UTC (server must run in UTC)."""
    m = _SEGMENT_RE.match(path.name)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S-%f").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def find_segments(meta: dict, data_root: str | Path = "/srv/glasses") -> list[Path]:
    """Recording segments overlapping the session (+/- 60 s), in time order.

    A segment's end is approximated by its modification time. An open session
    (``ended_recv_wall_ms`` null) extends to now.
    """
    rec = Path(data_root) / "recordings" / "glasses"
    if not rec.is_dir():
        return []
    lo = (meta.get("started_recv_wall_ms") or 0) - _MARGIN_MS
    end = meta.get("ended_recv_wall_ms")
    hi = float("inf") if end is None else end + _MARGIN_MS
    out: list[tuple[int, Path]] = []
    for p in rec.glob("*.mp4"):
        start = segment_start_ms(p)
        if start is None:
            continue
        seg_end = max(int(p.stat().st_mtime * 1000), start)
        if start <= hi and seg_end >= lo:
            out.append((start, p))
    return [p for _, p in sorted(out)]


def load_session(session_id: str, data_root: str | Path = "/srv/glasses") -> dict:
    base = Path(data_root) / "data" / session_id
    meta = json.loads((base / "session.json").read_text())
    return {
        "meta": meta,
        "imu": _read(base, "imu", IMU_SCHEMA, ["seq", "timestamp_ns"]),
        "events": _read(base, "events", EVENTS_SCHEMA, ["seq"]),
        "segments": find_segments(meta, data_root),
    }
