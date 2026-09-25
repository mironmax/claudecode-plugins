#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.40 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0940.py

RETRIEVAL EVALUATION HARNESS. server/eval replays the recall decision log
against the graphs as they were, under named ranking variants, and scores
each by the endorsement log. The checked-in fixture (tests/fixtures/
eval_v0940: five nodes, nine recall lines, seven endorsements) proves the
ARITHMETIC — counts, rates, windows, seen-set reconstruction, variant
bookkeeping — and nothing about retrieval quality: it is far too small and
too contrived for any of its numbers to say whether a ranking is good.

Covers:
  1. Importing the harness starts nothing: no server module, no thread
  2. Descriptive counts on the fixture, overall and per project; time window
  3. Replay bookkeeping with hand-written variants: active nodes are seen,
     a variant's own surfacing and other routes' sightings are seen, and
     production's injections are not credited to a variant
  4. Recovered / kept / extra / dropped arithmetic
  5. Graph state from git: known, approx, current, file not yet created
  6. End to end: production writes the logs, the baseline reproduces every
     logged decision whose graph state is known
  7. The harness never writes to the storage root
  8. The CLI: text and --json, unknown variant, module:function variants
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-", dir=str(Path.home() / ".cache"))
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

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


_CLEANUP: list[str] = [tempfile.mkdtemp(prefix="kg-eval-scratch-", dir=str(Path.home() / ".cache"))]


