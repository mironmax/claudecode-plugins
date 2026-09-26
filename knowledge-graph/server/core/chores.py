"""Maintenance chores — one small, named piece of graph gardening.

A /kg-maintain PASS is a 25-call unit that orients itself, works six
categories under caps, verifies and stamps. It is the right shape for a
scheduled run and the wrong shape for a laptop: measured over the 45 days
after the systemd dispatcher was armed, 28 passes fired across 12 graphs —
one per graph per ~19 days, against a staleness horizon of 14. The gate is a
five-way conjunction whose terms are anti-correlated (the quota gauge is
fresh only while someone is working, which is exactly when the quota gate is
closed; the machine is suspended the rest of the time).

A CHORE is the same work re-cut so it fits the moments that actually exist.
One debt category, one or two targets THIS module names up front, and no
orientation: the pass spends its first third deciding what to do, which is
precisely the part the server can do for free — it already computes every
debt factor. What is left is a handful of tool calls, small enough to run
beside a live session without competing with it.

Three rules shape the selection, and each is a bug that would otherwise be:

  Never RENAME a node the user is looking at. A live session holds the old
  id; renaming it turns their next kg_read into a NOT FOUND. Rewriting its
  gist does not — the rule is "sharpen wording, not meaning", so the session
  is holding an older phrasing of the same claim — and adding an edge or
  rewriting notes is invisible to it. So the seen-set is a hard filter for
  renames and a preference for everything else. Measured the first time this
  ran against the live graphs: a session that has done the loud full kg_read
  has marked EVERY active node seen, so a blanket exclusion refuses every
  chore in the very project being worked on, which is the one place chores
  are supposed to happen.

  Never a node an earlier pass considered and refused. The `declined` lines
  in the maintain trail are decisions, not notes — re-proposing what was
  weighed and rejected is the exact work the trail exists to prevent.

  Never the same category forever. Categories are ranked by the weight the
  debt formula itself gives them, so gists lead; but a graph with no
  oversized gists and thirteen long ids must get id chores, and a run of
  three identical kinds yields to the next non-empty category.

And one guard that outranks all three: never rewrite a node that is being
rewritten too often. Repeated in-place rewriting of the same stored text is
the one maintenance pattern measured to degrade memory — streamed
consolidation falls from a 100% no-memory ceiling to ~54% where a one-pass
consolidation of the same material holds at 100%
(docs/research/cards/2605.12978-consolidation-degradation.md). Every kind
that writes a node's text back — gist, notes, and anchor, which re-sends the
gist with the repaired touches — skips a node whose gist changed more than
CHURN_MAX_REWRITES times inside CHURN_WINDOW_DAYS. The count comes from
_gist_ts, stamped by put_node on every real gist change, not from the version
counter, which also bumps when a read promotes a node out of the archive.
"""

import re
import time
from dataclasses import dataclass, field

from .constants import (
    CHORE_TARGETS,
    CHURN_MAX_REWRITES,
    CHURN_WINDOW_DAYS,
    GIST_TS_FIELD,
    LIFT_EDGE_REL,
    NODE_ID_TARGET_WORDS,
)
from .debt import GIST_OVERSIZE_CHARS
from .lift import lift_clusters, lifted_ids
from .utils import node_id_has_date, node_id_words

# Notes that read as a changelog rather than as current truth. Detection is
# deliberately conservative — a false positive spends a chore rewriting notes
# that were fine, which is wasteful but harmless; the caps keep it cheap.
_CHANGELOG_MARKERS = (
    "actually", "turns out", "turned out", "no longer", "superseded",
    "used to", "previously", "originally", "correction", "was wrong",
    "update:", "edit:", "now fixed", "since fixed", "revised",
)

# Category weights mirror core.debt's deficit term, so a chore always attacks
# the factor the DEBT line is loudest about. notes hygiene carries no debt
# weight (it is invisible to the formula) and therefore sorts last — it is
# what a graph gets once its countable wear is paid down.
_KIND_WEIGHT = {"gist": 1.0, "id": 0.5, "edge": 0.5, "anchor": 0.5,
                "lift": 0.25, "notes": 0.15}

