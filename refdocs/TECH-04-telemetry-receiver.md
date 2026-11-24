# TECH-04: Telemetry Receiver

Companion to `SOW.md` section 8 and `TECH-01-architecture-and-contracts.md`. Message formats, Parquet schemas, folder layout and the connection protocol are defined in TECH-01 sections 5 and 6 and are **not repeated here**; this document says how to build the service that implements them. Evidence tags **[V] [R] [A]** are defined in TECH-01.

Goal: a small Python service that receives IMU batches and events from the phone over a WebSocket, stores them crash-safely as Parquet, and gives analytics code a one-line loader.

## 1. Stack and layout

- Python 3.11 or later; FastAPI and uvicorn (with the `websockets` package installed so uvicorn can serve WebSockets); Pydantic v2; PyArrow; pandas (loader only); pytest, httpx and `websockets` for tests.
- Dependency pinning in `pyproject.toml`; lock file committed.

```text
server/telemetry/
  pyproject.toml
  app/
    main.py          # FastAPI app, endpoints, lifespan
    config.py        # env settings
    models.py        # Pydantic models for TECH-01 section 5
    sessions.py      # SessionStore, Session (seq tracking, acks, counters)
    writer.py        # RollingParquetWriter (imu, events), session.json writer
    compact.py       # optional compaction to imu.parquet
    loader.py        # load_session(), find_segments()
  tests/             # pytest
  Dockerfile  glasses-telemetry.service
tools/synthetic_imu_client.py
```

## 2. Endpoints

| Method and path | Purpose |
| --- | --- |
| `WS /ingest/{session_id}?token=` | IMU batches and events (TECH-01 5.1, 5.2, 5.4) |
| `WS /pcm/{session_id}?token=` | Optional raw PCM channel (TECH-01 5.3) |
| `GET /health` | `{"status": "ok", "active_sessions": n, "version": "..."}`; no token |
| `GET /sessions?token=` | Known sessions with start time, row counts, last seq |
| `POST /sessions/{session_id}/compact?token=` | Force compaction (also runs automatically after idle) |

`session_id` must parse as a UUID; anything else is rejected before any file is created (path safety).

Authentication: shared secret in `TELEMETRY_TOKEN`, compared with `secrets.compare_digest`. Starlette cannot send a custom close code before `accept()`, so accept the socket, then `close(code=4401)` on a bad token and `close(code=4400)` on an unknown `schema_version` (TECH-01 5.4).

## 3. Configuration

Environment variables are in TECH-01 section 8 (`DATA_ROOT`, `TELEMETRY_TOKEN`, `TELEMETRY_BIND`, `PART_SECONDS`, `PART_ROWS`, `LOG_LEVEL`). Refuse to start if `TELEMETRY_TOKEN` is missing or shorter than 16 characters.

Run:

```bash
uvicorn app.main:app --host "$TELEMETRY_BIND" --port 8000 --ws-max-size 1048576 --log-level "${LOG_LEVEL:-info}"
```

## 4. Models (Pydantic v2)

```python
# app/models.py  (mirrors TECH-01 section 5)
from typing import Literal, Optional
from pydantic import BaseModel

Vec3 = Optional[tuple[float, float, float]]
Quat = Optional[tuple[float, float, float, float]]   # w, x, y, z

class ImuSample(BaseModel):
    timestamp_ns: int
    source: str = "unknown"
    accel: Vec3 = None
    gyro: Vec3 = None
    mag: Vec3 = None
    quat: Quat = None

class ImuBatch(BaseModel):
    schema_version: int
    type: Literal["imu_batch"]
    session_id: str
    seq: int
    phone_wall_ms: int
    phone_elapsed_ns: int
    dropped_since_last: int = 0
    samples: list[ImuSample]

class Event(BaseModel):
    schema_version: int
    type: Literal["event"]
    session_id: str
    seq: int
    phone_wall_ms: int
    phone_elapsed_ns: int
    event: str                 # free string; unknown names are stored, not rejected
    detail: Optional[dict] = None
```

Rules: validate `session_id` in the body equals the URL's; reject mismatches with close code 4400. Unknown extra fields are ignored (forward compatibility). Never reorder or modify sample values.

## 5. Session handling

Per session the store keeps: `next_seq`, counters (`imu_rows`, `events`, `seq_gaps`, `imu_dropped_reported`), the two writers, an `asyncio.Lock`, and `last_activity`.

On connect:

