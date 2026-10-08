#!/usr/bin/env python3
"""Native hook transport. Only the exact event-result JSON goes to stdout."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


def output(text):
    return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]}


def request(base, path, payload=None, timeout=2):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def unavailable(event, base):
    """The hook's reply when the server did not answer; None once it was
    started and the event should be retried."""
    if event != "SessionStart":
        return {}
    try:
        request(base, "/health")
    except Exception:
        kg = shutil.which("kg") or str(Path.home() / ".local/bin/kg")
        if not os.access(kg, os.X_OK):
            return output("KG memory is not set up on this machine: the knowledge-graph plugin "
                          "needs the `kg` command, which runs the memory server for every "
                          "harness. Offer to install it now. If the user agrees, follow the "
                          "Install section of the kg-ops skill: `uv tool install kg-memory`, then "
                          "`kg setup --plan`, applying only the items the user accepts. Memory "
                          "loads from the next session; until then the kg_* tools are "
                          "unavailable, so proceed without memory.")
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        breadcrumb = state / "knowledge-graph/last_start_error"
        if breadcrumb.exists():
            cause = next((line[7:] for line in breadcrumb.read_text().splitlines()
                          if line.startswith("cause: ")), "cause not recorded")
            return output(f"KG memory server failed to start: {cause}. "
                          "Offer the remedy: run `kg doctor`, then `kg start`.")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_")}
        subprocess.Popen([kg, "start"], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        # The event's own timeout is 8s; a start through kg takes a few.
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                request(base, "/health")
                return None
            except Exception:
                pass
        return output("KG memory server is starting. If the kg_* tools stay offline, "
                      "run `kg doctor`.")
    return output("The running KG server predates the Antigravity adapter. Restart it "
                  "(kg restart), then start a new conversation. See the plugin's "
                  "ANTIGRAVITY.md.")


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    reply, data, payload = {}, {}, {}
    base = f"http://{os.environ.get('KG_HTTP_HOST', '127.0.0.1')}:{os.environ.get('KG_HTTP_PORT', '8765')}"
    if not os.environ.get("KG_CHORE") and event in ("SessionStart", "PreInvocation", "PostToolUse"):
        try:
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) <= 1024 * 1024:
                payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("conversationId"):
                try:
                    data = request(base, "/api/antigravity/hook/" + event, payload)
                except (urllib.error.URLError, TimeoutError, OSError):
                    reply = unavailable(event, base)
                    if reply is None:  # started just now
                        data = request(base, "/api/antigravity/hook/" + event, payload)
                if data:
                    reply = data.get("output") or {}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
            pass
    # The server's internal receipt fields must never enter the hook schema.
    sys.stdout.write(json.dumps(reply or {}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    # No dequeue on GET/prepare. A failed write never acknowledges delivery.
    if data.get("delivery_id"):
        try:
            request(base, "/api/antigravity/ack", {
                "conversationId": payload["conversationId"],
                "delivery_id": data["delivery_id"],
            }, timeout=1)
        except Exception:
            pass  # Retry this packet on the next hook; duplication is safe.


if __name__ == "__main__":
    main()
