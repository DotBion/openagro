"""Swappable LLM client. Discovers keys from the environment; never logs or returns secret values."""

from __future__ import annotations

import json
import re
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests

# OpenAI-compatible chat endpoints. Model ids are probed at runtime.
PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "openai",
        "env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "family": "openai",
        "router_candidates": ["gpt-4o-mini", "gpt-4.1-mini"],
        "generator_candidates": ["gpt-4o-mini", "gpt-4.1-mini"],
        "judge_candidates": ["gpt-4o-mini"],
    },
    {
        "name": "groq",
        "env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "family": "groq-llama",
        "router_candidates": ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        "generator_candidates": ["qwen/qwen3.8-27b"],
        "judge_candidates": ["qwen/qwen3.8-27b"],
    },
    {
        "name": "xai",
        "env": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "family": "xai-grok",
        "router_candidates": ["grok-4-fast-non-reasoning", "grok-3-mini", "grok-2-1212"],
        "generator_candidates": ["grok-4-fast-non-reasoning", "grok-3-mini", "grok-2-1212"],
        "judge_candidates": ["grok-4-fast-non-reasoning", "grok-3-mini"],
    },
    {
        "name": "openrouter",
        "env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "family": "openrouter",
        "router_candidates": ["openai/gpt-4o-mini", "meta-llama/llama-3.1-8b-instruct"],
        "generator_candidates": ["openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"],
        "judge_candidates": ["google/gemini-2.0-flash-001", "meta-llama/llama-3.3-70b-instruct"],
    },
    {
        "name": "gemini",
        "env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "family": "google-gemini",
        "router_candidates": ["gemini-2.5-flash", "gemini-3.8-flash"],
        "generator_candidates": ["gemini-2.5-flash"],
        "judge_candidates": ["gemini-2.5-flash", "gemini-3.8-flash"],
    },
]


@dataclass
class LLMSlot:
    role: str
    provider: str
    family: str
    model: str
    available: bool
    reason: str = ""


@dataclass
class LLMInventory:
    keys_present: list[str]
    keys_absent: list[str]
    router: LLMSlot | None
    generator: LLMSlot | None
    judge: LLMSlot | None
    stub_only: bool
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        def slot(s: LLMSlot | None):
            return asdict(s) if s else None

        return {
            "keys_present": self.keys_present,
            "keys_absent": self.keys_absent,
            "router": slot(self.router),
            "generator": slot(self.generator),
            "judge": slot(self.judge),
            "stub_only": self.stub_only,
            "notes": self.notes,
        }


_inventory: LLMInventory | None = None
_probed_ok: dict[tuple[str, str], bool] = {}


def _key(env_name: str) -> str | None:
    v = os.environ.get(env_name)
    if v and v.strip() and v.strip() not in {"0", "None", "null"}:
        return v.strip()
    return None


def present_key_names() -> tuple[list[str], list[str]]:
    present, absent = [], []
    seen = set()
    for p in PROVIDERS:
        n = p["env"]
        if n in seen:
            continue
        seen.add(n)
        (present if _key(n) else absent).append(n)
    # Also record ANTHROPIC even though we have no adapter if unset
    extra = "ANTHROPIC_API_KEY"
    if extra not in seen:
        (present if _key(extra) else absent).append(extra)
    return present, absent


def _openai_chat(base_url: str, api_key: str, model: str, messages: list[dict], *, timeout: int = 60, extra_headers: dict | None = None, temperature: float = 0.0, max_tokens: int = 800) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    if r.status_code >= 400:
        # Do not include request headers (they carry the key) in any raised message.
        try:
            err = r.json()
            msg = err.get("error", err)
            if isinstance(msg, dict):
                msg = msg.get("message") or msg.get("code") or "error"
        except Exception:
            msg = r.text[:200]
        ra = r.headers.get("Retry-After") or r.headers.get("retry-after")
        extra = f" Retry-After={ra}" if ra else ""
        raise RuntimeError(f"{r.status_code} from {base_url} model={model}: {msg}{extra}")
    data = r.json()
    return data["choices"][0]["message"]["content"] or ""


