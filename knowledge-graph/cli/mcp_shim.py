"""kg mcp — the memory's MCP tools over stdio, for any harness.

Every harness can launch a stdio server, so this is the one MCP entry point:
it starts the shared HTTP server when it is down and forwards each JSON-RPC
message to it. The server runs stateless with JSON responses, so one message
is one POST and nothing else (sessions, event streams) has to be bridged.

A refused connection means the server is restarting or gone: wait for it to
come back, start it if it does not, then retry. The harness keeps its tools
throughout instead of losing them until a manual reconnect.
"""

import contextlib
import http.client
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

try:
    from . import kg
except ImportError:  # run as a script from a checkout
    import kg

# A restart in progress answers again within seconds; starting a second copy
# meanwhile would only fail on the port and leave a false breadcrumb behind.
RESTART_GRACE = 8
CALL_TIMEOUT = 300

_out_lock = threading.Lock()
_start_lock = threading.Lock()
_client = {"agent": "kg-mcp", "protocol": None}


def log(text: str) -> None:
    print(f"kg mcp: {text}", file=sys.stderr, flush=True)


def emit(message: dict | list) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def ensure_server(grace: float = RESTART_GRACE) -> bool:
    with _start_lock:
        if kg.wait(lambda: kg.health() is not None, grace):
            return True
        log("server is down, starting it")
        with contextlib.redirect_stdout(sys.stderr):  # stdout is the MCP channel
            kg.start()
        return kg.health() is not None


def post(body: bytes) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "User-Agent": _client["agent"]}
    if _client["protocol"]:
        headers["MCP-Protocol-Version"] = _client["protocol"]
    conn = http.client.HTTPConnection(kg.HOST, kg.PORT, timeout=CALL_TIMEOUT)
    try:
        conn.request("POST", "/", body, headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def forward(line: bytes) -> None:
    try:
        message = json.loads(line)
    except ValueError:
        emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        return
    is_init = isinstance(message, dict) and message.get("method") == "initialize"
    if is_init:
        # The harness names itself only here; the server tells harnesses
        # apart by User-Agent, as it would on a direct HTTP connection.
        info = (message.get("params") or {}).get("clientInfo") or {}
        if info.get("name"):
            _client["agent"] = f"{info['name']}/{info.get('version', '0')} (kg mcp)"
    request_id = message.get("id") if isinstance(message, dict) else None
    try:
        relay(line, message, request_id, is_init)
    except Exception as exc:  # a lost answer would hang the harness's call
        log(f"forwarding failed: {exc!r}")
        if request_id is not None:
            emit({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32603, "message": f"kg mcp: {exc}"}})


def relay(line: bytes, message, request_id, is_init: bool) -> None:
    status, body = None, b""
    for attempt in range(2):
        try:
            status, body = post(line)
            break
        # Only a refused connection is safe to retry: a reset may come after
        # the server applied the call, and a write must not land twice.
        except ConnectionRefusedError as exc:
            if attempt or not ensure_server():
                status, body = None, str(exc).encode()
                break
    if request_id is None and isinstance(message, dict):
        return  # a notification: nothing to answer
    try:
        reply = json.loads(body) if body else None
    except ValueError:
        reply = None
    if isinstance(reply, (dict, list)):
        if is_init and isinstance(reply, dict):
            _client["protocol"] = (reply.get("result") or {}).get("protocolVersion")
        emit(reply)
        return
    reason = f"HTTP {status}" if status else "server unreachable"
    emit({"jsonrpc": "2.0", "id": request_id,
          "error": {"code": -32603,
                    "message": f"KG memory server: {reason}: {body.decode(errors='replace')[:300]}"
                               " — run `kg doctor`"}})


def run() -> int:
    if kg.health() is None:
        ensure_server(grace=0)  # before the harness's first call, not during it
    # Harnesses issue tool calls in parallel; serialising them here would
    # let one long read hold up the rest.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for line in sys.stdin.buffer:
            if line.strip():
                pool.submit(forward, line.strip())
    return 0
