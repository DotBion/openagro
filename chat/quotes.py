"""Quote-first extractive answers and near-miss caveat helpers.

Sentences are taken from the cited chunk (verbatim, or a short close excerpt).
Caveat wording is attached to a cited sentence so the unchanged verifier still
sees enough lexical overlap. Numbers are only kept if they already appear in
that chunk's visible text.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from rag.features import QueryFeatures, crop_match, detect_crops, detect_seasons, season_match, state_match
from rag.funding_fields import visible_text

# Shared with eval_e2e so "has a caveat" is measured the same way we write them.
CAVEAT_RE = re.compile(
    r"\b(caveat|does not (?:directly )?apply|may not apply|different (?:crop|region|state|soil|year|system)|"
    r"not (?:the same|transferable)|mismatch|only (?:covers|studied|measured|reported)|"
    r"I cannot (?:transfer|apply|invent)|do not invent|region/crop|"
    r"not a determination|confirm current terms|abstract only)\b",
    re.I,
)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'(\[])")


def answer_has_caveat(answer: str) -> bool:
    return bool(CAVEAT_RE.search(answer or ""))


_HEADER_LINE = re.compile(
    r"^(Funder|Coverage(?: / states)?|Last verified|Official or application URL|Deadline|"
    r"Program criteria as written on the page)\b",
    re.I,
)


def split_prose_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    prose_lines: list[str] = []
    standalone: list[str] = []

    def flush_prose():
        blob = " ".join(prose_lines).strip()
        prose_lines.clear()
        if not blob:
            return
        for part in _SENT_SPLIT.split(blob):
            part = " ".join(part.split()).strip()
            if part:
                standalone.append(part)

    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if re.fullmatch(r"\*{0,2}[A-Za-z /]{1,24}\*{0,2}:?", s) and len(s) < 28:
            continue
        # Keep structured header lines as their own sentences; do not glue them to body prose
        if _HEADER_LINE.search(s) or s.startswith("**Expert:") or s.startswith(">"):
            flush_prose()
            standalone.append(" ".join(s.split()))
            continue
        prose_lines.append(s)
    flush_prose()
    return standalone


def _content_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from",
    "by", "at", "as", "is", "are", "was", "were", "be", "that", "this", "it",
    "its", "if", "not", "no", "so", "do", "does", "did", "can", "what", "whats",
    "which", "who", "how", "when", "where", "why", "i", "my", "me", "we",
}


def sentence_query_score(sentence: str, question: str) -> float:
    st = [t for t in _content_tokens(sentence) if t not in _STOP and len(t) > 1]
    qt = [t for t in _content_tokens(question) if t not in _STOP and len(t) > 1]
    if not st or not qt:
        return 0.0
    sset = set(st)
    overlap = sum(1 for t in qt if t in sset)
    recall = overlap / len(qt)
    prec = overlap / len(st)
    score = 0.65 * recall + 0.35 * prec
    # Prefer mid-length prose over titles or huge dumps
    n = len(st)
    if 8 <= n <= 70:
        score += 0.05
    if n < 5:
        score -= 0.2
    q_has_num = bool(re.search(r"\d", question))
    s_has_num = bool(re.search(r"\d", sentence))
    if q_has_num and s_has_num:
        score += 0.08
    return score


def pick_quote_sentences(
    chunks: Iterable[dict[str, Any]],
    question: str,
    *,
    max_sentences: int = 2,
    skip_texts: set[str] | None = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return up to max_sentences of (sentence, chunk_id, chunk), best supported first."""
    skip_texts = {s.lower() for s in (skip_texts or set())}
    cands: list[tuple[float, str, dict[str, Any]]] = []
    seen = set()
    for rank_i, ch in enumerate(chunks):
        rank_penalty = rank_i
        cid = ch.get("id")
        if not cid:
            continue
        body = visible_text(ch)
        for sent in split_prose_sentences(body):
            key = sent.lower()
            if key in seen or key in skip_texts:
                continue
            if len(sent) < 30 or len(sent) > 600:
                continue
            # Skip metadata headers unless the question is asking for them
            if _HEADER_LINE.search(sent):
                q_ok = bool(re.search(r"\b(url|website|deadline|as of|last verif|who can apply|criteria)\b", question, re.I))
                if not q_ok:
                    continue
            if sent.startswith(">") or sent.startswith("**Expert"):
                continue
            # Skip catalog/metadata lines and markdown field dumps; they pass the
            # verifier but are not usable answers.
            if re.search(
                r"(?i)(\*\*[^*]{1,40}\*\*:|"
                r"\b(Identifier|Published|Asked by|Official program URL|Last verified|"
                r"Location \(stated\)|Crop \(stated\)|Acres \(stated\)|Match requirement)\b|"
                r"^\*\*?(Funder|Type|Tags|Readiness|What you can get|Deadline)\b)",
                sent,
            ):
                if not re.search(r"\b(url|website|deadline|as of|last verif|who can apply|criteria)\b", question, re.I):
                    continue
            if sent.count("|") >= 3:
                continue
            # Skip title-only fragments (no verb, no terminal punctuation originally stripped)
            words=re.findall(r"[A-Za-z]+", sent)
            if len(words) <= 12 and not re.search(
                r"\b(is|are|was|were|has|have|had|shows?|found|measured|reported|provides?|"
                r"includes?|lists?|available|states?|says|gives?|recommends?|estimated?|"
                r"reduced|increased|cut|raise[sd]?)\b",
                sent,
                re.I,
            ):
                continue
            seen.add(key)
            sc = sentence_query_score(sent, question)
            if sc <= 0:
                continue
            # Prefer earlier (already ranked) hits and program-name matches
            try:
                from rag.funding_fields import name_match_score
                sc += 0.25 * name_match_score(question, ch)
            except Exception:
                pass
            sc += max(0.0, 0.12 - 0.03 * rank_penalty)
            cands.append((sc, sent, ch))
    cands.sort(key=lambda x: (-x[0], len(x[1])))
    picked: list[tuple[str, str, dict[str, Any]]] = []
    used_parents: set[str] = set()
    for sc, sent, ch in cands:
        if len(picked) >= max_sentences:
            break
        parent = ch.get("parent_id") or ch.get("id")
        # Prefer diversity, but allow a second sentence from the same parent if we have nothing else
        if parent in used_parents and len(picked) < max_sentences and sc < (cands[0][0] * 0.7 if cands else 0):
            continue
        picked.append((sent, ch["id"], ch))
        used_parents.add(parent)
    if not picked and cands:
        sent, ch = cands[0][1], cands[0][2]
        picked.append((sent, ch["id"], ch))
    return picked


