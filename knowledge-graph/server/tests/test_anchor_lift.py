#!/usr/bin/env python3
"""Self-contained tests for anchor repair, lift, and the churn guard.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_anchor_lift.py

ANCHOR. A node's `touches` entry that no longer resolves. The server finds
where it went (the rename git recorded, or the one same-named file in the
project) and the chore only decides; two same-named files is a refusal.

LIFT. Instance-shaped nodes (dated records, sessions, reviews, snapshots)
that share a lesson. The chore writes it once as a principle node and edges
the members to it; a cluster needs two members, because one episode is not a
principle. Every lift decision lands in chores.jsonl.

CHURN. A node whose gist was rewritten too often recently is left alone by
every kind that rewrites text in place (gist, notes, anchor). put_node stamps
real gist changes into a bounded _gist_ts list that survives rename.

Covers:
  1. parse_touch: relative, absolute, ~, path:lines (anchor), dirs; free text,
     URLs and globs are not paths
  2. dangling detection on every form; user graph ignores relative entries;
     a project whose root is gone reports nothing
  3. discovery: unique basename -> moved (suffix and form kept); two
     same-named files -> ambiguous, no replacement; nothing -> gone;
     incomplete walk -> unknown; outside the tree -> unknown
  4. discovery via git: a rename (and a rename chain) -> moved; a plain
     delete -> gone
  5. anchor_candidates keeps only nodes a chore can act on
  6. instance shapes and cluster formation, with the two-member floor,
     lifted members excluded, archived excluded, size cap
  7. put_node stamps _gist_ts on a real change only, bounded; rename and
     disk keep it; promotion from the archive does not stamp
  8. churn guard excludes a hot node from gist, notes and anchor chores,
     not from edge; stamps outside the window do not count
  9. pick_chore: anchor with its records; lift takes one whole cluster; a
     dated lift member is not offered as an id chore; a declined member can
     drop a cluster under the floor
 10. both prompts name their targets and what the server found
 11. DEBT reports dangling touches and lift clusters, raw, in the line and
     the disk survey
 12. end to end: a lift dispatch and its done record in chores.jsonl, with
     the principle, linked members and the chore's own stamp; the dispatcher
     precomputes anchor candidates for the project graph
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.pop("KG_CHORES", None)

from core.anchors import (  # noqa: E402
    TreeIndex, anchor_candidates, dangling_touches, parse_touch, resolve_dangling,
)
from core.chores import (  # noqa: E402
    build_chore_prompt, candidates_by_kind, gist_rewrites, is_churning, pick_chore,
)
from core.constants import (  # noqa: E402
    CHORE_CONFIG_NAME, CHURN_MAX_REWRITES, GIST_TS_FIELD, GIST_TS_MAX,
    LIFT_EDGE_REL, LIFT_MAX_MEMBERS, get_storage_root,
)
from core.debt import compute_debt, debt_line, survey_debt  # noqa: E402
from core.lift import instance_shape, lift_clusters  # noqa: E402
from mcp_http import chore_dispatch  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402

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


def node(nid, gist="a gist", **kw):
    return {"id": nid, "gist": gist, **kw}


def touch(root: Path, rel: str, text: str = "x\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def git(root: Path, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], check=True, capture_output=True)


def chore_log():
    path = get_storage_root() / "chores.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    print("=== anchor repair, lift, churn guard ===")
    scratch = Path(tempfile.mkdtemp(prefix="kg-anchor-"))
    home = scratch / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)

    # --- 1. what counts as a path -------------------------------------------
    print("parse_touch:")
    check("relative path", parse_touch("src/a.py") == ("src/a.py", ""))
    check("path:lines (anchor) keeps the whole suffix",
          parse_touch("src/a.py:88-120 (rotation schedule)")
          == ("src/a.py", ":88-120 (rotation schedule)"),
          parse_touch("src/a.py:88-120 (rotation schedule)"))
    check("absolute", parse_touch("/etc/x.conf") == ("/etc/x.conf", ""))
    check("home-relative", parse_touch("~/.claude/settings.json")[0] == "~/.claude/settings.json")
    check("directory", parse_touch("src/auth/") == ("src/auth/", ""))
    check("bare filename with extension", parse_touch("deploy.sh") == ("deploy.sh", ""))
    check("free text is not a path", parse_touch("the auth module") is None)
    check("URL is not a path", parse_touch("https://example.com/a.md") is None)
    check("glob is not a path", parse_touch("src/*.py") is None)

    # --- 2. detection -----------------------------------------------------------
    print("dangling detection:")
    touch(proj, "src/live.py")
    touch(home, ".config/tool.toml")
    nodes = [
        node("rel-ok", touches=["src/live.py"]),
        node("rel-lines-ok", touches=["src/live.py:1-2 (head)"]),
        node("rel-gone", touches=["src/dead.py"]),
        node("rel-lines-gone", touches=["src/dead.py:10-20 (the loop)"]),
        node("abs-gone", touches=[str(proj / "docs" / "missing.md")]),
        node("abs-ok", touches=[str(proj / "src" / "live.py")]),
        node("tilde-ok", touches=["~/.config/tool.toml"]),
        node("tilde-gone", touches=["~/.config/other.toml"]),
        node("dir-gone", touches=["lib/old/"]),
        node("prose", touches=["the deploy flow", "https://x.y/z.md"]),
        node("archived-gone", touches=["src/dead.py"], _archived=True),
    ]
    dangling = dangling_touches(nodes, proj, home)
    check("relative, relative:lines, absolute, ~ and dir forms all flagged",
          set(dangling) == {"rel-gone", "rel-lines-gone", "abs-gone", "tilde-gone", "dir-gone"},
          sorted(dangling))
    check("the entry is reported verbatim, suffix included",
          dangling["rel-lines-gone"] == ["src/dead.py:10-20 (the loop)"], dangling)
    user_dangling = dangling_touches(nodes, None, home)
    check("user graph: relative entries have no base and are not flagged",
          set(user_dangling) == {"abs-gone", "tilde-gone"}, sorted(user_dangling))
    check("a project root gone from this machine reports nothing",
          dangling_touches(nodes, scratch / "no-such-project", home) == {})

    # --- 3. discovery without git ------------------------------------------------
    print("discovery (tree):")
    touch(proj, "notes/moved-doc.md")
    touch(proj, "a/config.yaml")
    touch(proj, "b/config.yaml")
    touch(proj, "lib/new/old/keep.py")
    recs = resolve_dangling(
        ["docs/moved-doc.md:3-9 (setup)", "etc/config.yaml", "src/vanished.py",
         "/elsewhere/x.py", "~/proj/docs/moved-doc.md", "lib/old/"],
        proj, home)
    by = {r["entry"]: r for r in recs}
    r = by["docs/moved-doc.md:3-9 (setup)"]
    check("unique basename -> moved, same form, suffix kept",
          r["verdict"] == "moved" and r["replacement"] == "notes/moved-doc.md:3-9 (setup)", r)
    r = by["etc/config.yaml"]
    check("two same-named files -> ambiguous, and no replacement offered",
          r["verdict"] == "ambiguous" and r["replacement"] is None
          and "not guessing" in r["evidence"], r)
    check("nothing of that name anywhere -> gone",
          by["src/vanished.py"]["verdict"] == "gone", by["src/vanished.py"])
    check("outside the project tree -> unknown",
          by["/elsewhere/x.py"]["verdict"] == "unknown", by["/elsewhere/x.py"])
    check("a ~ entry is repaired in ~ form",
          by["~/proj/docs/moved-doc.md"]["replacement"] == "~/proj/notes/moved-doc.md",
          by["~/proj/docs/moved-doc.md"])
    check("a directory entry resolves to the one same-named directory",
          by["lib/old/"]["replacement"] == "lib/new/old/", by["lib/old/"])
    partial = TreeIndex(proj.resolve(), max_entries=1)
    r = resolve_dangling(["docs/moved-doc.md"], proj, home, index=partial)[0]
    check("an incomplete walk never claims 'the only one' -> unknown",
          r["verdict"] == "unknown" and r["replacement"] is None, r)

    # --- 4. discovery via git ------------------------------------------------------
    print("discovery (git):")
    repo = home / "repo"
    touch(repo, "src/a.py", "alpha content that git can follow\n" * 5)
    touch(repo, "docs/b.md", "beta\n")
    touch(repo, "x/c.py", "gamma content that git can follow too\n" * 5)
    touch(repo, "other/c.py", "a different file that shares the name\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "one")
    git(repo, "mv", "src/a.py", "src/core_a.py")
    git(repo, "commit", "-qm", "rename")
    (repo / "lib").mkdir()
    git(repo, "mv", "src/core_a.py", "lib/core_alpha.py")
    git(repo, "commit", "-qm", "rename again")
    git(repo, "rm", "-q", "docs/b.md")
    git(repo, "commit", "-qm", "delete")
    (repo / "y").mkdir()
    git(repo, "mv", "x/c.py", "y/c.py")
    git(repo, "commit", "-qm", "move c")
    recs = {r["entry"]: r for r in resolve_dangling(
        ["src/a.py:5 (init)", "docs/b.md", "x/c.py"], repo, home)}
    r = recs["src/a.py:5 (init)"]
    check("a rename chain in git -> moved to where it lives now",
          r["verdict"] == "moved" and r["replacement"] == "lib/core_alpha.py:5 (init)"
          and r["evidence"].startswith("renamed in git"), r)
    r = recs["docs/b.md"]
    check("a plain delete in git -> gone, with the commit as evidence",
          r["verdict"] == "gone" and r["evidence"].startswith("deleted in git"), r)
    r = recs["x/c.py"]
    check("git's rename wins over a same-named decoy elsewhere",
          r["verdict"] == "moved" and r["replacement"] == "y/c.py", r)

    # --- 5. anchor_candidates ----------------------------------------------------
    print("anchor candidates:")
    cands = anchor_candidates({"n-moved": ["docs/moved-doc.md"],
                               "n-ambiguous": ["etc/config.yaml"],
                               "n-outside": ["/elsewhere/x.py"]}, proj, home)
    check("only nodes with a moved or gone entry are chore targets",
          set(cands) == {"n-moved"}, sorted(cands))
    check("no project root -> no candidates (the user graph)",
          anchor_candidates({"n": ["~/x.md"]}, None, home) == {})

    # --- 6. lift clusters -----------------------------------------------------------
    print("lift clusters:")
    check("dated id is an instance", instance_shape("deploy-notes-2026-08-01") == "dated record")
    check("session / review / snapshot ids are instances",
          instance_shape("auth-session-recap") and instance_shape("api-review")
          and instance_shape("build-status-snapshot"))
    check("a series entry is an instance", instance_shape("migration-week3") == "series entry")
    check("a subject id is not", instance_shape("auth-token-rotation") is None)

    lone = [node("deploy-review-2026-08-01", "rollback blocked by migration lock")]
    check("one episode is never a cluster", lift_clusters(lone, []) == [])
    unrelated = [node("deploy-review-2026-08-01", "rollback blocked by migration lock"),
                 node("design-review-2026-08-02", "palette contrast too low on banners")]
    check("two unrelated episodes are not a cluster", lift_clusters(unrelated, []) == [])
    shared_touch = [node("deploy-review-2026-08-01", "a", touches=["docs/deploy.md:1-5"]),
                    node("deploy-review-2026-08-09", "b", touches=["docs/deploy.md"])]
    cl = lift_clusters(shared_touch, [])
    check("a shared touched file links two episodes",
          len(cl) == 1 and cl[0]["members"] == ["deploy-review-2026-08-01",
                                                "deploy-review-2026-08-09"]
          and "docs/deploy.md" in cl[0]["evidence"][0], cl)
    shared_terms = [node("release-session-1", "rollback stalled: migration lockfile held by worker"),
                    node("release-session-2", "second rollback stalled on the migration lockfile"),
                    node("auth-token-rotation", "rollback migration lockfile")]
    cl = lift_clusters(shared_terms, [])
    check("shared vocabulary links episodes; the subject node is not an episode",
          len(cl) == 1 and cl[0]["members"] == ["release-session-1", "release-session-2"], cl)
    lifted_edges = [{"from": "deploy-review-2026-08-01", "to": "p", "rel": LIFT_EDGE_REL}]
    check("a member already lifted is not offered again (and the floor then holds)",
          lift_clusters(shared_touch, lifted_edges) == [])
    archived = [dict(shared_touch[0], _archived=True), shared_touch[1]]
    check("archived episodes are already out of the way", lift_clusters(archived, []) == [])
    many = [node(f"ops-review-2026-08-{i:02d}", "g", touches=["docs/ops.md"])
            for i in range(1, LIFT_MAX_MEMBERS + 4)]
    cl = lift_clusters(many, [])
    check("a large cluster is capped for one chore",
          len(cl) == 1 and len(cl[0]["members"]) == LIFT_MAX_MEMBERS, cl)

    # --- 7. _gist_ts on the node ----------------------------------------------------
    print("gist rewrite stamps:")
    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    sid = session_manager.register(str(Path.home()), claude_sid="cc-anchor-1")["session_id"]

    store.put_node(level="user", node_id="churny-node", gist="first", session_id=sid)
    n = store.graphs["user"]["nodes"]["churny-node"]
    check("a create is not a rewrite", GIST_TS_FIELD not in n, n)
    store.put_node(level="user", node_id="churny-node", gist="first",
                   touches=["~/x.md"], session_id=sid)
    check("re-sending the same gist is not a rewrite", GIST_TS_FIELD not in n, n)
    for i in range(3):
        store.put_node(level="user", node_id="churny-node", gist=f"v{i}", session_id=sid)
    check("each real gist change stamps once", len(n[GIST_TS_FIELD]) == 3, n.get(GIST_TS_FIELD))
    check("three rewrites inside the window is hot",
          is_churning(n) and gist_rewrites(n) == 3 > CHURN_MAX_REWRITES)
    for i in range(GIST_TS_MAX + 5):
        store.put_node(level="user", node_id="churny-node", gist=f"w{i}", session_id=sid)
    check("the list stays bounded", len(n[GIST_TS_FIELD]) == GIST_TS_MAX, len(n[GIST_TS_FIELD]))
    stamps = list(n[GIST_TS_FIELD])
    store.rename_node("churny-node", "churn-hot-node", level="user", session_id=sid)
    renamed = store.graphs["user"]["nodes"]["churn-hot-node"]
    check("the stamps survive a rename", renamed.get(GIST_TS_FIELD) == stamps)
    store._save_to_disk("user")
    disk = json.loads((get_storage_root() / "user.json").read_text())
    check("and reach disk under the new id",
          disk["nodes"]["churn-hot-node"].get(GIST_TS_FIELD) == stamps)
    store.put_node(level="user", node_id="archived-once", gist="kept", session_id=sid)
    store.graphs["user"]["nodes"]["archived-once"]["_archived"] = True
    store.read_node("archived-once", level="user", session_id=sid)
    check("promotion from the archive bumps the version but is no rewrite",
          GIST_TS_FIELD not in store.graphs["user"]["nodes"]["archived-once"])

    # --- 8. the guard -------------------------------------------------------------
    print("churn guard:")
    now = time.time()
    hot = [now - 3600 * i for i in range(1, 4)]
    cold = [now - 40 * 86400 - i for i in range(5)]
    guard_nodes = [
        node("hot-node", "x" * 400, notes=["actually wrong", "turns out fine"],
             **{GIST_TS_FIELD: hot}),
        node("cold-node", "y" * 400, notes=["actually wrong", "turns out fine"],
             **{GIST_TS_FIELD: cold}),
    ]
    anchors = {nid: [{"entry": "a.md", "verdict": "gone", "replacement": None,
                      "evidence": "e"}] for nid in ("hot-node", "cold-node")}
    pools = candidates_by_kind(guard_nodes, [], anchors=anchors, now=now)
    for kind in ("gist", "notes", "anchor"):
        check(f"a hot node is left out of {kind} chores",
              "hot-node" not in pools[kind] and "cold-node" in pools[kind], pools[kind])
    check("stamps older than the window do not count", not is_churning(guard_nodes[1], now))
    check("adding an edge rewrites nothing: a hot node still gets edge chores",
          "hot-node" in pools["edge"], pools["edge"])

    # --- 9. pick_chore ------------------------------------------------------------
    print("pick_chore:")
    anchor_rec = {"entry": "docs/moved-doc.md", "verdict": "moved",
                  "replacement": "notes/moved-doc.md", "evidence": "the only file"}
    c = pick_chore([node("setup-guide", "how to set up", touches=["docs/moved-doc.md"])],
                   [{"from": "setup-guide", "to": "x", "rel": "r"}],
                   anchors={"setup-guide": [anchor_rec]})
    check("an anchor chore names the node and carries the server's records",
          c and c.kind == "anchor" and c.targets == ["setup-guide"]
          and c.context["anchors"]["setup-guide"] == [anchor_rec], c)

    lift_nodes = shared_touch + [node("release-session-1", "rollback stalled: migration lockfile"),
                                 node("release-session-2", "rollback stalled: migration lockfile")]
    lift_edges = [{"from": a, "to": b, "rel": "follows"}
                  for a, b in (("deploy-review-2026-08-01", "deploy-review-2026-08-09"),
                               ("release-session-1", "release-session-2"))]
    c = pick_chore(lift_nodes, lift_edges)
    check("a lift chore takes one whole cluster, never a slice across two",
          c and c.kind == "lift"
          and c.targets in (["deploy-review-2026-08-01", "deploy-review-2026-08-09"],
                            ["release-session-1", "release-session-2"]), c)
    check("it carries the evidence and each member's touches",
          c and c.context["evidence"] and set(c.context["touches"]) == set(c.targets), c)
    pools = candidates_by_kind(lift_nodes, lift_edges)
    check("a dated id waiting in a lift cluster is not offered as a rename",
          not any(i.startswith("deploy-review") for i in pools["id"]), pools["id"])
    declined = [{"declined": ["deploy-review-2026-08-01: not an episode, a standing rule"]}]
    c = pick_chore(lift_nodes, lift_edges, maintain_trail=declined)
    check("a declined member drops its cluster under the floor; the other cluster steps up",
          c and c.kind == "lift" and c.targets == ["release-session-1", "release-session-2"], c)

    # --- 10. prompts ----------------------------------------------------------------
    print("prompts:")
    c = pick_chore([node("setup-guide", "how to set up",
                         touches=["docs/moved-doc.md", "etc/config.yaml"])],
                   [{"from": "setup-guide", "to": "x", "rel": "r"}],
                   anchors={"setup-guide": [anchor_rec,
                                            {"entry": "etc/config.yaml", "verdict": "ambiguous",
                                             "replacement": None, "evidence": "2 files"}]})
    c.level = "project"
    p = build_chore_prompt(c, str(proj))
    check("anchor prompt names the target and reads it by id",
          "Targets: setup-guide" in p and "ids=['setup-guide']" in p, p[:400])
    check("anchor prompt lists each entry with verdict and replacement",
          "'docs/moved-doc.md': moved -> 'notes/moved-doc.md'" in p
          and "'etc/config.yaml': ambiguous" in p, p)
    check("anchor prompt forbids inventing a path and keeps the gist",
          "Never write a path that is not listed" in p and "EXACTLY" in p)

    c = pick_chore(lift_nodes[:2], lift_edges[:1])
    c.level = "project"
    p = build_chore_prompt(c, str(proj))
    check("lift prompt names every member as a target",
          "Targets: deploy-review-2026-08-01, deploy-review-2026-08-09" in p, p[:400])
    check("lift prompt shows why they were grouped and what they touch",
          "Why they were grouped:" in p and "touches: docs/deploy.md" in p, p)
    check("lift prompt asks for the member -> principle edge and two members",
          f'rel="{LIFT_EDGE_REL}"' in p and "At least TWO members" in p, p)
    check("lift prompt forbids touching the members and stamps the principle",
          "Do not edit, rename or delete the members" in p and '"principle":' in p, p)

    # --- 11. DEBT -------------------------------------------------------------------
    print("debt:")
    touch(proj, "docs/deploy.md")
    debt_nodes = [node("rel-gone", touches=["src/dead.py", "src/dead2.py"]),
                  node("rel-ok", touches=["src/live.py"])] + shared_touch
    d = compute_debt(debt_nodes, [], None, 0, project_root=str(proj), home=str(home))
    check("dangling touches and lift clusters counted raw",
          d["dangling_touches"] == 2 and d["dangling_nodes"] == 1
          and d["lift_clusters"] == 1 and d["lift_members"] == 2, d)
    check("a dated lift member counts as lift, not as a long id", d["long_ids"] == 0, d)
    line = debt_line(d)
    check("both print in the DEBT line",
          "2 dangling touch(es)" in line and "1 lift cluster(s)" in line, line)
    d0 = compute_debt(debt_nodes, [], None, 0)
    check("no filesystem context -> no dangling count, not a wrong one",
          d0["dangling_touches"] == 0, d0)
    check("both factors raise the deficit", d["score"] > compute_debt(
        [node("rel-ok", touches=["src/live.py"])], [], None, 0,
        project_root=str(proj), home=str(home))["score"])

    pdir = get_storage_root() / "projects" / "demo-proj"
    pdir.mkdir(parents=True)
    (pdir / "graph.json").write_text(json.dumps({
        "_meta": {"project_path": str(proj)},
        "nodes": {n["id"]: n for n in debt_nodes}, "edges": {}}))
    row = next(r for r in survey_debt(get_storage_root()) if r["graph"] == "demo-proj")
    check("the disk survey resolves touches against the graph's own project_path",
          row["debt"]["dangling_touches"] == 2 and row["debt"]["lift_clusters"] == 1,
          row["debt"])

    # --- 12. end to end -----------------------------------------------------------
    print("dispatch:")
    proj2 = Path(tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache")))
    touch(proj2, "notes/runbook.md")
    touch(proj2, "docs/deploy.md")
    psid = session_manager.register(str(proj2), claude_sid="cc-anchor-2")["session_id"]
    store.read_graphs(psid)
    for nid, gist in (("deploy-review-2026-08-01", "rollback blocked by migration lock"),
                      ("deploy-review-2026-08-09", "rollback blocked again by the lock")):
        store.put_node(level="project", node_id=nid, gist=gist,
                       touches=["docs/deploy.md"], session_id=psid)
    store.put_edge(level="project", from_ref="deploy-review-2026-08-01",
                   to_ref="deploy-review-2026-08-09", rel="follows", session_id=psid)

    fake = scratch / "fake-claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\nsleep 1\n")
    fake.chmod(0o755)
    gauge = get_storage_root() / "gauge.json"
    gauge.write_text(json.dumps({"updated_at": time.time(), "five_hour_pct": 5,
                                 "seven_day_pct": 5,
                                 "seven_day_resets_at": time.time() + 3 * 86400}))
    (get_storage_root() / CHORE_CONFIG_NAME).write_text(json.dumps({
        "enabled": True, "limits": str(gauge), "claude_bin": str(fake),
        "min_interval_s": 0}))
    chore_dispatch._config_cache["mtime"] = "force-reread"
    # The user graph carries this file's earlier fixtures; park it on cooldown
    # so the project graph is the one in play.
    (get_storage_root() / chore_dispatch.STATE_NAME).write_text(
        json.dumps({"last_ts": 0, "graphs": {"user": time.time()}}))
    chore_dispatch.maybe_dispatch(store, session_manager, str(proj2))
    log = chore_log()
    disp = log[-1] if log else {}
    check("a lift chore is dispatched and logged with its cluster",
          disp.get("event") == "dispatch" and disp.get("kind") == "lift"
          and disp.get("cluster", {}).get("members") == ["deploy-review-2026-08-01",
                                                         "deploy-review-2026-08-09"]
          and disp["cluster"]["evidence"], disp)

    # Play the chore's part while the stand-in agent runs.
    store.put_node(level="project", node_id="lock-blocks-rollback",
                   gist="A held migration lock blocks every rollback",
                   notes=["when it matters: any rollback", "what goes wrong: silent stall"],
                   touches=["docs/deploy.md"], session_id=psid)
    for m in ("deploy-review-2026-08-01", "deploy-review-2026-08-09"):
        store.put_edge(level="project", from_ref=m, to_ref="lock-blocks-rollback",
                       rel=LIFT_EDGE_REL, session_id=psid)
    store.set_progress("chore", {"kind": "lift", "targets": ["deploy-review-2026-08-01"],
                                 "done": 1, "principle": "lock-blocks-rollback",
                                 "declined": []}, level="project", session_id=psid)
    done = None
    for _ in range(100):
        done = next((r for r in chore_log() if r.get("event") == "done"), None)
        if done:
            break
        time.sleep(0.1)
    out = (done or {}).get("outcome") or {}
    check("the done record carries what the lift actually did",
          done and done["kind"] == "lift" and done["rc"] == 0
          and out.get("principles") == ["lock-blocks-rollback"]
          and out.get("created") == ["lock-blocks-rollback"]
          and out.get("linked") == ["deploy-review-2026-08-01", "deploy-review-2026-08-09"],
          done)
    check("and the chore's own stamp",
          out.get("stamp", {}).get("principle") == "lock-blocks-rollback", out)
    graph_key = next(k for k in store.graphs if k.startswith("project"))
    pgraph = store.graphs[graph_key]
    check("once lifted, the members are no longer a lift candidate",
          lift_clusters(list(pgraph["nodes"].values()), list(pgraph["edges"].values())) == [])

    pools = candidates_by_kind(list(pgraph["nodes"].values()), list(pgraph["edges"].values()))
    check("a lifted member is evidence waiting to archive, not an id to rename",
          not any(i.startswith("deploy-review") for i in pools["id"]), pools["id"])

    # The dispatcher finds anchor candidates itself, for the project graph.
    store.put_node(level="project", node_id="ops-runbook", gist="where the runbook lives",
                   touches=["docs/runbook.md:1-40 (restart)"], session_id=psid)
    store.put_edge(level="project", from_ref="ops-runbook", to_ref="lock-blocks-rollback",
                   rel="relates-to", session_id=psid)
    tier, chore = chore_dispatch._pick_target(
        store, session_manager, {}, {"debt_floor": 0}, time.time(),
        [("project", graph_key, str(proj2))])
    check("the dispatcher precomputes anchors: the moved runbook is offered",
          tier == "chore" and chore.kind == "anchor" and chore.targets == ["ops-runbook"]
          and chore.context["anchors"]["ops-runbook"][0]["replacement"]
          == "notes/runbook.md:1-40 (restart)", (tier, chore))

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