_SAME_KIND_RUN = 3   # consecutive chores of one kind before yielding

# Kinds whose change would break a live session's copy rather than merely age
# it. A rename invalidates the id the session is holding; nothing else does.
_CONTEXT_EXCLUSIVE_KINDS = ("id",)

# Kinds that write a node's text back in place, and so fall under the churn
# guard. An anchor chore is one: kg_put_node takes the gist with the touches,
# and a gist re-sent by a model is a gist that can drift. Renames, edges and
# lifts leave the target's text alone.
_REWRITING_KINDS = ("gist", "notes", "anchor")


def gist_rewrites(node, now: float | None = None,
                  window_days: float = CHURN_WINDOW_DAYS) -> int:
    """How many times the node's gist changed inside the window."""
    now = now or time.time()
    cutoff = now - window_days * 86400
    return sum(1 for ts in node.get(GIST_TS_FIELD) or [] if ts and ts > cutoff)


def is_churning(node, now: float | None = None) -> bool:
    """A node rewritten often enough recently that another rewrite would hurt."""
    return gist_rewrites(node, now) > CHURN_MAX_REWRITES


@dataclass
class Chore:
    """One dispatchable unit of maintenance."""

    kind: str                      # gist | id | edge | notes | anchor | lift
    targets: list[str]
    reason: str                    # the debt factor, in words, for the prompt
    level: str = "user"            # user | project
    graph: str = "user"            # graph key / slug, for logging
    project_path: str | None = None
    debt: float = 0.0
    candidates: int = 0            # how many nodes of this kind remain
    pool: dict = field(default_factory=dict)   # kind -> remaining count
    # What the server found that the chore cannot find itself: for anchor,
    # {"anchors": {id: [record]}}; for lift, {"evidence", "shapes", "touches"}.
    context: dict = field(default_factory=dict)


def _active(nodes):
    return [n for n in nodes if not n.get("_archived") and "_orphaned_ts" not in n]


def _connected_ids(edges) -> set:
    out = set()
    for e in edges:
        out.add(e.get("from", ""))
        out.add(e.get("to", ""))
    return out


def _notes_read_as_changelog(node) -> bool:
    notes = node.get("notes") or []
    if len(notes) < 2:
        return False
    blob = " ".join(str(n) for n in notes).lower()
    return any(m in blob for m in _CHANGELOG_MARKERS)


def candidates_by_kind(nodes, edges, exclude: set | None = None,
                      in_context: set | None = None, anchors: dict | None = None,
                      now: float | None = None) -> dict:
    """Eligible target ids per chore kind, worst-first within each kind.

    exclude: ids no chore may touch at all.
    in_context: ids a live session is holding — barred from the kinds that
    would break its copy, demoted (never barred) for the kinds that would not.
    anchors: {id: [record]} from core.anchors.anchor_candidates — nodes with a
    dangling touch the server found a fix for. Precomputed by the caller,
    because finding one walks the tree and runs git.
    """
    exclude = exclude or set()
    in_context = in_context or set()
    anchors = anchors or {}
    active = [n for n in _active(nodes) if n["id"] not in exclude]
    connected = _connected_ids(edges)
    hot = {n["id"] for n in active if is_churning(n, now)}
    active_ids = {n["id"] for n in active}
    clusters = lift_clusters(nodes, edges, exclude=exclude)
    lift_members = [m for c in clusters for m in c["members"]]

    oversized = sorted(
        (n for n in active if len(n.get("gist", "")) > GIST_OVERSIZE_CHARS),
        key=lambda n: -len(n.get("gist", "")),
    )
    # A dated id waiting in a lift cluster is not a naming problem: the skill
    # says a run of dated siblings wants one enduring node, not a rename each,
    # and renaming it first would take the date that marks it as an episode.
    # Once lifted it is evidence waiting for the scorer to archive it.
    in_lift = set(lift_members) | lifted_ids(edges)
    long_ids = sorted(
        (n for n in active
         if n["id"] not in in_lift
         and (node_id_words(n["id"]) > NODE_ID_TARGET_WORDS or node_id_has_date(n["id"]))),
        # A dated id is the more damaging kind (it names an event where the
        # graph wanted an enduring subject), so it outranks mere length.
        key=lambda n: (-int(node_id_has_date(n["id"])), -node_id_words(n["id"])),
    )
    # An unconnected node with a substantial gist is the one most likely to
    # have an honest edge waiting; a thin one usually needs sharpening, which
    # the chore instruction offers as the fallback.
    unconnected = sorted(
        (n for n in active if n["id"] not in connected),
        key=lambda n: -len(n.get("gist", "")),
    )
    changelog = sorted(
        (n for n in active if _notes_read_as_changelog(n)),
        key=lambda n: -len(n.get("notes") or []),
    )
    pools = {
        "gist": [n["id"] for n in oversized],
        "id": [n["id"] for n in long_ids],
        "edge": [n["id"] for n in unconnected],
        "notes": [n["id"] for n in changelog],
        "anchor": [nid for nid in anchors if nid in active_ids],
        "lift": lift_members,
    }
    for kind in _REWRITING_KINDS:
        pools[kind] = [i for i in pools[kind] if i not in hot]
    for kind, ids in pools.items():
        if kind == "lift":
            # Members are grouped by cluster, and a lift writes nothing to
            # them — it adds a node and edges out of them, which a session
            # holding a member never sees. Reordering would split clusters.
            continue
        fresh = [i for i in ids if i not in in_context]
        # Barred for the kinds that would break a live copy, merely last for
        # the kinds that would only age it.
        pools[kind] = fresh if kind in _CONTEXT_EXCLUSIVE_KINDS else (
            fresh + [i for i in ids if i in in_context])
    return pools


