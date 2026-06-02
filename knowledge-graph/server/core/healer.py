"""Self-healing for malformed node fields.

Background
----------
A node is meant to arrive as structured fields: a short ``gist`` headline, a
``notes`` list, and an optional ``touches`` list. Occasionally a client (the
model driving the MCP tool call) serializes the *entire* node — gist plus notes
plus the surrounding tool-call markup — into the single ``gist`` string argument,
leaving ``notes`` empty. The server used to store that verbatim, so the gist
ballooned to 20-30x its intended size and ``notes`` was lost as a structured
field (it survived only as text embedded in the gist).

Because every full-graph ``kg_read`` renders ``id + gist`` for each active node,
these oversized gists dominate the token budget — they are the main reason a
graph that should cost ~5k tokens can cost several times that.

We cannot stop a model from occasionally emitting a bad ``gist`` argument, so the
server heals defensively instead:

  * on write  (``put_node``)  — sanitize before storing, so corruption never lands
  * on load   (graph open)    — repair anything already on disk, idempotently

Both paths call :func:`heal_node_fields`, so the rule lives in exactly one place.

Corruption shapes seen in real data (all heal the same way)::

    <real gist></gist>\n<notes>[...json...]</notes>\n<invoke ...>...   # tool-call leak
    <real gist></gist>\n<parameter name="notes">[...]                    # parameter leak
    <real gist></invoke>                                                # bare close tag
    <real gist></gist>\n<notes">[...]                                   # malformed quote

The shared signature is: a clean leading gist, then a marker (``</gist>``,
``<notes``, ``<parameter``, ``<invoke``, ``</invoke>``) after which everything is
leaked markup we re-parse or discard.
"""

import json
import re

# Markers whose first appearance ends the real gist. Everything from the earliest
# marker onward is leaked tool-call / field markup, not part of the headline.
# Where to CUT once corruption is confirmed: the earliest of these ends the real
# gist. These are permissive (bare tag starts) because by the time we cut we have
# already confirmed via _CORRUPTION_SIGNATURES that this gist is genuinely leaked.
_GIST_END_MARKERS = ("</gist>", "<notes", "<parameter", "<invoke", "</invoke>")

# DETECTION signature — must be STRUCTURAL, not a bare-substring test. A gist that
# merely *mentions* markup (e.g. a node documenting "the <parameter name=...> tag"
# or this healer itself) must NOT be flagged: heal-on-write/load would silently
# truncate it. Real leaked content always carries one of these precise forms — a
# closing </gist>, or a fully-formed leaked tool-call / parameter open tag — none of
# which appear in ordinary prose that just references the words.
_CORRUPTION_SIGNATURES = (
    re.compile(r'</gist>'),                          # the field-close that leaked verbatim
    re.compile(r'</invoke>'),                        # a closed tool-call block leaked in
    re.compile(r'<invoke\s+name='),                  # an opened tool call (not the bare word)
    re.compile(r'<parameter\s+name="(?:notes|touches|session_id)"'),  # a leaked parameter tag
    re.compile(r'<notes>\s*\['),                     # a leaked <notes> field with its JSON array
)

# A notes/touches payload is a JSON array. We pull it out of the leaked tail by
# locating its opener tag and reading the first balanced [...] that follows.
# The ``(?=[\s>])`` lookahead pins the tag name to a real boundary (a space before
# attributes, or the closing ``>``). Without it, ``[^>]*>`` is ambiguous about where
# the name ends, so re's backtracker retries the unbounded tail at every offset —
# quadratic time on a crafted gist (a ReDoS the heal-on-write/load paths would run).
# The boundary also stops ``<notesfoo>`` from being mistaken for a ``<notes>`` opener.
_NOTES_OPENER = re.compile(r'<(?:parameter\s+name="notes"|notes)(?=[\s>])[^>]*>')
_TOUCHES_OPENER = re.compile(r'<(?:parameter\s+name="touches"|touches)(?=[\s>])[^>]*>')


def gist_is_malformed(gist: str) -> bool:
    """True only if the gist carries genuinely leaked field/tool-call markup.

    Cheap guard both heal paths use; a clean gist makes healing a no-op. Detection
    is STRUCTURAL: it matches precise leaked-tag forms (</gist>, </invoke>,
    <invoke name=, <parameter name="...">, <notes>[), never a bare mention of the
    words "notes"/"parameter"/"invoke" or an unrelated "<" — so a gist that merely
    *documents* this markup is not mistaken for corruption and truncated.
    """
    if not gist:
        return False
    return any(sig.search(gist) for sig in _CORRUPTION_SIGNATURES)


def _first_json_array_after(text: str, opener: re.Pattern) -> list | None:
    """Find ``opener`` in ``text`` and parse the first balanced JSON array after it.

    Returns the parsed list, or ``None`` if no opener / no parseable array. Robust
    to trailing junk (more tool calls, ``</notes>``, stray quotes) because it scans
    for the matching ``]`` by bracket depth, respecting strings and escapes.
    """
    m = opener.search(text)
    if not m:
        return None
    start = text.find("[", m.end())
    if start == -1:
        return None

    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except (json.JSONDecodeError, ValueError):
                    return None
                return None
    return None


def heal_node_fields(gist: str, notes, touches):
    """Return ``(gist, notes, touches)`` with any leaked markup repaired.

    If ``gist`` is clean, the inputs are returned unchanged (fast path). If it
    carries leaked markup, the real headline is cut at the first marker and any
    embedded ``notes`` / ``touches`` arrays are recovered from the tail — but only
    used to fill fields the caller left empty, never to overwrite caller-supplied
    structured data.

    Idempotent: healing already-clean output is a no-op, so it is safe to run on
    every write and on every load.
    """
    if not gist_is_malformed(gist):
        return gist, notes, touches

    # 1. The real gist is everything before the earliest marker.
    cut = min(
        (idx for idx in (gist.find(m) for m in _GIST_END_MARKERS) if idx != -1),
        default=len(gist),
    )
    clean_gist = gist[:cut].strip()

    # 2. Recover embedded notes/touches from the leaked tail — only to backfill
    #    fields the caller didn't already provide as structured data.
    tail = gist[cut:]
    if not notes:
        recovered = _first_json_array_after(tail, _NOTES_OPENER)
        if recovered:
            notes = recovered
    if not touches:
        recovered = _first_json_array_after(tail, _TOUCHES_OPENER)
        if recovered:
            touches = recovered

    return clean_gist, notes, touches
