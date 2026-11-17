"""Build kb/chunks_v2.jsonl with bge-m3 token counts. Leaves chunks.jsonl (v1) in place."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bge_m3 import CHUNK_VERSION, EXPECTED_DIM, MODEL_NAME, count_tokens, decode_ids, encode_ids  # noqa: E402
from common import KB_DIR, LOG_DIR, ensure_dirs, read_jsonl, write_json, write_jsonl  # noqa: E402
from deadlines import TODAY, parse_deadline, parse_last_verified  # noqa: E402

SOURCE_LABEL = {
    "she_qa": "SHE expert Q&A",
    "she_journal": "SHE Journal article",
    "she_funding": "SHE funding program",
    "farmdeck_product": "FarmDeck product profile",
    "paper_abstract": "Peer-reviewed paper abstract",
}

TRUST_TIER = {
    "she_qa": 1,
    "she_journal": 3,
    "paper_abstract": 4,  # abstract-only in this corpus
    "farmdeck_product": 5,
    # funding uses the separate marker "program_page"
}

QA_MAX = 1000
FUNDING_MAX = 1000
WINDOW_MIN = 350
WINDOW_MAX = 500
JOURNAL_OVERLAP = 0.15


def trust_for(rec: dict) -> int | str:
    if rec.get("source") == "she_funding":
        return "program_page"
    return TRUST_TIER[rec["source"]]


def display_title(rec: dict) -> str:
    title = rec.get("title") or rec.get("id")
    if rec.get("source") == "she_funding" and rec.get("funder"):
        funder = rec["funder"]
        if funder.lower() not in title.lower():
            return f"{funder} — {title}"
    return title


def state_label(rec: dict) -> str | None:
    states = rec.get("states") or []
    region = rec.get("region")
    if states:
        return ", ".join(str(s) for s in states)
    if region:
        return str(region)
    return None


def make_embed_text(title: str, section_path: str | None, rec: dict, text: str, extra: dict | None = None) -> str:
    path = section_path.strip() if section_path else ""
    if path and path != title and not path.startswith(title):
        header_line = f"{title} > {path}"
    elif path:
        header_line = path
    else:
        header_line = title
    lines = [header_line, f"Source: {SOURCE_LABEL.get(rec.get('source'), rec.get('source'))}"]
    loc = state_label(rec)
    if loc:
        lines.append(f"State/coverage: {loc}")
    extra = extra or {}
    if extra.get("as_of"):
        lines.append(f"As of: {extra['as_of']}")
    if rec.get("source") == "she_qa" and rec.get("crop"):
        lines.append("Crop (stated): " + ", ".join(rec["crop"]))
    if rec.get("source") == "paper_abstract" and rec.get("doi"):
        lines.append(f"DOI: {rec['doi']}")
        lines.append("Note: abstract only; not the full paper.")
    lines.append("")
    lines.append(text.strip())
    return "\n".join(lines)


def split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Return (heading_path_tail, section_text including heading line)."""
    lines = (text or "").splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_h = ""
    buf: list[str] = []
    stack: list[tuple[int, str]] = []

    def flush():
        nonlocal buf, current_h
        body = "\n".join(buf).strip()
        if body:
            sections.append((current_h, body))
        buf = []

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            name = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, name))
            current_h = " > ".join(h for _, h in stack)
            buf = [line]
        else:
            buf.append(line)
    flush()
    if not sections:
        return [("", (text or "").strip())]
    return sections


def repeated_header(rec: dict, as_of: str | None = None) -> str:
    parts = [f"# {display_title(rec)}"]
    if rec.get("source") == "she_qa":
        if rec.get("expert"):
            parts.append(f"**Expert:** {rec['expert']}")
        if rec.get("identifier") or rec.get("id"):
            parts.append(f"**Identifier:** {rec.get('identifier') or rec.get('id')}")
        q = rec.get("question") or rec.get("question_title")
        if q:
            parts.append(f"**Question:** {q.strip()}")
    elif rec.get("source") == "she_funding":
        if rec.get("funder"):
            parts.append(f"**Funder:** {rec['funder']}")
        if rec.get("coverage") or state_label(rec):
            parts.append(f"**Coverage:** {rec.get('coverage') or state_label(rec)}")
        if as_of:
            parts.append(f"**As of (last verified):** {as_of}")
        if rec.get("application_url"):
            parts.append(f"**Official program URL:** {rec['application_url']}")
    return "\n".join(parts) + "\n"


