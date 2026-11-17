#!/usr/bin/env python3
"""Build retrieval_eval.json from cached query embeddings + rerank scores (no model load)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from rag.paths import RESULTS_DIR  # noqa: E402
from rag.retrieve import Retriever  # noqa: E402
from rag.store import ChunkStore  # noqa: E402
from scripts.eval_retrieval import (  # noqa: E402
    VARIANTS,
    farmdeck_report,
    fmt_ci,
    group_metrics,
    load_eval_items,
    load_or_embed_queries,
    load_rerank_cache,
    reranker_decision,
    run_variant,
)


def main() -> int:
    items = load_eval_items()
    cache = load_rerank_cache()
    missing = [it["id"] for it in items if it["id"] not in cache]
    store = ChunkStore.load(require_embeddings=True)
    retriever = Retriever(store)
    query_vecs = load_or_embed_queries(items, force=False)
    rows_by = {}
    metrics = {}
    # Always (re)compute all variants; hybrid_rerank uses cache only.
    for variant in VARIANTS:
        if variant == "hybrid_rerank" and missing:
            print(f"rerank cache incomplete: {len(cache)}/{len(items)} missing={len(missing)}", flush=True)
            # Score only cached items for a partial, plus leave uncached as rank=None
        fill = False  # never load the reranker here
        rows = run_variant(retriever, items, query_vecs, variant, cache, fill_rerank=fill)
        if variant == "hybrid_rerank" and missing:
            # For uncached items, run_variant will call rank_hybrid_rerank with precomputed=None
            # and would try to load the reranker. Prevent that by scoring only cached rows here.
            # Re-run just the cached subset and mark others skipped.
            rows = []
            from scripts.eval_retrieval import first_match_flags, is_real, golds_hit_at, TOP_K

            for i, item in enumerate(items):
                pre = cache.get(item["id"])
                if pre is None:
                    rows.append(
                        {
                            "id": item["id"],
                            "category": item.get("category"),
                            "synthetic": bool(item.get("synthetic")),
                            "real": is_real(item),
                            "expected_behavior": item.get("expected_behavior"),
                            "gold_doc_ids": item.get("gold_doc_ids") or [],
                            "rank": None,
                            "hit@5": False,
                            "hit@10": False,
                            "skipped_rerank": True,
                            "state_in_query": False,
                            "crop_in_query": False,
                            "top_state_match": False,
                            "top_crop_match": False,
                            "effectiveness": False,
                            "farmdeck_outranks": False,
                        }
                    )
                    continue
                result = retriever.search(
                    item["question"],
                    variant="hybrid_rerank",
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
        rows_by[variant] = rows
        # For incomplete rerank, metrics only over scored items
        if variant == "hybrid_rerank" and missing:
            scored = [r for r in rows if not r.get("skipped_rerank")]
            metrics[variant] = group_metrics(scored)
            metrics[variant]["partial"] = True
            metrics[variant]["n_cached"] = len(scored)
            metrics[variant]["n_missing"] = len(missing)
        else:
            metrics[variant] = group_metrics(rows)

    complete = not missing
    decision = None
    fd = farmdeck_report({k: v for k, v in rows_by.items() if k != "hybrid_rerank" or not missing})
    if complete:
        decision = reranker_decision(
            metrics["hybrid"],
            metrics["hybrid_rerank"],
            rows_by["hybrid"],
            rows_by["hybrid_rerank"],
        )
        print("Running default variant with FarmDeck demotion...", flush=True)
        demoted_rows = []
        from scripts.eval_retrieval import first_match_flags, is_real, golds_hit_at, TOP_K

        for i, item in enumerate(items):
            result = retriever.search(
                item["question"],
                variant=decision["default_variant"],
                query_vec=query_vecs[i],
                top_k=TOP_K,
                demote_farmdeck_on_effectiveness=True,
                rerank_precomputed=cache.get(item["id"]) if decision["default_variant"] == "hybrid_rerank" else None,
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
        from scripts.eval_retrieval import group_metrics as gm

        demoted_metrics = gm(demoted_rows)
        fd_demoted = farmdeck_report({"default_demoted": demoted_rows})
    else:
        demoted_metrics = None
        fd_demoted = None
        # still report farmdeck on completed variants
        fd = farmdeck_report({k: v for k, v in rows_by.items() if k != "hybrid_rerank"})
        # and on cached rerank subset
        fd["hybrid_rerank_partial"] = farmdeck_report({"hybrid_rerank": [r for r in rows_by["hybrid_rerank"] if not r.get("skipped_rerank")]})["hybrid_rerank"]

    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": complete,
        "n_eval_with_gold": len(items),
        "n_chunks": len(store.chunks),
        "exclude_expired_funding": True,
        "rrf_k": 60,
        "rerank_top": 30,
        "rerank_cache_n": len(cache),
        "variants": metrics,
        "reranker_decision": decision,
        "farmdeck_vs_independent": fd,
        "farmdeck_demoted_on_default": {
            "metrics": demoted_metrics,
            "farmdeck_vs_independent": fd_demoted,
        }
        if complete
        else None,
        "notes": (
            "Recall is 1 when any retrieved chunk's parent_id or id is in gold_doc_ids. "
            "State/season/crop are boosts, not filters. Expired funding programs are excluded. "
            "Bootstrap 95% CIs are over items, 10000 resamples, seed 20261117."
        ),
    }
    out = RESULTS_DIR / ("retrieval_eval.json" if complete else "retrieval_eval_partial.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    slim = {}
    for v, rows in rows_by.items():
        slim[v] = [{k: r.get(k) for k in ("id", "category", "real", "rank", "hit@5", "hit@10", "skipped_rerank")} for r in rows]
    slim_path = RESULTS_DIR / ("retrieval_eval_items.json" if complete else "retrieval_eval_items_partial.json")
    slim_path.write_text(json.dumps(slim) + "\n", encoding="utf-8")

    print("\n===== Retrieval results =====")
    for v in VARIANTS:
        if v not in metrics:
            continue
        m = metrics[v]["overall"]
        tag = " (partial)" if metrics[v].get("partial") else ""
        print(f"{v}{tag}: recall@5 {fmt_ci(m, 'recall@5')}  recall@10 {fmt_ci(m, 'recall@10')}  MRR {fmt_ci(m, 'mrr')}")
    if decision:
        print("Reranker decision:", decision["keep_reranker"], "-", decision["reason"])
    print("Wrote", out)
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