def tree_digest(root: Path) -> dict:
    """Path -> sha256 for every file under root, .git included."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def git(root, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(date)} +0000"
    subprocess.run(["git", "-C", str(root), *args], check=True, env=env,
                   capture_output=True)


def commit_all(root, date, msg="c"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", msg, date=date)


# --------------------------------------------------------------------------

def test_imports_start_nothing():
    print("imports:")
    threads = threading.active_count()
    from eval import data, replay, report, variants  # noqa: F401
    from eval.report import build_report
    build_report(FIXTURE)
    check("no server module imported", "mcp_streamable_server" not in sys.modules)
    check("no thread started", threading.active_count() == threads,
          threading.enumerate())


def test_descriptive():
    print("descriptive:")
    from eval.report import build_report
    rep = build_report(FIXTURE)
    d = rep["descriptive"]["overall"]
    check("every recall line read", d["prompts_logged"] == 9, d["prompts_logged"])
    check("ranked = records that carry terms", d["ranked"] == 5, d["ranked"])
    check("fire rate of logged", d["fire_rate_all"] == round(2 / 9, 4), d["fire_rate_all"])
    check("fire rate of ranked", d["fire_rate_ranked"] == 0.4, d["fire_rate_ranked"])
    check("payload chars", d["payload_chars"] == {"n": 2, "mean": 120.0, "median": 120.0, "max": 140},
          d["payload_chars"])
    e = d["endorsements"]
    check("refusals excluded and counted", e["accepted"] == 6 and e["refused"] == {"duplicate": 1}, e)
    check("via distribution", e["via"] == {"read": 1, "ambient": 1, "search": 1, "unknown": 1,
                                          "preload": 1, "never shown": 1}, e["via"])
    check("dug up = search, read, never shown; unknown is not a miss",
          e["dug_up"] == 3 and e["dug_up_share"] == 0.5, e)
    check("injected then endorsed: later, same session",
          d["injected_nodes"] == 2 and d["injected_then_endorsed"] == 1, d)
    projects = rep["descriptive"]["projects"]
    check("per project split", set(projects) == {"/home/dev/demo", "(no project)"}
          and projects["(no project)"]["prompts_logged"] == 1, list(projects))

    windowed = build_report(FIXTURE, since=1500)
    check("window start inclusive", windowed["inputs"]["recall_records"] == 4
          and windowed["inputs"]["useful_records"] == 2, windowed["inputs"])
    wd = windowed["descriptive"]["overall"]
    check("endorsement log start ignores the window",
          wd["injected_before_log"] == 0 and wd["injected_nodes"] >= 1, wd)
    with tempfile.TemporaryDirectory() as tmp:
        late = Path(tmp) / "useful.jsonl"
        late.write_text("".join(line for line in (FIXTURE / "useful.jsonl").read_text().splitlines(True)
                                if json.loads(line)["ts"] >= 1500))
        ld = build_report(FIXTURE, useful_path=late)["descriptive"]["overall"]
        # kg-s1 ended (1035) before the trimmed log's first record (2030);
        # kg-s2 injected at 2000 but was still active at 2030, so it counts.
        check("sessions over before the endorsement log leave the denominator",
              ld["injected_before_log"] == 1 and 0 < ld["injected_nodes"] < d["injected_nodes"], ld)
    windowed = build_report(FIXTURE, until=1020)
    check("window end exclusive", windowed["inputs"]["recall_records"] == 2, windowed["inputs"])


def test_replay_bookkeeping():
    print("replay bookkeeping:")
    from eval.data import GraphHistory, load_logs
    from eval.replay import Replayer, score_variant
    from eval.variants import variant

    @variant("t-everything")
    def everything(terms, graphs, seen):
        return list(graphs.user["nodes"]) + list(graphs.project["nodes"])

    @variant("t-oracle")
    def oracle(terms, graphs, seen):
        if "login" in terms:
            return ["demo-flaky-login-test"]
        if "venv" in terms:
            return ["python-venv-selfheal"]
        return []

    # r4 (deploy) flags demo-cache-invalidation seen — but production had
    # injected it at r2, so for a variant that never did, it is still unseen.
    @variant("t-cache-late")
    def cache_late(terms, graphs, seen):
        return ["demo-cache-invalidation"] if "deploy" in terms else []

    # r8 (rebase) flags demo-flaky-login-test seen and production never
    # injected it in kg-s2: another route showed it, so it is seen for all.
    @variant("t-flaky-late")
    def flaky_late(terms, graphs, seen):
        return ["demo-flaky-login-test"] if "rebase" in terms else []

    recall, useful = load_logs(FIXTURE)
    rp = Replayer(GraphHistory(FIXTURE), recall)
    check("sessions without ranked prompts are not replayed",
          set(rp.sessions) == {"kg-s1", "kg-s2"}, list(rp.sessions))

    ev = rp.run_variant("t-everything")
    surfaced = {sid: [i for st in steps for i in st["surfaced"]] for sid, steps in ev.items()}
    archived = {"python-venv-selfheal", "demo-cache-invalidation", "demo-flaky-login-test"}
    check("active nodes count as seen from the full read",
          all(set(v) == archived for v in surfaced.values()), surfaced)
    check("a variant's own surfacing is seen afterwards",
          [bool(st["surfaced"]) for st in ev["kg-s1"]] == [True, False, False], ev["kg-s1"])

    from eval.data import session_projects
    check("registered project comes from the endorsement log",
          session_projects(FIXTURE, useful) == {"kg-s1": "/home/dev/demo", "kg-s2": "/home/dev/demo"})
    moved = Replayer(GraphHistory(FIXTURE), recall, {"kg-s1": "/home/dev/other"})
    ev_moved = moved.run_variant("t-everything")["kg-s1"]
    check("the registered project wins over the hook's cwd",
          [i for st in ev_moved for i in st["surfaced"]] == ["python-venv-selfheal"], ev_moved)

    s = score_variant(ev, useful, None)
    check("graph state without git is current", s["graph_state"] == {"current": 5}, s["graph_state"])
    check("injections and surfaced nodes", s["injections"] == 2 and s["surfaced_nodes"] == 6, s)

    s = score_variant(rp.run_variant("t-oracle"), useful, None)
    dug, amb = s["dug_up"], s["ambient_endorsed"]
    check("dug-up recovered before its endorsement",
          dug["recovered"] == 1 and dug["recovered_ids"] == ["demo-flaky-login-test"], dug)
    check("an endorsement before any prompt is not replayable",
          dug["total"] == 3 and dug["replayable"] == 2, dug)
    check("ambient endorsement the variant misses is lost",
          amb == {"total": 1, "replayable": 1, "kept": 0, "lost_ids": ["demo-cache-invalidation"]}, amb)

    late = rp.run_variant("t-cache-late")
    s = score_variant(late, useful, None)
    check("production's injection is not a variant's seen",
          s["injections"] == 1 and s["ambient_endorsed"]["kept"] == 1, s)

    s = score_variant(rp.run_variant("t-flaky-late"), useful, None)
    check("a node another route showed is seen for every variant", s["injections"] == 0, s)


def test_vs_baseline_arithmetic():
    print("vs baseline:")
    from eval.replay import score_variant

    def steps(*surf):
        return [{"ts": 10 * (i + 1), "surfaced": list(x), "states": {"user": "known"},
                 "logged_reason": "no_hits"} for i, x in enumerate(surf)]
    base = {"s": steps(["a"], [], ["b"], [])}
    var = {"s": steps([], ["c"], ["b"], ["d"])}
    useful = [{"ts": 35, "kg_session": "s", "id": "d", "via": "search"},
              {"ts": 25, "kg_session": "s", "id": "c", "via": "read"}]
    r = score_variant(var, useful, base)
    check("extra and dropped counted per prompt",
          r["vs_baseline"] == {"extra_injections": 2, "dropped_injections": 1}, r["vs_baseline"])
    check("surfaced after endorsement is not a recovery",
          r["dug_up"]["recovered_ids"] == ["c"], r["dug_up"])


def test_graph_history():
    print("graph history:")
    from eval.data import APPROX, CURRENT, KNOWN, GraphHistory

    root = Path(tempfile.mkdtemp(prefix="kg-eval-git-", dir=str(Path.home() / ".cache")))
    _CLEANUP.append(str(root))
    g = lambda ids: json.dumps({"nodes": {i: {"gist": i} for i in ids}, "edges": {}})
    git(root, "init", "-q")
    (root / "user.json").write_text(g(["v1"]))
    commit_all(root, 1000)
    (root / "user.json").write_text(g(["v2"]))
    (root / "late.json").write_text(g(["x"]))
    commit_all(root, 2000)
    (root / "other.txt").write_text("unrelated")
    commit_all(root, 3000)
    (root / "user.json").write_text(g(["v3-uncommitted"]))

    h = GraphHistory(root)
    before = tree_digest(root)
    at = lambda rel, ts: (sorted(h.at(rel, ts)[0]["nodes"]), h.at(rel, ts)[1])
    check("before history: current graph, flagged", at("user.json", 500) == (["v3-uncommitted"], CURRENT),
          at("user.json", 500))
    check("next commit changes the file: approx", at("user.json", 1500) == (["v1"], APPROX),
          at("user.json", 1500))
    check("next commit leaves the file alone: known", at("user.json", 2500) == (["v2"], KNOWN),
          at("user.json", 2500))
    check("nothing committed since: approx", at("user.json", 3500) == (["v2"], APPROX),
          at("user.json", 3500))
    check("file not created yet: empty", at("late.json", 1500) == ([], APPROX), at("late.json", 1500))
    check("file never tracked: empty and known", at("nope.json", 2500) == ([], KNOWN),
          at("nope.json", 2500))
    check("git reads leave the repo untouched", tree_digest(root) == before)
    check("no git: current", GraphHistory(FIXTURE).at("user.json", 1)[1] == CURRENT)


def test_end_to_end():
    print("end to end (production writes the logs):")
    from core.constants import RECALL_LOG_NAME, USEFUL_LOG_NAME
    from core.utils import active_node_ids
    from eval.report import build_report
    from mcp_http.ambient import build_prompt_recall
    from mcp_http.session_manager import HTTPSessionManager
    from mcp_http.store import GraphConfig, MultiProjectGraphStore

    root = Path(_TMP_STORAGE)
    git(root, "init", "-q")
    project = tempfile.mkdtemp(prefix="kg-eval-proj-", dir=str(Path.home() / ".cache"))
    _CLEANUP.append(project)
    sm = HTTPSessionManager()
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), sm, broadcast_callback=None)
    # Nodes are written by an earlier session: a writer has seen its own.
    writer = sm.register(project, claude_sid="cc-writer")["session_id"]

    nodes = [
        ("user", "zephyr-tokenizer-quirk", "The zephyr tokenizer drops trailing umlauts", False),
        ("user", "marmot-cache-warmup", "Marmot cache warmup must precede the first query", True),
        ("project", "quokka-migration-order", "Quokka migrations run in dependency order, not by date", True),
        ("project", "wombat-retry-budget", "Wombat client retry budget is three attempts per minute", True),
        ("project", "ibis-lint-config", "Ibis lint config lives in pyproject, not setup.cfg", False),
    ]
    for level, nid, gist, _archived in nodes:
        store.put_node(level=level, node_id=nid, gist=gist, session_id=writer)
    # Archive only after the last write: put_node's refill pass promotes
    # archived nodes back while the graph has headroom.
    for level, nid, _gist, archived in nodes:
        key = "user" if level == "user" else f"project:{project}"
        if archived:
            store.graphs[key]["nodes"][nid]["_archived"] = True
        store._write_through(key)
    t0 = time.time()
    commit_all(root, t0 - 100, "graphs")
    sid = sm.register(project, claude_sid="cc-e2e")["session_id"]

    ask = lambda p: build_prompt_recall(store, sm, project, p, claude_sid="cc-e2e")
    ask("tell me about the zephyr tokenizer")                    # full_read_nudge
    active = active_node_ids(store.graphs["user"]["nodes"]) | \
        active_node_ids(store.graphs[f"project:{project}"]["nodes"])
    sm.mark_seen(sid, sorted(active), via="full_read")
    sm.mark_full_read(sid)
    out = [
        ask("how should quokka migrations be ordered"),           # injected
        ask("quokka migrations order again please"),             # all_seen
        ask("zephyr tokenizer umlauts behaviour"),                # all_seen (active)
        ask("completely unrelated xylophone harmonics"),          # no_hits
    ]
    sm.mark_seen(sid, ["wombat-retry-budget"], via="search")
    out.append(ask("wombat retry budget attempts"))              # all_seen via search
    out.append(ask("marmot cache warmup before first query"))     # injected
    # Autocommit stages the whole tree: this one adds only the logs, and is
    # what proves the graphs unchanged through every prompt above.
    t1 = int(time.time()) + 1        # whole seconds, after every prompt
    commit_all(root, t1, "logs")
    store.mark_useful(["wombat-retry-budget", "quokka-migration-order"], sid)
    commit_all(root, t1 + 1, "likes")

    recall = [json.loads(line) for line in (root / RECALL_LOG_NAME).read_text().splitlines()]
    reasons = [r["reason"] for r in recall]
    check("production logged the scenario",
          reasons == ["full_read_nudge", "injected", "all_seen", "all_seen", "no_hits",
                      "all_seen", "injected"], reasons)
    check("production endorsements logged", (root / USEFUL_LOG_NAME).exists())

    before = tree_digest(root)
    rep = build_report(root, variants=["score-only", "half-threshold"])
    c = rep["replay"]["consistency"]
    check("baseline reproduces every known logged decision",
          c["checked"] == 6 and c["matched"] == 6 and not c["skipped_by_state"], c)
    check("reconstructed seen-set agrees with the log",
          rep["replay"]["seen_agreement"] == {"steps": 6, "agree": 6}, rep["replay"]["seen_agreement"])
    base = rep["replay"]["variants"]["baseline"]
    check("graph state known from git", base["graph_state"] == {"known": 6}, base["graph_state"])
    check("baseline keeps the ambient endorsement",
          base["ambient_endorsed"]["kept"] == 1 and base["ambient_endorsed"]["replayable"] == 1,
          base["ambient_endorsed"])
    check("the dug-up endorsement is one baseline did not surface",
          base["dug_up"]["replayable"] == 1 and base["dug_up"]["recovered"] == 0, base["dug_up"])
    check("variants are compared with the baseline",
          all("vs_baseline" in rep["replay"]["variants"][v] for v in ("score-only", "half-threshold")))
    check("the harness never writes to the storage root", tree_digest(root) == before)

    # The check can fail: a log that disagrees with the graphs is reported.
    tampered = Path(_CLEANUP[0]) / "tampered-recall.jsonl"
    lines = [dict(r) for r in recall]
    first = next(i for i, r in enumerate(lines) if r["reason"] == "injected")
    lines[first]["hits"] = [dict(lines[first]["hits"][0], id="not-what-ran")]
    tampered.write_text("".join(json.dumps(r) + "\n" for r in lines))
    c = build_report(root, recall_path=tampered)["replay"]["consistency"]
    check("a disagreeing log is reported as a mismatch",
          c["matched"] == 5 and c["mismatches"][0]["logged_ids"] == ["not-what-ran"], c)

    # A graph change after the last commit makes the next prompt's state
    # unprovable: those records must be skipped, never checked. The commits
    # above are dated ahead of the clock, so wait for it to pass them.
    while time.time() < t1 + 1.5:
        time.sleep(0.2)
    store.put_node(level="project", node_id="heron-late-node", gist="Heron appears late", session_id=writer)
    ask("heron late node")
    c = build_report(root)["replay"]["consistency"]
    check("unprovable graph state is skipped, not checked",
          c["checked"] == 6 and c["skipped_by_state"] == {"approx": 1}, c)
    store.shutdown()


def test_cli():
    print("cli:")
    from eval.__main__ import main

    def run(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = main(list(argv))
        return rc, buf.getvalue(), err.getvalue()

    rc, text, _ = run("--root", str(FIXTURE), "--variant", "score-only")
    check("text report runs", rc == 0 and "Replay by variant" in text and "score-only" in text, text[-300:])
    check("names proxies honestly", "weak negatives" in text
          and "precision" not in text.lower(), text[-300:])
    rc, out, _ = run("--root", str(FIXTURE), "--json")
    rep = json.loads(out) if rc == 0 else {}
    check("--json is machine-readable", rep.get("inputs", {}).get("recall_records") == 9)
    rc, _, err = run("--root", str(FIXTURE), "--variant", "no-such-variant")
    check("unknown variant is an error, not a crash", rc == 2 and "unknown variant" in err, err)
    rc, out, _ = run("--root", str(FIXTURE), "--json", "--variant", "eval.variants:score_only")
    check("module:function variants load",
          rc == 0 and "eval.variants:score_only" in json.loads(out)["replay"]["variants"])
    rc, out, _ = run("--no-root", "--recall", str(FIXTURE / "recall.jsonl"), "--json")
    rep = json.loads(out) if rc == 0 else {}
    check("explicit files without a root: descriptive only",
          rep.get("inputs", {}).get("recall_records") == 9 and "replay_skipped" in rep, rep.get("inputs"))
    rc, _, err = run("--root", str(FIXTURE / "missing"))
    check("missing root is an error", rc == 2, err)


def main():
    print("=== v0.9.40 retrieval evaluation harness tests ===")
    fixture_before = tree_digest(FIXTURE)
    test_imports_start_nothing()
    test_descriptive()
    test_replay_bookkeeping()
    test_vs_baseline_arithmetic()
    test_graph_history()
    test_cli()
    check("the fixture is unchanged by every run", tree_digest(FIXTURE) == fixture_before)
    try:
        test_end_to_end()
    finally:
        for d in _CLEANUP + [_TMP_STORAGE]:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
