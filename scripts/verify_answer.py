"""Verify a cited RAG answer against retrieved chunks.

Input: answer text with citation markers like [c:<chunk_id>], plus the retrieved
chunks (list of dicts with at least id and text). Funding answers and
abstract-only chunks have extra checks.

Usage:
  python scripts/verify_answer.py --answer-file ans.txt --chunks-file retrieved.json
  python scripts/verify_answer.py  # reads JSON from stdin: {answer, chunks, is_funding?}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from units import canon_unit, equivalent_values, infer_crop, nearly_equal  # noqa: E402

CITATION_RE = re.compile(r"\[c:([^\]]+)\]")
ELIGIBILITY_RE = re.compile(
    r"\b("
    r"you\s+(?:do\s+not\s+|don't\s+|do\s+not\s+)?(?:qualify|are\s+(?:not\s+)?eligible)|"
    r"you're\s+(?:not\s+)?eligible|"
    r"you\s+are\s+(?:not\s+)?eligible|"
    r"this\s+means\s+you\s+qualify|"
    r"you\s+will\s+qualify|"
    r"you'?re\s+guaranteed\s+(?:enrollment|payment|funding)"
    r")\b",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\]\)>]+", re.I)
# ISO or "Nov 14, 2026" / "November 14, 2026"
AS_OF_RE = re.compile(
    r"\b(?:as\s+of|last\s+verified|verified)\b[^.]{0,40}?"
    r"(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}"
    r")",
    re.I,
)
DATE_BARE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
NUMBER_RE = re.compile(
    r"""
    (?P<money>\$\s?-?\s?(?P<money_val>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?))
    |
    (?P<pct>(?P<pct_val>-?\d+(?:\.\d+)?)\s*%)
    |
    (?P<date>(?:(?:""" + MONTHS + r""")\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})|\d{4}-\d{2}-\d{2})
    |
    (?P<range>(?P<r1>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)
              \s*(?:–|—|-|to)\s*
              (?P<r2>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)
              (?P<runit>\s*%|\s*\$)?)
    |
    (?P<plain>(?P<p_val>-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+))
    """,
    re.I | re.VERBOSE,
)

UNIT_RE = re.compile(
    r"""^\s*(
        %|percent|percentage|pct|
        ppm|
        bu/?ac(?:re)?|bu\s+per\s+acre|bushels?\s+per\s+acre|bushels?/acre|
        lb/?ac(?:re)?|lbs/?ac(?:re)?|lb\s+per\s+acre|pounds?\s+per\s+acre|
        lb\s*n\s*/?\s*ac(?:re)?|lb\s*n\s+per\s+acre|
        kg/?ha|kg\s*ha-1|kg\s*per\s*hectare|kg\s*n\s*ha-1|kg\s*n/?ha|
        \$\s*/\s*ac(?:re)?|dollars?\s+per\s+acre|
        tons?\s*/\s*ac(?:re)?|tons?\s+per\s+acre
    )\b""",
    re.I | re.VERBOSE,
)

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from",
    "by", "at", "as", "is", "are", "was", "were", "be", "been", "being", "that",
    "this", "it", "its", "than", "then", "but", "if", "not", "no", "so", "do",
    "does", "did", "can", "could", "should", "would", "may", "might", "into",
    "about", "over", "after", "before", "between", "per", "vs", "via",
}

EFFECT_SIZE_RE = re.compile(
    r"""
    (?:
        (?:reduced|increased|cut|raised|lowered|lost|gained|declined|improved)
        \s+(?:\w+\s+){0,6}?
        (?:by\s+)?
        \$?\s*-?\d+(?:\.\d+)?\s*(?:%|percent|bu/ac|bu\s+per\s+acre|lb/ac|kg/ha)?
    )
    |
    (?:
        (?:yield\s+(?:loss|gain|reduction|increase|drag)|nitrate\s+(?:loss|leaching|reduction))
        \s+(?:of\s+)?
        \$?\s*-?\d+(?:\.\d+)?\s*(?:%|percent|bu/ac|lb/ac|kg/ha)?
    )
    |
    (?:-?\d+(?:\.\d+)?\s*%\s+(?:lower|higher|less|more|loss|gain|reduction|increase))
    """,
    re.I | re.VERBOSE,
)

DEFAULT_SUPPORT_THRESHOLD = 0.30


@dataclass
class NumberSpan:
    raw: str
    start: int
    end: int
    values: list[float]
    unit: str | None
    kind: str  # money, percent, date, range, plain
    context: str


@dataclass
class Claim:
    sentence: str
    start: int
    end: int
    citations: list[str]
    numbers: list[NumberSpan]
    is_factual: bool


@dataclass
class CheckResult:
    name: str
    pass_: bool
    detail: str
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {"name": self.name, "pass": self.pass_, "detail": self.detail}
        if self.extra:
            d.update(self.extra)
        return d


def parse_number_token(raw: str) -> float:
    raw = raw.replace(",", "").replace("−", "-").replace("—", "-").replace("–", "-")
    raw = raw.replace("$", "").replace("%", "").strip()
    return float(raw)


def extract_numbers(text: str) -> list[NumberSpan]:
    found: list[NumberSpan] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(a, b, c, d) -> bool:
        return a < d and c < b

    for m in NUMBER_RE.finditer(text):
        start, end = m.start(), m.end()
        if any(overlaps(start, end, s, e) for s, e in occupied):
            continue
        ctx_start = max(0, start - 40)
        ctx_end = min(len(text), end + 40)
        context = text[ctx_start:ctx_end]
        kind = "plain"
        values: list[float] = []
        unit = None
        if m.group("money"):
            kind = "money"
            values = [parse_number_token(m.group("money_val"))]
            # $/ac?
            tail = text[end:end + 20]
            um = UNIT_RE.match(tail)
            unit = "$/ac" if um and "ac" in um.group(1).lower() else "$"
            if um:
                end = end + um.end()
        elif m.group("pct"):
            kind = "percent"
            values = [parse_number_token(m.group("pct_val"))]
            unit = "%"
        elif m.group("date"):
            kind = "date"
            values = []
            unit = "date"
        elif m.group("range"):
            kind = "range"
            values = [parse_number_token(m.group("r1")), parse_number_token(m.group("r2"))]
            runit = (m.group("runit") or "").strip()
            tail = text[end:end + 24]
            um = UNIT_RE.match(runit + tail) if runit else UNIT_RE.match(tail)
            if um:
                unit = um.group(1)
                if not runit:
                    end = end + um.end()
            elif runit == "%":
                unit = "%"
            elif runit == "$":
                unit = "$"
        else:
            kind = "plain"
            values = [parse_number_token(m.group("p_val"))]
            tail = text[end:end + 28]
            um = UNIT_RE.match(tail)
            if um:
                unit = um.group(1)
                end = end + um.end()
            # skip bare years used only as citations / identifiers if 19xx/20xx and no unit
            # Keep them as dates/years — they still must be grounded if presented as facts.
        # Skip isolated 1-2 digit list numbers? Keep them; tests can avoid them.
        found.append(
            NumberSpan(
                raw=text[start:end].strip(),
                start=start,
                end=end,
                values=values,
                unit=unit,
                kind=kind,
                context=context,
            )
        )
        occupied.append((start, end))
    return found


_ABBREV_RE = re.compile(
    r"\b(?:al|dr|mr|mrs|ms|prof|fig|vs|approx|inc|ltd|jr|sr|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.$",
    re.I,
)


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, sentence) spans."""
    spans = []
    start = 0
    # Split on .?! followed by space and a capital, but not after abbreviations
    pattern = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'(\[])")
    for m in pattern.finditer(text):
        # Don't split after "et al." / "U.S." / "Dr." and similar abbreviations
        prefix = text[max(0, m.start() - 12) : m.start()]
        if _ABBREV_RE.search(prefix) or prefix.endswith("U.S.") or prefix.endswith("e.g.") or prefix.endswith("i.e."):
            continue
        sent = text[start:m.start()].strip()
        if sent:
            # compute actual start
            s = text.find(sent, start)
            spans.append((s, s + len(sent), sent))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        s = text.find(tail, start)
        spans.append((s, s + len(tail), tail))
    return spans or ([(0, len(text), text.strip())] if text.strip() else [])


