"""End to end: the real tools/synthetic_imu_client.py against a live receiver (TECH-04 section 11)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.loader import load_session

from .conftest import TOKEN, start_server, stop_server, wait_for

CLIENT = Path(__file__).resolve().parents[3] / "tools" / "synthetic_imu_client.py"
SID = "11111111-2222-4333-8444-555555555555"


def test_run_with_drop_has_no_duplicates_and_no_gaps(tmp_path):
    srv = start_server(tmp_path)
    try:
        out = subprocess.run(
            [sys.executable, str(CLIENT), "--host", "127.0.0.1", "--port", str(srv.port), "--token", TOKEN,
             "--rate", "60", "--seconds", "8", "--drop-at", "3", "--session-id", SID],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        assert "dropped at" in out and "final_seq=" in out
        base = tmp_path / "data" / SID
        wait_for(lambda: (base / "imu.parquet").exists(), timeout=10)   # session_stopped -> compacted
    finally:
        stop_server(srv)
    d = load_session(SID, tmp_path)
    imu, ev = d["imu"], d["events"]
    all_seq = sorted(set(imu.seq) | set(ev.seq))
    assert all_seq == list(range(all_seq[0], all_seq[-1] + 1))          # no gaps across imu+events
    assert len(imu) == imu.groupby("timestamp_ns").ngroups               # no duplicate samples
    assert abs(len(imu) - 60 * 8) <= 0.01 * 60 * 8                       # within 1% of nominal
    assert list(ev.event) == ["session_started", "settings_applied", "session_stopped"]
    assert d["meta"]["ended_recv_wall_ms"] is not None
