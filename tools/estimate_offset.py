#!/usr/bin/env python3
"""Tap-test calibration for a session (TECH-05 section 6).

  python tools/estimate_offset.py <session_id> [--data-root /srv/glasses] [--backend auto] [--write]

Finds the three taps in the IMU, audio and video, prints the offsets to ADD to each stream's time
to land on the video timeline, and with --write stores them in session.json -> calibration
(imu_to_video_ms, audio_to_video_ms, spread_ms, method). Exit code 1 if a stream failed or the
spread is 50 ms or more (SOW section 11, row 10). Nothing is guessed: failures say why.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpu"))
from glasses_reader import open_session
from glasses_reader.calibration import estimate_session

SPREAD_LIMIT_MS = 50.0


def write_calibration(session_json: Path, res: dict) -> None:
    meta = json.loads(session_json.read_text())
    cal = meta.setdefault("calibration", {})
    for key, ms_key in (("imu_to_video_s", "imu_to_video_ms"), ("audio_to_video_s", "audio_to_video_ms")):
        if res.get(key) is not None:                   # keep the previous value if this run failed
            cal[ms_key] = round(res[key] * 1000, 3)
    if res.get("spread_ms") is not None:
        cal["spread_ms"] = round(res["spread_ms"], 3)
    cal["method"] = res.get("method", "tap_v1")
    tmp = session_json.with_name(session_json.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2))
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, session_json)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id")
    ap.add_argument("--data-root", default="/srv/glasses")
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--write", action="store_true", help="store the result in session.json")
    a = ap.parse_args()

    s = open_session(a.session_id, a.data_root)
    res = estimate_session(s, a.backend)
    print(json.dumps(res, indent=2))
    ok = not res["failures"] and res["spread_ms"] is not None and res["spread_ms"] < SPREAD_LIMIT_MS
    if res["spread_ms"] is not None and res["spread_ms"] >= SPREAD_LIMIT_MS:
        print(f"FAIL: spread {res['spread_ms']:.1f} ms is not under {SPREAD_LIMIT_MS:.0f} ms", file=sys.stderr)
    for stream, why in res["failures"].items():
        print(f"FAIL {stream}: {why}", file=sys.stderr)
    if a.write:
        write_calibration(Path(a.data_root) / "data" / a.session_id / "session.json", res)
        print("calibration written to session.json", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
