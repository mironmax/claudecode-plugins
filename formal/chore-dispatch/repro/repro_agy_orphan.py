"""A timed-out Antigravity run leaves agy running (Tiers.lean P1).

Real: chore_dispatch._spawn (Popen, watcher, timeout kill of the process
group), AntigravityRunner.command and the agy_chore.py wrapper.
Stubbed: the agy binary (a script that lists the kg-maintainer agent, then
works for 30 s as a wedged run would) and the timeout (1 s instead of 420).
Two runs back to back, each started only once the watcher has cleared
_running, as maybe_dispatch requires.
"""
import os, sys, tempfile, threading, time
from pathlib import Path
from types import SimpleNamespace

root = tempfile.mkdtemp(prefix="kg-agy-orphan-")
os.environ["KG_STORAGE_ROOT"] = root
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from mcp_http import chore_dispatch as cd  # noqa: E402

FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if args[:2] == ["-p", "/agents"]:
    print(json.dumps({"command": {"data": {"agents": ["kg-maintainer"]}}}))
    sys.exit(0)
sys.stdin.readline()
open(os.environ["AGY_PIDS"], "a").write(f"{os.getpid()}\n")
time.sleep(30)          # a long model turn or tool call
'''
fake = Path(root) / "agy"
fake.write_text(FAKE_AGY)
fake.chmod(0o755)
pids = Path(root) / "agy.pids"
os.environ["AGY_PIDS"] = str(pids)

store = SimpleNamespace(lock=threading.Lock(), graphs={}, _progress={})


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def run_once(n: int) -> None:
    job = {"tier": "chore", "runner": "antigravity", "bin": str(fake),
           "settings": str(cd.SHIPPED_SETTINGS), "timeout_s": 1, "graph": "user",
           "level": "user", "kind": "edge", "targets": ["n1"], "debt_before": 1.0}
    cd._running.set()                     # what maybe_dispatch does under _lock
    cd._spawn({}, job, "TIDY", store)
    deadline = time.time() + 30
    while cd._running.is_set() and time.time() < deadline:
        time.sleep(0.05)
    print(f"run {n}: watcher cleared _running after the timeout")


run_once(1)
time.sleep(0.5)
run_once(2)
time.sleep(0.5)
listed = [int(x) for x in pids.read_text().split()] if pids.exists() else []
running = [p for p in listed if alive(p)]
print(f"agy processes started: {len(listed)}; still running after both runs 'ended': {len(running)}")
for p in running:
    os.kill(p, 9)
ok = not running
print("PASS" if ok else
      "BUG: the timeout killed the wrapper's group only; agy, in a session of its own, "
      "keeps running while the next run starts")
sys.exit(0 if ok else 1)
