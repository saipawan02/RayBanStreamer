import numpy as np

from glasses_reader.calibration import estimate, match_three, peaks

from .conftest import needs_av


def _signal(times, taps, dt=0.01, width=0.01, seed=0):
    t = np.arange(0, 14, dt)
    x = np.abs(np.random.default_rng(seed).normal(0, 0.01, len(t)))
    for tap in taps:
        x[np.abs(t - tap) < width] += 1.0
    return t, x


def test_peaks_find_three_strongest():
    t, x = _signal(None, (3.0, 4.0, 5.0))
    p = peaks(t, x)
    assert np.allclose(p, [3.0, 4.0, 5.0], atol=0.02)


def test_estimate_recovers_known_offsets():
    ti, xi = _signal(None, (4.88, 5.88, 6.88))               # IMU own clock: video taps - 0.12
    tv, xv = _signal(None, (5.0, 6.0, 7.0), seed=1)
    ta, xa = _signal(None, (5.03, 6.03, 7.03), seed=2)       # audio click 30 ms after video jolt
    r = estimate(ti, xi, tv, xv, ta, xa)
    assert abs(r["imu_to_video_s"] - 0.12) < 0.02
    assert abs(r["audio_to_video_s"] + 0.03) < 0.02
    assert r["spread_ms"] < 20 and not r["failures"]


def test_failure_is_reported_not_guessed():
    ti, xi = _signal(None, (4.0, 5.0, 6.0))
    tv, xv = _signal(None, (5.0, 6.0, 7.0), seed=1)
    flat = np.full(len(ti), 0.01)
    r = estimate(ti, flat, tv, xv, ti, xi)
    assert r["imu_to_video_s"] is None and "imu" in r["failures"]
    d, why = match_three(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.9]))
    assert d is None and "spacing" in why


@needs_av
def test_end_to_end_on_a_fake_session(fake_one):
    from glasses_reader import open_session
    from glasses_reader.calibration import estimate_session

    root, truth = fake_one
    s = open_session(truth["session_id"], str(root))
    r = estimate_session(s)
    assert not r["failures"], r
    assert abs(r["imu_to_video_s"] - truth["imu_to_video_s"]) < 0.04
    assert abs(r["audio_to_video_s"] - truth["audio_to_video_s"]) < 0.04
    assert r["spread_ms"] < 50
    # the uncalibrated anchor estimate carries the simulated Bluetooth error
    assert abs(s.offsets.imu_to_video_s - truth["anchor_estimate_imu_to_video_s"]) < 1e-6