def split_markdown_table(text: str, max_tok: int) -> list[str] | None:
    lines = text.splitlines()
    table_idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("|")]
    if len(table_idx) < 4:
        return None
    # Require the table to be the bulk of the section
    if len(table_idx) < 0.4 * max(1, sum(1 for ln in lines if ln.strip())):
        return None
    first, last = table_idx[0], table_idx[-1]
    preamble = "\n".join(lines[:first]).strip()
    header_rows = []
    body_rows = []
    # Keep the header row and the separator row with every piece
    tlines = lines[first : last + 1]
    if len(tlines) >= 2 and re.search(r"\|\s*-{2,}", tlines[1]):
        header_rows = tlines[:2]
        body_rows = tlines[2:]
    else:
        header_rows = tlines[:1]
        body_rows = tlines[1:]
    post = "\n".join(lines[last + 1 :]).strip()
    header_txt = "\n".join(header_rows)
    pieces = []
    acc_rows: list[str] = []
    def pack_rows(rows: list[str], first_piece: bool = False) -> None:
        acc: list[str] = []
        for row in rows:
            trial_rows = acc + [row]
            trial = (preamble + "\n\n" if (first_piece and not pieces and preamble) else "")
            trial += header_txt + "\n" + "\n".join(trial_rows)
            if acc and count_tokens(trial) > max_tok:
                chunk = header_txt + "\n" + "\n".join(acc)
                if first_piece and not pieces and preamble:
                    chunk = preamble + "\n\n" + chunk
                pieces.append(chunk.strip())
                acc = [row]
            else:
                acc = trial_rows
        if acc:
            chunk = header_txt + "\n" + "\n".join(acc)
            if first_piece and not pieces and preamble:
                chunk = preamble + "\n\n" + chunk
            pieces.append(chunk.strip())
    pack_rows(body_rows, first_piece=True)
    if post:
        if pieces and count_tokens(pieces[-1] + "\n\n" + post) <= max_tok:
            pieces[-1] = pieces[-1] + "\n\n" + post
        else:
            pieces.extend(sentence_chunks(post, max_tok) if count_tokens(post) > max_tok else [post])
    return pieces or None


