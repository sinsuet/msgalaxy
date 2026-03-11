from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from .catalog_geometry import GeometryProfileSpec, PROFILE_KIND_BOX, PROFILE_KIND_CYLINDER, PROFILE_KIND_FRUSTUM

BOX_PANEL_FACES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
CYLINDER_DEFAULT_PANEL_FACES = ("+Z", "-Z")
CYLINDER_SUPPORTED_PANEL_FACES = BOX_PANEL_FACES
FRUSTUM_DEFAULT_PANEL_FACES = ("+Z", "-Z")
FRUSTUM_SUPPORTED_PANEL_FACES = BOX_PANEL_FACES
DEFAULT_SHELL_SPEC_DIR = Path(__file__).resolve().parent.parent / "config" / "catalog_components"


def _model_validate(model_cls: Any, payload: Any) -> Any:
    validator = getattr(model_cls, "model_validate", None)
    if callable(validator):
        return validator(payload)
    return model_cls.parse_obj(payload)


class ApertureSiteSpec(BaseModel):
    """A predefined shell cutout site that is activated at STEP generation time."""

    aperture_id: str
    panel_id: str
    shape: str = "rectangular_cutout"
    center_mm: Tuple[float, float] = (0.0, 0.0)
    size_mm: Tuple[float, float]
    profile_points_mm: List[Tuple[float, float]] = Field(default_factory=list)
    depth_mm: Optional[float] = None
    proxy_depth_mm: Optional[float] = None
    through_shell: bool = True
    allowed_component_families: List[str] = Field(default_factory=list)
    enabled: bool = True
    impact_tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def normalized_shape(self) -> str:
        return str(self.shape or "rectangular_cutout").strip().lower()


class PanelSpec(BaseModel):
    """A shell panel face that owns zero or more aperture sites."""

    panel_id: str
    face: str
    span_mm: Optional[Tuple[float, float]] = None
    replaceable: bool = False
    active_variant: Optional[str] = None
    surface_semantics: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def normalized_face(self) -> str:
        face = str(self.face or "").strip().upper()
        if face not in BOX_PANEL_FACES:
            return "+Z"
        return face


class ShellSpec(BaseModel):
    """Formal shell contract for geometry, panel ownership, and aperture sites."""

    shell_id: str = "shell"
    outer_profile: GeometryProfileSpec
    thickness_mm: float
    panels: List[PanelSpec] = Field(default_factory=list)
    aperture_sites: List[ApertureSiteSpec] = Field(default_factory=list)
    material: Optional[str] = None
    mount_base_face: Optional[str] = None
    surface_semantics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def outer_size_mm(self) -> Tuple[float, float, float]:
        return self.outer_profile.approximate_size_mm()

    def resolved_panels(self) -> List[PanelSpec]:
        if self.panels:
            return self.panels
        outer_kind = self.outer_profile.normalized_kind()
        if outer_kind == PROFILE_KIND_BOX:
            return build_box_panels(self.outer_size_mm())
        if outer_kind == PROFILE_KIND_CYLINDER:
            return build_cylinder_panels(self.outer_size_mm())
        if outer_kind == PROFILE_KIND_FRUSTUM:
            return build_frustum_panels(self.outer_profile)
        return []

    def panel_index(self) -> Dict[str, PanelSpec]:
        return {panel.panel_id: panel for panel in self.resolved_panels()}


def build_box_panels(outer_size_mm: Tuple[float, float, float]) -> List[PanelSpec]:
    size_x, size_y, size_z = (float(value) for value in outer_size_mm)
    spans = {
        "+X": (size_y, size_z),
        "-X": (size_y, size_z),
        "+Y": (size_x, size_z),
        "-Y": (size_x, size_z),
        "+Z": (size_x, size_y),
        "-Z": (size_x, size_y),
    }
    return [
        PanelSpec(
            panel_id=f"panel_{face.replace('+', 'p').replace('-', 'n').lower()}",
            face=face,
            span_mm=spans[face],
            metadata={"source": "generated_default_box_panel"},
        )
        for face in BOX_PANEL_FACES
    ]


