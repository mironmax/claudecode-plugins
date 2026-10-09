#!/usr/bin/env python3
"""Run one maintenance prompt through Antigravity CLI as a tool-less agent.

agy has no per-run settings: a headless run uses the user's own settings,
grants and plugins. Scope comes from a workspace agent with `tools: []`,
which leaves the model only the MCP tools and task tracking. An agent that
fails to load is silently replaced by the default agent with every tool,
and the stream's init event still names the requested agent (1.2.16). So
the agent must be listed by `/agents` before the run, the CLI log must not
report a fallback once it starts, and the workspace is an empty directory
of its own: never the storage root, never a project. Which kg tools a run
may call is the user's grant in settings.json (leave deletions on Ask and
a headless run cannot delete).

The prompt arrives on stdin; the last lines of stdout reach chores.jsonl.
Exit 3: the agent did not load or fell back; otherwise agy's own code.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

AGENT = "kg-maintainer"
AGENT_MD = f"""---
name: {AGENT}
description: Knowledge-graph maintenance through the kg MCP tools only.
tools: []
---
You maintain a knowledge graph through its kg_* MCP tools. You have no other
tools and need none. Do the task you are given, then stop.
"""
# Only this wording: other log lines also say "falling back to default".
FALLBACK_MARKERS = ("not found, falling back to default",)


def agent_listed(binary: str, workspace: Path) -> bool:
    proc = subprocess.run([binary, "-p", "/agents", "--output-format", "json"],
                          cwd=workspace, capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    try:
        agents = json.loads(proc.stdout)["command"]["data"]["agents"]
    except (ValueError, KeyError, TypeError):
        return False
    return AGENT in agents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--model")
    parser.add_argument("--workspace", type=Path, default=Path.cwd() / "agy-runner")
    args = parser.parse_args()
    prompt = sys.stdin.read()

    agent_dir = args.workspace / ".agents" / "agents" / AGENT
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.md").write_text(AGENT_MD)
    if not agent_listed(args.bin, args.workspace):
        print(f"refused: agent {AGENT} did not load (agy -p /agents)")
        return 3

    log = args.workspace / "cli.log"
    log.write_text("")
    cmd = [args.bin, "--agent", AGENT, "--effort", args.effort,
           "--input-format", "stream-json", "--output-format", "stream-json",
           "--log-file", str(log)]
    if args.model:
        cmd += ["--model", args.model]
    started = {}

    def stop(signum, _frame):
        # The server ends a timed-out run with TERM to this wrapper's group.
        # agy leads a session of its own (so a fallback can kill all of it),
        # which also puts it beyond that group: take it down from here.
        if "proc" in started:
            try:
                os.killpg(started["proc"].pid, signal.SIGKILL)
            except OSError:
                pass
        os.write(1, b"stopped: the run was terminated\n")
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    proc = started["proc"] = subprocess.Popen(
        cmd, cwd=args.workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    proc.stdin.write(json.dumps({"event": "user", "message": {"content": prompt}}) + "\n")
    proc.stdin.close()

    fell_back = threading.Event()

    def watch_log():
        while proc.poll() is None:
            try:
                text = log.read_text(errors="replace")
            except OSError:
                text = ""
            if any(marker in text for marker in FALLBACK_MARKERS):
                fell_back.set()
                os.killpg(proc.pid, signal.SIGKILL)
                return
            time.sleep(0.05)

    threading.Thread(target=watch_log, daemon=True).start()
    result = {}
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "result":
            result = event.get("result") or {}
    rc = proc.wait()
    if fell_back.is_set():
        print("refused: agy fell back to its default agent; run killed")
        return 3
    denied = result.get("denied_actions") or []
    print(json.dumps({"status": result.get("status"), "turns": result.get("num_turns"),
                      "denied_actions": denied}))
    print((result.get("response") or "").strip()[-300:])
    return rc


if __name__ == "__main__":
    sys.exit(main())
