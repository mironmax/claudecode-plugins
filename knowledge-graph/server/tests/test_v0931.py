#!/usr/bin/env python3
"""Self-contained regression tests for the v0.9.31 change area.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0931.py

Covers the four builds driven by the week-2 ambient audit (2026-07-28):
  1. Search core: ./_- subtokens, light stemming, adjacent-bigram terms with
     their own IDF, field-weighted occurrences (id ×3, gist ×2, notes ×1),
     and per-record match meta (matched_terms / max_term_idf / title_match)
  2. Recall noise gate: a hit speaks only with corroboration (≥2 terms),
     near-unique evidence (idf ≥ 0.85), or a title match — the measured
     "one moderately common word in somebody's notes" class stays silent
  3. Bootstrap recovery is source-agnostic: fork (the value that slipped
     past v0.9.29 and re-preloaded for a week) and unknown future sources
     recover from transcript markers; only "clear" hard-resets; compact
     still re-renders the full preload
  4. Near-duplicate nudge on put_node CREATE: same-graph probe via the
     search term pipeline; nudge returned, write never blocked

Uses a temp KG_STORAGE_ROOT and temp projects under ~/.cache.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from fastapi.testclient import TestClient

from mcp_http.ambient import build_prompt_recall
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore, search_terms, _stem
from mcp_http.websocket import ConnectionManager

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


def _mkproject():
    return tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))


def main():
    print("=== v0.9.31 search-quality + lineage + near-dup tests ===")

    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)
    rest_api = create_rest_api(store, session_manager, ConnectionManager(), version="test")
    client = TestClient(rest_api)

    dirs = []
    try:
        # ==================================================================
        # 1. Search core
        # ==================================================================
        print("term pipeline:")
        uni, bi = search_terms("CLAUDE.md-cleanup session")
        check("subtokens split on ./-", "claude" in uni and "md" in uni and "cleanup" in uni, uni)
        check("composite token kept", "claude.md-cleanup" in uni, uni)
        check("adjacent bigram built", ("claude", "md") in bi, bi)
        check("stem strips -ing above 4 chars", _stem("scheduling") == "schedul")
        check("stem never drops below 4 chars", _stem("pass") == "pass" and _stem("using") == "using")

        print("search ranking:")
        proj_a = _mkproject(); dirs.append(proj_a)
        sid_a = session_manager.register(proj_a)["session_id"]
        fillers = [
            ("nginx-cache-keys", "Nginx cache key normalization for HEAD probes"),
            ("pwa-icon-fit", "Maskable icon safe circle fit for PWA installs"),
            ("borg-dedup-notes", "Borg dedups JSON well across weekly archives"),
            ("wrangler-oauth", "Wrangler OAuth token idle expiry behaviour"),
            ("psd-groups", "PSD composite filter breaks baked groups"),
            ("srcset-pitfall", "object-fit cover with srcset sizes pitfall"),
        ]
        for nid, gist in fillers:
            store.put_node(level="project", node_id=nid, gist=gist, session_id=sid_a)
        store.put_node(level="project", node_id="turnstile-spam-protection",
                       gist="Cloudflare Turnstile protects checkout forms",
                       notes=["CF_TURNSTILE_SITE_KEY and SECRET rotation lives in .env"],
                       session_id=sid_a)
        store.put_node(level="project", node_id="backup-rotation-schedule",
                       gist="Weekly borg backup rotation schedule on the NAS",
                       session_id=sid_a)
        store.put_node(level="project", node_id="deploy-pipeline",
                       gist="Deploy scheduling for prod pushes goes through the bare repo hook",
                       session_id=sid_a)
        store.put_node(level="project", node_id="claude-md-doctrine",
                       gist="Keep CLAUDE.md lightweight; repo gotchas only",
                       session_id=sid_a)
        store.put_node(level="project", node_id="mail-tooling",
                       gist="Notmuch mail setup on arch",
                       notes=["database sync pull of mail folders runs daily"],
                       session_id=sid_a)
        store.put_node(level="project", node_id="database-sync-procedure",
                       gist="Sync the prod database to local via dump and pull",
                       session_id=sid_a)

        r = store.search("scheduling deploy pushes", session_id=sid_a)
        top_ids = [x["id"] for x in r["top"]]
        check("stemming: 'scheduling' finds 'Deploy scheduling' node top",
              top_ids and top_ids[0] == "deploy-pipeline", top_ids)

        r = store.search("claude.md-cleanup", session_id=sid_a)
        top_ids = [x["id"] for x in r["top"]]
        check("hyphen-glued token finds CLAUDE.md node top",
              top_ids and top_ids[0] == "claude-md-doctrine", top_ids)

        r = store.search("turnstile rotation", session_id=sid_a)
        top_ids = [x["id"] for x in r["top"]]
        check("bigram co-occurrence outranks single-term stray",
              top_ids and top_ids[0] == "turnstile-spam-protection"
              and "backup-rotation-schedule" in top_ids[1:], top_ids)

        r = store.search("database sync", session_id=sid_a)
        top_ids = [x["id"] for x in r["top"]]
        check("field weighting: id/gist match outranks notes-only mention",
              top_ids.index("database-sync-procedure") < top_ids.index("mail-tooling"),
              top_ids)

        rec = r["top"][0]
        check("match meta rides the record",
              rec.get("matched_terms", 0) >= 2 and rec.get("title_match") is True
              and 0.0 < rec.get("max_term_idf", 0) <= 1.0, rec)

        # ==================================================================
        # 2. Recall noise gate
        # ==================================================================
        print("noise gate:")
        proj_b = _mkproject(); dirs.append(proj_b)
        sid_b = session_manager.register(proj_b)["session_id"]
        session_manager.mark_full_read(sid_b)
        # 12-node graph; "continue" sits in notes of exactly 2 nodes
        # (idf ≈ 0.72: clears the score threshold, fails the solo-idf bar);
        # "deploy" likewise df=2 but names a gist (title match).
        for i in range(8):
            store.put_node(level="project", node_id=f"filler-{i}",
                           gist=f"Unrelated filler topic number {i} about area{i}",
                           session_id=sid_b)
        store.put_node(level="project", node_id="rate-limit-recovery",
                       gist="Recovery pattern after the 5h limit resets",
                       notes=["after limits reset we continue the queued work"],
                       session_id=sid_b)
        store.put_node(level="project", node_id="queue-drain",
                       gist="Drain order for the overnight queue",
                       notes=["drained jobs continue from their checkpoint"],
                       session_id=sid_b)
        store.put_node(level="project", node_id="deploy-shorthand",
                       gist="Deploy means: run the standard prod loop",
                       notes=["one fix per push, verify before next"],
                       session_id=sid_b)
        store.put_node(level="project", node_id="zephyr-widget",
                       gist="Zephyr widget entry point",
                       notes=["widget styling lives in the shared bundle",
                              "xylograph exporter feeds the preview"],
                       session_id=sid_b)
        # second "deploy" mention in notes so df=2 (not near-unique)
        store.put_node(level="project", node_id="release-notes-habit",
                       gist="Release notes habit for the changelog",
                       notes=["written right after a deploy lands"],
                       session_id=sid_b)

        text = build_prompt_recall(store, session_manager, proj_b, "Continue")
        check("single common term, notes-only match -> silent", text is None, text)

        text = build_prompt_recall(store, session_manager, proj_b, "deploying?")
        check("single common term naming a gist -> speaks (title match)",
              text is not None and "deploy-shorthand" in text, text)

        text = build_prompt_recall(store, session_manager, proj_b, "xylograph")
        check("near-unique term, notes-only -> speaks",
              text is not None and "zephyr-widget" in text, text)

        session_manager._sessions[sid_b]["seen_ids"] = []  # reset dedup between cases
        text = build_prompt_recall(store, session_manager, proj_b, "widget styling bundle")
        check("two corroborating notes-only terms -> speaks",
              text is not None and "zephyr-widget" in text, text)

        # ==================================================================
        # 3. Source-agnostic bootstrap recovery
        # ==================================================================
        print("bootstrap lineage:")
        proj_c = _mkproject(); dirs.append(proj_c)
        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-origin",
            "source": "startup"}).json()
        kg_sid = r["session_id"]
        check("fresh startup preloads in full",
              r["reused"] is False and "KG MEMORY PRELOADED" in r["context"], r)

        tf = Path(tempfile.mkdtemp(prefix="kg-test-tr-", dir=str(Path.home() / ".cache"))) / "forked.jsonl"
        tf.write_text(f'{{"x":"KG MEMORY PRELOADED ... session_id: {kg_sid} (pass it)"}}\n')

        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-fork-1",
            "source": "fork", "transcript_path": str(tf)}).json()
        check("source=fork recovers via transcript, continuity note only",
              r["reused"] is True and r["session_id"] == kg_sid
              and "resumed" in r["context"] and "PRELOADED" not in r["context"], r)
        check("fork binds the new claude sid",
              session_manager.find_by_claude_sid("cc-fork-1")[0] == kg_sid)

        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-fork-2",
            "source": "some-future-source", "transcript_path": str(tf)}).json()
        check("unknown future source recovers the same way",
              r["reused"] is True and r["session_id"] == kg_sid, r)

        bare = tf.parent / "fresh.jsonl"
        bare.write_text('{"x":"no markers here"}\n')
        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-fresh",
            "source": "startup", "transcript_path": str(bare)}).json()
        check("markerless transcript registers fresh", r["reused"] is False, r)

        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-clear",
            "source": "clear", "transcript_path": str(tf)}).json()
        check("clear starts fresh despite recoverable transcript",
              r["reused"] is False and r["session_id"] != kg_sid, r)

        r = client.get("/api/session_bootstrap", params={
            "project_path": proj_c, "claude_session_id": "cc-fork-2",
            "source": "compact", "transcript_path": str(tf)}).json()
        check("compact re-renders the full preload for a reused session",
              r["reused"] is True and "KG MEMORY PRELOADED" in r["context"], r)

        # ==================================================================
        # 4. Near-duplicate nudge on create
        # ==================================================================
        print("near-dup nudge:")
        proj_d = _mkproject(); dirs.append(proj_d)
        sid_d = session_manager.register(proj_d)["session_id"]
        for i in range(10):
            store.put_node(level="project", node_id=f"distinct-{i}",
                           gist=f"Standalone topic {i} covering realm{i} only",
                           session_id=sid_d)
        store.put_node(level="project", node_id="mutating-script-needs-readback",
                       gist="Scripts mutating live config must verify, back up, read back, auto-rollback",
                       session_id=sid_d)

        res = store.put_node(level="project", node_id="mutating-config-scripts-verify-readback",
                             gist="Scripts mutating live config must verify, back up, read back, and auto-rollback",
                             session_id=sid_d)
        dup = res.get("near_duplicate")
        check("near-duplicate create returns the existing node",
              dup is not None and dup["id"] == "mutating-script-needs-readback", dup)
        check("write itself proceeded",
              "mutating-config-scripts-verify-readback" in store.graphs[
                  [k for k in store.graphs if proj_d in k][0]]["nodes"])

        res = store.put_node(level="project", node_id="safari-subgrid-gap",
                             gist="Safari subgrid gap rendering quirk needs explicit row gap",
                             session_id=sid_d)
        check("distinct create has no nudge", res.get("near_duplicate") is None,
              res.get("near_duplicate"))

        res = store.put_node(level="project", node_id="mutating-script-needs-readback",
                             gist="Scripts mutating live config must verify, back up, read back, auto-rollback (v2)",
                             session_id=sid_d)
        check("update of existing id never nudges", res.get("near_duplicate") is None)

        proj_e = _mkproject(); dirs.append(proj_e)
        sid_e = session_manager.register(proj_e)["session_id"]
        store.put_node(level="project", node_id="only-node",
                       gist="Tiny graph seed", session_id=sid_e)
        res = store.put_node(level="project", node_id="second-node",
                             gist="Tiny graph seed sibling", session_id=sid_e)
        check("tiny graph (<10 others) never nudges", res.get("near_duplicate") is None)

        # ==================================================================
        # 5. Hub-mention nudge (entity smearing prevention)
        # ==================================================================
        print("hub-mention nudge:")
        # proj_d already has 12 distinct nodes; add a hub + two mentioners so
        # 'zephyr' reaches df=3, then create a chronicle that re-describes it.
        store.put_node(level="project", node_id="zephyr-gateway",
                       gist="Zephyr gateway owns all upstream API traffic shaping",
                       session_id=sid_d)
        store.put_node(level="project", node_id="perf-audit-2026-06-01",
                       gist="Perf audit: zephyr added 40ms p95; accepted",
                       session_id=sid_d)
        store.put_node(level="project", node_id="oncall-runbook",
                       gist="Oncall runbook covers zephyr restarts and cache flushes",
                       session_id=sid_d)
        res = store.put_node(level="project", node_id="deploy-2026-07-28-notes",
                             gist="Deploy notes: bumped the zephyr sidecar and rotated its API token",
                             session_id=sid_d)
        m = res.get("near_duplicate")
        check("chronicle mentioning a named entity gets the edge nudge",
              m is not None and m.get("kind") == "mention"
              and m.get("id") == "zephyr-gateway" and m.get("term", "").startswith("zephyr"), m)
        res = store.put_node(level="project", node_id="wholly-unrelated-topic",
                             gist="Quarterly billing export format switched to parquet",
                             session_id=sid_d)
        check("node mentioning no named entity gets no nudge",
              res.get("near_duplicate") is None, res.get("near_duplicate"))

        # ==================================================================
        # 6. Smear debt factor
        # ==================================================================
        print("smear factor:")
        from core.debt import compute_debt, debt_line, smeared_terms
        smear_nodes = [
            {"id": "zephyr-gateway", "gist": "Zephyr gateway owns traffic shaping"},
        ]
        for i in range(8):
            smear_nodes.append({"id": f"log-2026-0{(i % 6) + 1}-1{i} ", "gist": f"Session {i}: touched zephyr config again"})
        for i in range(4):
            smear_nodes.append({"id": f"other-{i}", "gist": f"Standalone area {i}"})
        sm = smeared_terms(smear_nodes, [], slug="acme-shop")
        check("smeared term detected with its hub",
              any(s["term"].startswith("zephyr") and s["hub"] == "zephyr-gateway" for s in sm), sm)
        sm2 = smeared_terms(smear_nodes, [], slug="zephyr-shop")
        check("project slug tokens are not smeared entities",
              not any(s["term"].startswith("zephyr") for s in sm2), sm2)
        d = compute_debt(smear_nodes, [], None, 3, slug="acme-shop")
        check("DEBT line renders the smear", "smeared:" in debt_line(d) and "zephyr" in debt_line(d),
              debt_line(d))

    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