1. Create `data/<session_id>/` if missing; write `session.json` (TECH-01 6.4) if new.
2. Compute `next_seq`: in memory if the session is known; otherwise recover from disk by reading the `seq` column of existing `imu/` and `events/` parts and taking `max + 1` (0 if none). This makes reconnects work after a server restart.
3. Send `{"type": "hello", "schema_version": 1, "next_seq": next_seq}`.

On each text frame:

1. Parse and validate; on a validation error send `{"type": "nack", "reason": "..."}` and keep the socket open (one bad message must not end a session).
2. If `seq < next_seq`: duplicate, ignore silently (but still count toward the ack timer).
3. If `seq > next_seq`: record a `warning` row in `events` (`{"message": "seq_gap", "expected": n, "got": m}`), increment `seq_gaps`, accept.
4. Append rows to the writer buffers, set `next_seq = seq + 1`, update counters.
5. Every 50 accepted messages send `{"type": "ack", "seq": seq}`.

On disconnect: keep the session open (the phone will reconnect). After 5 minutes of inactivity, or on a `session_stopped` event followed by disconnect, close writers, write the final `session.json` and run compaction.

## 6. Crash-safe Parquet writing

A Parquet file written in one go is unreadable if the process dies before the footer is written. So the writer **never keeps a file open**:

- Buffer rows in memory (lists of Python values per column).
- Flush when buffered rows reach `PART_ROWS` (default 1000) **or** `PART_SECONDS` (default 5) have elapsed since the last flush. A background task per session checks the clock every second, because a quiet stream will not hit the row limit.
- A flush writes a complete small Parquet file to a temporary name in the same folder, calls `fsync`, and atomically renames it to `imu/part-000001.parquet` (the number increases by 1). `events/` works the same way.
- On clean close, flush what remains. On `SIGTERM`, flush all sessions in the app's lifespan shutdown.
- Result: after `kill -9`, at most one flush interval of data is lost (target under 10 seconds; the default settings give about 5).

```python
# app/writer.py (sketch; adapt, then test)
import os, time, pyarrow as pa, pyarrow.parquet as pq

IMU_SCHEMA = pa.schema([
    ("seq", pa.int64()), ("timestamp_ns", pa.int64()), ("recv_wall_ms", pa.int64()),
    ("phone_wall_ms", pa.int64()), ("phone_elapsed_ns", pa.int64()), ("source", pa.string()),
    *[(c, pa.float64()) for c in ("ax","ay","az","gx","gy","gz","mx","my","mz","qw","qx","qy","qz")],
])

class RollingParquetWriter:
    def __init__(self, folder, schema, part_rows, part_seconds):
        self.folder, self.schema = folder, schema
        self.part_rows, self.part_seconds = part_rows, part_seconds
        os.makedirs(folder, exist_ok=True)
        self.n = len([f for f in os.listdir(folder) if f.startswith("part-")])
        self.rows = {name: [] for name in schema.names}
        self.count, self.t0 = 0, time.monotonic()

    def append(self, row: dict):
        for name in self.schema.names:
            self.rows[name].append(row.get(name))
        self.count += 1
        if self.count >= self.part_rows:
            self.flush()

    def tick(self):
        if self.count and time.monotonic() - self.t0 >= self.part_seconds:
            self.flush()

    def flush(self):
        if not self.count:
            return
        table = pa.table(self.rows, schema=self.schema)
        self.n += 1
        final = os.path.join(self.folder, f"part-{self.n:06d}.parquet")
        tmp = final + ".tmp"
        pq.write_table(table, tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, final)
        self.rows = {name: [] for name in self.schema.names}
        self.count, self.t0 = 0, time.monotonic()
```

Row mapping from `ImuBatch` to `IMU_SCHEMA`: one row per sample; `recv_wall_ms = time.time_ns() // 1_000_000` taken **once per batch** when the frame arrives; `accel` to `ax, ay, az` (all `None` when the array is `null`), `gyro` to `gx, gy, gz`, `mag` to `mx, my, mz`, `quat` to `qw, qx, qy, qz`.

`session.json` is rewritten atomically (temp file and rename) at least every 30 seconds while active, and on close.

Do the Parquet writes in a worker thread (`asyncio.to_thread`) so a slow disk never blocks the WebSocket loop.

## 7. Compaction

