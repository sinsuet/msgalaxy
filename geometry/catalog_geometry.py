from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from core.protocol import ComponentGeometry
from .geometry_proxy import GeometryProxySpec

PROFILE_KIND_BOX = "box"
PROFILE_KIND_CYLINDER = "cylinder"
PROFILE_KIND_FRUSTUM = "frustum"
PROFILE_KIND_ELLIPSOID = "ellipsoid"
PROFILE_KIND_EXTRUDED = "extruded_profile"
PROFILE_KIND_COMPOSITE = "composite_primitive"

SUPPORTED_PROFILE_KINDS = {
    PROFILE_KIND_BOX,
    PROFILE_KIND_CYLINDER,
    PROFILE_KIND_FRUSTUM,
    PROFILE_KIND_ELLIPSOID,
    PROFILE_KIND_EXTRUDED,
    PROFILE_KIND_COMPOSITE,
}

DEFAULT_CATALOG_COMPONENT_DIR = Path(__file__).resolve().parent.parent / "config" / "catalog_components"


def _model_validate(model_cls: Any, payload: Any) -> Any:
    validator = getattr(model_cls, "model_validate", None)
    if callable(validator):
        return validator(payload)
    return model_cls.parse_obj(payload)


def _model_rebuild(model_cls: Any) -> None:
    rebuilder = getattr(model_cls, "model_rebuild", None)
    if callable(rebuilder):
        rebuilder()
        return
    model_cls.update_forward_refs()


def _float_pair(values: Sequence[Any]) -> Tuple[float, float]:
    return float(values[0]), float(values[1])


def _float_triplet(values: Sequence[Any]) -> Tuple[float, float, float]:
    return float(values[0]), float(values[1]), float(values[2])


class GeometryProfileSpec(BaseModel):
    """Precise geometry contract consumed by STEP export and downstream CAD tools."""

    kind: str = PROFILE_KIND_BOX
    size_mm: Optional[Tuple[float, float, float]] = None
    radius_mm: Optional[float] = None
    height_mm: Optional[float] = None
    bottom_radius_mm: Optional[float] = None
    top_radius_mm: Optional[float] = None
    semi_axes_mm: Optional[Tuple[float, float, float]] = None
    profile_points_mm: List[Tuple[float, float]] = Field(default_factory=list)
    depth_mm: Optional[float] = None
    children: List["PrimitivePlacementSpec"] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def normalized_kind(self) -> str:
        kind = str(self.kind or PROFILE_KIND_BOX).strip().lower()
        if kind not in SUPPORTED_PROFILE_KINDS:
            return PROFILE_KIND_BOX
        return kind

    def approximate_size_mm(self) -> Tuple[float, float, float]:
        kind = self.normalized_kind()
        if self.size_mm is not None:
            return _float_triplet(self.size_mm)

        if kind == PROFILE_KIND_CYLINDER:
            radius = float(self.radius_mm or 0.0)
            height = float(self.height_mm or 0.0)
            diameter = radius * 2.0
            return diameter, diameter, height

        if kind == PROFILE_KIND_FRUSTUM:
            radius = max(float(self.bottom_radius_mm or 0.0), float(self.top_radius_mm or 0.0))
            diameter = radius * 2.0
            return diameter, diameter, float(self.height_mm or 0.0)

        if kind == PROFILE_KIND_ELLIPSOID and self.semi_axes_mm is not None:
            axes = _float_triplet(self.semi_axes_mm)
            return axes[0] * 2.0, axes[1] * 2.0, axes[2] * 2.0

        if kind == PROFILE_KIND_EXTRUDED:
            if not self.profile_points_mm:
                return 0.0, 0.0, float(self.depth_mm or 0.0)
            xs = [float(point[0]) for point in self.profile_points_mm]
            ys = [float(point[1]) for point in self.profile_points_mm]
            return max(xs) - min(xs), max(ys) - min(ys), float(self.depth_mm or 0.0)

        if kind == PROFILE_KIND_COMPOSITE:
            if not self.children:
                return 0.0, 0.0, 0.0
            min_corner = [float("inf"), float("inf"), float("inf")]
            max_corner = [float("-inf"), float("-inf"), float("-inf")]
            for child in self.children:
                child_size = child.profile.approximate_size_mm()
                child_offset = _float_triplet(child.offset_mm)
                child_min = [
                    child_offset[0] - child_size[0] / 2.0,
                    child_offset[1] - child_size[1] / 2.0,
                    child_offset[2] - child_size[2] / 2.0,
                ]
                child_max = [
                    child_offset[0] + child_size[0] / 2.0,
                    child_offset[1] + child_size[1] / 2.0,
                    child_offset[2] + child_size[2] / 2.0,
                ]
                for axis in range(3):
                    min_corner[axis] = min(min_corner[axis], child_min[axis])
                    max_corner[axis] = max(max_corner[axis], child_max[axis])
            return (
                max_corner[0] - min_corner[0],
                max_corner[1] - min_corner[1],
                max_corner[2] - min_corner[2],
            )

        return 0.0, 0.0, 0.0

    @classmethod
    def from_component_geometry(cls, comp: ComponentGeometry) -> "GeometryProfileSpec":
        size_mm = (
            float(comp.dimensions.x),
            float(comp.dimensions.y),
            float(comp.dimensions.z),
        )
        kind = str(getattr(comp, "envelope_type", PROFILE_KIND_BOX) or PROFILE_KIND_BOX).strip().lower()

        if kind == PROFILE_KIND_CYLINDER:
            return cls(
                kind=PROFILE_KIND_CYLINDER,
                size_mm=size_mm,
                radius_mm=min(size_mm[0], size_mm[1]) / 2.0,
                height_mm=size_mm[2],
                metadata={"source": "legacy_component_geometry"},
            )

        if kind == PROFILE_KIND_FRUSTUM:
            return cls(
                kind=PROFILE_KIND_FRUSTUM,
                size_mm=size_mm,
                bottom_radius_mm=min(size_mm[0], size_mm[1]) / 2.0,
                top_radius_mm=min(size_mm[0], size_mm[1]) / 4.0,
                height_mm=size_mm[2],
                metadata={"source": "legacy_component_geometry"},
            )

        if kind == PROFILE_KIND_ELLIPSOID:
            return cls(
                kind=PROFILE_KIND_ELLIPSOID,
                semi_axes_mm=(size_mm[0] / 2.0, size_mm[1] / 2.0, size_mm[2] / 2.0),
                metadata={"source": "legacy_component_geometry"},
            )

        if kind in SUPPORTED_PROFILE_KINDS:
            return cls(kind=kind, size_mm=size_mm, metadata={"source": "legacy_component_geometry"})

        return cls(kind=PROFILE_KIND_BOX, size_mm=size_mm, metadata={"source": "legacy_component_geometry", "legacy_kind": kind})


