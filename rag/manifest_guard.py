"""Startup guard: refuse to serve if the on-disk index does not match kb/index_manifest.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.paths import EMBED_META_PATH, MANIFEST_PATH


class ManifestMismatch(RuntimeError):
    """Raised when embeddings were built with a different model, dim, or chunk version."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ManifestMismatch(f"Missing index manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_embed_meta(path: Path = EMBED_META_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ManifestMismatch(
            f"Missing embedding metadata: {path}. Run: .venv/bin/python scripts/embed_chunks.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def check_manifest(
    embed_meta: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    *,
    embeddings_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Compare embedding metadata to the chunk manifest. Raises ManifestMismatch on conflict."""
    manifest = manifest or load_manifest()
    embed_meta = embed_meta or load_embed_meta()
    expected = {
        "embedding_model": manifest.get("embedding_model") or manifest.get("tokenizer"),
        "expected_dim": int(manifest.get("expected_dim", 1024)),
        "chunk_version": manifest.get("chunk_version"),
    }
    got = {
        "embedding_model": embed_meta.get("embedding_model"),
        "expected_dim": int(embed_meta.get("expected_dim") or embed_meta.get("dim") or 0),
        "chunk_version": embed_meta.get("chunk_version"),
    }
    mismatches = {k: {"manifest": expected[k], "index": got[k]} for k in expected if expected[k] != got[k]}
    if embeddings_shape is not None:
        n, d = embeddings_shape
        if d != expected["expected_dim"]:
            mismatches["dim_shape"] = {"manifest": expected["expected_dim"], "index": d}
        n_chunks = embed_meta.get("n_chunks")
        if n_chunks is not None and int(n_chunks) != n:
            mismatches["n_chunks"] = {"embed_meta": n_chunks, "matrix": n}
    if mismatches:
        raise ManifestMismatch(
            "Index does not match kb/index_manifest.json; refusing to start. " + json.dumps(mismatches)
        )
    return {"ok": True, "expected": expected, "index": got}


def guard_or_die() -> dict[str, Any]:
    return check_manifest()
