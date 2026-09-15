"""Constants for knowledge graph operations."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Size budgets — exact rendered characters, fixed by design (no env overrides).
# The estimator measures the exact strings kg_read renders (core.render), so
# these budgets are invariants, not tuning knobs. The arithmetic that makes the
# inline guarantee hold:
#
#   MAX_CHARS_PER_LEVEL × 2 levels + section headers/health/session lines
#     < READ_CHAR_BUDGET (the render-time degradation ladder's hard ceiling)
#     < the MCP client's tool-result persistence threshold (~50K chars in
#       Claude Code — beyond it the result lands in a file, not in context)
#
# Per-level budget for the compactor: when the rendered level (active gists +
# live-string edges + archived anchors) exceeds this, the lowest-scored active
# nodes are archived. 17,500 chars ≈ the old 5,000-token budget, tightened
# slightly so two full levels plus wrapper text stay under READ_CHAR_BUDGET.
MAX_CHARS_PER_LEVEL = 17500
# Hard ceiling for a single kg_read result. Graphs the compactor maintains never
# reach it; the render-time ladder enforces it for everything else (legacy or
# externally-edited graphs) by dropping lowest-scored archived anchors, then
# lowest-value live edges — never active gists.
READ_CHAR_BUDGET = 40000
# kg_search output ceiling — same inline philosophy as READ_CHAR_BUDGET, sized
# for a focused answer: top hits with notes, connections, a page of one-liners.
SEARCH_CHAR_BUDGET = 10000
# Session-start preload ceiling. Hook additionalContext rides a much smaller
# inline window than tool results: measured on Claude Code 2.1.199, hook output
# stays inline up to ~10,100 chars and spills to a persisted file (2KB preview)
# at ~10,150. 10,000 keeps the whole preload — instruction header included —
# safely inline. The bootstrap ladder degrades to fit: archived anchors first,
# then edge citations, then lowest-scored active gists (the loud kg_read
# renders whatever the preload had to drop, without repeating what it showed).
BOOTSTRAP_CHAR_BUDGET = 10000
COMPACTION_TARGET_RATIO = 0.8
# ---------------------------------------------------------------------------
# Ambient memory (v0.9.24): per-event hook endpoints.
#
# Prompt-relevant recall — the UserPromptSubmit hook posts the prompt; when it
# matches the graph well enough, the search result's whole neighbourhood rides
# the hook's additionalContext: unseen gists in full, already-seen nodes as
# bare id anchors (attention re-focus at zero budget), and the connection
# edges between them (cite-once per injection; edges deliberately have NO
# cross-session seen-tracking — a small trace refreshing focus is a feature).
# Two gates keep precision: the score threshold decides whether to speak at
# all, and at least one UNSEEN node must be present — an all-seen match set
# injects nothing (habituation is the failure mode: a channel that repeats
# itself trains the model to ignore it).
# 4, after a detour through 3. Week 3 read the noisy TAIL as the disease and
# cut the cap 5->3; week 4 measured that as the worst setting on the board.
# The tail was a symptom of the re-sort in ambient.py ranking by max_term_idf
# (one rare-word coincidence beat broad topical agreement), so trimming it
# only removed slots the right node could have occupied. With the ranking
# fixed, replaying six live prompts scored cap 4 at 5/5 prompts carrying at
# least one useful node against 2/5 at cap 3 — the measure that matters is
# whether the injection carries something worth having, not how short it is.
PROMPT_RECALL_MAX_HITS = 4
PROMPT_RECALL_CHAR_BUDGET = 2500
# Search terms shorter than this carry too little signal. 3, not 4: live
# replay of the week-2 misses showed the discarded vocabulary was exactly the
# 3-char technical kind — css, woo, seo, smtp arrived as prompts' core terms
# and never reached search. Function words this length ("the", "was", "are")
# are stopworded instead of length-filtered.
PROMPT_RECALL_MIN_TERM_LEN = 3
# Recall answers a human asking something. Harness records ride the same
# UserPromptSubmit event — task notifications, image-paste placeholders
# ("[Image: source: /path.png]"), bare drag-and-dropped paths — and the
# week-1 live audit (2026-07-24) found 20% of injections fired on exactly
# that: file-path fragments and notification boilerplate. After placeholders
# and path tokens are set aside, at least this many chars of real text must
# remain or recall stays silent. Low on purpose: short directive prompts
# ("Yes commit all") are real asks and landed meaningful hits in the audit.
PROMPT_RECALL_MIN_PROMPT_CHARS = 8
# RRF scores are IDF-weighted (see store.search): a term contributes
# idf^IDF_SHARPNESS/(60+rank) where idf = log(N/df)/log(N) — near 1.0 for a
# term unique to one node, near 0 for a ubiquitous one. Calibration: a single
# rare term at a top rank yields ~0.012-0.016, so 0.010 means "one genuinely
# rare term, ranked well". For multi-term prompts, 0.020 requires either two
# meaningful terms corroborating or one rare term dominating — while a stack
# of generic conversational terms sums below it and stays silent, which is
# the point: ubiquitous vocabulary must not trigger injection.
PROMPT_RECALL_SCORE_SINGLE = 0.010
PROMPT_RECALL_SCORE_MULTI = 0.020
# Exponent applied to idf before the RRF merge. 1.0 is classic weighting; the
# week-2 offline replay showed its flaw at 1.0 on mature graphs: one node
# accumulating five ubiquitous terms outvoted the single sharp term that
# named the right node (the turnstile/embeddings miss class). Raising the
# exponent widens the gap between sharp and dull evidence while leaving
# unique terms untouched. Value picked by sweep over both audit weeks' real
# prompts (see devdocs eval notes).
IDF_SHARPNESS = 1.5
# Noise gate (week-2 audit, 2026-07-28: 8 of 10 noise injections rode
# low-signal prompts — "Continue", "How its going?" — where one moderately
# rare word cleared the score threshold and dragged in a lexical stray). A
# hit now justifies speaking only with corroboration (≥2 distinct matched
# terms), near-unique evidence (a term found in almost no other node), or a
# title match (the term names the node's id/gist — "deploying?" hitting a
# deploy node speaks; a notes-only mention of "continue" stays silent).
# The threshold below is the near-unique bar on max_term_idf.
PROMPT_RECALL_MIN_SOLO_IDF = 0.85
# Near-duplicate nudge on node CREATE: sessions measurably never search
# before writing (two audited weeks: 229 writes, 6 searches), so duplicate
# control lives at the interface — put_node probes the new node's id + gist
# against its graph via the same term pipeline search uses, and the tool
# result names the best candidate. A nudge, never a block: the write always
# proceeds; the model decides whether to merge. The measure is a RATIO of
# the best other node's score to the probe's own theoretical maximum (every
# term at rank 0) — raw scores grow with probe length, so an absolute
# threshold flagged 100% of a mature graph in the leave-one-out replay.
# Ratio floor calibrated there: median best-neighbour ratio 0.23, p90 0.43;
# the pairs above 0.6 were ACTUAL near-duplicates the graph already carried
# (ssh-hardening vs hardening-session, same day). 0.50 also reaches down to
# close paraphrases at ~5% base nudge rate on a mature graph — acceptable
# for a one-line nudge that only ever fires on brand-new node ids.
NEAR_DUP_RATIO = 0.50
NEAR_DUP_MIN_SCORE = 0.02  # raw floor so two-term flukes on tiny probes stay quiet

# Node id length. Measured 2026-08-28 across 1737 nodes in 27 graphs: mean id
# length by creation month ran 3.4 -> 4.5 -> 5.1 -> 5.1 -> 6.4 words, with 65%
# of August ids over five words and the worst at eleven
# ("cd-chained-into-git-is-hardcoded-no-allow-rule-beats-it"). The cause was a
# doctrine gap — the only guidance anywhere was "kebab-case", while the GIST
# doctrine ("compressed headline") bled into the id, so ids became sentence
# claims. It is not cosmetic: search field-weights the id x3 and in_title()
# fires on a match in id OR gist, so every extra id word is another token that
# can set title_match and let a weak hit clear the prompt-recall noise gate;
# long ids also inflate df for common technical terms, flattening IDF for
# everyone. Shortening costs retrieval almost nothing precisely because the
# gist keeps the words and still counts as a title match.
#
# The rule is: the ID NAMES THE SUBJECT, THE GIST MAKES THE CLAIM. Three to
# five words. Six is tolerated with a nudge in the tool result; seven or more
# is refused at the write boundary with a steering error, because a nudge is
# known not to be enough here (two audited weeks: 229 writes, 6 searches — the
# near-duplicate nudge exists for the same reason). A date in an id is nudged
# too: it records when something was written down, never what it is, so it
# ages into noise in the one field that must stay recognisable years later.
# Dates are still COUNTED as one word so legacy dated ids are not punished
# twice for a habit the rule is separately unlearning.
NODE_ID_TARGET_WORDS = 5
NODE_ID_MAX_WORDS = 6

# Tool-event capture nudges — the PostToolUse hook reports Read/WebFetch/
# WebSearch targets; the server counts them across sessions and nudges capture
# only on proven re-derivation: an uncovered file read in a 2nd distinct
# session, or the same URL/query fetched twice. Precision over recall — a
# first-time read never nudges, and throttles keep nudges rare enough to be
# heard.
TOOL_EVENT_FILE_MIN_SESSIONS = 2   # distinct Claude sessions reading a file
TOOL_EVENT_WEB_MIN_COUNT = 2       # total fetches of a URL / repeats of a query
NUDGE_COOLDOWN_SECONDS = 600       # min gap between nudges to one session
NUDGE_MAX_PER_SESSION = 3
NUDGE_TARGET_COOLDOWN_SECONDS = 86400  # don't re-nudge the same target within a day
TOOL_EVENTS_MAX_KEYS = 500         # oldest-evicted bound on the counters file
# Refill (reverse compaction): when the active graph sits below the fill ceiling
# (COMPACTION_TARGET_RATIO × max), the highest-scored archived nodes are promoted
# back to active to use the headroom. A single threshold — the ceiling itself —
# governs both trigger and fill level. The old separate low-water trigger (0.6)
# created a dead band: a graph at 0.62-0.79 of budget had real headroom but refill
# never fired, so graphs settled there permanently with most nodes stranded in the
# archive. The no-thrash guarantee never needed the dead band — it comes from the
# ceiling (0.8) sitting below the archive threshold (1.0), plus _maybe_compact
# skipping refill on any tick that just archived.
# Archived nodes budget: max fraction of the per-level char budget that archived
# anchor lines may occupy. When exceeded, lowest-scored archived nodes are
# demoted to orphaned (invisible in kg_read).
ARCHIVED_BUDGET_RATIO = 0.30
# Resurrection: minimum score delta for an archived node to displace a freshly-archived one.
RESURRECTION_MARGIN = 0.05
# Usefulness signal ("likes"): explicit endorsement via kg_useful — the agent marks
# the nodes that helped a session, AND the ones that should have been surfaced and
# were not. The second kind is what keeps the signal two-sided: credit earned only
# by nodes the surface already showed would confirm every archival decision that
# went right and hear about none that went wrong. Reads deliberately do NOT feed
# this: a well-formed gist is self-sufficient, so counting reads would reward weak
# gists.
# Each like decays with a half-life so past usefulness fades unless renewed.
USEFUL_HALF_LIFE_DAYS = 90
# The budget is two numbers, not one. GUIDANCE is what the doctrine asks for: five
# is enough to name what mattered, and a number that must be spent carefully is what
# keeps endorsement from decaying into traffic. But it is advice, not a wall — a
# session that keeps turning up real signal (a run of misses after the user corrects
# it, a long session that genuinely used ten nodes) must be able to report all of it,
# and refusing there destroys exactly the evidence the signal exists to carry. So MAX
# is a flood stop rather than a budget, set high enough that no honest session reaches
# it; past the guidance every response says how far over it is, which keeps the
# pressure to be selective without ever making a real endorsement impossible.
# One vote per node per session either way.
LIKES_GUIDANCE_PER_SESSION = 5
MAX_LIKES_PER_SESSION = 10
# Archival score blend (percentile ranks): recency / connectedness / usefulness.
SCORE_WEIGHT_RECENCY = 0.25
SCORE_WEIGHT_CONNECTEDNESS = 0.40
SCORE_WEIGHT_USEFULNESS = 0.35

# Connectedness weight for an edge to an ARCHIVED neighbour, relative to an edge to an
# active neighbour (which is 1.0). A "live string" you can pull (active endpoint) is worth
# full weight; a string between two archived nodes is worth less — but NOT zero. Counting
# archived-neighbour edges at zero created a ratchet: when a well-connected cluster archived
# together, every member's connectedness collapsed to 0 at once, so the refill pass could
# never pull any of them back ("big nodes flying inactive"). At 0.2 a dense archived hub
# scores above an isolated archived node and floats up the refill order; once it is promoted,
# its edges become fully live and the rest of its cluster becomes eligible on the next tick —
# gradual, self-limiting cluster recovery rather than an all-at-once resurrection.
ARCHIVED_EDGE_WEIGHT = 0.2

# ---------------------------------------------------------------------------
# Recall injection log (v0.9.36)
#
# build_prompt_recall used to compute what it injected and then keep nothing;
# recall metrics could only be reconstructed from Claude Code transcripts,
# which expire in 30 days.
#
# Every decision point now appends one JSON line here, SILENCES INCLUDED: a
# log of injections alone gives no denominator and, worse, no near-misses —
# the prompts that scored just under the bar are exactly the evidence a
# threshold change needs, and nothing has ever seen them. Records carry the
# matched terms so a prompt stays replayable after its transcript is gone.
RECALL_LOG_NAME = "recall.jsonl"
# Rotate to <name>.prev at this size (~40-60k records). One generation is
# enough: the audit window is weeks, not years.
RECALL_LOG_MAX_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# kg_progress trail (v0.9.36)
#
# set_progress assigned the state dict, so each stamp destroyed the previous
# one. The maintenance pass is asked to carry "found-but-deferred" forward as
# the next pass's cursor, and the storage could not hold it across two passes:
# the 20-minute dispatcher reconsidered from scratch every tick, so a merge
# weighed and declined left no trace and got re-litigated on the next one.
# Git records what changed; nothing recorded what was considered and refused.
#
# Each write now appends a size-bounded copy of the stamp to a ring carried
# inside the stored dict under _trail. Top-level keys are untouched, so
# readers that reach for state["last_ts"] (debt) keep working unchanged.
PROGRESS_TRAIL_KEY = "_trail"
PROGRESS_TRAIL_MAX = 20          # entries kept per task
PROGRESS_TRAIL_VALUE_CHARS = 240  # per string value in an entry
PROGRESS_TRAIL_LIST_ITEMS = 8     # per list value in an entry

# ---------------------------------------------------------------------------
# Maintenance chores (v0.9.37)
#
# Maintenance used to exist only as a full 25-call PASS fired by a systemd
# timer. Measured over the 45 days after arming: 28 firings across 12 graphs —
# one pass per graph per ~19 days, against a staleness horizon of 14. The fire
# condition is a five-way conjunction (machine awake AND quota gauge fresh AND
# 5h<60% AND 7d<85% AND inside the last 75 min of the moving 5h window), and
# its terms are anti-correlated: the gauge is fresh only while an interactive
# session renders the statusline, i.e. while the user is working — which is
# exactly when the 5h gate is closed. The laptop is suspended the rest of the
# time. The schedule was aiming at hours that barely exist.
#
# A CHORE is the same work in a unit that fits a live session: ONE debt
# category, one or two targets the server names up front, ~5 kg_* calls, no
# orientation pass. It is dispatched on the signal that actually correlates
# with opportunity — the user typing a prompt — as a detached headless
# process, so it costs the live session no context and needs no cooperation
# from the model running it.
#
# Off unless switched on: spawning agent processes spends the user's quota,
# which is never a default. Enable per machine in ~/.knowledge-graph/chores.json
# ({"enabled": true}) or with KG_CHORES=1.
CHORE_CONFIG_NAME = "chores.json"
CHORE_LOG_NAME = "chores.jsonl"
CHORE_LOG_MAX_BYTES = 4 * 1024 * 1024
CHORE_TASK_ID = "chore"           # kg_progress task the chore stamps
# Global spacing: a chore at most this often across ALL graphs, so a busy day
# of prompts cannot turn into a queue of agents.
CHORE_MIN_INTERVAL_SECONDS = 45 * 60
# Per-graph spacing: one graph should not absorb every chore. Six hours still
# lets a neglected graph get 3-4 chores a day when nothing else needs them.
CHORE_GRAPH_COOLDOWN_SECONDS = 6 * 3600
CHORE_MAX_PER_DAY = 8
CHORE_DEBT_FLOOR = 0.12           # below this a graph is tended enough to skip
# Quota gates. Deliberately tighter than the full pass on the 5h gauge:
# a chore fires WHILE the user is working, so it must stay far from the
# ceiling their own session needs. A stale gauge is not a fresh one — unlike
# the timer's blind night window there is no starvation to compensate for
# here, because prompts only arrive when the machine is awake anyway.
CHORE_GAUGE_MAX_AGE_SECONDS = 90 * 60
CHORE_GAUGE_MAX_5H = 55
CHORE_GAUGE_MAX_7D = 80
CHORE_TIMEOUT_SECONDS = 420       # a chore that takes 7 min is wedged, not slow
CHORE_MODEL = "claude-sonnet-5"
# Targets per chore, by kind. Small on purpose: the point is that a chore
# always finishes, so the graph moves a little on most days instead of a lot
# on the rare day every gate opens at once.
CHORE_TARGETS = {"gist": 2, "id": 2, "edge": 1, "notes": 1}
# Lessons carried into the chore prompt (the maintain graph's own memory).
# Rendered inline rather than read by a tool call: it costs the chore nothing
# and cannot be skipped.
CHORE_LESSONS_MAX = 8
CHORE_LESSONS_CHAR_BUDGET = 1400

# ---------------------------------------------------------------------------
# The pass tier
#
# Chores pay down countable wear, and doing that well creates a trap: the
# scheduled dispatcher selects graphs scoring >= 0.3, and the deficit term
# bottoms out at the debt formula's own constant 0.25. Measured — a groomed,
# fully-active graph caps at 0.25 debt no matter how long it goes untended,
# so a well-chored graph would never be selected for a full pass AGAIN, and
# the two categories chores refuse (entity consolidation, duplicate merges)
# would never happen on it. Debt says the graph is CORRECT; it cannot say
# there is structural work waiting.
#
# So the pass is triggered by TIME SINCE THE LAST PASS, not by debt — the one
# question debt cannot answer. And it is paid for out of the weekly surplus.
PASS_INTERVAL_DAYS = 21           # since the last stamped "maintain" pass
PASS_MAX_PER_DAY = 1
PASS_MIN_ACTIVE_NODES = 8         # too small to have structure worth restructuring
PASS_TIMEOUT_SECONDS = 1500       # a full runbook, not a chore
# A pass runs long, so it keeps further from the 5h ceiling than a chore does.
PASS_GAUGE_MAX_5H = 40
# A pass is funded by weekly SURPLUS, not absolute headroom: usage is compared
# against the linear burn that would end the week at 100%, and the pass runs
# only while the week is under that pace. Self-adjusting — no day-of-week rule.
#
#     pace = seven_day_pct / (100 * fraction_of_week_elapsed)
#
# pace <= PASS_PACE_MAX means "on track to leave quota unused — take some".
PASS_PACE_MAX = 1.0
# Absolute backstop: never fund a pass out of the last of the week, however
# far under pace the arithmetic says we are (at 6.5 days elapsed the linear
# line sits at 93%, which would otherwise wave through a nearly spent week).
PASS_GAUGE_MAX_7D = 70

# Session
SESSION_ID_LENGTH = 8
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Grace periods
GRACE_PERIOD_DAYS = 5
ORPHAN_GRACE_DAYS = 365

# Graph levels
#
# "maintain" is the maintenance agent's OWN memory — craft learned from doing
# maintenance ("this merge has been proposed and refused three times"), kept
# deliberately apart from the knowledge the graphs are for. It is reachable
# only by an explicit level: never preloaded, never rendered by a full
# kg_read, never searched, never surveyed for debt. Isolation is by
# construction — read_graphs/search/survey_debt each name their graphs — so a
# gardening note can never surface as prompt recall.
LEVELS = ("user", "project", "maintain")

# ---------------------------------------------------------------------------
# Graph namespaces
#
# A graph is addressed by a namespace key. Two kinds exist today — the
# singleton "user" namespace and per-project namespaces ("project:<root>") —
# but the key scheme is deliberately open: future kinds (e.g. role graphs for
# team setups: "role:cmo") extend the scheme without touching storage or store
# internals. Construct and inspect keys ONLY through these helpers; never
# hand-build "project:..." strings at call sites.
# ---------------------------------------------------------------------------
USER_NAMESPACE = "user"
MAINTAIN_NAMESPACE = "maintain"


def project_namespace(project_root: str) -> str:
    """Namespace key for a project graph."""
    return f"project:{project_root}"


def is_project_namespace(key: str) -> bool:
    """Is this namespace key a project graph?"""
    return key.startswith("project:")


def namespace_kind(key: str) -> str:
    """The kind prefix of a namespace key: 'user', 'project', (future: 'role', …)."""
    return key.split(":", 1)[0]

# Centralized storage
# All graphs stored under ~/.knowledge-graph/ (git-tracked, outside .claude/)
DEFAULT_STORAGE_ROOT = Path.home() / ".knowledge-graph"

# Legacy paths (for migration detection)
LEGACY_USER_PATH = Path.home() / ".claude/knowledge/user.json"
LEGACY_PROJECT_KNOWLEDGE_PATH = ".claude/knowledge/graph.json"
LEGACY_SESSIONS_PATH = Path.home() / ".claude/knowledge/sessions.json"


def get_storage_root() -> Path:
    """Get centralized storage root. Reads KG_STORAGE_ROOT env var, defaults to ~/.knowledge-graph/."""
    return Path(os.getenv("KG_STORAGE_ROOT", str(DEFAULT_STORAGE_ROOT)))


def safe_project_path(project_root: str) -> Path:
    """Resolve and validate a user-supplied project root.

    Constrains the resolved path to the user's home directory to prevent
    path traversal (e.g. '../../etc/passwd') from escaping expected bounds.
    Raises ValueError if the path escapes home.
    """
    home = Path.home().resolve()
    # Resolve via os.path.realpath — avoids symlink games
    resolved_str = os.path.realpath(project_root)
    # Check containment on strings before constructing a Path from user input
    if not (resolved_str + "/").startswith(str(home) + "/"):
        raise ValueError(f"Project path must be within home directory: {resolved_str}")
    return Path(resolved_str)


def _safe_slug(slug: str) -> str:
    """Validate a slug is a plain single directory name with no traversal."""
    if not slug or "/" in slug or "\\" in slug or slug in (".", "..") or slug.startswith("-"):
        raise ValueError(f"Invalid slug: {slug!r}")
    return slug


def project_slug(project_root: str) -> str:
    """Derive a unique slug from project root path.

    Uses last path component.

    Examples:
        ~/projects/my-app -> my-app
        /srv/work/api-server -> api-server
    """
    # Extract the last component from the string before any Path operations
    # so the slug is derived from validated string manipulation, not a tainted Path
    normalized = os.path.normpath(project_root)
    slug = os.path.basename(normalized)
    return _safe_slug(slug)


def _load_aliases() -> dict:
    """Load slug alias map from ~/.knowledge-graph/aliases.json.

    Maps old_slug -> new_slug for projects that were renamed.
    """
    aliases_path = get_storage_root() / "aliases.json"
    if aliases_path.exists():
        try:
            return json.loads(aliases_path.read_text())
        except Exception:
            pass
    return {}


def _save_aliases(aliases: dict):
    """Save slug alias map atomically."""
    import os
    aliases_path = get_storage_root() / "aliases.json"
    temp_path = aliases_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(aliases, indent=2))
    os.replace(temp_path, aliases_path)


def project_graph_path(project_root: str) -> Path:
    """Get centralized graph path for a project.

    Handles renames: if slug has no graph but an alias or old slug does,
    migrates the old graph to the new slug location.

    Example: ~/.knowledge-graph/projects/my-app/graph.json
    """
    slug = project_slug(project_root)   # slug is validated — no separators, no traversal
    storage = get_storage_root()
    # Path built entirely from trusted base + validated slug, never from raw user input
    graph_path = storage / "projects" / slug / "graph.json"

    if graph_path.exists():
        return graph_path

    # Check aliases: maybe this project was renamed
    aliases = _load_aliases()

    # Reverse lookup: is there an old slug that points to this one?
    for old_slug, new_slug in aliases.items():
        if new_slug == slug:
            try:
                old_path = storage / "projects" / _safe_slug(old_slug) / "graph.json"
            except ValueError:
                continue
            if old_path.exists():
                _migrate_slug(old_path, graph_path, old_slug, slug)
                return graph_path

    # No alias found — scan existing project dirs for a graph whose
    # _meta.project_path matches (handles first-time rename detection)
    projects_dir = storage / "projects"
    if projects_dir.exists():
        for candidate_dir in projects_dir.iterdir():
            if not candidate_dir.is_dir() or candidate_dir.name == slug:
                continue
            candidate_graph = candidate_dir / "graph.json"
            if candidate_graph.exists():
                try:
                    data = json.loads(candidate_graph.read_text())
                    stored_path = data.get("_meta", {}).get("project_path", "")
                    # Check if the stored path's directory name matches this slug
                    if stored_path and Path(stored_path).name == slug:
                        old_slug = candidate_dir.name
                        logger.info(
                            f"Detected project rename: {old_slug} -> {slug} "
                            f"(stored path: {stored_path})"
                        )
                        _migrate_slug(candidate_graph, graph_path, old_slug, slug)
                        return graph_path
                except Exception:
                    continue

    # Last resort for legacy graphs without _meta.project_path:
    # check if sessions.json has any session whose project_path
    # resolves to a slug that matches an existing project dir
    sessions_path = storage / "sessions.json"
    if sessions_path.exists() and projects_dir.exists():
        try:
            sessions = json.loads(sessions_path.read_text())
            for _sid, sinfo in sessions.items():
                sp = sinfo.get("project_path", "")
                if sp and Path(sp).resolve().name == slug:
                    # This session's project path matches our slug
                    # Check if there's a graph under a different slug
                    old_slug_candidate = Path(sp).name
                    if old_slug_candidate != slug:
                        old_path = storage / "projects" / old_slug_candidate / "graph.json"
                        if old_path.exists():
                            logger.info(
                                f"Detected rename via sessions: {old_slug_candidate} -> {slug}"
                            )
                            _migrate_slug(old_path, graph_path, old_slug_candidate, slug)
                            return graph_path
        except Exception:
            pass

    return graph_path


def _migrate_slug(old_path: Path, new_path: Path, old_slug: str, new_slug: str):
    """Copy graph from old slug to new slug and record alias."""
    import shutil
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(old_path), str(new_path))
    logger.info(f"Migrated graph: {old_slug} -> {new_slug}")

    # Record alias so future lookups are fast
    aliases = _load_aliases()
    aliases[old_slug] = new_slug
    _save_aliases(aliases)
    logger.info(f"Recorded slug alias: {old_slug} -> {new_slug}")


def user_graph_path() -> Path:
    """Get centralized user graph path."""
    return get_storage_root() / "user.json"


def maintain_graph_path() -> Path:
    """Get the maintenance-lessons graph path (the chore agent's own memory)."""
    return get_storage_root() / "maintain.json"


def sessions_file_path() -> Path:
    """Get centralized sessions file path."""
    return get_storage_root() / "sessions.json"