def is_factual_sentence(sentence: str, numbers: list[NumberSpan]) -> bool:
    cleaned = CITATION_RE.sub("", sentence).strip()
    if not cleaned or cleaned in {"", ".", "?"}:
        return False
    if cleaned.endswith("?") and not numbers:
        return False
    if re.fullmatch(r"(however|therefore|in summary|overall|see below|as follows)[:.]?", cleaned, re.I):
        return False
    # Meta / disclaimer lines that point at a source without adding a new farm fact
    if re.search(
        r"\b(confirm current terms|not a (?:decision|determination) that|"
        r"this is what the (?:program )?page states|always (?:re-)?check the official)\b",
        cleaned,
        re.I,
    ) and not numbers:
        return False
    if numbers:
        return True
    if re.search(
        r"\b(is|are|was|were|has|have|had|shows?|found|measured|reported|reduced|increased|"
        r"cut|raised?|lowered|equals?|means?|includes?|lists?|available|states?|says|"
        r"replace[sd]?|work[s]?|pay[s]?)\b",
        cleaned,
        re.I,
    ):
        return True
    # Long declaratives without a light verb still count as claims
    content = [w for w in tokenize(cleaned) if w not in STOPWORDS]
    if len(content) >= 6 and not cleaned.endswith("?"):
        return True
    return False