def build_cylinder_panels(outer_size_mm: Tuple[float, float, float]) -> List[PanelSpec]:
    diameter_x, diameter_y, _size_z = (float(value) for value in outer_size_mm)
    diameter = min(diameter_x, diameter_y)
    spans = {
        "+Z": (diameter, diameter),
        "-Z": (diameter, diameter),
    }
    return [
        PanelSpec(
            panel_id=f"panel_{face.replace('+', 'p').replace('-', 'n').lower()}",
            face=face,
            span_mm=spans[face],
            metadata={"source": "generated_default_cylinder_endcap_panel"},
        )
        for face in CYLINDER_DEFAULT_PANEL_FACES
    ]


def build_frustum_panels(outer_profile: GeometryProfileSpec) -> List[PanelSpec]:
    bottom_radius = float(outer_profile.bottom_radius_mm or 0.0)
    top_radius = float(outer_profile.top_radius_mm or bottom_radius)
    if bottom_radius <= 0.0 and outer_profile.size_mm is not None:
        approx_size = outer_profile.approximate_size_mm()
        bottom_radius = max(float(approx_size[0]), float(approx_size[1])) / 2.0
        top_radius = bottom_radius
    spans = {
        "+Z": (top_radius * 2.0, top_radius * 2.0),
        "-Z": (bottom_radius * 2.0, bottom_radius * 2.0),
    }
    return [
        PanelSpec(
            panel_id=f"panel_{face.replace('+', 'p').replace('-', 'n').lower()}",
            face=face,
            span_mm=spans[face],
            metadata={"source": "generated_default_frustum_endcap_panel"},
        )
        for face in FRUSTUM_DEFAULT_PANEL_FACES
    ]


def resolve_frustum_radius_mm(shell_spec: ShellSpec, z_mm: float) -> float:
    profile = shell_spec.outer_profile
    bottom_radius = float(profile.bottom_radius_mm or 0.0)
    top_radius = float(profile.top_radius_mm or bottom_radius)
    height = float(profile.height_mm or shell_spec.outer_size_mm()[2])
    if height <= 1e-9:
        return max(bottom_radius, top_radius)
    z_clamped = max(-height / 2.0, min(height / 2.0, float(z_mm)))
    alpha = (z_clamped + height / 2.0) / height
    return bottom_radius + (top_radius - bottom_radius) * alpha


def load_shell_spec(path: str | Path) -> ShellSpec:
    shell = _model_validate(ShellSpec, json.loads(Path(path).read_text(encoding="utf-8")))
    if not shell.panels:
        shell.panels = shell.resolved_panels()
    return shell


def resolve_shell_spec_from_mapping(payload: Mapping[str, Any]) -> Optional[ShellSpec]:
    mapping = dict(payload or {})
    raw_spec = mapping.get("shell_spec")
    if raw_spec:
        if isinstance(raw_spec, (str, Path)):
            return load_shell_spec(Path(str(raw_spec)))
        spec = raw_spec if isinstance(raw_spec, ShellSpec) else _model_validate(ShellSpec, raw_spec)
        if not spec.panels:
            spec.panels = spec.resolved_panels()
        return spec
    shell_spec_path = mapping.get("shell_spec_path")
    if shell_spec_path:
        return load_shell_spec(Path(str(shell_spec_path)))
    shell_spec_file = mapping.get("shell_spec_file")
    if shell_spec_file:
        return load_shell_spec(DEFAULT_SHELL_SPEC_DIR / str(shell_spec_file))
    return None


def shell_spec_from_legacy_design_state(design_state: Any) -> Optional[ShellSpec]:
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    shell_meta = dict(metadata.get("shell", {}) or {})
    enabled = bool(shell_meta.get("enabled", False))
    envelope = getattr(design_state, "envelope", None)
    if not enabled or envelope is None:
        return None

    outer = getattr(envelope, "outer_size", None)
    if outer is None:
        return None

    size_mm = (
        float(getattr(outer, "x", 0.0) or 0.0),
        float(getattr(outer, "y", 0.0) or 0.0),
        float(getattr(outer, "z", 0.0) or 0.0),
    )
    thickness_mm = float(shell_meta.get("thickness_mm", getattr(envelope, "thickness", 0.0)) or 0.0)
    if min(size_mm) <= 0.0 or thickness_mm <= 0.0:
        return None

    panels_payload = list(shell_meta.get("panels", []) or [])
    if panels_payload:
        panels = [_model_validate(PanelSpec, payload) for payload in panels_payload]
    else:
        panels = build_box_panels(size_mm)

    apertures_payload = list(shell_meta.get("aperture_sites", shell_meta.get("apertures", [])) or [])
    apertures = [_model_validate(ApertureSiteSpec, payload) for payload in apertures_payload]

    return ShellSpec(
        shell_id=str(shell_meta.get("shell_id", "legacy_shell")),
        outer_profile=GeometryProfileSpec(
            kind=PROFILE_KIND_BOX,
            size_mm=size_mm,
            metadata={"source": "legacy_envelope"},
        ),
        thickness_mm=thickness_mm,
        panels=panels,
        aperture_sites=apertures,
        material=shell_meta.get("material"),
        mount_base_face=shell_meta.get("mount_base_face"),
        surface_semantics=dict(shell_meta.get("surface_semantics", {}) or {}),
        metadata={"source": "legacy_design_state_shell"},
    )


