"""Read-only search primitives shared by kg_search, recall and the visual editor.

Callers supply a stable graph snapshot (or hold the store lock). These helpers
never load graphs, promote nodes, mark them seen, or persist anything.
"""

import math
import re

from .constants import IDF_SHARPNESS

RRF_K = 60

_SUBTOKEN_SPLIT_RE = re.compile(r"[./_\-]+")
# Light stemming: one suffix stripped, never below a 4-char stem — enough for
# schedule≈scheduling≈scheduled without mangling short words ("pass" stays).
_STEM_SUFFIXES = ("ing", "ed", "es", "s")
_SEARCH_TERM_CAP = 32
# Field weights: a term in a node's id names the concept, in the gist it
# headlines it, in notes/touches it may be mentioned only in passing. The
# weights bias ranking toward nodes ABOUT the query — the live misrank class
# (week-2 audit: a mail-tooling node topping a database-sync query) came
# entirely from incidental notes matches.
_FIELD_W_ID, _FIELD_W_GIST, _FIELD_W_REST = 3, 2, 1


def _stem(term: str) -> str:
    for suf in _STEM_SUFFIXES:
        if term.endswith(suf) and len(term) - len(suf) >= 4:
            return term[: -len(suf)]
    return term


def _term_stream(query: str) -> tuple[list[str], list[str]]:
    """Ordered subtoken stream + composite tokens kept as exact terms.

    "CLAUDE.md-cleanup session" → stream [claude, md, cleanup, session],
    composites [claude.md-cleanup]. The stream preserves word order so
    bigrams can be built from adjacency; composites keep hyphen/dot-joined
    ids searchable as exact strings.
    """
    stream: list[str] = []
    composites: list[str] = []
    for tok in query.lower().split():
        tok = tok.strip("./-_")
        if not tok:
            continue
        parts = [p for p in _SUBTOKEN_SPLIT_RE.split(tok) if len(p) >= 2]
        if len(parts) > 1:
            composites.append(tok)
        stream.extend(parts or [tok])
    return stream, composites


