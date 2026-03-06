"""
Mission/FOV proxy interfaces.

Current runtime provides keepout-based proxy checks and explicit source labels.
This is not full high-fidelity FOV/EMC solving.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np

from simulation.contracts import (
    MISSION_SOURCE_FOV_REAL,
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
    proxy_payload["interface_status"] = "proxy_fallback"
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
