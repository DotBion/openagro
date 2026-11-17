"""Field-level search over funding program name and state fields.

Used as an extra ranking list (RRF-fused) on funding-like questions, and by
the find_funding tool. State is a boost in general retrieval and a filter only
inside the funding tool. Expired programs are excluded by the retriever.
"""

from __future__ import annotations

import pickle
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from rank_bm25 import BM25Okapi

from rag.features import QueryFeatures, chunk_is_nationwide, chunk_states, state_match
from rag.paths import INDEX_DIR
from rag.store import ChunkStore, tokenize

FUNDING_FIELDS_BM25_PATH = INDEX_DIR / "funding_fields_bm25.pkl"

# Strong funding-intent signals. Bare "cost-share" is omitted so evidence
# questions like "returns without grazing or cost-share" stay on the evidence index.
FUNDING_QUERY_RE = re.compile(
    r"\b("
    r"funding programs?|funding types?|cost-?share programs?|"
    r"conservation (?:funding|programs?)|"
    r"eqip|environmental quality incentives|"
    r"\bcsp\b|conservation stewardship|"
    r"\bacep\b|agricultural conservation easement|"
    r"\bnrcs\b|\bfsa\b|"
    r"who (?:does|can|may) (?:the .+? )?(?:say )?may apply|"
    r"who can apply|official usda page|"
    r"application deadline|signup deadline|national application deadline|"
    r"last verify|last verified|"
    r"program pages?|coverage state|"
    r"grant programs?|loan programs?|"
    r"beginning farmers? or veterans|"
    r"soil health exchange (?:funding|library cover)|"
    r"she funding|"
    r"available in (?:iowa|california|illinois|new york|[a-z]+)"
    r")\b",
    re.I,
)

ONLY_STATE_RE = re.compile(r"\bonly coverage state\b", re.I)

# Common program aliases keyed by parent_id suffix / slug tokens.
PROGRAM_ALIASES: dict[str, list[str]] = {
    "usda-nrcs-eqip": [
        "eqip",
        "environmental quality incentives program",
        "environmental quality incentives",
    ],
    "usda-nrcs-csp": [
        "csp",
        "conservation stewardship program",
        "conservation stewardship",
    ],
    "usda-nrcs-acep": [
        "acep",
        "agricultural conservation easement program",
    ],
    "usda-fsa-occsp": ["occsp", "organic certification cost share"],
    "usda-rd-reap": ["reap", "rural energy for america"],
}


def is_funding_query(text: str) -> bool:
    return bool(text and FUNDING_QUERY_RE.search(text))


def is_only_state_query(text: str) -> bool:
    return bool(text and ONLY_STATE_RE.search(text))


def funding_field_text(chunk: dict) -> str:
    """Concatenation of structured program-name and state fields (plus short criteria)."""
    parts: list[str] = []
    for key in ("title", "funder"):
        if chunk.get(key):
            parts.append(str(chunk[key]))
    pid = chunk.get("parent_id") or chunk.get("id") or ""
    slug = pid.split(":", 1)[-1].replace("-", " ").replace("_", " ").replace("::", " ")
    if "::" in slug:
        slug = slug.split("::", 1)[0]
    parts.append(slug)
    for key in ("states", "coverage", "region"):
        val = chunk.get(key)
        if isinstance(val, (list, tuple)):
            parts.extend(str(x) for x in val if x)
        elif val:
            parts.append(str(val))
    for key in ("eligibility_text", "who_can_apply", "deadline", "amount", "cost_share"):
        if chunk.get(key):
            parts.append(str(chunk[key])[:800])
    practices = chunk.get("practice") or []
    if practices:
        parts.extend(str(x) for x in practices)
    return "\n".join(p for p in parts if p)


def _is_funding_chunk(chunk: dict) -> bool:
    return chunk.get("source") == "she_funding" or chunk.get("trust_tier") == "program_page"


def visible_text(chunk: dict) -> str:
    """Chunk body plus structured fields the generator/verifier may quote.

    as_of, URL, eligibility, and state often live in metadata rather than `text`.
    This does not change embeddings or the on-disk chunk file.
    """
    body = (chunk.get("text") or "").strip()
    lines: list[str] = []
    title = chunk.get("title")
    if title and title not in body[:400]:
        lines.append(str(title) + ".")
    funder = chunk.get("funder")
    if funder and str(funder) not in body:
        lines.append(f"Funder: {funder}.")
    states = chunk.get("states") or []
    if states:
        label = ", ".join(str(s) for s in states) if isinstance(states, (list, tuple)) else str(states)
        if label and label not in body:
            lines.append(f"Coverage / states: {label}.")
    coverage = chunk.get("coverage")
    if coverage and str(coverage) not in body:
        lines.append(f"Coverage: {coverage}.")
    as_of = chunk.get("as_of") or chunk.get("last_verified")
    if as_of and str(as_of) not in body:
        lines.append(f"Last verified / as of {as_of}.")
    url = chunk.get("application_url") or chunk.get("url")
    if url and str(url) not in body:
        lines.append(f"Official or application URL: {url}.")
    deadline = chunk.get("deadline")
    if deadline and str(deadline) not in body:
        lines.append(f"Deadline: {deadline}.")
    elig = chunk.get("eligibility_text") or chunk.get("who_can_apply")
    if elig and str(elig)[:80] not in body:
        lines.append("Program criteria as written on the page: " + str(elig).strip())
    header = "\n".join(lines).strip()
    if not header:
        return body
    if not body:
        return header
    return header + "\n\n" + body


