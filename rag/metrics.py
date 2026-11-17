"""Recall@k, MRR, and bootstrap 95% CIs."""

from __future__ import annotations

from typing import Iterable

import numpy as np


def bootstrap_ci(
    values: Iterable[float],
    *,
    n_boot: int = 10000,
    seed: int = 20261117,
    alpha: float = 0.05,
) -> dict[str, float]:
    arr = np.asarray(list(values), dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "ci95_lo": float("nan"), "ci95_hi": float("nan")}
    mean = float(arr.mean())
    if n == 1:
        return {"n": 1, "mean": mean, "ci95_lo": mean, "ci95_hi": mean}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    means = arr[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {"n": n, "mean": mean, "ci95_lo": float(lo), "ci95_hi": float(hi)}


def recall_at(ranks: Iterable[int | None], k: int) -> list[float]:
    out = []
    for r in ranks:
        out.append(1.0 if (r is not None and r <= k) else 0.0)
    return out


def mrr_values(ranks: Iterable[int | None]) -> list[float]:
    out = []
    for r in ranks:
        out.append((1.0 / r) if (r is not None and r > 0) else 0.0)
    return out


def summarize(ranks: list[int | None], *, ks: tuple[int, ...] = (5, 10), seed: int = 20261117) -> dict:
    out: dict = {"n": len(ranks)}
    for k in ks:
        out[f"recall@{k}"] = bootstrap_ci(recall_at(ranks, k), seed=seed)
    out["mrr"] = bootstrap_ci(mrr_values(ranks), seed=seed)
    return out
