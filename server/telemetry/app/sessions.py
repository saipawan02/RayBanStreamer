"""Session registry: seq tracking, counters, writers, crash recovery (TECH-04 section 5)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .compact import compact_fully, uncompact_for_reopen
from .config import Settings
from .models import SCHEMA_VERSION, Event, ImuBatch
from .writer import (
    EVENTS_SCHEMA,
    IMU_SCHEMA,
    RollingParquetWriter,
    atomic_write_json,
    list_parts,
)

log = logging.getLogger("telemetry")


def jlog(event: str, **kv) -> None:
    """Structured log line. Never pass sample values, audio or secrets."""
    log.info(json.dumps({"event": event, **kv}, default=str))


def parse_session_id(raw: str) -> str | None:
    """Canonical lowercase UUID string, or None. Must run before any path is built."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _max_seq(folder: Path) -> int:
    best = -1
    for p in list_parts(folder):
        col = pq.read_table(p, columns=["seq"]).column("seq")
        m = pc.max(col).as_py()
        if m is not None:
            best = max(best, m)
    return best


def _total_rows(folder: Path) -> int:
    return sum(pq.ParquetFile(p).metadata.num_rows for p in list_parts(folder))


def _new_meta(session_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "started_recv_wall_ms": now_ms(),
        "ended_recv_wall_ms": None,
        "app": {},
        "settings": {},
        "delivered": {},
        "recordings": [],
        "calibration": {"imu_to_video_ms": None, "audio_to_video_ms": None,
                        "spread_ms": None, "method": "tap_v1"},
        "counts": {"imu_rows": 0, "events": 0, "seq_gaps": 0, "imu_dropped_reported": 0},
    }