def build_funding_field_bm25(store: ChunkStore) -> tuple[BM25Okapi, list[int]]:
    """BM25 over funding chunks' field text. Returns (index, store-index list)."""
    corpus = []
    indices: list[int] = []
    for i, ch in enumerate(store.chunks):
        if not _is_funding_chunk(ch):
            continue
        corpus.append(tokenize(funding_field_text(ch)))
        indices.append(i)
    if not corpus:
        corpus = [[]]
        indices = []
    return BM25Okapi(corpus), indices


def save_funding_field_bm25(bm25: BM25Okapi, indices: list[int], path: Path = FUNDING_FIELDS_BM25_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump({"bm25": bm25, "indices": indices}, f)


def load_funding_field_bm25(path: Path = FUNDING_FIELDS_BM25_PATH) -> tuple[BM25Okapi, list[int]] | None:
    if not path.exists():
        return None
    with path.open("rb") as f:
        blob = pickle.load(f)
    if not isinstance(blob, dict) or "bm25" not in blob or "indices" not in blob:
        return None
    return blob["bm25"], list(blob["indices"])


def _alias_terms_for(chunk: dict) -> list[str]:
    terms: list[str] = []
    pid = (chunk.get("parent_id") or chunk.get("id") or "").split("::", 1)[0]
    for slug, aliases in PROGRAM_ALIASES.items():
        if pid.endswith(slug) or slug in pid:
            terms.extend(aliases)
    title = (chunk.get("title") or "").strip()
    if title:
        terms.append(title.lower())
    return list(dict.fromkeys(terms))


def name_match_score(query: str, chunk: dict) -> float:
    """Exact / fuzzy score in [0, 1] for program-name overlap with the query."""
    q = (query or "").lower()
    if not q:
        return 0.0
    best = 0.0
    for term in _alias_terms_for(chunk):
        t = term.lower().strip()
        if not t:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", q):
            best = max(best, 1.0 if len(t) >= 4 or t in {"eqip", "csp", "acep"} else 0.7)
            continue
        # Fuzzy: compare the alias to windows of similar length in the query.
        if len(t) >= 8:
            ratio = SequenceMatcher(None, t, q).ratio()
            if ratio >= 0.55:
                # also try a local window
                best = max(best, min(0.95, ratio))
            for i in range(0, max(1, len(q) - len(t) + 3), 4):
                window = q[i : i + len(t) + 4]
                r = SequenceMatcher(None, t, window).ratio()
                if r >= 0.86:
                    best = max(best, r)
    return best


def score_funding_fields(
    query: str,
    store: ChunkStore,
    bm25: BM25Okapi,
    indices: list[int],
    *,
    exclude_expired: bool = True,
    feats: QueryFeatures | None = None,
) -> dict[str, float]:
    """Combined field-BM25 + name-match scores for funding chunks (expired excluded)."""
    feats = feats or QueryFeatures.from_query(query)
    toks = tokenize(query)
    raw = bm25.get_scores(toks) if toks else [0.0] * len(indices)
    expired = store.expired_funding_ids() if exclude_expired else set()
    only_state = is_only_state_query(query)
    scores: dict[str, float] = {}
    # Normalize BM25 to 0-1-ish by dividing by max
    max_b = max((float(s) for s in raw), default=0.0) or 1.0
    for local_i, store_i in enumerate(indices):
        ch = store.chunks[int(store_i)]
        cid = ch["id"]
        if cid in expired:
            continue
        b = float(raw[local_i]) / max_b if max_b else 0.0
        name = name_match_score(query, ch)
        # State is a boost, never a filter, in this general scorer.
        st = 0.0
        if feats.states and state_match(feats.states, ch):
            st = 0.25
            if only_state and not chunk_is_nationwide(ch):
                cstates = [s.lower() for s in chunk_states(ch) if s.lower() != "nationwide"]
                qstates = [s.lower() for s in feats.states]
                if cstates and set(cstates) <= set(qstates) and len(cstates) == 1:
                    st += 0.55
        score = b + 1.6 * name + st
        if score > 0:
            scores[cid] = score
    return scores


def top_funding_field_hits(
    scores: dict[str, float],
    *,
    top_n: int = 100,
) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