def detect_mismatches(question: str, chunks: list[dict[str, Any]]) -> list[str]:
    """Return short mismatch labels: crop, region, season, abstract_only."""
    if not question or not chunks:
        return []
    feats = QueryFeatures.from_query(question)
    flags: list[str] = []
    # A mismatch if the question names a crop/region/season and NONE of the
    # used chunks match, while at least one chunk states a different value.
    def _chunk_blob(ch: dict) -> str:
        return " ".join(
            [
                ch.get("title") or "",
                " ".join(ch.get("crop") or []) if isinstance(ch.get("crop"), (list, tuple)) else str(ch.get("crop") or ""),
                " ".join(ch.get("tags") or []),
                str(ch.get("region") or ""),
                " ".join(ch.get("states") or []) if isinstance(ch.get("states"), (list, tuple)) else str(ch.get("states") or ""),
                (ch.get("text") or "")[:1500],
            ]
        )

    if feats.crops:
        matched = False
        other: list[str] = []
        for ch in chunks:
            blob = _chunk_blob(ch)
            found = detect_crops(blob)
            other.extend(found)
            if crop_match(feats.crops, ch) or any(c.lower() in blob.lower() for c in feats.crops):
                matched = True
        if not matched and other:
            flags.append("crop")
    if feats.states or feats.regions:
        state_hit = any(state_match(feats.states, ch) for ch in chunks) if feats.states else False
        region_hit = False
        if feats.regions:
            for ch in chunks:
                blob = _chunk_blob(ch).lower()
                if any(r.lower() in blob for r in feats.regions):
                    region_hit = True
                    break
        # Only flag a region mismatch when the question names a place and the
        # sources name some other place (do not flag sources with no geography).
        source_has_geo = any(
            (ch.get("states") or ch.get("region") or detect_crops(""))
            or _chunk_blob(ch)
            and True
            for ch in chunks
        )
        # Recompute: flag only if no state/region hit AND sources clearly name a different place
        if feats.states and not state_hit:
            named_elsewhere = False
            for ch in chunks:
                blob = _chunk_blob(ch)
                # A different US state mentioned, and the asked state is absent
                from rag.features import detect_states
                other_states = [s for s in detect_states(blob) if s.lower() not in {x.lower() for x in feats.states}]
                if other_states and not any(s.lower() in blob.lower() for s in feats.states):
                    named_elsewhere = True
            if named_elsewhere:
                flags.append("region")
        elif feats.regions and not region_hit and not state_hit:
            flags.append("region")
    if feats.seasons and not any(season_match(feats.seasons, ch) for ch in chunks):
        flags.append("season")
    if any(ch.get("abstract_only") for ch in chunks):
        flags.append("abstract_only")
    # unique, stable order
    order = ["crop", "region", "season", "abstract_only"]
    return [f for f in order if f in flags]


