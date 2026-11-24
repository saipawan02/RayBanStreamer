"""Read a session from disk: session.json, imu, events, recording segments.

Standalone on purpose (the readers must not import the telemetry service), but it follows the
same rules as server/telemetry/app/loader.py: ``imu.parquet`` is preferred over ``imu/`` parts,
and segments are matched by UTC file-name time (TECH-04 section 8).
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

_SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6})\.mp4$")
_MARGIN_MS = 60_000

IMU_COLUMNS = ["seq", "timestamp_ns", "recv_wall_ms", "phone_wall_ms", "phone_elapsed_ns", "source",
               "ax", "ay", "az", "gx", "gy", "gz", "mx", "my", "mz", "qw", "qx", "qy", "qz"]
EVENT_COLUMNS = ["seq", "recv_wall_ms", "phone_wall_ms", "phone_elapsed_ns", "event", "detail_json"]

IMU_SCHEMA = pa.schema(
    [("seq", pa.int64()), ("timestamp_ns", pa.int64()), ("recv_wall_ms", pa.int64()),
     ("phone_wall_ms", pa.int64()), ("phone_elapsed_ns", pa.int64()), ("source", pa.string()),
     *[(c, pa.float64()) for c in IMU_COLUMNS[6:]]]
)
EVENTS_SCHEMA = pa.schema(
    [("seq", pa.int64()), ("recv_wall_ms", pa.int64()), ("phone_wall_ms", pa.int64()),
     ("phone_elapsed_ns", pa.int64()), ("event", pa.string()), ("detail_json", pa.string())]
)


def segment_start_ms(path: Path) -> int | None:
    m = _SEGMENT_RE.match(Path(path).name)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S-%f").replace(tzinfo=UTC)
    return round(dt.timestamp() * 1000)


def find_segments(meta: dict, data_root: str | Path) -> list[Path]:
    """Segments overlapping the session window (+/- 60 s), in time order.

    A segment's end is approximated by its mtime; an open session extends to now.
    """
    rec = Path(data_root) / "recordings" / "glasses"
    if not rec.is_dir():
        return []
    lo = (meta.get("started_recv_wall_ms") or 0) - _MARGIN_MS
    end = meta.get("ended_recv_wall_ms")
    hi = float("inf") if end is None else end + _MARGIN_MS
    found: list[tuple[int, Path]] = []
    for p in rec.glob("*.mp4"):
        start = segment_start_ms(p)
        if start is None:
            continue
        seg_end = max(round(p.stat().st_mtime * 1000), start)
        if start <= hi and seg_end >= lo:
            found.append((start, p))
    return [p for _, p in sorted(found)]


def _read_table(base: Path, name: str, schema: pa.Schema, sort_by: list[str]) -> pd.DataFrame:
    single = base / f"{name}.parquet"
    src = single if single.exists() else base / name
    if not src.exists():
        return schema.empty_table().to_pandas()
    df = ds.dataset(str(src), format="parquet", schema=schema).to_table().to_pandas()
    return df.sort_values(sort_by, kind="stable").reset_index(drop=True)


def read_session_files(session_id: str, data_root: str | Path):
    """Returns (meta, imu, events, segments). Raises FileNotFoundError if the session is unknown."""
    base = Path(data_root) / "data" / session_id
    meta_path = base / "session.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"no session.json for {session_id} under {base}")
    meta = json.loads(meta_path.read_text())
    imu = _read_table(base, "imu", IMU_SCHEMA, ["seq", "timestamp_ns"])
    events = _read_table(base, "events", EVENTS_SCHEMA, ["seq"])
    return meta, imu, events, find_segments(meta, data_root)


def event_detail(row_detail_json: str | None) -> dict:
    if not row_detail_json:
        return {}
    try:
        d = json.loads(row_detail_json)
    except json.JSONDecodeError:
        return {}
    return d if isinstance(d, dict) else {}