def resolve_shell_spec(design_state: Any) -> Optional[ShellSpec]:
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    resolved = resolve_shell_spec_from_mapping(metadata)
    if resolved is not None:
        return resolved
    return shell_spec_from_legacy_design_state(design_state)


def resolve_panel_span_mm(shell_spec: ShellSpec, panel: PanelSpec) -> Tuple[float, float]:
    if panel.span_mm is not None:
        return float(panel.span_mm[0]), float(panel.span_mm[1])
    outer_x, outer_y, outer_z = shell_spec.outer_size_mm()
    outer_kind = shell_spec.outer_profile.normalized_kind()
    face = panel.normalized_face()
    if outer_kind == PROFILE_KIND_CYLINDER and face in CYLINDER_SUPPORTED_PANEL_FACES:
        diameter = min(outer_x, outer_y)
        if face in {"+X", "-X"}:
            return diameter, outer_z
        if face in {"+Y", "-Y"}:
            return diameter, outer_z
        return diameter, diameter
    if outer_kind == PROFILE_KIND_FRUSTUM and face in FRUSTUM_SUPPORTED_PANEL_FACES:
        if face == "+Z":
            top_radius = float(shell_spec.outer_profile.top_radius_mm or shell_spec.outer_profile.bottom_radius_mm or 0.0)
            diameter = top_radius * 2.0
            return diameter, diameter
        if face == "-Z":
            bottom_radius = float(shell_spec.outer_profile.bottom_radius_mm or shell_spec.outer_profile.top_radius_mm or 0.0)
            diameter = bottom_radius * 2.0
            return diameter, diameter
        max_diameter = max(
            float(shell_spec.outer_profile.bottom_radius_mm or 0.0),
            float(shell_spec.outer_profile.top_radius_mm or 0.0),
        ) * 2.0
        return max_diameter, outer_z
    if face in {"+X", "-X"}:
        return outer_y, outer_z
    if face in {"+Y", "-Y"}:
        return outer_x, outer_z
    return outer_x, outer_y


def resolve_panel_variant_payload(panel: PanelSpec) -> Optional[Dict[str, Any]]:
    active_variant = str(panel.active_variant or "").strip()
    if not active_variant:
        return None
    metadata = dict(panel.metadata or {})
    variant_definitions = dict(metadata.get("variant_definitions", {}) or {})
    payload = variant_definitions.get(active_variant)
    if payload is None:
        return None
    resolved = dict(payload or {})
    resolved["variant_id"] = active_variant
    return resolved


def orient_local_size_to_panel_face(
    local_size_mm: Tuple[float, float, float],
    panel_face: str,
) -> Tuple[float, float, float]:
    size_x, size_y, size_z = (float(value) for value in local_size_mm)
    face = str(panel_face or "").strip().upper()
    if face in {"+X", "-X"}:
        return size_z, size_x, size_y
    if face in {"+Y", "-Y"}:
        return size_x, size_z, size_y
    return size_x, size_y, size_z


def resolve_panel_variant_profile(
    variant: Mapping[str, Any],
) -> Optional[Tuple[GeometryProfileSpec, str]]:
    profile_payload = variant.get("profile") or variant.get("geometry_profile")
    if profile_payload is not None:
        profile = profile_payload if isinstance(profile_payload, GeometryProfileSpec) else _model_validate(GeometryProfileSpec, profile_payload)
        variant_kind = str(variant.get("kind") or f"{profile.normalized_kind()}_pad").strip().lower()
        return profile, variant_kind

    variant_kind = str(variant.get("kind") or "box_pad").strip().lower()
    if variant_kind != "box_pad":
        return None

    center_mm = tuple(float(value) for value in variant.get("center_mm", (0.0, 0.0)))
    size_u, size_v = tuple(float(value) for value in variant.get("size_mm", (0.0, 0.0)))
    depth = max(float(variant.get("depth_mm", 0.0) or 0.0), 1e-3)
    profile = GeometryProfileSpec(
        kind=PROFILE_KIND_BOX,
        size_mm=(size_u, size_v, depth),
        metadata={
            "source": "legacy_box_pad_variant",
            "panel_center_mm": center_mm,
        },
    )
    return profile, variant_kind


