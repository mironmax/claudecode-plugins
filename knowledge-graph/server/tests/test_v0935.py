#!/usr/bin/env python3
"""Self-contained regression tests for NODE ID RENAME and the id length rule.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_v0935.py

Background (measured 2026-08-28): node ids had been drifting longer every
month — mean words by creation month 3.4 -> 4.5 -> 5.1 -> 5.1 -> 6.4, 65% of
August ids over five words, worst at eleven. The only guidance anywhere was
"kebab-case", so the GIST doctrine ("compressed headline") bled into the id
and ids became sentence claims. Ids are load-bearing (search weights them x3
and matches them for the prompt-recall gate), so this is a retrieval problem, not
a cosmetic one — but nothing could be fixed, because there was no rename: a
put_node + delete_node loses timestamps, endorsements, version history and
every edge, and silently strands cross-level edges in project graphs that are
not loaded at the time.

Covers:
  1. Rename preserves the node's identity fields (_created_ts, _useful_ts,
     _archived) — the thing put+delete destroys
  2. Edges in the node's own graph are RE-KEYED, both directions, no ghost
     left under the old key
  3. Cross-level edges in a LOADED project graph follow the rename
  4. Cross-level edges in an UNLOADED project graph follow it too — the
     silent-loss path, and the reason this is a server primitive
  5. A project graph with its OWN node of that name is left alone (its edges
     are local references, not cross-level ones)
  6. A collision target is refused, not silently merged
  7. Version history moves with the name instead of resetting
  8. Session seen/preload state follows, so dedup survives a rename
  9. The length rule refuses a 7-word CREATE with a steering error
 10. ...and refuses a 7-word rename target
 11. ...but never blocks an UPDATE to a node named before the rule existed
 12. A date counts as one word, so dated audit ids stay legal
 13. The 6-word nudge fires without blocking
 14. A date in an id is nudged, not refused — it records when something was
     written down, never what it is

Uses a temp KG_STORAGE_ROOT and temp projects under ~/.cache.
"""

import json
import os
import time
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from core.exceptions import KGError  # noqa: E402
from core.utils import (  # noqa: E402
    node_id_warning,
    node_id_words,
    validate_new_node_id,
    validate_node_id,
)
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


def _seed_legacy(store, node_id, gist, level, session_id):
    """A node named BEFORE the length rule existed.

    put_node refuses to create one now, which is the rule working — but the
    live graph carries 177 of them, and they are exactly what has to stay
    updatable and renamable. Insert past the write boundary, then let put_node
    update it so the fixture carries real timestamps and version history.
    """
    with store.lock:
        _, graph_key = store._resolve_graph_key(level, session_id, None)
        store.graphs[graph_key]["nodes"][node_id] = {
            "id": node_id, "gist": "seed", "_created_ts": time.time(),
        }
    store.put_node(level=level, node_id=node_id, gist=gist, session_id=session_id)


def _raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except KGError as e:
        return str(e)
    return None


