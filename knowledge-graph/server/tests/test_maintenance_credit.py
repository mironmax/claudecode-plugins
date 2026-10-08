#!/usr/bin/env python3
"""Maintenance credit: how a pass keeps a lesson in view without counting as use.

No pytest dependency — run directly with the project venv:

    cd knowledge-graph/server && ./venv/bin/python tests/test_maintenance_credit.py

Background (senior trial, 2026-10-08): a pass decided five rules should stay in
view, but its reads promote nothing by design, and the scorer archived them
again. A pass now credits a node 1-3 times; credits decay like endorsements.

Covers:
  1. A maintenance credit stamps the node `credits` times, records the event,
     and is logged as via "maintenance" with its weight
  2. Credits outside 1-3 are refused
  3. A working session cannot pass credits; without them it endorses as before
  4. One credit event per node per pass
  5. The pass total caps credits
  6. The evaluator drops maintenance and recurrence records, keeps endorsements
  7. A full node read shows when maintenance credited it

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_STORAGE = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ["KG_STORAGE_ROOT"] = _TMP_STORAGE

from core.constants import MAINTENANCE_CREDITS_PER_PASS, USEFUL_LOG_NAME  # noqa: E402
from eval.data import load_logs  # noqa: E402
from mcp_http.read_format import format_node_full  # noqa: E402
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


def log_records():
    path = Path(_TMP_STORAGE) / USEFUL_LOG_NAME
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main():
    project = tempfile.mkdtemp(prefix="kg-test-project-", dir=str(Path.home() / ".cache"))
    try:
        session_manager = HTTPSessionManager()
        store = MultiProjectGraphStore(GraphConfig(), session_manager)
        work = session_manager.register(project)["session_id"]
        chore = session_manager.register(project)["session_id"]
        store.mark_maintenance(chore)
        for i in range(8):
            store.put_node(level="user", node_id=f"rule-{i}", gist=f"Rule {i}", session_id=work)
        nodes = store.graphs["user"]["nodes"]

        print("maintenance credit:")
        res = store.mark_useful(["rule-0"], chore, credits=2)
        check("accepted", res["accepted"] == ["rule-0"], res)
        check("stamps the node twice", len(nodes["rule-0"].get("_useful_ts", [])) == 2)
        check("records one credit event on the node", len(nodes["rule-0"].get("_credited_ts", [])) == 1)
        rec = log_records()[-1]
        check("logged as maintenance with its weight",
              rec.get("via") == "maintenance" and rec.get("credits") == 2, rec)
        check("remaining counts the pass's credits",
              res["remaining"] == MAINTENANCE_CREDITS_PER_PASS - 2, res)

        print("weight bounds:")
        for bad in (0, 4):
            res = store.mark_useful(["rule-1"], chore, credits=bad)
            check(f"credits={bad} refused", "rule-1" in res["rejected"] and not res["accepted"], res)
        check("a refused weight leaves the node untouched", not nodes["rule-1"].get("_useful_ts"))

        print("working session:")
        res = store.mark_useful(["rule-1"], work, credits=2)
        check("credits refused outside a pass", "maintenance pass" in res["rejected"].get("rule-1", ""), res)
        res = store.mark_useful(["rule-1"], work)
        check("an endorsement still stamps once", res["accepted"] == ["rule-1"]
              and len(nodes["rule-1"]["_useful_ts"]) == 1 and not nodes["rule-1"].get("_credited_ts"), res)
        check("logged without the maintenance route", log_records()[-1].get("via") != "maintenance")

        print("per node and per pass:")
        res = store.mark_useful(["rule-0"], chore, credits=1)
        check("one credit event per node per pass", "rule-0" in res["rejected"], res)
        res = store.mark_useful([f"rule-{i}" for i in range(2, 8)], chore, credits=3)
        # 2 used; 15 allows four more nodes at 3 (14), the fifth would make 17.
        check("the pass total caps credits", res["accepted"] == ["rule-2", "rule-3", "rule-4", "rule-5"]
              and set(res["rejected"]) == {"rule-6", "rule-7"}, res)
        check("a capped node is untouched", not nodes["rule-6"].get("_useful_ts"))

        print("evaluator:")
        with open(Path(_TMP_STORAGE) / USEFUL_LOG_NAME, "a") as f:
            f.write(json.dumps({"ts": 1.0, "id": "rule-0", "via": "recurrence"}) + "\n")
        _recall, useful = load_logs(Path(_TMP_STORAGE))
        vias = {u.get("via") for u in useful}
        check("maintenance and recurrence credits are not counted as use",
              "maintenance" not in vias and "recurrence" not in vias, vias)
        check("endorsements still are", any(u.get("id") == "rule-1" for u in useful))

        print("read:")
        text = format_node_full("rule-0", {"node": nodes["rule-0"], "level": "user", "edges": []})
        check("a full read shows the credit", "credited by maintenance: " in text, text)
        text = format_node_full("rule-1", {"node": nodes["rule-1"], "level": "user", "edges": []})
        check("and says nothing for an uncredited node", "credited by maintenance" not in text)
    finally:
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
