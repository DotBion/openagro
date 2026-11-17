"""Answer generation: extractive stub (always) and optional LLM, both citation-grounded."""

from __future__ import annotations

import re
from typing import Any

from chat.router import RouteDecision
from chat.quotes import (
    answer_has_caveat,
    detect_mismatches,
    extractive_quote_answer,
    format_quoted_sentence,
    pick_quote_sentences,
)

REFUSAL_TEXT = {
    "pesticide_rate": (
        "I can't give pesticide label rates, tank mixes, or product ounces-per-acre. "
        "Use the current product label and follow local regulations. "
        "If you want a meteorological weather window (wind, rain, temperature) that is not a spray recommendation, ask for a forecast."
    ),
    "livestock_dosing": (
        "I can't give livestock drug doses or withdrawal times. "
        "That has to come from the current veterinary label and a licensed veterinarian."
    ),
    "legal_tax": (
        "I can't give legal or tax advice, draft waivers, or tell you how to file a cost-share payment on a tax return. "
        "A qualified attorney or tax professional would need to answer that."
    ),
    "eligibility_determination": (
        "I can't tell you whether you qualify, are eligible, are enrolled, or are guaranteed a payment. "
        "I can only point at a program page's written criteria, an as-of date, and the official URL if you ask about the program itself."
    ),
    "farmx": (
        "I don't have FarmX field records, yields, operations, soil maps, or acreage in this local knowledge base, "
        "so I can't invent those numbers. Ask a general evidence, funding, or weather question if that is useful."
    ),
    "privacy": (
        "I can't provide logins, passwords, API credentials, or another person's private farm records."
    ),
    "out_of_scope": (
        "That question is outside what this agronomy assistant covers (soil-health evidence, public conservation programs, and weather)."
    ),
    "llm": (
        "I shouldn't answer that from this knowledge base. If it is about soil-health evidence, a public conservation program, or weather, try rephrasing."
    ),
    "verifier": (
        "I could not produce an answer that passed the citation and number checks, so I am not going to guess."
    ),
}


def refusal_message(topic: str | None) -> str:
    return REFUSAL_TEXT.get(topic or "", REFUSAL_TEXT["out_of_scope"])


def _year_from(chunk: dict) -> str:
    d = chunk.get("date") or ""
    m = re.search(r"(19|20)\d{2}", str(d))
    return m.group(0) if m else "year not stated"


def _csv(v) -> str:
    if not v:
        return "not stated"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if x) or "not stated"
    return str(v)


def validity_line(chunk: dict) -> str:
    region = chunk.get("region") or _csv(chunk.get("states"))
    crop = _csv(chunk.get("crop"))
    year = _year_from(chunk)
    bits = [f"region: {region or 'not stated'}", f"crop: {crop}", f"years: {year}"]
    if chunk.get("abstract_only"):
        bits.append("this figure is from the paper abstract only, not the full paper")
    return "Validity (" + "; ".join(bits) + ")"



