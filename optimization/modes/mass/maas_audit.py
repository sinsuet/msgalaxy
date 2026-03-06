"""
Utilities for MaaS post-solver physics auditing.
"""

from __future__ import annotations

from typing import List

import numpy as np


def select_top_pareto_indices(pareto_f: np.ndarray, top_k: int) -> List[int]:
    """
    Select top-k Pareto points by minimal objective sum.

    Rules:
    - Finite objective rows are prioritized.
    - If all rows are non-finite, fallback to the first k rows.
    - Return indices in ascending score order.
    """
    if top_k <= 0:
        return []

    arr = np.asarray(pareto_f, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return []

    score = np.sum(arr, axis=1)
    finite_idx = np.where(np.isfinite(score))[0]
    if finite_idx.size == 0:
        limit = min(int(top_k), arr.shape[0])
        return [int(i) for i in range(limit)]

    ordered = finite_idx[np.argsort(score[finite_idx])]
    limit = min(int(top_k), ordered.size)
    return [int(i) for i in ordered[:limit]]