def _recent_kinds(trail, limit: int = _SAME_KIND_RUN) -> list[str]:
    return [e.get("kind") for e in list(trail)[-limit:] if e.get("kind")]


def recent_chore_targets(trail, limit: int = 10) -> set:
    """Targets the last few chores already worked — never re-chew them."""
    out: set = set()
    for entry in list(trail)[-limit:]:
        for t in entry.get("targets") or []:
            out.add(str(t))
    return out


def declined_ids(maintain_trail, candidate_ids) -> set:
    """Candidate ids named in a maintain pass's `declined` lines.

    Free text by design — a decision is written as a sentence — so the match
    is a substring scan of the candidates against it. Cheap, and it only ever
    removes work.
    """
    blob_parts = []
    for entry in list(maintain_trail)[-10:]:
        for line in entry.get("declined") or []:
            blob_parts.append(str(line))
    if not blob_parts:
        return set()
    blob = " ".join(blob_parts)
    return {cid for cid in candidate_ids if cid and cid in blob}


REASONS = {
    "gist": "oversized gist(s) — over {limit} chars, the documented compactor-stall cause",
    "id": "long or dated id(s) — the id names the subject, the gist makes the claim",
    "edge": "unconnected active node(s) — connectedness is 40% of the archival score",
    "notes": "notes that read as a changelog rather than as current truth",
    "anchor": "touches that no longer resolve — the node points at a file that moved or is gone",
    "lift": "instance-level nodes (dated records, sessions, reviews, snapshots) "
            "sharing a lesson nobody has written down once",
}


