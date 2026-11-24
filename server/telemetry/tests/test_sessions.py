import pyarrow.parquet as pq

from app.config import Settings
from app.models import Event, ImuBatch
from app.sessions import Session, parse_session_id
from app.writer import list_parts

from .helpers import SID, batch, event

S = lambda tmp: Settings(token="x" * 16, data_root=tmp, part_rows=1000, part_seconds=60)


def test_parse_session_id_rejects_paths():
    assert parse_session_id(SID.upper()) == SID
    for bad in ("../etc", "abc", "", SID + "/x"):
        assert parse_session_id(bad) is None


def test_seq_rules(tmp_path):
    s = Session(SID, S(tmp_path))
    assert s.next_seq == 0
    assert s.accept(ImuBatch.model_validate(batch(0)), 1) == "accepted"
    assert s.accept(ImuBatch.model_validate(batch(0)), 2) == "duplicate"
    assert s.counts["imu_rows"] == 6
    assert s.accept(Event.model_validate(event(5, "x")), 3) == "accepted"   # gap 1..4
    assert s.next_seq == 6 and s.counts["seq_gaps"] == 1
    s.flush_due(force=True)
    rows = pq.read_table(list_parts(tmp_path / "data" / SID / "events")[0]).to_pylist()
    assert [r["event"] for r in rows] == ["warning", "x"]
    assert '"seq_gap"' in rows[0]["detail_json"] and '"expected": 1' in rows[0]["detail_json"]


def test_values_stored_exactly(tmp_path):
    s = Session(SID, S(tmp_path))
    b = batch(0, n=1)
    b["samples"][0]["timestamp_ns"] = 123456789012345
    s.accept(ImuBatch.model_validate(b), 42)
    s.flush_due(force=True)
    r = pq.read_table(list_parts(tmp_path / "data" / SID / "imu")[0]).to_pylist()[0]
    assert r["timestamp_ns"] == 123456789012345 and r["recv_wall_ms"] == 42
    assert (r["ax"], r["ay"], r["az"]) == (0.01, 9.81, 0.02)
    assert r["mx"] is None and r["qw"] == 1.0


def test_recovery_after_unclean_stop(tmp_path):
    cfg = S(tmp_path)
    s = Session(SID, cfg)
    for i in range(4):
        s.accept(ImuBatch.model_validate(batch(i)), 1)
    s.flush_due(force=True)                                       # seq 0..3 durable
    s.accept(ImuBatch.model_validate(batch(4)), 1)                # buffered only, then "kill -9"
    s2 = Session(SID, cfg)
    assert s2.next_seq == 4                                       # max stored + 1
    assert s2.counts["imu_rows"] == 24


def test_session_json_written_at_start(tmp_path):
    Session(SID, S(tmp_path))
    import json
    m = json.loads((tmp_path / "data" / SID / "session.json").read_text())
    assert m["schema_version"] == 1 and m["session_id"] == SID and m["ended_recv_wall_ms"] is None