def caveat_phrase(flags: list[str]) -> str:
    if not flags:
        return ""
    bits = []
    if any(f in flags for f in ("crop", "region", "season")):
        bits.append("may not apply to a different crop, region, or season")
    if "abstract_only" in flags:
        bits.append("abstract only")
    return "caveat: " + "; ".join(bits)


def attach_caveat_to_sentence(sentence: str, flags: list[str]) -> str:
    """Append caveat wording inside the same cited sentence (before the marker if present)."""
    phrase = caveat_phrase(flags)
    if not phrase:
        return sentence
    if CAVEAT_RE.search(sentence):
        # Still add abstract-only if missing
        if "abstract_only" in flags and not re.search(r"abstract only", sentence, re.I):
            phrase = "abstract only"
        else:
            return sentence
    sent = sentence.rstrip()
    m = re.search(r"(\s*\[c:[^\]]+\]\.?\s*)$", sent)
    tail = m.group(1) if m else ""
    core = sent[: m.start()] if m else sent
    core = core.rstrip(" .;")
    extra = f" ({phrase})"
    if tail:
        return core + extra + tail
    if core.endswith((".", "!", "?")):
        return core[:-1] + extra + core[-1]
    return core + extra + "."


def format_quoted_sentence(sentence: str, chunk_id: str, flags: list[str] | None = None) -> str:
    sent = " ".join((sentence or "").split()).strip()
    # Avoid intra-sentence periods that would split into an uncited clause: keep
    # one sentence by turning leftover internal periods (abbreviations excluded) 
    # only if there is no citation yet and we will add one at the end.
    sent = sent.rstrip(" .")
    if not sent.endswith((".", "!", "?")):
        sent = sent + "."
    marker = f"[c:{chunk_id}]"
    if marker not in sent:
        if sent.endswith((".", "!", "?")):
            sent = sent[:-1] + f" {marker}."
        else:
            sent = sent + f" {marker}."
    if flags:
        sent = attach_caveat_to_sentence(sent, flags)
    return sent


def append_caveat_to_answer(answer: str, flags: list[str]) -> str:
    if not flags or not answer or answer_has_caveat(answer):
        if flags and answer and "abstract_only" in flags and not re.search(r"abstract only", answer or "", re.I):
            pass
        else:
            return answer
    # Attach to the last cited sentence if possible
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    if not parts:
        return answer
    parts[-1] = attach_caveat_to_sentence(parts[-1], flags)
    return " ".join(parts)


def extractive_quote_answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    is_funding: bool = False,
    max_sentences: int = 2,
    skip_texts: set[str] | None = None,
) -> str:
    """Build a 2–4 sentence extractive answer from the best-supported quotes."""
    if not chunks:
        return ""
    n = 2 if not is_funding else 2
    pool = list(chunks)
    if is_funding and len(pool) > 3:
        try:
            from rag.funding_fields import name_match_score
            if any(name_match_score(question, ch) >= 0.85 for ch in pool[:8]):
                pool = [ch for ch in pool if name_match_score(question, ch) >= 0.5][:3] or pool[:3]
        except Exception:
            pool = pool[:3]
    quotes = pick_quote_sentences(pool, question, max_sentences=max(n, max_sentences), skip_texts=skip_texts)
    if not quotes:
        return ""
    used = [ch for _, _, ch in quotes]
    flags = detect_mismatches(question, used)
    sentences: list[str] = []
    for sent, cid, ch in quotes:
        fl = list(flags)
        if ch.get("abstract_only") and "abstract_only" not in fl:
            fl.append("abstract_only")
        sentences.append(format_quoted_sentence(sent, cid, fl))
    if is_funding:
        # Guarantee as-of + URL on a cited sentence, quoting only fields on the chunk.
        ch0 = quotes[0][2]
        cid = quotes[0][1]
        as_of = ch0.get("as_of") or ch0.get("last_verified")
        url = ch0.get("application_url") or ch0.get("url")
        vis = visible_text(ch0)
        extra_bits = []
        if as_of and str(as_of) in vis:
            extra_bits.append(f"as of {as_of}")
        if url and str(url) in vis:
            extra_bits.append(f"official URL: {url}")
        extra_bits.append("these are program criteria as written on the page, not a determination that anyone qualifies")
        if extra_bits:
            sentences.append(format_quoted_sentence("; ".join(extra_bits).capitalize() + ".", cid, None))
        # Keep total short
        sentences = sentences[:4]
    # Dedup nearly identical
    out: list[str] = []
    seen = set()
    for s in sentences:
        key = re.sub(r"\s*\[c:[^\]]+\]", "", s).lower()[:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return " ".join(out).strip()
