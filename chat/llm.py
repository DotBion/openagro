"""Swappable LLM client. Discovers keys from the environment; never logs or returns secret values."""

from __future__ import annotations

import json
import re
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

# OpenAI-compatible chat endpoints plus Bedrock Converse. Model ids are probed at runtime.
PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "bedrock",
        "env": "AWS_ACCESS_KEY_ID",  # also requires AWS_SECRET_ACCESS_KEY
        "family": "amazon-nova",
        "kind": "bedrock",
        "regions": ["us-east-1", "us-west-2"],
        "router_candidates": ["us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"],
        "generator_candidates": ["us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"],
        "judge_candidates": ["us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"],
    },
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

# Official on-demand list price for Amazon Nova Lite on Bedrock in us-east-1 / us-west-2,
# checked 2026-11-17 (https://aws.amazon.com/bedrock/pricing/ and consistent secondary listings).
NOVA_LITE_INPUT_USD_PER_M = 0.06
NOVA_LITE_OUTPUT_USD_PER_M = 0.24


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
_provider_cool_until: dict[str, float] = {}
_bedrock_endpoint: dict[str, str] = {}  # model -> region that last worked
_usage_totals: dict[str, dict[str, int]] = defaultdict(
    lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
)


def _key(env_name: str) -> str | None:
    v = os.environ.get(env_name)
    if v and v.strip() and v.strip() not in {"0", "None", "null"}:
        return v.strip()
    return None


def _bedrock_creds() -> bool:
    return bool(_key("AWS_ACCESS_KEY_ID") and _key("AWS_SECRET_ACCESS_KEY"))


def _provider_ready(provider: dict) -> bool:
    if provider["name"] == "bedrock":
        return _bedrock_creds()
    return bool(_key(provider["env"]))


def present_key_names() -> tuple[list[str], list[str]]:
    present, absent = [], []
    seen = set()
    for p in PROVIDERS:
        n = p["env"]
        if n in seen:
            continue
        seen.add(n)
        ok = _provider_ready(p)
        (present if ok else absent).append(n)
    extra = "ANTHROPIC_API_KEY"
    if extra not in seen:
        (present if _key(extra) else absent).append(extra)
    return present, absent


def _usage_path() -> Path:
    try:
        from rag.paths import RESULTS_DIR

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        return RESULTS_DIR / "llm_usage.jsonl"
    except Exception:
        p = Path("/workspace/openagronomy/results")
        p.mkdir(parents=True, exist_ok=True)
        return p / "llm_usage.jsonl"


def _record_usage(
    *,
    provider: str,
    model: str,
    role: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
) -> None:
    """Append one usage row. Never logs prompts, keys, or message text."""
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    tot = int(total_tokens or (inp + out))
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": provider,
        "model": model,
        "role": role,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": tot,
    }
    key = f"{provider}:{role}"
    acc = _usage_totals[key]
    acc["calls"] += 1
    acc["input_tokens"] += inp
    acc["output_tokens"] += out
    acc["total_tokens"] += tot
    try:
        with _usage_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def usage_summary() -> dict[str, Any]:
    """Process-local token totals plus a Nova Lite cost estimate."""
    by = {k: dict(v) for k, v in _usage_totals.items()}
    nova_in = nova_out = nova_calls = 0
    all_in = all_out = all_calls = 0
    for key, acc in by.items():
        all_in += acc["input_tokens"]
        all_out += acc["output_tokens"]
        all_calls += acc["calls"]
        if key.startswith("bedrock:"):
            nova_in += acc["input_tokens"]
            nova_out += acc["output_tokens"]
            nova_calls += acc["calls"]
    cost = (nova_in / 1_000_000) * NOVA_LITE_INPUT_USD_PER_M + (nova_out / 1_000_000) * NOVA_LITE_OUTPUT_USD_PER_M
    return {
        "by_provider_role": by,
        "all": {"calls": all_calls, "input_tokens": all_in, "output_tokens": all_out, "total_tokens": all_in + all_out},
        "nova_lite": {
            "calls": nova_calls,
            "input_tokens": nova_in,
            "output_tokens": nova_out,
            "total_tokens": nova_in + nova_out,
            "usd_estimate": round(cost, 8),
            "price_source": (
                "On-demand list price $0.06 / $0.24 per 1M input/output tokens for "
                "amazon.nova-lite-v1:0 in us-east-1 and us-west-2, checked 2026-11-17 "
                "(https://aws.amazon.com/bedrock/pricing/)."
            ),
        },
    }


def estimate_nova_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * NOVA_LITE_INPUT_USD_PER_M + (output_tokens / 1_000_000) * NOVA_LITE_OUTPUT_USD_PER_M


