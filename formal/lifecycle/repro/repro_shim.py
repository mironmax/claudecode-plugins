"""The `kg mcp` shim against a server that fails mid-call (Shim.lean M1-M3).

Real: cli/mcp_shim.py forward/relay/post. Stubbed: the server, by a TCP
listener on a sandbox port that decides how the call fails:
  A  reads the whole request (the server applied it) and drops the connection
     without a reply, as a crash does: the shim answers with an error and does
     not send the request again (M1: at most once);
  B  never accepts: the connection waits in the backlog until the listener
     closes, as at a graceful stop: the shim answers with an error, and the
     request was never read (M3: a restart can cost an error reply; M2 holds
     only when no crash is involved, so the agent cannot tell A from B).
"""
import os, socket, sys, tempfile, threading, time
from pathlib import Path

home = tempfile.mkdtemp(prefix="kg-shim-")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
os.environ.update({"HOME": home, "KG_HTTP_PORT": str(PORT), "XDG_STATE_HOME": home + "/state",
                   "KG_STORAGE_ROOT": home + "/storage"})
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "cli"))
import mcp_shim  # noqa: E402

emitted = []
mcp_shim.emit = emitted.append
mcp_shim.log = lambda text: None
LINE = (b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"kg_put_node",'
        b'"arguments":{"session_id":"s","level":"user","id":"n","gist":"g"}}}')
ok = True


def case_a():
    global ok
    received = []
    lst = socket.socket()
    lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lst.bind(("127.0.0.1", PORT))
    lst.listen(8)

    def serve():
        while True:
            try:
                conn, _ = lst.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            head, _, body = data.partition(b"\r\n\r\n")
            length = int([l for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0].split(b":")[1])
            while len(body) < length:
                body += conn.recv(65536)
            received.append(body)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            conn.close()   # reset, no reply: the server died after applying

    threading.Thread(target=serve, daemon=True).start()
    emitted.clear()
    mcp_shim.forward(LINE)
    time.sleep(0.3)
    lst.close()
    reply = emitted[-1] if emitted else None
    print(f"A  server read the call {len(received)} time(s), then reset; shim answered: {reply}")
    good = len(received) == 1 and reply and "error" in reply
    print("   " + ("ok: one delivery, an error reply (no resend)" if good else "BUG: the call was sent twice"))
    ok &= bool(good)


def case_b():
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    mcp_shim.kg.PORT = lst.getsockname()[1]
    lst.listen(8)               # never accepted
    emitted.clear()
    t = threading.Thread(target=mcp_shim.forward, args=(LINE,))
    t.start()
    time.sleep(0.5)
    lst.close()                 # graceful stop: listener closes, backlog reset
    t.join(10)
    reply = emitted[-1] if emitted else None
    print(f"B  connection reset in the backlog (never read); shim answered: {reply}")
    print("   observed: an error reply for a call that was never applied (M3); not retried, since the shim "
          "cannot tell this reset from A's")


case_a()
case_b()
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
