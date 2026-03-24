from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np


def _pairwise_clearance(design_state) -> Tuple[float, int]:
    comps = list(design_state.components)
    if len(comps) < 2:
        return float("inf"), 0

    min_signed_clearance = float("inf")
    collisions = 0
    for i, comp_a in enumerate(comps):
        ax, ay, az = float(comp_a.position.x), float(comp_a.position.y), float(comp_a.position.z)
        ahx, ahy, ahz = (
            float(comp_a.dimensions.x) / 2.0,
            float(comp_a.dimensions.y) / 2.0,
            float(comp_a.dimensions.z) / 2.0,
        )
        for comp_b in comps[i + 1 :]:
            bx, by, bz = float(comp_b.position.x), float(comp_b.position.y), float(comp_b.position.z)
            bhx, bhy, bhz = (
                float(comp_b.dimensions.x) / 2.0,
                float(comp_b.dimensions.y) / 2.0,
                float(comp_b.dimensions.z) / 2.0,
            )

            sep_x = abs(ax - bx) - (ahx + bhx)
            sep_y = abs(ay - by) - (ahy + bhy)
            sep_z = abs(az - bz) - (ahz + bhz)
            if sep_x <= 0.0 and sep_y <= 0.0 and sep_z <= 0.0:
                collisions += 1
                signed_clearance = -min(-sep_x, -sep_y, -sep_z)
            else:
                signed_clearance = float(
                    np.linalg.norm([max(sep_x, 0.0), max(sep_y, 0.0), max(sep_z, 0.0)])
                )
            min_signed_clearance = min(min_signed_clearance, signed_clearance)
    return float(min_signed_clearance), int(collisions)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _state_geometry_arrays(design_state):
    centers = []
    half_sizes = []
    powers = []
    for comp in list(getattr(design_state, "components", []) or []):
        centers.append(
            [
                _safe_float(getattr(getattr(comp, "position", None), "x", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "y", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "z", 0.0)),
            ]
        )
        half_sizes.append(
            [
                0.5 * _safe_float(getattr(getattr(comp, "dimensions", None), "x", 0.0)),
                0.5 * _safe_float(getattr(getattr(comp, "dimensions", None), "y", 0.0)),
                0.5 * _safe_float(getattr(getattr(comp, "dimensions", None), "z", 0.0)),
            ]
        )
        powers.append(_safe_float(getattr(comp, "power", 0.0)))
    return (
        np.asarray(centers, dtype=float),
        np.asarray(half_sizes, dtype=float),
        np.asarray(powers, dtype=float),
    )


def _wall_cooling_score(design_state, centers: np.ndarray, half_sizes: np.ndarray, powers: np.ndarray) -> float:
    if centers.size == 0:
        return 0.0
    envelope = getattr(design_state, "envelope", None)
    inner = getattr(envelope, "inner_size", None)
    if inner is None:
        return 0.0

    half_env = np.asarray(
        [
            0.5 * _safe_float(getattr(inner, "x", 0.0)),
            0.5 * _safe_float(getattr(inner, "y", 0.0)),
            0.5 * _safe_float(getattr(inner, "z", 0.0)),
        ],
        dtype=float,
    )
    if np.any(half_env <= 0.0):
        return 0.0

    free_margin = np.maximum(half_env[None, :] - half_sizes, 1e-6)
    wall_dist = np.maximum(free_margin - np.abs(centers), 0.0)
    nearest_wall_dist = np.min(wall_dist, axis=1)
    proximity = 1.0 - np.clip(nearest_wall_dist / 140.0, 0.0, 1.0)
    weights = np.clip(powers, 0.0, None)
    total = float(np.sum(weights))
    if total <= 1e-9:
        return float(np.mean(proximity))
    return float(np.sum(proximity * weights) / total)


def _hotspot_compaction_score(centers: np.ndarray, powers: np.ndarray) -> float:
    if centers.shape[0] < 2:
        return 0.0
    weights = np.clip(powers, 0.0, None)
    if float(np.sum(weights)) <= 1e-9:
        return 0.0
    threshold = max(float(np.percentile(weights, 70.0)), float(np.mean(weights)))
    hot_idx = np.where(weights >= threshold)[0]
    if hot_idx.size < 2:
        hot_idx = np.argsort(weights)[-min(3, weights.size) :]
    if hot_idx.size < 2:
        return 0.0

    hot_centers = centers[hot_idx]
    hot_weights = weights[hot_idx]
    numerator = 0.0
    denominator = float(np.sum(hot_weights))
    for idx in range(hot_centers.shape[0]):
        dists = np.linalg.norm(hot_centers - hot_centers[idx], axis=1)
        dists = dists[dists > 1e-9]
        if dists.size <= 0:
            continue
        numerator += float(np.exp(-float(np.min(dists)) / 160.0)) * float(hot_weights[idx])
    return float(np.clip(numerator / max(denominator, 1e-9), 0.0, 1.0))


def _power_spread_score(centers: np.ndarray, powers: np.ndarray) -> float:
    if centers.size == 0:
        return 0.0
    weights = np.clip(powers, 0.0, None)
    total = float(np.sum(weights))
    if total <= 1e-9:
        return 0.0
    normalized = weights / total
    centroid = np.sum(centers * normalized[:, None], axis=0)
    rms_spread = float(
        np.sqrt(np.sum(normalized * np.sum((centers - centroid) ** 2, axis=1)))
    )
    return float(np.clip(rms_spread / 220.0, 0.0, 1.0))


def estimate_proxy_thermal_metrics(
    design_state,
    *,
    min_clearance_mm: Optional[float] = None,
    num_collisions: Optional[int] = None,
) -> Dict[str, float]:
    centers, half_sizes, powers = _state_geometry_arrays(design_state)
    total_power = float(np.sum(np.clip(powers, 0.0, None)))
    if min_clearance_mm is None or num_collisions is None:
        min_clearance_mm, num_collisions = _pairwise_clearance(design_state)

    min_clearance = _safe_float(min_clearance_mm, 0.0)
    collisions = max(int(num_collisions or 0), 0)
    wall_cooling = _wall_cooling_score(design_state, centers, half_sizes, powers)
    hotspot_compaction = _hotspot_compaction_score(centers, powers)
    spread_score = _power_spread_score(centers, powers)

    base_temp = 18.0 + 0.062 * total_power
    clearance_term = 20.0 / max(min_clearance + 12.0, 1.0)
    collision_term = float(collisions) * 6.0
    hotspot_penalty = 11.0 * hotspot_compaction
    spread_bonus = 5.0 * spread_score
    wall_cooling_bonus = 7.0 * wall_cooling

    max_temp = max(
        float(base_temp + clearance_term + collision_term + hotspot_penalty - spread_bonus - wall_cooling_bonus),
        5.0,
    )
    min_temp = float(max_temp - (8.0 + 1.5 * hotspot_compaction))
    avg_temp = float((max_temp + min_temp) / 2.0)
    temp_gradient = float(0.0045 * total_power + 0.9 * hotspot_compaction + 0.1 * collisions)

    return {
        "max_temp": float(max_temp),
        "min_temp": float(min_temp),
        "avg_temp": float(avg_temp),
        "temp_gradient": float(temp_gradient),
        "wall_cooling_score": float(wall_cooling),
        "hotspot_compaction_score": float(hotspot_compaction),
        "power_spread_score": float(spread_score),
    }
