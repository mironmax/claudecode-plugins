"""Deferred context for a harness whose MCP output can spill to a file.

View changes travel with the rendered snapshot, not with the small receipt.
The hook prepares a bounded UTF-8 message, prints it, then acknowledges it.
Unacknowledged messages are retried; only the last chunk commits the view.
Queues live in the session record so a server restart does not lose a read.
"""

import uuid

INLINE_BYTES = 3500  # Also fits the CLI's default lazy-tool route (4096).
HOOK_BYTES = 40000  # Measured intact on agy 1.2.15; includes our framing.
QUEUE_BYTES = 1024 * 1024
QUEUE_ITEMS = 64

VIEW_METHODS = frozenset({
    "mark_seen", "note_viewed", "mark_promoted", "mark_full_read",
    "mark_synced", "set_preloaded",
})


class DeferredView:
    """Session-manager facade buffering only context-dependent mutations."""

    def __init__(self, manager):
        self.manager = manager
        self.effects = []

    def __getattr__(self, name):
        if name not in VIEW_METHODS:
            return getattr(self.manager, name)

        def record(*args, **kwargs):
            # Materialize iterables now; effects are persisted as JSON.
            args = [list(x) if isinstance(x, (set, tuple)) else x for x in args]
            self.effects.append({"method": name, "args": args, "kwargs": kwargs})
        return record

    def commit(self):
        apply_effects(self.manager, self.effects)


def apply_effects(manager, effects):
    for effect in effects:
        if effect["method"] not in VIEW_METHODS:
            raise ValueError("Unknown deferred view change")
        getattr(manager, effect["method"])(*effect["args"], **effect["kwargs"])


def enqueue(data, text, kind, effects):
    """Append without evicting anything already promised to the model."""
    queue = data.setdefault("agy_pending", [])
    size = len(text.encode("utf-8"))
    if len(queue) >= QUEUE_ITEMS or size + sum(
            len(item["text"].encode("utf-8")) for item in queue) > QUEUE_BYTES:
        return False
    queue.append({"id": uuid.uuid4().hex, "kind": kind, "text": text,
                  "offset": 0, "effects": effects})
    return True


def prepare(data, sid):
    """One retryable packet. Caller holds the session manager's lock."""
    if data.get("agy_delivery"):
        return data["agy_delivery"]
    queue = data.get("agy_pending") or []
    if not queue:
        return None
    # Reserve framing bytes before slicing, including the continuation hint.
    remaining = HOOK_BYTES - 700
    parts, selected = [], []
    for item in queue:
        header = f"KG context — {item['kind']}:\n"
        available = remaining - len(header.encode("utf-8")) - 2
        if available <= 0:
            break
        start = item["offset"]
        chunk = item["text"][start:].encode("utf-8")[:available].decode("utf-8", errors="ignore")
        if not chunk:
            break
        end = start + len(chunk)
        selected.append({"id": item["id"], "end": end})
        part = header + chunk
        parts.append(part)
        remaining -= len(part.encode("utf-8")) + 2
        if end < len(item["text"]):
            break
    more = (len(selected) < len(queue)
            or selected[-1]["end"] < len(queue[len(selected) - 1]["text"]))
    text = "\n\n".join(parts)
    if more:
        text += (f"\n\nKG delivery continues; this is a partial memory reply. "
                 f"Call kg_sync(session_id='{sid}') to trigger the next hook "
                 "before relying on or updating this reply. The read is complete "
                 "only after all parts arrive.")
    packet = {"id": uuid.uuid4().hex, "text": text, "selected": selected}
    data["agy_delivery"] = packet
    return packet


def acknowledge(manager, data, delivery_id):
    packet = data.get("agy_delivery")
    if not packet or packet["id"] != delivery_id:
        return False
    queue = data["agy_pending"]
    for selection in packet["selected"]:
        item = queue[0]
        if item["id"] != selection["id"]:
            raise ValueError("Deferred context order changed")
        item["offset"] = selection["end"]
        if item["offset"] < len(item["text"]):
            break
        apply_effects(manager, item["effects"])
        queue.pop(0)
    data.pop("agy_delivery", None)
    return True
