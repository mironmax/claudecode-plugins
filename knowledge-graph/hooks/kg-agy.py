#!/usr/bin/env python3
"""Native hook transport. Only the exact event-result JSON goes to stdout."""

import json
import os
from pathlib import Path
import subprocess
import sys
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
    if event != "SessionStart":
        return {}
    try:
        request(base, "/health")
    except Exception:
        plugin = Path(__file__).resolve().parents[1]
        manage = plugin / "server/manage_server.sh"
        breadcrumb = plugin / "server/.last_start_error"
        if breadcrumb.exists():
            cause = next((line[7:] for line in breadcrumb.read_text().splitlines()
                          if line.startswith("cause: ")), "cause not recorded")
            return output(f"KG memory server failed to start: {cause}. "
                          f"Check {breadcrumb}; repair its environment before retrying.")
        if not manage.exists():
            return output("KG memory server is offline and its start script is missing. "
                          "Check the knowledge-graph plugin installation.")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_")}
        subprocess.Popen(["bash", str(manage), "start"], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return output("KG memory server was offline; starting it in the background. "
                      "First setup can take about a minute. After it answers "
                      f"{base}/health, start a new Antigravity conversation to connect "
                      "the kg_* MCP tools.")
    return output("The running KG server does not provide the Antigravity adapter. "
                  "Use the server from the Antigravity branch, then start a new conversation. "
                  "See the plugin's Antigravity setup guide.")


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
                data = request(base, "/api/antigravity/hook/" + event, payload)
                reply = data.get("output") or {}
        except (urllib.error.URLError, TimeoutError, OSError):
            reply = unavailable(event, base)
        except (ValueError, TypeError):
            pass
    # The server's internal receipt fields must never enter the hook schema.
    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
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
