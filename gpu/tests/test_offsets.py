import json

import numpy as np
import pandas as pd

from glasses_reader.offsets import add_video_time, resolve_offsets

S = 1_000_000_000


def _events(rows):
    return pd.DataFrame([{"seq": i, "recv_wall_ms": 0, "phone_wall_ms": 0, "phone_elapsed_ns": p,
                          "event": e, "detail_json": json.dumps(d) if d is not None else None}
                         for i, (p, e, d) in enumerate(rows)])


def _imu(timestamps, elapsed):
    return pd.DataFrame({"seq": range(len(timestamps)), "timestamp_ns": timestamps,
                         "phone_elapsed_ns": elapsed})


def test_anchors_run0_matches_spec_formula():
    ev = _events([(100 * S, "clock_anchor", {"stream": "video", "first_pts_us": 0}),
                  (100 * S + 400_000_000, "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S})])
    imu = _imu([5 * S, 5 * S + 16_000_000], [100 * S + 400_000_000, 100 * S + 500_000_000])
    off = resolve_offsets({}, ev, imu)
    assert off.source == "anchors" and abs(off.imu_to_video_s - 0.4) < 1e-9
    out = add_video_time(imu, off)
    assert np.allclose(out["t_video_s"], [0.4, 0.416])


def test_calibration_overrides_anchors_and_audio_offset_applies():
    ev = _events([(100 * S, "clock_anchor", {"stream": "video"}),
                  (101 * S, "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S})])
    imu = _imu([5 * S], [101 * S])
    meta = {"calibration": {"imu_to_video_ms": 120.0, "audio_to_video_ms": -30.0, "spread_ms": 4.0,
                            "method": "tap_v1"}}
    off = resolve_offsets(meta, ev, imu)
    assert off.source == "tap_v1" and abs(off.imu_to_video_s - 0.12) < 1e-12
    assert abs(off.audio_to_video_s + 0.03) < 1e-12 and off.spread_ms == 4.0
    assert abs(add_video_time(imu, off)["t_video_s"].iloc[0] - 0.12) < 1e-12


def test_no_anchors_no_calibration_is_unaligned_but_works():
    off = resolve_offsets({}, _events([]).reindex(columns=["seq", "recv_wall_ms", "phone_wall_ms",
                          "phone_elapsed_ns", "event", "detail_json"]), _imu([5 * S], [1]))
    assert off.source == "none" and off.imu_to_video_s == 0.0


def test_runs_after_a_pause_get_their_own_offset():
    # run 0: imu starts 0.4 s after video. Pause 20..30 s (phone time). Run 1: imu timestamps continue
    # on the glasses clock (T keeps counting), imu anchor arrives 0.5 s after resume.
    v0 = 100 * S
    ev = _events([
        (v0, "clock_anchor", {"stream": "video"}),
        (v0 + int(0.4 * S), "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S}),
        (v0 + 20 * S, "session_paused", {}),
        (v0 + 30 * S, "session_resumed", {}),
        (v0 + int(30.5 * S), "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S + 21 * S}),
    ])
    imu = _imu([5 * S, 5 * S + 10 * S, 26 * S, 26 * S + S],
               [v0 + int(0.4 * S), v0 + int(10.4 * S), v0 + int(30.5 * S), v0 + int(31.5 * S)])
    off = resolve_offsets({}, ev, imu)
    t = add_video_time(imu, off)["t_video_s"].to_numpy()
    # sample 3 arrived at phone time 30.5 s => video time 30.5 s regardless of the IMU clock jump
    assert np.allclose(t, [0.4, 10.4, 30.5, 31.5])


def test_calibrated_keeps_run_differences():
    v0 = 100 * S
    ev = _events([
        (v0, "clock_anchor", {"stream": "video"}),
        (v0 + int(0.4 * S), "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S}),
        (v0 + 30 * S, "session_resumed", {}),
        (v0 + int(30.5 * S), "clock_anchor", {"stream": "imu", "first_timestamp_ns": 5 * S + 21 * S}),
    ])
    imu = _imu([5 * S, 26 * S], [v0 + int(0.4 * S), v0 + int(30.5 * S)])
    off = resolve_offsets({"calibration": {"imu_to_video_ms": 100.0}}, ev, imu)
    t = add_video_time(imu, off)["t_video_s"].to_numpy()
    assert np.allclose(t, [0.1, 30.2])        # both shifted by calibrated - anchor0 = -0.3 s
