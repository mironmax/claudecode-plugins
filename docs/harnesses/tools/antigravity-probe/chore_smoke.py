#!/usr/bin/env python3
"""Run a maintenance chore through the Antigravity runner, against a real agy.

The wrapper (server/mcp_http/agy_chore.py) runs as the dispatcher runs it:
from the storage root, with KG_CHORE=1, the prompt on stdin. A local mock
Gemini model records which tools the CLI offers and calls kg_read once.

Linux + bubblewrap; requires agy and the repository's server venv. The real
home is read-only, overlaid with a scratch home without changing HOME. No
account, real credentials or production graph is used. Artifacts stay in /tmp.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from stage_plugin import stage_plugin


def main():
    repo = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy", default=shutil.which("agy"))
    parser.add_argument("--python", default=str(repo / "knowledge-graph/server/venv/bin/python"))
    args = parser.parse_args()
    if not args.agy or not shutil.which("bwrap"):
        parser.error("Provide --agy <binary> and install bubblewrap")
    root = Path(tempfile.mkdtemp(prefix="kg-antigravity-chore-"))
    scratch = root / "home"
    (scratch / ".gemini/antigravity-cli").mkdir(parents=True)
    (scratch / "work/proj").mkdir(parents=True)
    project = str(Path.home() / "work/proj")
    settings = {"modelProvider": "gemini", "enableTelemetry": False,
                "permissions": {"allow": ["mcp(knowledge-graph_kg/*)", "read"]}}
    (scratch / ".gemini/antigravity-cli/settings.json").write_text(json.dumps(settings))
    staged = root / "plugin-src/knowledge-graph"
    wrap = ["bwrap", "--die-with-parent", "--ro-bind", "/", "/", "--bind", str(scratch), str(Path.home()),
            "--ro-bind", str(repo), str(repo), "--bind", str(root), str(root),
            "--dev", "/dev", "--proc", "/proc", "--chdir", project]
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LANG") if k in os.environ}
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    stage_plugin(repo, staged, port)
    env.update(KG_HTTP_PORT=str(port), KG_STORAGE_ROOT=str(root / "storage"),
               KG_AUTOCOMMIT_INTERVAL="0", KG_LOG_LEVEL="WARNING")

    def api(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(base + path, data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    model_requests = []

    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def send(self, value, content_type="application/json"):
            data = (value if isinstance(value, str) else json.dumps(value)).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.send({"models": []})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            model_requests.append(body)
            if "countTokens" in self.path:
                self.send({"totalTokens": 100})
                return
            names = [f["name"] for t in body.get("tools", []) for f in t.get("functionDeclarations", [])]
            calls = [p["functionCall"] for c in body.get("contents", [])
                     for p in c.get("parts", []) if "functionCall" in p]
            read_name = next((n for n in names if n.endswith("_kg_read")), None)
            if not names:
                parts = [{"text": "Mock title"}]
            elif read_name and not calls:
                parts = [{"functionCall": {"name": read_name, "args": {
                    "toolSummary": "chore probe", "cwd": project}}}]
            else:
                parts = [{"text": "MOCK_CHORE_DONE"}]
            response = {"candidates": [{"content": {"role": "model", "parts": parts},
                "finishReason": "STOP", "index": 0}], "usageMetadata": {
                "promptTokenCount": 100, "candidatesTokenCount": 10, "totalTokenCount": 110},
                "modelVersion": "mock"}
            self.send("data: " + json.dumps(response) + "\r\n\r\n", "text/event-stream") if "alt=sse" in self.path else self.send(response)

    model = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    env.update(GEMINI_API_KEY="dummy-kg-smoke", GOOGLE_GEMINI_BASE_URL=f"http://127.0.0.1:{model.server_port}")
    storage = root / "storage"
    storage.mkdir()
    server_log = (root / "server.log").open("w")
    server = subprocess.Popen(wrap + [args.python, str(repo / "knowledge-graph/server/mcp_streamable_server.py")],
                              env=env, stdout=server_log, stderr=server_log)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError(f"Server exited; see {root}/server.log")
            try:
                api("/health")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not become healthy")
        install = subprocess.run(wrap + [args.agy, "plugin", "install", str(staged)], env=env,
                                 capture_output=True, text=True, timeout=30)
        if install.returncode:
            raise RuntimeError(install.stdout + install.stderr)
        config_file = scratch / ".gemini/config/plugins/knowledge-graph/mcp_config.json"
        config = json.loads(config_file.read_text())
        kg_entry = config["mcpServers"]["kg"]
        for key in ("command", "args", "env"):
            kg_entry.pop(key, None)
        kg_entry["serverUrl"] = base + "/"
        config_file.write_text(json.dumps(config))
        chore_env = dict(env, KG_CHORE="1")
        wrapper = repo / "knowledge-graph/server/mcp_http/agy_chore.py"
        chore = subprocess.run(wrap[:-1] + [str(storage), args.python, str(wrapper), "--bin", args.agy],
                               input=f"Maintenance chore probe. Call kg_read(cwd='{project}').",
                               env=chore_env, capture_output=True, text=True, timeout=90)
        (root / "chore.txt").write_text(chore.stdout + chore.stderr)
        usage = subprocess.run(wrap + [args.agy, "-p", "/usage", "--output-format", "json"],
                               env=env, capture_output=True, text=True, timeout=30)
        offered = sorted({f["name"] for b in model_requests for t in b.get("tools", [])
                          for f in t.get("functionDeclarations", [])})
        sessions = json.loads((storage / "sessions.json").read_text()) if (storage / "sessions.json").exists() else {}
        forbidden = {"run_command", "view_file", "write_to_file", "replace_file_content",
                     "multi_replace_file_content", "read_url_content", "search_web", "invoke_subagent"}
        checks = {
            "agent_loaded_and_run_succeeded": chore.returncode == 0 and '"status": "SUCCESS"' in chore.stdout,
            "only_mcp_tools_offered": bool(offered) and not forbidden & set(offered),
            "kg_tools_offered": any(n.endswith("_kg_read") for n in offered),
            "kg_read_reached_server": any(d.get("project_path") == project for d in sessions.values()),
            # KG_CHORE=1: the plugin's hooks never reach the server in a chore.
            "hooks_stood_down": not any(d.get("agy_hooks_seen") for d in sessions.values()),
            "workspace_is_its_own": (storage / "agy-runner/.agents/agents/kg-maintainer/agent.md").exists(),
            "api_key_usage_has_no_groups": '"groups":[]' in usage.stdout.replace(" ", "") or '"groups":null' in usage.stdout.replace(" ", ""),
        }
        print(json.dumps({"artifacts": str(root), "offered_tools": offered,
                          "model_requests": len(model_requests), "checks": checks}, indent=2))
        if not all(checks.values()):
            raise SystemExit(1)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server_log.close()
        model.shutdown()


if __name__ == "__main__":
    main()
