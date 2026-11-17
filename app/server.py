"""Minimal FastAPI + HTML chat UI. Local only; binds to 127.0.0.1 by default."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/workspace/openagronomy")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chat.llm import discover
from chat.pipeline import answer_question
from chat.runtime import make_context, set_default_variant
from rag.manifest_guard import ManifestMismatch, check_manifest
from rag.paths import RESULTS_DIR

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="OpenAgronomy local chat", docs_url=None, redoc_url=None)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

_ctx = None
_boot_error = None


def _variant() -> str:
    p = RESULTS_DIR / "retrieval_eval.json"
    if p.exists():
        try:
            import json

            return json.loads(p.read_text())["reranker_decision"].get("default_variant") or "hybrid"
        except Exception:
            return "hybrid"
    return "hybrid"


@app.on_event("startup")
def startup():
    global _ctx, _boot_error
    try:
        check_manifest()
        variant = _variant()
        set_default_variant(variant)
        _ctx = make_context(variant=variant, demote_farmdeck=True)
        # Discover keys once (does not print secrets)
        discover()
    except ManifestMismatch as e:
        _boot_error = str(e)
        _ctx = None
    except Exception as e:
        _boot_error = f"startup failed: {e}"
        _ctx = None


class ChatIn(BaseModel):
    question: str
    use_llm: bool | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    inv = discover()
    return {
        "ok": _ctx is not None,
        "error": _boot_error,
        "variant": _variant(),
        "llm_stub_only": inv.stub_only,
        "generator": None if not inv.generator else {"provider": inv.generator.provider, "model": inv.generator.model},
        "keys_present": inv.keys_present,
    }


@app.post("/chat")
def chat(body: ChatIn):
    if _ctx is None:
        return JSONResponse({"ok": False, "error": _boot_error or "index not loaded"}, status_code=503)
    q = (body.question or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "empty question"}, status_code=400)
    ctx = _ctx
    if body.use_llm is False and _ctx.use_llm:
        from dataclasses import replace

        ctx = replace(_ctx, use_llm=False)
    rec = answer_question(ctx, q, log=True)
    return {
        "ok": True,
        "answer": rec.get("answer"),
        "route": rec.get("route"),
        "status": rec.get("status"),
        "generator": rec.get("generator"),
        "retrieved_ids": rec.get("retrieved_ids"),
        "verifier": rec.get("verifier"),
        "tool_names": rec.get("tool_names"),
    }


def main():
    import argparse

    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    uvicorn.run("app.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