def citations_in(text: str) -> list[str]:
    return [m.group(1).strip() for m in CITATION_RE.finditer(text)]


def mask_citations(text: str) -> str:
    """Replace [c:...] with same-length spaces so offsets stay aligned and ids are not parsed as numbers."""
    return CITATION_RE.sub(lambda m: " " * len(m.group(0)), text)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def lexical_support(claim: str, chunk_text: str, threshold: float = DEFAULT_SUPPORT_THRESHOLD) -> tuple[bool, float, str]:
    """Return (ok, score, reason). Score is content-token recall, with n-gram bonus."""
    claim_clean = CITATION_RE.sub("", claim)
    ct = tokenize(claim_clean)
    xt = tokenize(chunk_text)
    if not ct:
        return True, 1.0, "no content tokens in claim"
    chunk_set = set(xt)
    overlap = [w for w in ct if w in chunk_set]
    recall = len(overlap) / len(ct)
    # 3-gram exact phrase
    def ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
        return {tuple(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1)}

    trigram_hit = False
    if len(ct) >= 3:
        trigram_hit = bool(ngrams(ct, 3) & ngrams(xt, 3))
        if trigram_hit:
            recall = max(recall, threshold)
    # Character-normalized numbers already handled separately
    ok = recall + (0.15 if trigram_hit else 0.0) >= threshold or trigram_hit
    reason = f"content-token recall={recall:.2f}, trigram_hit={trigram_hit}, threshold={threshold}"
    return ok, recall, reason


