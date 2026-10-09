#!/usr/bin/env python3
"""Project-bound WebSocket subscriptions for the visual editor (formal F6).

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_ws_subscriptions.py

Drives the real REST + /ws routes, ConnectionManager and store broadcasts.
"Not received" is checked with a user-level marker write: user changes go to
every connection, so if the next message is the marker, the change before it
was not delivered.

Covers:
  1. a connection subscribed to project P gets P's changes (tagged with P's
     root) and not project Q's; user-level changes still reach it
  2. a write addressed by project_path (the editor's own) reaches P's subscribers
  3. switching the subscription: the reply echoes `sub`; afterwards only the
     new project's changes arrive
  4. a subscription names a project as REST does (safe_project_path): a path
     outside $HOME or a non-string is refused and the connection is unbound
  5. compatibility: a connection that never subscribes (an older page) gets no
     project changes, as before; one whose session is registered to P still
     gets P's changes; a new connection starts unsubscribed
  6. the subscription is checked right before each send: a connection that
     switches project while an earlier send is awaited does not get the
     old project's change
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.pop("KG_CHORES", None)
logging.disable(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402

from mcp_http.rest import create_rest_api  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402
from mcp_http.websocket import ConnectionManager  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


# Project roots must live under $HOME (safe_project_path).
_HOME_TMP = tempfile.mkdtemp(dir=Path.home(), prefix="kg-test-ws-")
P = str(Path(_HOME_TMP, "proj-p").resolve())
Q = str(Path(_HOME_TMP, "proj-q").resolve())
os.makedirs(P)
os.makedirs(Q)

sm = HTTPSessionManager()
cm = ConnectionManager()


async def _cb(project_path, message, exclude):
    await cm.broadcast_to_project(project_path, message, exclude, sm)

store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, _cb)
client = TestClient(create_rest_api(store, sm, cm, "test"),
                    headers={"host": "127.0.0.1:8765"})
sid_p = sm.register(P)["session_id"]
sid_q = sm.register(Q)["session_id"]
_marks = 0


def put(level, nid, **kw):
    r = client.post("/api/nodes", json={"level": level, "id": nid, "gist": "g", **kw})
    assert r.status_code == 200, r.text


def changes(ws):
    """Write a user-level marker and return every change message delivered
    before it, as {node id or node_id: message}."""
    global _marks
    _marks += 1
    mark = f"marker-{_marks}"
    put("user", mark)
    got = {}
    while True:
        msg = ws.receive_json()
        key = msg.get("node", {}).get("id") or msg.get("node_id")
        if key == mark:
            return got
        got[key] = msg


def subscribe(ws, path, sub):
    """Send a subscribe; return the reply, or {} if the server sent none
    (a ping sent after it is answered in order, so a bare pong means none)."""
    ws.send_text(json.dumps({"type": "subscribe", "project_path": path, "sub": sub}))
    ws.send_text("ping")
    reply = ws.receive_json()
    if reply == {"type": "pong"}:
        return {}
    pong = ws.receive_json()
    assert pong == {"type": "pong"}, pong
    return reply


def test_bound_subscriber():
    print("1. a P subscriber gets P's changes, not Q's")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        reply = subscribe(ws, P, 1)
        check("reply confirms P's resolved root",
              reply == {"type": "subscribed", "sub": 1, "project_path": P}, reply)
        put("project", "p-node", session_id=sid_p)
        put("project", "q-node", session_id=sid_q)
        got = changes(ws)
        msg = got.get("p-node", {})
        check("P's node_updated delivered", msg.get("type") == "node_updated", got)
        check("it is tagged with P's root", msg.get("project_path") == P, msg)
        check("Q's change not delivered", "q-node" not in got, got)
        put("user", "u-node")
        msg = changes(ws).get("u-node", {})
        check("user-level change delivered, without a project tag",
              msg.get("level") == "user" and "project_path" not in msg, msg)


def test_project_path_write():
    print("2. a write addressed by project_path reaches P's subscribers")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        subscribe(ws, P + "/", 1)   # trailing slash: resolved as REST resolves it
        put("project", "editor-p-node", project_path=P)
        msg = changes(ws).get("editor-p-node", {})
        check("editor-style write delivered", msg.get("project_path") == P, msg)
        r = client.request("DELETE", "/api/nodes/project/editor-p-node",
                           params={"project_path": P})
        msg = changes(ws).get("editor-p-node", {})
        check("delete by project_path delivered", r.status_code == 200
              and msg.get("type") == "node_deleted" and msg.get("project_path") == P, msg)


def test_switch():
    print("3. switching the subscription")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        subscribe(ws, P, 1)
        reply = subscribe(ws, Q, 2)
        check("reply echoes the latest sub", reply.get("sub") == 2
              and reply.get("project_path") == Q, reply)
        put("project", "p-after-switch", session_id=sid_p)
        put("project", "q-after-switch", project_path=Q)
        got = changes(ws)
        check("old project's change not delivered", "p-after-switch" not in got, got)
        check("new project's change delivered",
              got.get("q-after-switch", {}).get("project_path") == Q, got)
        reply = subscribe(ws, None, 3)
        check("null subscribes to the user graph only",
              reply == {"type": "subscribed", "sub": 3, "project_path": None}, reply)
        put("project", "q-after-user", project_path=Q)
        got = changes(ws)
        check("no project change after subscribing to user only", got == {}, got)


def test_validation():
    print("4. a subscription names a project as REST does")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        subscribe(ws, P, 1)
        reply = subscribe(ws, "/etc/../etc", 2)
        check("path outside $HOME refused", reply.get("type") == "subscribe_error"
              and reply.get("sub") == 2, reply)
        put("project", "p-after-refusal", session_id=sid_p)
        got = changes(ws)
        check("refused subscription unbinds (fails closed)", got == {}, got)
        reply = subscribe(ws, ["not", "a", "path"], 3)
        check("non-string path refused", reply.get("type") == "subscribe_error", reply)
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "something-else"}))
        ws.send_text("ping")
        check("other text ignored, ping still answered",
              ws.receive_json() == {"type": "pong"})


def test_compatibility():
    print("5. connections that never subscribe")
    with client.websocket_connect("/ws") as ws:   # an older editor page
        ws.receive_json()
        put("project", "p-unsubscribed", session_id=sid_p)
        got = changes(ws)
        check("unsubscribed connection gets no project change", got == {}, got)
    with client.websocket_connect(f"/ws?session_id={sid_p}") as ws:
        ws.receive_json()
        put("project", "p-by-session", project_path=P)
        got = changes(ws)
        check("session registered to P still gets P's change", "p-by-session" in got, got)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        subscribe(ws, P, 1)
    subs = getattr(cm, "subscriptions", {})
    check("disconnect drops the subscription", hello["session_id"] not in subs, subs)


class _SlowSocket:
    """A connection whose send yields, running `during` inside the send."""
    def __init__(self, during=None):
        self.sent, self.during = [], during

    async def send_json(self, message):
        await asyncio.sleep(0)
        if self.during:
            self.during()
        self.sent.append(message)


def test_check_before_each_send():
    print("6. the subscription is checked right before each send")
    mgr = ConnectionManager()
    b = _SlowSocket()
    a = _SlowSocket(during=lambda: mgr.subscribe("b", Q))   # b switches while a's send runs
    mgr.active_connections.update(a=a, b=b)
    mgr.subscribe("a", P)
    mgr.subscribe("b", P)
    asyncio.run(mgr.broadcast_to_project(
        P, {"type": "node_updated", "level": "project", "project_path": P}, None, sm))
    check("a (still on P) got it", len(a.sent) == 1, a.sent)
    check("b (switched to Q during the broadcast) did not", b.sent == [], b.sent)


if __name__ == "__main__":
    try:
        for test in (test_bound_subscriber, test_project_path_write, test_switch,
                     test_validation, test_compatibility, test_check_before_each_send):
            try:
                test()
            except Exception as e:
                check(f"{test.__name__} ran", False, repr(e))
    finally:
        store.shutdown()
        shutil.rmtree(_HOME_TMP, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
