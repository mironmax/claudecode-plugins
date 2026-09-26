"""Can an unlocked session registration kill the store's saver thread?

Real code: MultiProjectGraphStore._periodic_save -> session_manager.cleanup_expired()
running on the saver thread, HTTPSessionManager.register() running on another
thread (in production: the asyncio event loop thread, which does not take
store.lock for register). Amplifiers, disclosed: 20k live sessions so the
iteration is long enough, save_interval=0.05s, and a 1e-5 s GIL switch interval.
Usage: python repro_saver_death.py [amplified|default]
  default: 500 sessions and CPython's default switch interval (save_interval stays 0.05s).
"""
import os, sys, tempfile, threading, time
from pathlib import Path
os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "server"))
from mcp_http.store import MultiProjectGraphStore, GraphConfig
from mcp_http.session_manager import HTTPSessionManager

died = {}
def hook(args):
    died[args.thread.name] = repr(args.exc_value)
threading.excepthook = hook
mode = sys.argv[1] if len(sys.argv) > 1 else "amplified"
if mode == "amplified":
    sys.setswitchinterval(1e-5)

sm = HTTPSessionManager()
sm.save_sessions = lambda: None      # register() fsyncs all sessions each call; stubbed so the test runs fast
for _ in range(20000 if mode == "amplified" else 500):
    sm.register(None)
store = MultiProjectGraphStore(GraphConfig(save_interval=0.05), sm, broadcast_callback=None)
store.saver_thread.name = "saver"

deadline = time.time() + 20
n = 0
while store.saver_thread.is_alive() and time.time() < deadline:
    sm.register(None); n += 1        # what every first kg_read / session_bootstrap does
alive = store.saver_thread.is_alive()
print(f"mode={mode} registrations={n}  saver alive={alive}  died={died}")
print("FAIL: saver thread is dead for the rest of the process" if not alive else "saver survived 20 s")
