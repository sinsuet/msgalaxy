"""
Shared geometry metrics for runtime evaluation and seed services.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from core.protocol import DesignState
from geometry.catalog_geometry import resolve_catalog_component_specs


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _component_center_and_size(
    comp: object,
    *,
    catalog_specs: Optional[dict] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    position = np.asarray(
        [
            _safe_float(getattr(getattr(comp, "position", None), "x", 0.0)),
            _safe_float(getattr(getattr(comp, "position", None), "y", 0.0)),
            _safe_float(getattr(getattr(comp, "position", None), "z", 0.0)),
        ],
        dtype=float,
    )
    size = np.asarray(
        [
            max(_safe_float(getattr(getattr(comp, "dimensions", None), "x", 0.0)), 0.0),
            max(_safe_float(getattr(getattr(comp, "dimensions", None), "y", 0.0)), 0.0),
            max(_safe_float(getattr(getattr(comp, "dimensions", None), "z", 0.0)), 0.0),
        ],
        dtype=float,
    )

    comp_id = str(getattr(comp, "id", "") or "")
    if catalog_specs and comp_id in catalog_specs:
        try:
            proxy = catalog_specs[comp_id].resolved_proxy()
            proxy_size = np.asarray([_safe_float(value) for value in proxy.size_mm], dtype=float)
            if proxy_size.shape == (3,) and np.all(proxy_size > 0.0):
                size = proxy_size
            proxy_offset = np.asarray([_safe_float(value) for value in proxy.center_offset_mm], dtype=float)
            if proxy_offset.shape == (3,):
                position = position + proxy_offset
        except Exception:
            pass

    return position, size


def component_arrays(design_state: DesignState) -> Tuple[np.ndarray, np.ndarray]:
    catalog_specs = resolve_catalog_component_specs(design_state)
    centers = []
    half_sizes = []
    for comp in list(getattr(design_state, "components", []) or []):
        center, size = _component_center_and_size(comp, catalog_specs=catalog_specs)
        centers.append(center.tolist())
        half_sizes.append((size * 0.5).tolist())

    return (
        np.asarray(centers, dtype=float),
        np.asarray(half_sizes, dtype=float),
    )


def envelope_bounds(
    design_state: DesignState,
    *,
    prefer_inner: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    envelope = getattr(design_state, "envelope", None)
    if envelope is None:
        return np.zeros(3, dtype=float), np.zeros(3, dtype=float)

    size_source = getattr(envelope, "inner_size", None) if prefer_inner else None
    if size_source is None:
        size_source = getattr(envelope, "outer_size", None)
    if size_source is None:
        return np.zeros(3, dtype=float), np.zeros(3, dtype=float)

    size = np.asarray(
        [
            max(_safe_float(getattr(size_source, "x", 0.0)), 0.0),
            max(_safe_float(getattr(size_source, "y", 0.0)), 0.0),
            max(_safe_float(getattr(size_source, "z", 0.0)), 0.0),
        ],
        dtype=float,
    )

    if np.any(size <= 0.0) and prefer_inner:
        return envelope_bounds(design_state, prefer_inner=False)

    if str(getattr(envelope, "origin", "center") or "center").strip().lower() == "center":
        half = size * 0.5
        return -half, half

    return np.zeros(3, dtype=float), size


def calculate_pairwise_clearance(design_state: DesignState) -> tuple[float, int]:
    centers, half_sizes = component_arrays(design_state)
    if centers.shape[0] < 2:
        return float("inf"), 0

    axis_clearance = np.abs(centers[:, None, :] - centers[None, :, :]) - (
        half_sizes[:, None, :] + half_sizes[None, :, :]
    )
    pair_mask = np.triu(np.ones((centers.shape[0], centers.shape[0]), dtype=bool), k=1)
    pair_clearance = axis_clearance[pair_mask]
    if pair_clearance.size == 0:
        return float("inf"), 0

    overlap_mask = np.all(pair_clearance <= 0.0, axis=1)
    penetration_depth = np.min(-pair_clearance, axis=1)
    euclidean_gap = np.linalg.norm(np.maximum(pair_clearance, 0.0), axis=1)
    signed_clearance = np.where(overlap_mask, -penetration_depth, euclidean_gap)

    return float(np.min(signed_clearance)), int(np.count_nonzero(overlap_mask))


def calculate_boundary_violation(design_state: DesignState) -> float:
    env_min, env_max = envelope_bounds(design_state, prefer_inner=False)
    centers, half_sizes = component_arrays(design_state)
    if centers.size == 0:
        return 0.0

    lower_excess = (env_min.reshape(1, 3) + half_sizes) - centers
    upper_excess = centers - (env_max.reshape(1, 3) - half_sizes)
    overflow = np.maximum(np.maximum(lower_excess, upper_excess), 0.0)
    if overflow.size == 0:
        return 0.0
    return float(np.max(overflow))


def calculate_component_volume_sum(design_state: DesignState) -> float:
    catalog_specs = resolve_catalog_component_specs(design_state)
    total_volume = 0.0
    for comp in list(getattr(design_state, "components", []) or []):
        _, size = _component_center_and_size(comp, catalog_specs=catalog_specs)
        dx, dy, dz = size.tolist()
        total_volume += dx * dy * dz
    return float(total_volume)


def calculate_packing_efficiency(
    design_state: DesignState,
    *,
    prefer_inner_envelope: bool = True,
) -> float:
    env_min, env_max = envelope_bounds(design_state, prefer_inner=prefer_inner_envelope)
    envelope_size = np.maximum(env_max - env_min, 0.0)
    envelope_volume = float(np.prod(envelope_size))
    if envelope_volume <= 1e-9:
        return 0.0

    component_volume = calculate_component_volume_sum(design_state)
    return float(component_volume / envelope_volume * 100.0)


def summarize_geometry_state(design_state: DesignState) -> Dict[str, float]:
    min_clearance, num_collisions = calculate_pairwise_clearance(design_state)
    return {
        "min_clearance": float(min_clearance),
        "num_collisions": float(num_collisions),
        "boundary_violation": float(calculate_boundary_violation(design_state)),
        "packing_efficiency": float(calculate_packing_efficiency(design_state)),
        "component_volume_sum": float(calculate_component_volume_sum(design_state)),
    }
