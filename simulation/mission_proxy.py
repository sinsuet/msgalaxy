"""
Mission/FOV proxy interfaces.

Current runtime provides keepout-based proxy checks and explicit source labels.
This is not full high-fidelity FOV/EMC solving.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np

from geometry.shell_spec import aperture_proxy_plans, resolve_shell_spec
from simulation.contracts import (
    MISSION_SOURCE_FOV_REAL,
    MISSION_SOURCE_FOV_PROXY,
    MISSION_SOURCE_KEEP_OUT_ALIAS,
    MISSION_SOURCE_UNAVAILABLE,
)


def _axis_index(axis: str) -> int:
    text = str(axis or "z").strip().lower()
    if text == "x":
        return 0
    if text == "y":
        return 1
    return 2


def _component_box(comp: Any) -> tuple[np.ndarray, np.ndarray]:
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


def _signed_aabb_separation(
    center_a: np.ndarray,
    half_a: np.ndarray,
    center_b: np.ndarray,
    half_b: np.ndarray,
) -> float:
    delta = np.abs(np.asarray(center_a, dtype=float) - np.asarray(center_b, dtype=float))
    combined = np.asarray(half_a, dtype=float) + np.asarray(half_b, dtype=float)
    gaps = delta - combined
    positive_gaps = np.maximum(gaps, 0.0)
    if np.any(positive_gaps > 0.0):
        return float(np.linalg.norm(positive_gaps))
    penetration = combined - delta
    return -float(np.min(penetration))


def _placement_index(design_state: Any) -> Dict[str, Dict[str, Any]]:
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    placements = list(metadata.get("placement_state", []) or [])
    indexed: Dict[str, Dict[str, Any]] = {}
    for payload in placements:
        mapping = dict(payload or {})
        component_id = str(mapping.get("instance_id", "") or "").strip()
        if component_id:
            indexed[component_id] = mapping
    return indexed


def _evaluate_shell_aperture_keepout_proxy(
    design_state: Any,
    *,
    min_separation_mm: float,
) -> Optional[Dict[str, float]]:
    shell_spec = resolve_shell_spec(design_state)
    if shell_spec is None:
        return None

    plans = aperture_proxy_plans(shell_spec)
    if not plans:
        return None

    placement_index = _placement_index(design_state)
    aperture_index = {
        str(aperture.aperture_id): aperture
        for aperture in list(getattr(shell_spec, "aperture_sites", []) or [])
    }
    components = list(getattr(design_state, "components", []) or [])

    best_sep = float("inf")
    has_checked_component = False
    has_occlusion = False
    for plan in plans:
        aperture_id = str(plan.get("aperture_id", "") or "").strip()
        aperture_spec = aperture_index.get(aperture_id)
        exempt_ids = {
            comp_id
            for comp_id, placement in placement_index.items()
            if str(placement.get("aperture_site", "") or "").strip() == aperture_id
        }
        allowed_families = {
            str(family or "").strip().lower()
            for family in list(getattr(aperture_spec, "allowed_component_families", []) or [])
            if str(family or "").strip()
        }
        keepout_center = np.asarray(plan.get("center_mm", (0.0, 0.0, 0.0)), dtype=float)
        keepout_half = 0.5 * np.asarray(plan.get("size_mm", (0.0, 0.0, 0.0)), dtype=float)
        for comp in components:
            comp_id = str(getattr(comp, "id", "") or "").strip()
            if not comp_id or comp_id in exempt_ids:
                continue
            if allowed_families and str(getattr(comp, "category", "") or "").strip().lower() in allowed_families:
                continue
            center, half = _component_box(comp)
            signed_sep = _signed_aabb_separation(center, half, keepout_center, keepout_half)
            best_sep = min(best_sep, float(signed_sep))
            has_checked_component = True
            has_occlusion = has_occlusion or float(signed_sep) < 0.0

    if not has_checked_component:
        best_sep = max(float(min_separation_mm), 0.0)

    violation = max(float(min_separation_mm) - float(best_sep), 0.0)
    return {
        "mission_keepout_violation": float(violation),
        "mission_keepout_min_separation": float(best_sep),
        "fov_occlusion_proxy": float(1.0 if has_occlusion else 0.0),
        "emc_separation_proxy": float(best_sep),
        "mission_source": MISSION_SOURCE_FOV_PROXY,
        "interface_status": "shell_aperture_proxy",
    }


def evaluate_mission_keepout_proxy(
    design_state,
    *,
    axis: str = "z",
    keepout_center_mm: float = 0.0,
    min_separation_mm: float = 0.0,
) -> Dict[str, float]:
    """
    Keepout proxy evaluator used by current mission action family.
    """
    aperture_payload = _evaluate_shell_aperture_keepout_proxy(
        design_state,
        min_separation_mm=float(min_separation_mm),
    )
    if aperture_payload is not None:
        return aperture_payload

    axis_id = _axis_index(axis)
    center = float(keepout_center_mm)
    min_sep = max(float(min_separation_mm), 0.0)

    best_sep = float("inf")
    for comp in list(getattr(design_state, "components", []) or []):
        pos = getattr(comp, "position", None)
        dim = getattr(comp, "dimensions", None)
        if pos is None or dim is None:
            continue
        coords = np.asarray(
            [
                float(getattr(pos, "x", 0.0)),
                float(getattr(pos, "y", 0.0)),
                float(getattr(pos, "z", 0.0)),
            ],
            dtype=float,
        )
        dims = np.asarray(
            [
                float(getattr(dim, "x", 0.0)),
                float(getattr(dim, "y", 0.0)),
                float(getattr(dim, "z", 0.0)),
            ],
            dtype=float,
        )
        signed = abs(float(coords[axis_id]) - center) - float(dims[axis_id]) * 0.5
        best_sep = min(best_sep, float(signed))

    if not np.isfinite(best_sep):
        best_sep = 0.0

    violation = max(min_sep - float(best_sep), 0.0)
    return {
        "mission_keepout_violation": float(violation),
        "mission_keepout_min_separation": float(best_sep),
        "fov_occlusion_proxy": float(violation),
        "emc_separation_proxy": float(best_sep),
        "mission_source": MISSION_SOURCE_KEEP_OUT_ALIAS,
        "interface_status": "axis_plane_keepout_proxy",
    }


def evaluate_mission_fov_interface(
    design_state,
    *,
    evaluator: Optional[Callable[..., Dict[str, Any]]] = None,
    axis: str = "z",
    keepout_center_mm: float = 0.0,
    min_separation_mm: float = 0.0,
    require_real: bool = False,
) -> Dict[str, Any]:
    """
    Unified mission/FOV evaluation interface.

    - If `evaluator` is callable, use it as primary path.
    - Otherwise fallback to keepout proxy alias unless `require_real=True`.
    """
    if callable(evaluator):
        try:
            payload = dict(
                evaluator(
                    design_state=design_state,
                    axis=str(axis),
                    keepout_center_mm=float(keepout_center_mm),
                    min_separation_mm=float(min_separation_mm),
                )
                or {}
            )
            payload.setdefault("mission_source", MISSION_SOURCE_FOV_REAL)
            payload.setdefault("interface_status", "external_evaluator")
            return payload
        except Exception as exc:
            if bool(require_real):
                return unavailable_mission_interface(
                    violation=max(float(min_separation_mm), 1e6),
                    interface_status="real_evaluator_failed",
                    error=str(exc),
                )

    if bool(require_real):
        return unavailable_mission_interface(
            violation=max(float(min_separation_mm), 1e6),
            interface_status="real_required_missing",
            error="mission_fov_evaluator_unavailable",
        )

    proxy_payload = evaluate_mission_keepout_proxy(
        design_state,
        axis=str(axis),
        keepout_center_mm=float(keepout_center_mm),
        min_separation_mm=float(min_separation_mm),
    )
    proxy_payload.setdefault("interface_status", "proxy_fallback")
    return proxy_payload


def unavailable_mission_interface(
    *,
    violation: float = 0.0,
    interface_status: str = "unavailable",
    error: str = "",
) -> Dict[str, Any]:
    penalty = max(float(violation), 0.0)
    return {
        "mission_keepout_violation": float(penalty),
        "mission_keepout_min_separation": 0.0,
        "fov_occlusion_proxy": float(penalty),
        "emc_separation_proxy": 0.0,
        "mission_source": MISSION_SOURCE_UNAVAILABLE,
        "interface_status": str(interface_status or "unavailable"),
        "error": str(error or ""),
    }
