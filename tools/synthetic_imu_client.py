#!/usr/bin/env python3
"""Synthetic phone for the telemetry receiver (TECH-04 section 11).

Sends session_started, settings_applied, 100 ms IMU batches, session_stopped over
ws://<host>:<port>/ingest/<session_id>. Honours hello.next_seq (resends unacked messages) and ack.

  python tools/synthetic_imu_client.py --host 100.x.y.z --token $TOKEN --rate 60 --seconds 300
  ... --drop-at 120      close the socket at second 120 and reconnect
  ... --kill-note        print the last seq sent every second (to compare after a server kill -9)

Requires: pip install websockets
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from collections import OrderedDict

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

BATCH_S = 0.1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--token", required=True)
    ap.add_argument("--rate", type=float, default=60.0, help="nominal IMU rate in Hz")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--drop-at", type=float, default=None, help="close and reconnect at this second")
    ap.add_argument("--kill-note", action="store_true")
    args = ap.parse_args()

    sid = args.session_id or str(uuid.uuid4())
    url = f"ws://{args.host}:{args.port}/ingest/{sid}?token={args.token}"
    print(f"session_id={sid}", flush=True)

    rng = random.Random(1)
    interval_ns = round(1e9 / args.rate)
    base_ns = time.monotonic_ns()           # stand-in for the glasses' monotonic clock
    pending: OrderedDict[int, str] = OrderedDict()   # seq -> json, until acked
    state = {"seq": 0, "sample": 0, "ws": None, "dropped": False, "last_try": 0.0, "sent": -1}

    def common(kind: str) -> dict:
        return {"schema_version": 1, "type": kind, "session_id": sid, "seq": state["seq"],
                "phone_wall_ms": time.time_ns() // 1_000_000, "phone_elapsed_ns": time.monotonic_ns()}

    def enqueue(msg: dict) -> None:
        pending[msg["seq"]] = json.dumps(msg)
        state["seq"] += 1

    def event(name: str, detail: dict | None = None) -> None:
        enqueue({**common("event"), "event": name, "detail": detail})

    def imu_batch(upto_sample: int) -> None:
        samples = []
        while state["sample"] < upto_sample:
            k = state["sample"]
            samples.append({
                "timestamp_ns": base_ns + k * interval_ns, "source": "glasses",
                "accel": [rng.gauss(0, 0.02), 9.81 + rng.gauss(0, 0.02), rng.gauss(0, 0.02)],
                "gyro": [rng.gauss(0, 0.002) for _ in range(3)],
                "mag": [22.1 + rng.gauss(0, 0.1), -3.4, 40.0],
                "quat": [1.0, 0.0, 0.0, 0.0],
            })
            state["sample"] += 1
        enqueue({**common("imu_batch"), "dropped_since_last": 0, "samples": samples})

    def drain_incoming(ws) -> None:
        while True:
            try:
                msg = json.loads(ws.recv(timeout=0))
            except TimeoutError:
                return
            if msg.get("type") == "ack":
                for s in [s for s in pending if s <= msg["seq"]]:
                    del pending[s]
            elif msg.get("type") == "nack":
                print(f"nack: {msg.get('reason')}", file=sys.stderr, flush=True)

    def connect_and_sync():
        ws = connect(url, open_timeout=5)
        hello = json.loads(ws.recv(timeout=5))
        nxt = hello["next_seq"]
        for s in [s for s in pending if s < nxt]:
            del pending[s]                    # server already has these
        if pending and min(pending) > nxt:
            print(f"warning: server expects seq {nxt}, oldest buffered is {min(pending)}",
                  file=sys.stderr, flush=True)
        for payload in list(pending.values()):
            ws.send(payload)                  # resend unacked messages with seq >= next_seq
        state["sent"] = max(pending, default=nxt - 1)
        print(f"connected next_seq={nxt} resent={len(pending)}", flush=True)
        return ws

    t0 = time.monotonic()
    event("session_started", {"app_version": "synthetic", "sdk_version": "none",
                              "device_model": "synthetic", "android_version": "0"})
    event("settings_applied", {"video_quality": "MEDIUM", "frame_rate": 24, "audio_rate_hz": 48000,
                               "audio_channels": 1, "imu_rate_hz": int(args.rate),
                               "compressed_video": True})
    n_batches = int(args.seconds / BATCH_S)
    next_note = 1.0
    for i in range(1, n_batches + 1):
        imu_batch(round(i * BATCH_S * args.rate))
        if i == n_batches:
            event("session_stopped", {"reason": "synthetic_done", "stats": {}})
        elapsed = time.monotonic() - t0

        if args.drop_at is not None and not state["dropped"] and elapsed >= args.drop_at and state["ws"]:
            state["dropped"] = True
            state["ws"].close()
            state["ws"] = None
            print(f"dropped at {elapsed:.1f}s", flush=True)
        if state["ws"] is None and time.monotonic() - state["last_try"] >= 1.0:
            state["last_try"] = time.monotonic()
            try:
                state["ws"] = connect_and_sync()   # resends everything unacked
            except (OSError, ConnectionClosed, TimeoutError) as exc:
                print(f"connect failed: {exc!r}", file=sys.stderr, flush=True)
        ws = state["ws"]
        if ws is not None:
            try:
                for seq in [q for q in pending if q > state["sent"]]:
                    ws.send(pending[seq])
                    state["sent"] = seq
                drain_incoming(ws)
            except (OSError, ConnectionClosed):
                state["ws"] = None
        if args.kill_note and elapsed >= next_note:
            next_note += 1.0
            print(f"last_seq={state['seq'] - 1} unacked={len(pending)}", flush=True)
        time.sleep(max(0.0, t0 + i * BATCH_S - time.monotonic()))

    # Acks arrive only every 50 messages, so unacked messages at the end are normal. Give the
    # server a moment to process the tail, then close.
    if state["ws"] is not None:
        try:
            drain_incoming(state["ws"])
            time.sleep(0.5)
            state["ws"].close()
        except (OSError, ConnectionClosed):
            pass
    print(f"final_seq={state['seq'] - 1} samples={state['sample']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
