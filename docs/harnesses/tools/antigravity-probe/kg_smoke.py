#!/usr/bin/env python3
"""Run the native KG plugin against an actual agy CLI and a local mock model.

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
    root = Path(tempfile.mkdtemp(prefix="kg-antigravity-smoke-"))
    scratch = root / "home"
    (scratch / ".gemini/antigravity-cli").mkdir(parents=True)
    (scratch / "work/proj").mkdir(parents=True)
    (scratch / "work/proj/component.py").write_text("# Native file-recall probe\n")
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
    large = "SMOKE_LARGE_BEGIN-" + "界🙂memory" * 8000 + "-SMOKE_LARGE_END"
    writer = None

    def seed(nid, gist, notes=None, touches=None):
        api("/api/nodes", {"level": "project", "id": nid, "gist": gist,
            "notes": notes or [], "touches": touches or [], "session_id": writer})

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
            (root / f"model-{len(model_requests):03}.json").write_text(json.dumps(body, indent=2))
            if "countTokens" in self.path:
                self.send({"totalTokens": 100})
                return
            names = [f["name"] for t in body.get("tools", []) for f in t.get("functionDeclarations", [])]
            calls = [p["functionCall"] for c in body.get("contents", [])
                     for p in c.get("parts", []) if "functionCall" in p]
            read_name = next((n for n in names if n.endswith("_kg_read")), None)
            sync_name = next((n for n in names if n.endswith("_kg_sync")), None)
            sid_match = re.search(r"session_id: ([0-9a-f]{8})", json.dumps(body))
            last = json.dumps((body.get("contents") or [{}])[-1], ensure_ascii=False)
            if not names:
                parts = [{"text": "Mock title"}]
            elif not read_name or not sid_match:
                parts = [{"text": "MOCK_KG_MISSING_NATIVE_SETUP"}]
            else:
                sid = sid_match.group(1)
                if not calls:
                    name, arguments = read_name, {"session_id": sid}
                elif "KG delivery continues" in last:
                    name, arguments = sync_name, {"session_id": sid}
                elif not any(c.get("args", {}).get("id") == "smoke-large-memory" for c in calls):
                    name, arguments = read_name, {"session_id": sid, "id": "smoke-large-memory"}
                elif not any(c["name"] == "view_file" for c in calls):
                    seed("smoke-file-memory", "SMOKE_FILE_CONTEXT", touches=["component.py"])
                    name, arguments = "view_file", {"AbsolutePath": project + "/component.py",
                                                     "toolAction": "Read component.py"}
                else:
                    name = None
                parts = ([{"functionCall": {"name": name, "args": {
                    "toolSummary": "KG smoke probe", **arguments}}}] if name
                    else [{"text": "MOCK_KG_SMOKE_DONE"}])
            response = {"candidates": [{"content": {"role": "model", "parts": parts},
                "finishReason": "STOP", "index": 0}], "usageMetadata": {
                "promptTokenCount": 100, "candidatesTokenCount": 10, "totalTokenCount": 110},
                "modelVersion": "mock"}
            self.send("data: " + json.dumps(response) + "\r\n\r\n", "text/event-stream") if "alt=sse" in self.path else self.send(response)

    model = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    env.update(GEMINI_API_KEY="dummy-kg-smoke", GOOGLE_GEMINI_BASE_URL=f"http://127.0.0.1:{model.server_port}")
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
        writer = api("/api/sessions/register?" + urllib.parse.urlencode({"project_path": project}), {})["session_id"]
        for i in range(45):
            seed(f"smoke-graph-{i}", f"Independent memory {i}: " + chr(65 + i % 26) * 280)
        seed("smoke-large-memory", "Large node snapshot", [large])
        install = subprocess.run(wrap + [args.agy, "plugin", "install", str(staged)], env=env,
                                 capture_output=True, text=True, timeout=30)
        (root / "install.txt").write_text(install.stdout + install.stderr)
        if install.returncode:
            raise RuntimeError(f"Plugin install failed; see {root}/install.txt")
        config_file = scratch / ".gemini/config/plugins/knowledge-graph/mcp_config.json"
        config = json.loads(config_file.read_text())
        config["mcpServers"]["kg"]["serverUrl"] = base + "/"
        config_file.write_text(json.dumps(config))
        version = subprocess.run(wrap + [args.agy, "--version"], env=env, capture_output=True, text=True, timeout=15)
        hooks = subprocess.run(wrap + [args.agy, "-p", "/hooks", "--output-format", "json"],
                               env=env, capture_output=True, text=True, timeout=20)
        (root / "hooks.json").write_text(hooks.stdout)
        run = subprocess.run(wrap + [args.agy, "-p", "Read KG, read smoke-large-memory in full, then inspect component.py.",
            "--output-format", "stream-json", "--print-timeout", "45s", "--log-file", str(root / "cli.log")],
            env=env, capture_output=True, text=True, timeout=60)
        (root / "stdout.jsonl").write_text(run.stdout)
        (root / "stderr.txt").write_text(run.stderr)
        input_text = json.dumps(model_requests, ensure_ascii=False)
        final_input = json.dumps(model_requests[-1], ensure_ascii=False) if model_requests else ""
        sessions = json.loads((root / "storage/sessions.json").read_text())
        cli_sessions = [data for data in sessions.values() if data.get("harness") == "antigravity-cli"]
        checks = {
            "native_hooks_loaded": all(event in hooks.stdout for event in ("SessionStart", "PreInvocation", "PostToolUse")),
            "eager_descriptions": "Read the knowledge graph. First call:" in input_text,
            "plugin_rule_loaded": "A queued receipt alone does not" in input_text,
            "full_graph_delivered": "Full graph now in context" in input_text,
            "large_unicode_end_delivered": "SMOKE_LARGE_BEGIN" in input_text and "SMOKE_LARGE_END" in input_text,
            "unicode_payload_complete_in_final_input": final_input.count("界") == 8000 and final_input.count("🙂") == 8000,
            "chunk_continuation_used": "KG delivery continues" in input_text,
            "post_tool_recall_delivered": "SMOKE_FILE_CONTEXT" in input_text,
            "no_spilled_or_truncated_reply": "output was truncated" not in input_text and "The output was large and was saved" not in input_text,
            "queue_drained_and_read_committed": any(not data.get("agy_pending") and data.get("full_read_ts")
                and "smoke-large-memory" in data.get("read_at", {}) for data in cli_sessions),
            "turn_completed": run.returncode == 0 and "MOCK_KG_SMOKE_DONE" in run.stdout,
        }
        print(json.dumps({"version": version.stdout.strip(), "artifacts": str(root),
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