def _gemini_chat(api_key: str, model: str, messages: list[dict], *, timeout: int = 60, temperature: float = 0.0, max_tokens: int = 800) -> str:
    # Convert chat messages to a Gemini generateContent body. Key goes in the query string.
    sys_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body: dict[str, Any] = {
        "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max(int(max_tokens), 2048)},
    }
    # Gemini 2.5 "thinking" can consume the output budget and truncate the visible answer.
    body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    if sys_txt:
        body["systemInstruction"] = {"parts": [{"text": sys_txt}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = requests.post(url, params={"key": api_key}, json=body, timeout=timeout)
    if r.status_code >= 400:
        try:
            err = r.json()
            msg = (err.get("error") or {}).get("message") or "error"
        except Exception:
            msg = r.text[:200]
        ra = r.headers.get("Retry-After") or r.headers.get("retry-after")
        extra = f" Retry-After={ra}" if ra else ""
        raise RuntimeError(f"{r.status_code} from gemini model={model}: {msg}{extra}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini model={model} returned no candidates")
    parts = ((cands[0].get("content") or {}).get("parts") or [])
    return "".join(p.get("text") or "" for p in parts)


def _chat_with_provider(provider: dict, model: str, messages: list[dict], **kw) -> str:
    key = _key(provider["env"])
    if not key:
        raise RuntimeError(f"no key for {provider['name']}")
    if provider["name"] == "gemini":
        return _gemini_chat(key, model, messages, **kw)
    extra = None
    if provider["name"] == "openrouter":
        extra = {"HTTP-Referer": "http://localhost", "X-Title": "OpenAgronomy local"}
    return _openai_chat(provider["base_url"], key, model, messages, extra_headers=extra, **kw)


def _probe_model(provider: dict, model: str) -> tuple[bool, str]:
    cache_key = (provider["name"], model)
    if cache_key in _probed_ok:
        return _probed_ok[cache_key], "cached"
    try:
        text = _chat_with_provider(
            provider,
            model,
            [{"role": "user", "content": "Reply with the single word pong."}],
            timeout=40,
            max_tokens=8,
        )
        ok = bool(text and text.strip())
        if ok:
            _probed_ok[cache_key] = True
        return ok, "ok" if ok else "empty"
    except Exception as e:
        err = _sanitize_err(str(e))
        low = err.lower()
        transient = any(s in low for s in ("429", "503", "timeout", "high demand", "temporarily", "rate limit", "unavailable", "resource_exhausted"))
        if not transient:
            _probed_ok[cache_key] = False
        return False, err


def _sanitize_err(msg: str) -> str:
    """Strip API keys and provider key-prefix leaks from error text (never log secrets)."""
    msg = msg or ""
    msg = re.sub(r"sk-[A-Za-z0-9_\-]+", "[redacted]", msg)
    msg = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", msg)
    msg = re.sub(r"Incorrect API key provided:[^.]*(?:\.|$)", "Incorrect API key provided.", msg)
    msg = re.sub(r"api[_-]?key[=:]\s*\S+", "api_key=[redacted]", msg, flags=re.I)
    return msg[:200]


def _first_working(role: str, preferred: list[str] | None = None) -> LLMSlot | None:
    # `preferred` is tried in the given order, then any remaining providers.
    by_name = {p["name"]: p for p in PROVIDERS}
    if preferred:
        order = [by_name[n] for n in preferred if n in by_name]
        order += [p for p in PROVIDERS if p["name"] not in set(preferred)]
    else:
        order = list(PROVIDERS)
    role_key = f"{role}_candidates"
    for p in order:
        if not _key(p["env"]):
            continue
        for model in p[role_key]:
            ok, reason = _probe_model(p, model)
            if ok:
                return LLMSlot(role=role, provider=p["name"], family=p["family"], model=model, available=True, reason=reason)
    return None


def discover(force: bool = False) -> LLMInventory:
    global _inventory
    if _inventory is not None and not force:
        return _inventory
    if force:
        _probed_ok.clear()
    present, absent = present_key_names()
    notes = []
    if not present:
        notes.append("No LLM API keys in the environment. Using the extractive stub generator.")
    # Pin the generator to Gemini when that key is present. Do not fall through
    # to Groq: Groq is reserved for the router/judge and is easily 429'd, and a
    # cached Groq probe (from the router) would otherwise steal the generator slot.
    if _key("GEMINI_API_KEY"):
        gem = next(p for p in PROVIDERS if p["name"] == "gemini")
        model = "gemini-2.5-flash"
        ok, reason = _probe_model(gem, model)
        generator = LLMSlot(
            role="generator",
            provider="gemini",
            family="google-gemini",
            model=model,
            available=True,
            reason=reason if ok else f"pinned; probe={reason}",
        )
        if not ok:
            notes.append(f"Gemini generator pinned to {model} despite probe ({reason[:120]}).")
    else:
        generator = _first_working("generator", preferred=["openai", "gemini", "groq", "openrouter", "xai"])
    router = None
    # Prefer a cheap/fast router: groq 8b, else same as generator
    router = _first_working("router", preferred=["groq", "openai", "xai", "gemini", "openrouter"])
    judge = None
    gen_family = generator.family if generator else None
    # Judge must be a different family than the generator when both exist.
    # Prefer Gemini (fast, separate quota) before Groq so the router and judge
    # do not share one rate limit.
    judge_order = [p for p in PROVIDERS if p["name"] == "gemini"] + [p for p in PROVIDERS if p["name"] != "gemini"]
    for p in judge_order:
        if not _key(p["env"]):
            continue
        if gen_family and p["family"] == gen_family:
            continue
        for model in p["judge_candidates"]:
            ok, reason = _probe_model(p, model)
            if ok:
                judge = LLMSlot(role="judge", provider=p["name"], family=p["family"], model=model, available=True, reason=reason)
                break
        if judge:
            break
    if generator and judge and generator.family == judge.family:
        judge = None
        notes.append("Judge skipped: could not find a working model from a different family than the generator.")
    if generator and not judge:
        notes.append("LLM judge unavailable (need a second, different-family key/model).")
    stub_only = generator is None
    if stub_only:
        notes.append("Pipeline stays runnable with the extractive stub so retrieval and verifier evals still run.")
    inv = LLMInventory(
        keys_present=present,
        keys_absent=absent,
        router=router,
        generator=generator,
        judge=judge,
        stub_only=stub_only,
        notes=" ".join(notes),
    )
    _inventory = inv
    return inv


_provider_cool_until: dict[str, float] = {}


def complete(role: str, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 900, timeout: int = 90, max_attempts: int | None = None) -> str:
    """Run a chat completion on the discovered slot for this role. Raises if that slot is down."""
    inv = discover()
    slot = {"router": inv.router, "generator": inv.generator, "judge": inv.judge}.get(role)
    if not slot or not slot.available:
        raise RuntimeError(f"no LLM available for role={role}")
    provider = next(p for p in PROVIDERS if p["name"] == slot.provider)
    # Retry transient errors, with a longer backoff on 429 rate limits.
    last = None
    default_attempts = {"generator": 6, "router": 2, "judge": 2}.get(role, 6)
    attempts = default_attempts if max_attempts is None else max(1, int(max_attempts))
    cool_until = _provider_cool_until.get(provider["name"], 0)
    if time.time() < cool_until:
        # Router/judge: skip during a short 429 window. Generator: skip only on
        # a hard quota cooldown so the extractive fallback can proceed.
        if role in {"router", "judge"} or role == "generator":
            raise RuntimeError(
                f"{provider['name']} cooling down after 429/quota ({int(cool_until - time.time())}s left); skipping {role}"
            )
    for attempt in range(attempts):
        try:
            return _chat_with_provider(
                provider, slot.model, messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout
            )
        except Exception as e:
            last = e
            msg = str(e)
            low = msg.lower()
            if "401" in msg or "403" in msg or "402" in msg or "credits" in low or "incorrect api key" in low:
                break
            wait = None
            m = re.search(r"Retry-After=(\d+)", msg, re.I)
            if m:
                wait = min(90, int(m.group(1)))
            elif "429" in msg or "rate limit" in low or "resource_exhausted" in low or "quota" in low:
                wait = min(90, 8 * (2 ** attempt))  # 8, 16, 32, 64, 90, 90
            elif "503" in msg or "high demand" in low or "timeout" in low or "temporarily" in low:
                wait = min(45, 3 * (attempt + 1))
            else:
                wait = min(12, 1.5 * (attempt + 1))
            is_rate = "429" in msg or "rate limit" in low or "resource_exhausted" in low or "quota" in low
            hard_quota = "exceeded your current quota" in low or "billing details" in low or "quota exceeded" in low
            if hard_quota:
                # Daily/plan quota will not recover in a few minutes. Fall through
                # to the extractive stub instead of sleeping through 6 backoffs.
                _provider_cool_until[provider["name"]] = time.time() + 300
                print(f"  llm {role}/{slot.provider} hard quota; skipping further {role} calls for 300s", flush=True)
                break
            if is_rate and role in {"router", "judge"}:
                wait = min(wait, 20)
                _provider_cool_until[provider["name"]] = time.time() + max(wait, 20)
            if attempt >= attempts - 1:
                break
            print(f"  llm {role}/{slot.provider} retry in {int(wait)}s: {_sanitize_err(msg)[:160]}", flush=True)
            time.sleep(wait)
    raise RuntimeError(_sanitize_err(str(last)))
