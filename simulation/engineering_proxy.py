"""
Lightweight structural and power proxy estimators for MaaS M2.

These models are intentionally simple, deterministic, and layout-sensitive.
They provide executable signals for multiphysics constraint handling when
fully coupled high-fidelity solvers are not available in-loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _state_arrays(design_state) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], List[str]]:
    masses: List[float] = []
    powers: List[float] = []
    centers: List[List[float]] = []
    dims: List[List[float]] = []
    categories: List[str] = []
    ids: List[str] = []

    for comp in list(getattr(design_state, "components", []) or []):
        ids.append(str(getattr(comp, "id", "") or ""))
        categories.append(str(getattr(comp, "category", "") or "").strip().lower())
        masses.append(max(_safe_float(getattr(comp, "mass", 0.0)), 0.0))
        powers.append(max(_safe_float(getattr(comp, "power", 0.0)), 0.0))
        centers.append(
            [
                _safe_float(getattr(getattr(comp, "position", None), "x", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "y", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "z", 0.0)),
            ]
        )
        dims.append(
            [
                _safe_float(getattr(getattr(comp, "dimensions", None), "x", 0.0)),
                _safe_float(getattr(getattr(comp, "dimensions", None), "y", 0.0)),
                _safe_float(getattr(getattr(comp, "dimensions", None), "z", 0.0)),
            ]
        )

    return (
        np.asarray(masses, dtype=float),
        np.asarray(powers, dtype=float),
        np.asarray(centers, dtype=float),
        np.asarray(dims, dtype=float),
        categories,
        ids,
    )


def estimate_structural_proxy_metrics(
    design_state,
    *,
    cg_offset_mm: Optional[float] = None,
    min_clearance_mm: Optional[float] = None,
    num_collisions: Optional[int] = None,
    boundary_violation_mm: Optional[float] = None,
) -> Dict[str, float]:
    masses, _powers, centers, _dims, _categories, _ids = _state_arrays(design_state)
    if masses.size == 0:
        return {
            "max_stress": 0.0,
            "max_displacement": 0.0,
            "first_modal_freq": 200.0,
            "safety_factor": 10.0,
            "total_mass": 0.0,
            "mass_spread_rms": 0.0,
            "stiffness_gain": 1.0,
        }

    total_mass = float(np.sum(np.clip(masses, 0.0, None)))
    center = np.mean(centers, axis=0) if centers.size > 0 else np.zeros(3, dtype=float)
    radial = np.linalg.norm(centers - center, axis=1) if centers.size > 0 else np.zeros(1, dtype=float)
    mass_spread_rms = float(np.sqrt(np.mean(radial**2))) if radial.size > 0 else 0.0

    cg_offset = _safe_float(cg_offset_mm, 0.0)
    collisions = int(max(num_collisions or 0, 0))
    boundary_violation = max(_safe_float(boundary_violation_mm, 0.0), 0.0)
    min_clearance = _safe_float(min_clearance_mm, 10.0)

    bracket_count = 0
    for comp in list(getattr(design_state, "components", []) or []):
        bracket = getattr(comp, "bracket", None)
        if isinstance(bracket, dict) and bracket:
            bracket_count += 1
    stiffness_gain = 1.0 + 0.03 * float(bracket_count)
    stiffness_gain = float(np.clip(stiffness_gain, 1.0, 1.45))

    clearance_penalty = 0.0
    if np.isfinite(min_clearance) and min_clearance < 5.0:
        clearance_penalty = float((5.0 - min_clearance) * 0.6)

    equivalent_stress = (
        18.0
        + 0.16 * total_mass
        + 0.55 * cg_offset
        + 0.08 * mass_spread_rms
        + 6.0 * float(collisions)
        + 0.25 * boundary_violation
        + clearance_penalty
    ) / max(stiffness_gain, 1e-6)
    max_stress = max(float(equivalent_stress), 1.0)

    max_displacement = (
        0.015 * cg_offset
        + 0.002 * mass_spread_rms
        + 0.040 * float(collisions)
        + 0.003 * boundary_violation
        + 0.002 * max(clearance_penalty, 0.0)
    ) / max(stiffness_gain, 1e-6)
    max_displacement = max(float(max_displacement), 0.0)

    freq_mass_term = max(total_mass / 35.0, 1e-6)
    freq_cg_term = 1.0 + 0.012 * cg_offset
    first_modal_freq = (95.0 / np.sqrt(freq_mass_term)) / freq_cg_term * stiffness_gain
    first_modal_freq = float(max(first_modal_freq, 5.0))

    allowable_stress = 150.0
    safety_factor = float(np.clip(allowable_stress / max(max_stress, 1e-6), 0.2, 10.0))

    return {
        "max_stress": float(max_stress),
        "max_displacement": float(max_displacement),
        "first_modal_freq": float(first_modal_freq),
        "safety_factor": float(safety_factor),
        "total_mass": float(total_mass),
        "mass_spread_rms": float(mass_spread_rms),
        "stiffness_gain": float(stiffness_gain),
    }


def estimate_power_proxy_metrics(
    design_state,
    *,
    max_power_w: Optional[float] = None,
    bus_voltage_v: float = 28.0,
) -> Dict[str, float]:
    masses, powers, centers, _dims, categories, ids = _state_arrays(design_state)
    _ = masses  # reserved for future coupling
    total_power = float(np.sum(np.clip(powers, 0.0, None)))
    if powers.size == 0 or total_power <= 1e-9:
        return {
            "total_power": 0.0,
            "peak_power": 0.0,
            "power_margin": 100.0,
            "voltage_drop": 0.0,
            "avg_harness_path_mm": 0.0,
            "current_a": 0.0,
            "power_budget_used_w": float(max_power_w or 0.0),
        }

    avg_power = float(np.mean(powers))
    max_power = float(np.max(powers))
    concentration = max_power / max(avg_power, 1e-6)
    peak_factor = float(np.clip(1.10 + 0.06 * (concentration - 1.0), 1.08, 1.32))
    peak_power = float(total_power * peak_factor)

    source_indices = [
        idx
        for idx, (cat, comp_id) in enumerate(zip(categories, ids))
        if ("power" in cat) or ("battery" in comp_id.lower())
    ]
    if not source_indices:
        source_indices = [int(np.argmax(powers))]

    path_lengths: List[float] = []
    for idx in range(centers.shape[0]):
        if idx in source_indices:
            path_lengths.append(0.0)
            continue
        src_centers = centers[source_indices]
        dists = np.linalg.norm(src_centers - centers[idx], axis=1)
        nearest = float(np.min(dists)) if dists.size > 0 else 0.0
        path_lengths.append(nearest)
    path_arr = np.asarray(path_lengths, dtype=float)
    weights = np.clip(powers, 0.0, None)
    avg_path_mm = float(np.sum(path_arr * weights) / max(np.sum(weights), 1e-9))

    current_a = float(peak_power / max(_safe_float(bus_voltage_v, 28.0), 1.0))
    resistance_ohm_per_m = 0.0085
    congestion_multiplier = float(np.clip(1.0 + 0.12 * max(concentration - 1.0, 0.0), 1.0, 1.4))
    voltage_drop = current_a * resistance_ohm_per_m * (avg_path_mm / 1000.0) * congestion_multiplier
    voltage_drop = float(max(voltage_drop, 0.0))

    budget = _safe_float(max_power_w, 0.0)
    if budget <= 0.0:
        budget = float(peak_power * 1.15)
    power_margin = float((budget - peak_power) / max(budget, 1e-6) * 100.0)

    return {
        "total_power": float(total_power),
        "peak_power": float(peak_power),
        "power_margin": float(power_margin),
        "voltage_drop": float(voltage_drop),
        "avg_harness_path_mm": float(avg_path_mm),
        "current_a": float(current_a),
        "power_budget_used_w": float(budget),
    }