def search_terms(query: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(unigram stems, bigram stem pairs) — order-preserving dedup, capped.

    Bigrams are adjacent subtoken pairs; their document frequency is the
    co-occurrence count, so "claude"+"md" — each common alone — score high
    as a pair. Pairs of very short stems carry no phrase signal and are
    skipped.
    """
    stream, composites = _term_stream(query)
    stems = [_stem(t) for t in stream]
    unigrams = list(dict.fromkeys(stems + composites))[:_SEARCH_TERM_CAP]
    bigrams = list(dict.fromkeys(
        (a, b) for a, b in zip(stems, stems[1:])
        if a != b and len(a) + len(b) >= 5
    ))[:_SEARCH_TERM_CAP]
    return unigrams, bigrams


def rank_nodes(nodes: dict, unigrams: list[str], bigrams: list[tuple[str, str]]) -> tuple[dict[str, float], dict[str, dict]]:
    """Per-graph RRF: (scores, per-node match meta).

    IDF-style term weighting: a term's contribution scales with its
    rarity in this graph. RRF ranks are relative, so without this a
    ubiquitous term ("user", "works", "project") still produces a
    confident-looking ranking while carrying no signal — the live
    failure mode of prompt recall on conversational prompts. Weight
    = log(N/df)/log(N): a term unique to one node ≈ 1.0, a term in
    half the graph ≈ 0.15 for N=100, a term in every node = 0.

    Meta per matched node — matched_terms (distinct unigrams),
    max_term_idf (rarity of its best evidence), title_match (any
    evidence in id/gist rather than notes) — feeds the recall noise
    gate; kg_search itself never gates on it.
    """
    fields = {
        node_id: (
            node_id.lower(),
            node.get("gist", "").lower(),
            " ".join(node.get("notes", []) + node.get("touches", [])).lower(),
        )
        for node_id, node in nodes.items()
    }

    def weighted_count(node_id: str, stem: str) -> int:
        f_id, f_gist, f_rest = fields[node_id]
        return (_FIELD_W_ID * f_id.count(stem)
                + _FIELD_W_GIST * f_gist.count(stem)
                + _FIELD_W_REST * f_rest.count(stem))

    def in_title(node_id: str, stem: str) -> bool:
        f_id, f_gist, _ = fields[node_id]
        return stem in f_id or stem in f_gist

    n_total = len(fields)
    rrf_scores: dict[str, float] = {}
    meta: dict[str, dict] = {}

    def idf(df: int) -> float:
        if n_total <= 1:
            return 1.0
        base = math.log(n_total / df) / math.log(n_total)
        # Sharpened: rare evidence keeps its weight, dull evidence
        # decays faster — otherwise five ubiquitous terms outvote
        # the one term that names the right node (see constants).
        return base ** IDF_SHARPNESS if base > 0 else base

    def accumulate(matches: list[tuple[str, int, bool]], idf_w: float,
                   counts_unigram: bool) -> None:
        matches.sort(key=lambda x: x[1], reverse=True)
        for rank, (node_id, _cnt, title) in enumerate(matches):
            rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + idf_w / (RRF_K + rank)
            m = meta.setdefault(node_id, {
                "matched_terms": 0, "max_term_idf": 0.0, "title_match": False,
            })
            if counts_unigram:
                m["matched_terms"] += 1
            m["max_term_idf"] = max(m["max_term_idf"], idf_w)
            m["title_match"] = m["title_match"] or title

    for stem in unigrams:
        matches = []
        for node_id in fields:
            cnt = weighted_count(node_id, stem)
            if cnt:
                matches.append((node_id, cnt, in_title(node_id, stem)))
        if not matches:
            continue
        idf_w = idf(len(matches))
        if idf_w <= 0:
            continue  # term in every node — zero signal
        accumulate(matches, idf_w, counts_unigram=True)

    for stem_a, stem_b in bigrams:
        matches = []
        for node_id in fields:
            cnt_a = weighted_count(node_id, stem_a)
            if not cnt_a:
                continue
            cnt_b = weighted_count(node_id, stem_b)
            if not cnt_b:
                continue
            title = in_title(node_id, stem_a) and in_title(node_id, stem_b)
            matches.append((node_id, min(cnt_a, cnt_b), title))
        if not matches:
            continue
        idf_w = idf(len(matches))
        if idf_w <= 0:
            continue
        accumulate(matches, idf_w, counts_unigram=False)

    return rrf_scores, meta


def connection_paths(top_ids: list[str], node_ids: set[str], edges: list[dict]) -> list[dict]:
    """Edges forming pairwise shortest paths (≤4 hops) between top hits.

    Adjacency is undirected over node-node
    edges in the participating graphs; artifact endpoints don't route.
    """
    MAX_HOPS = 4
    adj: dict[str, list] = {}
    for e in edges:
        f, t = e["from"], e["to"]
        if f in node_ids and t in node_ids and f != t:
            adj.setdefault(f, []).append((t, e))
            adj.setdefault(t, []).append((f, e))

    path_edges: dict[int, dict] = {}
    for i, src in enumerate(top_ids):
        for dst in top_ids[i + 1:]:
            if src not in adj or dst not in adj:
                continue
            # BFS with parent tracking
            parents = {src: None}
            frontier = [src]
            depth = 0
            found = False
            while frontier and depth < MAX_HOPS and not found:
                next_frontier = []
                for nid in frontier:
                    for other, edge in adj.get(nid, []):
                        if other in parents:
                            continue
                        parents[other] = (nid, edge)
                        if other == dst:
                            found = True
                            break
                        next_frontier.append(other)
                    if found:
                        break
                frontier = next_frontier
                depth += 1
            if found:
                cur = dst
                while parents[cur] is not None:
                    prev, edge = parents[cur]
                    path_edges[id(edge)] = edge
                    cur = prev
    return list(path_edges.values())
