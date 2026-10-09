#!/usr/bin/env bash
# Re-run every model and reproduction; logs land in <concern>/evidence/.
# Needs: lean (elan, toolchain pinned in ./lean-toolchain) and the server's
# Python deps (fastapi + httpx for the REST/WS reproductions); set PYTHON
# to use another interpreter, such as the server venv.
# Reproductions print BUG/FAIL lines while the findings are unfixed; that is
# the expected output, so this script never stops on a failing reproduction.
set -u
cd "$(dirname "$0")"
export PATH="$HOME/.elan/bin:$PATH"
PY=${PYTHON:-python3}   # e.g. PYTHON="$(realpath ../knowledge-graph/server/venv/bin/python)"

run() {  # run <log> <cmd...>
  local log=$1; shift
  mkdir -p "$(dirname "$log")"
  echo "== $* -> $log"
  { echo "\$ $*"; "$@" 2>&1 | grep -v -E '^INFO|TestClient|testclient'; } > "$log"
  tail -n 3 "$log" | sed 's/^/   /'
}

run chore-dispatch/evidence/model.log        lean --run chore-dispatch/lean/Dispatch.lean
run chore-dispatch/evidence/repro-spawn_fail.log $PY chore-dispatch/repro/repro_stale_state.py spawn_fail
run chore-dispatch/evidence/repro-quick_exit.log $PY chore-dispatch/repro/repro_stale_state.py quick_exit
run chore-dispatch/evidence/tiers-model.log   lean --run chore-dispatch/lean/Tiers.lean
run chore-dispatch/evidence/check-rules.log   $PY chore-dispatch/repro/check_rules.py
run chore-dispatch/evidence/repro-agy-orphan-after.log $PY chore-dispatch/repro/repro_agy_orphan.py
run chore-dispatch/evidence/repro-runner-pin-after.log $PY chore-dispatch/repro/repro_runner_pin.py
run chore-dispatch/evidence/repro-state-write-after.log $PY chore-dispatch/repro/repro_state_write.py
run chore-dispatch/evidence/repro-wedge-after.log $PY chore-dispatch/repro/repro_wedge.py
run chore-dispatch/evidence/repro-gauge-carried-after.log $PY chore-dispatch/repro/repro_gauge_carried.py
run chore-dispatch/evidence/repro-midflight-config-after.log $PY chore-dispatch/repro/repro_midflight.py config
run chore-dispatch/evidence/repro-midflight-suspend-after.log $PY chore-dispatch/repro/repro_midflight.py suspend
run budget-notices/evidence/model.log         lean --run budget-notices/lean/Budget.lean
run budget-notices/evidence/repro-agy-stale-after.log $PY budget-notices/repro/repro_agy_stale.py
run budget-notices/evidence/check-budget.log  $PY budget-notices/repro/check_budget.py
run lifecycle/evidence/model.log             lean --run lifecycle/lean/Lifecycle.lean
run lifecycle/evidence/model-shim.log        lean --run lifecycle/lean/Shim.lean
run lifecycle/evidence/repro-after.log       $PY lifecycle/repro/repro_lifecycle.py
run lifecycle/evidence/repro-shim.log        $PY lifecycle/repro/repro_shim.py
run lifecycle/evidence/repro-retry.log       $PY lifecycle/repro/repro_retry.py
run credits/evidence/model.log               lean --run credits/lean/Credits.lean
run credits/evidence/repro.log               $PY credits/repro/repro_credits.py
run store-persistence/evidence/model.log     lean --run store-persistence/lean/Persist.lean
run store-persistence/evidence/repro.log     $PY store-persistence/repro/repro_failed_save.py
run rename/evidence/model.log                lean --run rename/lean/Rename.lean
run rename/evidence/repro.log                $PY rename/repro/repro_rename.py
run sessions/evidence/model.log              lean --run sessions/lean/Sessions.lean
run sessions/evidence/repro-fork.log         $PY sessions/repro/repro_fork.py
run sessions/evidence/repro-fork-unbound.log $PY sessions/repro/repro_fork_unbound.py
run sessions/evidence/repro-saver-amplified.log timeout 120 $PY sessions/repro/repro_saver_death.py amplified
run sessions/evidence/repro-saver-default.log   timeout 120 $PY sessions/repro/repro_saver_death.py default
run websocket/evidence/model.log             lean --run websocket/lean/Subscribe.lean
run websocket/evidence/repro.log             $PY websocket/repro/repro_ws.py
run compaction/evidence/search.log           $PY compaction/repro/thrash_search.py
run compaction/evidence/model.log            lean --run compaction/lean/Compaction.lean
run compaction/evidence/repro.log            $PY compaction/repro/repro_tick.py
run compaction/evidence/tick-store-v1.log    $PY compaction/repro/tick_search.py store v1 20000
run compaction/evidence/tick-store-v2.log    $PY compaction/repro/tick_search.py store v2 20000
run security/evidence/repro.log              $PY security/repro/repro_csrf.py
run delivery/evidence/model-paging.log       lean --run delivery/lean/Paging.lean
run delivery/evidence/model-agy.log          lean --run delivery/lean/Agy.lean
run delivery/evidence/repro-paging-after.log $PY delivery/repro/repro_paging.py
run delivery/evidence/repro-agy-after.log    $PY delivery/repro/repro_agy.py
run concurrent-writes/evidence/model.log     lean --run concurrent-writes/lean/Writes.lean
run concurrent-writes/evidence/repro-after.log $PY concurrent-writes/repro/repro_lost_update.py
