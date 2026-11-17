#!/usr/bin/env python3
"""Compare Nova Lite e2e (e2e_nova_*) against the extractive after-fix run."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
RESULTS = ROOT / "results"
NOVA = RESULTS / "e2e_nova_items.jsonl"
EXT = RESULTS / "e2e_extractive_items.jsonl"
NOVA_REP = RESULTS / "e2e_nova_eval.json"
EXT_REP = RESULTS / "e2e_extractive_eval.json"
OUT = RESULTS / "e2e_nova_vs_extractive.json"

FACTUAL = {"ok", "ok_after_retry", "answer_with_caveat", "answer_with_caveat_after_retry"}


def load_items(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def mean_len(rows: list[dict]) -> float:
    ans = [r.get("answer") or "" for r in rows if r.get("status") in FACTUAL]
    if not ans:
        return 0.0
    return sum(len(a) for a in ans) / len(ans)


def issued(rows):
    return [r for r in rows if r.get("status") in FACTUAL]


def summarize(rows, report=None):
    st = Counter(r.get("status") for r in rows)
    gen = Counter(r.get("generator") for r in rows)
    rec = [r for r in rows if r.get("recall@5") is not None]
    cit = [r for r in issued(rows) if r.get("citation_ok") is not None]
    num = [r for r in issued(rows) if r.get("numbers_ok") is not None]
    out = {
        "n": len(rows),
        "status_counts": dict(st),
        "generators": dict(gen),
        "issued_factual": len(issued(rows)),
        "hedged": st.get("hedged_extractive", 0) + st.get("hedged", 0),
        "refused": st.get("refused", 0),
        "llm_generator_share": (gen.get("llm", 0) / len(rows)) if rows else 0,
        "route_accuracy": sum(1 for r in rows if r.get("route_ok")) / len(rows) if rows else None,
        "chat_recall@5": (sum(1 for r in rec if r.get("recall@5")) / len(rec)) if rec else None,
        "citation_pass": (sum(1 for r in cit if r.get("citation_ok")) / len(cit)) if cit else None,
        "citation_n": len(cit),
        "numbers_pass": (sum(1 for r in num if r.get("numbers_ok")) / len(num)) if num else None,
        "numbers_n": len(num),
        "refusal_accuracy": sum(1 for r in rows if r.get("refusal_ok")) / len(rows) if rows else None,
        "invented_numbers": (report or {}).get("metrics", {}).get("invented_numbers_total"),
        "avg_issued_answer_chars": mean_len(rows),
    }
    if report:
        out["caveat_n9"] = (report.get("caveat_behavior") or {}).get("expected_answer_with_caveat_n9")
        out["caveat_n10"] = (report.get("caveat_behavior") or {}).get("partial_match_n10")
        out["gates_met"] = report.get("gates_met")
        out["llm_usage"] = report.get("llm_usage")
    return out


def main() -> int:
    nova_rows = load_items(NOVA)
    ext_rows = load_items(EXT)
    nova_rep = json.loads(NOVA_REP.read_text()) if NOVA_REP.exists() else {}
    ext_rep = json.loads(EXT_REP.read_text()) if EXT_REP.exists() else {}
    nova_by = {r["id"]: r for r in nova_rows}
    ext_by = {r["id"]: r for r in ext_rows}
    # Prefer issued factual LLM answers that also exist in extractive
    candidates = []
    for rid, nr in nova_by.items():
        er = ext_by.get(rid)
        if not er:
            continue
        if nr.get("generator") != "llm":
            continue
        if nr.get("status") not in FACTUAL or er.get("status") not in FACTUAL:
            continue
        candidates.append(rid)
    # pick 3 diverse categories
    examples = []
    seen_cat = set()
    for rid in candidates:
        cat = nova_by[rid].get("category")
        if cat in seen_cat and len(examples) < 3:
            continue
        examples.append({
            "id": rid,
            "category": cat,
            "question": None,
            "nova_status": nova_by[rid].get("status"),
            "extractive_status": ext_by[rid].get("status"),
            "nova_answer": nova_by[rid].get("answer"),
            "extractive_answer": ext_by[rid].get("answer"),
        })
        seen_cat.add(cat)
        if len(examples) >= 3:
            break
    # fill questions from eval_v2
    qpath = ROOT / "qna" / "eval_v2.jsonl"
    qmap = {}
    if qpath.exists():
        for line in qpath.open():
            if line.strip():
                rec = json.loads(line)
                qmap[rec["id"]] = rec.get("question")
    for ex in examples:
        ex["question"] = qmap.get(ex["id"])

    report = {
        "created_at": nova_rep.get("created_at"),
        "nova": summarize(nova_rows, nova_rep),
        "extractive": summarize(ext_rows, ext_rep),
        "examples": examples,
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False)[:4000])
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