`compact_session(session_id)` reads all parts with `pyarrow.dataset`, sorts by `(seq, timestamp_ns)`, writes `imu.parquet` (zstd compression) atomically, and leaves the parts in place until a second run confirms equal row counts, then deletes them. Do the same for `events`. Compaction is optional; the loader works with either form.

## 8. Loader

```python
# app/loader.py (sketch)
import json, pathlib, pyarrow.dataset as ds

def load_session(session_id: str, data_root: str = "/srv/glasses"):
    base = pathlib.Path(data_root) / "data" / session_id
    meta = json.loads((base / "session.json").read_text())

    def read(name):
        single = base / f"{name}.parquet"
        src = single if single.exists() else base / name
        return ds.dataset(str(src), format="parquet").to_table().to_pandas().sort_values(
            ["seq", "timestamp_ns"] if name == "imu" else ["seq"], kind="stable"
        ).reset_index(drop=True)

    return {"meta": meta, "imu": read("imu"), "events": read("events"),
            "segments": find_segments(meta, data_root)}
```

`find_segments(meta, data_root)` lists `recordings/glasses/*.mp4`, parses each file's start time from its name (server time zone; **set the server to UTC** so names are UTC), and returns files whose time span overlaps `[started_recv_wall_ms - 60 s, ended_recv_wall_ms + 60 s]`. Because MediaMTX starts a new segment on each publisher reconnect, a session may map to several files; return them in time order.

## 9. Optional endpoints

- **`/pcm/{session_id}`:** append payloads to `audio.wav` with a placeholder header, record `(presentation_time_us, byte_offset)` in `audio_pts.csv`, and fix the WAV header on close. Sample rate and channels come from the latest `settings_applied` event.
- **`/media/{session_id}`:** only for Plan B (TECH-01 section 7). Not built unless the SRT spike fails.

## 10. Deployment

```ini
# glasses-telemetry.service
[Unit]
Description=Glasses telemetry receiver
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=glasses
EnvironmentFile=/etc/glasses/telemetry.env
WorkingDirectory=/opt/glasses/server/telemetry
ExecStart=/opt/glasses/venv/bin/uvicorn app.main:app --host ${TELEMETRY_BIND} --port 8000 --ws-max-size 1048576
Restart=always
RestartSec=2
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

`TimeoutStopSec` leaves time for the shutdown flush. Bind to the Tailscale IP (`tailscale ip -4`) so the port is not reachable elsewhere; the firewall rule in TECH-03 is a second layer.

## 11. Testing

Unit and API tests (pytest):

- Model validation: accepts nulls for `accel`, `gyro`, `mag`, `quat`; ignores unknown fields; rejects missing `timestamp_ns`.
- Protocol: bad token closes with 4401; `hello` carries `next_seq`; duplicates are ignored; a `seq` gap writes a `warning` event; an ack arrives after 50 messages; a bad message gets a `nack` and the socket stays open.
- Writer: rolls at `PART_ROWS`; rolls on the timer when idle; temp files never appear as parts; parts are readable by `pyarrow` at every step.
- Recovery: create a session, stop the app object without a clean close, start a new app object, reconnect, and check `next_seq` equals the stored max plus one.
- Loader: returns sorted frames; works with parts only and after compaction.

Synthetic client (`tools/synthetic_imu_client.py`): arguments `--host`, `--token`, `--rate 60`, `--seconds 300`, `--session-id`, `--drop-at N` (close the socket at second N and reconnect), `--kill-note` (print the final `seq` sent so a crash test can compare). It generates plausible samples (gravity on one axis plus small noise), strictly increasing `timestamp_ns` at the nominal interval, batches every 100 ms, sends events (`session_started`, `settings_applied`, `session_stopped`), honours `hello.next_seq` and `ack`.

Acceptance tests (SOW section 11, row 4):

1. 5-minute run at 60 Hz: Parquet row count within 1% of `60 × 300 = 18,000`.
2. Mid-stream `kill -9` of the service: restart it, let the client reconnect, and confirm total loss is under 10 seconds of samples and that `seq` continuity resumes via `hello`.
3. `--drop-at 120`: no duplicates in the loaded data (check `seq` uniqueness), no gap.
4. Fifty parallel synthetic sessions for 60 seconds: no errors, event loop not blocked (health endpoint stays responsive).

## 12. References

- Message and storage contracts: `TECH-01-architecture-and-contracts.md`.
- SOW section 8 and section 11 (acceptance).
- Meta Motion API (field meanings and units): https://wearables.developer.meta.com/docs/develop/dat/motion/ [V]