def number_in_text(span: NumberSpan, text: str) -> bool:
    """True if the number (or a converted equivalent) appears in text."""
    norm_text = text.replace(",", "").replace("−", "-").replace("—", "-").replace("–", "-")
    if span.kind == "date":
        # Compare normalized date strings
        raw = re.sub(r"\s+", " ", span.raw.replace(",", ", ")).strip()
        if raw.lower() in text.lower() or raw in text:
            return True
        # Try ISO vs Month Day, Year
        try:
            from dateutil import parser as dp
            d = dp.parse(span.raw, fuzzy=True).date()
            iso = d.isoformat()
            pretty = d.strftime("%B %-d, %Y") if sys.platform != "win32" else d.strftime("%B %d, %Y").replace(" 0", " ")
            pretty2 = d.strftime("%b %d, %Y").replace(" 0", " ")
            pretty3 = d.strftime("%b %-d, %Y") if sys.platform != "win32" else pretty2
            return any(p.lower() in text.lower() for p in (iso, pretty, pretty2, pretty3, d.strftime("%Y-%m-%d")))
        except Exception:
            return span.raw.lower() in text.lower()

    chunk_nums = extract_numbers(text)
    crop = infer_crop(span.context + " " + text[:400])
    candidates = []
    for v in span.values:
        candidates.extend(equivalent_values(v, span.unit, span.context + " " + (text[:200] if not infer_crop(span.context) else "")))

    # Also accept the raw digit sequence (with/without commas) when units match or both lack units
    raw_vals = [re.sub(r"[^\d.eE-]", "", str(v)) for v in span.values]
    for rv in raw_vals:
        if rv and re.search(r"(?<![\d.])" + re.escape(rv) + r"(?![\d.])", norm_text):
            # If the answer had a unit, prefer a unit-compatible mention, but a bare
            # identical number in a scientific text is accepted.
            if span.unit is None or span.kind in {"percent", "money", "range", "plain"}:
                return True

    for cn in chunk_nums:
        for cv in cn.values:
            for ev, eu in candidates:
                cu = canon_unit(cn.unit)
                # same family or convertible
                if eu is None or cu is None or eu == cu:
                    if nearly_equal(ev, cv):
                        return True
                else:
                    # try converting chunk number into the answer's family
                    from units import convert
                    conv = convert(cv, cn.unit, span.unit, crop or infer_crop(cn.context))
                    if conv is not None and nearly_equal(ev, conv):
                        return True
                    conv2 = convert(ev, eu, cn.unit, crop or infer_crop(span.context + " " + cn.context))
                    if conv2 is not None and nearly_equal(conv2, cv):
                        return True
    # percent: look for the numeral plus % in the normalized text
    if span.kind == "percent" or canon_unit(span.unit) == "percent":
        for v in span.values:
            if re.search(rf"(?<![\d.]){re.escape(_fmt_num(v))}\s*%", norm_text):
                return True
    return False


def _fmt_num(v: float) -> str:
    if float(v).is_integer():
        return str(int(v))
    return format(v, ".10g")


