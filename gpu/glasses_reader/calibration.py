"""Tap-test calibration (TECH-05 section 6). Pure numpy/scipy; testable without a GPU.

All times passed in are each stream's OWN seconds (IMU from its first sample, video as ``pts_s``
before offsets, audio on the video timeline with ``audio_to_video_s`` not applied). The result is
the value to ADD to a stream's time to land on the video timeline, which is what ``Offsets`` stores.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks, medfilt

N_TAPS = 3
MAX_OFFSET_S = 3.0
SPACING_TOL_S = 0.25


def mad(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x)))) + 1e-12


def peaks(t: np.ndarray, x: np.ndarray, min_gap_s: float = 0.5, k: float = 8.0) -> np.ndarray:
    """Times of the (up to) three strongest peaks, ascending. Empty if nothing is above threshold."""
    if len(t) < 3:
        return np.array([])
    thr = float(np.median(x) + k * mad(x))
    step = float(np.median(np.diff(t)))
    idx, props = find_peaks(x, height=thr, distance=max(1, int(min_gap_s / step)),
                            prominence=thr * 0.3)
    if len(idx) == 0:
        return np.array([])
    top = idx[np.argsort(props["peak_heights"])[-N_TAPS:]]
    return np.sort(t[top])


def match_three(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray | None, str | None]:
    """Per-tap offsets ``b - a`` or ``(None, reason)``."""
    if len(a) < N_TAPS or len(b) < N_TAPS:
        return None, "fewer than 3 taps found"
    if np.max(np.abs(np.diff(a) - np.diff(b))) > SPACING_TOL_S:
        return None, "tap spacing disagrees between streams"
    d = b - a
    if np.max(np.abs(d)) > MAX_OFFSET_S:
        return None, f"offset exceeds {MAX_OFFSET_S} s"
    return d, None


# ---- activity signals --------------------------------------------------------------------
def imu_signal(timestamp_ns: np.ndarray, accel: np.ndarray, gyro: np.ndarray | None = None
               ) -> tuple[np.ndarray, np.ndarray]:
    """(t_imu_s, activity). High-passed accelerometer magnitude (0.5 s moving-median removed);
    falls back to the gyro-magnitude derivative when accel is missing."""
    order = np.argsort(timestamp_ns, kind="stable")
    ts = timestamp_ns[order]
    t = (ts - ts[0]) / 1e9
    a = accel[order]
    if not np.isnan(a).all():
        mag = np.linalg.norm(np.nan_to_num(a), axis=1)
        rate = 1.0 / np.median(np.diff(t))
        k = int(rate * 0.5) | 1
        return t, np.abs(mag - medfilt(mag, k))
    if gyro is None:
        raise ValueError("no accel or gyro samples")
    g = np.linalg.norm(np.nan_to_num(gyro[order]), axis=1)
    return t, np.abs(np.gradient(g, t))


def audio_signal(samples: np.ndarray, t: np.ndarray, sample_rate: int, hop_ms: float = 5.0
                 ) -> tuple[np.ndarray, np.ndarray]:
    """(t_s, onset). Short-time energy on a 5 ms hop, then the positive derivative."""
    hop = max(1, int(sample_rate * hop_ms / 1000))
    n = len(samples) // hop
    if n < 3:
        return np.array([]), np.array([])
    energy = (samples[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1)
    onset = np.maximum(np.diff(energy, prepend=energy[0]), 0.0)
    return t[: n * hop : hop], onset


def downsample_gray(img: np.ndarray, out_w: int = 64, out_h: int = 112) -> np.ndarray:
    """RGB uint8 -> grayscale float32 (out_h, out_w) by block averaging."""
    g = img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    h, w = g.shape
    ys = np.linspace(0, h, out_h + 1).astype(int)
    xs = np.linspace(0, w, out_w + 1).astype(int)
    rows = np.add.reduceat(g, ys[:-1], axis=0) / np.diff(ys)[:, None]
    return np.add.reduceat(rows, xs[:-1], axis=1) / np.diff(xs)[None, :]


def video_signal(pts_s: np.ndarray, small_frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """(t_s, activity): mean absolute difference between consecutive downsampled frames."""
    if len(small_frames) < 3:
        return np.array([]), np.array([])
    f = np.stack(small_frames)
    act = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return np.asarray(pts_s)[1:], act


def estimate(imu_t, imu_sig, video_t, video_sig, audio_t, audio_sig) -> dict:
    """Three-tap offsets. Failures are reported with a reason instead of guessing."""
    ti, tv, ta = (peaks(imu_t, imu_sig), peaks(video_t, video_sig), peaks(audio_t, audio_sig))
    out: dict = {"method": "tap_v1", "imu_to_video_s": None, "audio_to_video_s": None,
                 "spread_ms": None, "failures": {}}
    if len(tv) < N_TAPS:
        out["failures"]["video"] = f"found {len(tv)} of 3 taps (no peak above threshold)"
    for name, label, other, sig in (("imu_to_video_s", "imu", ti, imu_sig),
                                    ("audio_to_video_s", "audio", ta, audio_sig)):
        if len(other) < N_TAPS:
            out["failures"][label] = f"found {len(other)} of 3 taps (no peak above threshold)"
            continue
        d, why = match_three(other, tv)
        if d is None:
            out["failures"].setdefault(label, why)
            continue
        out[name] = float(np.median(d))
        if label == "imu":
            out["spread_ms"] = float((d.max() - d.min()) * 1000)
    return out


def estimate_session(s, backend: str = "auto") -> dict:
    """Run the tap test on a session (``Session`` from ``open_session``).

    IMU time is its own seconds from the first sample; video is ``pts_s``; audio is on the video
    timeline with ``audio_to_video_s`` NOT applied, so the result is the full correction.
    """
    from .audio import load_audio_raw
    from .video import iter_timed_frames

    out_fail: dict = {"method": "tap_v1", "imu_to_video_s": None, "audio_to_video_s": None,
                      "spread_ms": None, "failures": {}}
    imu = s.imu
    if imu.empty:
        out_fail["failures"]["imu"] = "session has no IMU rows"
        return out_fail
    imu_t, imu_sig = imu_signal(imu["timestamp_ns"].to_numpy(),
                                imu[["ax", "ay", "az"]].to_numpy(dtype=float),
                                imu[["gx", "gy", "gz"]].to_numpy(dtype=float))
    pts, small = [], []
    for tf in iter_timed_frames(s, backend):
        pts.append(tf.pts_s)
        small.append(downsample_gray(tf.image))
    video_t, video_sig = video_signal(np.array(pts), small)
    samples, t = load_audio_raw(s, 16000, apply_offset=False)
    audio_t, audio_sig = audio_signal(samples, t, 16000)
    res = estimate(imu_t, imu_sig, video_t, video_sig, audio_t, audio_sig)
    if len(samples) == 0:
        res["failures"]["audio"] = "session has no audio"
    return res
