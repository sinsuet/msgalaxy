"""
Simplified multiphysics backend for fast non-COMSOL smoke/integration runs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.protocol import SimulationRequest, SimulationResult, ViolationItem, ViolationType
from simulation.base import SimulationDriver
from simulation.structural_physics import calculate_cg_offset


def _pairwise_clearance(design_state) -> Tuple[float, int]:
    comps = list(design_state.components)
    if len(comps) < 2:
        return float("inf"), 0

    min_signed_clearance = float("inf")
    collisions = 0

    for i in range(len(comps)):
        a = comps[i]
        ax, ay, az = float(a.position.x), float(a.position.y), float(a.position.z)
        ahx, ahy, ahz = float(a.dimensions.x) / 2.0, float(a.dimensions.y) / 2.0, float(a.dimensions.z) / 2.0
        for j in range(i + 1, len(comps)):
            b = comps[j]
            bx, by, bz = float(b.position.x), float(b.position.y), float(b.position.z)
            bhx, bhy, bhz = float(b.dimensions.x) / 2.0, float(b.dimensions.y) / 2.0, float(b.dimensions.z) / 2.0

            sep_x = abs(ax - bx) - (ahx + bhx)
            sep_y = abs(ay - by) - (ahy + bhy)
            sep_z = abs(az - bz) - (ahz + bhz)

            if sep_x <= 0.0 and sep_y <= 0.0 and sep_z <= 0.0:
                penetration = min(-sep_x, -sep_y, -sep_z)
                signed_clearance = -penetration
                collisions += 1
            else:
                gap_x = max(sep_x, 0.0)
                gap_y = max(sep_y, 0.0)
                gap_z = max(sep_z, 0.0)
                signed_clearance = float(np.sqrt(gap_x * gap_x + gap_y * gap_y + gap_z * gap_z))

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


def _state_geometry_arrays(design_state) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    centers: List[List[float]] = []
    half_sizes: List[List[float]] = []
    powers: List[float] = []
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
    wall_scale_mm = 140.0
    proximity = 1.0 - np.clip(nearest_wall_dist / wall_scale_mm, 0.0, 1.0)

    weights = np.clip(powers, 0.0, None)
    total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        return float(np.mean(proximity))
    return float(np.sum(proximity * weights) / total_w)


def _hotspot_compaction_score(centers: np.ndarray, powers: np.ndarray) -> float:
    if centers.shape[0] < 2:
        return 0.0
    weights = np.clip(powers, 0.0, None)
    if float(np.sum(weights)) <= 1e-9:
        return 0.0

    threshold = max(float(np.percentile(weights, 70.0)), float(np.mean(weights)))
    hot_idx = np.where(weights >= threshold)[0]
    if hot_idx.size < 2:
        hot_idx = np.argsort(weights)[-min(3, weights.size):]
    if hot_idx.size < 2:
        return 0.0

    hot_centers = centers[hot_idx]
    hot_weights = weights[hot_idx]
    numer = 0.0
    denom = float(np.sum(hot_weights))
    if denom <= 1e-9:
        return 0.0

    for idx in range(hot_centers.shape[0]):
        dists = np.linalg.norm(hot_centers - hot_centers[idx], axis=1)
        dists = dists[dists > 1e-9]
        if dists.size <= 0:
            continue
        nearest = float(np.min(dists))
        numer += float(np.exp(-nearest / 160.0)) * float(hot_weights[idx])

    return float(np.clip(numer / denom, 0.0, 1.0))


def _power_spread_score(centers: np.ndarray, powers: np.ndarray) -> float:
    if centers.size == 0:
        return 0.0
    weights = np.clip(powers, 0.0, None)
    total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        return 0.0
    w = weights / total_w
    centroid = np.sum(centers * w[:, None], axis=0)
    sq_dist = np.sum((centers - centroid) ** 2, axis=1)
    rms_spread = float(np.sqrt(np.sum(w * sq_dist)))
    return float(np.clip(rms_spread / 220.0, 0.0, 1.0))


def estimate_proxy_thermal_metrics(
    design_state,
    *,
    min_clearance_mm: Optional[float] = None,
    num_collisions: Optional[int] = None,
) -> Dict[str, float]:
    """
    Layout-sensitive thermal proxy for development and MaaS fallback paths.

    The model keeps execution cheap while exposing meaningful optimization
    gradients:
    - power load (base heating),
    - hotspot compaction penalty,
    - wall cooling bonus (radiator exposure),
    - spread bonus (avoid local heat concentration),
    - clearance/collision effects.
    """

    centers, half_sizes, powers = _state_geometry_arrays(design_state)
    total_power = float(np.sum(np.clip(powers, 0.0, None)))

    if min_clearance_mm is None or num_collisions is None:
        clearance_calc, collision_calc = _pairwise_clearance(design_state)
        if min_clearance_mm is None:
            min_clearance_mm = float(clearance_calc)
        if num_collisions is None:
            num_collisions = int(collision_calc)

    min_clearance = _safe_float(min_clearance_mm, 0.0)
    collisions = max(int(num_collisions or 0), 0)

    wall_cooling = _wall_cooling_score(design_state, centers, half_sizes, powers)
    hotspot_compaction = _hotspot_compaction_score(centers, powers)
    spread_score = _power_spread_score(centers, powers)

    base_temp = 18.0 + 0.078 * total_power
    clearance_term = 20.0 / max(min_clearance + 12.0, 1.0)
    collision_term = float(collisions) * 6.0
    hotspot_penalty = 12.0 * hotspot_compaction
    spread_bonus = 6.0 * spread_score
    wall_cooling_bonus = 8.0 * wall_cooling

    max_temp = base_temp + clearance_term + collision_term + hotspot_penalty - spread_bonus - wall_cooling_bonus
    max_temp = max(float(max_temp), 5.0)
    min_temp = float(max_temp - (8.0 + 1.5 * hotspot_compaction))
    avg_temp = float((max_temp + min_temp) / 2.0)
    temp_gradient = float(0.0055 * total_power + 0.9 * hotspot_compaction + 0.1 * collisions)

    return {
        "max_temp": float(max_temp),
        "min_temp": float(min_temp),
        "avg_temp": float(avg_temp),
        "temp_gradient": float(temp_gradient),
        "wall_cooling_score": float(wall_cooling),
        "hotspot_compaction": float(hotspot_compaction),
        "power_spread_score": float(spread_score),
        "total_power": float(total_power),
    }


class SimplifiedPhysicsEngine(SimulationDriver):
    """
    Fast surrogate backend.

    Intended for algorithmic smoke tests and development loops where COMSOL
    turnaround is too expensive.
    """

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        if not self.connected:
            self.connect()

        state = request.design_state
        total_power = float(sum(float(c.power) for c in state.components))
        total_mass = float(sum(float(c.mass) for c in state.components))
        min_clearance, num_collisions = _pairwise_clearance(state)
        cg_offset = float(calculate_cg_offset(state))

        thermal = estimate_proxy_thermal_metrics(
            state,
            min_clearance_mm=float(min_clearance),
            num_collisions=int(num_collisions),
        )

        metrics = {
            "max_temp": float(thermal["max_temp"]),
            "min_temp": float(thermal["min_temp"]),
            "avg_temp": float(thermal["avg_temp"]),
            "temp_gradient": float(thermal["temp_gradient"]),
            "total_power": float(total_power),
            "total_mass": float(total_mass),
            "min_clearance": float(min_clearance),
            "cg_offset": float(cg_offset),
            "num_collisions": float(num_collisions),
            "proxy_wall_cooling_score": float(thermal.get("wall_cooling_score", 0.0)),
            "proxy_hotspot_compaction": float(thermal.get("hotspot_compaction", 0.0)),
            "proxy_power_spread_score": float(thermal.get("power_spread_score", 0.0)),
        }

        violations_raw = self.check_constraints(metrics)
        violations: List[ViolationItem] = []
        for item in violations_raw:
            violation_type = str(item.get("type", "GEOMETRY_CLASH"))
            try:
                v_type = ViolationType(violation_type)
            except Exception:
                v_type = ViolationType.GEOMETRY_CLASH
            violations.append(
                ViolationItem(
                    id=str(item.get("id", "SIMPLIFIED_VIOLATION")),
                    type=v_type,
                    description=str(item.get("description", "")),
                    involved_components=list(item.get("involved_components", [])),
                    severity=float(item.get("severity", 0.0)),
                )
            )

        return SimulationResult(
            success=True,
            metrics=metrics,
            violations=violations,
            raw_data={"backend": "simplified"},
            error_message=None,
        )
