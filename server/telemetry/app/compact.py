"""Optional compaction of rolling parts into one Parquet file (TECH-04 section 7)."""
from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .writer import EVENTS_SCHEMA, IMU_SCHEMA, list_parts

_SORT = {"imu": [("seq", "ascending"), ("timestamp_ns", "ascending")], "events": [("seq", "ascending")]}
_SCHEMA = {"imu": IMU_SCHEMA, "events": EVENTS_SCHEMA}


def _read_parts(parts: list[Path], name: str) -> pa.Table:
    tables = [pq.read_table(p, schema=_SCHEMA[name]) for p in parts]
    return pa.concat_tables(tables)


def _rows(path: Path) -> int:
    return pq.ParquetFile(path).metadata.num_rows


def compact_one(base: Path, name: str) -> str:
    """One compaction step for ``imu`` or ``events``.

    Run 1 writes ``<name>.parquet``. Run 2 sees equal row counts and deletes the parts.
    Returns ``noop``, ``written`` or ``cleaned``.
    """
    parts = list_parts(base / name)
    if not parts:
        return "noop"
    single = base / f"{name}.parquet"
    if single.exists() and _rows(single) == sum(_rows(p) for p in parts):
        for p in parts:
            p.unlink()
        return "cleaned"
    table = _read_parts(parts, name)
    idx = pc.sort_indices(table, sort_keys=_SORT[name])
    table = table.take(idx)
    tmp = single.with_name(single.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, single)
    return "written"


def compact_session(base: Path) -> dict[str, str]:
    """Single step for both datasets."""
    return {name: compact_one(base, name) for name in ("imu", "events")}


def compact_fully(base: Path) -> dict[str, str]:
    """Run twice: write, then verify counts and delete the parts."""
    compact_session(base)
    return compact_session(base)


def uncompact_for_reopen(base: Path) -> None:
    """Called when a session is reopened for writing.

    If parts exist next to a single file (compaction interrupted), the parts are authoritative,
    so drop the single. If only the single exists, move it back in as ``part-000000`` so new
    parts continue after it and the loader never hides them.
    """
    for name in ("imu", "events"):
        single = base / f"{name}.parquet"
        if not single.exists():
            continue
        folder = base / name
        if list_parts(folder):
            single.unlink()
        else:
            folder.mkdir(parents=True, exist_ok=True)
            os.replace(single, folder / "part-000000.parquet")
