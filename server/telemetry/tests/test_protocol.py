from __future__ import annotations

import json
import time

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.writer import list_parts

from .conftest import TOKEN, start_server, stop_server, wait_for
from .helpers import SID, batch, event


def recv(ws):
    return json.loads(ws.recv(timeout=5))


def open_ws(srv, sid=SID, token=TOKEN):
    ws = connect(srv.ws_url(sid, token))
    return ws


def close_code(ws) -> int:
    with pytest.raises(ConnectionClosed) as info:
        ws.recv(timeout=5)
    return info.value.rcvd.code


def read_events(srv, sid=SID):
    import pyarrow.parquet as pq
    parts = list_parts(srv.root / "data" / sid / "events")
    return [r for p in parts for r in pq.read_table(p).to_pylist()]


def test_health_needs_no_token(srv):
    code, body = srv.get("/health")
    assert code == 200 and body["status"] == "ok" and "version" in body


def test_sessions_requires_token(srv):
    assert srv.get("/sessions")[0] == 401
    assert srv.get(f"/sessions?token={TOKEN}")[0] == 200


def test_bad_token_closes_4401(srv):
    assert close_code(open_ws(srv, token="wrong")) == 4401
    assert close_code(open_ws(srv, token=None)) == 4401


def test_bad_session_id_closes_4400_and_creates_no_file(srv):
    ws = open_ws(srv, sid="not-a-uuid")
    assert close_code(ws) == 4400
    assert not (srv.root / "data" / "not-a-uuid").exists()


def test_hello_then_reconnect_resumes_next_seq(srv):
    with open_ws(srv) as ws:
        assert recv(ws) == {"type": "hello", "schema_version": 1, "next_seq": 0}
        for seq in range(3):
            ws.send(json.dumps(batch(seq)))
    wait_for(lambda: srv.get(f"/sessions?token={TOKEN}")[1][0]["last_seq"] == 2)
    with open_ws(srv) as ws:
        assert recv(ws)["next_seq"] == 3


def test_duplicates_ignored(srv):
    with open_ws(srv) as ws:
        recv(ws)
        for seq in (0, 1, 1, 0, 2):
            ws.send(json.dumps(batch(seq)))
    row = lambda: srv.get(f"/sessions?token={TOKEN}")[1][0]
    wait_for(lambda: row()["last_seq"] == 2)
    assert row()["imu_rows"] == 18


def test_gap_writes_warning_event(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send(json.dumps(batch(0)))
        ws.send(json.dumps(batch(3)))
    evs = wait_for(lambda: read_events(srv))
    w = next(e for e in evs if e["event"] == "warning")
    assert json.loads(w["detail_json"]) == {"message": "seq_gap", "expected": 1, "got": 3}


def test_ack_every_50_messages(srv):
    with open_ws(srv) as ws:
        recv(ws)
        for seq in range(50):
            ws.send(json.dumps(batch(seq, n=1)))
        assert recv(ws) == {"type": "ack", "seq": 49}


def test_bad_message_gets_nack_and_socket_stays_open(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send("{not json")
        assert recv(ws)["type"] == "nack"
        bad = batch(0)
        del bad["samples"][0]["timestamp_ns"]
        ws.send(json.dumps(bad))
        assert recv(ws)["type"] == "nack"
        ws.send(json.dumps({**batch(0), "type": "mystery"}))
        assert recv(ws)["type"] == "nack"
        ws.send(json.dumps(batch(0)))                      # still works
        wait_for(lambda: srv.get(f"/sessions?token={TOKEN}")[1][0]["last_seq"] == 0)


def test_unknown_schema_version_closes_4400(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send(json.dumps(batch(0, schema_version=99)))
        assert close_code(ws) == 4400


def test_body_session_id_mismatch_closes_4400(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send(json.dumps(batch(0, sid="11111111-1111-4111-8111-111111111111")))
        assert close_code(ws) == 4400


def test_timer_flush_makes_data_durable_while_idle(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send(json.dumps(batch(0)))
        wait_for(lambda: list_parts(srv.root / "data" / SID / "imu"), timeout=5)   # part_seconds=0.5


def test_session_stopped_then_disconnect_finalizes_and_compacts(srv):
    with open_ws(srv) as ws:
        recv(ws)
        ws.send(json.dumps(batch(0)))
        ws.send(json.dumps(event(1, "session_stopped", {"reason": "user"})))
    base = srv.root / "data" / SID
    wait_for(lambda: (base / "imu.parquet").exists() and not list_parts(base / "imu"), timeout=8)
    meta = json.loads((base / "session.json").read_text())
    assert meta["ended_recv_wall_ms"] is not None and meta["counts"]["imu_rows"] == 6


def test_idle_session_closes(tmp_path):
    s = start_server(tmp_path, idle_seconds=1.0)
    try:
        with open_ws(s) as ws:
            recv(ws)
            ws.send(json.dumps(batch(0)))
        wait_for(lambda: (tmp_path / "data" / SID / "imu.parquet").exists(), timeout=10)
    finally:
        stop_server(s)


def test_server_restart_resumes_from_disk(tmp_path):
    s = start_server(tmp_path)
    with open_ws(s) as ws:
        recv(ws)
        for seq in range(4):
            ws.send(json.dumps(batch(seq)))
    wait_for(lambda: s.get(f"/sessions?token={TOKEN}")[1][0]["last_seq"] == 3)
    stop_server(s)                                           # shutdown flush path
    s2 = start_server(tmp_path)
    try:
        with open_ws(s2) as ws:
            assert recv(ws)["next_seq"] == 4
    finally:
        stop_server(s2)


def test_health_responsive_under_parallel_sessions(srv):
    import uuid
    socks = []
    for _ in range(20):
        sid = str(uuid.uuid4())
        ws = open_ws(srv, sid=sid)
        recv(ws)
        for seq in range(5):
            ws.send(json.dumps(batch(seq, sid=sid)))
        socks.append(ws)
    t0 = time.time()
    assert srv.get("/health")[0] == 200
    assert time.time() - t0 < 1.0
    for ws in socks:
        ws.close()
