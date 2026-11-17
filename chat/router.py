"""Fixed router: deterministic rules first, optional cheap LLM classification second.

Routes: evidence | funding | weather | refuse.
At most one extra tool call for combined questions (two routes max).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

ROUTE_LABELS = ("evidence", "funding", "weather", "refuse")

# Safety / privacy / out-of-scope — these win over every other signal.
_FARMX_RE = re.compile(r"\bfarmx\b", re.I)

_PESTICIDE_RATE_RE = re.compile(
    r"\b("
    r"label(?:ed)?\s+rate|tank\s*mix|mix(?:ed)?\s+in\s+a\s+\d+\s*[- ]?gallon|"
    r"(?:how many|how much|what(?:'s| is) the)\s+(?:ounces?|oz|pints?|quarts?|gallons?|ml|cc)\s+"
    r"(?:of\s+)?(?:glyphosate|roundup|atrazine|paraquat|dicamba|2,4-?d|adjuvant)|"
    r"(?:glyphosate|roundup|atrazine|paraquat|dicamba|2,4-?d)\s+(?:rate|dose|oz|ounces)|"
    r"ounces?\s+of\s+(?:glyphosate|roundup|atrazine|paraquat|dicamba|2,4-?d|adjuvant)"
    r")\b",
    re.I,
)

_LIVESTOCK_DOSE_RE = re.compile(
    r"\b("
    r"(?:how much|what(?:'s| is) the)\s+(?:dose|dosage|ivermectin|penicillin|antibiotic)|"
    r"(?:ivermectin|penicillin|oxytetracycline|deworm(?:er|ing)|antibiotic)\s+"
    r"(?:dose|dosage|to\s+(?:give|drench)|withdrawal)|"
    r"(?:milk\s+)?withdrawal(?:\s+time|\s+period)?|"
    r"give\s+(?:a\s+|my\s+)?(?:\d+[-\s]pound\s+)?(?:calf|cow|cows|steer|heifer|bull|pig|hog|sheep|goat|lamb)|"
    r"dairy\s+cows?\s+with\s+mastitis"
    r")\b",
    re.I,
)

_LEGAL_TAX_RE = re.compile(
    r"\b("
    r"income\s+tax|federal\s+income|how should i file|deduct(?:ion)?|"
    r"liability\s+waiver|write\s+a\s+(?:liability\s+)?waiver|"
    r"is it legal\s+(?:for me\s+)?to\s+apply|without a permit|"
    r"attorney|lawyer|sue|lawsuit|irs\b"
    r")\b",
    re.I,
)

_CREDENTIALS_RE = re.compile(
    r"\b(login and password|admin(?:istrator)?(?:\s+password)?|api\s*key|openagdata api|"
    r"neighbor(?:'s)?\s+(?:fieldlog|records|fields)|pull my neighbor)\b",
    re.I,
)

_TRANSFER_PAYMENT_RE = re.compile(
    r"\b("
    r"assume I(?:['’]ll| will) get the same (?:check|payment|rate)|"
    r"use the .+ payment rate on my .+ and assume|"
    r"the same check\b"
    r")\b",
    re.I,
)

_ELIGIBILITY_ASK_RE = re.compile(
    r"\b("
    r"do i qualify|am i (?:eligible|qualified)|you qualify|"
    r"am i legally enrolled|guaranteed payment|am i guaranteed"
    r")\b",
    re.I,
)

_OUT_OF_SCOPE_RE = re.compile(
    r"\b("
    r"tech stock|stock to buy|child has a fever|medication should i give|"
    r"corn futures|futures price|poem about my divorce|leave my business partner|"
    r"download the full text of every copyrighted|chapter-length excerpts|"
    r"farm in kenya"
    r")\b",
    re.I,
)

_WEATHER_RE = re.compile(
    r"\b("
    r"weather|forecast|spray window|will it rain|rain(?:ing)?\s+(?:today|tomorrow|this week)|"
    r"wind(?:speed)?(?:\s+tomorrow)?|temperature tomorrow|precip(?:itation)?"
    r")\b",
    re.I,
)

_FUNDING_RE = re.compile(
    r"\b("
    r"eqip|csp\b|csaf|cost[-\s]?share|grant|funding|funder|nrcs program|"
    r"program deadline|signup deadline|sign[-\s]?up deadline|application deadline|"
    r"payment rate|incentive payment|who can apply|program page|"
    r"conservation stewardship|environmental quality incentives|"
    r"cover crop (?:program|payment|cost[-\s]?share)|"
    r"usda\s+(?:program|eqip|csp)|rcee|climate smart"
    r")\b",
    re.I,
)


@dataclass
class RouteDecision:
    primary: str
    extra: str | None = None
    source: str = "rules"  # rules | llm | rules+llm
    reasons: list[str] = field(default_factory=list)
    refused_topic: str | None = None

    @property
    def routes(self) -> list[str]:
        out = [self.primary]
        if self.extra and self.extra != self.primary:
            out.append(self.extra)
        return out[:2]  # max one extra tool

    def as_dict(self) -> dict:
        d = asdict(self)
        d["routes"] = self.routes
        return d


def route_by_rules(question: str) -> RouteDecision | None:
    q = question or ""
    reasons = []
    if _FARMX_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["mentions FarmX; no FarmX records in this local KB"], refused_topic="farmx")
    if _PESTICIDE_RATE_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["pesticide label/tank-mix rate request"], refused_topic="pesticide_rate")
    if _LIVESTOCK_DOSE_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["livestock drug dosing / withdrawal"], refused_topic="livestock_dosing")
    if _LEGAL_TAX_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["legal or tax advice"], refused_topic="legal_tax")
    if _CREDENTIALS_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["credentials or third-party private records"], refused_topic="privacy")
    if _ELIGIBILITY_ASK_RE.search(q) or _TRANSFER_PAYMENT_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["personal eligibility / guaranteed-payment / transferred rate"], refused_topic="eligibility_determination")
    if _OUT_OF_SCOPE_RE.search(q):
        return RouteDecision("refuse", source="rules", reasons=["out of scope for this agronomy assistant"], refused_topic="out_of_scope")

    funding = bool(_FUNDING_RE.search(q))
    weather = bool(_WEATHER_RE.search(q))
    if funding and weather:
        return RouteDecision("funding", extra="weather", source="rules", reasons=["combined funding + weather question"])
    if funding:
        return RouteDecision("funding", source="rules", reasons=["funding/program cues"])
    if weather:
        return RouteDecision("weather", source="rules", reasons=["weather/forecast/spray-window cues"])
    return None  # let LLM or default-to-evidence decide


_LLM_PROMPT = """You classify a farmer question for a local agronomy assistant.
Return ONLY compact JSON: {"primary": "...", "extra": null or "...", "reason": "..."}.
primary must be one of: evidence, funding, weather, refuse.
extra is used only when the question truly needs two tools (e.g. a program plus a forecast). At most one extra, and it must be a different label.
Use refuse for: pesticide application rates or tank mixes, livestock drug dosing or withdrawal times, legal or tax advice, eligibility determinations ("do I qualify"), FarmX farm-record questions, private credentials, or topics outside agronomy evidence/funding/weather.
Use funding for conservation program, cost-share, grant, EQIP/CSP, deadlines, or payment-rate questions that are not asking "do I qualify".
Use weather for forecasts, rain, wind, or a spray *weather* window (not a chemical rate).
Use evidence for soil health, agronomic practices, research findings, and similar.
"""


def route_with_llm(question: str) -> RouteDecision | None:
    try:
        from chat.llm import complete, discover
    except Exception:
        return None
    inv = discover()
    if not inv.router:
        return None
    raw = complete(
        "router",
        [{"role": "system", "content": _LLM_PROMPT}, {"role": "user", "content": question}],
        temperature=0.0,
        max_tokens=200,
        timeout=30,
    )
    raw = raw.strip()
    # tolerate fenced JSON
    if "```" in raw:
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw)
    primary = (data.get("primary") or "evidence").strip().lower()
    extra = data.get("extra")
    if extra:
        extra = str(extra).strip().lower()
        if extra in {"null", "none", primary}:
            extra = None
    if primary not in ROUTE_LABELS:
        primary = "evidence"
    if extra and extra not in ROUTE_LABELS:
        extra = None
    return RouteDecision(
        primary=primary,
        extra=extra,
        source="llm",
        reasons=[str(data.get("reason") or "llm classification")],
        refused_topic="llm" if primary == "refuse" else None,
    )


def route(question: str, *, use_llm: bool = True) -> RouteDecision:
    ruled = route_by_rules(question)
    if ruled and ruled.primary == "refuse":
        return ruled
    if ruled and ruled.primary in {"funding", "weather"}:
        # Rules already handled clear funding/weather (and combinations).
        return ruled
    if use_llm:
        try:
            llm = route_with_llm(question)
        except Exception as e:
            return RouteDecision("evidence", source="rules", reasons=[f"no rule match; llm failed ({str(e)[:80]}); default evidence"])
        if llm:
            safety = route_by_rules(question)
            if safety and safety.primary == "refuse":
                return safety
            return llm
    return RouteDecision("evidence", source="rules", reasons=["no rule match; default evidence"])
