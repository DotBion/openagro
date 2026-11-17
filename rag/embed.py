"""BAAI/bge-m3 dense encoder (1024-d, L2-normalized) on CPU."""

from __future__ import annotations

import os
from typing import Iterable, Sequence

import numpy as np

from rag.paths import MANIFEST_PATH
from rag.manifest_guard import load_manifest

MODEL_NAME = "BAAI/bge-m3"
EXPECTED_DIM = 1024

_model = None


def get_embedder():
    global _model
    if _model is None:
        # Keep CPU thread count reasonable on a shared box.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME, device="cpu", trust_remote_code=True)
    return _model


def unload_embedder() -> None:
    global _model
    if _model is not None:
        del _model
        _model = None
        import gc

        gc.collect()
        try:
            import torch

            if hasattr(torch, "cpu") and hasattr(torch.cpu, "empty_cache") is False:
                pass
        except Exception:
            pass


def embed_texts(
    texts: Sequence[str],
    *,
    batch_size: int = 8,
    show_progress: bool = True,
) -> np.ndarray:
    model = get_embedder()
    vecs = model.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
    )
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[1] != EXPECTED_DIM:
        raise RuntimeError(f"bge-m3 returned dim {arr.shape[1]}, expected {EXPECTED_DIM}")
    # belt and suspenders
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-12, None)
    arr = arr / norms
    return arr.astype(np.float32)


def expected_from_manifest() -> dict:
    m = load_manifest(MANIFEST_PATH)
    return {
        "embedding_model": m.get("embedding_model", MODEL_NAME),
        "expected_dim": int(m.get("expected_dim", EXPECTED_DIM)),
        "chunk_version": m.get("chunk_version", "v2"),
    }