class PrimitivePlacementSpec(BaseModel):
    """A primitive node inside a composite primitive assembly."""

    placement_id: Optional[str] = None
    offset_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    profile: GeometryProfileSpec
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CatalogComponentSpec(BaseModel):
    """Catalog truth for a component; layout state stays outside this object."""

    catalog_id: str
    family: str
    display_name: Optional[str] = None
    geometry_profile: GeometryProfileSpec
    geometry_proxy: Optional[GeometryProxySpec] = None
    mass_kg: Optional[float] = None
    power_w: Optional[float] = None
    material: Optional[str] = None
    mount_faces: List[str] = Field(default_factory=list)
    allowed_orientations: List[str] = Field(default_factory=list)
    default_appendages: List[Dict[str, Any]] = Field(default_factory=list)
    interfaces: Dict[str, Any] = Field(default_factory=dict)
    placeholder: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def resolved_proxy(self) -> GeometryProxySpec:
        if self.geometry_proxy is not None:
            return self.geometry_proxy
        return GeometryProxySpec.from_profile(
            self.geometry_profile,
            mount_faces=self.mount_faces,
            metadata={"source": "derived_from_profile"},
        )

    @classmethod
    def from_component_geometry(cls, comp: ComponentGeometry) -> "CatalogComponentSpec":
        geometry_profile = GeometryProfileSpec.from_component_geometry(comp)
        return cls(
            catalog_id=comp.id,
            family=str(comp.category or "generic"),
            display_name=comp.id,
            geometry_profile=geometry_profile,
            geometry_proxy=GeometryProxySpec.from_profile(
                geometry_profile,
                mount_faces=[],
                metadata={"source": "legacy_component_geometry"},
            ),
            mass_kg=float(comp.mass),
            power_w=float(comp.power),
            metadata={"source": "legacy_component_geometry"},
        )


def load_catalog_component_spec(path: str | Path) -> CatalogComponentSpec:
    return _model_validate(CatalogComponentSpec, json.loads(Path(path).read_text(encoding="utf-8")))


