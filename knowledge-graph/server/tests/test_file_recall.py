#!/usr/bin/env python3
"""Self-contained regression tests for file-anchored recall.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_file_recall.py

FILE-ANCHORED RECALL. When a tool call reads or edits a file, the nodes whose
touches name it reach the session once, through the PostToolUse hook output.

Covers:
  1. Touch normalisation: relative, absolute, ~, 'path:12-40 (anchor)', URLs
  2. The touches index: build, reuse, invalidation on put/delete/rename
  3. Recall on Read/Edit/Write/MultiEdit/NotebookEdit: gist lines, archived
     nodes included and not promoted, seen-dedup, route 'file'
  4. Cap and char budget; ranking by node score, ties by recency
  5. Per-session throttle; withheld nodes stay unseen and arrive later
  6. Bash extraction: clear reads found, ambiguous commands ignored
  7. The recall.jsonl record, silences included
  8. The capture nudge still fires for an uncovered file; never both
  9. Never raises on a broken store or junk payloads; REST hook shape
 10. The eval harness over logs holding file recall records: python -m eval
     runs, file recall gets its own line, prompts stay prompts

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache — never touches
real graphs under ~/.knowledge-graph.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-", dir=str(Path.home() / ".cache"))
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from fastapi.testclient import TestClient  # noqa: E402

from core.constants import (  # noqa: E402
    FILE_RECALL_CHAR_BUDGET,
    FILE_RECALL_MAX_NODES,
    FILE_RECALL_MAX_PER_WINDOW,
    FILE_RECALL_REASON,
    RECALL_LOG_NAME,
    SURFACE_VIAS,
)
from mcp_http import file_recall  # noqa: E402
from mcp_http.ambient import handle_tool_event  # noqa: E402
from mcp_http.file_recall import _INDEX, bash_read_files, normalize_touch  # noqa: E402
from mcp_http.rest import create_rest_api  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402
from mcp_http.websocket import ConnectionManager  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "eval_v0940"

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


_CLEANUP = [_TMP_STORAGE]


def new_project():
    d = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    _CLEANUP.append(d)
    return d


def fresh():
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, broadcast_callback=None)
    file_recall.reset_throttle()
    return store, sm


def write(path, text="x\n"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    return str(path)


def event(store, sm, cwd, tool, tool_input, claude_sid="cs1"):
    return handle_tool_event(store, sm, {"cwd": cwd, "tool_name": tool,
                                         "session_id": claude_sid, "tool_input": tool_input})


def log_records():
    try:
        lines = (Path(_TMP_STORAGE) / RECALL_LOG_NAME).read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def file_records():
    return [r for r in log_records() if r.get("reason") == FILE_RECALL_REASON]


# --------------------------------------------------------------------------

def test_normalize_touch():
    print("touch normalisation:")
    home = str(Path.home().resolve())
    check("relative stays relative", normalize_touch("src/a.py") == "src/a.py")
    check("./ and duplicate slashes collapse", normalize_touch("./src//a.py") == "src/a.py")
    check("line range and anchor stripped",
          normalize_touch("src/a.py:12-40 (anchor)") == "src/a.py", normalize_touch("src/a.py:12-40 (anchor)"))
    check("symbol suffix stripped", normalize_touch("src/a.py:handle_event") == "src/a.py")
    check("fragment stripped", normalize_touch("docs/x.md#setup") == "docs/x.md")
    check("~ expands to the real home", normalize_touch("~/notes/x.md") == f"{home}/notes/x.md",
          normalize_touch("~/notes/x.md"))
    check("absolute stays absolute", normalize_touch("/etc/hosts") == os.path.realpath("/etc/hosts"))
    check("trailing annotation after a space dropped",
          normalize_touch("hooks/hooks.json — the matcher") == "hooks/hooks.json")
    check("URL is not a file", normalize_touch("https://example.com/a.py") is None)
    check("empty and junk are nothing",
          normalize_touch("") is None and normalize_touch(None) is None and normalize_touch(".") is None)


def test_index():
    print("touches index:")
    store, sm = fresh()
    proj = new_project()
    sid = sm.register(proj, claude_sid="cs1")["session_id"]
    store.put_node(level="project", node_id="engine-core", gist="Engine core loop",
                   touches=["src/engine.py:10-40 (main loop)"], session_id=sid)
    with store.lock:
        key = f"project:{os.path.realpath(proj)}"
        t1 = _INDEX.table(store, key, True)
        t1b = _INDEX.table(store, key, True)
    check("built lazily, normalised key", t1.get("src/engine.py") == ["engine-core"], t1)
    check("reused while nothing is written", t1 is t1b)

    store.put_node(level="project", node_id="engine-core", gist="Engine core loop",
                   touches=["src/engine2.py"], session_id=sid)
    with store.lock:
        t2 = _INDEX.table(store, key, True)
    check("a write invalidates it", "src/engine.py" not in t2 and t2.get("src/engine2.py") == ["engine-core"], t2)

    store.rename_node("engine-core", "engine-loop", level="project", session_id=sid)
    with store.lock:
        t3 = _INDEX.table(store, key, True)
    check("rename carries the id", t3.get("src/engine2.py") == ["engine-loop"], t3)

    store.delete_node("engine-loop", level="project", session_id=sid)
    with store.lock:
        t4 = _INDEX.table(store, key, True)
    check("delete drops it", not t4.get("src/engine2.py"), t4)

    store.put_node(level="user", node_id="rel-user-touch", gist="A user node with a relative touch",
                   touches=["README.md", "~/.cache/some-tool.conf"])
    with store.lock:
        tu = _INDEX.table(store, "user", False)
    check("user graph indexes only anchored paths",
          "README.md" not in tu and any(k.endswith("some-tool.conf") for k in tu), tu)
    store.delete_node("rel-user-touch", level="user")


def test_recall_basics():
    print("recall on file tools:")
    store, sm = fresh()
    proj = new_project()
    sid = sm.register(proj, claude_sid="cs1")["session_id"]
    f = write(os.path.join(proj, "src", "cache.py"))
    store.put_node(level="project", node_id="cache-invalidation",
                   gist="Cache must be flushed after every deploy", touches=["src/cache.py:1-30 (flush)"],
                   session_id=sid)
    store.put_node(level="project", node_id="cache-old-design",
                   gist="Old cache design used TTLs only", touches=[f], session_id=sid)
    key = f"project:{os.path.realpath(proj)}"
    with store.lock:
        store.graphs[key]["nodes"]["cache-old-design"]["_archived"] = True
        store._write_through(key)

    text = event(store, sm, proj, "Read", {"file_path": f})
    check("Read of a covered file recalls", text and "cache-invalidation" in text and
          "flushed after every deploy" in text, text)
    check("archived node included (absolute touch matched)", text and "cache-old-design" in text, text)
    check("header names the file", text and text.startswith("KG memory on src/cache.py:"), text)
    node = store.graphs[key]["nodes"]["cache-old-design"]
    check("surfacing does not promote", node.get("_archived") is True and "_last_read_ts" not in node, node)
    via = sm._sessions[sid].get("seen_via", {})
    check("marked seen via file", via.get("cache-invalidation") == "file"
          and via.get("cache-old-design") == "file", via)
    check("file is a surfaced route", "file" in SURFACE_VIAS)
    check("usefulness untouched", "_useful_ts" not in node)

    check("Edit of the same file stays silent (all seen)",
          event(store, sm, proj, "Edit", {"file_path": f, "old_string": "a", "new_string": "b"}) is None)

    g = write(os.path.join(proj, "src", "g.py"))
    n = write(os.path.join(proj, "nb", "a.ipynb"), "{}")
    m = write(os.path.join(proj, "src", "m.py"))
    w = os.path.join(proj, "src", "w.py")
    for nid, path in (("gee-node", g), ("nb-node", n), ("em-node", m), ("new-file-node", w)):
        store.put_node(level="project", node_id=nid, gist=f"About {nid}",
                       touches=[os.path.relpath(path, proj)], session_id=sid)
    file_recall.reset_throttle()
    check("Write recalls", "new-file-node" in (event(store, sm, proj, "Write", {"file_path": w, "content": ""}) or ""))
    check("NotebookEdit recalls via notebook_path",
          "nb-node" in (event(store, sm, proj, "NotebookEdit", {"notebook_path": n, "new_source": ""}) or ""))
    check("MultiEdit recalls", "em-node" in (event(store, sm, proj, "MultiEdit", {"file_path": m, "edits": []}) or ""))
    file_recall.reset_throttle()
    check("Bash cat recalls", "gee-node" in (event(store, sm, proj, "Bash", {"command": "cat src/g.py"}) or ""))
    check("uncovered file is silent", event(store, sm, proj, "Read",
                                            {"file_path": write(os.path.join(proj, "src", "none.py"))}) is None)
    check("unregistered session: silent",
          event(store, sm, new_project(), "Read", {"file_path": f}, claude_sid="nobody") is None)


def test_cap_budget_rank():
    print("cap, budget, rank:")
    store, sm = fresh()
    proj = new_project()
    sid = sm.register(proj, claude_sid="cs1")["session_id"]
    f = write(os.path.join(proj, "big.py"))
    for i in range(5):
        store.put_node(level="project", node_id=f"big-node-{i}", gist=f"Big node number {i}",
                       touches=["big.py"], session_id=sid)
        time.sleep(0.01)
    text = event(store, sm, proj, "Read", {"file_path": f})
    lines = (text or "").splitlines()[1:]
    check(f"capped at {FILE_RECALL_MAX_NODES}", len(lines) == FILE_RECALL_MAX_NODES, text)
    check("unscored (grace) nodes rank by recency, newest first",
          [ln.split("]")[1].split(":")[0].strip() for ln in lines] == ["big-node-4", "big-node-3", "big-node-2"],
          lines)
    rec = file_records()[-1]
    check("withheld count logged", rec.get("withheld") == 2, rec)
    text2 = event(store, sm, proj, "Read", {"file_path": f})
    check("the rest arrive on the next touch", text2 and "big-node-1" in text2 and "big-node-0" in text2, text2)

    # Score wins over recency once nodes are past grace: endorse the oldest.
    store2, sm2 = fresh()
    proj2 = new_project()
    sid2 = sm2.register(proj2, claude_sid="cs2")["session_id"]
    f2 = write(os.path.join(proj2, "ranked.py"))
    key2 = f"project:{os.path.realpath(proj2)}"
    for i in range(4):
        store2.put_node(level="project", node_id=f"ranked-node-{i}", gist=f"Ranked node {i}",
                        touches=["ranked.py"], session_id=sid2)
        time.sleep(0.01)
    with store2.lock:
        # Same write time for all, so recency cannot decide; the endorsement does.
        for nid, n in store2.graphs[key2]["nodes"].items():
            n["_created_ts"] = 0
            store2._versions[key2][f"node:{nid}"]["ts"] = 1_000_000
        store2.graphs[key2]["nodes"]["ranked-node-2"]["_useful_ts"] = [time.time()] * 3
    text3 = event(store2, sm2, proj2, "Read", {"file_path": f2}, claude_sid="cs2")
    first = (text3 or "\n\n").splitlines()[1]
    check("node score ranks first, past grace", "ranked-node-2" in first, text3)

    # Budget: long gists trim to fewer lines, never to none.
    store3, sm3 = fresh()
    proj3 = new_project()
    sid3 = sm3.register(proj3, claude_sid="cs3")["session_id"]
    f3 = write(os.path.join(proj3, "long.py"))
    for i in range(3):
        store3.put_node(level="project", node_id=f"long-node-{i}", gist=("word " * 110).strip(),
                        touches=["long.py"], session_id=sid3)
    text4 = event(store3, sm3, proj3, "Read", {"file_path": f3}, claude_sid="cs3")
    n4 = len((text4 or "").splitlines()) - 1
    check("budget trims lines", 1 <= n4 < 3, n4)
    check("within budget when more than one line", n4 == 1 or len(text4) <= FILE_RECALL_CHAR_BUDGET)
    single = FILE_RECALL_CHAR_BUDGET + 200
    store3.put_node(level="project", node_id="huge-node", gist="y" * single,
                    touches=["huge.py"], session_id=sid3)
    text5 = event(store3, sm3, proj3, "Read", {"file_path": write(os.path.join(proj3, "huge.py"))},
                  claude_sid="cs3")
    check("one line always survives", text5 and "huge-node" in text5, text5)


def test_throttle():
    print("throttle:")
    store, sm = fresh()
    proj = new_project()
    sid = sm.register(proj, claude_sid="cs1")["session_id"]
    paths = []
    for i in range(FILE_RECALL_MAX_PER_WINDOW + 1):
        p = write(os.path.join(proj, f"t{i}.py"))
        paths.append(p)
        store.put_node(level="project", node_id=f"throttle-node-{i}", gist=f"Throttle node {i}",
                       touches=[f"t{i}.py"], session_id=sid)
    spoke = [bool(event(store, sm, proj, "Read", {"file_path": p})) for p in paths]
    check(f"{FILE_RECALL_MAX_PER_WINDOW} injections, then silence",
          spoke == [True] * FILE_RECALL_MAX_PER_WINDOW + [False], spoke)
    last = f"throttle-node-{FILE_RECALL_MAX_PER_WINDOW}"
    rec = file_records()[-1]
    check("throttled decision logged with the withheld node",
          rec["outcome"] == "throttled" and [n["id"] for n in rec["nodes"]] == [last], rec)
    check("withheld node stays unseen", last not in sm.get_seen(sid))
    # Another session is not throttled by this one.
    sm.register(proj, claude_sid="cs-other")
    check("throttle is per session",
          bool(event(store, sm, proj, "Read", {"file_path": paths[-1]}, claude_sid="cs-other")))
    # The window passes.
    with file_recall._throttle_lock:
        file_recall._injections[sid] = [t - 10_000 for t in file_recall._injections[sid]]
    text = event(store, sm, proj, "Read", {"file_path": paths[-1]})
    check("after the window it arrives", text and last in text, text)


def test_bash_extraction():
    print("bash extraction:")
    proj = new_project()
    a = write(os.path.join(proj, "a.py"))
    b = write(os.path.join(proj, "b.py"))
    c = write(os.path.join(proj, "c.json"), "{}")
    sub_a = write(os.path.join(proj, "sub", "a.py"))
    home_rel = "~/" + os.path.relpath(a, str(Path.home().resolve()))
    write(os.path.join(proj, "foo"))                 # a file named like a pattern

    def got(cmd):
        return bash_read_files(cmd, proj)

    positives = [
        ("cat a.py", [a]),
        ("cat -n a.py b.py", [a, b]),
        ("head -n 20 a.py", [a]),
        ("head -20 a.py", [a]),
        ("head -n5 a.py", [a]),
        ("tail -f b.py", [b]),
        ("sed -n '1,80p' a.py", [a]),
        ("sed -ne 's/x/y/p' a.py", [a]),
        ("less a.py", [a]),
        ("grep -n foo a.py b.py", [a, b]),
        ("grep -e foo -e bar a.py", [a]),
        ("grep -rn 'a|b' a.py sub", [a]),
        ("jq .x c.json", [c]),
        ("jq -r --arg k v '.x' c.json", [c]),
        ("jq . < c.json", [c]),
        ("cat a.py | head -5", [a]),
        ("cat a.py 2>/dev/null", [a]),
        (f"cat {a}", [a]),
        (f"cat {home_rel}", [a]),
        ("/bin/cat a.py", [a]),
        ("LC_ALL=C grep foo a.py", [a]),
        ("git status && cat a.py; echo done", [a]),
        ("cd sub && cat " + a, [a]),
    ]
    for cmd, want in positives:
        check(f"reads: {cmd!r}", got(cmd) == want, got(cmd))

    negatives = [
        "sed 's/x/y/' a.py",            # not sed -n
        "sed -n -i 's/x/y/' a.py",      # in-place edit
        "grep foo",                     # the pattern is not a file
        "cat missing.py",               # does not exist
        "cat $HOME/a.py",               # expansion
        "cat *.py",                     # glob
        "cat `echo a.py`",
        "echo a.py",
        "rm a.py",
        "python a.py",
        "cd sub && cat a.py",           # relative after cd: ambiguous
        "cat <<EOF\na.py\nEOF",
        "cat 'a.py",                    # unbalanced quote
        "cat sub",                      # a directory
        "jq --args '.x' a.py",
        "cat a.py > b.py; echo",        # b.py is written, not read
        "",
    ]
    for cmd in negatives:
        want = [a] if cmd.startswith("cat a.py >") else []
        check(f"ignores: {cmd!r}", got(cmd) == want, got(cmd))
    check("grep pattern that names a file is still the pattern", got("grep foo b.py") == [b], got("grep foo b.py"))
    check("non-string command", bash_read_files(None, proj) == [])
    check("sub/a.py never invented from a relative operand", sub_a not in got("cd sub && cat a.py"))


def test_log_record():
    print("log record:")
    store, sm = fresh()
    proj = new_project()
    sid = sm.register(proj, claude_sid="cs-log")["session_id"]
    f = write(os.path.join(proj, "logged.py"))
    store.put_node(level="project", node_id="logged-node", gist="Logged node gist",
                   touches=["logged.py"], session_id=sid)
    before = len(file_records())
    event(store, sm, proj, "Read", {"file_path": write(os.path.join(proj, "bare.py"))}, "cs-log")
    text = event(store, sm, proj, "Read", {"file_path": f}, "cs-log")
    event(store, sm, proj, "Edit", {"file_path": f}, "cs-log")
    recs = file_records()[before:]
    check("one record per decision, silences included",
          [r["outcome"] for r in recs] == ["no_nodes", "injected", "all_seen"], recs)
    inj = recs[1]
    check("record fields", inj["kg_session"] == sid and inj["claude_session"] == "cs-log"
          and inj["tool"] == "Read" and inj["files"] == ["logged.py"]
          and inj["chars"] == len(text) and inj["project"] == os.path.realpath(proj), inj)
    check("node entries", inj["nodes"] == [{"id": "logged-node", "level": "project", "seen": False,
                                             "archived": False, "file": "logged.py"}], inj["nodes"])
    check("no prompt terms on file records", all("terms" not in r for r in recs))
    check("all_seen names what was seen", recs[2]["nodes"][0]["seen"] is True, recs[2])
    n = len(log_records())
    event(store, sm, proj, "WebFetch", {"url": "https://example.invalid/x"}, "cs-log")
    event(store, sm, proj, "Bash", {"command": "ls -la"}, "cs-log")
    check("non-file events write no recall record", len(log_records()) == n)


def test_nudge_path():
    print("capture nudge:")
    store, sm = fresh()
    proj = new_project()
    sm.register(proj, claude_sid="n1")
    f = write(os.path.join(proj, "src", "uncovered.py"))
    check("first read silent", event(store, sm, proj, "Read", {"file_path": f}, "n1") is None)
    sm.register(proj, claude_sid="n2")
    nudge = event(store, sm, proj, "Read", {"file_path": f}, "n2")
    check("uncovered file re-read in a second session nudges",
          nudge and nudge.startswith("KG capture:") and "src/uncovered.py" in nudge, nudge)

    g = write(os.path.join(proj, "src", "covered.py"))
    sid3 = sm.register(proj, claude_sid="n3")["session_id"]
    store.put_node(level="project", node_id="covered-node", gist="Covered node gist",
                   touches=["src/covered.py"], session_id=sid3)
    event(store, sm, proj, "Read", {"file_path": g}, "n3")   # recalled in n3
    sm.register(proj, claude_sid="n4")
    both = event(store, sm, proj, "Read", {"file_path": g}, "n4")
    check("covered file in a second session: recall, no nudge",
          both and "covered-node" in both and "KG capture" not in both, both)
    again = event(store, sm, proj, "Read", {"file_path": g}, "n4")
    check("covered and all seen: silent, still no nudge", again is None, again)
    check("edits do not count toward the nudge",
          event(store, sm, proj, "Edit", {"file_path": write(os.path.join(proj, "e.py"))}, "n1") is None
          and event(store, sm, proj, "Edit", {"file_path": os.path.join(proj, "e.py")}, "n2") is None)


def test_never_raises():
    print("never raises:")
    proj = new_project()

    class Broken:
        lock = None

        def __getattr__(self, name):
            raise RuntimeError("store down")

    sm = HTTPSessionManager()
    sm.register(proj, claude_sid="br")
    f = write(os.path.join(proj, "x.py"))
    try:
        out = event(Broken(), sm, proj, "Edit", {"file_path": f}, "br")
        check("broken store: no exception, no output", out is None, out)
    except Exception as e:
        check("broken store: no exception", False, repr(e))
    store, sm2 = fresh()
    sm2.register(proj, claude_sid="br")
    for payload in ({"cwd": proj, "tool_name": "Bash", "tool_input": "cat x.py"},
                    {"cwd": proj, "tool_name": "Bash", "tool_input": {"command": 42}},
                    {"cwd": proj, "tool_name": "Edit", "tool_input": {"file_path": ["x"]}},
                    {"cwd": proj, "tool_name": "Read"}):
        try:
            handle_tool_event(store, sm2, payload)
            check(f"junk payload tolerated: {payload.get('tool_input')!r}", True)
        except Exception as e:
            check(f"junk payload tolerated: {payload.get('tool_input')!r}", False, repr(e))

    sid = sm2.register(proj, claude_sid="rest")["session_id"]
    store.put_node(level="project", node_id="rest-node", gist="Rest node gist", touches=["x.py"],
                   session_id=sid)
    client = TestClient(create_rest_api(store, sm2, ConnectionManager(), version="test"))
    r = client.post("/api/tool_event", json={"cwd": proj, "tool_name": "Edit", "session_id": "rest",
                                             "tool_input": {"file_path": f}}).json()
    hso = r.get("hookSpecificOutput", {})
    check("REST: PostToolUse additionalContext", hso.get("hookEventName") == "PostToolUse"
          and "rest-node" in hso.get("additionalContext", ""), r)
    r2 = client.post("/api/tool_event", json={"cwd": proj, "tool_name": "Edit", "session_id": "rest",
                                              "tool_input": {"file_path": f}}).json()
    check("REST: {} when nothing to say", r2 == {}, r2)

    hooks = json.loads((Path(SERVER).parent / "hooks" / "hooks.json").read_text())
    matcher = hooks["hooks"]["PostToolUse"][0]["matcher"].split("|")
    check("hook matcher widened", {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash",
                                   "WebFetch", "WebSearch"} == set(matcher), matcher)


def run_cli(*args):
    env = dict(os.environ, KG_STORAGE_ROOT=_TMP_STORAGE)
    return subprocess.run([sys.executable, "-m", "eval", *args], cwd=SERVER, env=env,
                          capture_output=True, text=True, timeout=120)


def test_eval_harness():
    print("eval harness:")
    from eval.data import load_logs
    from eval.replay import Replayer, file_injected_ids
    from eval.data import GraphHistory
    from eval.report import build_report

    # Production writes the records: a file injection before the full read,
    # then a prompt, then an endorsement of the file-surfaced node.
    root = Path(tempfile.mkdtemp(prefix="kg-eval-root-", dir=str(Path.home() / ".cache")))
    _CLEANUP.append(str(root))
    old = os.environ["KG_STORAGE_ROOT"]
    os.environ["KG_STORAGE_ROOT"] = str(root)
    try:
        from mcp_http.ambient import build_prompt_recall
        store, sm = fresh()
        proj = new_project()
        sid = sm.register(proj, claude_sid="ev1")["session_id"]
        f = write(os.path.join(proj, "deploy.py"))
        store.put_node(level="project", node_id="deploy-cache-flush",
                       gist="Deploy must flush the edge cache before switching traffic",
                       touches=["deploy.py"], session_id=sid)
        event(store, sm, proj, "Read", {"file_path": f}, "ev1")
        event(store, sm, proj, "Bash", {"command": "cat deploy.py"}, "ev1")
        time.sleep(0.01)
        sm.mark_full_read(sid)
        build_prompt_recall(store, sm, proj, "why does deploy need an edge cache flush first",
                            claude_sid="ev1")
        time.sleep(0.01)
        store.mark_useful(["deploy-cache-flush"], sid)
    finally:
        os.environ["KG_STORAGE_ROOT"] = old

    useful = [json.loads(x) for x in (root / "useful.jsonl").read_text().splitlines()]
    check("endorsement records the file route as surfaced",
          useful[-1]["via"] == "file" and useful[-1]["surfaced"] is True, useful[-1])

    recall, useful = load_logs(root)
    reasons = [r["reason"] for r in recall]
    check("log holds file and prompt records",
          reasons.count(FILE_RECALL_REASON) == 2 and "all_seen" in reasons, reasons)
    rp = Replayer(GraphHistory(root), recall, {sid: os.path.realpath(proj)})
    s = rp.sessions.get(sid)
    prompt = next(r for r in recall if r["reason"] == "all_seen")
    check("full read is marked by the prompt, not the earlier file record",
          s is not None and s.full_read_ts == prompt["ts"], s and s.full_read_ts)
    check("file-injected ids are seen for replay",
          s is not None and "deploy-cache-flush" in s.other_seen[0], s and s.other_seen)
    check("file_injected_ids reads only injections",
          [file_injected_ids(r) for r in recall if r["reason"] == FILE_RECALL_REASON]
          == [["deploy-cache-flush"], []])

    rep = build_report(root)
    d = rep["descriptive"]["overall"]
    check("prompt counts exclude file records", d["prompts_logged"] == 1 and d["ranked"] == 1, d)
    fr = d["file_recall"]
    check("file recall described apart",
          fr["events"] == 2 and fr["outcomes"] == {"injected": 1, "all_seen": 1}
          and fr["injected_nodes"] == 1 and fr["injected_then_endorsed"] == 1, fr)
    check("endorsement via file", d["endorsements"]["via"] == {"file": 1}
          and d["endorsements"]["dug_up"] == 0, d["endorsements"])

    out = run_cli("--root", str(root))
    check("python -m eval runs over production file records", out.returncode == 0, out.stderr[-800:])
    check("file recall has its own line",
          "file recall (tool events, not replayable): 2 (" in out.stdout, out.stdout)
    js = run_cli("--root", str(root), "--json")
    check("--json too", js.returncode == 0 and json.loads(js.stdout)["descriptive"]["overall"]
          ["file_recall"]["events"] == 2, js.stderr[-800:])

    # The checked-in v0.9.40 fixture with file records mixed in: every
    # number it already asserts stays put, and the CLI still runs.
    mixed = Path(tempfile.mkdtemp(prefix="kg-eval-mixed-", dir=str(Path.home() / ".cache")))
    _CLEANUP.append(str(mixed))
    shutil.copytree(FIXTURE, mixed, dirs_exist_ok=True)
    with open(mixed / "recall.jsonl", "a") as fh:
        for ts, outcome, nodes in ((1001, "injected", [{"id": "python-venv-selfheal", "level": "user",
                                                        "seen": False, "archived": True, "file": "setup.py"}]),
                                   (1002, "no_nodes", []),
                                   (2001, "throttled", [{"id": "demo-flaky-login-test", "level": "project",
                                                         "seen": False, "archived": True, "file": "t.py"}])):
            fh.write(json.dumps({"ts": ts, "reason": FILE_RECALL_REASON, "project": "/home/dev/demo",
                                 "claude_session": "c", "kg_session": "kg-s1" if ts < 2000 else "kg-s2",
                                 "outcome": outcome, "tool": "Read", "files": ["x"], "nodes": nodes}) + "\n")
    base = build_report(FIXTURE)["descriptive"]["overall"]
    got = build_report(mixed)["descriptive"]["overall"]
    same = {k: base[k] for k in base if k != "file_recall"} == {k: got[k] for k in got if k != "file_recall"}
    check("fixture prompt statistics unchanged by file records", same,
          {k: (base[k], got[k]) for k in base if base.get(k) != got.get(k)})
    check("fixture file line counts", got["file_recall"]["events"] == 3
          and got["file_recall"]["outcomes"] == {"injected": 1, "no_nodes": 1, "throttled": 1},
          got["file_recall"])
    base_rep = build_report(FIXTURE)["replay"]["consistency"]
    mixed_rep = build_report(mixed)["replay"]["consistency"]
    check("consistency check unchanged", base_rep == mixed_rep, (base_rep, mixed_rep))
    out = run_cli("--root", str(mixed))
    check("python -m eval runs over the mixed fixture",
          out.returncode == 0 and "file recall (tool events, not replayable): 3" in out.stdout,
          out.stderr[-800:] or out.stdout)
    out = run_cli("--no-root", "--recall", str(mixed / "recall.jsonl"),
                  "--useful", str(mixed / "useful.jsonl"))
    check("--no-root over the mixed logs", out.returncode == 0, out.stderr[-800:])


def main():
    print("=== file-anchored recall tests ===")
    try:
        test_normalize_touch()
        test_index()
        test_recall_basics()
        test_cap_budget_rank()
        test_throttle()
        test_bash_extraction()
        test_log_record()
        test_nudge_path()
        test_never_raises()
        test_eval_harness()
    finally:
        for d in _CLEANUP:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
