"""FastAPI app: WS /ingest, GET /health, GET /sessions, POST /sessions/{id}/compact (TECH-04 section 2)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time

from fastapi import FastAPI, HTTPException, Query, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from . import __version__
from .compact import compact_fully
from .config import Settings, load_settings
from .models import SCHEMA_VERSION, Event, ImuBatch
from .sessions import SessionStore, jlog, now_ms, parse_session_id

ACK_EVERY = 50
CLOSE_BAD_TOKEN = 4401
CLOSE_BAD_REQUEST = 4400


def _token_ok(given: str | None, settings: Settings) -> bool:
    return given is not None and secrets.compare_digest(given.encode(), settings.token.encode())


def create_app(settings: Settings | None = None) -> FastAPI:
    """``settings=None`` loads from the environment at startup and refuses to start on a bad token."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or load_settings()  # raises ConfigError -> uvicorn refuses to start
        logging.basicConfig(level=cfg.log_level.upper(), format="%(message)s")
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        store = SessionStore(cfg)
        app.state.settings, app.state.store = cfg, store

        async def ticker() -> None:
            while True:
                await asyncio.sleep(1.0)
                try:
                    await store.tick()
                except Exception as exc:  # noqa: BLE001  keep the loop alive; one bad flush must not end it
                    jlog("tick_error", error=repr(exc))

        task = asyncio.create_task(ticker())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await store.flush_all()

    app = FastAPI(title="glasses-telemetry", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "active_sessions": len(app.state.store.sessions), "version": __version__}

    def require_token(token: str | None) -> None:
        if not _token_ok(token, app.state.settings):
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/sessions")
    async def list_sessions(token: str | None = Query(default=None)) -> list[dict]:
        require_token(token)
        store: SessionStore = app.state.store
        out: dict[str, dict] = {s.id: s.summary() for s in store.sessions.values()}
        data_dir = app.state.settings.data_dir

        def scan() -> list[dict]:
            found = []
            for d in sorted(data_dir.iterdir()) if data_dir.is_dir() else []:
                if d.name in out or parse_session_id(d.name) != d.name:
                    continue
                try:
                    m = json.loads((d / "session.json").read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                c = m.get("counts", {})
                found.append({
                    "session_id": d.name, "started_recv_wall_ms": m.get("started_recv_wall_ms"),
                    "ended_recv_wall_ms": m.get("ended_recv_wall_ms"),
                    "imu_rows": c.get("imu_rows", 0), "events": c.get("events", 0),
                    "last_seq": None, "active": False,
                })
            return found

        for item in await asyncio.to_thread(scan):
            out[item["session_id"]] = item
        return sorted(out.values(), key=lambda s: s["started_recv_wall_ms"] or 0)

    @app.post("/sessions/{session_id}/compact")
    async def compact(session_id: str, token: str | None = Query(default=None)) -> dict:
        require_token(token)
        sid = parse_session_id(session_id)
        if sid is None:
            raise HTTPException(status_code=400, detail="session_id must be a UUID")
        if sid in app.state.store.sessions:
            raise HTTPException(status_code=409, detail="session is open; it compacts when it closes")
        base = app.state.settings.data_dir / sid
        if not base.is_dir():
            raise HTTPException(status_code=404, detail="unknown session")
        return {"session_id": sid, "result": await asyncio.to_thread(compact_fully, base)}

    @app.websocket("/ingest/{session_id}")
    async def ingest(ws: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
        await ws.accept()  # Starlette cannot send a custom close code before accept
        if not _token_ok(token, app.state.settings):
            await ws.close(code=CLOSE_BAD_TOKEN)
            return
        sid = parse_session_id(session_id)
        if sid is None:  # reject before any file is created
            await ws.close(code=CLOSE_BAD_REQUEST)
            return

        store: SessionStore = app.state.store
        sess = await store.get_or_open(sid)
        sess.connections += 1
        since_ack = 0
        try:
            await ws.send_json({"type": "hello", "schema_version": SCHEMA_VERSION, "next_seq": sess.next_seq})
            while True:
                frame = await ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                recv_ms = now_ms()  # once per batch, when the frame arrives
                text = frame.get("text")
                if text is None:
                    await ws.send_json({"type": "nack", "reason": "text frames only"})
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "nack", "reason": "invalid json"})
                    continue
                if not isinstance(payload, dict):
                    await ws.send_json({"type": "nack", "reason": "expected a json object"})
                    continue
                if payload.get("schema_version") != SCHEMA_VERSION:
                    await ws.close(code=CLOSE_BAD_REQUEST)
                    return
                kind = payload.get("type")
                model = {"imu_batch": ImuBatch, "event": Event}.get(kind)
                if model is None:
                    await ws.send_json({"type": "nack", "reason": f"unknown type {kind!r}"})
                    continue
                try:
                    msg = model.model_validate(payload)
                except ValidationError as exc:
                    first = exc.errors()[0]
                    reason = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
                    await ws.send_json({"type": "nack", "reason": reason})
                    continue
                if parse_session_id(msg.session_id) != sid:
                    await ws.close(code=CLOSE_BAD_REQUEST)
                    return

                async with sess.lock:
                    sess.accept(msg, recv_ms)
                    if sess.needs_flush():
                        await asyncio.to_thread(sess.flush_due)
                    last = sess.next_seq - 1
                since_ack += 1
                if since_ack >= ACK_EVERY:
                    since_ack = 0
                    await ws.send_json({"type": "ack", "seq": last})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sess.connections -= 1
            sess.last_activity = time.monotonic()
            if sess.stopped and sess.connections == 0:
                await store.close_session(sid)

    return app


app = create_app()
