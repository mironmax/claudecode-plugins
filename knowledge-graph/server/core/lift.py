"""Lift — instance-level nodes whose shared lesson belongs in one principle.

The graph is the tier meant for durable knowledge, and a large share of real
nodes are episodes in it: a dated session record, a one-off review, a status
snapshot. Principle-level knowledge transfers and lasts; instance-level traces
do not (docs/research/synthesis.md, Conflict 1). Session notes remain the
place for episodes. What the graph wants is the lesson several episodes share,
written once, touching them as evidence — after which the instances can
archive on their own.

This module only finds the candidates: instance-shaped active nodes, grouped
by what they demonstrably share (a touched file, an edge, neighbours and
vocabulary). A group needs LIFT_MIN_MEMBERS, because one episode is not a
principle. Writing the principle is a chore's judgement; archiving the
members is the scorer's.
"""

import re

from .anchors import parse_touch
from .constants import LIFT_EDGE_REL, LIFT_MAX_MEMBERS, LIFT_MIN_MEMBERS, LIFT_MIN_SHARED
from .debt import _SMEAR_STOP
from .utils import node_id_has_date

# Id words that name an occasion rather than a subject.
_INSTANCE_WORDS = {
    "session": "session record", "sessions": "session record",
    "review": "review", "audit": "audit", "retro": "retrospective",
    "retrospective": "retrospective", "postmortem": "postmortem",
    "debrief": "debrief", "handover": "handover", "standup": "standup",
    "recap": "recap", "snapshot": "status snapshot", "status": "status snapshot",
    "incident": "incident record",
}
_SERIES_RE = re.compile(r"^(week|sprint|day|iter|iteration|round|session|run)\d+$")
_TERM_RE = re.compile(r"[a-z][a-z0-9]{4,}")
# A neighbour or term held by more than this many episodes links none of them.
_DF_CAP_FLOOR = 8
_DF_CAP_RATIO = 0.1


def instance_shape(node_id: str) -> str | None:
    """What kind of episode the id names, or None for a subject-shaped id."""
    if not isinstance(node_id, str):
        return None
    if node_id_has_date(node_id):
        return "dated record"
    for word in node_id.lower().split("-"):
        if word in _INSTANCE_WORDS:
            return _INSTANCE_WORDS[word]
        if _SERIES_RE.match(word):
            return "series entry"
    return None


def _terms(node: dict) -> set:
    text = (node.get("id", "").replace("-", " ") + " " + node.get("gist", "")).lower()
    return {t for t in _TERM_RE.findall(text)
            if t not in _SMEAR_STOP and t not in _INSTANCE_WORDS
            and not _SERIES_RE.match(t)}


def _touch_keys(node: dict) -> set:
    out = set()
    for entry in node.get("touches") or []:
        parsed = parse_touch(entry)
        key = parsed[0] if parsed else (entry.strip() if isinstance(entry, str) else "")
        if key:
            out.add(key.rstrip("/"))
    return out


def lifted_ids(edges) -> set:
    """Members already pointed at a principle — lifted once is lifted."""
    return {e.get("from", "") for e in edges if e.get("rel") == LIFT_EDGE_REL}


