"""A logging stand-in for the Gemini API, for driving Antigravity CLI offline.

Antigravity CLI (`agy`) sends model requests to GOOGLE_GEMINI_BASE_URL when
settings.json has "modelProvider": "gemini" and GEMINI_API_KEY is set to any
non-empty value. Pointed here, every request body is saved as
<log_dir>/req-NNNN.json, so what the CLI put in front of the model (hook
injections included) can be read exactly. No account is needed.

Replies come from <log_dir>/scenario.json, read on every request:

    {"steps": [
        {"call": "view_file", "args": {"AbsolutePath": "/abs/path"}},
        {"text": "final answer"}
    ]}

Step N answers the model call that has N model turns before it in the
request. A requests without tools (title generation) gets a short text.
Built-in tools require toolSummary and toolAction; they are filled in.
A first user text containing SUBAGENT-PROMPT gets a plain text answer, so a
"self" subagent driven by the same scenario does not recurse.

Usage: python3 gemini_mock.py <port> <log_dir>
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[2]
os.makedirs(LOG, exist_ok=True)
_lock = threading.Lock()
_counter = [0]


def _scenario():
    try:
        with open(os.path.join(LOG, "scenario.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _reply_parts(body):
    if not body.get("tools"):
        return [{"text": "Mock title"}]
    contents = body.get("contents", [])
    first = next((p.get("text", "") for c in contents for p in c.get("parts", [])
                  if "text" in p), "")
    if "SUBAGENT-PROMPT" in first:
        return [{"text": "SUBAGENT-DONE"}]
    # Tool results come back under role "model" too: count only the model
    # turns that called a tool or answered in text.
    answered = sum(1 for c in contents if c.get("role") == "model" and any(
        "functionCall" in p or ("text" in p and not p.get("thought"))
        for p in c.get("parts", [])))
    steps = _scenario().get("steps", [])
    if answered < len(steps):
        step = steps[answered]
        if "call" in step:
            args = {"toolSummary": "probe", "toolAction": "probe", **step.get("args", {})}
            return [{"functionCall": {"name": step["call"], "args": args}}]
        return [{"text": step["text"]}]
    return [{"text": "MOCK-DONE"}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _record(self, raw):
        with _lock:
            _counter[0] += 1
            n = _counter[0]
        rec = {"i": n, "t": time.time(), "method": self.command, "path": self.path,
               "headers": dict(self.headers)}
        try:
            rec["body"] = json.loads(raw or b"{}")
        except ValueError:
            rec["raw"] = raw.decode("utf-8", "replace")
        with open(os.path.join(LOG, f"req-{n:04d}.json"), "w") as f:
            json.dump(rec, f, indent=1)
        return rec

    def _send(self, ctype, payload):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._record(b"")
        self._send("application/json", b'{"models": []}')

    def do_POST(self):
        rec = self._record(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if "countTokens" in self.path:
            self._send("application/json", b'{"totalTokens": 10}')
            return
        resp = {"candidates": [{"content": {"role": "model", "parts": _reply_parts(rec.get("body") or {})},
                                "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5,
                                  "totalTokenCount": 105},
                "modelVersion": "mock"}
        if "alt=sse" in self.path:
            self._send("text/event-stream", b"data: " + json.dumps(resp).encode() + b"\r\n\r\n")
        else:
            self._send("application/json", json.dumps(resp).encode())


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
