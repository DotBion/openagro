#!/usr/bin/env python3
"""Write a partial e2e report from e2e_items.jsonl so an interrupt does not lose the tally."""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from rag.metrics import bootstrap_ci  # noqa
from rag.paths import RESULTS_DIR  # noqa

GATES = {"citation": 0.85, "numbers": 0.90, "refusal": 0.90, "invented_numbers": 0}

def rate(values):
    vals = [float(v) for v in values]
    return bootstrap_ci(vals) if vals else {"n": 0, "mean": None}

def main(path: Path, out: Path) -> None:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    cit = [r["citation_ok"] for r in rows if r.get("citation_ok") is not None]
    num = [r["numbers_ok"] for r in rows if r.get("numbers_ok") is not None]
    ref = [r["refusal_ok"] for r in rows]
    cav = [r["caveat_ok"] for r in rows if r.get("caveat_ok") is not None]
    ver = [r["verifier_pass"] for r in rows if r.get("verifier_pass") is not None]
    rec5 = [r["recall@5"] for r in rows if r.get("recall@5") is not None]
    rec10 = [r["recall@10"] for r in rows if r.get("recall@10") is not None]
    invented = sum(int(r.get("invented_numbers") or 0) for r in rows)
    citation_rate, numbers_rate = rate(cit), rate(num)
    refusal_rate = rate(ref)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "partial": True,
        "n": len(rows),
        "status_counts": dict(Counter(r.get("status") for r in rows)),
        "n_issued_factual": sum(1 for r in rows if r.get("status") in {"ok", "ok_after_retry", "hedged_extractive"}),
        "metrics": {
            "route_accuracy": rate([r["route_ok"] for r in rows]),
            "recall@5": rate(rec5) if rec5 else {"n": 0},
            "recall@10": rate(rec10) if rec10 else {"n": 0},
            "citation_pass": citation_rate,
            "numbers_pass": numbers_rate,
            "verifier_pass": rate(ver) if ver else {"n": 0},
            "refusal_accuracy": refusal_rate,
            "answer_with_caveat": rate(cav) if cav else {"n": 0, "mean": None},
            "invented_numbers_total": invented,
        },
        "gates_met": {
            "citation": (citation_rate.get("mean") or 0) >= GATES["citation"] if cit else False,
            "numbers": (numbers_rate.get("mean") or 0) >= GATES["numbers"] if num else False,
            "refusal": (refusal_rate.get("mean") or 0) >= GATES["refusal"] if ref else False,
            "zero_invented_numbers": invented == 0,
        },
        "notes": "Partial snapshot from e2e_items.jsonl while the eval is still running.",
    }
    judged = [r.get("judge") for r in rows if r.get("judge") and "error" not in (r.get("judge") or {})]
    report["judge"] = {"used": bool(judged), "uncalibrated": True, "n": len(judged)}
    if judged:
        report["judge"]["grounded"] = sum(int(j.get("grounded") or 0) for j in judged) / len(judged)
        report["judge"]["refusal_ok"] = sum(int(j.get("refusal_ok") or 0) for j in judged) / len(judged)
        report["judge"]["caveat_ok"] = sum(int(j.get("caveat_ok") or 0) for j in judged) / len(judged)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print("n", len(rows), "gates", report["gates_met"], "status", report["status_counts"], "wrote", out)

if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS_DIR / "e2e_items.jsonl"
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else RESULTS_DIR / "e2e_eval_partial.json"
    main(src, dest)
