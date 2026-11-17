"""End-to-end chat: route → at most two tools → generate → verify (one regen) → log."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from rag.paths import ROOT, SCRIPTS

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_answer import verify_answer  # noqa: E402

from chat.generate import generate_answer, refusal_message
from chat.quotes import answer_has_caveat, append_caveat_to_answer, detect_mismatches
from chat.router import RouteDecision, route
from chat.tools import find_funding, get_weather, search_evidence
from chat.traces import append_trace
from rag.retrieve import Retriever


def _verifier_feedback(report: dict[str, Any]) -> str:
    lines = []
    for c in report.get("checks") or []:
        if c.get("pass"):
            continue
        lines.append(f"- {c.get('name')}: {c.get('detail')}")
        extra = {k: v for k, v in c.items() if k not in {"name", "pass", "detail"}}
        if extra:
            lines.append(f"  {extra}")
    return "\n".join(lines) or "verification failed"


def _collected_chunks(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for t in tool_results:
        for ch in t.get("chunks") or []:
            cid = ch.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                out.append(ch)
    return out


@dataclass
class ChatContext:
    retriever: Retriever
    retrieval_variant: str = "hybrid"
    use_llm: bool = True
    demote_farmdeck: bool = True
    query_vec_cache: dict[str, Any] = field(default_factory=dict)
    # Optional map of question text -> {chunk_id: rerank_score} so eval can reuse the cache
    # and the server does not have to reload the cross-encoder for already-scored queries.
    rerank_cache_by_query: dict[str, dict[str, float]] = field(default_factory=dict)


def run_tools(ctx: ChatContext, question: str, decision: RouteDecision, query_vec=None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for label in decision.routes:
        if label == "refuse":
            continue
        if label == "evidence":
            results.append(
                search_evidence(
                    ctx.retriever,
                    question,
                    query_vec=query_vec,
                    variant=ctx.retrieval_variant,
                    demote_farmdeck=ctx.demote_farmdeck,
                    rerank_precomputed=ctx.rerank_cache_by_query.get(question),
                )
            )
        elif label == "funding":
            results.append(
                find_funding(
                    ctx.retriever,
                    question,
                    query_vec=query_vec,
                    variant=ctx.retrieval_variant,
                    rerank_precomputed=ctx.rerank_cache_by_query.get(question),
                )
            )
        elif label == "weather":
            try:
                results.append(get_weather(question))
            except Exception as e:
                results.append({"tool": "get_weather", "error": str(e)[:200], "hits": [], "chunks": []})
    return results


def answer_question(
    ctx: ChatContext,
    question: str,
    *,
    query_vec=None,
    log: bool = True,
    eval_id: str | None = None,
) -> dict[str, Any]:
    decision = route(question, use_llm=ctx.use_llm)
    tool_results: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    retrieved_ids: list[str] = []
    answers: list[dict[str, Any]] = []
    report: dict[str, Any] | None = None
    final = ""
    generator = "none"
    status = "ok"

    if decision.primary == "refuse" and not decision.extra:
        final = refusal_message(decision.refused_topic)
        generator = "refusal_template"
        report = {
            "pass": True,
            "checks": [{"name": "refusal", "pass": True, "detail": decision.refused_topic or "refused"}],
            "skipped": True,
        }
        status = "refused"
    else:
        tool_results = run_tools(ctx, question, decision, query_vec=query_vec)
        chunks = _collected_chunks(tool_results)
        retrieved_ids = [c["id"] for c in chunks]
        is_funding = decision.primary == "funding" or any(t.get("tool") == "find_funding" for t in tool_results)
        final, generator = generate_answer(question, decision, tool_results, use_llm=ctx.use_llm)
        report = verify_answer(final, chunks, is_funding=is_funding if is_funding else None)
        answers.append({"attempt": 1, "generator": generator, "answer": final, "verifier": report})
        if not report.get("pass"):
            feedback = _verifier_feedback(report)
            retry, gen2 = generate_answer(
                question, decision, tool_results, use_llm=ctx.use_llm, retry_feedback=feedback
            )
            report2 = verify_answer(retry, chunks, is_funding=is_funding if is_funding else None)
            answers.append({"attempt": 2, "generator": gen2, "answer": retry, "verifier": report2})
            if report2.get("pass"):
                final, generator, report = retry, gen2, report2
                status = "ok_after_retry"
            else:
                # Hedge: do not return an unverified answer. The issued text is the hedge;
                # keep failed drafts in attempts, but the verifier report on the record
                # describes what was actually returned (no invented numbers issued).
                hedge = refusal_message("verifier")
                issued = False
                if generator != "extractive":
                    extracted, _ = generate_answer(question, decision, tool_results, use_llm=False)
                    ext_report = verify_answer(extracted, chunks, is_funding=is_funding if is_funding else None)
                    answers.append({"attempt": 3, "generator": "extractive_hedge", "answer": extracted, "verifier": ext_report})
                    if ext_report.get("pass"):
                        final, generator, report = extracted, "extractive_hedge", ext_report
                        status = "hedged_extractive"
                        issued = True
                if not issued:
                    final, generator = hedge, "hedge"
                    status = "hedged"
                    report = {
                        "pass": True,
                        "skipped": True,
                        "checks": [
                            {
                                "name": "hedge",
                                "pass": True,
                                "detail": "returned a hedge after verifier failure; no factual claims issued",
                            }
                        ],
                        "prior_failure": report2,
                    }


    # Near-miss caveats on issued, verified answers (never on hedges/refusals).
    if status in {"ok", "ok_after_retry", "hedged_extractive"} and chunks:
        used = []
        cited = []
        try:
            from verify_answer import citations_in

            cited = citations_in(final)
        except Exception:
            cited = []
        if cited:
            byid = {c.get("id"): c for c in chunks if c.get("id")}
            used = [byid[i] for i in cited if i in byid]
        used = used or chunks[:4]
        flags = detect_mismatches(question, used)
        if any(c.get("abstract_only") for c in used) and "abstract_only" not in flags:
            flags.append("abstract_only")
        if flags and not answer_has_caveat(final):
            patched = append_caveat_to_answer(final, flags)
            if patched != final:
                is_funding = decision.primary == "funding" or any(t.get("tool") == "find_funding" for t in tool_results)
                patched_report = verify_answer(patched, chunks, is_funding=is_funding if is_funding else None)
                if patched_report.get("pass"):
                    final, report = patched, patched_report
        if flags and answer_has_caveat(final):
            if status == "ok_after_retry":
                status = "answer_with_caveat_after_retry"
            else:
                status = "answer_with_caveat"

    record = {
        "eval_id": eval_id,
        "query": question,
        "route": decision.as_dict(),
        "retrieved_ids": retrieved_ids,
        "tool_names": [t.get("tool") for t in tool_results],
        "answer": final,
        "generator": generator,
        "status": status,
        "verifier": report,
        "attempts": [
            {"attempt": a["attempt"], "generator": a["generator"], "verifier_pass": (a["verifier"] or {}).get("pass")}
            for a in answers
        ],
        "retrieval_variant": ctx.retrieval_variant,
    }
    if log:
        append_trace(record)
    record["chunks"] = chunks
    record["tool_results"] = tool_results
    record["decision"] = decision
    return record
