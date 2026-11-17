"""Field-level funding search, name match, expired exclusion, and state filter vs boost."""

from __future__ import annotations

import pytest

from rag.features import QueryFeatures, boost_factor
from rag.funding_fields import (
    funding_field_text,
    is_funding_query,
    name_match_score,
    score_funding_fields,
    build_funding_field_bm25,
    visible_text,
)
from rag.store import ChunkStore, load_chunks


@pytest.fixture(scope="module")
def store() -> ChunkStore:
    return ChunkStore(load_chunks(), embeddings=None)


def test_is_funding_query_detects_programs_not_bare_cost_share():
    assert is_funding_query("Is the Environmental Quality Incentives Program (EQIP) available in Iowa?")
    assert is_funding_query("Which SHE funding programs list Iowa as their only coverage state?")
    assert is_funding_query("What is the national application deadline for USDA NRCS EQIP?")
    # Evidence questions that merely mention cost-share should not trigger fusion
    assert not is_funding_query(
        "What net return range does FarmDeck quote from Iowa State CARD for rye before corn without grazing or cost-share?"
    )


def test_eqip_name_match_ranks_national_eqip(store: ChunkStore):
    ch = store.by_id["she_funding:usda-nrcs-eqip::c1"]
    q = "Is the Environmental Quality Incentives Program (EQIP) available in Iowa?"
    assert name_match_score(q, ch) >= 0.9
    # A different program should score lower than EQIP on this query
    other = store.by_id.get("she_funding:usda-nrcs-csp::c1") or next(
        c for c in store.chunks if c.get("source") == "she_funding" and "eqip" not in (c.get("parent_id") or "")
    )
    assert name_match_score(q, other) < name_match_score(q, ch)


def test_field_scores_exclude_expired_and_surface_eqip(store: ChunkStore):
    bm25, indices = build_funding_field_bm25(store)
    q = "Where is the official USDA page for EQIP, and when did Soil Health Exchange last verify its EQIP entry?"
    scores = score_funding_fields(q, store, bm25, indices, exclude_expired=True)
    expired = store.expired_funding_ids()
    assert not any(cid in expired for cid in scores)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_parents = [(store.by_id[cid].get("parent_id") or cid) for cid, _ in ranked[:5]]
    assert "she_funding:usda-nrcs-eqip" in top_parents
    # The national EQIP page itself should be first or second
    assert top_parents[0] == "she_funding:usda-nrcs-eqip" or top_parents[1] == "she_funding:usda-nrcs-eqip"


def test_visible_text_includes_as_of_and_url(store: ChunkStore):
    ch = store.by_id["she_funding:usda-nrcs-eqip::c1"]
    vis = visible_text(ch)
    assert ch.get("as_of") in vis or (ch.get("last_verified") or "") in vis
    url = ch.get("application_url") or ch.get("url")
    assert url and url in vis


def test_state_boost_not_a_filter_on_evidence(store: ChunkStore):
    feats = QueryFeatures.from_query("What nitrogen credit does hairy vetch give before corn in Iowa?")
    # A non-Iowa chunk must keep factor 1.0 (boost, never filter)
    ca = None
    for c in store.chunks:
        if c.get("source") == "she_funding":
            continue
        st = c.get("states") or []
        if st and "Iowa" not in st and "IA" not in st:
            ca = c
            break
    if ca is None:
        pytest.skip("no non-Iowa evidence chunk")
    assert boost_factor(feats, ca) >= 1.0
    # Unmatched does not zero out
    assert boost_factor(feats, ca) >= 1.0


def test_find_funding_state_filter_excludes_other_states(store: ChunkStore):
    from rag.retrieve import Retriever
    from chat.tools import find_funding

    retriever = Retriever(store)
    out = find_funding(retriever, "Which SHE funding programs list California as their only coverage state?", variant="bm25")
    assert out["filters"]["state"] == "California"
    assert out["filters"]["expired_excluded"] is True
    for h in out["hits"]:
        ch = store.by_id[h["id"]]
        assert ch.get("expired") is not True
        states = [str(s).lower() for s in (ch.get("states") or [])]
        cov = str(ch.get("coverage") or "").lower()
        # Tool-level filter: California or nationwide
        assert (
            "california" in states
            or "ca" in states
            or "california" in cov
            or "nationwide" in states
            or "nationwide" in cov
            or "available nationwide" in cov
        )


def test_funding_field_text_uses_name_and_state(store: ChunkStore):
    ch = store.by_id["she_funding:ia-iowa-nutrient-reduction-strategy::c1"]
    blob = funding_field_text(ch).lower()
    assert "iowa" in blob
    assert "cover crop" in blob or "idals" in blob or "iowa" in blob


def test_eqip_official_page_in_bm25_top(store: ChunkStore):
    from rag.retrieve import Retriever, golds_hit_at
    from rag.features import QueryFeatures

    retriever = Retriever(store)
    q = "Where is the official USDA page for EQIP, and when did Soil Health Exchange last verify its EQIP entry?"
    feats = QueryFeatures.from_query(q)
    ranked = retriever.rank_bm25(q, feats, top_n=10)
    class H:
        def __init__(self, cid, rank, parent):
            self.chunk_id=cid; self.rank=rank; self.parent_id=parent
    hits=[]
    for i,(cid, _) in enumerate(ranked[:10],1):
        ch=store.by_id[cid]
        hits.append(H(cid, i, ch.get("parent_id") or cid))
    assert golds_hit_at(hits, ["she_funding:usda-nrcs-eqip"]) is not None
    assert golds_hit_at(hits, ["she_funding:usda-nrcs-eqip"]) <= 5
