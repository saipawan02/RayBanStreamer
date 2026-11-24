"""Message builders shared by the tests."""
from __future__ import annotations

SID = "b6f0c2e4-8a31-4d0e-9b57-0a1c2d3e4f50"


def batch(seq: int, n: int = 6, ts0: int = 1_000_000_000, sid: str = SID, **over) -> dict:
    msg = {
        "schema_version": 1, "type": "imu_batch", "session_id": sid, "seq": seq,
        "phone_wall_ms": 1_764_000_000_000 + seq * 100, "phone_elapsed_ns": 98_765_000_000_000 + seq,
        "dropped_since_last": 0,
        "samples": [
            {"timestamp_ns": ts0 + (seq * n + i) * 16_666_667, "source": "glasses",
             "accel": [0.01, 9.81, 0.02], "gyro": [0.001, 0.0, -0.002],
             "mag": None, "quat": [1.0, 0.0, 0.0, 0.0]}
            for i in range(n)
        ],
    }
    msg.update(over)
    return msg


def event(seq: int, name: str = "warning", detail: dict | None = None, sid: str = SID) -> dict:
    return {
        "schema_version": 1, "type": "event", "session_id": sid, "seq": seq,
        "phone_wall_ms": 1_764_000_000_000 + seq * 100, "phone_elapsed_ns": 98_765_000_000_000 + seq,
        "event": name, "detail": detail,
    }
