import os

import pyarrow.parquet as pq

from app.compact import compact_fully, compact_one, uncompact_for_reopen
from app.config import Settings
from app.loader import find_segments, load_session, segment_start_ms
from app.models import ImuBatch
from app.sessions import Session
from app.writer import list_parts

from .helpers import SID, batch


def _session_with_parts(tmp_path):
    s = Session(SID, Settings(token="x" * 16, data_root=tmp_path, part_rows=1000, part_seconds=60))
    for seq in (2, 0, 1):                       # arrival order differs from seq order
        s.next_seq = seq                        # force acceptance so ordering is visible on disk
        s.accept(ImuBatch.model_validate(batch(seq)), 1)
        s.flush_due(force=True)
    return s


def test_loader_sorted_with_parts_only(tmp_path):
    _session_with_parts(tmp_path)
    d = load_session(SID, tmp_path)
    assert list(d["imu"]["seq"].drop_duplicates()) == [0, 1, 2]
    assert len(d["imu"]) == 18


def test_compaction_two_steps_then_loader(tmp_path):
    _session_with_parts(tmp_path)
    base = tmp_path / "data" / SID
    before = load_session(SID, tmp_path)["imu"]
    assert compact_one(base, "imu") == "written"
    assert len(list_parts(base / "imu")) == 3          # parts kept until the second run
    assert compact_one(base, "imu") == "cleaned"
    assert list_parts(base / "imu") == []
    after = load_session(SID, tmp_path)["imu"]
    assert after.equals(before)
    assert pq.ParquetFile(base / "imu.parquet").metadata.row_group(0).column(0).compression == "ZSTD"


def test_reopen_after_compaction_keeps_everything(tmp_path):
    _session_with_parts(tmp_path)
    base = tmp_path / "data" / SID
    compact_fully(base)
    uncompact_for_reopen(base)
    assert not (base / "imu.parquet").exists() and len(list_parts(base / "imu")) == 1
    s = Session(SID, Settings(token="x" * 16, data_root=tmp_path))
    assert s.next_seq == 3 and s.counts["imu_rows"] == 18


def test_find_segments_by_utc_name(tmp_path):
    rec = tmp_path / "recordings" / "glasses"
    rec.mkdir(parents=True)
    names = ["2026-11-24_14-40-01-123456.mp4", "2026-11-24_15-10-00-000000.mp4", "2026-11-24_18-00-00-000000.mp4"]
    for n in names:
        (rec / n).write_bytes(b"x")
    starts = [segment_start_ms(rec / n) for n in names]
    for n, st in zip(names, starts):
        os.utime(rec / n, (st / 1000 + 1500, st / 1000 + 1500))      # segment ends 25 min after start
    meta = {"started_recv_wall_ms": starts[0] + 60_000, "ended_recv_wall_ms": starts[1] + 600_000}
    got = [p.name for p in find_segments(meta, tmp_path)]
    assert got == names[:2]
    assert segment_start_ms(rec / "junk.mp4") is None