def main():
    dirs = []
    store = None
    try:
        session_manager = HTTPSessionManager()
        store = MultiProjectGraphStore(GraphConfig(), session_manager)

        proj_a, proj_b = _mkproject(), _mkproject()
        dirs += [proj_a, proj_b]
        sid_a = session_manager.register(proj_a)["session_id"]
        sid_b = session_manager.register(proj_b)["session_id"]

        # ---------------------------------------------------------------
        # A long-lived user node with history, endorsement and edges.
        # ---------------------------------------------------------------
        long_id = "a-401-in-a-log-is-an-event-not-a-state"
        _seed_legacy(store, long_id,
                     "An auth error in a log is an event, not a current state.",
                     "user", sid_a)
        store.put_node(level="user", node_id=long_id,
                       gist="An auth error in a log is an event, not a current state. v2",
                       session_id=sid_a)
        store.put_node(level="user", node_id="oauth-refresh-daemon",
                       gist="The daemon refreshes tokens unattended.", session_id=sid_a)
        store.mark_useful([long_id], sid_a)
        store.put_edge(level="user", from_ref="oauth-refresh-daemon", to_ref=long_id,
                       rel="supplies-mechanism-for", session_id=sid_a)
        store.put_edge(level="user", from_ref=long_id, to_ref="oauth-refresh-daemon",
                       rel="explained-by", session_id=sid_a)

        before = dict(store.graphs["user"]["nodes"][long_id])
        before_ver = store._versions["user"].get(f"node:{long_id}", {}).get("v")

        # Cross-level edge from a LOADED project graph up to the user node.
        store.put_node(level="project", node_id="scheduled-agent-deaths",
                       gist="Scheduled agents die on 401.", session_id=sid_a)
        store.put_edge(level="project", from_ref="scheduled-agent-deaths",
                       to_ref=long_id, rel="instance-of", session_id=sid_a)

        # Cross-level edge from a project graph we will then UNLOAD.
        store.put_node(level="project", node_id="nightly-tick",
                       gist="Nightly tick fires the maintenance pass.", session_id=sid_b)
        store.put_edge(level="project", from_ref="nightly-tick", to_ref=long_id,
                       rel="fails-under", session_id=sid_b)

        # A third project that owns a node by the SAME name — its edges are
        # local references and must NOT be touched.
        proj_c = _mkproject()
        dirs.append(proj_c)
        sid_c = session_manager.register(proj_c)["session_id"]
        _seed_legacy(store, long_id,
                     "A DIFFERENT node that happens to share the name.",
                     "project", sid_c)
        store.put_node(level="project", node_id="local-neighbour",
                       gist="Local neighbour.", session_id=sid_c)
        store.put_edge(level="project", from_ref="local-neighbour", to_ref=long_id,
                       rel="mentions", session_id=sid_c)

        # Session state that must follow the rename.
        session_manager.mark_seen(sid_a, [long_id])
        session_manager.set_preloaded(sid_a, [long_id, "oauth-refresh-daemon"])

        # Unload proj_b so its edge lives only on disk at rename time.
        from core.constants import project_namespace
        key_b = project_namespace(proj_b)
        path_b = store._persistence[key_b].path
        del store.graphs[key_b], store._persistence[key_b], store._versions[key_b]

        # ---------------------------------------------------------------
        new_id = "auth-401-is-an-event"
        result = store.rename_node(long_id, new_id, session_id=sid_a)
        # ---------------------------------------------------------------

        node = store.graphs["user"]["nodes"].get(new_id, {})
        check("1 identity fields survive the rename",
              long_id not in store.graphs["user"]["nodes"]
              and node.get("_created_ts") == before.get("_created_ts")
              and node.get("_useful_ts") == before.get("_useful_ts")
              and node.get("id") == new_id,
              f"{node.get('_created_ts')} vs {before.get('_created_ts')}")

        user_edges = store.graphs["user"]["edges"]
        check("2 own-graph edges re-keyed both directions, no ghost",
              ("oauth-refresh-daemon", new_id, "supplies-mechanism-for") in user_edges
              and (new_id, "oauth-refresh-daemon", "explained-by") in user_edges
              and not any(long_id in k for k in user_edges),
              list(user_edges))

        key_a = project_namespace(proj_a)
        check("3 cross-level edge in a LOADED project follows",
              ("scheduled-agent-deaths", new_id, "instance-of") in store.graphs[key_a]["edges"],
              list(store.graphs[key_a]["edges"]))

        on_disk_b = json.load(open(path_b))
        check("4 cross-level edge in an UNLOADED project follows",
              any(e["to"] == new_id for e in on_disk_b["edges"].values())
              and not any(e["to"] == long_id for e in on_disk_b["edges"].values()),
              list(on_disk_b["edges"]))

        key_c = project_namespace(proj_c)
        check("5 a project owning that id keeps its own local edge",
              ("local-neighbour", long_id, "mentions") in store.graphs[key_c]["edges"]
              and long_id in store.graphs[key_c]["nodes"],
              list(store.graphs[key_c]["edges"]))

        collide = _raises(store.rename_node, "oauth-refresh-daemon", new_id, session_id=sid_a)
        check("6 collision refused, not merged",
              collide is not None and new_id in collide
              and "oauth-refresh-daemon" in store.graphs["user"]["nodes"],
              collide)

        after_ver = store._versions["user"].get(f"node:{new_id}", {}).get("v")
        check("7 version history moves with the name",
              f"node:{long_id}" not in store._versions["user"]
              and after_ver is not None and before_ver is not None and after_ver > before_ver,
              f"{before_ver} -> {after_ver}")

        check("8 session seen/preload state follows",
              new_id in session_manager.get_seen(sid_a)
              and new_id in session_manager.get_preloaded(sid_a)
              and long_id not in session_manager.get_seen(sid_a),
              session_manager.get_seen(sid_a))

        # ---------------------------------------------------------------
        # The length rule
        # ---------------------------------------------------------------
        seven = "shell-integer-test-on-jq-float-masquerades-as-policy-skip"
        err = _raises(store.put_node, level="user", node_id=seven,
                      gist="A float read as an integer skips the policy branch.",
                      session_id=sid_a)
        check("9 a 7+ word CREATE is refused with a steering error",
              err is not None and "NAMES THE SUBJECT" in err
              and seven not in store.graphs["user"]["nodes"], err)

        err = _raises(store.rename_node, "oauth-refresh-daemon", seven, session_id=sid_a)
        check("10 a 7+ word rename TARGET is refused",
              err is not None and "NAMES THE SUBJECT" in err
              and "oauth-refresh-daemon" in store.graphs["user"]["nodes"], err)

        # A node named before the rule existed, injected past the write
        # boundary the way the live graph carries 177 of them.
        legacy = "cd-chained-into-git-is-hardcoded-no-allow-rule-beats-it"
        _seed_legacy(store, legacy, "Legacy.", "user", sid_a)
        err = _raises(store.put_node, level="user", node_id=legacy,
                      gist="Legacy node, updated.", session_id=sid_a)
        check("11 an UPDATE to a legacy long id is never blocked",
              err is None
              and store.graphs["user"]["nodes"][legacy]["gist"] == "Legacy node, updated.",
              err)

        dated = "ambient-recall-week5-audit-2026-08-25"
        check("12 a date counts as one word",
              node_id_words(dated) == 5 and _raises(validate_new_node_id, dated) is None,
              node_id_words(dated))

        six = "kg-maintain-tick-lives-outside-repo"
        check("13 the 6-word nudge fires without blocking",
              _raises(validate_new_node_id, six) is None
              and "6 words" in node_id_warning(six)
              and node_id_warning("kg-mcp-server") == "",
              node_id_warning(six))

        check("14 a dated id is nudged, not refused",
              _raises(validate_new_node_id, dated) is None
              and "date is a reference" in node_id_warning(dated)
              and node_id_warning("ambient-recall-week5-audit") == "",
              node_id_warning(dated))

        # Charset validation must stay independent of length (addressing an
        # existing long node must never fail).
        check("bonus: validate_node_id stays length-blind",
              _raises(validate_node_id, legacy) is None, "")

    finally:
        if store is not None:
            store.running = False
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
