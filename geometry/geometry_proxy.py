from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class GeometryProxySpec(BaseModel):
    """Lightweight geometry used for collision, clearance, and envelope checks."""

    kind: str = "aabb"
    size_mm: Tuple[float, float, float]
    center_offset_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    mount_faces: List[str] = Field(default_factory=list)
    functional_axis: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def normalized_kind(self) -> str:
        return str(self.kind or "aabb").strip().lower()

    @classmethod
    def from_profile(
        cls,
        profile: Any,
        *,
        mount_faces: Optional[List[str]] = None,
        functional_axis: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "GeometryProxySpec":
        size_mm = (0.0, 0.0, 0.0)
        if hasattr(profile, "approximate_size_mm"):
            size_mm = tuple(float(value) for value in profile.approximate_size_mm())
        return cls(
            kind="aabb",
            size_mm=size_mm,
            mount_faces=list(mount_faces or []),
            functional_axis=functional_axis,
            metadata=dict(metadata or {}),
        )


def _safe_float_tuple(values: Any, length: int = 3) -> Tuple[float, ...]:
    if values is None:
        return tuple(0.0 for _ in range(length))
    return tuple(float(values[index]) for index in range(length))


def _interior_keepout_entry(
    *,
    shell_id: str,
    geometry_kind: str,
    quadrant: str,
    slice_index: int,
    min_mm: Tuple[float, float, float],
    max_mm: Tuple[float, float, float],
    inner_radius_mm: float,
) -> Dict[str, Any]:
    size_mm = (
        max(float(max_mm[0]) - float(min_mm[0]), 0.0),
        max(float(max_mm[1]) - float(min_mm[1]), 0.0),
        max(float(max_mm[2]) - float(min_mm[2]), 0.0),
    )
    center_mm = (
        (float(min_mm[0]) + float(max_mm[0])) / 2.0,
        (float(min_mm[1]) + float(max_mm[1])) / 2.0,
        (float(min_mm[2]) + float(max_mm[2])) / 2.0,
    )
    return {
        "id": f"{shell_id}:interior:{slice_index}:{quadrant}",
        "role": "shell_interior_proxy",
        "proxy_kind": "aabb_keepout",
        "geometry_kind": geometry_kind,
        "quadrant": quadrant,
        "slice_index": int(slice_index),
        "size_mm": list(size_mm),
        "center_mm": list(center_mm),
        "min_mm": list(min_mm),
        "max_mm": list(max_mm),
        "inner_radius_mm": float(inner_radius_mm),
        "approx_method": "inscribed_square_corner_keepout",
    }


def shell_interior_proxy_entries_from_shell_spec(shell_spec: Any) -> List[Dict[str, Any]]:
    outer_kind = str(getattr(getattr(shell_spec, "outer_profile", None), "normalized_kind", lambda: "box")()).strip().lower()
    thickness = float(getattr(shell_spec, "thickness_mm", 0.0) or 0.0)
    outer_size = tuple(float(value) for value in shell_spec.outer_size_mm())
    if thickness <= 0.0 or outer_kind not in {"cylinder", "frustum"}:
        return []

    shell_id = str(getattr(shell_spec, "shell_id", "shell") or "shell")
    inner_half_x = max(outer_size[0] / 2.0 - thickness, 0.0)
    inner_half_y = max(outer_size[1] / 2.0 - thickness, 0.0)
    inner_height = max(outer_size[2] - 2.0 * thickness, 0.0)
    if min(inner_half_x, inner_half_y, inner_height) <= 0.0:
        return []

    if outer_kind == "cylinder":
        outer_radius = float(getattr(shell_spec.outer_profile, "radius_mm", None) or min(outer_size[0], outer_size[1]) / 2.0)
        inner_radius = max(outer_radius - thickness, 0.0)
        slice_bounds = [(-inner_height / 2.0, inner_height / 2.0)]

        def radius_min_for_slice(_z0: float, _z1: float) -> float:
            return inner_radius
    else:
        num_slices = max(1, int(dict(getattr(shell_spec, "metadata", {}) or {}).get("proxy_shell_slices", 4)))
        step = inner_height / num_slices
        slice_bounds = [
            (-inner_height / 2.0 + index * step, -inner_height / 2.0 + (index + 1) * step)
            for index in range(num_slices)
        ]
        inner_bottom_radius = max(float(getattr(shell_spec.outer_profile, "bottom_radius_mm", 0.0) or 0.0) - thickness, 0.0)
        inner_top_radius = max(float(getattr(shell_spec.outer_profile, "top_radius_mm", inner_bottom_radius) or inner_bottom_radius) - thickness, 0.0)
        inner_height_for_radius = max(float(getattr(shell_spec.outer_profile, "height_mm", outer_size[2]) or outer_size[2]) - 2.0 * thickness, 0.0)
        if min(inner_bottom_radius, inner_top_radius, inner_height_for_radius) <= 0.0:
            return []

        def radius_min_for_slice(z0: float, z1: float) -> float:
            def inner_frustum_radius(z_mm: float) -> float:
                if inner_height_for_radius <= 1e-9:
                    return min(inner_bottom_radius, inner_top_radius)
                z_clamped = max(-inner_height_for_radius / 2.0, min(inner_height_for_radius / 2.0, z_mm))
                alpha = (z_clamped + inner_height_for_radius / 2.0) / inner_height_for_radius
                return inner_bottom_radius + (inner_top_radius - inner_bottom_radius) * alpha

            return min(inner_frustum_radius(z0), inner_frustum_radius(z1))

    entries: List[Dict[str, Any]] = []
    quadrant_specs = [
        ("px_py", (1.0, 1.0)),
        ("px_ny", (1.0, -1.0)),
        ("nx_py", (-1.0, 1.0)),
        ("nx_ny", (-1.0, -1.0)),
    ]
    for slice_index, (z_min, z_max) in enumerate(slice_bounds):
        inner_radius = radius_min_for_slice(float(z_min), float(z_max))
        square_half = inner_radius / math.sqrt(2.0)
        if square_half <= 0.0 or square_half >= min(inner_half_x, inner_half_y):
            continue
        for quadrant, (sign_x, sign_y) in quadrant_specs:
            if sign_x > 0:
                x_min, x_max = square_half, inner_half_x
            else:
                x_min, x_max = -inner_half_x, -square_half
            if sign_y > 0:
                y_min, y_max = square_half, inner_half_y
            else:
                y_min, y_max = -inner_half_y, -square_half
            entries.append(
                _interior_keepout_entry(
                    shell_id=shell_id,
                    geometry_kind=outer_kind,
                    quadrant=quadrant,
                    slice_index=slice_index,
                    min_mm=(x_min, y_min, z_min),
                    max_mm=(x_max, y_max, z_max),
                    inner_radius_mm=inner_radius,
                )
            )
    return entries


def shell_interior_proxy_entries(design_state: Any) -> List[Dict[str, Any]]:
    from .shell_spec import resolve_shell_spec

    shell_spec = resolve_shell_spec(design_state)
    if shell_spec is None:
        return []
    return shell_interior_proxy_entries_from_shell_spec(shell_spec)


def component_proxy_entries(design_state: Any) -> List[Dict[str, Any]]:
    from .catalog_geometry import resolve_catalog_component_specs

    specs = resolve_catalog_component_specs(design_state)
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    truth_index = dict(metadata.get("resolved_geometry_truth", {}) or {})
    entries: List[Dict[str, Any]] = []
    for comp in list(getattr(design_state, "components", []) or []):
        spec = specs.get(str(getattr(comp, "id", "") or ""))
        if spec is None:
            continue
        resolved_truth = dict(truth_index.get(str(comp.id), {}) or {})
        if not resolved_truth:
            rotation_deg = (
                float(getattr(getattr(comp, "rotation", None), "x", 0.0)),
                float(getattr(getattr(comp, "rotation", None), "y", 0.0)),
                float(getattr(getattr(comp, "rotation", None), "z", 0.0)),
            )
            resolved_truth = spec.resolved_geometry_truth(rotation_deg=rotation_deg).model_dump()
        proxy = spec.resolved_proxy(
            rotation_deg=tuple(resolved_truth.get("rotation_deg", (0.0, 0.0, 0.0))),
        )
        center = (
            float(getattr(getattr(comp, "position", None), "x", 0.0)) + float(proxy.center_offset_mm[0]),
            float(getattr(getattr(comp, "position", None), "y", 0.0)) + float(proxy.center_offset_mm[1]),
            float(getattr(getattr(comp, "position", None), "z", 0.0)) + float(proxy.center_offset_mm[2]),
        )
        entries.append(
            {
                "id": str(comp.id),
                "role": "component_proxy",
                "proxy_kind": proxy.normalized_kind(),
                "size_mm": list(_safe_float_tuple(proxy.size_mm)),
                "center_mm": list(center),
                "effective_bbox_size_mm": list(_safe_float_tuple(resolved_truth.get("effective_bbox_size_mm"))),
                "effective_bbox_center_offset_mm": list(
                    _safe_float_tuple(resolved_truth.get("effective_bbox_center_offset_mm"))
                ),
                "local_bbox_size_mm": list(_safe_float_tuple(resolved_truth.get("local_bbox_size_mm"))),
                "local_bbox_center_offset_mm": list(
                    _safe_float_tuple(resolved_truth.get("local_bbox_center_offset_mm"))
                ),
                "rotation_deg": list(_safe_float_tuple(resolved_truth.get("rotation_deg"))),
                "mount_faces": list(proxy.mount_faces or []),
                "functional_axis": proxy.functional_axis,
                "position_semantics": str(resolved_truth.get("position_semantics", "effective_bbox_center") or "effective_bbox_center"),
            }
        )
    return entries


def shell_proxy_entries(design_state: Any) -> List[Dict[str, Any]]:
    from .shell_spec import aperture_proxy_plans, plan_box_panel_variant, resolve_shell_spec

    shell_spec = resolve_shell_spec(design_state)
    if shell_spec is None:
        return []

    entries: List[Dict[str, Any]] = []
    outer_kind = shell_spec.outer_profile.normalized_kind()
    outer_size = tuple(float(value) for value in shell_spec.outer_size_mm())
    thickness = float(shell_spec.thickness_mm or 0.0)
    inner_size = (
        max(outer_size[0] - 2.0 * thickness, 0.0),
        max(outer_size[1] - 2.0 * thickness, 0.0),
        max(outer_size[2] - 2.0 * thickness, 0.0),
    )
    entries.append(
        {
            "id": shell_spec.shell_id,
            "role": "shell_proxy",
            "proxy_kind": f"{outer_kind}_shell",
            "geometry_kind": outer_kind,
            "outer_size_mm": list(outer_size),
            "inner_size_mm": list(inner_size),
            "thickness_mm": thickness,
        }
    )
    if outer_kind == "cylinder":
        outer_radius = float(shell_spec.outer_profile.radius_mm or min(outer_size[0], outer_size[1]) / 2.0)
        outer_height = float(shell_spec.outer_profile.height_mm or outer_size[2])
        entries[0]["outer_radius_mm"] = outer_radius
        entries[0]["inner_radius_mm"] = max(outer_radius - thickness, 0.0)
        entries[0]["height_mm"] = outer_height
    elif outer_kind == "frustum":
        outer_bottom_radius = float(shell_spec.outer_profile.bottom_radius_mm or min(outer_size[0], outer_size[1]) / 2.0)
        outer_top_radius = float(shell_spec.outer_profile.top_radius_mm or outer_bottom_radius)
        outer_height = float(shell_spec.outer_profile.height_mm or outer_size[2])
        entries[0]["outer_bottom_radius_mm"] = outer_bottom_radius
        entries[0]["outer_top_radius_mm"] = outer_top_radius
        entries[0]["inner_bottom_radius_mm"] = max(outer_bottom_radius - thickness, 0.0)
        entries[0]["inner_top_radius_mm"] = max(outer_top_radius - thickness, 0.0)
        entries[0]["height_mm"] = outer_height
    entries.extend(shell_interior_proxy_entries_from_shell_spec(shell_spec))
    for panel in shell_spec.resolved_panels():
        variant_plan = plan_box_panel_variant(shell_spec=shell_spec, panel=panel)
        if variant_plan is None:
            continue
        entries.append(
            {
                "id": f"{panel.panel_id}:{variant_plan['variant_id']}",
                "role": "panel_variant_proxy",
                "proxy_kind": "aabb_pad",
                "panel_id": panel.panel_id,
                "panel_face": variant_plan["panel_face"],
                "variant_id": variant_plan["variant_id"],
                "variant_kind": variant_plan["variant_kind"],
                "profile_kind": variant_plan.get("profile_kind"),
                "size_mm": list(_safe_float_tuple(variant_plan["size_mm"])),
                "local_size_mm": list(_safe_float_tuple(variant_plan.get("local_size_mm", variant_plan["size_mm"]))),
                "center_mm": list(_safe_float_tuple(variant_plan["center_mm"])),
            }
        )
    for plan in aperture_proxy_plans(shell_spec):
        entries.append(
            {
                "id": plan["aperture_id"],
                "role": "aperture_proxy",
                "proxy_kind": "aabb_zone",
                "panel_id": plan["panel_id"],
                "panel_face": plan["panel_face"],
                "shape_kind": plan["shape_kind"],
                "size_mm": list(_safe_float_tuple(plan["size_mm"])),
                "center_mm": list(_safe_float_tuple(plan["center_mm"])),
            }
        )
    return entries


def build_geometry_proxy_manifest(design_state: Any) -> Dict[str, Any]:
    entries = []
    entries.extend(shell_proxy_entries(design_state))
    entries.extend(component_proxy_entries(design_state))
    return {
        "manifest_version": "catalog-shell-geometry-proxy-v1",
        "entries": entries,
    }