def _one_line(text: str, limit: int = 500) -> str:
    """Flatten a snippet to a single sentence-like string (semicolons, not periods)."""
    text = " ".join((text or "").split())
    text = re.sub(r"\s*[.]\s+", "; ", text).strip(" ;.")
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def _cite_all(block: str, cid: str) -> str:
    """Ensure every sentence in a block carries [c:id]."""
    marker = f"[c:{cid}]"
    parts = re.split(r"(?<=[.!?])\s+", block.strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if marker not in p:
            p = p.rstrip() + (" " if p[-1] in ".!?" else ". ") + marker + ("." if p[-1] not in ".!?" else "")
        out.append(p)
    return " ".join(out)


def _first_sentences(text: str, n: int = 2, limit: int = 600) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # Skip leading markdown headings / blanks so we quote prose, not the title line.
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    text = " ".join(lines).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(parts[:n]).strip()
    if len(out) > limit:
        out = out[: limit - 1].rsplit(" ", 1)[0] + "…"
    return out


def extractive_answer(question: str, decision: RouteDecision, tool_results: list[dict[str, Any]], *, retry_feedback: str | None = None) -> str:
    if decision.primary == "refuse":
        return refusal_message(decision.refused_topic)

    parts: list[str] = []
    by_tool = {t.get("tool"): t for t in tool_results}
    # On retry, skip sentences that likely caused the previous failure by
    # picking the next-best supported quotes (simple: skip first-choice text
    # if the feedback mentions them is too brittle; just request extras).
    skip: set[str] = set()
    if retry_feedback:
        # Nudge the picker off the previous first sentences
        skip.add((retry_feedback or "")[:40])

    if "search_evidence" in by_tool or (decision.primary == "evidence" and "find_funding" not in by_tool):
        ev = by_tool.get("search_evidence") or {}
        chunks = ev.get("chunks") or ev.get("hits") or []
        if not chunks:
            parts.append("I did not find a passage in this local library that I can ground this question on, so I won't invent an answer.")
        else:
            ans = extractive_quote_answer(question, chunks, is_funding=False, max_sentences=2, skip_texts=skip)
            if ans:
                parts.append(ans)
            else:
                parts.append("I did not find a short passage I can quote in support of this question, so I won't invent an answer.")

    if "find_funding" in by_tool or decision.primary == "funding":
        fun = by_tool.get("find_funding") or {}
        chunks = fun.get("chunks") or fun.get("hits") or []
        if not chunks:
            parts.append(
                "I did not find an open (non-expired) program page in this library that matches those filters, so I won't invent a deadline or payment rate."
            )
        else:
            ans = extractive_quote_answer(question, chunks, is_funding=True, max_sentences=2, skip_texts=skip)
            if ans:
                parts.append(ans)
            else:
                parts.append(
                    "I did not find a short passage I can quote from an open program page, so I won't invent a deadline or payment rate."
                )

    if "get_weather" in by_tool or decision.primary == "weather":
        wx = by_tool.get("get_weather") or {}
        if wx.get("error"):
            parts.append(f"The weather service could not be reached ({wx.get('error')}).")
        elif wx:
            place = (wx.get("place") or {}).get("name") or "the requested point"
            issued = wx.get("issued_at") or ""
            src = wx.get("source_url") or "Open-Meteo"
            lines = [f"Open-Meteo forecast for {place} (issued {issued} America/New_York; source {src})."]
            for d in (wx.get("daily") or [])[:4]:
                lines.append(
                    f"{d.get('date')}: max {d.get('temperature_2m_max')} °C, min {d.get('temperature_2m_min')} °C, "
                    f"precip {d.get('precipitation_sum')} mm, wind max {d.get('wind_speed_10m_max')} km/h."
                )
            windows = wx.get("spray_weather_windows_next_72h") or []
            rule = wx.get("spray_window_rule") or {}
            if windows:
                lines.append(
                    "Hours in the next 72 that meet a simple meteorological window "
                    f"(wind {rule.get('wind_kmh')} km/h, temp {rule.get('temp_c')} °C, "
                    f"RH {rule.get('rh_pct')} %, no precip): "
                    + ", ".join(windows[:12])
                    + (" …" if len(windows) > 12 else "")
                    + "."
                )
            else:
                lines.append("No hour in the next 72 meets the simple meteorological weather window stored in this tool.")
            lines.append((rule.get("disclaimer") or "").strip())
            parts.append(" ".join(x for x in lines if x))

    return "\n\n".join(parts).strip() or refusal_message("out_of_scope")



_SYSTEM = """You are OpenAgronomy, a local agronomy assistant. You may use ONLY the source blocks provided.

Write a short answer of 2–4 sentences. Each sentence must be a verbatim quote or a close paraphrase of ONE cited source, with a citation marker [c:<chunk_id>] at the end of that sentence, BEFORE the period. Copy the chunk id exactly from the SOURCE id=... line, including the ::cN suffix. Example: ... make the soil hard [c:SHE-FA-2026-0037::c1]. Do not cite a parent document id without ::cN. Use only ids that appear in the sources. Do not dump long passages or copy whole sections.

Rules:
- Never invent numbers, dates, rates, yields, payment amounts, or study results. If a figure is not in the cited source block, omit it.
- Never say "you qualify", "you are eligible", "you are enrolled", or "you are guaranteed a payment".
- If the source crop, region, or season differs from the question, or the source is abstract_only, put an explicit caveat in the SAME cited sentence, using wording such as "may not apply to a different crop or region" and/or "abstract only". Do not add numbers in the caveat.
- Do not write a separate uncited validity sentence. If you mention region, crop, or year, those words/figures must appear in the cited source block.
- For funding, include the as-of / last-verified date and a source URL in a cited sentence. Quote criteria; do not determine eligibility.
- Weather numbers come from the weather tool result; do not invent a forecast. A spray window is meteorological only, never a pesticide rate.
- If the sources are not enough, say so and stop. Do not pad with general knowledge.
"""


def _pack_sources(tool_results: list[dict[str, Any]], question: str = "") -> str:
    blocks = []
    all_chunks = []
    for tr in tool_results:
        name = tr.get("tool")
        if name == "get_weather":
            blocks.append("WEATHER_TOOL_RESULT:\n" + _trim_json(tr))
            continue
        for h in tr.get("hits") or []:
            all_chunks.append(h)
            header = (
                f"SOURCE id={h.get('id')}  (cite exactly as [c:{h.get('id')}]) source={h.get('source')} title={h.get('title')!r} "
                f"region={h.get('region')!r} states={h.get('states')!r} crop={h.get('crop')!r} "
                f"date={h.get('date')!r} abstract_only={h.get('abstract_only')} "
                f"as_of={h.get('as_of')!r} url={h.get('url')!r} deadline={h.get('deadline')!r}"
            )
            quotes = pick_quote_sentences([h], question, max_sentences=2) if question else []
            quote_txt = ""
            if quotes:
                quote_txt = "\nCANDIDATE QUOTES (prefer verbatim or a close paraphrase of these):\n" + "\n".join(
                    f"- {s}" for s, _, _ in quotes
                )
            blocks.append(header + quote_txt + "\n" + (h.get("text") or "")[:1600])
    if question and all_chunks:
        flags = detect_mismatches(question, all_chunks[:8])
        if flags:
            blocks.insert(0, "NEAR-MISS FLAGS (must caveat in a cited sentence if you answer): " + ", ".join(flags))
    return "\n\n---\n\n".join(blocks) or "(no sources)"


def _trim_json(obj: Any, limit: int = 2500) -> str:
    import json

    s = json.dumps(obj, ensure_ascii=False, default=str, indent=2)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def llm_answer(
    question: str,
    decision: RouteDecision,
    tool_results: list[dict[str, Any]],
    *,
    retry_feedback: str | None = None,
) -> str:
    from chat.llm import complete

    user = f"Question: {question}\nRoute: {decision.primary}" + (f" + {decision.extra}" if decision.extra else "")
    user += "\n\nSOURCES:\n" + _pack_sources(tool_results, question)
    if retry_feedback:
        user += (
            "\n\nThe previous draft failed automatic verification. "
            "Rewrite using only the sources. Fix these problems:\n"
            + retry_feedback
            + "\nIf you cannot fix them without inventing facts, say you cannot verify the answer and stop."
        )
    text = complete(
        "generator",
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=500,
        timeout=90,
    )
    ids: list[str] = []
    for tr in tool_results:
        for key in ("hits", "chunks"):
            for h in tr.get(key) or []:
                if h.get("id"):
                    ids.append(h["id"])
    from chat.quotes import normalize_llm_citations

    return normalize_llm_citations((text or "").strip(), ids)


def generate_answer(
    question: str,
    decision: RouteDecision,
    tool_results: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    retry_feedback: str | None = None,
) -> tuple[str, str]:
    """Return (answer, generator_name)."""
    if decision.primary == "refuse" and not decision.extra:
        return refusal_message(decision.refused_topic), "refusal_template"
    if use_llm:
        try:
            from chat.llm import discover

            if discover().generator:
                return llm_answer(question, decision, tool_results, retry_feedback=retry_feedback), "llm"
        except Exception as e:
            # Fall through to extractive. Record the failure without secrets.
            try:
                from datetime import datetime
                from rag.paths import RESULTS_DIR
                import json as _json
                rec = {
                    "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "error_type": type(e).__name__,
                    "error": str(e)[:300],
                    "question": (question or "")[:180],
                }
                path = RESULTS_DIR / "llm_generate_errors.jsonl"
                with path.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass
    return extractive_answer(question, decision, tool_results, retry_feedback=retry_feedback), "extractive"