def chunk_by_id(chunks: Iterable[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in chunks if c.get("id")}


def verify_answer(
    answer: str,
    retrieved_chunks: list[dict],
    *,
    is_funding: bool | None = None,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
) -> dict[str, Any]:
    """Return a JSON-serializable report with overall pass/fail and per-check reasons."""
    retrieved = chunk_by_id(retrieved_chunks)
    retrieved_ids = set(retrieved)
    cited_ids = citations_in(answer)
    masked = mask_citations(answer)
    numbers = extract_numbers(masked)
    sentences = split_sentences(masked)
    claims: list[Claim] = []
    for start, end, sent in sentences:
        # Citations live in the original answer; the masked copy only clears them for number parsing.
        cites = citations_in(answer[start:end])
        trailing = citations_in(answer[end : end + 20])
        cites = list(dict.fromkeys(cites + trailing))
        # Restore a readable sentence (mask only used for offsets)
        sent = answer[start:end].strip() or sent.strip()
        nums = [n for n in numbers if n.start >= start and n.end <= end]
        claims.append(
            Claim(
                sentence=sent,
                start=start,
                end=end,
                citations=cites,
                numbers=nums,
                is_factual=is_factual_sentence(sent, nums),
            )
        )

    checks: list[CheckResult] = []

    # (a) every cited chunk id exists in the retrieved set
    missing = [cid for cid in cited_ids if cid not in retrieved_ids]
    checks.append(
        CheckResult(
            "cited_chunks_retrieved",
            not missing,
            "all cited chunk ids are in the retrieved set"
            if not missing
            else f"cited but not retrieved: {missing}",
            {"missing_ids": missing, "cited_ids": cited_ids, "retrieved_ids": sorted(retrieved_ids)},
        )
    )

    # (d) + (b) uncited numbers fail; cited numbers must appear in a cited chunk
    uncited_nums = []
    unsupported_nums = []
    supported_nums = []
    for n in numbers:
        # which claims / citations cover this number?
        covering = [c for c in claims if c.start <= n.start and n.end <= c.end]
        cites = []
        for c in covering:
            cites.extend(c.citations)
        # also any citation in a 60-char window after the number
        cites += citations_in(answer[n.end : n.end + 80])
        cites = list(dict.fromkeys(cites))
        if not cites:
            uncited_nums.append(n.raw)
            continue
        texts = []
        for cid in cites:
            ch = retrieved.get(cid)
            if ch:
                texts.append(ch.get("text") or "")
        if not texts:
            unsupported_nums.append({"raw": n.raw, "reason": "citation not in retrieved set"})
            continue
        if any(number_in_text(n, t) for t in texts):
            supported_nums.append(n.raw)
        else:
            unsupported_nums.append({"raw": n.raw, "reason": "number not found in cited chunk(s) after normalization", "cited": cites})

    checks.append(
        CheckResult(
            "uncited_numbers",
            not uncited_nums,
            "no uncited numbers" if not uncited_nums else f"uncited numbers: {uncited_nums}",
            {"uncited": uncited_nums},
        )
    )
    checks.append(
        CheckResult(
            "numbers_supported_by_cited_chunks",
            not unsupported_nums,
            "every cited number appears in a cited chunk (after unit/date normalization)"
            if not unsupported_nums
            else "numbers missing from cited chunks",
            {"unsupported": unsupported_nums, "supported": supported_nums},
        )
    )

    # (c) factual sentences need a citation; cited claims need lexical support
    missing_cite_sents = []
    cited_but_unsupported = []
    for c in claims:
        if not c.is_factual:
            continue
        if not c.citations:
            missing_cite_sents.append(c.sentence)
            continue
        ok_any = False
        reasons = []
        for cid in c.citations:
            ch = retrieved.get(cid)
            if not ch:
                reasons.append(f"{cid}: not retrieved")
                continue
            ok, score, reason = lexical_support(c.sentence, ch.get("text") or "", support_threshold)
            reasons.append(f"{cid}: {reason}")
            if ok:
                ok_any = True
        if not ok_any:
            cited_but_unsupported.append({"sentence": c.sentence, "citations": c.citations, "reasons": reasons})

    checks.append(
        CheckResult(
            "factual_sentences_cited",
            not missing_cite_sents,
            "every factual sentence has a citation"
            if not missing_cite_sents
            else f"{len(missing_cite_sents)} factual sentence(s) lack a citation",
            {"uncited_sentences": missing_cite_sents},
        )
    )
    checks.append(
        CheckResult(
            "cited_but_unsupported",
            not cited_but_unsupported,
            "cited claims have lexical support in the cited chunk"
            if not cited_but_unsupported
            else "one or more cited claims are not supported by the cited chunk text",
            {"flags": cited_but_unsupported},
        )
    )

    # (e) funding answers
    funding = is_funding
    if funding is None:
        # Infer if any cited/retrieved chunk is a funding program page
        funding = any(
            (retrieved.get(cid) or {}).get("source") == "she_funding"
            or (retrieved.get(cid) or {}).get("trust_tier") == "program_page"
            for cid in cited_ids
        )
    if funding:
        has_as_of = bool(AS_OF_RE.search(answer) or (
            DATE_BARE_RE.search(answer) and re.search(r"\b(as of|last verified|verified)\b", answer, re.I)
        ))
        has_url = bool(URL_RE.search(answer))
        eligibility_hits = [m.group(0) for m in ELIGIBILITY_RE.finditer(answer)]
        checks.append(
            CheckResult(
                "funding_as_of_and_url",
                has_as_of and has_url,
                "funding answer includes an as-of/last-verified date and a source URL"
                if (has_as_of and has_url)
                else f"funding answer missing {'as-of date' if not has_as_of else ''}{' and ' if (not has_as_of and not has_url) else ''}{'source URL' if not has_url else ''}",
                {"has_as_of": has_as_of, "has_url": has_url},
            )
        )
        checks.append(
            CheckResult(
                "funding_no_eligibility_assertion",
                not eligibility_hits,
                "no 'you qualify / are eligible' assertion"
                if not eligibility_hits
                else f"eligibility assertion(s) found: {eligibility_hits}",
                {"matches": eligibility_hits},
            )
        )
    else:
        checks.append(CheckResult("funding_as_of_and_url", True, "not a funding answer; check skipped"))
        checks.append(CheckResult("funding_no_eligibility_assertion", True, "not a funding answer; check skipped"))

    # (f) abstract-only citations must not carry effect sizes absent from the abstract
    abstract_violations = []
    for c in claims:
        abstract_cites = [
            cid
            for cid in c.citations
            if (retrieved.get(cid) or {}).get("abstract_only")
            or (retrieved.get(cid) or {}).get("trust_tier") == 4
        ]
        if not abstract_cites:
            continue
        effects = [m.group(0).strip() for m in EFFECT_SIZE_RE.finditer(CITATION_RE.sub("", c.sentence))]
        # Also treat standalone percents and bu/ac deltas in the sentence as effect sizes
        for n in c.numbers:
            if n.kind == "percent" or (n.unit and canon_unit(n.unit) in {"percent", "bu_ac", "lb_ac", "kg_ha"}):
                effects.append(n.raw)
        # unique
        seen_e = set()
        uniq_e = []
        for e in effects:
            k = e.lower()
            if k not in seen_e:
                seen_e.add(k)
                uniq_e.append(e)
        for cid in abstract_cites:
            text = (retrieved.get(cid) or {}).get("text") or ""
            for e in uniq_e:
                # parse as number span in a tiny string
                spans = extract_numbers(e)
                if spans:
                    if not any(number_in_text(s, text) for s in spans):
                        abstract_violations.append(
                            {"sentence": c.sentence, "effect": e, "chunk_id": cid, "reason": "effect size not present in the cited abstract"}
                        )
                else:
                    if e.lower() not in text.lower():
                        abstract_violations.append(
                            {"sentence": c.sentence, "effect": e, "chunk_id": cid, "reason": "effect size wording not present in the cited abstract"}
                        )
    checks.append(
        CheckResult(
            "abstract_only_effect_sizes",
            not abstract_violations,
            "no effect sizes cited from an abstract that does not contain them"
            if not abstract_violations
            else "effect size(s) attached to an abstract-only citation are not in the abstract",
            {"violations": abstract_violations},
        )
    )

    overall = all(c.pass_ for c in checks)
    report = {
        "pass": overall,
        "checks": [c.as_dict() for c in checks],
        "n_claims": len(claims),
        "n_factual_claims": sum(1 for c in claims if c.is_factual),
        "cited_ids": cited_ids,
        "retrieved_ids": sorted(retrieved_ids),
        "is_funding": bool(funding),
        "support_threshold": support_threshold,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify a cited RAG answer against retrieved chunks")
    p.add_argument("--answer", help="Answer text (or use --answer-file / stdin JSON)")
    p.add_argument("--answer-file")
    p.add_argument("--chunks-file", help="JSON list of retrieved chunk objects (or {answer, chunks})")
    p.add_argument("--is-funding", action="store_true")
    p.add_argument("--support-threshold", type=float, default=DEFAULT_SUPPORT_THRESHOLD)
    p.add_argument("--out")
    args = p.parse_args(argv)

    answer = args.answer
    chunks: list[dict] = []
    is_funding = True if args.is_funding else None
    if args.answer_file:
        answer = Path(args.answer_file).read_text(encoding="utf-8")
    if args.chunks_file:
        blob = json.loads(Path(args.chunks_file).read_text(encoding="utf-8"))
        if isinstance(blob, dict) and "chunks" in blob:
            chunks = blob["chunks"]
            answer = answer or blob.get("answer")
            if blob.get("is_funding") is not None:
                is_funding = blob["is_funding"]
        else:
            chunks = blob
    if answer is None and not sys.stdin.isatty():
        blob = json.load(sys.stdin)
        answer = blob["answer"]
        chunks = blob["chunks"]
        if blob.get("is_funding") is not None:
            is_funding = blob["is_funding"]
    if not answer:
        p.error("No answer text provided")
    report = verify_answer(answer, chunks, is_funding=is_funding, support_threshold=args.support_threshold)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
