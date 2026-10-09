"""Selection and runner rules, checked against the real code (all hold).

1. pick_chore (core/chores.py), seeded random search over small graphs:
   three nodes, each with an id that is short, long or dated; an oversized
   gist or not; changelog-style notes or not; churning (three gist rewrites
   in 30 days) or not; held by a live session or not; named in a maintain
   `declined` line or not; a recent chore target or not; an anchor
   candidate or not; and random edges among them. For every chore picked:
     - a rename (kind "id") never takes an id a live session holds
     - a text-rewriting kind (gist, notes, anchor) never takes a churning node
     - no chore takes a declined id or a recent chore target
2. AntigravityRunner.refusal, exhaustively over its inputs (settings.json
   unreadable/absent/present, useG1Credits, antigravity_allow_credits, the
   grants, both tiers): a run may start only when no paid credits can be
   spent and every non-delete kg tool of the tier is granted.
Usage: python check_rules.py [samples]
"""
import itertools, json, os, random, sys, tempfile, time
from pathlib import Path
from unittest.mock import patch

os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-rules-")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "knowledge-graph" / "server"))
from core.chores import is_churning, pick_chore  # noqa: E402
from core.constants import GIST_TS_FIELD  # noqa: E402
from mcp_http import chore_dispatch as cd  # noqa: E402

samples = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
NOW = 1_800_000_000.0
IDS = {"short": ["alpha-node", "beta-node", "gamma-node"],
       "long": ["alpha-node-that-carries-its-whole-claim-in-the-name",
                "beta-node-that-carries-its-whole-claim-in-the-name",
                "gamma-node-that-carries-its-whole-claim-in-the-name"],
       "dated": ["alpha-review-2026-08-25", "beta-review-2026-08-26", "gamma-review-2026-08-27"]}
rng = random.Random(7)
picked, violations, kinds = 0, [], {}
for _ in range(samples):
    nodes, held, declined, recent, anchors, churning = [], set(), [], [], {}, set()
    for i in range(3):
        nid = IDS[rng.choice(list(IDS))][i]
        node = {"id": nid, "gist": ("x" * 400 if rng.random() < .4 else f"{nid} gist"),
                "touches": ["docs/shared.md"] if rng.random() < .5 else []}
        if rng.random() < .3:
            node["notes"] = ["actually it was the cache", "turns out it was the lock"]
        if rng.random() < .3:
            node[GIST_TS_FIELD] = [NOW - 86400 * d for d in (1, 2, 3)]
        if rng.random() < .3:
            held.add(nid)
        if rng.random() < .2:
            declined.append(f"left {nid} as it is: the name is right")
        if rng.random() < .2:
            recent.append({"kind": "edge", "targets": [nid]})
        if rng.random() < .3:
            anchors[nid] = [{"entry": "a.py", "verdict": "moved", "replacement": "b.py"}]
        nodes.append(node)
    ids = [n["id"] for n in nodes]
    edges = [{"from": a, "to": b, "rel": "relates-to"}
             for a, b in itertools.combinations(ids, 2) if rng.random() < .3]
    c = pick_chore(nodes, edges, in_context=held, chore_trail=recent,
                   maintain_trail=[{"declined": declined}] if declined else [],
                   anchors=anchors, now=NOW)
    if not c:
        continue
    picked += 1
    kinds[c.kind] = kinds.get(c.kind, 0) + 1
    by_id = {n["id"]: n for n in nodes}
    blocked = {t for e in recent for t in e["targets"]}
    for t in c.targets:
        why = []
        if c.kind == "id" and t in held:
            why.append("rename of a held id")
        if c.kind in ("gist", "notes", "anchor") and is_churning(by_id[t], NOW):
            why.append("rewrite of a churning node")
        if any(t in line for line in declined):
            why.append("declined id")
        if t in blocked:
            why.append("recent target")
        if why:
            violations.append((c.kind, t, why))
print(f"1. pick_chore: {samples} random graphs, {picked} chores picked {dict(sorted(kinds.items()))}")
print(f"   violations: {len(violations)}" + (f"; first: {violations[0]}" if violations else ""))

work = Path(tempfile.mkdtemp(prefix="kg-agy-settings-"))
agy_settings = work / "settings.json"
tiers = {"chore": str(cd.SHIPPED_SETTINGS), "pass": str(cd.SHIPPED_PASS_SETTINGS)}
runner = cd.AntigravityRunner()
checked, bad = 0, []
for tier, settings in tiers.items():
    needed = [t for t in cd.allowed_tools(settings) if t not in ("kg_delete_node", "kg_delete_edge")]
    grants = {"none": [], "all": [f"mcp({cd.AGY_MCP_SERVER}/{t})" for t in needed],
              "all-but-one": [f"mcp({cd.AGY_MCP_SERVER}/{t})" for t in needed[1:]],
              "server-wide": [f"mcp({cd.AGY_MCP_SERVER}/*)"], "every-mcp": ["mcp(*)"],
              "other-server": ["mcp(other/*)"]}
    for file_state, credits, allow_credits, grant in itertools.product(
            ("absent", "unreadable", "present"), (None, False, True), (False, True), grants):
        if file_state == "absent":
            agy_settings.unlink(missing_ok=True)
        elif file_state == "unreadable":
            agy_settings.write_text("{not json")
        else:
            data = {"permissions": {"allow": grants[grant]}}
            if credits is not None:
                data["useG1Credits"] = credits
            agy_settings.write_text(json.dumps(data))
        with patch.object(cd, "AGY_SETTINGS", agy_settings):
            refusal = runner.refusal({"antigravity_allow_credits": allow_credits}, settings)
        checked += 1
        granted = file_state == "present" and grant in ("all", "server-wide", "every-mcp")
        credit_risk = file_state == "present" and credits and not allow_credits
        if not refusal and (not granted or credit_risk):
            bad.append((tier, file_state, credits, allow_credits, grant))
print(f"2. AntigravityRunner.refusal: {checked} input combinations; runs allowed without "
      f"grants or with paid credits at risk: {len(bad)}" + (f"; first: {bad[0]}" if bad else ""))
ok = not violations and not bad and picked > samples // 4
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
