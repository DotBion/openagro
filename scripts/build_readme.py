"""Write kb/README.md from the latest scrape, parse, abstract, chunk, and Q&A logs."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import KB_DIR, LOG_DIR, QNA_DIR, ROOT, read_jsonl  # noqa: E402

EXPECTED = {
    "she_funding": 206,
    "she_journal": 73,
    "she_qa": 33,
    "farmdeck_product": 57,
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def field_coverage(records: list[dict], source: str, fields: list[str]) -> dict:
    rows = [r for r in records if r.get("source") == source]
    n = len(rows) or 1
    out = {}
    for f in fields:
        filled = 0
        for r in rows:
            v = r.get(f)
            if v is None or v == "" or v == []:
                continue
            filled += 1
        out[f] = {"filled": filled, "of": len(rows), "pct": round(100 * filled / max(len(rows), 1), 1)}
    return out



def md_coverage_table(cov: dict) -> str:
    lines = ["| Field | Filled | Of | % |", "|---|---:|---:|---:|"]
    for f, s in cov.items():
        lines.append(f"| `{f}` | {s['filled']} | {s['of']} | {s['pct']} |")
    return "\n".join(lines)


def token_stats(rows: list[dict], field: str) -> dict:
    vals = sorted(int(r[field]) for r in rows if r.get(field) is not None)
    if not vals:
        return {"count": 0, "min": "", "median": "", "max": "", "avg": ""}
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    return {
        "count": n,
        "min": min(vals),
        "median": med if isinstance(med, int) or med != int(med) else int(med),
        "max": max(vals),
        "avg": round(sum(vals) / n, 1),
    }


def md_chunk_stats(chunks: list[dict], field: str, sources: list[str]) -> str:
    lines = [
        "| Source | Chunks | Parents | Min | Median | Max | Avg |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    overall = token_stats(chunks, field)
    parents = len({c.get("parent_id") for c in chunks})
    lines.append(
        f"| all | {overall['count']} | {parents} | {overall['min']} | {overall['median']} | {overall['max']} | {overall['avg']} |"
    )
    for src in sources:
        rows = [c for c in chunks if c.get("source") == src]
        st = token_stats(rows, field)
        n_par = len({c.get("parent_id") for c in rows})
        lines.append(
            f"| `{src}` | {st['count']} | {n_par} | {st['min']} | {st['median']} | {st['max']} | {st['avg']} |"
        )
    return "\n".join(lines)



def build_readme() -> None:
    records = read_jsonl(KB_DIR / "kb.jsonl") if (KB_DIR / "kb.jsonl").exists() else []
    chunks = read_jsonl(KB_DIR / "chunks.jsonl") if (KB_DIR / "chunks.jsonl").exists() else []
    discovery = load_json(LOG_DIR / "url_discovery.json", {})
    fetch = load_json(LOG_DIR / "fetch_log.json", {})
    parse = load_json(LOG_DIR / "parse_log.json", {})
    abstracts = load_json(LOG_DIR / "abstracts_log.json", {})
    chunk_log = load_json(LOG_DIR / "chunk_log.json", {})
    qna_log = load_json(LOG_DIR / "qna_log.json", {})
    chunks_v2 = read_jsonl(KB_DIR / "chunks_v2.jsonl") if (KB_DIR / "chunks_v2.jsonl").exists() else []
    manifest = load_json(KB_DIR / "index_manifest.json", {})
    eval_v2_log = load_json(LOG_DIR / "eval_v2_log.json", {})
    eval_v2 = read_jsonl(QNA_DIR / "eval_v2.jsonl") if (QNA_DIR / "eval_v2.jsonl").exists() else []

    by_source = Counter(r.get("source") for r in records)
    scrape_start = fetch.get("started_at") or discovery.get("started_at")
    scrape_end = fetch.get("finished_at") or parse.get("finished_at")
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    lines: list[str] = []
    lines.append("# OpenAgronomy knowledge base")
    lines.append("")
    lines.append(
        "Local corpus of public Soil Health Exchange and FarmDeck pages, plus abstracts of studies cited on FarmDeck (and other DOIs found in those pages). "
        "Nothing in this folder was emailed or posted; it lives on this computer only."
    )
    lines.append("")
    lines.append(f"- README generated: {now} (America/New_York is the box timezone).")
    if scrape_start:
        lines.append(f"- Scrape started: `{scrape_start}`")
    if scrape_end:
        lines.append(f"- Scrape / fetch finished: `{scrape_end}`")
    lines.append(f"- Scripts: `{ROOT / 'scripts'}`")
    lines.append("")
    lines.append("## Counts versus expected")
    lines.append("")
    lines.append(
        "Expected counts come from the earlier public-site inventory (206 funding programs, 73 Journal articles, 33 expert Q&As, 57 FarmDeck product profiles). "
        "Live sitemaps can add or remove pages after that inventory."
    )
    lines.append("")
    lines.append("| Source | Expected | Discovered | On disk (raw HTML or abstract sidecar) | KB records | Discrepancy vs expected |")
    lines.append("|---|---:|---:|---:|---:|---|")

    disc = (discovery.get("sources") or {})
    fetch_counts = fetch.get("counts") or {}
    notes = []
    for source, expected in EXPECTED.items():
        discovered = (disc.get(source) or {}).get("count", "")
        on_disk = (fetch_counts.get(source) or {}).get("on_disk", "")
        got = by_source.get(source, 0)
        if got == expected:
            delta = "match"
        elif got > expected:
            delta = f"+{got - expected}"
        else:
            delta = f"{got - expected}"
        lines.append(f"| `{source}` | {expected} | {discovered} | {on_disk} | {got} | {delta} |")
        live_only = (disc.get(source) or {}).get("live_only") or []
        snap_only = (disc.get(source) or {}).get("snapshot_only") or []
        if live_only:
            notes.append(f"- `{source}` live-sitemap-only URLs (added since the Nov 15, 2026 snapshot): " + ", ".join(f"`{u}`" for u in live_only))
        if snap_only:
            notes.append(f"- `{source}` snapshot-only URLs (no longer in the live sitemap): " + ", ".join(f"`{u}`" for u in snap_only))

    got_abs = by_source.get("paper_abstract", 0)
    n_dois = abstracts.get("n_dois", "")
    n_ok = abstracts.get("n_abstracts", got_abs)
    n_miss = len(abstracts.get("no_abstract") or [])
    n_fail = len(abstracts.get("failures") or [])
    remaining_fail = abstracts.get("n_not_found_after_retry", n_fail)
    lines.append(
        f"| `paper_abstract` | n/a (optional) | {n_dois} unique DOIs | {got_abs} sidecars | {got_abs} | "
        f"{got_abs} abstracts fetched; {n_miss} DOIs had metadata but no abstract; "
        f"{remaining_fail} still not found after cleaning trailing /full from extracted DOIs |"
    )
    lines.append("")
    if notes:
        lines.append("### Discrepancy notes")
        lines.append("")
        lines.extend(notes)
        lines.append("")

    if by_source.get("she_journal") and EXPECTED["she_journal"] and by_source["she_journal"] != EXPECTED["she_journal"]:
        lines.append(
            f"The Journal count is {by_source['she_journal']} rather than 73 because the live sitemap now includes "
            "pages that were not in the Nov 15, 2026 snapshot, or listing-page filters differed. See the live-only / snapshot-only lists above."
        )
        lines.append("")

    # Failures
    lines.append("## Failures and blocks")
    lines.append("")
    failures = []
    for f in fetch.get("failures") or []:
        failures.append(f"- Fetch: `{f.get('url')}` — {f.get('error') or f.get('status')}")
    for f in parse.get("failures") or []:
        failures.append(f"- Parse: `{f.get('path')}` — {f.get('error')}")
    for f in abstracts.get("failures") or []:
        failures.append(f"- Abstract not found: `{f.get('doi')}` — {f.get('crossref_error') or f.get('openalex_error') or f.get('status')}")
    no_abs = abstracts.get("no_abstract") or []
    if no_abs:
        failures.append(
            "- DOIs with no abstract in Crossref or OpenAlex: "
            + ", ".join(f"`{x.get('doi')}`" for x in no_abs)
        )
    if not failures:
        lines.append("No fetch or parse failures were logged.")
    else:
        lines.extend(failures)
    lines.append("")
    lines.append(
        "Robots.txt was respected. Soil Health Exchange and FarmDeck both disallow `/api/` (and admin/studio paths); those were not requested. "
        "OpenAgData `/api` remains disallowed and was not used. Requests were spaced at about one per second with a normal browser User-Agent."
    )
    lines.append("")

    # Field coverage
    lines.append("## Field coverage")
    lines.append("")
    lines.append("A field is counted as filled when it is present and non-empty on the KB record. Crop and practice are filled only from tags or labeled fields stated on the page, not inferred from body prose.")
    lines.append("")
    lines.append("### Expert Q&As (`she_qa`)")
    lines.append("")
    lines.append(
        md_coverage_table(
            field_coverage(
                records,
                "she_qa",
                ["title", "date", "author", "expert", "question", "states", "region", "crop", "practice", "tags", "identifier", "expert_answer", "references"],
            )
        )
    )
    lines.append("")
    lines.append("### Journal articles (`she_journal`)")
    lines.append("")
    lines.append(
        md_coverage_table(
            field_coverage(
                records,
                "she_journal",
                ["title", "date", "author", "identifier", "crop", "practice", "tags", "states", "references", "keywords"],
            )
        )
    )
    lines.append("")
    lines.append("### Funding programs (`she_funding`)")
    lines.append("")
    lines.append(
        md_coverage_table(
            field_coverage(
                records,
                "she_funding",
                [
                    "title",
                    "funder",
                    "amount",
                    "cost_share",
                    "deadline",
                    "states",
                    "coverage",
                    "eligibility_text",
                    "application_url",
                    "last_verified",
                    "who_can_apply",
                    "readiness",
                    "practice",
                    "tags",
                ],
            )
        )
    )
    lines.append("")
    lines.append("### FarmDeck products (`farmdeck_product`)")
    lines.append("")
    lines.append(
        md_coverage_table(
            field_coverage(
                records,
                "farmdeck_product",
                ["title", "category", "cited_dois", "last_verified", "description", "crop", "practice", "tags"],
            )
        )
    )
    lines.append("")
    lines.append("### Paper abstracts (`paper_abstract`)")
    lines.append("")
    lines.append(
        md_coverage_table(
            field_coverage(
                records,
                "paper_abstract",
                ["title", "date", "author", "doi", "abstract", "provider", "licenses"],
            )
        )
    )
    lines.append("")


    sources = ["she_qa", "she_journal", "she_funding", "farmdeck_product", "paper_abstract"]

    # Chunks v1
    lines.append("## Chunks (v1, kept)")
    lines.append("")
    lines.append(
        "`kb/chunks.jsonl` is the first pass. Token figures below are the original word-based `token_estimate`, not the bge-m3 tokenizer. "
        "Keep this file; retrieval work should use v2."
    )
    lines.append("")
    if chunks:
        lines.append(md_chunk_stats(chunks, "token_estimate", sources))
        if chunk_log:
            lines.append("")
            lines.append(
                f"{chunk_log.get('n_chunks', len(chunks))} chunks from {chunk_log.get('n_parents')} parent documents. "
                f"{chunk_log.get('outside_300_500')} chunks fall outside the original 300–500 word-estimate target, usually short funding pages or leftover tails."
            )
    else:
        lines.append("v1 chunks file is missing.")
    lines.append("")

    # Chunks v2
    lines.append("## Chunks v2 (bge-m3 tokens)")
    lines.append("")
    tok_method = manifest.get("token_count_method") or "AutoTokenizer.from_pretrained('BAAI/bge-m3').encode(text, add_special_tokens=False)"
    emb = manifest.get("embedding_model", "BAAI/bge-m3")
    dim = manifest.get("expected_dim", 1024)
    ver = manifest.get("chunk_version", "v2")
    lines.append(
        f"`kb/chunks_v2.jsonl` is the retrieval corpus. Token counts use the real `BAAI/bge-m3` tokenizer (`{tok_method}`). "
    )
    lines.append(
        f"Embedding model `{emb}`, expected dimension `{dim}`, chunk version `{ver}`. See `kb/index_manifest.json` for the startup guard fields."
    )
    lines.append("")
    if chunks_v2:
        lines.append(md_chunk_stats(chunks_v2, "token_count", sources))
    else:
        lines.append("v2 chunks file is missing.")
    lines.append("")
    lines.append("Chunking rules:")
    lines.append("")
    lines.append("- SHE expert Q&A: keep each question+answer whole unless it is over ~1000 tokens; then split at sections and repeat a short document header.")
    lines.append("- Funding programs: keep each program page whole (structured fields + eligibility) unless over ~1000 tokens. `deadline_iso` is parsed only when a single close date is clearly stated; `expired` is versus 2026-11-17; `as_of` is the last-verified ISO date.")
    lines.append("- Journal articles: heading-aware windows of 350–500 tokens with about 15% overlap. Short heading stubs may be folded into the next window.")
    lines.append("- Paper abstracts: exactly one chunk per abstract, even if outside the 350–500 window. `abstract_only: true`, trust tier 4.")
    lines.append("- FarmDeck products: heading-aware 350–500 tokens, no overlap. Trust tier 5.")
    lines.append("- Every chunk has raw `text` plus `embed_text` (contextual header: title + section path, source type, state/coverage if known, then the text).")
    lines.append("")
    if chunks_v2:
        fund = [c for c in chunks_v2 if c.get("source") == "she_funding"]
        n_iso = sum(1 for c in fund if c.get("deadline_iso"))
        n_asof = sum(1 for c in fund if c.get("as_of"))
        expired = Counter(c.get("expired") for c in fund)
        lines.append(
            f"Funding flags on {len(fund)} program chunks: `deadline_iso` filled on {n_iso}; "
            f"`expired` True={expired.get(True, 0)}, False={expired.get(False, 0)}, unknown/null={expired.get(None, 0)}; "
            f"`as_of` filled on {n_asof}. Expired is computed against 2026-11-17."
        )
        lines.append("")
        n_under = sum(1 for c in chunks_v2 if int(c.get("token_count") or 0) < 350)
        n_over500 = sum(1 for c in chunks_v2 if int(c.get("token_count") or 0) > 500)
        n_over1000 = sum(1 for c in chunks_v2 if int(c.get("token_count") or 0) > 1000)
        lines.append(
            f"Window notes: {n_under} chunks are under 350 tokens (short funding pages kept whole, leftover tails after heading-aware splits, and a few short abstracts). "
            f"{n_over500} are over 500 (SHE Q&As kept whole up to ~1000, journal overlap plus folded stubs, long abstracts kept whole). "
            f"{n_over1000} abstracts exceed 1000 tokens and were still kept as a single chunk."
        )
        lines.append("")

    lines.append("Trust tiers in v2 (different from v1): `1` expert Q&A, `2` peer-reviewed full source (none in this corpus), `3` SHE Journal article, `4` abstract-only paper (`abstract_only: true`), `5` FarmDeck product/commercial, `program_page` for funding program pages.")
    lines.append("")

    # QnA
    lines.append("## Q&A sets")
    lines.append("")
    real_path = QNA_DIR / "real_qa.jsonl"
    syn_path = QNA_DIR / "synthetic_qa.jsonl"
    eval_path = QNA_DIR / "eval_v2.jsonl"
    spot_path = QNA_DIR / "human_spotcheck_30.csv"
    lines.append(f"- Real expert Q&As (kept): `{real_path}` — {qna_log.get('n_real_qa', 33)} rows, copied from the published SHE pages.")
    lines.append(
        f"- Synthetic questions (kept): `{syn_path}` — {qna_log.get('n_synthetic', 52)} rows, all with `synthetic: true`. "
        f"By category: {qna_log.get('synthetic_by_category', {})}. "
        f"{qna_log.get('synthetic_gold_present', 0)} have a gold answer grounded in KB text; "
        f"{qna_log.get('synthetic_gold_null', 0)} have `gold_answer` null (FarmX items, refuse/out-of-scope items, or facts that could not be verified in the KB)."
    )
    lines.append("")
    if eval_v2 or eval_v2_log:
        n = eval_v2_log.get("n_eval_v2") or len(eval_v2)
        n_real = eval_v2_log.get("n_real") or sum(1 for r in eval_v2 if not r.get("synthetic"))
        n_syn = eval_v2_log.get("n_synthetic") or sum(1 for r in eval_v2 if r.get("synthetic"))
        by_cat = eval_v2_log.get("by_category") or dict(Counter(r.get("category") for r in eval_v2))
        by_beh = eval_v2_log.get("by_expected_behavior") or dict(Counter(r.get("expected_behavior") for r in eval_v2))
        lines.append(f"- Eval v2: `{eval_path}` — {n} items ({n_real} real, {n_syn} synthetic). Prior real and synthetic items are included, remapped to the eval schema. Paraphrases of the 33 published Q&As, ~10 partial-match items, and ~6 safety-refusal items were added. Every item has `human_checked: false`. `gold_answer` is null unless already grounded in the prior set or copied from a published expert answer.")
        lines.append("")
        lines.append("Eval v2 counts by category:")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|---|---:|")
        for k, v in by_cat.items():
            lines.append(f"| `{k}` | {v} |")
        lines.append("")
        lines.append("Eval v2 counts by expected behavior:")
        lines.append("")
        lines.append("| expected_behavior | Count |")
        lines.append("|---|---:|")
        for k, v in by_beh.items():
            lines.append(f"| `{k}` | {v} |")
        lines.append("")
        lines.append(
            f"- Human spot-check sample: `{spot_path}` — {eval_v2_log.get('spotcheck_n', 30)} synthetic items, stratified with a floor of 2 per category (paraphrase stratum capped) using `random.Random(20261117)`. "
            f"By category in the sample: {eval_v2_log.get('spotcheck_by_category', {})}. "
            "Columns include `reviewer_name`, `verdict` (pass / fail / revise), and `comments`. Nothing has been sent for review."
        )
    else:
        lines.append(f"- Eval v2 not built yet (`{eval_path}`).")
    lines.append("")

    # Verifier
    lines.append("## Answer verifier")
    lines.append("")
    lines.append(
        "`scripts/verify_answer.py` checks a model answer that uses citation markers of the form `[c:<chunk_id>]` against the retrieved chunks. "
        "It reports pass/fail as JSON, with a reason for each check."
    )
    lines.append("")
    lines.append("Checks:")
    lines.append("")
    lines.append("- Every cited chunk id must be in the retrieved set.")
    lines.append("- Every number in the answer (plain values, units, %, $, dates, ranges) must appear in a cited chunk after normalization (comma stripping, unit synonyms such as bu/ac and bushels per acre, and deterministic unit conversion). Uncited numbers fail.")
    lines.append("- Each sentence with a factual claim must have a citation. Key-claim spans need lexical support (token overlap / trigram match, configurable threshold, default 0.30) in the cited chunk; otherwise the report flags `cited_but_unsupported`.")
    lines.append("- Funding answers must include an as-of / last-verified date and a source URL, and must not assert eligibility (`you qualify`, `you are eligible`, and similar).")
    lines.append("- Citations to `abstract_only` chunks must not carry effect sizes that are absent from the abstract.")
    lines.append("")
    lines.append(
        "`scripts/units.py` converts lb/ac ↔ kg/ha and bu/ac ↔ kg/ha for corn (56 lb/bu) and soy (60 lb/bu). "
        "Tests live in `scripts/tests/` and use real v2 chunks."
    )
    lines.append("")
    lines.append("Usage (from `/workspace/openagronomy`, after `python3 -m venv .venv` and installing `transformers`, `tokenizers`, `pytest`, `python-dateutil`, `requests`):")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/pytest -q scripts/tests/")
    lines.append(".venv/bin/python -c 'from verify_answer import verify_answer'  # run from scripts/, or:")
    lines.append(".venv/bin/python scripts/verify_answer.py --help")
    lines.append("```")
    lines.append("")
    lines.append("If the verifier is run as a module, import `verify_answer(answer_text, retrieved_chunks)` and write the returned dict. Do not send reports off this computer.")
    lines.append("")

    # Layout
    lines.append("## Files")
    lines.append("")
    lines.append("```")
    lines.append("kb/")
    lines.append("  raw/she_qa/, raw/she_journal/, raw/she_funding/, raw/farmdeck_product/   # one HTML file per page")
    lines.append("  raw/sitemaps/                                                             # live sitemaps used for discovery")
    lines.append("  raw/paper_abstract/                                                       # compact Crossref/OpenAlex sidecars")
    lines.append("  kb.jsonl")
    lines.append("  chunks.jsonl            # v1, word-based token_estimate, kept")
    lines.append("  chunks_v2.jsonl         # retrieval chunks, bge-m3 token_count")
    lines.append("  index_manifest.json     # embedding model, tokenizer, dim 1024, chunk version")
    lines.append("  logs/                   # discovery, fetch, parse, abstracts, chunks, qna, eval")
    lines.append("  README.md")
    lines.append("qna/real_qa.jsonl")
    lines.append("qna/synthetic_qa.jsonl")
    lines.append("qna/eval_v2.jsonl")
    lines.append("qna/human_spotcheck_30.csv")
    lines.append("scripts/rechunk_v2.py")
    lines.append("scripts/build_eval_v2.py")
    lines.append("scripts/verify_answer.py")
    lines.append("scripts/units.py")
    lines.append("scripts/tests/")
    lines.append("```")
    lines.append("")

    # Regenerate
    lines.append("## How to regenerate")
    lines.append("")
    lines.append("From `/workspace/openagronomy`:")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 scripts/build_all.py")
    lines.append("```")
    lines.append("")
    lines.append("Or step by step (use `.venv/bin/python` once the venv exists):")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 scripts/fetch_pages.py            # discover sitemaps + fetch HTML (~1 req/sec)")
    lines.append("python3 scripts/parse_kb.py               # parse raw HTML -> kb.jsonl + chunks.jsonl (v1)")
    lines.append("python3 scripts/fetch_abstracts.py        # Crossref/OpenAlex abstracts for cited DOIs, append to kb.jsonl")
    lines.append("python3 scripts/parse_kb.py               # re-chunk v1 after abstracts")
    lines.append(".venv/bin/python scripts/rechunk_v2.py    # chunks_v2.jsonl + index_manifest.json")
    lines.append("python3 scripts/build_qna.py              # real_qa.jsonl + synthetic_qa.jsonl (kept)")
    lines.append(".venv/bin/python scripts/build_eval_v2.py # eval_v2.jsonl + human_spotcheck_30.csv")
    lines.append(".venv/bin/pytest -q scripts/tests/        # verifier + unit conversion")
    lines.append("python3 scripts/build_readme.py           # this file")
    lines.append("```")
    lines.append("")
    lines.append("`fetch_pages.py` skips HTML files that already exist and are larger than 500 bytes unless you pass `--force`.")
    lines.append("Abstracts are retrieved for unique DOIs found in FarmDeck `cited_dois` and in page text or reference lists. Only abstracts are stored, not full text. License or OA status is copied from the API when present.")
    lines.append("")
    lines.append("Do not fetch OpenAgData `/api` (disallowed by robots.txt). Do not send these files to anyone from the scripts.")
    lines.append("")

    (KB_DIR / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {KB_DIR / 'README.md'}", flush=True)


if __name__ == "__main__":
    build_readme()