def pick_chore(nodes, edges, *, in_context: set | None = None,
               exclude: set | None = None, chore_trail=(), maintain_trail=(),
               anchors: dict | None = None, now: float | None = None) -> Chore | None:
    """The next chore for one graph, or None when there is nothing to do.

    in_context: ids a recently-active session is holding — no rename may take
    one, other kinds take them only when nothing fresher is available.
    exclude: ids no chore may touch at all.
    chore_trail / maintain_trail: the kg_progress `_trail` rings for tasks
    "chore" and "maintain" on this graph — what recent work already covered,
    and what an earlier pass considered and refused.
    anchors: precomputed anchor candidates (see candidates_by_kind).
    """
    now = now or time.time()
    blocked = set(exclude or set())
    blocked |= recent_chore_targets(chore_trail)

    pools = candidates_by_kind(nodes, edges, exclude=blocked,
                               in_context=set(in_context or set()),
                               anchors=anchors, now=now)
    # A declined decision blocks the node, not the category: drop the named
    # ids and let the next-worst candidate of the same kind step up.
    all_candidates = {cid for ids in pools.values() for cid in ids}
    refused = declined_ids(maintain_trail, all_candidates)
    if refused:
        pools = {k: [i for i in ids if i not in refused] for k, ids in pools.items()}
        # A refused member can leave its cluster one episode short of a principle.
        pools["lift"] = [m for c in lift_clusters(nodes, edges, exclude=blocked | refused)
                         for m in c["members"]]

    ranked = sorted(
        ((k, ids) for k, ids in pools.items() if ids),
        key=lambda kv: -(_KIND_WEIGHT[kv[0]] * len(kv[1])),
    )
    if not ranked:
        return None

    recent = _recent_kinds(chore_trail)
    if (len(recent) >= _SAME_KIND_RUN and len(set(recent)) == 1
            and len(ranked) > 1 and ranked[0][0] == recent[0]):
        # Same kind three times running while another category has work:
        # rotate. A graph whose gists are being tightened one pair at a time
        # must not leave thirteen long ids untouched for a month.
        ranked = ranked[1:] + ranked[:1]

    kind, ids = ranked[0]
    targets = ids[: CHORE_TARGETS.get(kind, 1)]
    context: dict = {}
    if kind == "anchor":
        context = {"anchors": {t: (anchors or {})[t] for t in targets}}
    elif kind == "lift":
        # One whole cluster, never a slice across two: the members are the
        # evidence, and evidence from unrelated episodes is no evidence.
        cluster = next(c for c in lift_clusters(nodes, edges, exclude=blocked | refused)
                       if c["members"][0] == ids[0])
        targets = cluster["members"][: CHORE_TARGETS.get(kind, 1)]
        by_id = {n["id"]: n for n in nodes}
        context = {
            "evidence": cluster["evidence"],
            "shapes": cluster["shapes"],
            "touches": {t: list(by_id.get(t, {}).get("touches") or []) for t in targets},
        }
    return Chore(
        kind=kind,
        targets=targets,
        reason=REASONS[kind].format(limit=GIST_OVERSIZE_CHARS),
        candidates=len(ids),
        pool={k: len(v) for k, v in pools.items() if v},
        context=context,
    )


# ---------------------------------------------------------------------------
# The dispatch prompt
#
# A chore agent starts cold: no preload (the runner suppresses it), no
# transcript, no idea which graph it is in. Everything it needs rides in the
# prompt, and everything it does NOT need stays out — the economy of a chore
# is that it never orients. The first tool call reads exactly the targets and
# nothing else, which is also why kg_read is given ids up front.
# ---------------------------------------------------------------------------

