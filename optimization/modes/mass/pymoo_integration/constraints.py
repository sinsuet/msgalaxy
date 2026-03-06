"""
Vectorized geometry and boundary constraint utilities.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np


def pairwise_separation(
    centers: np.ndarray,
    half_sizes: np.ndarray,
) -> Tuple[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """
    Return pairwise separation tensor for upper-triangle component pairs.

    sep_ijk = |center_i - center_j| - (half_i + half_j)
    """

    n_comp = centers.shape[0]
    if n_comp < 2:
        empty = np.zeros((0, 3), dtype=float)
        tri = (np.array([], dtype=int), np.array([], dtype=int))
        return empty, tri

    delta = np.abs(centers[:, None, :] - centers[None, :, :]) - (
        half_sizes[:, None, :] + half_sizes[None, :, :]
    )
    tri = np.triu_indices(n_comp, k=1)
    return delta[tri], tri


def compute_geometry_violation_metrics(
    centers: np.ndarray,
    half_sizes: np.ndarray,
    min_clearance_mm: float,
) -> Dict[str, float]:
    """
    Compute signed-clearance and overlap metrics in vectorized form.

    Returns:
        - min_clearance
        - num_collisions
        - max_overlap_depth
        - clearance_violation (min_clearance_limit - min_clearance)
        - collision_violation (max_overlap_depth)
    """

    pair_sep, _ = pairwise_separation(centers, half_sizes)
    if pair_sep.shape[0] == 0:
        pseudo_clearance = 1e6
        return {
            "min_clearance": pseudo_clearance,
            "num_collisions": 0.0,
            "max_overlap_depth": 0.0,
            "clearance_violation": float(min_clearance_mm - pseudo_clearance),
            "collision_violation": 0.0,
        }

    overlap_mask = np.all(pair_sep < 0.0, axis=1)
    num_collisions = float(np.count_nonzero(overlap_mask))

    max_overlap_depth = 0.0
    if np.any(overlap_mask):
        overlap_depth = np.min(-pair_sep[overlap_mask], axis=1)
        max_overlap_depth = float(np.max(overlap_depth))

    pair_gap = np.maximum(pair_sep, 0.0)
    signed_clearance = np.linalg.norm(pair_gap, axis=1)
    if np.any(overlap_mask):
        signed_clearance[overlap_mask] = -np.min(-pair_sep[overlap_mask], axis=1)

    min_clearance = float(np.min(signed_clearance))

    return {
        "min_clearance": min_clearance,
        "num_collisions": num_collisions,
        "max_overlap_depth": max_overlap_depth,
        "clearance_violation": float(min_clearance_mm - min_clearance),
        "collision_violation": max_overlap_depth,
    }


def compute_boundary_violation(
    centers: np.ndarray,
    half_sizes: np.ndarray,
    envelope_min: np.ndarray,
    envelope_max: np.ndarray,
) -> float:
    """
    Compute maximum boundary overflow across all components.

    A positive value means at least one component exceeds capsule boundary.
    """

    lower_excess = (envelope_min[None, :] + half_sizes) - centers
    upper_excess = centers - (envelope_max[None, :] - half_sizes)
    overflow = np.maximum(np.maximum(lower_excess, upper_excess), 0.0)
    if overflow.size == 0:
        return 0.0
    return float(np.max(overflow))