def _openai_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    timeout: int = 60,
    extra_headers: dict | None = None,
    temperature: float = 0.0,
    max_tokens: int = 800,
    role: str = "unknown",
    provider_name: str = "openai",
) -> str:
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
    usage = data.get("usage") or {}
    _record_usage(
        provider=provider_name,
        model=model,
        role=role,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
    return data["choices"][0]["message"]["content"] or ""


def _gemini_chat(
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    timeout: int = 60,
    temperature: float = 0.0,
    max_tokens: int = 800,
    role: str = "unknown",
) -> str:
    # Convert chat messages to a Gemini generateContent body. Key goes in the query string.
    sys_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role_g = "user" if m["role"] == "user" else "model"
        contents.append({"role": role_g, "parts": [{"text": m["content"]}]})
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
    usage = data.get("usageMetadata") or {}
    _record_usage(
        provider="gemini",
        model=model,
        role=role,
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        total_tokens=usage.get("totalTokenCount"),
    )
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini model={model} returned no candidates")
    parts = ((cands[0].get("content") or {}).get("parts") or [])
    return "".join(p.get("text") or "" for p in parts)


def bedrock_messages(messages: list[dict]) -> tuple[list[dict], list[dict] | None]:
    """Split OpenAI-style messages into Bedrock Converse system + messages."""
    system: list[dict] = []
    conv: list[dict] = []
    for m in messages:
        text = m.get("content") or ""
        if not isinstance(text, str):
            text = str(text)
        if m.get("role") == "system":
            if text.strip():
                system.append({"text": text})
            continue
        role = "assistant" if m.get("role") == "assistant" else "user"
        conv.append({"role": role, "content": [{"text": text}]})
    if not conv:
        conv = [{"role": "user", "content": [{"text": ""}]}]
    return conv, (system or None)


def _bedrock_client(region: str, timeout: int):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(connect_timeout=10, read_timeout=timeout, retries={"max_attempts": 1, "mode": "standard"}),
    )


def _bedrock_chat(
    model: str,
    messages: list[dict],
    *,
    timeout: int = 60,
    temperature: float = 0.0,
    max_tokens: int = 800,
    role: str = "unknown",
    regions: list[str] | None = None,
    **_ignored: Any,
) -> str:
    """Call Amazon Bedrock Converse. Retries across us-east-1 / us-west-2 on region/model errors."""
    if not _bedrock_creds():
        raise RuntimeError("no AWS credentials for bedrock")
    import botocore.exceptions

    conv, system = bedrock_messages(messages)
    kwargs: dict[str, Any] = {
        "modelId": model,
        "messages": conv,
        "inferenceConfig": {"maxTokens": int(max_tokens), "temperature": float(temperature)},
    }
    if system:
        kwargs["system"] = system
    preferred = _bedrock_endpoint.get(model)
    region_list = list(regions or ["us-east-1", "us-west-2"])
    if preferred in region_list:
        region_list = [preferred] + [r for r in region_list if r != preferred]
    last_err: Exception | None = None
    for region in region_list:
        try:
            client = _bedrock_client(region, timeout)
            resp = client.converse(**kwargs)
            usage = resp.get("usage") or {}
            _record_usage(
                provider="bedrock",
                model=model,
                role=role,
                input_tokens=usage.get("inputTokens"),
                output_tokens=usage.get("outputTokens"),
                total_tokens=usage.get("totalTokens"),
            )
            _bedrock_endpoint[model] = region
            parts = ((resp.get("output") or {}).get("message") or {}).get("content") or []
            text = "".join(p.get("text") or "" for p in parts if isinstance(p, dict))
            if not text.strip():
                raise RuntimeError(f"bedrock model={model} region={region} returned empty text")
            return text
        except botocore.exceptions.ClientError as e:
            err = e.response.get("Error") or {}
            code = err.get("Code") or e.__class__.__name__
            msg = err.get("Message") or str(e)
            last_err = RuntimeError(f"{code} from bedrock model={model} region={region}: {msg}")
            # Throttling and service errors should not silently move to the other region
            # after a successful endpoint is known; re-raise so complete() can back off.
            if code in {"ThrottlingException", "ServiceQuotaExceededException", "ModelTimeoutException"}:
                raise last_err from e
            # Permission or missing-model in this region: try the next region.
            continue
        except RuntimeError:
            raise
        except Exception as e:
            last_err = RuntimeError(f"{type(e).__name__} from bedrock model={model} region={region}: {e}")
            continue
    raise last_err or RuntimeError(f"bedrock model={model} failed in all regions")


def _chat_with_provider(provider: dict, model: str, messages: list[dict], **kw) -> str:
    role = kw.pop("role", "unknown")
    if provider["name"] == "bedrock":
        return _bedrock_chat(model, messages, role=role, regions=provider.get("regions"), **kw)
    key = _key(provider["env"])
    if not key:
        raise RuntimeError(f"no key for {provider['name']}")
    if provider["name"] == "gemini":
        return _gemini_chat(key, model, messages, role=role, **kw)
    extra = None
    if provider["name"] == "openrouter":
        extra = {"HTTP-Referer": "http://localhost", "X-Title": "OpenAgronomy local"}
    return _openai_chat(
        provider["base_url"],
        key,
        model,
        messages,
        extra_headers=extra,
        role=role,
        provider_name=provider["name"],
        **kw,
    )


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
            role="probe",
        )
        ok = bool(text and text.strip())
        if ok:
            _probed_ok[cache_key] = True
        return ok, "ok" if ok else "empty"
    except Exception as e:
        err = _sanitize_err(str(e))
        low = err.lower()
        transient = any(
            s in low
            for s in (
                "429",
                "503",
                "timeout",
                "high demand",
                "temporarily",
                "rate limit",
                "unavailable",
                "resource_exhausted",
                "throttling",
            )
        )
        if not transient:
            _probed_ok[cache_key] = False
        return False, err


