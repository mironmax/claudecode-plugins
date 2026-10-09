"""Foreign writes: what other sessions wrote while this one worked, pushed.

A session assumes it works alone: measured on parallel runs, no session ever
called kg_sync unprompted, and five concurrent sessions wrote one lesson as
four separate nodes. Pull needs the model to suspect concurrency, so the
server says it instead — on the hook replies and on kg_put_node/kg_search —
once per change, gists only, and silently when nothing changed.

Only writes that could hold this session's lesson are pushed. A maintenance
session's rewording or rename teaches nothing new. The user graph is shared
by every project, so a user-level write is pushed only when it came from a
session in this session's project (or both are user-only).
"""

import time

MAX_PUSHED = 3
GIST_CHARS = 300


def notice(store, session_manager, session_id: str | None) -> str | None:
    """Gists of nodes other sessions wrote since this session last looked."""
    if not session_id:
        return None
    # Through a deferred view (Antigravity), the push mark and the seen mark
    # are recorded with the reply's delivery: a refused reply leaves the
    # window open and nothing marked.
    now = time.time()
    deferred = getattr(session_manager, "deferred", False)
    window = session_manager.claim_push_window(session_id, now, claim=not deferred)
    if window is None:
        return None
    since, seen_at = window
    if deferred:
        session_manager.mark_pushed(session_id, now)
        # Replies already queued are delivered or replayed, never dropped:
        # what they will show is not pushed a second time.
        pending_since, pending_seen = session_manager.pending_marks(session_id)
        since = max(since, pending_since)
        for nid, at in pending_seen.items():
            seen_at[nid] = max(seen_at.get(nid, 0), at)
    diff = store.get_sync_diff(session_id, since)
    own = session_manager.lookup(session_id) or {}

    changed = []
    for level in ("project", "user"):
        for nid, node in diff[level]["nodes"].items():
            stamp = node.get("_written") or {}
            written, writer = stamp.get("ts", 0), stamp.get("by")
            # The diff also lists a rename, a promotion or an unchanged re-put:
            # a new version, but no write since this session last looked.
            if written <= since or writer == session_id:
                continue
            if node.get("_archived") or seen_at.get(nid, 0) >= written:
                continue  # archived, or already read as it stands now
            if store.is_maintenance(writer):
                continue
            if level == "user" and not _same_project(session_manager, writer, own):
                continue
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


def _same_project(session_manager, writer: str | None, own: dict) -> bool:
    """Whether the writing session worked in this session's project. An
    unknown writer (expired, or a write from before sessions were stamped)
    is not assumed to be local."""
    record = session_manager.lookup(writer) if writer else None
    return record is not None and record.get("project_path") == own.get("project_path")
