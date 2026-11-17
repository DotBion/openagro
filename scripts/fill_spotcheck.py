#!/usr/bin/env python3
"""Fill qna/human_spotcheck_30.csv model answers from the latest e2e run.

Prefers answers from results/e2e_items.jsonl when an id is present, then
falls back to the extractive run. Label columns are left empty for the reviewer.
Does not invent scores.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
CSV_PATH = ROOT / "qna" / "human_spotcheck_30.csv"
ITEMS = ROOT / "results" / "e2e_items.jsonl"
FALLBACK = ROOT / "results" / "e2e_extractive_items.jsonl"

OUT_COLUMNS = [
    "id",
    "category",
    "expected_behavior",
    "question",
    "gold_doc_ids",
    "gold_answer",
    "paraphrase_of",
    "notes",
    "model_answer",
    "model_status",
    "model_route",
    "model_source",
    "model_generator",
    "reviewer_name",
    "human_checked",
    "grounded",
    "refusal_ok",
    "caveat_ok",
    "invented_numbers",
    "verdict",
    "comments",
]


def load_items(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id"):
            out[rec["id"]] = rec
    return out


def main() -> int:
    primary = load_items(ITEMS)
    ext = load_items(FALLBACK)
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = []
    n_primary = n_ext = 0
    for row in rows:
        rid = row["id"]
        if rid in primary:
            rec = primary[rid]
            row["model_source"] = "e2e_items"
            n_primary += 1
        elif rid in ext:
            rec = ext[rid]
            row["model_source"] = "extractive_e2e"
            n_ext += 1
        else:
            rec = None
        if not rec:
            missing.append(rid)
            row["model_answer"] = ""
            row["model_status"] = ""
            row["model_route"] = ""
            row["model_source"] = ""
            row["model_generator"] = ""
        else:
            row["model_answer"] = (rec.get("answer") or "").replace("\r\n", "\n")
            row["model_status"] = rec.get("status") or ""
            row["model_route"] = rec.get("got_route") or ""
            row["model_generator"] = rec.get("generator") or ""
        row["reviewer_name"] = ""
        row["human_checked"] = ""
        row["grounded"] = ""
        row["refusal_ok"] = ""
        row["caveat_ok"] = ""
        row["invented_numbers"] = ""
        row["verdict"] = ""
        row["comments"] = ""
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in OUT_COLUMNS})
    print(
        f"Wrote {CSV_PATH} ({len(rows)} rows; {n_primary} from e2e_items.jsonl, "
        f"{n_ext} from extractive fallback, {len(missing)} missing: {missing})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