_KIND_INSTRUCTIONS = {
    "gist": (
        "Rewrite each target's gist as a headline of at most 300 characters — "
        "the subject plus the claim it makes. Move the displaced detail into "
        "notes, merged with what is already there: discard no facts. Leave the "
        "id alone; ids are a different chore with a different tool. Write with "
        "kg_put_node(session_id, level=\"{level}\", id=<same id>, gist=..., notes=[...])."
    ),
    "id": (
        "Rename each target: three to five kebab-case words NAMING THE SUBJECT, "
        "no date — a date says when a thing was written down, never what it is, "
        "and the claim already lives in the gist. Use "
        "kg_rename_node(session_id, old_id=..., new_id=..., level=\"{level}\") and "
        "nothing else: put_node plus delete_node is not a rename, it strands "
        "cross-level edges that the next load silently deletes. If the right "
        "name collides with a node that already exists, that is a merge and not "
        "a rename — skip it and say so in `declined`."
    ),
    "edge": (
        "Give each target ONE honest edge to a node that already exists. Find "
        "the counterpart with kg_search first, then "
        "kg_put_edge(session_id, level=\"{level}\", from=..., to=..., rel=...) with a "
        "kebab-case rel that says what the relationship IS. If no honest edge "
        "exists, do not invent one: sharpen the target's gist instead and "
        "record the refusal in `declined`. An unconnected but crisp node beats "
        "a fake edge."
    ),
    "notes": (
        "Rewrite the target's notes to current truth only: standalone bullets a "
        "future session with no other context could act on. Drop the history — "
        "\"actually\", \"turns out\", superseded corrections — and keep the "
        "conclusions they arrived at. Notes are not a changelog. Write with "
        "kg_put_node(session_id, level=\"{level}\", id=<same id>, notes=[...])."
    ),
    "anchor": (
        "Repair the target's touches using ONLY what the server found (listed "
        "above). For each dangling entry: `moved` — replace it with the "
        "replacement exactly as given; `gone` — remove it; `ambiguous` or "
        "`unknown` — leave it as it is and say so in `declined`. Never write a "
        "path that is not listed: you cannot see the filesystem, and a guessed "
        "anchor looks right to every later reader. Keep every other entry as it "
        "is. Write the whole list back with kg_put_node(session_id, "
        "level=\"{level}\", id=<same id>, gist=<the gist EXACTLY as you read it>, "
        "touches=[...]) and leave notes out of the call so they stay unchanged — "
        "this chore repairs an anchor, not wording."
    ),
    "lift": (
        "The targets are episodes: instance-level records the server grouped "
        "for the reasons above. Read them and ask what holds OUTSIDE them — "
        "the claim that would still be true next month, in a different "
        "session. kg_search for it first: if a node already states it, use "
        "that node as the principle instead of writing a new one. Otherwise "
        "write ONE principle node: kg_put_node(session_id, level=\"{level}\", "
        "id=<3-5 kebab words naming the principle, no date>, gist=<the claim>, "
        "notes=[\"when it matters: ...\", \"what goes wrong without it: ...\"], "
        "touches=<the evidence documents, copied from the members' touches "
        "above — never a new path>). Then edge each member that supports it "
        "to it: kg_put_edge(session_id, level=\"{level}\", from=<member>, "
        "to=<principle>, rel=\"{lift_rel}\"). At least TWO members must support "
        "the principle — one episode is not a principle, so if fewer than two "
        "do, write nothing. Do not edit, rename or delete the members: they are "
        "the evidence, and archiving them is the scorer's job once the "
        "principle carries the lesson. No lesson that holds beyond these "
        "episodes? Write nothing and say why in `declined`."
    ),
}


def _context_lines(chore: Chore) -> list[str]:
    """What the server found for this chore, rendered for a reader with no filesystem."""
    lines: list[str] = []
    if chore.kind == "anchor":
        for nid, recs in (chore.context.get("anchors") or {}).items():
            lines.append(f"Dangling touches of {nid}:")
            for r in recs:
                line = f"  - {r['entry']!r}: {r['verdict']}"
                if r.get("replacement"):
                    line += f" -> {r['replacement']!r}"
                if r.get("evidence"):
                    line += f" ({r['evidence']})"
                lines.append(line)
    elif chore.kind == "lift":
        shapes = chore.context.get("shapes") or {}
        touches = chore.context.get("touches") or {}
        lines.append("Members:")
        for t in chore.targets:
            line = f"  - {t} ({shapes.get(t, 'instance')})"
            if touches.get(t):
                line += f"; touches: {', '.join(touches[t])}"
            lines.append(line)
        lines.append("Why they were grouped:")
        lines += [f"  - {e}" for e in chore.context.get("evidence") or []]
    return lines

LESSON_PROTOCOL = """\
Then, and only if this chore taught you something a FUTURE chore would act on
differently, record it in your own memory:

    kg_put_node(session_id, level="maintain", id=<3-5 kebab words>, gist=<the claim>)

Write to an EXISTING lesson's id to reinforce or correct it rather than minting
a near-duplicate — that is how a lesson earns its keep. A lesson is about the
CRAFT of maintenance ("a rename whose new id drops the term prompts actually
use costs recall"), never about the subject matter of the graph you just
gardened. Learning nothing is the normal outcome and the honest one: most
chores are routine. Say so in one line and stop."""


