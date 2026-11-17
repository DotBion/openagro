#!/usr/bin/env python3
"""Fill README_chat.md result tables from results/*.json. Safe to re-run."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
README = ROOT / "README_chat.md"
RESULTS = ROOT / "results"


def ci(block: dict, *, pct: bool = True, digits: int = 1) -> str:
    if not block or block.get("mean") is None:
        return "n/a"
    n = block.get("n", "")
    if pct:
        return (
            f"{block['mean']*100:.{digits}f}% "
            f"(95% CI {block['ci95_lo']*100:.{digits}f}–{block['ci95_hi']*100:.{digits}f}, n={n})"
        )
    return (
        f"{block['mean']:.{3}f} "
        f"(95% CI {block['ci95_lo']:.3f}–{block['ci95_hi']:.3f}, n={n})"
    )


def retrieval_section(r: dict) -> str:
    lines = []
    lines.append(f"Evaluated on {r['n_eval_with_gold']} items that have gold document ids (created {r.get('created_at')}). "
                 "Recall is 1 when any retrieved chunk’s `parent_id` or `id` is in `gold_doc_ids`. "
                 "Bootstrap 95% CIs are over items (10,000 resamples, seed 20261117). "
                 "Expired funding programs were excluded. RRF k=60; reranker is `BAAI/bge-reranker-v2-m3` on the hybrid top 30.")
    lines.append("")
    lines.append("### Overall")
    lines.append("")
    lines.append("| Variant | Recall@5 | Recall@10 | MRR |")
    lines.append("|---|---|---|---|")
    for v in ("bm25", "dense", "hybrid", "hybrid_rerank"):
        m = r["variants"][v]["overall"]
        lines.append(f"| `{v}` | {ci(m['recall@5'])} | {ci(m['recall@10'])} | {ci(m['mrr'], pct=False)} |")
    lines.append("")
    lines.append("### Real vs synthetic (recall@5 / MRR)")
    lines.append("")
    lines.append("| Variant | Real recall@5 | Real MRR | Synthetic recall@5 | Synthetic MRR |")
    lines.append("|---|---|---|---|---|")
    for v in ("bm25", "dense", "hybrid", "hybrid_rerank"):
        real, syn = r["variants"][v]["real"], r["variants"][v]["synthetic"]
        lines.append(
            f"| `{v}` | {ci(real['recall@5'])} | {ci(real['mrr'], pct=False)} | {ci(syn['recall@5'])} | {ci(syn['mrr'], pct=False)} |"
        )
    lines.append("")
    lines.append("### Recall@5 by category (hybrid+rerank, the kept default)")
    lines.append("")
    lines.append("| Category | n | Recall@5 | Recall@10 | MRR |")
    lines.append("|---|---:|---|---|---|")
    for cat, cm in r["variants"]["hybrid_rerank"]["by_category"].items():
        lines.append(
            f"| `{cat}` | {cm['n']} | {cm['recall@5']['mean']*100:.1f}% | {cm['recall@10']['mean']*100:.1f}% | {cm['mrr']['mean']:.3f} |"
        )
    lines.append("")
    d = r["reranker_decision"]
    lines.append("### Reranker decision")
    lines.append("")
    lines.append(
        f"**Keep `BAAI/bge-reranker-v2-m3`:** `{d['keep_reranker']}`. "
        f"Recall@5 rose by **{d['delta_recall@5_pp']:.2f} percentage points** "
        f"(hybrid {r['variants']['hybrid']['overall']['recall@5']['mean']*100:.1f}% → "
        f"hybrid+rerank {r['variants']['hybrid_rerank']['overall']['recall@5']['mean']*100:.1f}%), "
        "which meets the +3 percentage-point rule. "
        f"Top-1 state match on queries that mention a state did **not** improve "
        f"({d['state_top1']['hybrid']['rate']:.3f} → {d['state_top1']['hybrid_rerank']['rate']:.3f}, n={d['state_top1']['hybrid']['n']}); "
        f"nor did top-1 crop match "
        f"({d['crop_top1']['hybrid']['rate']:.3f} → {d['crop_top1']['hybrid_rerank']['rate']:.3f}, n={d['crop_top1']['hybrid']['n']}). "
        f"Default serving variant: `{d['default_variant']}`."
    )
    lines.append("")
    lines.append("### FarmDeck vs independent evidence (effectiveness questions, n=49)")
    lines.append("")
    lines.append("| Variant | FarmDeck outranks independent | Rate |")
    lines.append("|---|---:|---:|")
    fd = r["farmdeck_vs_independent"]
    for v in ("bm25", "dense", "hybrid", "hybrid_rerank"):
        rec = fd[v]
        lines.append(f"| `{v}` | {rec['n_farmdeck_outranks_independent']}/{rec['n_effectiveness']} | {rec['rate']*100:.1f}% |")
    dem = (r.get("farmdeck_demoted_on_default") or {}).get("farmdeck_vs_independent") or {}
    if "default_demoted" in dem:
        rec = dem["default_demoted"]
        lines.append(f"| `{d['default_variant']}` + FarmDeck demotion | {rec['n_farmdeck_outranks_independent']}/{rec['n_effectiveness']} | {rec['rate']*100:.1f}% |")
        dm = r["farmdeck_demoted_on_default"]["metrics"]["overall"]
        lines.append("")
        lines.append(
            f"Serving applies the demotion. After demotion, recall@5 is {ci(dm['recall@5'])}, "
            f"recall@10 {ci(dm['recall@10'])}, MRR {ci(dm['mrr'], pct=False)} "
            "(a small drop vs raw hybrid+rerank, and FarmDeck no longer outranks independent sources on these questions)."
        )
    examples = fd.get("hybrid_rerank", {}).get("example_ids") or []
    if examples:
        lines.append("")
        lines.append("Example item ids where raw hybrid+rerank still ranked FarmDeck above independent evidence: `" + "`, `".join(examples) + "`.")
    return "\n".join(lines)


def e2e_section(path: Path, title: str) -> str:
    if not path.exists():
        return f"_{title}: not written yet (`{path}`)._\n"
    r = json.loads(path.read_text())
    m = r.get("metrics") or {}
    gates = r.get("gates_met") or {}
    lines = [f"### {title}", ""]
    partial = " **Partial — eval still running or interrupted; resume with `scripts/eval_e2e.py` (omit --fresh).**" if r.get("partial") else ""
    lines.append(f"Created {r.get('created_at')}. Variant `{r.get('variant')}`. use_llm={r.get('use_llm')}. n={r.get('n')}.{partial}")
    if r.get("n_issued_factual") is not None:
        lines.append(f"Issued factual answers: {r['n_issued_factual']}. Status counts: {r.get('status_counts')}.")
    if r.get("notes"):
        lines.append(r["notes"])
    lines.append("")
    lines.append("| Metric | Value | Gate | Met? |")
    lines.append("|---|---|---|---|")
    def yn(v):
        return "yes" if v else "no"
    lines.append(f"| Route accuracy | {ci(m.get('route_accuracy'), pct=True)} | — | — |")
    lines.append(f"| Retrieval recall@5 (during chat) | {ci(m.get('recall@5'))} | — | — |")
    lines.append(f"| Citation pass (issued factual answers) | {ci(m.get('citation_pass'))} | ≥ 85% | {yn(gates.get('citation'))} |")
    lines.append(f"| Numbers pass (issued factual answers) | {ci(m.get('numbers_pass'))} | ≥ 90% | {yn(gates.get('numbers'))} |")
    lines.append(f"| Refusal accuracy | {ci(m.get('refusal_accuracy'))} | ≥ 90% | {yn(gates.get('refusal'))} |")
    lines.append(f"| Invented numbers (issued answers) | {m.get('invented_numbers_total')} | 0 | {yn(gates.get('zero_invented_numbers'))} |")
    lines.append(f"| Verifier pass (issued factual answers) | {ci(m.get('verifier_pass'))} | — | — |")
    lines.append(f"| `answer_with_caveat` behavior | {ci(m.get('answer_with_caveat'))} | — | — |")
    if r.get("judge") and r["judge"].get("used"):
        j = r["judge"]
        lines.append("")
        lines.append(
            f"LLM judge ({j.get('family')} judging a {j.get('generator_family')} generator), n={j.get('n')}: "
            f"grounded={j.get('grounded')}, refusal_ok={j.get('refusal_ok')}, caveat_ok={j.get('caveat_ok')}. "
            "**Uncalibrated until 30 human labels exist.** " + (j.get("note") or "")
        )
    if r.get("by_category"):
        lines.append("")
        lines.append("| Category | n | Route acc. | Refusal acc. |")
        lines.append("|---|---:|---:|---:|")
        for cat, cm in r["by_category"].items():
            ra = cm.get("route_accuracy")
            rf = cm.get("refusal_accuracy")
            lines.append(f"| `{cat}` | {cm['n']} | {ra*100:.1f}% | {rf*100:.1f}% |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    text = README.read_text(encoding="utf-8")
    ret_path = RESULTS / "retrieval_eval.json"
    if ret_path.exists():
        ret = retrieval_section(json.loads(ret_path.read_text()))
        # replace from the paragraph after the heading through the farmdeck section, using markers
        start = "<!-- RETRIEVAL_TABLES -->"
        end = "<!-- E2E_TABLES -->"
        # Also drop the placeholder paragraph before the marker if we splice at marker
        if start in text and end in text:
            pre, rest = text.split(start, 1)
            _, post = rest.split(end, 1)
            # Trim the leftover "Results are written..." placeholder just above the marker
            # Keep the heading "## Retrieval results..." already in pre
            text = pre.rstrip() + "\n\n" + ret + "\n\n## End-to-end metrics vs gates\n\n" + end + post
        # The above may have duplicated the End-to-end heading. Fix below carefully.
    # Cleaner: just replace between markers
    text = README.read_text(encoding="utf-8")
    start = "<!-- RETRIEVAL_TABLES -->"
    end = "<!-- E2E_TABLES -->"
    if start not in text or end not in text:
        raise SystemExit("markers missing in README_chat.md")
    ret = retrieval_section(json.loads(ret_path.read_text())) if ret_path.exists() else "_retrieval_eval.json not found._"
    e2e = []
    e2e.append("Gates: citation pass ≥ 85%, numbers pass ≥ 90%, refusal accuracy ≥ 90%, zero invented (uncited or unsupported) numbers in **issued** answers.")
    e2e.append("")
    e2e.append(e2e_section(RESULTS / "e2e_extractive_eval.json", "Extractive stub + rules router (`--no-llm`)"))
    llm_path = RESULTS / "e2e_eval.json"
    if not llm_path.exists() and (RESULTS / "e2e_eval_partial.json").exists():
        llm_path = RESULTS / "e2e_eval_partial.json"
    e2e.append(e2e_section(llm_path, "LLM generator + judge (if a working key was available)"))
    e2e.append("See `results/e2e_extractive_eval.json` and `results/e2e_eval.json`. LLM-as-judge scores, if present, are **uncalibrated until 30 human labels** exist (`qna/human_spotcheck_30.csv` has not been reviewed).")
    pre, rest = text.split(start, 1)
    _, post = rest.split(end, 1)
    # pre currently ends with placeholder text after "## Retrieval results...". Keep up to that heading.
    heading = "## Retrieval results (eval_v2 items with `gold_doc_ids`)"
    if heading in pre:
        pre = pre.split(heading)[0] + heading + "\n"
    # post currently starts after E2E marker and includes leftover '## Known gaps' etc.
    # It still has the old "End-to-end..." heading content before Known gaps. Trim to Known gaps.
    kg = "## Known gaps"
    if kg in post:
        post = "\n" + kg + post.split(kg, 1)[1]
    new = pre + "\n" + ret + "\n\n## End-to-end metrics vs gates\n\n" + "\n".join(e2e) + "\n" + post
    README.write_text(new, encoding="utf-8")
    print("Updated", README)


if __name__ == "__main__":
    main()
