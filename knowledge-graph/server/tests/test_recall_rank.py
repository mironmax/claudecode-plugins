#!/usr/bin/env python3
"""Self-contained regression tests for prompt-recall INJECTION ORDER.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_recall_rank.py

Locks the 2026-08-10 fix (week-4 audit). Recall used to order its hits by
(title_match, max_term_idf, score). IDF is computed per graph, so the key
inverted the ranking: a user graph is heterogeneous and nearly any specific
term is unique in it (idf ~1.0), while a project graph is topically dense and
the very vocabulary its nodes are ABOUT scores low. One rare-word coincidence
therefore outranked a node matching ten prompt terms — measured live, the
prompt "rewrite for version 2.0 of MCP … do you have notes" dropped all three
correct nodes for a one-term user hit.

Covers:
  1. The asymmetry is real — the same term is rare in a small user graph and
     dull in a topic-dense project graph (guards the premise, not the fix)
  2. A broad topical hit outranks a one-term rarity when both name it
  3. title_match still leads: a named node beats a higher-scoring notes-only
     match (this half of the old key was never the problem)
  4. max_term_idf still GATES — demoted from ranking, not deleted: a
     single-term notes-only brush stays silent
  5. The cap is 4, and four hits actually ride one injection

Uses a temp KG_STORAGE_ROOT and temp projects under ~/.cache.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from core.constants import PROMPT_RECALL_MAX_HITS  # noqa: E402
from mcp_http.ambient import _terms, build_prompt_recall  # noqa: E402
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


def _mkproject():
    return tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))


def _injected_ids(text):
    """Node ids in injection order, hits only (connectors are indented)."""
    ids = []
    for line in (text or "").splitlines():
        if line.startswith("- ["):
            ids.append(line.split("] ", 1)[1].split(":")[0].split(" (in context)")[0])
    return ids


def main():
    print("=== prompt-recall injection order ===")

    config = GraphConfig(save_interval=9999)
    session_manager = HTTPSessionManager()
    store = MultiProjectGraphStore(config, session_manager, broadcast_callback=None)

    dirs = []
    try:
        proj = _mkproject(); dirs.append(proj)
        sid = session_manager.register(proj)["session_id"]
        session_manager.mark_full_read(sid)

        # ------------------------------------------------------------------
        # Fixture: a topic-DENSE project graph and a heterogeneous user graph,
        # the live shape that produced the inversion.
        # ------------------------------------------------------------------
        # "migration" recurs across the project graph — dull there by design.
        # No project node says "callbacks": that word is the user graph's, and
        # unique there, which is the whole asymmetry under test.
        # Every one of these shares the same vocabulary — that is what a
        # topic-dense project graph looks like, and it drives each term's df
        # up and its idf down. None of them holds a term unique to itself.
        for nid, gist in [
            # Identical word SET in all four, only the order differs — no term
            # is unique to any one of them, which is the point.
            ("mcp-migration-shape",
             "mcp migration rewrite scope: server startup decorators become handlers"),
            ("mcp-migration-dep-cap",
             "server startup handlers become decorators in the mcp migration rewrite scope"),
            ("mcp-migration-notes",
             "rewrite scope for the mcp migration: handlers become decorators at server startup"),
            ("mcp-migration-tests",
             "scope of the mcp migration rewrite — startup server decorators become handlers"),
        ]:
            store.put_node(level="project", node_id=nid, gist=gist, session_id=sid)
        for i in range(8):
            store.put_node(level="project", node_id=f"filler-{i}",
                           gist=f"Unrelated filler topic number {i} about area{i}",
                           session_id=sid)

        # The user graph: small and heterogeneous, so BOTH words this node
        # shares with the prompt are unique in it (idf ~1.0) — enough score to
        # clear the gate, and the maximum possible per-term rarity. It is a
        # genuine near-miss, the exact shape that used to win: a lesson about
        # reporting, matching a prompt about a migration on two stray words.
        store.put_node(level="user", node_id="verify-before-reporting",
                       gist="Re-run a failing callbacks rewrite before reporting it broken",
                       session_id=sid)
        for i in range(5):
            store.put_node(level="user", node_id=f"user-filler-{i}",
                           gist=f"Cross-project lesson number {i} about subject{i}",
                           session_id=sid)

        # Every content word here is dense in the project graph except
        # "callbacks", which only the user node holds.
        prompt = ("going to rewrite the mcp migration scope, where the server startup "
                  "decorators become handlers and callbacks")

        # 1. The premise: the user hit rides ONE unique word, the project hits
        #    ride the prompt's whole vocabulary — and the one word wins on idf.
        #    Search through _terms(), as the recall path does: passing raw
        #    prompt text would leave punctuation glued to tokens ("migration,")
        #    and invent unique terms this fixture is built to avoid.
        print("per-graph idf asymmetry:")
        res = store.search(" ".join(_terms(prompt)), session_id=sid, seen=set(), top_k=10)
        by_level = {}
        for r in res["top"]:
            by_level.setdefault(r["level"], []).append(r)
        best_user = max(by_level.get("user", []), key=lambda r: r["max_term_idf"], default=None)
        best_proj = max(by_level.get("project", []), key=lambda r: r["score"], default=None)
        check("a term unique in the user graph scores near 1.0",
              best_user and best_user["max_term_idf"] >= 0.85, best_user)
        check("the dense project hit is duller per-term despite matching more",
              best_user and best_proj
              and best_proj["max_term_idf"] < best_user["max_term_idf"]
              and best_proj["matched_terms"] > best_user["matched_terms"],
              f"project={best_proj} user={best_user}")
        check("...and it is the project hit that scores higher overall",
              best_user and best_proj and best_proj["score"] > best_user["score"],
              f"project={best_proj['score']} user={best_user['score']}")

        # 2. The fix: breadth beats the one-word rarity.
        print("injection order:")
        text = build_prompt_recall(store, session_manager, proj, prompt)
        ids = _injected_ids(text)
        check("recall speaks on a topical prompt", bool(ids), text)
        check("a broad project hit leads, not the one-term user rarity",
              ids and ids[0].startswith("mcp-migration"), ids)
        check("the one-term user hit does not displace the topic",
              sum(1 for i in ids if i.startswith("mcp-migration")) >= 2, ids)

        # 3. title_match still leads — it is a per-node fact, not a
        #    cross-graph-calibrated number, so it ranks honestly.
        proj_b = _mkproject(); dirs.append(proj_b)
        sid_b = session_manager.register(proj_b)["session_id"]
        session_manager.mark_full_read(sid_b)
        store.put_node(level="project", node_id="zephyr-widget-entry",
                       gist="Zephyr widget entry point and its mount order",
                       session_id=sid_b)
        for i in range(9):
            store.put_node(level="project", node_id=f"b-filler-{i}",
                           # zephyr buried in notes, repeatedly: scores without naming
                           gist=f"Area {i} overview",
                           notes=["zephyr zephyr zephyr appears here in passing",
                                  f"unrelated body text {i}"],
                           session_id=sid_b)
        text_b = build_prompt_recall(store, session_manager, proj_b,
                                     "where is the zephyr widget entry point")
        ids_b = _injected_ids(text_b)
        check("a node NAMED by the prompt leads a notes-only mention",
              ids_b and ids_b[0] == "zephyr-widget-entry", ids_b)

        # 4. max_term_idf demoted from ranking but still gating: a lone
        #    moderately-common notes-only term must stay silent.
        proj_c = _mkproject(); dirs.append(proj_c)
        sid_c = session_manager.register(proj_c)["session_id"]
        session_manager.mark_full_read(sid_c)
        for i in range(10):
            store.put_node(level="project", node_id=f"c-filler-{i}",
                           gist=f"Area {i} overview", session_id=sid_c)
        for nid in ("c-queue-drain", "c-rate-limit-recovery"):
            store.put_node(level="project", node_id=nid,
                           gist=f"{nid} behaviour",
                           notes=["we continue the queued work after a reset"],
                           session_id=sid_c)
        silent = build_prompt_recall(store, session_manager, proj_c, "continue")
        check("a single dull notes-only term still injects nothing",
              silent is None, silent)

        # 5. The cap.
        print("cap:")
        check("PROMPT_RECALL_MAX_HITS is 4", PROMPT_RECALL_MAX_HITS == 4,
              PROMPT_RECALL_MAX_HITS)
        proj_d = _mkproject(); dirs.append(proj_d)
        sid_d = session_manager.register(proj_d)["session_id"]
        session_manager.mark_full_read(sid_d)
        # Four nodes, each NAMED by a different term of the prompt, so each
        # clears the threshold on its own evidence rather than by crowding.
        for nid, gist in [
            ("checkout-order-flow", "Checkout order and the cart handoff"),
            ("payment-gateway-retry", "Payment gateway retry policy"),
            ("webhook-timeout-handling", "Webhook timeout handling"),
            ("refund-ledger-sync", "Refund ledger sync against the provider"),
        ]:
            store.put_node(level="project", node_id=nid, gist=gist, session_id=sid_d)
        for i in range(8):
            store.put_node(level="project", node_id=f"d-filler-{i}",
                           gist=f"Area {i} overview", session_id=sid_d)
        text_d = build_prompt_recall(
            store, session_manager, proj_d,
            "checkout order is broken: the payment gateway retry loops, the webhook "
            "timeout fires, and the refund ledger never syncs")
        check("four hits ride one injection", len(_injected_ids(text_d)) == 4,
              _injected_ids(text_d))
    finally:
        store.running = False
        import shutil
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