def plan_box_panel_variant(
    *,
    shell_spec: ShellSpec,
    panel: PanelSpec,
) -> Optional[Dict[str, Any]]:
    outer_kind = shell_spec.outer_profile.normalized_kind()
    if outer_kind not in {PROFILE_KIND_BOX, PROFILE_KIND_CYLINDER, PROFILE_KIND_FRUSTUM}:
        return None

    variant = resolve_panel_variant_payload(panel)
    if variant is None:
        return None

    profile_resolved = resolve_panel_variant_profile(variant)
    if profile_resolved is None:
        return None
    profile, variant_kind = profile_resolved

    outer_x, outer_y, outer_z = shell_spec.outer_size_mm()
    span_u, span_v = resolve_panel_span_mm(shell_spec, panel)
    center_u, center_v = tuple(float(value) for value in variant.get("center_mm", (0.0, 0.0)))
    local_size_mm = tuple(float(value) for value in profile.approximate_size_mm())
    size_u = max(local_size_mm[0], 1e-3)
    size_v = max(local_size_mm[1], 1e-3)
    depth = max(local_size_mm[2], 1e-3)
    panel_face = panel.normalized_face()
    axis = "z"

    if abs(center_u) + size_u / 2.0 > span_u / 2.0 + 1e-6 or abs(center_v) + size_v / 2.0 > span_v / 2.0 + 1e-6:
        pass

    if outer_kind == PROFILE_KIND_CYLINDER and panel_face not in CYLINDER_SUPPORTED_PANEL_FACES:
        return None
    if outer_kind == PROFILE_KIND_FRUSTUM and panel_face not in FRUSTUM_SUPPORTED_PANEL_FACES:
        return None

    if outer_kind == PROFILE_KIND_FRUSTUM and panel_face in {"+X", "-X", "+Y", "-Y"}:
        radial_surface = max(resolve_frustum_radius_mm(shell_spec, center_v), 0.0)
        if panel_face == "+X":
            center_mm = (radial_surface + depth / 2.0, center_u, center_v)
            size_mm = (depth, size_u, size_v)
            axis = "x"
        elif panel_face == "-X":
            center_mm = (-radial_surface - depth / 2.0, center_u, center_v)
            size_mm = (depth, size_u, size_v)
            axis = "x"
        elif panel_face == "+Y":
            center_mm = (center_u, radial_surface + depth / 2.0, center_v)
            size_mm = (size_u, depth, size_v)
            axis = "y"
        else:
            center_mm = (center_u, -radial_surface - depth / 2.0, center_v)
            size_mm = (size_u, depth, size_v)
            axis = "y"
    elif panel_face == "+X":
        center_mm = (outer_x / 2.0 + depth / 2.0, center_u, center_v)
        size_mm = (depth, size_u, size_v)
        axis = "x"
    elif panel_face == "-X":
        center_mm = (-outer_x / 2.0 - depth / 2.0, center_u, center_v)
        size_mm = (depth, size_u, size_v)
        axis = "x"
    elif panel_face == "+Y":
        center_mm = (center_u, outer_y / 2.0 + depth / 2.0, center_v)
        size_mm = (size_u, depth, size_v)
        axis = "y"
    elif panel_face == "-Y":
        center_mm = (center_u, -outer_y / 2.0 - depth / 2.0, center_v)
        size_mm = (size_u, depth, size_v)
        axis = "y"
    elif panel_face == "+Z":
        center_mm = (center_u, center_v, outer_z / 2.0 + depth / 2.0)
        size_mm = orient_local_size_to_panel_face(local_size_mm, panel_face)
    else:
        center_mm = (center_u, center_v, -outer_z / 2.0 - depth / 2.0)
        size_mm = orient_local_size_to_panel_face(local_size_mm, panel_face)

    return {
        "variant_id": str(variant.get("variant_id") or panel.active_variant or "panel_variant"),
        "variant_kind": variant_kind,
        "panel_id": panel.panel_id,
        "panel_face": panel_face,
        "center_mm": center_mm,
        "size_mm": size_mm,
        "local_size_mm": local_size_mm,
        "axis": axis,
        "profile": profile,
        "profile_kind": profile.normalized_kind(),
    }


