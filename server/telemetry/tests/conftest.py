from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app

TOKEN = "test-token-0123456789"


@dataclass
class Srv:
    port: int
    root: Path
    settings: Settings
    server: uvicorn.Server
    thread: threading.Thread

    def ws_url(self, sid: str, token: str | None = TOKEN) -> str:
        q = "" if token is None else f"?token={token}"
        return f"ws://127.0.0.1:{self.port}/ingest/{sid}{q}"

    def get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def post(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None


def start_server(root: Path, **overrides) -> Srv:
    kw = {"token": TOKEN, "data_root": root, "part_seconds": 0.5, "part_rows": 1000,
          "idle_seconds": 300.0}
    kw.update(overrides)
    settings = Settings(**kw)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=0,
                                           log_level="warning", ws_max_size=1048576))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    return Srv(port, root, settings, server, thread)


def stop_server(srv: Srv) -> None:
    srv.server.should_exit = True
    srv.thread.join(timeout=15)
    assert not srv.thread.is_alive(), "server did not shut down"


@pytest.fixture
def srv(tmp_path):
    s = start_server(tmp_path)
    yield s
    stop_server(s)


def wait_for(cond, timeout: float = 5.0, step: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")
