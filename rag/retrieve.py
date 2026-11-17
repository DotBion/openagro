"""BM25, dense, hybrid RRF (k=60), and hybrid+rerank retrieval."""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import numpy as np
from rank_bm25 import BM25Okapi

from rag.features import QueryFeatures, boost_factor, is_effectiveness_question
from rag.funding_fields import (
    build_funding_field_bm25,
    is_funding_query,
    load_funding_field_bm25,
    save_funding_field_bm25,
    score_funding_fields,
    top_funding_field_hits,
)
from rag.paths import BM25_PATH
from rag.store import ChunkStore, tokenize

Variant = Literal["bm25", "dense", "hybrid", "hybrid_rerank"]
RRF_K = 60
DEFAULT_CANDIDATES = 100
RERANK_TOP = 30


def build_bm25(store: ChunkStore, *, field: str = "embed_text") -> BM25Okapi:
    corpus = []
    for c in store.chunks:
        text = c.get(field) or c.get("text") or ""
        corpus.append(tokenize(text))
    return BM25Okapi(corpus)


def save_bm25(bm25: BM25Okapi, path=BM25_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(bm25, f)


def load_bm25(path=BM25_PATH) -> BM25Okapi | None:
    if not path.exists():
        return None
    with path.open("rb") as f:
        return pickle.load(f)


def rrf_fuse(rank_lists: list[list[str]], *, k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rank_lists:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


def _apply_boost(scores: dict[str, float], store: ChunkStore, feats: QueryFeatures) -> dict[str, float]:
    return {cid: sc * boost_factor(feats, store.by_id[cid]) for cid, sc in scores.items() if cid in store.by_id}


def _topk(scores: dict[str, float], k: int) -> list[tuple[str, float]]:
    items = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:k]


def _filter_expired(store: ChunkStore, indices: np.ndarray | None, exclude: bool) -> np.ndarray:
    if indices is None:
        return store.eligible_indices(exclude_expired_funding=exclude)
    if not exclude:
        return indices
    expired = store.expired_funding_ids()
    return np.array([i for i in indices if store.ids[int(i)] not in expired], dtype=np.int64)


@dataclass
class Hit:
    chunk_id: str
    score: float
    rank: int
    parent_id: str
    source: str
    chunk: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    variant: str
    hits: list[Hit]
    features: QueryFeatures


class Retriever:
    def __init__(self, store: ChunkStore, bm25: BM25Okapi | None = None):
        self.store = store
        self.bm25 = bm25 or load_bm25() or build_bm25(store)
        if not BM25_PATH.exists():
            save_bm25(self.bm25)
        loaded = load_funding_field_bm25()
        if loaded is None:
            self.funding_bm25, self.funding_indices = build_funding_field_bm25(store)
            save_funding_field_bm25(self.funding_bm25, self.funding_indices)
        else:
            self.funding_bm25, self.funding_indices = loaded
            # Rebuild if the stored index length does not match current funding chunks
            n_funding = sum(1 for c in store.chunks if c.get("source") == "she_funding" or c.get("trust_tier") == "program_page")
            if len(self.funding_indices) != n_funding:
                self.funding_bm25, self.funding_indices = build_funding_field_bm25(store)
                save_funding_field_bm25(self.funding_bm25, self.funding_indices)
        self._reranker = None

    def rank_funding_fields(
        self,
        query: str,
        feats: QueryFeatures | None = None,
        *,
        top_n: int = DEFAULT_CANDIDATES,
        exclude_expired: bool = True,
    ) -> list[tuple[str, float]]:
        scores = score_funding_fields(
            query,
            self.store,
            self.funding_bm25,
            self.funding_indices,
            exclude_expired=exclude_expired,
            feats=feats or QueryFeatures.from_query(query),
        )
        return top_funding_field_hits(scores, top_n=top_n)

    def _promote_strong_funding_hits(
        self,
        query: str,
        feats: QueryFeatures,
        ranked: list[tuple[str, float]],
        *,
        top_n: int,
        exclude_expired: bool,
    ) -> list[tuple[str, float]]:
        """Keep high-confidence program-name / field hits from being buried by RRF or the reranker."""
        if not is_funding_query(query):
            return ranked
        field = self.rank_funding_fields(query, feats, top_n=top_n, exclude_expired=exclude_expired)
        strong = [(cid, sc) for cid, sc in field if sc >= 1.2][:5]
        if not strong and field and field[0][1] >= 0.9:
            strong = field[:1]
        if not strong:
            return ranked
        seen = {cid for cid, _ in strong}
        # Keep relative order of strong hits; then the rest of the existing ranking.
        max_r = max((abs(sc) for _, sc in ranked), default=1.0) or 1.0
        out = [(cid, max_r + sc) for cid, sc in strong]
        for cid, sc in ranked:
            if cid not in seen:
                out.append((cid, sc))
                seen.add(cid)
        return out[:top_n]

    def _fuse_funding_fields(
        self,
        query: str,
        feats: QueryFeatures,
        ranked: list[tuple[str, float]],
        *,
        top_n: int,
        exclude_expired: bool,
        force: bool = False,
    ) -> list[tuple[str, float]]:
        if not force and not is_funding_query(query):
            return ranked
        field = self.rank_funding_fields(query, feats, top_n=top_n, exclude_expired=exclude_expired)
        if not field:
            return ranked
        fused = rrf_fuse(
            [[cid for cid, _ in ranked], [cid for cid, _ in field]],
            k=RRF_K,
        )
        fused = _apply_boost(fused, self.store, feats)
        ranked2 = _topk(fused, top_n)
        return self._promote_strong_funding_hits(query, feats, ranked2, top_n=top_n, exclude_expired=exclude_expired)

    def _eligible(self, exclude_expired: bool = True) -> np.ndarray:
        return self.store.eligible_indices(exclude_expired_funding=exclude_expired)

    def bm25_scores(self, query: str, *, exclude_expired: bool = True) -> dict[str, float]:
        toks = tokenize(query)
        if not toks:
            return {}
        raw = np.asarray(self.bm25.get_scores(toks), dtype=np.float64)
        scores: dict[str, float] = {}
        for i in self._eligible(exclude_expired):
            s = float(raw[int(i)])
            if s > 0:
                scores[self.store.ids[int(i)]] = s
        return scores

    def dense_scores(self, query_vec: np.ndarray, *, exclude_expired: bool = True) -> dict[str, float]:
        if self.store.embeddings is None:
            raise RuntimeError("Embeddings are not loaded")
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        if q.shape[0] != self.store.embeddings.shape[1]:
            raise RuntimeError(f"query dim {q.shape[0]} != index dim {self.store.embeddings.shape[1]}")
        # embeddings are L2-normalized; cosine = dot
        eligible = self._eligible(exclude_expired)
        # mmap-friendly: compute in one GEMV
        dots = np.asarray(self.store.embeddings[eligible], dtype=np.float32) @ q
        return {self.store.ids[int(i)]: float(s) for i, s in zip(eligible, dots)}

    def rank_bm25(self, query: str, feats: QueryFeatures, *, top_n: int = DEFAULT_CANDIDATES, exclude_expired: bool = True, fuse_funding: bool = True) -> list[tuple[str, float]]:
        scores = _apply_boost(self.bm25_scores(query, exclude_expired=exclude_expired), self.store, feats)
        ranked = _topk(scores, top_n)
        if fuse_funding:
            ranked = self._fuse_funding_fields(feats.text, feats, ranked, top_n=top_n, exclude_expired=exclude_expired)
        return ranked

    def rank_dense(self, query_vec: np.ndarray, feats: QueryFeatures, *, top_n: int = DEFAULT_CANDIDATES, exclude_expired: bool = True, fuse_funding: bool = True) -> list[tuple[str, float]]:
        scores = _apply_boost(self.dense_scores(query_vec, exclude_expired=exclude_expired), self.store, feats)
        ranked = _topk(scores, top_n)
        if fuse_funding:
            ranked = self._fuse_funding_fields(feats.text, feats, ranked, top_n=top_n, exclude_expired=exclude_expired)
        return ranked

    def rank_hybrid(
        self,
        query: str,
        query_vec: np.ndarray,
        feats: QueryFeatures,
        *,
        top_n: int = DEFAULT_CANDIDATES,
        rrf_k: int = RRF_K,
        exclude_expired: bool = True,
    ) -> list[tuple[str, float]]:
        bm25 = [cid for cid, _ in self.rank_bm25(query, feats, top_n=top_n, exclude_expired=exclude_expired, fuse_funding=False)]
        dense = [cid for cid, _ in self.rank_dense(query_vec, feats, top_n=top_n, exclude_expired=exclude_expired, fuse_funding=False)]
        lists = [bm25, dense]
        if is_funding_query(query):
            lists.append([cid for cid, _ in self.rank_funding_fields(query, feats, top_n=top_n, exclude_expired=exclude_expired)])
        fused = rrf_fuse(lists, k=rrf_k)
        # Boost again lightly after fusion (state/season still visible; never a filter)
        fused = _apply_boost(fused, self.store, feats)
        ranked = _topk(fused, top_n)
        return self._promote_strong_funding_hits(query, feats, ranked, top_n=top_n, exclude_expired=exclude_expired)

    def rank_hybrid_rerank(
        self,
        query: str,
        query_vec: np.ndarray,
        feats: QueryFeatures,
        *,
        top_n: int = DEFAULT_CANDIDATES,
        rerank_top: int = RERANK_TOP,
        exclude_expired: bool = True,
        precomputed: dict[str, float] | None = None,
    ) -> list[tuple[str, float]]:
        hybrid = self.rank_hybrid(query, query_vec, feats, top_n=max(top_n, rerank_top), exclude_expired=exclude_expired)
        cand = [cid for cid, _ in hybrid[:rerank_top]]
        if precomputed is not None:
            scored = [(cid, float(precomputed[cid])) for cid in cand if cid in precomputed]
            missing = [cid for cid in cand if cid not in precomputed]
            if missing:
                from rag.rerank import score_pairs

                chunks = [self.store.by_id[cid] for cid in missing]
                texts = [(c.get("embed_text") or c.get("text") or "") for c in chunks]
                pts = score_pairs(query, texts)
                scored.extend(zip(missing, [float(x) for x in pts]))
        else:
            from rag.rerank import score_pairs

            chunks = [self.store.by_id[cid] for cid in cand]
            texts = [(c.get("embed_text") or c.get("text") or "") for c in chunks]
            pts = score_pairs(query, texts)
            scored = list(zip(cand, [float(x) for x in pts]))
        scored = sorted(scored, key=lambda kv: (-kv[1], kv[0]))
        # Keep leftover hybrid tail after the reranked window, un-scored by the cross-encoder
        ranked_ids = {cid for cid, _ in scored}
        tail = [(cid, sc) for cid, sc in hybrid if cid not in ranked_ids]
        ranked = scored + tail[: max(0, top_n - len(scored))]
        return self._promote_strong_funding_hits(query, feats, ranked, top_n=top_n, exclude_expired=exclude_expired)

    def search(
        self,
        query: str,
        *,
        variant: Variant = "hybrid",
        query_vec: np.ndarray | None = None,
        top_k: int = 10,
        exclude_expired: bool = True,
        demote_farmdeck_on_effectiveness: bool = False,
        rerank_precomputed: dict[str, float] | None = None,
    ) -> RetrievalResult:
        feats = QueryFeatures.from_query(query)
        if variant in {"dense", "hybrid", "hybrid_rerank"} and query_vec is None:
            from rag.embed import embed_texts

            query_vec = embed_texts([query], show_progress=False)[0]
        if variant == "bm25":
            ranked = self.rank_bm25(query, feats, top_n=max(top_k, DEFAULT_CANDIDATES), exclude_expired=exclude_expired)
        elif variant == "dense":
            ranked = self.rank_dense(query_vec, feats, top_n=max(top_k, DEFAULT_CANDIDATES), exclude_expired=exclude_expired)
        elif variant == "hybrid":
            ranked = self.rank_hybrid(query, query_vec, feats, top_n=max(top_k, DEFAULT_CANDIDATES), exclude_expired=exclude_expired)
        elif variant == "hybrid_rerank":
            ranked = self.rank_hybrid_rerank(
                query, query_vec, feats, top_n=max(top_k, DEFAULT_CANDIDATES), exclude_expired=exclude_expired, precomputed=rerank_precomputed
            )
        else:
            raise ValueError(f"Unknown variant: {variant}")

        if demote_farmdeck_on_effectiveness and feats.effectiveness:
            ranked = demote_farmdeck(ranked, self.store)

        hits = []
        for rank, (cid, score) in enumerate(ranked[:top_k], start=1):
            ch = self.store.by_id[cid]
            hits.append(
                Hit(
                    chunk_id=cid,
                    score=float(score),
                    rank=rank,
                    parent_id=ch.get("parent_id") or cid,
                    source=ch.get("source") or "",
                    chunk=ch,
                )
            )
        return RetrievalResult(query=query, variant=variant, hits=hits, features=feats)


def demote_farmdeck(ranked: list[tuple[str, float]], store: ChunkStore) -> list[tuple[str, float]]:
    """On effectiveness questions, independent sources keep their relative order and sit above FarmDeck."""
    indep, fd, other = [], [], []
    for item in ranked:
        src = store.by_id[item[0]].get("source")
        if src == "farmdeck_product":
            fd.append(item)
        elif src in {"she_qa", "she_journal", "paper_abstract"}:
            indep.append(item)
        else:
            other.append(item)
    return indep + other + fd


def golds_hit_at(hits: list[Hit], gold_doc_ids: Iterable[str]) -> int | None:
    """1-based rank of the first hit whose parent_id or id is in the gold set, else None."""
    gold = set(gold_doc_ids or [])
    if not gold:
        return None
    for h in hits:
        if h.parent_id in gold or h.chunk_id in gold:
            return h.rank
    return None


def farmdeck_outranks_independent(hits: list[Hit]) -> bool:
    first_fd = next((h.rank for h in hits if h.source == "farmdeck_product"), None)
    first_ind = next((h.rank for h in hits if h.source in {"she_qa", "she_journal", "paper_abstract"}), None)
    if first_fd is None:
        return False
    if first_ind is None:
        return True
    return first_fd < first_ind
