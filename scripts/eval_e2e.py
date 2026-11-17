#!/usr/bin/env python3
"""End-to-end eval on eval_v2: route, retrieval, verifier, refusal, caveats; optional LLM judge."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from chat.llm import complete, discover  # noqa: E402
from chat.pipeline import answer_question  # noqa: E402
from chat.runtime import make_context, set_default_variant  # noqa: E402
from rag.metrics import bootstrap_ci  # noqa: E402
from rag.paths import QNA_DIR, RESULTS_DIR  # noqa: E402
from rag.retrieve import Retriever, golds_hit_at  # noqa: E402
from rag.store import ChunkStore, read_jsonl  # noqa: E402

GATES = {
    "citation": 0.85,
    "numbers": 0.90,
    "refusal": 0.90,
    "invented_numbers": 0,
}


ITEMS_PATH = RESULTS_DIR / "e2e_items.jsonl"
REPORT_PATH = RESULTS_DIR / "e2e_eval.json"


def expected_route(item: dict) -> str:
    if item.get("expected_behavior") == "refuse":
        return "refuse"
    cat = item.get("category") or ""
    if cat in {"should_refuse", "safety_refusal", "out_of_scope", "farmx"}:
        return "refuse"
    if cat == "funding":
        return "funding"
    return "evidence"


def check_named(report: dict | None, names: list[str]) -> bool | None:
    if not report:
        return None
    checks = {c["name"]: c for c in report.get("checks") or []}
    if report.get("skipped"):
        return None
    relevant = [checks[n] for n in names if n in checks]
    if not relevant:
        return None
    return all(c.get("pass") for c in relevant)


def invented_number_count(report: dict | None) -> int:
    if not report or report.get("skipped"):
        return 0
    n = 0
    for c in report.get("checks") or []:
        if c.get("name") == "uncited_numbers":
            n += len(c.get("uncited") or [])
        if c.get("name") == "numbers_supported_by_cited_chunks":
            n += len(c.get("unsupported") or [])
    return n


def has_caveat(answer: str) -> bool:
    from chat.quotes import answer_has_caveat

    return answer_has_caveat(answer)


def load_done() -> dict[str, dict]:
    done = {}
    if ITEMS_PATH.exists():
        for line in ITEMS_PATH.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("id"):
                done[rec["id"]] = rec
    return done


def append_item(rec: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with ITEMS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def judge_item(item: dict, result: dict) -> dict | None:
    inv = discover()
    if not inv.judge:
        return None
    prompt = {
        "question": item["question"],
        "expected_behavior": item.get("expected_behavior"),
        "category": item.get("category"),
        "gold_doc_ids": item.get("gold_doc_ids") or [],
        "gold_answer": (item.get("gold_answer") or "")[:1500],
        "model_answer": result.get("answer") or "",
        "route": (result.get("route") or {}).get("primary"),
    }
    sys_msg = (
        "You are an uncalibrated reviewer of an agronomy assistant. "
        "Score 0 or 1 for: grounded (no invented facts vs the gold answer / likely sources), "
        "refusal_ok (refused iff expected_behavior is refuse, and the refusal is appropriate), "
        "caveat_ok (if expected_behavior is answer_with_caveat, the answer caveats crop/region/year mismatch; else 1 if N/A). "
        "Return ONLY JSON: {\"grounded\":0or1,\"refusal_ok\":0or1,\"caveat_ok\":0or1,\"comment\":\"...\"}. "
        "You are uncalibrated until 30 human labels exist; do not claim otherwise."
    )
    raw = complete(
        "judge",
        [{"role": "system", "content": sys_msg}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        temperature=0.0,
        max_tokens=250,
        timeout=45,
        max_attempts=2,
    )
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    data["uncalibrated"] = True
    data["judge"] = inv.judge.as_dict() if hasattr(inv.judge, "as_dict") else {
        "provider": inv.judge.provider,
        "model": inv.judge.model,
        "family": inv.judge.family,
    }
    return data


def rate(values: list[bool | int | float]) -> dict:
    vals = [float(v) for v in values]
    return bootstrap_ci(vals)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-llm", action="store_true", help="Force extractive stub + rules router (no API calls)")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--variant", default=None, help="Retrieval variant (default: decision from retrieval_eval.json or hybrid)")
    p.add_argument("--resume", action="store_true", default=True)
    p.add_argument("--fresh", action="store_true", help="Ignore previous e2e_items.jsonl")
    p.add_argument("--tag", default="", help="Suffix for output files, e.g. extractive → e2e_extractive_items.jsonl")
    args = p.parse_args()

    variant = args.variant
    tag = (args.tag or "").strip().lstrip("_-")
    items_path = RESULTS_DIR / (f"e2e_{tag}_items.jsonl" if tag else "e2e_items.jsonl")
    report_path = RESULTS_DIR / (f"e2e_{tag}_eval.json" if tag else "e2e_eval.json")
    # Rebind module-level paths used by load_done/append_item
    global ITEMS_PATH, REPORT_PATH
    ITEMS_PATH = items_path
    REPORT_PATH = report_path
    decision_path = RESULTS_DIR / "retrieval_eval.json"
    if variant is None and decision_path.exists():
        variant = (json.loads(decision_path.read_text())["reranker_decision"]).get("default_variant") or "hybrid"
    variant = variant or "hybrid"
    set_default_variant(variant)

    items = read_jsonl(QNA_DIR / "eval_v2.jsonl")
    if args.limit:
        items = items[: args.limit]
    if args.fresh and ITEMS_PATH.exists():
        ITEMS_PATH.unlink()
    done = {} if args.fresh else load_done()

    use_llm = not args.no_llm
    if use_llm:
        inv = discover(force=False)
        if inv.stub_only:
            print("No working LLM generator; running extractive stub.", flush=True)
            use_llm = False
    else:
        print("Running extractive stub + rules router (--no-llm).", flush=True)
    ctx = make_context(use_llm=use_llm, variant=variant, demote_farmdeck=True)
    # Reuse hybrid+rerank scores so e2e does not reload the cross-encoder.
    try:
        try:
            from eval_retrieval import load_rerank_cache
        except ImportError:
            from scripts.eval_retrieval import load_rerank_cache

        cache = load_rerank_cache()
        id_to_q = {}
        for it in items:
            if it.get("id") in cache:
                id_to_q[it["question"]] = cache[it["id"]]
        ctx.rerank_cache_by_query = id_to_q
        print(f"Attached rerank cache for {len(id_to_q)} questions", flush=True)
    except Exception as e:
        print(f"No rerank cache attached ({e})", flush=True)
    # Pre-embed nothing here; the retriever will embed each query. For speed, batch-embed first if LLM path would be slow...
    # Embed all questions once.
    from rag.embed import embed_texts

    need = [it for it in items if it["id"] not in done]
    print(f"E2E: {len(items)} items, {len(done)} already done, {len(need)} to run, variant={variant}, use_llm={use_llm}", flush=True)
    qvecs = {}
    if need:
        print("Embedding queries for remaining items...", flush=True)
        vecs = embed_texts([it["question"] for it in need], batch_size=8, show_progress=True)
        for it, v in zip(need, vecs):
            qvecs[it["id"]] = v

    judge_disabled = False
    judge_fail_streak = 0
    for i, it in enumerate(items, 1):
        if it["id"] in done:
            continue
        rec = answer_question(ctx, it["question"], query_vec=qvecs.get(it["id"]), log=True, eval_id=it["id"])
        exp_route = expected_route(it)
        got_route = (rec.get("route") or {}).get("primary")
        report = rec.get("verifier") or {}
        citation_ok = check_named(report, ["cited_chunks_retrieved", "factual_sentences_cited"])
        numbers_ok = check_named(report, ["uncited_numbers", "numbers_supported_by_cited_chunks"])
        invented = invented_number_count(report)
        refused = rec.get("status") == "refused" or got_route == "refuse"
        expected_refuse = it.get("expected_behavior") == "refuse"
        refusal_ok = refused == expected_refuse
        caveat_ok = None
        if it.get("expected_behavior") == "answer_with_caveat" or it.get("category") == "partial_match":
            caveat_ok = has_caveat(rec.get("answer") or "")
        # retrieval@5 on this run's retrieved ids (tool chunks, in order)
        hits = []
        # Rebuild hit-like ranks from retrieved_ids
        from rag.retrieve import Hit

        store = ctx.retriever.store
        hit_objs = []
        for rank, cid in enumerate(rec.get("retrieved_ids") or [], 1):
            ch = store.get(cid)
            if not ch:
                continue
            hit_objs.append(
                Hit(chunk_id=cid, score=0, rank=rank, parent_id=ch.get("parent_id") or cid, source=ch.get("source") or "", chunk=ch)
            )
        rnk = golds_hit_at(hit_objs, it.get("gold_doc_ids") or []) if it.get("gold_doc_ids") else None
        row = {
            "id": it["id"],
            "category": it.get("category"),
            "synthetic": bool(it.get("synthetic")),
            "expected_behavior": it.get("expected_behavior"),
            "expected_route": exp_route,
            "got_route": got_route,
            "route_ok": got_route == exp_route,
            "status": rec.get("status"),
            "generator": rec.get("generator"),
            "citation_ok": citation_ok,
            "numbers_ok": numbers_ok,
            "invented_numbers": invented,
            "refusal_ok": refusal_ok,
            "refused": refused,
            "caveat_ok": caveat_ok,
            "verifier_pass": (report.get("pass") if not report.get("skipped") else None),
            "recall@5": (rnk is not None and rnk <= 5) if it.get("gold_doc_ids") else None,
            "recall@10": (rnk is not None and rnk <= 10) if it.get("gold_doc_ids") else None,
            "answer": rec.get("answer"),
            "retrieved_ids": rec.get("retrieved_ids"),
            "judge": None,
        }
        if use_llm and not args.no_judge and inv.judge and not judge_disabled:
            try:
                row["judge"] = judge_item(it, rec)
                judge_fail_streak = 0
            except Exception as e:
                err = str(e)
                row["judge"] = {"error": err[:200], "uncalibrated": True}
                if "429" in err or "rate limit" in err.lower():
                    judge_fail_streak += 1
                    if judge_fail_streak >= 3:
                        judge_disabled = True
                        print("  skipping remaining LLM judges after repeated 429s; judge stays uncalibrated", flush=True)
                print(f"  judge failed for {it['id']}: {err[:160]}", flush=True)
        elif use_llm and not args.no_judge and inv.judge and judge_disabled:
            row["judge"] = {"skipped": "repeated_429", "uncalibrated": True}
        append_item(row)
        done[it["id"]] = row
        print(f"  e2e {len(done)}/{len(items)} last={it['id']} route={got_route} status={rec.get('status')} gen={rec.get('generator')}", flush=True)
        try:
            from collections import Counter
            snap = {
                "partial": True,
                "n": len(done),
                "status_counts": dict(Counter(r.get("status") for r in done.values())),
                "generators": dict(Counter(r.get("generator") for r in done.values())),
                "last_id": it["id"],
            }
            (RESULTS_DIR / (ITEMS_PATH.name.replace("_items.jsonl", "_eval_partial.json"))).write_text(
                __import__("json").dumps(snap, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:
            pass

    rows = [done[it["id"]] for it in items if it["id"] in done]
    cit_vals = [r["citation_ok"] for r in rows if r["citation_ok"] is not None]
    num_vals = [r["numbers_ok"] for r in rows if r["numbers_ok"] is not None]
    ref_vals = [r["refusal_ok"] for r in rows]
    cav = [r["caveat_ok"] for r in rows if r["caveat_ok"] is not None]
    ver = [r["verifier_pass"] for r in rows if r["verifier_pass"] is not None]
    rec5 = [r["recall@5"] for r in rows if r["recall@5"] is not None]
    rec10 = [r["recall@10"] for r in rows if r["recall@10"] is not None]
    invented_total = sum(int(r.get("invented_numbers") or 0) for r in rows)
    route_vals = [r["route_ok"] for r in rows]

    citation_rate = rate(cit_vals) if cit_vals else {"n": 0, "mean": None}
    numbers_rate = rate(num_vals) if num_vals else {"n": 0, "mean": None}
    refusal_rate = rate(ref_vals)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n": len(rows),
        "variant": variant,
        "use_llm": use_llm,
        "llm": discover().as_dict(),
        "gates": GATES,
        "metrics": {
            "route_accuracy": rate(route_vals),
            "recall@5": rate(rec5) if rec5 else {"n": 0},
            "recall@10": rate(rec10) if rec10 else {"n": 0},
            "citation_pass": citation_rate,
            "numbers_pass": numbers_rate,
            "verifier_pass": rate(ver) if ver else {"n": 0},
            "refusal_accuracy": refusal_rate,
            "answer_with_caveat": rate(cav) if cav else {"n": 0, "mean": None},
            "invented_numbers_total": invented_total,
        },
        "gates_met": {
            "citation": (citation_rate.get("mean") or 0) >= GATES["citation"] if cit_vals else False,
            "numbers": (numbers_rate.get("mean") or 0) >= GATES["numbers"] if num_vals else False,
            "refusal": (refusal_rate.get("mean") or 0) >= GATES["refusal"],
            "zero_invented_numbers": invented_total == 0,
        },
        "by_category": {},
        "judge": {"used": bool(use_llm and not args.no_judge and discover().judge), "uncalibrated": True, "n": 0},
    }
    from collections import Counter, defaultdict

    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    for cat, sub in by.items():
        report["by_category"][cat] = {
            "n": len(sub),
            "route_accuracy": sum(1 for r in sub if r["route_ok"]) / len(sub),
            "refusal_accuracy": sum(1 for r in sub if r["refusal_ok"]) / len(sub),
            "verifier_pass": (
                sum(1 for r in sub if r["verifier_pass"]) / max(1, sum(1 for r in sub if r["verifier_pass"] is not None))
            ),
        }
    from collections import Counter

    report["status_counts"] = dict(Counter(r.get("status") for r in rows))
    # 9 expected caveat items + all 10 partial-match items (union is 10 unique ids)
    cav9 = [r for r in rows if r.get("expected_behavior") == "answer_with_caveat"]
    part10 = [r for r in rows if r.get("category") == "partial_match"]
    def _cav_rate(sub):
        if not sub:
            return {"n": 0, "mean": None, "n_with_caveat_wording": 0}
        hits = [bool(r.get("caveat_ok") or has_caveat(r.get("answer") or "")) and r.get("status") not in {"hedged", "refused"} for r in sub]
        # For the expected-refuse partial item, success is a refusal, not a caveat.
        adjusted = []
        for r in sub:
            if r.get("expected_behavior") == "refuse":
                adjusted.append(bool(r.get("refusal_ok")))
            else:
                wording = bool(r.get("caveat_ok") if r.get("caveat_ok") is not None else has_caveat(r.get("answer") or ""))
                issued = r.get("status") not in {"hedged", "refused"}
                adjusted.append(bool(wording and issued))
        return {
            "n": len(sub),
            "mean": sum(adjusted) / len(sub),
            "n_ok": sum(adjusted),
            "ids_ok": [r["id"] for r, ok in zip(sub, adjusted) if ok],
            "ids_miss": [r["id"] for r, ok in zip(sub, adjusted) if not ok],
            "status_counts": dict(Counter(r.get("status") for r in sub)),
        }
    report["caveat_behavior"] = {
        "expected_answer_with_caveat_n9": _cav_rate(cav9),
        "partial_match_n10": _cav_rate(part10),
        "union": _cav_rate({r["id"]: r for r in cav9 + part10}.values() if False else list({r["id"]: r for r in (cav9 + part10)}.values())),
        "note": (
            "Rate is the share of items that issued an answer containing explicit caveat wording "
            "(or, for the one expected-refuse partial-match item, a correct refusal). "
            "Hedged non-answers do not count as caveat behavior."
        ),
    }

    judged = [r["judge"] for r in rows if r.get("judge") and "error" not in (r.get("judge") or {})]
    report["judge"]["n"] = len(judged)
    if judged:
        report["judge"]["grounded"] = sum(int(j.get("grounded") or 0) for j in judged) / len(judged)
        report["judge"]["refusal_ok"] = sum(int(j.get("refusal_ok") or 0) for j in judged) / len(judged)
        report["judge"]["caveat_ok"] = sum(int(j.get("caveat_ok") or 0) for j in judged) / len(judged)
        report["judge"]["note"] = (
            "LLM-as-judge scores are uncalibrated until 30 human labels exist "
            "(see qna/human_spotcheck_30.csv). Do not treat these as a gate."
        )
        report["judge"]["family"] = discover().judge.family if discover().judge else None
        report["judge"]["generator_family"] = discover().generator.family if discover().generator else None

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print("gates_met", report["gates_met"])
    print("Wrote", REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