def sentence_chunks(text: str, max_tok: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= max_tok:
        return [text]
    table_parts = split_markdown_table(text, max_tok)
    if table_parts:
        out: list[str] = []
        for part in table_parts:
            if count_tokens(part) <= max_tok:
                out.append(part)
            else:
                out.extend(_sentence_split(part, max_tok))
        return out
    return _sentence_split(text, max_tok)


def _sentence_split(text: str, max_tok: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= max_tok:
        return [text]
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'(\[])", text)
    parts = [p for p in parts if p.strip()] or [text]
    out, acc = [], ""
    for p in parts:
        trial = (acc + " " + p).strip() if acc else p
        if acc and count_tokens(trial) > max_tok:
            out.append(acc)
            acc = p
            if count_tokens(acc) > max_tok:
                out.extend(_force_windows(acc, max_tok))
                acc = ""
        else:
            acc = trial
    if acc:
        if count_tokens(acc) > max_tok:
            out.extend(_force_windows(acc, max_tok))
        else:
            out.append(acc)
    return out


def _force_windows(text: str, max_tok: int) -> list[str]:
    ids = encode_ids(text)
    if len(ids) <= max_tok:
        return [text]
    out = []
    i = 0
    while i < len(ids):
        out.append(decode_ids(ids[i : i + max_tok]))
        i += max_tok
    return [p for p in out if p]


def tail_by_tokens(text: str, n_tokens: int) -> str:
    if n_tokens <= 0 or not text:
        return ""
    ids = encode_ids(text)
    if len(ids) <= n_tokens:
        return text.strip()
    tail = decode_ids(ids[-n_tokens:])
    # Prefer starting at a sentence boundary in the first half of the tail.
    m = re.search(r"(?<=[.!?])\s+", tail)
    if m and m.end() < max(20, len(tail) // 2):
        tail = tail[m.end() :]
    return tail.strip()


def pack_windows(
    sections: list[tuple[str, str]],
    min_tok: int,
    max_tok: int,
    overlap: float = 0.0,
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Pack heading sections into token windows. Returns (section_path, chunk_text)."""
    prefix = prefix.strip()
    prefix_toks = count_tokens(prefix) if prefix else 0
    usable_max = max(max_tok - prefix_toks - 2, 80) if prefix else max_tok

    pieces: list[tuple[str, str]] = []  # path, body (no prefix)

    def emit_body(path: str, body: str) -> None:
        body = body.strip()
        if not body:
            return
        if count_tokens(body) <= usable_max:
            pieces.append((path, body))
            return
        for part in sentence_chunks(body, usable_max):
            pieces.append((path, part))

    acc_path = ""
    acc_body = ""
    for path, body in sections:
        body = body.strip()
        if not body:
            continue
        trial = (acc_body + "\n\n" + body).strip() if acc_body else body
        if acc_body and count_tokens(trial) > usable_max:
            emit_body(acc_path, acc_body)
            acc_body, acc_path = body, path
        elif count_tokens(body) > usable_max and not acc_body:
            emit_body(path, body)
        else:
            acc_body = trial
            # Keep a combined path when packing multiple sections
            if not acc_path:
                acc_path = path
            elif path and path not in acc_path:
                acc_path = acc_path + " > " + path.split(" > ")[-1]
    if acc_body:
        emit_body(acc_path, acc_body)

    # Merge undersized pieces forward/back when possible
    merged: list[tuple[str, str]] = []
    for path, body in pieces:
        if merged and count_tokens(merged[-1][1]) < min_tok:
            trial = merged[-1][1] + "\n\n" + body
            if count_tokens(trial) <= usable_max:
                p0 = merged[-1][0]
                if path and path not in p0:
                    p0 = p0 + " > " + path.split(" > ")[-1] if p0 else path
                merged[-1] = (p0, trial)
                continue
        merged.append((path, body))
    if len(merged) >= 2 and count_tokens(merged[-1][1]) < min_tok:
        trial = merged[-2][1] + "\n\n" + merged[-1][1]
        if count_tokens(trial) <= usable_max + 40:
            p0 = merged[-2][0]
            p1 = merged[-1][0]
            if p1 and p1 not in p0:
                p0 = p0 + " > " + p1.split(" > ")[-1] if p0 else p1
            merged[-2] = (p0, trial)
            merged.pop()

    # Fold a leading stub (title/byline or a leftover under 200 tokens) into the following chunk.
    folded: list[tuple[str, str]] = []
    i = 0
    while i < len(merged):
        path, body = merged[i]
        if i + 1 < len(merged) and count_tokens(body) < 200:
            nxt_path, nxt_body = merged[i + 1]
            combined = body + "\n\n" + nxt_body
            # Allow modest overflow so a heading/byline is not left as its own chunk
            if count_tokens(combined) <= usable_max + 120:
                p = path
                if nxt_path and nxt_path not in p:
                    p = (p + " > " + nxt_path.split(" > ")[-1]) if p else nxt_path
                folded.append((p, combined))
                i += 2
                continue
        folded.append((path, body))
        i += 1
    merged = folded

    # Apply overlap on the body (not the prefix)
    if overlap and len(merged) > 1:
        overlapped: list[tuple[str, str]] = [merged[0]]
        for i, (path, body) in enumerate(merged[1:], 1):
            prev = overlapped[-1][1] if not prefix else merged[i - 1][1]
            # use original previous body without accumulating overlap
            prev_body = merged[i - 1][1]
            n_over = max(1, int(round(count_tokens(prev_body) * overlap)))
            tail = tail_by_tokens(prev_body, n_over)
            if tail and tail not in body[: len(tail) + 20]:
                new_body = tail + "\n\n" + body
                # Keep overlap only when the result stays near the window
                if count_tokens(new_body) <= usable_max + int(usable_max * overlap) + 5:
                    body = new_body
            overlapped.append((path, body))
        merged = overlapped

    out = []
    for path, body in merged:
        if prefix:
            text = prefix.rstrip() + "\n\n" + body
        else:
            text = body
        out.append((path, text.strip()))
    return out


def funding_extra(rec: dict) -> dict:
    as_of = parse_last_verified(rec.get("last_verified"))
    parsed = parse_deadline(rec.get("deadline"), TODAY)
    return {
        "as_of": as_of,
        "last_verified": rec.get("last_verified"),
        "deadline": rec.get("deadline"),
        "deadline_iso": parsed["deadline_iso"],
        "expired": parsed["expired"],
        "deadline_parse_note": parsed["deadline_parse_note"],
        "application_url": rec.get("application_url"),
        "funder": rec.get("funder"),
        "amount": rec.get("amount"),
        "cost_share": rec.get("cost_share"),
        "eligibility_text": rec.get("eligibility_text"),
        "who_can_apply": rec.get("who_can_apply"),
        "coverage": rec.get("coverage"),
    }


def base_chunk(rec: dict, extra: dict | None = None) -> dict:
    extra = extra or {}
    ch = {
        "parent_id": rec["id"],
        "source": rec.get("source"),
        "url": rec.get("url"),
        "title": rec.get("title"),
        "author": rec.get("author") or rec.get("expert"),
        "states": rec.get("states") or [],
        "region": rec.get("region"),
        "crop": rec.get("crop") or [],
        "practice": rec.get("practice") or [],
        "tags": rec.get("tags") or [],
        "trust_tier": trust_for(rec),
        "abstract_only": rec.get("source") == "paper_abstract",
        "chunk_version": CHUNK_VERSION,
        "date": rec.get("date"),
        "doi": rec.get("doi"),
    }
    if rec.get("source") == "she_funding":
        ch.update({k: extra.get(k) for k in [
            "as_of", "last_verified", "deadline", "deadline_iso", "expired",
            "deadline_parse_note", "application_url", "funder", "coverage",
        ]})
    if rec.get("source") == "farmdeck_product":
        ch["category"] = rec.get("category")
        ch["cited_dois"] = rec.get("cited_dois") or []
        ch["as_of"] = parse_last_verified(rec.get("last_verified"))
    if rec.get("source") == "paper_abstract":
        ch["container"] = rec.get("container")
        ch["provider"] = rec.get("provider")
        ch["licenses"] = rec.get("licenses") or []
    return {k: v for k, v in ch.items() if v is not None or k in {"abstract_only", "states", "crop", "practice"}}


def finalize(parent_chunks: list[dict]) -> list[dict]:
    n = len(parent_chunks)
    out = []
    for i, ch in enumerate(parent_chunks, 1):
        ch = dict(ch)
        ch["chunk_index"] = i
        ch["n_chunks"] = n
        ch["id"] = f"{ch['parent_id']}::c{i}"
        ch["token_count"] = count_tokens(ch["text"])
        ch["embed_token_count"] = count_tokens(ch["embed_text"])
        out.append(ch)
    return out


def chunk_qa(rec: dict) -> list[dict]:
    text = (rec.get("text") or "").strip()
    title = display_title(rec)
    extra = {}
    if count_tokens(text) <= QA_MAX:
        ch = base_chunk(rec)
        ch["text"] = text
        ch["section_path"] = title
        ch["embed_text"] = make_embed_text(title, None, rec, text)
        return finalize([ch])
    header = repeated_header(rec)
    sections = split_markdown_sections(text)
    chunks = []
    short = header
    pieces = pack_windows(sections, min_tok=350, max_tok=QA_MAX - count_tokens(short) - 4, overlap=0.0, prefix="")
    for path, body in pieces:
        # If body already starts with the title heading, don't prepend the short header
        if body.lstrip().startswith("# " + (rec.get("title") or "")[:20]):
            text_i = body
        else:
            text_i = short.rstrip() + "\n\n" + body
        ch = base_chunk(rec)
        ch["text"] = text_i.strip()
        ch["section_path"] = path or title
        ch["embed_text"] = make_embed_text(title, path, rec, text_i)
        chunks.append(ch)
    return finalize(chunks or [_single(rec, text, title)])


def _single(rec: dict, text: str, title: str, path: str | None = None, extra: dict | None = None) -> dict:
    ch = base_chunk(rec, extra)
    ch["text"] = text.strip()
    ch["section_path"] = path or title
    ch["embed_text"] = make_embed_text(title, path, rec, text, extra)
    return ch


def chunk_funding(rec: dict) -> list[dict]:
    text = (rec.get("text") or "").strip()
    title = display_title(rec)
    extra = funding_extra(rec)
    if count_tokens(text) <= FUNDING_MAX:
        return finalize([_single(rec, text, title, title, extra)])
    header = repeated_header(rec, as_of=extra.get("as_of"))
    sections = split_markdown_sections(text)
    pieces = pack_windows(sections, min_tok=350, max_tok=FUNDING_MAX - count_tokens(header) - 4, overlap=0.0)
    chunks = []
    for path, body in pieces:
        if body.lstrip().startswith("# "):
            text_i = body
        else:
            text_i = header.rstrip() + "\n\n" + body
        chunks.append(_single(rec, text_i, title, path or title, extra))
    return finalize(chunks)


def chunk_journal(rec: dict) -> list[dict]:
    text = (rec.get("text") or "").strip()
    title = display_title(rec)
    sections = split_markdown_sections(text)
    pieces = pack_windows(sections, min_tok=WINDOW_MIN, max_tok=WINDOW_MAX, overlap=JOURNAL_OVERLAP)
    return finalize([_single(rec, body, title, path or title) for path, body in pieces] or [_single(rec, text, title)])


def chunk_product(rec: dict) -> list[dict]:
    text = (rec.get("text") or "").strip()
    title = display_title(rec)
    sections = split_markdown_sections(text)
    pieces = pack_windows(sections, min_tok=WINDOW_MIN, max_tok=WINDOW_MAX, overlap=0.0)
    return finalize([_single(rec, body, title, path or title) for path, body in pieces] or [_single(rec, text, title)])


def chunk_abstract(rec: dict) -> list[dict]:
    text = (rec.get("text") or rec.get("abstract") or "").strip()
    title = display_title(rec)
    return finalize([_single(rec, text, title, title)])


CHUNKERS = {
    "she_qa": chunk_qa,
    "she_funding": chunk_funding,
    "she_journal": chunk_journal,
    "farmdeck_product": chunk_product,
    "paper_abstract": chunk_abstract,
}


def stats_for(chunks: list[dict]) -> dict:
    if not chunks:
        return {"count": 0}
    toks = sorted(c["token_count"] for c in chunks)
    return {
        "count": len(chunks),
        "parents": len({c["parent_id"] for c in chunks}),
        "token_min": toks[0],
        "token_median": int(median(toks)),
        "token_max": toks[-1],
        "token_avg": round(sum(toks) / len(toks), 1),
        "below_350": sum(1 for t in toks if t < 350),
        "in_350_500": sum(1 for t in toks if 350 <= t <= 500),
        "above_500": sum(1 for t in toks if t > 500),
        "above_1000": sum(1 for t in toks if t > 1000),
    }


def build() -> list[dict]:
    ensure_dirs()
    records = read_jsonl(KB_DIR / "kb.jsonl")
    all_chunks: list[dict] = []
    by_source: dict[str, list[dict]] = defaultdict(list)
    for i, rec in enumerate(records, 1):
        fn = CHUNKERS.get(rec.get("source"))
        if not fn:
            continue
        chs = fn(rec)
        all_chunks.extend(chs)
        by_source[rec["source"]].extend(chs)
        if i % 50 == 0 or i == len(records):
            print(f"  [{i}/{len(records)}] {rec['source']} running total {len(all_chunks)} chunks", flush=True)

    out_path = KB_DIR / "chunks_v2.jsonl"
    write_jsonl(out_path, all_chunks)

    per_source = {s: stats_for(chs) for s, chs in by_source.items()}
    overall = stats_for(all_chunks)
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = {
        "chunk_version": CHUNK_VERSION,
        "embedding_model": MODEL_NAME,
        "tokenizer": MODEL_NAME,
        "tokenizer_class": "XLMRobertaTokenizer",
        "expected_dim": EXPECTED_DIM,
        "token_count_method": "AutoTokenizer.from_pretrained('BAAI/bge-m3').encode(text, add_special_tokens=False)",
        "created_at": created,
        "today_for_expired_flag": TODAY.isoformat(),
        "source_file": str(KB_DIR / "kb.jsonl"),
        "chunks_file": str(out_path),
        "n_parents": overall.get("parents"),
        "n_chunks": overall.get("count"),
        "rules": {
            "she_qa": "Keep the whole question+answer unless over ~1000 bge-m3 tokens; then split at markdown sections and repeat a short doc header.",
            "she_funding": "Keep the whole program page (structured fields + eligibility) unless over ~1000 tokens; then split at sections with header repeated. deadline_iso is parsed only when a single close date is clearly stated; expired is versus 2026-11-17; as_of is last_verified in ISO form.",
            "she_journal": "Heading-aware windows of 350-500 tokens with ~15% token overlap.",
            "paper_abstract": "Exactly one chunk per abstract, even if outside the 350-500 window. abstract_only=true, trust_tier=4.",
            "farmdeck_product": "Heading-aware windows of 350-500 tokens, no overlap. trust_tier=5.",
            "trust_tiers": {
                "1": "expert Q&A",
                "2": "peer-reviewed full source (none in this corpus; abstracts are tier 4)",
                "3": "SHE Journal article",
                "4": "abstract-only paper",
                "5": "FarmDeck product/commercial",
                "program_page": "SHE funding program page",
            },
            "embed_text": "Contextual header (title + section path, source type, state/coverage if known, as-of for funding) followed by the chunk text. Raw text is stored separately in 'text'.",
        },
        "stats_overall": overall,
        "stats_by_source": per_source,
    }
    write_json(KB_DIR / "index_manifest.json", manifest)
    write_json(LOG_DIR / "chunk_v2_log.json", manifest)
    print(json.dumps({"overall": overall, "by_source": per_source}, indent=2), flush=True)
    print(f"Wrote {out_path} and {KB_DIR / 'index_manifest.json'}", flush=True)
    return all_chunks


if __name__ == "__main__":
    build()