def resolve_catalog_component_spec_from_component_config(
    component_cfg: Mapping[str, Any],
    *,
    default_dir: Optional[Path] = None,
) -> Optional[CatalogComponentSpec]:
    payload = dict(component_cfg or {})
    catalog_payload = payload.get("catalog_component_spec")
    catalog_path = payload.get("catalog_component_path")
    catalog_file = payload.get("catalog_component_file")

    if catalog_payload:
        return _model_validate(CatalogComponentSpec, catalog_payload)

    if catalog_path:
        return load_catalog_component_spec(Path(str(catalog_path)))

    if catalog_file:
        base_dir = default_dir or DEFAULT_CATALOG_COMPONENT_DIR
        return load_catalog_component_spec(base_dir / str(catalog_file))

    if payload.get("geometry_profile") or payload.get("geometry_proxy"):
        profile_payload = payload.get("geometry_profile") or {
            "kind": PROFILE_KIND_BOX,
            "size_mm": tuple(payload.get("dims_mm", (0.0, 0.0, 0.0))),
        }
        geometry_profile = _model_validate(GeometryProfileSpec, profile_payload)
        proxy_payload = payload.get("geometry_proxy")
        geometry_proxy = _model_validate(GeometryProxySpec, proxy_payload) if proxy_payload else None
        return CatalogComponentSpec(
            catalog_id=str(payload.get("catalog_id") or payload.get("id") or "catalog_component"),
            family=str(payload.get("family") or payload.get("category") or "generic"),
            display_name=payload.get("display_name"),
            geometry_profile=geometry_profile,
            geometry_proxy=geometry_proxy,
            mass_kg=float(payload.get("mass_kg")) if payload.get("mass_kg") is not None else None,
            power_w=float(payload.get("power_w")) if payload.get("power_w") is not None else None,
            material=payload.get("material"),
            mount_faces=list(payload.get("mount_faces", []) or []),
            allowed_orientations=list(payload.get("allowed_orientations", []) or []),
            interfaces=dict(payload.get("interfaces", {}) or {}),
            placeholder=bool(payload.get("placeholder", False)),
            metadata={"source": "component_config_inline_catalog"},
        )

    return None


def extract_catalog_component_specs_from_layout_config(
    cfg: Mapping[str, Any],
    *,
    default_dir: Optional[Path] = None,
) -> Dict[str, CatalogComponentSpec]:
    specs: Dict[str, CatalogComponentSpec] = {}
    for component_cfg in list(dict(cfg or {}).get("components", []) or []):
        payload = dict(component_cfg or {})
        comp_id = str(payload.get("id") or "")
        if not comp_id:
            continue
        spec = resolve_catalog_component_spec_from_component_config(payload, default_dir=default_dir)
        if spec is None:
            continue
        specs[comp_id] = spec
    return specs


def resolve_catalog_component_specs(design_state: Any) -> Dict[str, CatalogComponentSpec]:
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    raw_specs = dict(metadata.get("catalog_components", {}) or {})
    raw_paths = dict(metadata.get("catalog_component_paths", {}) or {})
    raw_files = dict(metadata.get("catalog_component_files", {}) or {})
    specs: Dict[str, CatalogComponentSpec] = {}
    for component_id, path_payload in raw_paths.items():
        specs[str(component_id)] = load_catalog_component_spec(Path(str(path_payload)))
    for component_id, file_payload in raw_files.items():
        specs[str(component_id)] = load_catalog_component_spec(DEFAULT_CATALOG_COMPONENT_DIR / str(file_payload))
    for component_id, payload in raw_specs.items():
        if isinstance(payload, (str, Path)):
            spec = load_catalog_component_spec(Path(str(payload)))
        elif isinstance(payload, CatalogComponentSpec):
            spec = payload
        else:
            spec = _model_validate(CatalogComponentSpec, payload)
        if spec.geometry_proxy is None:
            spec.geometry_proxy = spec.resolved_proxy()
        specs[str(component_id)] = spec
    return specs


def resolve_catalog_component_spec(
    comp: ComponentGeometry,
    design_state: Optional[Any] = None,
) -> CatalogComponentSpec:
    if design_state is not None:
        specs = resolve_catalog_component_specs(design_state)
        if comp.id in specs:
            spec = specs[comp.id]
            if spec.mass_kg is None:
                spec.mass_kg = float(comp.mass)
            if spec.power_w is None:
                spec.power_w = float(comp.power)
            if spec.geometry_proxy is None:
                spec.geometry_proxy = spec.resolved_proxy()
            return spec
    return CatalogComponentSpec.from_component_geometry(comp)

_model_rebuild(PrimitivePlacementSpec)
_model_rebuild(GeometryProfileSpec)
