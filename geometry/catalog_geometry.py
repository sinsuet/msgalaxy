from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

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


def _centered_bounds(size_mm: Sequence[Any]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    sx, sy, sz = _float_triplet(size_mm)
    return (
        (-sx / 2.0, -sy / 2.0, -sz / 2.0),
        (sx / 2.0, sy / 2.0, sz / 2.0),
    )


def _rotation_matrix_from_euler_deg(rotation_deg: Sequence[Any]) -> np.ndarray:
    rx_deg, ry_deg, rz_deg = _float_triplet(rotation_deg or (0.0, 0.0, 0.0))
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)

    rot_x = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(rx), -math.sin(rx)],
            [0.0, math.sin(rx), math.cos(rx)],
        ],
        dtype=float,
    )
    rot_y = np.array(
        [
            [math.cos(ry), 0.0, math.sin(ry)],
            [0.0, 1.0, 0.0],
            [-math.sin(ry), 0.0, math.cos(ry)],
        ],
        dtype=float,
    )
    rot_z = np.array(
        [
            [math.cos(rz), -math.sin(rz), 0.0],
            [math.sin(rz), math.cos(rz), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )
    return rot_z @ rot_y @ rot_x


def _bounds_corners(
    min_corner_mm: Sequence[Any],
    max_corner_mm: Sequence[Any],
) -> np.ndarray:
    min_x, min_y, min_z = _float_triplet(min_corner_mm)
    max_x, max_y, max_z = _float_triplet(max_corner_mm)
    return np.asarray(
        [
            [min_x, min_y, min_z],
            [min_x, min_y, max_z],
            [min_x, max_y, min_z],
            [min_x, max_y, max_z],
            [max_x, min_y, min_z],
            [max_x, min_y, max_z],
            [max_x, max_y, min_z],
            [max_x, max_y, max_z],
        ],
        dtype=float,
    )


def _transform_bounds(
    min_corner_mm: Sequence[Any],
    max_corner_mm: Sequence[Any],
    *,
    rotation_deg: Sequence[Any] = (0.0, 0.0, 0.0),
    translation_mm: Sequence[Any] = (0.0, 0.0, 0.0),
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    corners = _bounds_corners(min_corner_mm, max_corner_mm)
    rotation = _rotation_matrix_from_euler_deg(rotation_deg)
    translation = np.asarray(_float_triplet(translation_mm), dtype=float)
    transformed = (rotation @ corners.T).T + translation.reshape(1, 3)
    min_corner = transformed.min(axis=0)
    max_corner = transformed.max(axis=0)
    return (
        (float(min_corner[0]), float(min_corner[1]), float(min_corner[2])),
        (float(max_corner[0]), float(max_corner[1]), float(max_corner[2])),
    )


@dataclass(frozen=True)
class ResolvedGeometryTruth:
    profile_kind: str
    local_bbox_size_mm: Tuple[float, float, float]
    local_bbox_center_offset_mm: Tuple[float, float, float]
    effective_bbox_size_mm: Tuple[float, float, float]
    effective_bbox_center_offset_mm: Tuple[float, float, float]
    rotation_deg: Tuple[float, float, float]
    declared_proxy_size_mm: Optional[Tuple[float, float, float]]
    declared_proxy_center_offset_mm: Tuple[float, float, float]
    proxy_source: str

    def model_dump(self) -> Dict[str, Any]:
        return {
            "profile_kind": str(self.profile_kind),
            "local_bbox_size_mm": list(self.local_bbox_size_mm),
            "local_bbox_center_offset_mm": list(self.local_bbox_center_offset_mm),
            "effective_bbox_size_mm": list(self.effective_bbox_size_mm),
            "effective_bbox_center_offset_mm": list(self.effective_bbox_center_offset_mm),
            "rotation_deg": list(self.rotation_deg),
            "declared_proxy_size_mm": (
                list(self.declared_proxy_size_mm)
                if self.declared_proxy_size_mm is not None
                else None
            ),
            "declared_proxy_center_offset_mm": list(self.declared_proxy_center_offset_mm),
            "proxy_source": str(self.proxy_source),
        }


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

    def local_bounds_mm(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        kind = self.normalized_kind()
        if self.size_mm is not None:
            return _centered_bounds(self.size_mm)

        if kind == PROFILE_KIND_CYLINDER:
            radius = float(self.radius_mm or 0.0)
            height = float(self.height_mm or 0.0)
            diameter = radius * 2.0
            return _centered_bounds((diameter, diameter, height))

        if kind == PROFILE_KIND_FRUSTUM:
            radius = max(float(self.bottom_radius_mm or 0.0), float(self.top_radius_mm or 0.0))
            diameter = radius * 2.0
            return _centered_bounds((diameter, diameter, float(self.height_mm or 0.0)))

        if kind == PROFILE_KIND_ELLIPSOID and self.semi_axes_mm is not None:
            axes = _float_triplet(self.semi_axes_mm)
            return _centered_bounds((axes[0] * 2.0, axes[1] * 2.0, axes[2] * 2.0))

        if kind == PROFILE_KIND_EXTRUDED:
            if not self.profile_points_mm:
                return _centered_bounds((0.0, 0.0, float(self.depth_mm or 0.0)))
            xs = [float(point[0]) for point in self.profile_points_mm]
            ys = [float(point[1]) for point in self.profile_points_mm]
            depth = float(self.depth_mm or 0.0)
            return (
                (min(xs), min(ys), -depth / 2.0),
                (max(xs), max(ys), depth / 2.0),
            )

        if kind == PROFILE_KIND_COMPOSITE:
            if not self.children:
                return _centered_bounds((0.0, 0.0, 0.0))
            min_corner = [float("inf"), float("inf"), float("inf")]
            max_corner = [float("-inf"), float("-inf"), float("-inf")]
            for child in self.children:
                child_min, child_max = child.profile.local_bounds_mm()
                resolved_min, resolved_max = _transform_bounds(
                    child_min,
                    child_max,
                    rotation_deg=child.rotation_deg,
                    translation_mm=child.offset_mm,
                )
                for axis in range(3):
                    min_corner[axis] = min(min_corner[axis], resolved_min[axis])
                    max_corner[axis] = max(max_corner[axis], resolved_max[axis])
            return (
                (float(min_corner[0]), float(min_corner[1]), float(min_corner[2])),
                (float(max_corner[0]), float(max_corner[1]), float(max_corner[2])),
            )

        return _centered_bounds((0.0, 0.0, 0.0))

    def approximate_size_mm(self) -> Tuple[float, float, float]:
        min_corner, max_corner = self.local_bounds_mm()
        return (
            float(max_corner[0] - min_corner[0]),
            float(max_corner[1] - min_corner[1]),
            float(max_corner[2] - min_corner[2]),
        )

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

    def resolved_geometry_truth(
        self,
        *,
        rotation_deg: Sequence[Any] = (0.0, 0.0, 0.0),
    ) -> ResolvedGeometryTruth:
        return resolve_geometry_truth(self, rotation_deg=rotation_deg)

    def resolved_proxy(
        self,
        *,
        rotation_deg: Sequence[Any] = (0.0, 0.0, 0.0),
        prefer_declared_proxy: bool = False,
    ) -> GeometryProxySpec:
        if bool(prefer_declared_proxy) and self.geometry_proxy is not None:
            return self.geometry_proxy

        truth = self.resolved_geometry_truth(rotation_deg=rotation_deg)
        declared = self.geometry_proxy
        metadata = {
            "source": truth.proxy_source,
            "profile_kind": truth.profile_kind,
            "rotation_deg": list(truth.rotation_deg),
            "local_bbox_size_mm": list(truth.local_bbox_size_mm),
            "local_bbox_center_offset_mm": list(truth.local_bbox_center_offset_mm),
            "effective_bbox_size_mm": list(truth.effective_bbox_size_mm),
            "effective_bbox_center_offset_mm": list(truth.effective_bbox_center_offset_mm),
        }
        if declared is not None:
            metadata["declared_proxy_size_mm"] = list(declared.size_mm)
            metadata["declared_proxy_center_offset_mm"] = list(declared.center_offset_mm)
        return GeometryProxySpec(
            kind="aabb",
            size_mm=truth.effective_bbox_size_mm,
            center_offset_mm=truth.effective_bbox_center_offset_mm,
            mount_faces=list((declared.mount_faces if declared is not None else self.mount_faces) or []),
            functional_axis=(
                declared.functional_axis
                if declared is not None
                else None
            ),
            metadata=metadata,
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


def resolve_geometry_truth(
    spec: CatalogComponentSpec,
    *,
    rotation_deg: Sequence[Any] = (0.0, 0.0, 0.0),
) -> ResolvedGeometryTruth:
    local_min, local_max = spec.geometry_profile.local_bounds_mm()
    effective_min, effective_max = _transform_bounds(
        local_min,
        local_max,
        rotation_deg=rotation_deg,
        translation_mm=(0.0, 0.0, 0.0),
    )
    local_bbox_size = (
        float(local_max[0] - local_min[0]),
        float(local_max[1] - local_min[1]),
        float(local_max[2] - local_min[2]),
    )
    local_center_offset = (
        float((local_min[0] + local_max[0]) / 2.0),
        float((local_min[1] + local_max[1]) / 2.0),
        float((local_min[2] + local_max[2]) / 2.0),
    )
    effective_bbox_size = (
        float(effective_max[0] - effective_min[0]),
        float(effective_max[1] - effective_min[1]),
        float(effective_max[2] - effective_min[2]),
    )
    effective_center_offset = (
        float((effective_min[0] + effective_max[0]) / 2.0),
        float((effective_min[1] + effective_max[1]) / 2.0),
        float((effective_min[2] + effective_max[2]) / 2.0),
    )
    declared_proxy = spec.geometry_proxy
    declared_proxy_size = (
        _float_triplet(declared_proxy.size_mm)
        if declared_proxy is not None and declared_proxy.size_mm is not None
        else None
    )
    declared_proxy_center_offset = (
        _float_triplet(declared_proxy.center_offset_mm)
        if declared_proxy is not None
        else (0.0, 0.0, 0.0)
    )
    return ResolvedGeometryTruth(
        profile_kind=spec.geometry_profile.normalized_kind(),
        local_bbox_size_mm=local_bbox_size,
        local_bbox_center_offset_mm=local_center_offset,
        effective_bbox_size_mm=effective_bbox_size,
        effective_bbox_center_offset_mm=effective_center_offset,
        rotation_deg=_float_triplet(rotation_deg or (0.0, 0.0, 0.0)),
        declared_proxy_size_mm=declared_proxy_size,
        declared_proxy_center_offset_mm=declared_proxy_center_offset,
        proxy_source="geometry_profile_derived",
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
