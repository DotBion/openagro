"""BAAI/bge-reranker-v2-m3 cross-encoder, loaded only when needed."""

from __future__ import annotations

import os
from typing import Sequence

import numpy as np

RERANKER_NAME = "BAAI/bge-reranker-v2-m3"

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(RERANKER_NAME, device="cpu", trust_remote_code=True)
    return _reranker


def unload_reranker() -> None:
    global _reranker
    if _reranker is not None:
        del _reranker
        _reranker = None
        import gc

        gc.collect()


def score_pairs(query: str, texts: Sequence[str], *, batch_size: int = 8) -> list[float]:
    if not texts:
        return []
    model = get_reranker()
    pairs = [(query, t) for t in texts]
    scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    return [float(s) for s in np.asarray(scores).reshape(-1)]
