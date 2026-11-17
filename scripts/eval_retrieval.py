#!/usr/bin/env python3
"""Evaluate BM25, dense, hybrid RRF, and hybrid+rerank on eval_v2 items with gold_doc_ids."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

from rag.embed import embed_texts, unload_embedder  # noqa: E402
from rag.features import QueryFeatures, crop_match, state_match  # noqa: E402
from rag.metrics import summarize  # noqa: E402
from rag.paths import (  # noqa: E402
    QNA_DIR,
    QUERY_EMB_PATH,
    RERANK_CACHE_PATH,
    RESULTS_DIR,
)
from rag.retrieve import (  # noqa: E402
    Retriever,
    farmdeck_outranks_independent,
    golds_hit_at,
)
from rag.store import ChunkStore, read_jsonl  # noqa: E402

VARIANTS = ("bm25", "dense", "hybrid", "hybrid_rerank")
TOP_K = 10
RERANK_TOP = 30


def is_real(item: dict) -> bool:
    return (not item.get("synthetic")) and item.get("category") == "expert_qa"


def load_eval_items() -> list[dict]:
    rows = read_jsonl(QNA_DIR / "eval_v2.jsonl")
    return [r for r in rows if r.get("gold_doc_ids")]


def load_or_embed_queries(items: list[dict], force: bool = False) -> np.ndarray:
    ids = [it["id"] for it in items]
    if QUERY_EMB_PATH.exists() and not force:
        blob = np.load(QUERY_EMB_PATH, allow_pickle=True)
        saved = list(blob["ids"])
        if saved == ids and blob["vectors"].shape[1] == 1024:
            print(f"Loaded cached query embeddings {blob['vectors'].shape} from {QUERY_EMB_PATH}", flush=True)
            return blob["vectors"]
    print(f"Embedding {len(items)} eval queries with BAAI/bge-m3 on CPU...", flush=True)
    vecs = embed_texts([it["question"] for it in items], batch_size=8, show_progress=True)
    np.savez(QUERY_EMB_PATH, ids=np.array(ids, dtype=object), vectors=vecs)
    print(f"Wrote {QUERY_EMB_PATH}", flush=True)
    unload_embedder()
    return vecs


def load_rerank_cache() -> dict[str, dict[str, float]]:
    cache: dict[str, dict[str, float]] = {}
    if not RERANK_CACHE_PATH.exists():
        return cache
    with RERANK_CACHE_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            cache[rec["id"]] = {k: float(v) for k, v in rec.get("scores", {}).items()}
    return cache


def append_rerank_cache(eval_id: str, scores: dict[str, float]) -> None:
    with RERANK_CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": eval_id, "scores": scores}) + "\n")


def first_match_flags(hits, item) -> dict:
    q = item["question"]
    feats = QueryFeatures.from_query(q)
    top = hits[0] if hits else None
    return {
        "state_in_query": bool(feats.states),
        "crop_in_query": bool(feats.crops),
        "top_state_match": bool(top and state_match(feats.states, top.chunk)),
        "top_crop_match": bool(top and crop_match(feats.crops, top.chunk)),
        "effectiveness": feats.effectiveness,
        "farmdeck_outranks": farmdeck_outranks_independent(hits),
    }


def run_variant(retriever: Retriever, items, query_vecs, variant: str, rerank_cache, *, fill_rerank: bool) -> list[dict]:
    rows = []
    for i, item in enumerate(items):
        pre = rerank_cache.get(item["id"]) if variant == "hybrid_rerank" else None
        if variant == "hybrid_rerank" and pre is None and fill_rerank:
            # Compute and cache
            from rag.rerank import score_pairs

            feats_search = retriever.search(
                item["question"],
                variant="hybrid",
                query_vec=query_vecs[i],
                top_k=RERANK_TOP,
            )
            cids = [h.chunk_id for h in feats_search.hits]
            texts = [(retriever.store.by_id[c].get("embed_text") or retriever.store.by_id[c].get("text") or "") for c in cids]
            pts = score_pairs(item["question"], texts)
            pre = {c: float(s) for c, s in zip(cids, pts)}
            append_rerank_cache(item["id"], pre)
            rerank_cache[item["id"]] = pre
        result = retriever.search(
            item["question"],
            variant=variant,  # type: ignore[arg-type]
            query_vec=query_vecs[i],
            top_k=TOP_K,
            rerank_precomputed=pre,
        )
        rank = golds_hit_at(result.hits, item.get("gold_doc_ids") or [])
        flags = first_match_flags(result.hits, item)
        rows.append(
            {
                "id": item["id"],
                "category": item.get("category"),
                "synthetic": bool(item.get("synthetic")),
                "real": is_real(item),
                "expected_behavior": item.get("expected_behavior"),
                "gold_doc_ids": item.get("gold_doc_ids") or [],
                "rank": rank,
                "hit@5": rank is not None and rank <= 5,
                "hit@10": rank is not None and rank <= 10,
                "top_ids": [h.parent_id for h in result.hits],
                "top_chunk_ids": [h.chunk_id for h in result.hits],
                "top_sources": [h.source for h in result.hits],
                **flags,
            }
        )
        if (i + 1) % 25 == 0 or i + 1 == len(items):
            print(f"  {variant}: {i+1}/{len(items)}", flush=True)
    return rows


def slice_summary(rows: list[dict], pred) -> dict:
    sub = [r for r in rows if pred(r)]
    ranks = [r["rank"] for r in sub]
    out = summarize(ranks, ks=(5, 10))
    out["n_with_gold"] = len(sub)
    return out


def group_metrics(rows: list[dict]) -> dict:
    by_cat = {}
    cats = sorted({r["category"] for r in rows})
    for c in cats:
        by_cat[c] = slice_summary(rows, lambda r, c=c: r["category"] == c)
    return {
        "overall": slice_summary(rows, lambda r: True),
        "real": slice_summary(rows, lambda r: r["real"]),
        "synthetic": slice_summary(rows, lambda r: not r["real"]),
        "by_category": by_cat,
    }


def fmt_ci(block: dict, key: str) -> str:
    b = block[key]
    return f"{b['mean']*100:.1f}% (95% CI {b['ci95_lo']*100:.1f}–{b['ci95_hi']*100:.1f}, n={b['n']})"


def reranker_decision(base: dict, rerank: dict, base_rows: list[dict], rerank_rows: list[dict]) -> dict:
    rec5_base = base["overall"]["recall@5"]["mean"]
    rec5_rr = rerank["overall"]["recall@5"]["mean"]
    delta_pp = (rec5_rr - rec5_base) * 100.0  # percentage points
    # Region/crop ordering: among queries that mention a state/crop, did top-1 match improve?
    def rate(rows, flag_q, flag_hit):
        sub = [r for r in rows if r[flag_q]]
        if not sub:
            return {"n": 0, "rate": None}
        return {"n": len(sub), "rate": sum(1 for r in sub if r[flag_hit]) / len(sub)}

    st_base = rate(base_rows, "state_in_query", "top_state_match")
    st_rr = rate(rerank_rows, "state_in_query", "top_state_match")
    cr_base = rate(base_rows, "crop_in_query", "top_crop_match")
    cr_rr = rate(rerank_rows, "crop_in_query", "top_crop_match")
    region_fixed = (
        st_rr["rate"] is not None and st_base["rate"] is not None and st_rr["rate"] > st_base["rate"] + 1e-9
    )
    crop_fixed = (
        cr_rr["rate"] is not None and cr_base["rate"] is not None and cr_rr["rate"] > cr_base["rate"] + 1e-9
    )
    keep = (delta_pp >= 3.0) or region_fixed or crop_fixed
    reason = []
    if delta_pp >= 3.0:
        reason.append(f"recall@5 improved by {delta_pp:.2f} percentage points (>= 3)")
    else:
        reason.append(f"recall@5 change is {delta_pp:.2f} percentage points (need +3 to keep on that rule)")
    if region_fixed:
        reason.append(
            f"top-1 state match improved {st_base['rate']:.3f} → {st_rr['rate']:.3f} (n={st_rr['n']})"
        )
    else:
        reason.append(
            f"top-1 state match {st_base['rate']} → {st_rr['rate']} (n={st_rr['n']}); no improvement"
        )
    if crop_fixed:
        reason.append(
            f"top-1 crop match improved {cr_base['rate']:.3f} → {cr_rr['rate']:.3f} (n={cr_rr['n']})"
        )
    else:
        reason.append(
            f"top-1 crop match {cr_base['rate']} → {cr_rr['rate']} (n={cr_rr['n']}); no improvement"
        )
    return {
        "keep_reranker": keep,
        "delta_recall@5_pp": delta_pp,
        "state_top1": {"hybrid": st_base, "hybrid_rerank": st_rr},
        "crop_top1": {"hybrid": cr_base, "hybrid_rerank": cr_rr},
        "reason": " ".join(reason),
        "default_variant": "hybrid_rerank" if keep else "hybrid",
    }


def farmdeck_report(rows_by_variant: dict[str, list[dict]]) -> dict:
    out = {}
    for var, rows in rows_by_variant.items():
        eff = [r for r in rows if r["effectiveness"]]
        bad = [r for r in eff if r["farmdeck_outranks"]]
        out[var] = {
            "n_effectiveness": len(eff),
            "n_farmdeck_outranks_independent": len(bad),
            "rate": (len(bad) / len(eff)) if eff else None,
            "example_ids": [r["id"] for r in bad[:8]],
        }
    # Also the post-processed hybrid (demote FarmDeck)
    return out


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    items = load_eval_items()
    print(f"eval items with gold_doc_ids: {len(items)}", flush=True)
    store = ChunkStore.load(require_embeddings=True)
    retriever = Retriever(store)
    query_vecs = load_or_embed_queries(items)
    rerank_cache = load_rerank_cache()
    fill_rerank = True
    per_variant_rows = {}
    per_variant_metrics = {}
    for variant in VARIANTS:
        print(f"Running variant {variant}...", flush=True)
        rows = run_variant(retriever, items, query_vecs, variant, rerank_cache, fill_rerank=fill_rerank)
        per_variant_rows[variant] = rows
        per_variant_metrics[variant] = group_metrics(rows)

    decision = reranker_decision(
        per_variant_metrics["hybrid"],
        per_variant_metrics["hybrid_rerank"],
        per_variant_rows["hybrid"],
        per_variant_rows["hybrid_rerank"],
    )
    fd = farmdeck_report(per_variant_rows)

    # After a keep/drop decision, measure FarmDeck-demoted hybrid (always available as a safety post-process)
    print("Running hybrid with FarmDeck demotion on effectiveness questions...", flush=True)
    demoted_rows = []
    for i, item in enumerate(items):
        result = retriever.search(
            item["question"],
            variant=decision["default_variant"],  # type: ignore[arg-type]
            query_vec=query_vecs[i],
            top_k=TOP_K,
            demote_farmdeck_on_effectiveness=True,
            rerank_precomputed=rerank_cache.get(item["id"]) if decision["default_variant"] == "hybrid_rerank" else None,
        )
        rank = golds_hit_at(result.hits, item.get("gold_doc_ids") or [])
        flags = first_match_flags(result.hits, item)
        demoted_rows.append(
            {
                "id": item["id"],
                "category": item.get("category"),
                "synthetic": bool(item.get("synthetic")),
                "real": is_real(item),
                "rank": rank,
                **flags,
            }
        )
    demoted_metrics = group_metrics(demoted_rows)
    fd_demoted = farmdeck_report({"default_demoted": demoted_rows})

    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_eval_with_gold": len(items),
        "n_chunks": len(store.chunks),
        "exclude_expired_funding": True,
        "rrf_k": 60,
        "rerank_top": 30,
        "variants": per_variant_metrics,
        "reranker_decision": decision,
        "farmdeck_vs_independent": fd,
        "farmdeck_demoted_on_default": {
            "metrics": demoted_metrics,
            "farmdeck_vs_independent": fd_demoted,
        },
        "notes": (
            "Recall is 1 when any retrieved chunk's parent_id or id is in gold_doc_ids. "
            "State/season/crop are boosts, not filters. Expired funding programs are excluded. "
            "Bootstrap 95% CIs are over items, 10000 resamples, seed 20261117."
        ),
    }
    out = RESULTS_DIR / "retrieval_eval.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # slim per-item table
    slim = {v: [{k: r[k] for k in ("id", "category", "real", "rank", "hit@5", "hit@10")} for r in rows] for v, rows in per_variant_rows.items()}
    (RESULTS_DIR / "retrieval_eval_items.json").write_text(json.dumps(slim) + "\n", encoding="utf-8")

    print("\n===== Retrieval results =====")
    for v in VARIANTS:
        m = per_variant_metrics[v]["overall"]
        print(f"{v}: recall@5 {fmt_ci(m, 'recall@5')}  recall@10 {fmt_ci(m, 'recall@10')}  MRR {fmt_ci(m, 'mrr')}")
    print("Reranker decision:", decision["keep_reranker"], "-", decision["reason"])
    print("FarmDeck outranks independent (effectiveness questions):")
    for v, rec in fd.items():
        print(f"  {v}: {rec['n_farmdeck_outranks_independent']}/{rec['n_effectiveness']} ({rec['rate']})")
    print("Wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
