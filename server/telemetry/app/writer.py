"""Crash-safe rolling Parquet writer and atomic JSON writer (TECH-04 section 6).

A file is never kept open: rows are buffered, then flushed as a complete small Parquet file
written to a temp name, fsynced, and atomically renamed to ``part-NNNNNN.parquet``.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

IMU_SCHEMA = pa.schema(
    [
        ("seq", pa.int64()),
        ("timestamp_ns", pa.int64()),
        ("recv_wall_ms", pa.int64()),
        ("phone_wall_ms", pa.int64()),
        ("phone_elapsed_ns", pa.int64()),
        ("source", pa.string()),
        *[(c, pa.float64()) for c in ("ax", "ay", "az", "gx", "gy", "gz", "mx", "my", "mz")],
        *[(c, pa.float64()) for c in ("qw", "qx", "qy", "qz")],
    ]
)

EVENTS_SCHEMA = pa.schema(
    [
        ("seq", pa.int64()),
        ("recv_wall_ms", pa.int64()),
        ("phone_wall_ms", pa.int64()),
        ("phone_elapsed_ns", pa.int64()),
        ("event", pa.string()),
        ("detail_json", pa.string()),
    ]
)

_PART_RE = re.compile(r"^part-(\d{6,})\.parquet$")


def list_parts(folder: Path) -> list[Path]:
    """Finished parts only (never ``.tmp`` files), in numeric order."""
    if not folder.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in folder.iterdir() if (m := _PART_RE.match(p.name))]
    return [p for _, p in sorted(found)]


def _fsync_dir(folder: Path) -> None:
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:  # e.g. Windows
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=2, sort_keys=False).encode("utf-8"))


class RollingParquetWriter:
    def __init__(self, folder: Path, schema: pa.Schema, part_rows: int, part_seconds: float):
        self.folder, self.schema = Path(folder), schema
        self.part_rows, self.part_seconds = part_rows, part_seconds
        self.folder.mkdir(parents=True, exist_ok=True)
        for stale in self.folder.glob("part-*.parquet.tmp"):  # leftovers from a crash
            stale.unlink(missing_ok=True)
        parts = list_parts(self.folder)
        self.n = int(_PART_RE.match(parts[-1].name).group(1)) if parts else 0  # type: ignore[union-attr]
        self._reset()

    def _reset(self) -> None:
        self.rows: dict[str, list] = {name: [] for name in self.schema.names}
        self.count = 0
        self.t0 = time.monotonic()

    def append(self, row: dict) -> None:
        if self.count == 0:
            self.t0 = time.monotonic()
        for name in self.schema.names:
            self.rows[name].append(row.get(name))
        self.count += 1

    def due(self) -> bool:
        """True when a flush should happen (row limit or timer)."""
        if not self.count:
            return False
        return self.count >= self.part_rows or time.monotonic() - self.t0 >= self.part_seconds

    def flush(self) -> Path | None:
        """Write buffered rows as one complete part. Safe to call from a worker thread."""
        if not self.count:
            return None
        table = pa.table(self.rows, schema=self.schema)
        n = self.n + 1
        final = self.folder / f"part-{n:06d}.parquet"
        tmp = final.with_name(final.name + ".tmp")
        pq.write_table(table, tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, final)
        _fsync_dir(self.folder)
        self.n = n  # only advance after the rename succeeded
        self._reset()
        return final
