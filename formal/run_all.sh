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
run store-persistence/evidence/model.log     lean --run store-persistence/lean/Persist.lean
run store-persistence/evidence/repro.log     $PY store-persistence/repro/repro_failed_save.py
run rename/evidence/model.log                lean --run rename/lean/Rename.lean
run rename/evidence/repro.log                $PY rename/repro/repro_rename.py
run sessions/evidence/model.log              lean --run sessions/lean/Sessions.lean
run sessions/evidence/repro-fork.log         $PY sessions/repro/repro_fork.py
run sessions/evidence/repro-saver-amplified.log timeout 120 $PY sessions/repro/repro_saver_death.py amplified
run sessions/evidence/repro-saver-default.log   timeout 120 $PY sessions/repro/repro_saver_death.py default
run websocket/evidence/repro.log             $PY websocket/repro/repro_ws.py
run compaction/evidence/search.log           $PY compaction/repro/thrash_search.py
run security/evidence/repro.log              $PY security/repro/repro_csrf.py
run concurrent-writes/evidence/model.log     lean --run concurrent-writes/lean/Writes.lean
run concurrent-writes/evidence/repro-after.log $PY concurrent-writes/repro/repro_lost_update.py
