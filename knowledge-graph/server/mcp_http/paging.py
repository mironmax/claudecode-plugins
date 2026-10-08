"""Paged kg_read replies for clients that cut or spill long tool output.

A reply longer than the client's tool-part limit (harness profile, in the
unit that client counts) goes out in parts cut at line ends. The read's
session and graph effects are recorded, not applied (delivery.DeferredView),
then split by part: a node counts as seen, viewed or read only when the part
showing it goes out, and the full read completes with its last part. The
model asks for the next part with kg_read(session_id, more=true); a new read
replaces whatever was still pending, and nothing undelivered is marked.
"""

import re

from mcp.types import TextContent

from . import harness
from .delivery import DeferredView, apply_effects, apply_graph_effects

FOOTER = ("\n\n[KG reply part {i} of {n} — it continues. Call "
          "kg_read(session_id='{sid}', more=true) for part {next}; the reply is "
          "complete only after part {n}.]")
# Room for the footer at its longest (part and session numbers included).
FOOTER_RESERVE = 300
ID_LISTS = frozenset({"mark_seen", "note_viewed", "mark_promoted"})


def split(text: str, limit: int, measure) -> list[str]:
    """Parts within limit, cut at line ends; a line longer than a part is cut
    inside, at the longest prefix that fits."""
    room = limit - FOOTER_RESERVE
    parts, current = [], ""
    for line in text.split("\n"):
        candidate = line if not current else current + "\n" + line
        if measure(candidate) <= room:
            current = candidate
            continue
        if current:
            parts.append(current)
        while measure(line) > room:
            lo, hi = 1, len(line)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                lo, hi = (mid, hi) if measure(line[:mid]) <= room else (lo, mid - 1)
            parts.append(line[:lo])
            line = line[lo:]
        current = line
    parts.append(current)
    return parts


def _shown_in(part: str, ids) -> set:
    """Ids whose own line (a gist line or a node block header) is in part,
    not ids merely cited in an edge."""
    return {nid for nid in ids
            if re.search(rf"^(?:  |▸ ){re.escape(nid)}(?=[: (]|$)", part, re.M)}


def assign(effects: list, parts: list[str]) -> list[list]:
    """Each part's share of the read's effects. An id goes with the first part
    that shows it, or the last part when no line shows it; anything not about
    ids (the full-read flag) goes with the last part."""
    ids = {nid for e in effects if e["method"] in ID_LISTS for nid in e["args"][1]}
    ids |= {e["args"][0] for e in effects if e["method"] == "record_read"}
    home = {}
    for i, part in enumerate(parts):
        for nid in _shown_in(part, ids - home.keys()):
            home[nid] = i
    last = len(parts) - 1
    out = [[] for _ in parts]
    for e in effects:
        if e["method"] in ID_LISTS:
            by_part = {}
            for nid in e["args"][1]:
                by_part.setdefault(home.get(nid, last), []).append(nid)
            for i, nids in by_part.items():
                out[i].append(dict(e, args=[e["args"][0], nids, *e["args"][2:]]))
        elif e["method"] == "record_read":
            out[home.get(e["args"][0], last)].append(e)
        else:
            out[last].append(e)
    return out


def _deliver(store, manager, sid, text, effects, i, n):
    apply_graph_effects(store, apply_effects(manager, effects))
    if i < n:
        text += FOOTER.format(i=i, n=n, sid=sid, next=i + 1)
    return [TextContent(type="text", text=text)]


async def call_paged(store, manager, call_tool, arguments: dict, client: str):
    """kg_read for a client whose profile sets a tool-part limit."""
    profile = harness.profile(client)
    sid = arguments.get("session_id")
    if arguments.get("more"):
        page = manager.take_page(sid) if sid else None
        if page is None:
            return [TextContent(type="text", text=(
                "Nothing more to read: the last kg_read reply was complete "
                "(or a newer read replaced it)."))]
        text, effects, i, n = page
        return _deliver(store, manager, sid, text, effects, i, n)

    view = DeferredView(manager)
    content = await call_tool("kg_read", arguments, client, view=view)
    text = "\n\n".join(block.text for block in content)
    sid = sid or _session_of(text)
    parts = split(text, profile.tool_part_limit, profile.measure)
    if len(parts) == 1 or not sid:
        if sid:
            manager.set_pages(sid, [], [])
        view.commit(store)
        return content
    shares = assign(view.effects, parts)
    manager.set_pages(sid, parts[1:], shares[1:])
    return _deliver(store, manager, sid, parts[0], shares[0], 1, len(parts))


def _session_of(text: str) -> str | None:
    """A first kg_read mints its session; the reply names it on its last line."""
    found = re.findall(r"^Session: (\S+)$", text, re.M)
    return found[-1] if found else None