def plan_box_panel_aperture(
    *,
    shell_spec: ShellSpec,
    panel: PanelSpec,
    aperture: ApertureSiteSpec,
    mode: str = "cutout",
) -> Optional[Dict[str, Any]]:
    outer_kind = shell_spec.outer_profile.normalized_kind()
    if outer_kind not in {PROFILE_KIND_BOX, PROFILE_KIND_CYLINDER, PROFILE_KIND_FRUSTUM}:
        return None

    outer_x, outer_y, outer_z = shell_spec.outer_size_mm()
    thickness = float(shell_spec.thickness_mm or 0.0)
    if thickness <= 0.0:
        return None

    span_u, span_v = resolve_panel_span_mm(shell_spec, panel)
    center_u = float(aperture.center_mm[0])
    center_v = float(aperture.center_mm[1])
    panel_face = panel.normalized_face()
    shape_kind = aperture.normalized_shape()
    axis = "z"
    profile_points_mm = [(float(point[0]), float(point[1])) for point in list(aperture.profile_points_mm or [])]

    if shape_kind == "profile_cutout" and profile_points_mm:
        xs = [point[0] for point in profile_points_mm]
        ys = [point[1] for point in profile_points_mm]
        size_u = max(xs) - min(xs)
        size_v = max(ys) - min(ys)
    else:
        size_u = float(aperture.size_mm[0])
        size_v = float(aperture.size_mm[1])

    if mode == "proxy":
        proxy_depth = float(aperture.proxy_depth_mm or max(thickness * 2.0, min(max(size_u, size_v), 50.0)))
        depth_value = max(proxy_depth, 1e-3)
    else:
        depth = float(aperture.depth_mm or thickness)
        epsilon = max(0.5, min(thickness * 0.25, 2.0))
        depth_value = thickness + 2.0 * epsilon if aperture.through_shell else min(depth, thickness) + 2.0 * epsilon
    base_depth_value = depth_value

    if abs(center_u) + size_u / 2.0 > span_u / 2.0 + 1e-6 or abs(center_v) + size_v / 2.0 > span_v / 2.0 + 1e-6:
        # Keep plan deterministic even when aperture exceeds nominal span.
        pass

    if outer_kind == PROFILE_KIND_CYLINDER and panel_face not in CYLINDER_SUPPORTED_PANEL_FACES:
        return None
    if outer_kind == PROFILE_KIND_FRUSTUM and panel_face not in FRUSTUM_SUPPORTED_PANEL_FACES:
        return None

    frustum_side_extra = 0.0
    if outer_kind == PROFILE_KIND_FRUSTUM and panel_face in {"+X", "-X", "+Y", "-Y"}:
        outer_height = float(shell_spec.outer_profile.height_mm or outer_z)
        if outer_height > 1e-9:
            slope_abs = abs(
                float(shell_spec.outer_profile.top_radius_mm or 0.0)
                - float(shell_spec.outer_profile.bottom_radius_mm or 0.0)
            ) / outer_height
            frustum_side_extra = slope_abs * size_v
        depth_value += frustum_side_extra

    if mode == "proxy":
        inner_half_x = outer_x / 2.0 - thickness
        inner_half_y = outer_y / 2.0 - thickness
        inner_half_z = outer_z / 2.0 - thickness
        if outer_kind == PROFILE_KIND_FRUSTUM and panel_face in {"+X", "-X", "+Y", "-Y"}:
            radial_inner = max(resolve_frustum_radius_mm(shell_spec, center_v) - thickness, 0.0) - base_depth_value / 2.0
            if panel_face == "+X":
                center_mm = (radial_inner, center_u, center_v)
                size_mm = (depth_value, size_u, size_v)
                axis = "x"
            elif panel_face == "-X":
                center_mm = (-radial_inner, center_u, center_v)
                size_mm = (depth_value, size_u, size_v)
                axis = "x"
            elif panel_face == "+Y":
                center_mm = (center_u, radial_inner, center_v)
                size_mm = (size_u, depth_value, size_v)
                axis = "y"
            else:
                center_mm = (center_u, -radial_inner, center_v)
                size_mm = (size_u, depth_value, size_v)
                axis = "y"
        elif panel_face == "+X":
            center_mm = (inner_half_x - depth_value / 2.0, center_u, center_v)
            size_mm = (depth_value, size_u, size_v)
            axis = "x"
        elif panel_face == "-X":
            center_mm = (-inner_half_x + depth_value / 2.0, center_u, center_v)
            size_mm = (depth_value, size_u, size_v)
            axis = "x"
        elif panel_face == "+Y":
            center_mm = (center_u, inner_half_y - depth_value / 2.0, center_v)
            size_mm = (size_u, depth_value, size_v)
            axis = "y"
        elif panel_face == "-Y":
            center_mm = (center_u, -inner_half_y + depth_value / 2.0, center_v)
            size_mm = (size_u, depth_value, size_v)
            axis = "y"
        elif panel_face == "+Z":
            center_mm = (center_u, center_v, inner_half_z - depth_value / 2.0)
            size_mm = (size_u, size_v, depth_value)
        else:
            center_mm = (center_u, center_v, -inner_half_z + depth_value / 2.0)
            size_mm = (size_u, size_v, depth_value)
    else:
        if outer_kind == PROFILE_KIND_FRUSTUM and panel_face in {"+X", "-X", "+Y", "-Y"}:
            radial_mid = max(resolve_frustum_radius_mm(shell_spec, center_v) - thickness / 2.0, 0.0)
            if panel_face == "+X":
                center_mm = (radial_mid, center_u, center_v)
                size_mm = (depth_value, size_u, size_v)
                axis = "x"
            elif panel_face == "-X":
                center_mm = (-radial_mid, center_u, center_v)
                size_mm = (depth_value, size_u, size_v)
                axis = "x"
            elif panel_face == "+Y":
                center_mm = (center_u, radial_mid, center_v)
                size_mm = (size_u, depth_value, size_v)
                axis = "y"
            else:
                center_mm = (center_u, -radial_mid, center_v)
                size_mm = (size_u, depth_value, size_v)
                axis = "y"
        elif panel_face == "+X":
            center_mm = (outer_x / 2.0 - thickness / 2.0, center_u, center_v)
            size_mm = (depth_value, size_u, size_v)
            axis = "x"
        elif panel_face == "-X":
            center_mm = (-outer_x / 2.0 + thickness / 2.0, center_u, center_v)
            size_mm = (depth_value, size_u, size_v)
            axis = "x"
        elif panel_face == "+Y":
            center_mm = (center_u, outer_y / 2.0 - thickness / 2.0, center_v)
            size_mm = (size_u, depth_value, size_v)
            axis = "y"
        elif panel_face == "-Y":
            center_mm = (center_u, -outer_y / 2.0 + thickness / 2.0, center_v)
            size_mm = (size_u, depth_value, size_v)
            axis = "y"
        elif panel_face == "+Z":
            center_mm = (center_u, center_v, outer_z / 2.0 - thickness / 2.0)
            size_mm = (size_u, size_v, depth_value)
        else:
            center_mm = (center_u, center_v, -outer_z / 2.0 + thickness / 2.0)
            size_mm = (size_u, size_v, depth_value)

    plan: Dict[str, Any] = {
        "panel_id": panel.panel_id,
        "panel_face": panel_face,
        "center_mm": center_mm,
        "size_mm": size_mm,
        "shape_kind": shape_kind,
        "axis": axis,
        "mode": mode,
    }
    if shape_kind == "circular_cutout":
        plan["radius_mm"] = min(size_u, size_v) / 2.0
    elif shape_kind == "profile_cutout":
        plan["profile_points_mm"] = list(profile_points_mm)
        plan["local_size_mm"] = (size_u, size_v, depth_value)
    return plan


def aperture_proxy_plans(shell_spec: ShellSpec) -> List[Dict[str, Any]]:
    panel_index = shell_spec.panel_index()
    plans: List[Dict[str, Any]] = []
    for aperture in shell_spec.aperture_sites:
        if not aperture.enabled:
            continue
        panel = panel_index.get(aperture.panel_id)
        if panel is None:
            continue
        plan = plan_box_panel_aperture(shell_spec=shell_spec, panel=panel, aperture=aperture, mode="proxy")
        if plan is None:
            continue
        plan["aperture_id"] = aperture.aperture_id
        plans.append(plan)
    return plans
