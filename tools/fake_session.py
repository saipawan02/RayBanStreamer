#!/usr/bin/env python3
"""Generate a complete fake session on disk (TECH-06 section 2): IMU Parquet parts, events,
session.json and short HEVC+AAC fMP4 segment(s) named like MediaMTX recordings.

The session contains a tap test (three jolts at known times) with KNOWN offsets, so the readers
and `estimate_offset` can be checked against ground truth with no phone:

  video jolts at `taps` seconds (video timeline)
  IMU spikes at (tap - imu_offset_s) on the IMU's own clock   -> true imu_to_video_s = imu_offset_s
  audio clicks at (tap - audio_offset_s)                      -> true audio_to_video_s = audio_offset_s
  clock anchors are off by `anchor_error_s` from the truth (simulated Bluetooth delay)

Usage:  python tools/fake_session.py --data-root /tmp/glasses [--segments 2] [--seconds 14]
Requires: pip install av pyarrow numpy   (run from the repo root; it imports gpu/glasses_reader)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpu"))
from glasses_reader.data import EVENTS_SCHEMA, IMU_SCHEMA

DEFAULT_START_MS = 1_795_531_201_123  # 2026-11-24 14:40:01.123 UTC
AUDIO_RATE = 48_000


def _segment_name(start_ms: int) -> str:
    dt = datetime.fromtimestamp(start_ms // 1000, tz=UTC).replace(
        microsecond=(start_ms % 1000) * 1000)
    return dt.strftime("%Y-%m-%d_%H-%M-%S-%f") + ".mp4"


def _frame_image(base: np.ndarray, t: float, taps, fps: int) -> np.ndarray:
    jolt = any(tap <= t < tap + 2.0 / fps - 1e-9 for tap in taps)
    return np.roll(base, 6, axis=1) if jolt else base


def _audio(t0: float, t1: float, clicks, rng) -> np.ndarray:
    n = round((t1 - t0) * AUDIO_RATE)
    t = t0 + np.arange(n) / AUDIO_RATE
    x = 0.02 * np.sin(2 * np.pi * 440 * t) + rng.normal(0, 0.003, n)
    for c in clicks:
        i = round((c - t0) * AUDIO_RATE)
        if 0 <= i < n:
            m = min(int(0.005 * AUDIO_RATE), n - i)
            x[i : i + m] += rng.uniform(-0.9, 0.9, m)
    return np.clip(x, -1, 1).astype(np.float32)


def _write_segment(path: Path, t0: float, t1: float, taps, clicks, fps, w, h, base, rng,
                   with_audio: bool) -> None:
    import av

    opts = {"movflags": "frag_keyframe+empty_moov+default_base_moof"}
    with av.open(str(path), "w", format="mp4", options=opts) as out:
        v = out.add_stream("libx265", rate=fps)
        v.width, v.height, v.pix_fmt = w, h, "yuv420p"
        v.time_base = Fraction(1, fps)
        v.options = {"x265-params": "log-level=none:keyint=24:bframes=0", "crf": "18"}
        a = None
        if with_audio:
            a = out.add_stream("aac", rate=AUDIO_RATE, layout="mono")
        n_frames = round((t1 - t0) * fps)
        for i in range(n_frames):
            img = _frame_image(base, t0 + i / fps, taps, fps)
            fr = av.VideoFrame.from_ndarray(img, format="rgb24").reformat(format="yuv420p")
            fr.pts, fr.time_base = i, Fraction(1, fps)
            for p in v.encode(fr):
                out.mux(p)
        for p in v.encode(None):
            out.mux(p)
        if a is not None:
            pcm = _audio(t0, t1, clicks, rng)
            rs = av.AudioResampler(format="fltp", layout="mono", rate=AUDIO_RATE, frame_size=1024)
            pos = 0
            while pos < len(pcm):
                chunk = pcm[pos : pos + 1024]
                af = av.AudioFrame.from_ndarray(chunk[None, :], format="flt", layout="mono")
                af.sample_rate, af.pts, af.time_base = AUDIO_RATE, pos, Fraction(1, AUDIO_RATE)
                for r in rs.resample(af):
                    for p in a.encode(r):
                        out.mux(p)
                pos += len(chunk)
            for r in rs.resample(None):
                for p in a.encode(r):
                    out.mux(p)
            for p in a.encode(None):
                out.mux(p)


def make_fake_session(data_root: str | Path, session_id: str | None = None, seconds: float = 14.0,
                      fps: int = 24, width: int = 128, height: int = 224,
                      taps: tuple[float, float, float] = (5.0, 6.0, 7.0),
                      imu_offset_s: float = 0.12, audio_offset_s: float = -0.03,
                      anchor_error_s: float = 0.25, imu_rate: int = 60, segments: int = 1,
                      gap_s: float = 4.0, with_audio: bool = True, seed: int = 7,
                      start_wall_ms: int = DEFAULT_START_MS) -> dict:
    """Write the session under ``data_root``; returns the ground truth."""
    assert segments in (1, 2)
    root = Path(data_root)
    sid = session_id or str(uuid.uuid4())
    rng = np.random.default_rng(seed)
    split = max(seconds / 2, max(taps) + 1.0) if segments == 2 else seconds
    assert max(taps) + 0.2 < split < seconds + 1e-9, "taps must fall in the first segment"
    total = seconds + (gap_s if segments == 2 else 0.0)

    # --- video + audio segments
    base = np.kron(rng.integers(0, 255, (height // 4, width // 4, 3), dtype=np.uint8),
                   np.ones((4, 4, 1), dtype=np.uint8))
    rec = root / "recordings" / "glasses"
    rec.mkdir(parents=True, exist_ok=True)
    spans = [(0.0, split, start_wall_ms)]
    if segments == 2:
        spans.append((split, seconds, start_wall_ms + round((split + gap_s) * 1000)))
    audio_clicks = [t - audio_offset_s for t in taps]
    seg_files = []
    for t0, t1, seg_ms in spans:
        path = rec / _segment_name(seg_ms)
        _write_segment(path, t0, t1, taps, audio_clicks, fps, width, height, base, rng, with_audio)
        end = seg_ms / 1000 + (t1 - t0)
        os.utime(path, (end, end))
        seg_files.append(f"recordings/glasses/{path.name}")

    # --- IMU: own clock, spikes at tap - imu_offset
    n = int(total * imu_rate)
    t_imu = np.arange(n) / imu_rate
    t0_ns = 5_000_000_000_000
    ts = t0_ns + np.round(t_imu * 1e9).astype(np.int64)
    accel = np.stack([rng.normal(0, 0.02, n), 9.81 + rng.normal(0, 0.02, n),
                      rng.normal(0, 0.02, n)], axis=1)
    for tap in taps:
        k = round((tap - imu_offset_s) * imu_rate)
        accel[k, 1] += 6.0
        accel[k + 1, 1] -= 6.0
    gyro = rng.normal(0, 0.002, (n, 3))

    a_video0 = 1_000_000_000_000                               # phone_elapsed_ns of first video frame
    a_imu0 = a_video0 + round((imu_offset_s + anchor_error_s) * 1e9)
    batch = imu_rate // 10
    rows: dict[str, list] = {c: [] for c in IMU_SCHEMA.names}
    seq = 8
    for j in range(0, n, batch):
        stop = min(j + batch, n)
        for k in range(j, stop):
            r = {"seq": seq, "timestamp_ns": int(ts[k]),
                 "recv_wall_ms": start_wall_ms + int(t_imu[k] * 1000) + 40,
                 "phone_wall_ms": start_wall_ms + int(t_imu[k] * 1000) + 20,
                 "phone_elapsed_ns": a_imu0 + int((stop / imu_rate) * 1e9), "source": "glasses",
                 "ax": accel[k, 0], "ay": accel[k, 1], "az": accel[k, 2],
                 "gx": gyro[k, 0], "gy": gyro[k, 1], "gz": gyro[k, 2]}
            for c in IMU_SCHEMA.names:
                rows[c].append(r.get(c))
        seq += 1
    imu_dir = root / "data" / sid / "imu"
    imu_dir.mkdir(parents=True, exist_ok=True)
    table = pa.table(rows, schema=IMU_SCHEMA)
    for i, off in enumerate(range(0, table.num_rows, 1000), start=1):
        pq.write_table(table.slice(off, 1000), imu_dir / f"part-{i:06d}.parquet")

    # --- events
    def ev(s, name, elapsed, detail):
        return {"seq": s, "recv_wall_ms": start_wall_ms, "phone_wall_ms": start_wall_ms,
                "phone_elapsed_ns": elapsed, "event": name, "detail_json": json.dumps(detail)}

    evs = [
        ev(0, "session_started", a_video0, {"app_version": "fake", "sdk_version": "none",
                                            "device_model": "fake", "android_version": "0"}),
        ev(1, "settings_applied", a_video0, {"video_quality": "MEDIUM", "frame_rate": fps,
                                             "audio_rate_hz": AUDIO_RATE, "audio_channels": 1,
                                             "imu_rate_hz": imu_rate, "compressed_video": True}),
        ev(2, "video_started", a_video0, {"width": width, "height": height, "is_compressed": True}),
        ev(3, "audio_started", a_video0, {"sample_rate_hz": AUDIO_RATE, "channels": 1}),
        ev(4, "imu_started", a_imu0, {"rate_hz": imu_rate}),
        ev(5, "clock_anchor", a_video0, {"stream": "video", "first_pts_us": 0}),
        ev(6, "clock_anchor", a_video0, {"stream": "audio", "first_pts_us": 0}),
        ev(7, "clock_anchor", a_imu0, {"stream": "imu", "first_timestamp_ns": int(ts[0])}),
        ev(seq, "session_stopped", a_imu0 + int(total * 1e9), {"reason": "fake_done", "stats": {}}),
    ]
    ev_dir = root / "data" / sid / "events"
    ev_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({c: [e[c] for e in evs] for c in EVENTS_SCHEMA.names},
                            schema=EVENTS_SCHEMA), ev_dir / "part-000001.parquet")

    meta = {
        "schema_version": 1, "session_id": sid, "started_recv_wall_ms": start_wall_ms,
        "ended_recv_wall_ms": start_wall_ms + round(total * 1000),
        "app": {"version": "fake", "sdk_version": "none", "device_model": "fake",
                "android_version": "0"},
        "settings": {"video_quality": "MEDIUM", "frame_rate": fps, "audio_rate_hz": AUDIO_RATE,
                     "audio_channels": 1, "imu_rate_hz": imu_rate, "compressed_video": True},
        "delivered": {"width": width, "height": height},
        "recordings": seg_files,
        "calibration": {"imu_to_video_ms": None, "audio_to_video_ms": None, "spread_ms": None,
                        "method": "tap_v1"},
        "counts": {"imu_rows": n, "events": len(evs), "seq_gaps": 0, "imu_dropped_reported": 0},
    }
    (root / "data" / sid / "session.json").write_text(json.dumps(meta, indent=2))
    return {"session_id": sid, "taps_video_s": list(taps), "imu_to_video_s": imu_offset_s,
            "audio_to_video_s": audio_offset_s,
            "anchor_estimate_imu_to_video_s": imu_offset_s + anchor_error_s,
            "segments": seg_files, "fps": fps, "total_s": total,
            "gap_s": gap_s if segments == 2 else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--seconds", type=float, default=14.0)
    ap.add_argument("--segments", type=int, default=1, choices=(1, 2))
    ap.add_argument("--imu-offset-ms", type=float, default=120.0)
    ap.add_argument("--audio-offset-ms", type=float, default=-30.0)
    ap.add_argument("--no-audio", action="store_true")
    a = ap.parse_args()
    truth = make_fake_session(a.data_root, a.session_id, seconds=a.seconds, segments=a.segments,
                              imu_offset_s=a.imu_offset_ms / 1000,
                              audio_offset_s=a.audio_offset_ms / 1000, with_audio=not a.no_audio)
    print(json.dumps(truth, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
