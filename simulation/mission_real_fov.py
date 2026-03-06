"""
Deterministic mission/FOV evaluator used by strict real-only benchmark runs.

This evaluator is geometry-based (no alias fallback) and always returns
`mission_source=mission_fov_real`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from simulation.contracts import MISSION_SOURCE_FOV_REAL


def _axis_index(axis: str) -> int:
    text = str(axis or "z").strip().lower()
    if text == "x":
        return 0
    if text == "y":
        return 1
    return 2


def _component_box(comp: Any) -> Tuple[np.ndarray, np.ndarray]:
    pos = getattr(comp, "position", None)
    dim = getattr(comp, "dimensions", None)
    if pos is None or dim is None:
        return np.zeros(3, dtype=float), np.zeros(3, dtype=float)
    center = np.asarray(
        [
            float(getattr(pos, "x", 0.0)),
            float(getattr(pos, "y", 0.0)),
            float(getattr(pos, "z", 0.0)),
        ],
        dtype=float,
    )
    half = 0.5 * np.asarray(
        [
            float(getattr(dim, "x", 0.0)),
            float(getattr(dim, "y", 0.0)),
            float(getattr(dim, "z", 0.0)),
        ],
        dtype=float,
    )
    return center, half


def _is_mission_critical(comp: Any) -> bool:
    text = (
        str(getattr(comp, "category", "") or "").lower()
        + " "
        + str(getattr(comp, "id", "") or "").lower()
    )
    tokens = (
        "payload",
        "camera",
        "optic",
        "star",
        "tracker",
        "sensor",
        "antenna",
    )
    return any(token in text for token in tokens)


def _project_overlap_2d(
    center_a: np.ndarray,
    half_a: np.ndarray,
    center_b: np.ndarray,
    half_b: np.ndarray,
    axis_id: int,
) -> float:
    dims = [0, 1, 2]
    dims.remove(int(axis_id))
    overlap_ratio = 1.0
    for dim_id in dims:
        a0 = float(center_a[dim_id] - half_a[dim_id])
        a1 = float(center_a[dim_id] + half_a[dim_id])
        b0 = float(center_b[dim_id] - half_b[dim_id])
        b1 = float(center_b[dim_id] + half_b[dim_id])
        overlap = max(min(a1, b1) - max(a0, b0), 0.0)
        denom = max(min(a1 - a0, b1 - b0), 1e-6)
        overlap_ratio *= float(overlap / denom)
    return float(np.clip(overlap_ratio, 0.0, 1.0))


def evaluate_fov_keepout_real(
    *,
    design_state: Any,
    axis: str = "z",
    keepout_center_mm: float = 0.0,
    min_separation_mm: float = 0.0,
) -> Dict[str, Any]:
    axis_id = _axis_index(axis)
    center_value = float(keepout_center_mm)
    min_sep_required = max(float(min_separation_mm), 0.0)

    components = list(getattr(design_state, "components", []) or [])
    if not components:
        return {
            "mission_keepout_violation": float(min_sep_required),
            "mission_keepout_min_separation": 0.0,
            "fov_occlusion_proxy": 1.0,
            "emc_separation_proxy": 0.0,
            "mission_source": MISSION_SOURCE_FOV_REAL,
            "interface_status": "real_geometry_empty",
        }

    critical_components = [comp for comp in components if _is_mission_critical(comp)]
    if not critical_components:
        critical_components = list(components)

    min_axis_separation = float("inf")
    occlusion_scores: List[float] = []
    for focal in critical_components:
        focal_center, focal_half = _component_box(focal)
        focal_sep = abs(float(focal_center[axis_id]) - center_value) - float(focal_half[axis_id])
        min_axis_separation = min(min_axis_separation, float(focal_sep))

        focal_near = float(focal_center[axis_id] - focal_half[axis_id])
        focal_far = float(focal_center[axis_id] + focal_half[axis_id])
        focal_min = min(focal_near, focal_far)
        focal_max = max(focal_near, focal_far)
        span = max(focal_max - focal_min, 1e-6)

        occlusion = 0.0
        for other in components:
            if other is focal:
                continue
            other_center, other_half = _component_box(other)
            near = float(other_center[axis_id] - other_half[axis_id])
            far = float(other_center[axis_id] + other_half[axis_id])
            seg_min = max(min(near, far), focal_min)
            seg_max = min(max(near, far), focal_max)
            axial_overlap = max(seg_max - seg_min, 0.0)
            if axial_overlap <= 0.0:
                continue
            overlap_ratio = _project_overlap_2d(
                focal_center,
                focal_half,
                other_center,
                other_half,
                axis_id,
            )
            if overlap_ratio <= 0.0:
                continue
            occlusion += float((axial_overlap / span) * overlap_ratio)
        occlusion_scores.append(float(np.clip(occlusion, 0.0, 1.0)))

    if not np.isfinite(min_axis_separation):
        min_axis_separation = 0.0
    fov_occlusion_ratio = float(np.clip(max(occlusion_scores) if occlusion_scores else 0.0, 0.0, 1.0))
    keepout_violation = max(min_sep_required - float(min_axis_separation), 0.0)
    mission_violation = float(keepout_violation + fov_occlusion_ratio * max(min_sep_required, 1.0))

    return {
        "mission_keepout_violation": float(mission_violation),
        "mission_keepout_min_separation": float(min_axis_separation),
        "fov_occlusion_proxy": float(fov_occlusion_ratio),
        "emc_separation_proxy": float(min_axis_separation),
        "mission_source": MISSION_SOURCE_FOV_REAL,
        "interface_status": "real_geometry_evaluator",
    }