def render_lessons(lessons, char_budget: int) -> str:
    """The maintenance memory, as prompt lines. Empty string when there are none."""
    if not lessons:
        return ""
    lines = []
    used = 0
    for n in lessons:
        line = f"- {n['id']}: {n.get('gist', '')}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("What earlier chores learned — your own accumulated memory, and the\n"
            "first thing to apply here:\n" + "\n".join(lines))


def build_chore_prompt(chore: Chore, cwd: str, lessons=(), lessons_budget: int = 1400) -> str:
    """The complete stdin prompt for one detached chore run."""
    targets = ", ".join(chore.targets)
    instruction = _KIND_INSTRUCTIONS[chore.kind].format(level=chore.level,
                                                        lift_rel=LIFT_EDGE_REL)
    lessons_block = render_lessons(lessons, lessons_budget)
    parts = [
        "Knowledge-graph maintenance chore — ONE small action, then stop.",
        "",
        f"Graph: {chore.level} ({cwd})",
        f"Debt factor: {chore.reason}",
        f"Targets: {targets}",
    ]
    context = _context_lines(chore)
    if context:
        parts += [""] + context
    if lessons_block:
        parts += ["", lessons_block]
    parts += [
        "",
        "Do exactly this:",
        "",
        f'1. kg_read(cwd="{cwd}", ids=[{", ".join(repr(t) for t in chore.targets)}])',
        "   — it returns your session_id and the targets in full. Do NOT read the",
        "   whole graph: you have been told what to work on, and the orientation",
        "   pass is the cost a chore exists to avoid.",
        "",
        f"2. {instruction}",
        "",
        "3. Stamp what happened:",
        "",
        f'   kg_progress(session_id, task_id="chore", level="{chore.level}",',
        f'       state={{"kind": "{chore.kind}", "targets": [{", ".join(repr(t) for t in chore.targets)}],',
        '              "done": <how many you actually changed>,',
        *(['              "principle": "<the principle id, or null>",']
          if chore.kind == "lift" else []),
        '              "declined": ["<what you did not do, and why>"]})',
        "",
        "   `declined` is the half that compounds: a target you examined and left",
        "   alone has been decided, and the next chore reads this before choosing.",
        "   The server writes the timestamp — you have no clock, do not supply one.",
        "",
        LESSON_PROTOCOL,
        "",
        *(["Rules: never invent facts — state only what the members show. Write",
           "nothing but the one principle node and its edges. Archived nodes stay",
           "archived. You have only the kg_* tools: no Bash, no file edits, no web.",
           "About ten calls is a whole lift — finish and stop rather than finding",
           "more to do."]
          if chore.kind == "lift" else
          ["Rules: never invent facts — sharpen wording, not meaning. Touch nothing",
           "but the targets named above. Archived nodes stay archived. You have only",
           "the kg_* tools: no Bash, no file edits, no web. Six or seven calls is a",
           "whole chore — finish and stop rather than finding more to do."]),
        "",
        "Finish with one line: what changed, and what you declined.",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# The pass prompt
#
# The full runbook, carried inline. The previous dispatcher kept its prompt in
# a script outside the repo and it drifted: written before v0.9.35/36, it
# omitted entity consolidation, id refinement, the trail read and `declined`
# entirely — and the one pass that met id work declined the category as "out
# of scope per dispatch instructions" while the DEBT line it had been sent to
# pay down was counting seven long ids. A prompt versioned beside the skill it
# implements cannot drift from it silently.
# ---------------------------------------------------------------------------

def build_pass_prompt(level: str, cwd: str, debt: dict, lessons=(),
                      lessons_budget: int = 1400) -> str:
    """The complete stdin prompt for a full maintenance pass."""
    factors = debt.get("line") or (
        f"debt {debt.get('score')}, {debt.get('oversized_gists', 0)} oversized gist(s), "
        f"{debt.get('long_ids', 0)} long id(s), "
        f"{debt.get('unconnected_active', 0)} unconnected, "
        f"untended {debt.get('untended_days', '?')}d"
    )
    smeared = ", ".join(f"{s['term']}×{s['df']}→{s['hub']}"
                        for s in (debt.get("smeared") or [])) or "none detected"
    lessons_block = render_lessons(lessons, lessons_budget)
    parts = [
        "Knowledge-graph MAINTENANCE PASS — the full runbook, one graph, bounded.",
        "",
        f"Graph: {level} ({cwd})",
        f"Debt: {factors}",
        f"Smeared terms: {smeared}",
        "",
        "You were dispatched because this graph has not had a full pass in a "
        "long time — not because its debt is high. Chores keep the countable "
        "wear down; they deliberately never do the two structural categories "
        "below, so those are what you are here for.",
    ]
    if lessons_block:
        parts += ["", lessons_block]
    parts += [
        "",
        f'1. kg_read(cwd="{cwd}") — returns your session_id and both graphs with',
        f"   their DEBT lines. Work the {level} graph.",
        "",
        f'2. kg_progress(session_id, task_id="maintain", level="{level}") — the',
        "   prior state and `_trail`, the last ~20 stamps newest last. Read it",
        "   BEFORE working the list: it carries what earlier passes did and what",
        "   they considered and DECLINED. A merge weighed and refused twice does",
        "   not need weighing a third time.",
        "",
        "3. Work the list in this order, each capped so the pass ends:",
        "",
        "   a. ENTITY CONSOLIDATION — exactly ONE smeared term, if any are named",
        "      above. Smearing is many nodes re-describing one entity in prose",
        "      instead of edging to its owner, and it is what makes a project's",
        "      central vocabulary useless to search. Confirm the named hub (or",
        "      anoint a better undated node), move the durable entity facts the",
        "      satellites carry into the hub, rewrite the worst satellites' gists",
        "      to their own delta only, and edge them to the hub. Biggest lever;",
        "      may take half the pass — let the later caps shrink.",
        "   b. OVERSIZED GISTS — up to 8, longest first. Headline ≤300 chars",
        "      (subject + claim); displaced detail merges into notes, no facts",
        "      discarded. Leave ids alone here.",
        "   c. ID REFINEMENT — up to 5, via kg_rename_node ONLY (put_node +",
        "      delete_node is not a rename; it strands cross-level edges that",
        "      the next load silently deletes). Ids name the SUBJECT in 3-5",
        "      words, no dates. Beyond repair, look for REFINEMENT: read a",
        "      cluster together, ask what they are collectively about, and",
        "      rename toward the vocabulary the graph actually uses.",
        "   d. UNCONNECTED ACTIVE NODES — up to 5. Batch-read, then ONE honest",
        "      edge each. No honest edge? Sharpen the gist instead.",
        "   e. DUPLICATE MERGES — up to 3. Merge into the richer node (union of",
        "      notes/touches), re-point the poorer node's edges, delete the empty",
        "      shell. Verify overlap first — presumed duplicates often aren't.",
        "   f. NOTES HYGIENE — up to 3 of the nodes you touched above. Rewrite",
        "      changelog-style notes to current truth only.",
        "",
        "4. Re-read to verify the factors you worked have dropped, then STAMP —",
        "   mandatory, the stamp is what resets staleness; an unstamped pass",
        "   did not happen:",
        "",
        f'   kg_progress(session_id, task_id="maintain", level="{level}",',
        '       state={"entities_consolidated": N, "gists_tightened": N,',
        '              "ids_renamed": N, "edges_added": N, "merges": N,',
        '              "notes_rewritten": N,',
        '              "declined": ["<what you considered and did not do, and why>"]})',
        "",
        "   Do not supply a timestamp — you have no clock and the server writes",
        "   it. `declined` is the half that compounds: without it the next pass",
        "   re-examines what this one already decided.",
        "",
        LESSON_PROTOCOL,
        "",
        "Rules: never invent facts — tighten wording, not meaning. Archived nodes",
        "stay untouched except promotions your edges cause. You have only the",
        "kg_* tools: no Bash, no file edits, no web. About 25 kg_* calls is a",
        "whole pass — stop there, stamp, and report.",
        "",
        "Finish with one compact report: debt before → after, counts per",
        "category, and anything found but deferred.",
    ]
    return "\n".join(parts)
