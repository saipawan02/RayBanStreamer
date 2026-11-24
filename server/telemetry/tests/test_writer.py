import time

import pyarrow.parquet as pq

from app.writer import IMU_SCHEMA, RollingParquetWriter, list_parts


def _row(i):
    return {"seq": i, "timestamp_ns": i, "recv_wall_ms": 1, "phone_wall_ms": 2,
            "phone_elapsed_ns": 3, "source": "glasses", "ax": 1.0}


def test_rolls_at_part_rows(tmp_path):
    w = RollingParquetWriter(tmp_path / "imu", IMU_SCHEMA, part_rows=10, part_seconds=60)
    for i in range(25):
        w.append(_row(i))
        if w.due():
            w.flush()
    parts = list_parts(tmp_path / "imu")
    assert [p.name for p in parts] == ["part-000001.parquet", "part-000002.parquet"]
    assert all(pq.read_table(p).num_rows == 10 for p in parts)
    w.flush()
    assert len(list_parts(tmp_path / "imu")) == 3


def test_rolls_on_timer_when_idle(tmp_path):
    w = RollingParquetWriter(tmp_path / "imu", IMU_SCHEMA, part_rows=1000, part_seconds=0.1)
    w.append(_row(0))
    assert not w.due()
    time.sleep(0.15)
    assert w.due()
    w.flush()
    assert pq.read_table(list_parts(tmp_path / "imu")[0]).num_rows == 1


def test_tmp_never_listed_and_cleaned(tmp_path):
    folder = tmp_path / "imu"
    folder.mkdir()
    (folder / "part-000007.parquet.tmp").write_bytes(b"junk")
    w = RollingParquetWriter(folder, IMU_SCHEMA, 10, 5)
    assert list_parts(folder) == []
    assert not list(folder.glob("*.tmp"))
    w.append(_row(0))
    w.flush()
    assert [p.name for p in list_parts(folder)] == ["part-000001.parquet"]


def test_numbering_continues_after_restart(tmp_path):
    w = RollingParquetWriter(tmp_path / "imu", IMU_SCHEMA, 10, 5)
    w.append(_row(0))
    w.flush()
    w2 = RollingParquetWriter(tmp_path / "imu", IMU_SCHEMA, 10, 5)
    w2.append(_row(1))
    assert w2.flush().name == "part-000002.parquet"


def test_null_columns_are_null(tmp_path):
    w = RollingParquetWriter(tmp_path / "imu", IMU_SCHEMA, 10, 5)
    w.append(_row(0))
    p = w.flush()
    t = pq.read_table(p).to_pylist()[0]
    assert t["ax"] == 1.0 and t["ay"] is None and t["qw"] is None