def lift_clusters(nodes, edges, exclude: set | None = None) -> list[dict]:
    """Candidate clusters, largest first: [{members, evidence, shapes}].

    Two instance nodes link when they touch the same file, share an edge, or
    share at least LIFT_MIN_SHARED neighbours-plus-terms. Clusters are the
    connected components of those links with at least LIFT_MIN_MEMBERS. A
    component larger than LIFT_MAX_MEMBERS is cut to the members reached first
    from its best-linked one along real links, so a chore is never handed more
    episodes than it can read, nor one unlinked to the rest.
    """
    exclude = exclude or set()
    done = lifted_ids(edges)
    instances = []
    shapes = {}
    for n in nodes:
        nid = n.get("id", "")
        if n.get("_archived") or "_orphaned_ts" in n or nid in exclude or nid in done:
            continue
        shape = instance_shape(nid)
        if shape:
            instances.append(n)
            shapes[nid] = shape
    if len(instances) < LIFT_MIN_MEMBERS:
        return []

    ids = sorted(n["id"] for n in instances)
    by_id = {n["id"]: n for n in instances}
    # Links are found through inverted indices, never by comparing every
    # pair: this runs on every read, under the store lock, and a graph with a
    # few hundred episodes would otherwise pay for tens of thousands of set
    # intersections each time.
    adj: dict[str, dict[str, str]] = {i: {} for i in ids}
    strength: dict[str, int] = {i: 0 for i in ids}

    def link(a, b, why):
        if a == b or b in adj[a]:
            return
        adj[a][b] = why
        adj[b][a] = why

    # A shared touched file links every holder. Chained, not all pairs: the
    # component is the same, and a handover doc touched by forty sessions
    # costs forty links rather than eight hundred.
    by_touch: dict[str, list] = {}
    for i in ids:
        for key in _touch_keys(by_id[i]):
            by_touch.setdefault(key, []).append(i)
    for key in sorted(by_touch):
        holders = by_touch[key]
        for a, b in zip(holders, holders[1:]):
            link(a, b, f"{a} and {b} both touch {key}")
        for h in holders:
            strength[h] += len(holders) - 1

    neighbours: dict[str, set] = {}
    for e in edges:
        f, t = e.get("from", ""), e.get("to", "")
        if f in adj and t in adj and f != t:
            a, b = sorted((f, t))
            link(a, b, f"{a} and {b} are edged to each other")
            strength[a] += 1
            strength[b] += 1
        neighbours.setdefault(f, set()).add(t)
        neighbours.setdefault(t, set()).add(f)

    # Shared neighbours and shared terms, counted per pair through the keys
    # they share. A key most episodes hold (a hub, a house word) says nothing
    # about which of them belong together, so it is skipped — the same reason
    # search sharpens IDF.
    cap = max(_DF_CAP_FLOOR, int(_DF_CAP_RATIO * len(ids)))
    holders_of: dict[str, list] = {}
    for i in ids:
        for nb in neighbours.get(i, ()):
            if nb not in adj:
                holders_of.setdefault("n:" + nb, []).append(i)
        for t in _terms(by_id[i]):
            holders_of.setdefault("t:" + t, []).append(i)
    shared: dict[tuple, list] = {}
    for key in sorted(holders_of):
        holders = holders_of[key]
        if len(holders) < 2 or len(holders) > cap:
            continue
        for x, a in enumerate(holders):
            for b in holders[x + 1:]:
                shared.setdefault((a, b), []).append(key[2:])
    for (a, b), keys in sorted(shared.items()):
        if len(keys) >= LIFT_MIN_SHARED:
            link(a, b, f"{a} and {b} share {', '.join(keys[:4])}")
            strength[a] += 1
            strength[b] += 1

    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a in ids:
        for b in adj[a]:
            parent[find(a)] = find(b)
    groups: dict[str, list] = {}
    for i in ids:
        if adj[i]:
            groups.setdefault(find(i), []).append(i)

    clusters = []
    for members in groups.values():
        if len(members) < LIFT_MIN_MEMBERS:
            continue
        # Too many for one chore: grow outward from the best-linked member
        # along actual links, so every member kept is linked to one before it.
        rank = lambda i: (-strength[i], i)  # noqa: E731
        start = min(members, key=rank)
        chosen, frontier = [start], [start]
        while frontier and len(chosen) < LIFT_MAX_MEMBERS:
            cur = frontier.pop(0)
            for nb in sorted(adj[cur], key=rank):
                if nb in chosen or len(chosen) >= LIFT_MAX_MEMBERS:
                    continue
                chosen.append(nb)
                frontier.append(nb)
        members = sorted(chosen)
        inside = set(members)
        evidence = sorted({why for m in members for nb, why in adj[m].items()
                           if nb in inside})
        clusters.append({
            "members": members,
            "evidence": evidence[:6],
            "shapes": {m: shapes[m] for m in members},
        })
    clusters.sort(key=lambda c: (-len(c["members"]), c["members"][0]))
    return clusters
