"""Foreign writes: what other sessions wrote while this one worked, pushed.

A session assumes it works alone: measured on parallel runs, no session ever
called kg_sync unprompted, and five concurrent sessions wrote one lesson as
four separate nodes. Pull needs the model to suspect concurrency, so the
server says it instead — on the hook replies and on kg_put_node/kg_search —
once per change, gists only, and silently when nothing changed.
"""

import time

MAX_PUSHED = 3
GIST_CHARS = 300


def notice(store, session_manager, session_id: str | None) -> str | None:
    """Gists of nodes other sessions wrote since this session last looked."""
    if not session_id:
        return None
    window = session_manager.claim_push_window(session_id, time.time())
    if window is None:
        return None
    since, seen_at = window
    diff = store.get_sync_diff(session_id, since)

    changed = []
    for level in ("project", "user"):
        for nid, node in diff[level]["nodes"].items():
            written = (node.get("_written") or {}).get("ts", 0)
            if node.get("_archived") or seen_at.get(nid, 0) >= written:
                continue  # archived, or already read as it stands now
            changed.append((written, nid, node, nid in seen_at))
    if not changed:
        return None
    changed.sort(key=lambda c: c[0], reverse=True)

    shown = changed[:MAX_PUSHED]
    lines = ["While you worked, other sessions wrote to this memory:"]
    for _ts, nid, node, known in shown:
        lines.append(f"- {nid} ({'updated' if known else 'new'}): {node.get('gist', '')[:GIST_CHARS]}")
    if len(changed) > MAX_PUSHED:
        lines.append(f"+{len(changed) - MAX_PUSHED} more — kg_sync(session_id) lists them.")
    lines.append("If one already holds a lesson you are about to write, update that node "
                 "instead of adding another.")
    session_manager.mark_seen(session_id, [c[1] for c in shown], via="push", at=time.time())
    return "\n".join(lines)