class Session:
    def __init__(self, session_id: str, settings: Settings):
        self.id = session_id
        self.settings = settings
        self.base = settings.data_dir / session_id
        self.lock = asyncio.Lock()
        self.connections = 0
        self.last_activity = time.monotonic()
        self.last_meta_write = time.monotonic()
        self.stopped = False
        self._open_from_disk()

    # ----- disk recovery (sync; call via to_thread) -------------------------------------
    def _open_from_disk(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        uncompact_for_reopen(self.base)
        meta_path = self.base / "session.json"
        is_new = not meta_path.exists()
        try:
            self.meta = _new_meta(self.id) if is_new else json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):  # corrupt: rebuild from parts, keep counting
            self.meta = _new_meta(self.id)
            is_new = True
        s = self.settings
        self.imu_writer = RollingParquetWriter(self.base / "imu", IMU_SCHEMA, s.part_rows, s.part_seconds)
        self.events_writer = RollingParquetWriter(
            self.base / "events", EVENTS_SCHEMA, s.part_rows, s.part_seconds
        )
        self.next_seq = max(_max_seq(self.base / "imu"), _max_seq(self.base / "events")) + 1
        counts = self.meta.setdefault("counts", _new_meta(self.id)["counts"])
        counts["imu_rows"] = _total_rows(self.base / "imu")
        counts["events"] = _total_rows(self.base / "events")
        self.meta["ended_recv_wall_ms"] = None
        self.write_meta()
        jlog("session_opened", session_id=self.id, next_seq=self.next_seq, new=is_new)

    def write_meta(self) -> None:
        atomic_write_json(self.base / "session.json", self.meta)
        self.last_meta_write = time.monotonic()

    # ----- ingest (sync, cheap; call under self.lock) ----------------------------------
    @property
    def counts(self) -> dict:
        return self.meta["counts"]

    def accept(self, msg: ImuBatch | Event, recv_wall_ms: int) -> str:
        """Apply the TECH-04 section 5 seq rules. Returns ``duplicate`` or ``accepted``."""
        self.last_activity = time.monotonic()
        if msg.seq < self.next_seq:
            return "duplicate"
        if msg.seq > self.next_seq:
            self._add_event_row(
                seq=msg.seq, recv_wall_ms=recv_wall_ms, phone_wall_ms=msg.phone_wall_ms,
                phone_elapsed_ns=msg.phone_elapsed_ns, event="warning",
                detail={"message": "seq_gap", "expected": self.next_seq, "got": msg.seq},
            )
            self.counts["seq_gaps"] += 1
        if isinstance(msg, ImuBatch):
            self._add_batch(msg, recv_wall_ms)
        else:
            self._add_event_row(
                seq=msg.seq, recv_wall_ms=recv_wall_ms, phone_wall_ms=msg.phone_wall_ms,
                phone_elapsed_ns=msg.phone_elapsed_ns, event=msg.event, detail=msg.detail,
            )
            self._apply_event_meta(msg)
        self.next_seq = msg.seq + 1
        return "accepted"

    def _add_batch(self, b: ImuBatch, recv_wall_ms: int) -> None:
        for s in b.samples:
            row = {
                "seq": b.seq, "timestamp_ns": s.timestamp_ns, "recv_wall_ms": recv_wall_ms,
                "phone_wall_ms": b.phone_wall_ms, "phone_elapsed_ns": b.phone_elapsed_ns,
                "source": s.source,
            }
            for prefix, vec in (("a", s.accel), ("g", s.gyro), ("m", s.mag)):
                if vec is not None:
                    row.update({f"{prefix}x": vec[0], f"{prefix}y": vec[1], f"{prefix}z": vec[2]})
            if s.quat is not None:
                row.update({"qw": s.quat[0], "qx": s.quat[1], "qy": s.quat[2], "qz": s.quat[3]})
            self.imu_writer.append(row)
        self.counts["imu_rows"] += len(b.samples)
        self.counts["imu_dropped_reported"] += b.dropped_since_last

    def _add_event_row(self, *, seq, recv_wall_ms, phone_wall_ms, phone_elapsed_ns, event, detail) -> None:
        self.events_writer.append({
            "seq": seq, "recv_wall_ms": recv_wall_ms, "phone_wall_ms": phone_wall_ms,
            "phone_elapsed_ns": phone_elapsed_ns, "event": event,
            "detail_json": None if detail is None else json.dumps(detail),
        })
        self.counts["events"] += 1

    def _apply_event_meta(self, e: Event) -> None:
        d = e.detail or {}
        if e.event == "session_started":
            self.meta["app"] = {
                "version": d.get("app_version"), "sdk_version": d.get("sdk_version"),
                "device_model": d.get("device_model"), "android_version": d.get("android_version"),
            }
        elif e.event == "settings_applied":
            self.meta["settings"] = {k: d.get(k) for k in (
                "video_quality", "frame_rate", "audio_rate_hz", "audio_channels",
                "imu_rate_hz", "compressed_video")}
        elif e.event == "video_started":
            self.meta["delivered"].update({"width": d.get("width"), "height": d.get("height")})
        elif e.event == "session_stopped":
            self.stopped = True

    # ----- flushing (sync; call via to_thread under self.lock) --------------------------
    def flush_due(self, force: bool = False) -> None:
        for w in (self.imu_writer, self.events_writer):
            if force or w.due():
                w.flush()
        if force or time.monotonic() - self.last_meta_write >= self.settings.meta_seconds:
            self.write_meta()

    def needs_flush(self) -> bool:
        return self.imu_writer.due() or self.events_writer.due()

    def summary(self) -> dict:
        return {
            "session_id": self.id,
            "started_recv_wall_ms": self.meta.get("started_recv_wall_ms"),
            "ended_recv_wall_ms": self.meta.get("ended_recv_wall_ms"),
            "imu_rows": self.counts["imu_rows"],
            "events": self.counts["events"],
            "last_seq": self.next_seq - 1,
            "active": self.connections > 0,
        }


class SessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def get_or_open(self, session_id: str) -> Session:
        async with self._lock:
            sess = self.sessions.get(session_id)
            if sess is None:
                sess = await asyncio.to_thread(Session, session_id, self.settings)
                self.sessions[session_id] = sess
            return sess

    async def close_session(self, session_id: str) -> None:
        """Final flush, final session.json, compaction. Aborts if a client has reconnected."""
        async with self._lock:
            sess = self.sessions.get(session_id)
            if sess is None or sess.connections > 0:
                return
            async with sess.lock:
                sess.meta["ended_recv_wall_ms"] = now_ms()
                await asyncio.to_thread(sess.flush_due, True)
                result = await asyncio.to_thread(compact_fully, sess.base)
            del self.sessions[session_id]
            jlog("session_closed", session_id=session_id, compaction=result, **sess.counts)

    async def tick(self) -> None:
        """Called about once per second: timed flushes, periodic session.json, idle close."""
        for sess in list(self.sessions.values()):
            async with sess.lock:
                if sess.needs_flush() or (
                    time.monotonic() - sess.last_meta_write >= self.settings.meta_seconds
                ):
                    await asyncio.to_thread(sess.flush_due)
            idle = time.monotonic() - sess.last_activity
            if sess.connections == 0 and idle >= self.settings.idle_seconds:
                await self.close_session(sess.id)

    async def flush_all(self) -> None:
        """Shutdown path (SIGTERM): flush everything but keep sessions resumable."""
        for sess in list(self.sessions.values()):
            async with sess.lock:
                await asyncio.to_thread(sess.flush_due, True)