def _sanitize_err(msg: str) -> str:
    """Strip API keys and provider key-prefix leaks from error text (never log secrets)."""
    msg = msg or ""
    msg = re.sub(r"sk-[A-Za-z0-9_\-]+", "[redacted]", msg)
    msg = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", msg)
    msg = re.sub(r"AKIA[0-9A-Z]{16}", "[redacted]", msg)
    msg = re.sub(r"ASIA[0-9A-Z]{16}", "[redacted]", msg)
    msg = re.sub(r"AWS_SECRET_ACCESS_KEY[=:]\s*\S+", "AWS_SECRET_ACCESS_KEY=[redacted]", msg, flags=re.I)
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
        if not _provider_ready(p):
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
    # Default generator is Amazon Nova Lite on Bedrock when AWS creds work.
    # Fall back to Gemini, then Groq. Do not pin Gemini over a working Bedrock slot,
    # and do not let a cached Groq router probe steal the generator role.
    generator = _first_working("generator", preferred=["bedrock", "gemini", "groq", "openai", "openrouter", "xai"])
    # Prefer Groq for the router; Nova Lite may route if Groq is down.
    router = _first_working("router", preferred=["groq", "bedrock", "openai", "xai", "gemini", "openrouter"])
    judge = None
    gen_family = generator.family if generator else None
    # Judge must be a different family than the generator. Prefer Groq, then Gemini,
    # so the judge does not share Nova Lite's quota.
    judge_pref = ["groq", "gemini", "openai", "openrouter", "xai", "bedrock"]
    by_name = {p["name"]: p for p in PROVIDERS}
    for name in judge_pref:
        p = by_name.get(name)
        if not p or not _provider_ready(p):
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
    if generator and generator.provider == "bedrock":
        notes.append(f"Generator is Amazon Nova Lite ({generator.model}) via Bedrock Converse.")
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


def complete(role: str, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 900, timeout: int = 90, max_attempts: int | None = None) -> str:
    """Run a chat completion on the discovered slot for this role. Raises if that slot is down."""
    inv = discover()
    slot = {"router": inv.router, "generator": inv.generator, "judge": inv.judge}.get(role)
    if not slot or not slot.available:
        raise RuntimeError(f"no LLM available for role={role}")
    provider = next(p for p in PROVIDERS if p["name"] == slot.provider)
    # Retry transient errors, with a longer backoff on 429 / ThrottlingException.
    last = None
    default_attempts = {"generator": 6, "router": 2, "judge": 2}.get(role, 6)
    attempts = default_attempts if max_attempts is None else max(1, int(max_attempts))
    cool_until = _provider_cool_until.get(provider["name"], 0)
    if time.time() < cool_until:
        if role in {"router", "judge"} or role == "generator":
            raise RuntimeError(
                f"{provider['name']} cooling down after 429/quota ({int(cool_until - time.time())}s left); skipping {role}"
            )
    for attempt in range(attempts):
        try:
            return _chat_with_provider(
                provider,
                slot.model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                role=role,
            )
        except Exception as e:
            last = e
            msg = str(e)
            low = msg.lower()
            if "401" in msg or "403" in msg or "402" in msg or "credits" in low or "incorrect api key" in low or "unrecognizedclient" in low:
                break
            wait = None
            m = re.search(r"Retry-After=(\d+)", msg, re.I)
            if m:
                wait = min(90, int(m.group(1)))
            elif (
                "429" in msg
                or "rate limit" in low
                or "resource_exhausted" in low
                or "throttling" in low
                or "servicequotaexceeded" in low
            ):
                wait = min(90, 8 * (2 ** attempt))  # 8, 16, 32, 64, 90, 90
            elif "503" in msg or "high demand" in low or "timeout" in low or "temporarily" in low or "modeltimeoutexception" in low:
                wait = min(45, 3 * (attempt + 1))
            else:
                wait = min(12, 1.5 * (attempt + 1))
            is_rate = (
                "429" in msg
                or "rate limit" in low
                or "resource_exhausted" in low
                or "quota" in low
                or "throttling" in low
            )
            hard_quota = "exceeded your current quota" in low or "billing details" in low or "quota exceeded" in low
            # Bedrock ThrottlingException is transient, not a hard billing quota.
            if hard_quota and "throttling" not in low:
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
