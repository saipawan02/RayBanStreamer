#!/usr/bin/env python3
"""One-page health report for a session (TECH-06 sections 2 and 3).

  python tools/check_session.py <session_id> [--data-root /srv/glasses] [--json]

Exit code 1 if an acceptance threshold fails:
  - video, audio or IMU missing
  - delivered video fps or IMU rate below 90% of the requested value
  - audio gap over 200 ms with no pause or reconnect event to explain it
  - calibration spread of 50 ms or more (an uncalibrated session is a warning, not a failure)
IMU rate ignores timestamp jumps over 2 s (pauses); the p5 rate is the 5th percentile of 1 s bins.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpu"))
from glasses_reader import find_gaps, load_audio, open_session
from glasses_reader.data import segment_start_ms
from glasses_reader.timeline import FrameClock, nominal_fps, read_video_pts

MIN_FRACTION = 0.90
AUDIO_GAP_S = 0.2
PAUSE_JUMP_S = 2.0
SPREAD_LIMIT_MS = 50.0
EXPLAINING_EVENTS = {"session_paused", "session_resumed", "uplink_reconnect"}


def imu_stats(timestamp_ns: np.ndarray) -> dict:
    ts = np.sort(timestamp_ns)
    if len(ts) < 2:
        return {"imu_rate_mean_hz": None, "imu_rate_p5_hz": None}
    dt = np.diff(ts) / 1e9
    active = float(dt[dt <= PAUSE_JUMP_S].sum())
    mean = (len(ts) - 1) / active if active > 0 else None
    t = (ts - ts[0]) / 1e9
    counts = np.bincount(t.astype(int))
    if len(counts) > 2:
        counts = counts[:-1]                     # last bin is partial
    return {"imu_rate_mean_hz": mean, "imu_rate_p5_hz": float(np.percentile(counts, 5))}


def build_report(session_id: str, data_root: str) -> dict:
    s = open_session(session_id, data_root)
    warnings: list[str] = []
    failures: list[str] = []
    st = s.meta.get("settings") or {}
    counts = s.meta.get("counts") or {}
    event_names = set(s.events["event"]) if len(s.events) else set()

    # IMU
    imu = imu_stats(s.imu["timestamp_ns"].to_numpy()) if len(s.imu) else \
        {"imu_rate_mean_hz": None, "imu_rate_p5_hz": None}
    if not len(s.imu):
        failures.append("IMU missing: no rows")
    elif st.get("imu_rate_hz") and imu["imu_rate_mean_hz"] is not None \
            and imu["imu_rate_mean_hz"] < MIN_FRACTION * st["imu_rate_hz"]:
        failures.append(f"IMU rate {imu['imu_rate_mean_hz']:.1f} Hz is under "
                        f"{MIN_FRACTION:.0%} of requested {st['imu_rate_hz']} Hz")

    # video (timestamps only, no decoding)
    segs, video_gaps, total_frames, total_span = [], [], 0, 0.0
    if s.segments:
        fps = nominal_fps(s.meta, s.segments)
        starts = [segment_start_ms(p) for p in s.segments]
        clock = FrameClock(fps, starts[0])
        for p, start in zip(s.segments, starts, strict=True):
            pts = read_video_pts(p)
            segs.append({"file": p.name, "frames": len(pts),
                         "duration_s": float(pts[-1] - pts[0]) if len(pts) > 1 else 0.0})
            total_frames += max(len(pts) - 1, 0)
            total_span += segs[-1]["duration_s"]
            clock.new_segment(start)
            for x in pts:
                pts_s, _, gap = clock.stamp(float(x))
                if gap > 0:
                    video_gaps.append({"at_s": round(pts_s, 3), "missing_s": round(gap, 3)})
    video_fps = total_frames / total_span if total_span > 0 else None
    if not total_frames:
        failures.append("video missing: no recording segments or no frames")
    elif st.get("frame_rate") and video_fps is not None and video_fps < MIN_FRACTION * st["frame_rate"]:
        failures.append(f"video fps {video_fps:.1f} is under {MIN_FRACTION:.0%} of requested "
                        f"{st['frame_rate']}")
    if video_gaps:
        warnings.append(f"{len(video_gaps)} video gap(s): {video_gaps[:5]}")

    # audio
    samples, t = load_audio(s, 16000) if s.segments else (np.array([]), np.array([]))
    audio_present = len(samples) > 0
    agaps = [g for g in find_gaps(t, 16000, AUDIO_GAP_S)] if audio_present else []
    if not audio_present:
        failures.append("audio missing")
    elif agaps:
        msg = f"{len(agaps)} audio gap(s) over {AUDIO_GAP_S * 1000:.0f} ms: " \
              f"{[(round(a, 2), round(b, 2)) for a, b in agaps[:5]]}"
        (warnings if event_names & EXPLAINING_EVENTS else failures).append(
            msg + ("" if event_names & EXPLAINING_EVENTS else " (no pause or reconnect event)"))

    # calibration, counters
    cal = s.meta.get("calibration") or {}
    spread = cal.get("spread_ms")
    if spread is None:
        warnings.append("not calibrated: run tools/estimate_offset.py --write after a tap test")
    elif spread >= SPREAD_LIMIT_MS:
        failures.append(f"calibration spread {spread:.1f} ms is not under {SPREAD_LIMIT_MS:.0f} ms")
    if counts.get("seq_gaps"):
        warnings.append(f"{counts['seq_gaps']} telemetry seq gap(s)")
    if s.offsets.source == "none":
        warnings.append("IMU time is not aligned to video (no anchors, no calibration)")

    return {
        "session_id": session_id, "imu_rows": len(s.imu), **imu,
        "imu_dropped_reported": counts.get("imu_dropped_reported", 0),
        "seq_gaps": counts.get("seq_gaps", 0),
        "video_segments": segs, "video_fps_mean": video_fps, "video_gaps": video_gaps,
        "audio_present": audio_present,
        "audio_gaps": [{"start_s": round(a, 3), "end_s": round(b, 3)} for a, b in agaps],
        "calibration": {"spread_ms": spread, "offsets_source": s.offsets.source,
                        "imu_to_video_s": s.offsets.imu_to_video_s,
                        "audio_to_video_s": s.offsets.audio_to_video_s},
        "warnings": warnings, "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id")
    ap.add_argument("--data-root", default="/srv/glasses")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    a = ap.parse_args()
    r = build_report(a.session_id, a.data_root)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        f = lambda v, d=1: "n/a" if v is None else f"{v:.{d}f}"
        print(f"Session {r['session_id']}")
        print(f"  IMU    rows={r['imu_rows']}  mean={f(r['imu_rate_mean_hz'])} Hz  p5={f(r['imu_rate_p5_hz'])} Hz"
              f"  dropped_reported={r['imu_dropped_reported']}  seq_gaps={r['seq_gaps']}")
        print(f"  Video  fps_mean={f(r['video_fps_mean'])}  segments={len(r['video_segments'])}  "
              f"gaps={len(r['video_gaps'])}")
        for sg in r["video_segments"]:
            print(f"         {sg['file']}  frames={sg['frames']}  {sg['duration_s']:.1f} s")
        print(f"  Audio  present={r['audio_present']}  gaps>{AUDIO_GAP_S * 1000:.0f}ms={len(r['audio_gaps'])}")
        c = r["calibration"]
        print(f"  Calib  spread_ms={f(c['spread_ms'])}  offsets={c['offsets_source']}  "
              f"imu_to_video={c['imu_to_video_s']:.3f} s  audio_to_video={c['audio_to_video_s']:.3f} s")
        for w in r["warnings"]:
            print(f"  WARN   {w}")
        for x in r["failures"]:
            print(f"  FAIL   {x}")
        print("  RESULT", "FAIL" if r["failures"] else "PASS")
    return 1 if r["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
