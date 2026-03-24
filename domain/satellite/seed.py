from __future__ import annotations

from collections import defaultdict
from math import ceil
import re
from typing import Any, Dict, List, Tuple

from core.protocol import ComponentGeometry, DesignState, Envelope, Vector3D
from geometry.catalog_geometry import CatalogComponentSpec, ResolvedGeometryTruth
from geometry.shell_spec import ApertureSiteSpec, PanelSpec, ShellSpec
from optimization.modes.mass.pymoo_integration.specs import SemanticZone

from .baseline import load_default_satellite_reference_baseline
from .scenario import PlacementState, SatelliteScenarioSpec


def _safe_orientation(face: str) -> Tuple[float, float, float]:
    normalized = str(face or "").strip().upper()
    mapping = {
        "+X": (0.0, 90.0, 0.0),
        "-X": (0.0, -90.0, 0.0),
        "+Y": (-90.0, 0.0, 0.0),
        "-Y": (90.0, 0.0, 0.0),
        "+Z": (0.0, 0.0, 0.0),
        "-Z": (180.0, 0.0, 0.0),
    }
    return mapping.get(normalized, (0.0, 0.0, 0.0))


def _face_normal(face: str) -> Tuple[float, float, float]:
    normalized = str(face or "").strip().upper()
    mapping = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }
    return mapping.get(normalized, (0.0, 0.0, 1.0))


def _resolved_component_truth(
    spec: CatalogComponentSpec,
    *,
    rotation_deg: Tuple[float, float, float],
) -> ResolvedGeometryTruth:
    return spec.resolved_geometry_truth(rotation_deg=rotation_deg)


def _component_thermal_overrides(
    spec: CatalogComponentSpec,
    instance: Any,
) -> Dict[str, Any]:
    spec_meta = dict(getattr(spec, "metadata", {}) or {})
    spec_thermal = dict(spec_meta.get("thermal", {}) or {})
    instance_meta = dict(getattr(instance, "metadata", {}) or {})
    instance_thermal = dict(instance_meta.get("thermal", {}) or {})

    merged: Dict[str, Any] = {}
    for source in (spec_thermal, spec_meta, instance_thermal, instance_meta):
        for key in (
            "thermal_contacts",
            "emissivity",
            "absorptivity",
            "coating_type",
            "shell_mount_conductance",
            "shell_mount_conductance_w_m2k",
        ):
            if key in source and source.get(key) is not None:
                merged[key] = source.get(key)
    if "shell_mount_conductance" not in merged and "shell_mount_conductance_w_m2k" in merged:
        merged["shell_mount_conductance"] = merged.get("shell_mount_conductance_w_m2k")
    return merged


def _requires_shell_contact(
    instance: Any,
    spec: CatalogComponentSpec,
) -> bool:
    geometry_kind = str(spec.geometry_profile.normalized_kind() or "").strip().lower()
    return bool(
        bool(getattr(instance, "shell_contact_required", False))
        or bool(getattr(instance, "aperture_site", ""))
        or geometry_kind == "extruded_profile"
    )


def _shell_truth_payload(
    shell_spec: ShellSpec,
    *,
    scenario: SatelliteScenarioSpec,
) -> Dict[str, Any]:
    outer_x, outer_y, outer_z = (float(value) for value in shell_spec.outer_size_mm())
    return {
        "shell_id": str(shell_spec.shell_id),
        "shell_variant": str(scenario.shell_variant or ""),
        "shell_spec_file": str(scenario.shell_spec_file or ""),
        "shell_spec_path": str(scenario.shell_spec_path or ""),
        "outer_kind": str(shell_spec.outer_profile.normalized_kind()),
        "outer_size_mm": [outer_x, outer_y, outer_z],
        "thickness_mm": float(shell_spec.thickness_mm or 0.0),
        "panel_ids": [str(panel.panel_id) for panel in list(shell_spec.resolved_panels() or [])],
        "aperture_ids": [str(site.aperture_id) for site in list(shell_spec.aperture_sites or [])],
    }


def _inner_bounds(shell_spec: ShellSpec) -> Tuple[List[float], List[float]]:
    outer_x, outer_y, outer_z = (float(value) for value in shell_spec.outer_size_mm())
    thickness = float(shell_spec.thickness_mm or 0.0)
    inner_x = max(outer_x - 2.0 * thickness, 1.0)
    inner_y = max(outer_y - 2.0 * thickness, 1.0)
    inner_z = max(outer_z - 2.0 * thickness, 1.0)
    return (
        [-inner_x / 2.0, -inner_y / 2.0, -inner_z / 2.0],
        [inner_x / 2.0, inner_y / 2.0, inner_z / 2.0],
    )


def _panel_face(panel: PanelSpec | None) -> str:
    if panel is None:
        return "+Z"
    return str(panel.normalized_face())


def _mount_face_standoff_mm(
    clearance_mm: float,
    *,
    lock_axis: bool,
) -> float:
    """
    Mounted components should stay on their shell panel instead of floating inward.

    `clearance_mm` still governs component-to-component spacing, but the mounted
    face normal offset is treated as a physical contact/flush placement contract.
    """
    if bool(lock_axis):
        return 0.0
    return float(clearance_mm)


def _aperture_seed_position(
    *,
    aperture: ApertureSiteSpec,
    panel: PanelSpec | None,
    shell_spec: ShellSpec,
    size_mm: Tuple[float, float, float],
    clearance_mm: float,
) -> Tuple[float, float, float]:
    center_u, center_v = (float(value) for value in aperture.center_mm)
    inner_min, inner_max = _inner_bounds(shell_spec)
    outer_x, outer_y, outer_z = (float(value) for value in shell_spec.outer_size_mm())
    outer_min = (-outer_x / 2.0, -outer_y / 2.0, -outer_z / 2.0)
    outer_max = (outer_x / 2.0, outer_y / 2.0, outer_z / 2.0)
    face = _panel_face(panel)
    half_x, half_y, half_z = (0.5 * float(value) for value in size_mm)
    standoff_mm = _mount_face_standoff_mm(clearance_mm, lock_axis=True)
    anchor_min = outer_min if bool(aperture.through_shell) else tuple(inner_min)
    anchor_max = outer_max if bool(aperture.through_shell) else tuple(inner_max)
    if face == "+Z":
        return (center_u, center_v, anchor_max[2] - half_z - standoff_mm)
    if face == "-Z":
        return (center_u, center_v, anchor_min[2] + half_z + standoff_mm)
    if face == "+Y":
        return (center_u, anchor_max[1] - half_y - standoff_mm, center_v)
    if face == "-Y":
        return (center_u, anchor_min[1] + half_y + standoff_mm, center_v)
    if face == "+X":
        return (anchor_max[0] - half_x - standoff_mm, center_u, center_v)
    return (anchor_min[0] + half_x + standoff_mm, center_u, center_v)


def _clip_center(
    center: Tuple[float, float, float],
    *,
    size_mm: Tuple[float, float, float],
    bounds_min: List[float],
    bounds_max: List[float],
) -> Tuple[float, float, float]:
    half = [float(value) * 0.5 for value in size_mm]
    clipped = []
    for axis in range(3):
        lower = float(bounds_min[axis] + half[axis])
        upper = float(bounds_max[axis] - half[axis])
        value = min(max(float(center[axis]), lower), upper)
        clipped.append(value)
    return float(clipped[0]), float(clipped[1]), float(clipped[2])


def _clip_center_within_center_bounds(
    center: Tuple[float, float, float],
    *,
    bounds_min: Tuple[float, float, float],
    bounds_max: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    clipped = []
    for axis in range(3):
        value = min(max(float(center[axis]), float(bounds_min[axis])), float(bounds_max[axis]))
        clipped.append(value)
    return float(clipped[0]), float(clipped[1]), float(clipped[2])


def _zone_tokens(zone_id: str) -> Tuple[str, ...]:
    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", str(zone_id or "").strip().lower())
        if token
    )


def _semantic_zone_kind(zone_id: str) -> str:
    tokens = set(_zone_tokens(zone_id))
    if {"power", "battery", "eps"} & tokens:
        return "power"
    if {"avionics", "adcs", "middeck", "bus"} & tokens:
        return "avionics"
    if {"thermal", "radiator"} & tokens:
        return "thermal"
    if {"payload", "tube", "camera", "sensor", "aperture"} & tokens:
        return "payload"
    return "generic"


def _mounted_face_axis_bounds(
    *,
    mount_face: str,
    shell_spec: ShellSpec,
    size_mm: Tuple[float, float, float],
    margin_mm: float,
    clearance_mm: float,
    lock_axis: bool = False,
) -> Tuple[int, float, float] | None:
    _ = margin_mm
    if not bool(lock_axis):
        return None
    mount_anchor = _mount_face_anchor(
        mount_face=mount_face,
        shell_spec=shell_spec,
        size_mm=size_mm,
        clearance_mm=clearance_mm,
        lock_axis=lock_axis,
    )
    if mount_anchor is None:
        return None
    axis, anchor_value = mount_anchor
    return axis, float(anchor_value), float(anchor_value)


def _mount_face_anchor(
    *,
    mount_face: str,
    shell_spec: ShellSpec,
    size_mm: Tuple[float, float, float],
    clearance_mm: float,
    lock_axis: bool = False,
) -> Tuple[int, float] | None:
    face = str(mount_face or "").strip().upper()
    if face not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
        return None

    inner_min, inner_max = _inner_bounds(shell_spec)
    half_sizes = [0.5 * float(value) for value in size_mm]
    standoff_mm = _mount_face_standoff_mm(clearance_mm, lock_axis=lock_axis)
    if face == "+X":
        return 0, float(inner_max[0] - half_sizes[0] - standoff_mm)
    if face == "-X":
        return 0, float(inner_min[0] + half_sizes[0] + standoff_mm)
    if face == "+Y":
        return 1, float(inner_max[1] - half_sizes[1] - standoff_mm)
    if face == "-Y":
        return 1, float(inner_min[1] + half_sizes[1] + standoff_mm)
    if face == "+Z":
        return 2, float(inner_max[2] - half_sizes[2] - standoff_mm)
    return 2, float(inner_min[2] + half_sizes[2] + standoff_mm)


def _aperture_zone_bounds(
    *,
    aperture: ApertureSiteSpec,
    panel: PanelSpec | None,
    shell_spec: ShellSpec,
    size_mm: Tuple[float, float, float],
    margin_mm: float,
    clearance_mm: float,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    center = _aperture_seed_position(
        aperture=aperture,
        panel=panel,
        shell_spec=shell_spec,
        size_mm=size_mm,
        clearance_mm=clearance_mm,
    )
    face = _panel_face(panel)
    aperture_u = float(aperture.size_mm[0]) if aperture.size_mm else 0.0
    aperture_v = float(aperture.size_mm[1]) if aperture.size_mm else 0.0
    tangential_u = max(float(margin_mm), min(max(aperture_u * 0.25, margin_mm), 25.0))
    tangential_v = max(float(margin_mm), min(max(aperture_v * 0.25, margin_mm), 25.0))
    lower = [float(center[0]), float(center[1]), float(center[2])]
    upper = [float(center[0]), float(center[1]), float(center[2])]
    if face in {"+Z", "-Z"}:
        lower[0] -= tangential_u
        upper[0] += tangential_u
        lower[1] -= tangential_v
        upper[1] += tangential_v
        lower[2] = float(center[2])
        upper[2] = float(center[2])
    elif face in {"+Y", "-Y"}:
        lower[0] -= tangential_u
        upper[0] += tangential_u
        lower[2] -= tangential_v
        upper[2] += tangential_v
        lower[1] = float(center[1])
        upper[1] = float(center[1])
    else:
        lower[1] -= tangential_u
        upper[1] += tangential_u
        lower[2] -= tangential_v
        upper[2] += tangential_v
        lower[0] = float(center[0])
        upper[0] = float(center[0])

    inner_min, inner_max = _inner_bounds(shell_spec)
    clip_min = [float(value) for value in inner_min]
    clip_max = [float(value) for value in inner_max]
    if bool(aperture.through_shell):
        outer_x, outer_y, outer_z = (float(value) for value in shell_spec.outer_size_mm())
        outer_min = [-outer_x / 2.0, -outer_y / 2.0, -outer_z / 2.0]
        outer_max = [outer_x / 2.0, outer_y / 2.0, outer_z / 2.0]
        axis_map = {
            "+X": 0,
            "-X": 0,
            "+Y": 1,
            "-Y": 1,
            "+Z": 2,
            "-Z": 2,
        }
        normal_axis = axis_map.get(face)
        if normal_axis is not None:
            clip_min[normal_axis] = float(outer_min[normal_axis])
            clip_max[normal_axis] = float(outer_max[normal_axis])
    return _clip_center(
        tuple(lower),
        size_mm=size_mm,
        bounds_min=clip_min,
        bounds_max=clip_max,
    ), _clip_center(
        tuple(upper),
        size_mm=size_mm,
        bounds_min=clip_min,
        bounds_max=clip_max,
    )


def _semantic_zone_bounds(
    zone_id: str,
    *,
    shell_spec: ShellSpec,
    size_mm: Tuple[float, float, float],
    margin_mm: float,
    mount_face: str = "",
    clearance_mm: float = 0.0,
    lock_mount_axis: bool = False,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    inner_min, inner_max = _inner_bounds(shell_spec)
    lx, ly, lz = inner_min
    ux, uy, uz = inner_max
    zone_kind = _semantic_zone_kind(zone_id)

    if zone_kind == "payload":
        lower = (lx + margin_mm, ly + margin_mm, max(lz + margin_mm, uz * 0.10))
        upper = (ux - margin_mm, uy - margin_mm, uz - margin_mm)
    elif zone_kind == "power":
        lower = (lx + margin_mm, ly + margin_mm, lz + margin_mm)
        upper = (ux - margin_mm, uy - margin_mm, min(uz - margin_mm, lz * 0.10))
    elif zone_kind == "thermal":
        lower = (lx + margin_mm, ly + margin_mm, lz + margin_mm)
        upper = (ux - margin_mm, uy - margin_mm, uz - margin_mm)
    elif zone_kind == "avionics":
        lower = (lx + margin_mm, ly + margin_mm, lz * 0.15)
        upper = (ux - margin_mm, uy - margin_mm, uz * 0.35)
    else:
        lower = (lx + margin_mm, ly + margin_mm, lz + margin_mm)
        upper = (ux - margin_mm, uy - margin_mm, uz - margin_mm)

    mounted_axis = _mounted_face_axis_bounds(
        mount_face=mount_face,
        shell_spec=shell_spec,
        size_mm=size_mm,
        margin_mm=margin_mm,
        clearance_mm=clearance_mm,
        lock_axis=lock_mount_axis,
    )
    if mounted_axis is not None:
        axis, mounted_lower, mounted_upper = mounted_axis
        lower_values = list(lower)
        upper_values = list(upper)
        lower_values[axis] = float(mounted_lower)
        upper_values[axis] = float(mounted_upper)
        lower = tuple(lower_values)
        upper = tuple(upper_values)

    return _clip_center(lower, size_mm=size_mm, bounds_min=inner_min, bounds_max=inner_max), _clip_center(
        upper,
        size_mm=size_mm,
        bounds_min=inner_min,
        bounds_max=inner_max,
    )


def _grid_slot_center(
    *,
    bounds_min: Tuple[float, float, float],
    bounds_max: Tuple[float, float, float],
    slot_index: int,
    cols: int,
) -> Tuple[float, float, float]:
    cols = max(int(cols), 1)
    width_x = max(float(bounds_max[0] - bounds_min[0]), 1.0)
    width_y = max(float(bounds_max[1] - bounds_min[1]), 1.0)
    row = int(slot_index // cols)
    col = int(slot_index % cols)
    rows = max(int(ceil((slot_index + 1) / cols)), 1)
    x = float(bounds_min[0] + (col + 0.5) * (width_x / cols))
    y = float(bounds_min[1] + (row + 0.5) * (width_y / rows))
    z = float((bounds_min[2] + bounds_max[2]) / 2.0)
    return x, y, z


def build_seed_design_state(
    scenario: SatelliteScenarioSpec,
) -> tuple[DesignState, List[PlacementState], List[SemanticZone]]:
    shell_spec = scenario.load_shell_spec()
    catalog_specs = scenario.catalog_specs_by_instance()
    baseline = load_default_satellite_reference_baseline()
    archetype = baseline.get_archetype(scenario.archetype_id)
    seed_profile = scenario.seed_profile
    zone_margin = float(seed_profile.zone_margin_mm or 8.0)
    clearance_mm = float(seed_profile.clearance_buffer_mm or 6.0)
    panel_index = shell_spec.panel_index()
    aperture_index = {
        str(aperture.aperture_id): aperture for aperture in list(shell_spec.aperture_sites or [])
    }
    placements: List[PlacementState] = []
    components: List[ComponentGeometry] = []
    zone_slots: Dict[str, int] = defaultdict(int)
    resolved_geometry_truth: Dict[str, Dict[str, Any]] = {}
    semantic_zones: List[SemanticZone] = []

    for instance in list(scenario.catalog_component_instances or []):
        spec = catalog_specs[instance.instance_id]
        zone_id = str(instance.zone_id or "").strip()
        mount_face = str(instance.mount_face or "").strip().upper()
        rotation_deg = _safe_orientation(instance.preferred_orientation or mount_face)
        geometry_truth = _resolved_component_truth(spec, rotation_deg=rotation_deg)
        size_mm = tuple(float(value) for value in geometry_truth.effective_bbox_size_mm)
        shell_contact_required = _requires_shell_contact(instance, spec)
        lock_mount_axis = bool(shell_contact_required)
        thermal_overrides = _component_thermal_overrides(spec, instance)
        center = (0.0, 0.0, 0.0)
        aperture_seed_locked = False
        bounds_min: Tuple[float, float, float]
        bounds_max: Tuple[float, float, float]
        if instance.aperture_site:
            aperture = aperture_index.get(str(instance.aperture_site))
            if aperture is not None:
                panel = panel_index.get(str(aperture.panel_id))
                bounds_min, bounds_max = _aperture_zone_bounds(
                    aperture=aperture,
                    panel=panel,
                    shell_spec=shell_spec,
                    size_mm=size_mm,
                    margin_mm=zone_margin,
                    clearance_mm=clearance_mm,
                )
                center = _aperture_seed_position(
                    aperture=aperture,
                    panel=panel,
                    shell_spec=shell_spec,
                    size_mm=size_mm,
                    clearance_mm=clearance_mm,
                )
                center = (
                    center[0] + float(instance.offset_mm[0]),
                    center[1] + float(instance.offset_mm[1]),
                    center[2] + float(instance.offset_mm[2]),
                )
                zone_id = zone_id or "payload_zone"
                if not mount_face:
                    mount_face = _panel_face(panel)
                aperture_seed_locked = True
        if not aperture_seed_locked:
            bounds_min, bounds_max = _semantic_zone_bounds(
                zone_id or "core_zone",
                shell_spec=shell_spec,
                size_mm=size_mm,
                margin_mm=zone_margin,
                mount_face=mount_face,
                clearance_mm=clearance_mm,
                lock_mount_axis=lock_mount_axis,
            )
            slot_key = f"{zone_id or 'core_zone'}|{mount_face or 'free'}"
            slot = zone_slots[slot_key]
            zone_slots[slot_key] += 1
            center = _grid_slot_center(
                bounds_min=bounds_min,
                bounds_max=bounds_max,
                slot_index=slot,
                cols=int(seed_profile.grid_cols or 2),
            )
            center = (
                center[0] + float(instance.offset_mm[0]),
                center[1] + float(instance.offset_mm[1]),
                center[2] + float(instance.offset_mm[2]),
            )
            mount_anchor = _mount_face_anchor(
                mount_face=mount_face,
                shell_spec=shell_spec,
                size_mm=size_mm,
                clearance_mm=clearance_mm,
                lock_axis=lock_mount_axis,
            )
            if mount_anchor is not None:
                axis, anchor_value = mount_anchor
                center_values = [float(center[0]), float(center[1]), float(center[2])]
                center_values[axis] = float(anchor_value) + float(instance.offset_mm[axis])
                center = tuple(center_values)
        center = _clip_center_within_center_bounds(
            center,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
        )

        placement = PlacementState(
            instance_id=instance.instance_id,
            position_mm=(float(center[0]), float(center[1]), float(center[2])),
            rotation_deg=rotation_deg,
            mount_face=mount_face,
            aperture_site=str(instance.aperture_site or ""),
            zone_id=zone_id,
            tolerance_mm=float(instance.tolerance_mm or 0.0),
            metadata={
                "group_id": str(instance.group_id or ""),
                "thermal_role": str(instance.thermal_role or ""),
                "shell_contact_required": bool(shell_contact_required),
                "mount_axis_locked": bool(lock_mount_axis),
                **(
                    {
                        "shell_mount_conductance_w_m2k": float(
                            thermal_overrides.get("shell_mount_conductance")
                        )
                    }
                    if thermal_overrides.get("shell_mount_conductance") is not None
                    else {}
                ),
            },
        )
        placements.append(placement)
        resolved_geometry_truth[instance.instance_id] = {
            **geometry_truth.model_dump(),
            "catalog_id": str(spec.catalog_id),
            "display_name": str(spec.display_name or spec.catalog_id),
            "family": str(spec.family),
            "position_semantics": "effective_bbox_center",
        }
        semantic_zones.append(
            SemanticZone(
                zone_id=str(zone_id or "core_zone"),
                min_corner=tuple(float(value) for value in bounds_min),
                max_corner=tuple(float(value) for value in bounds_max),
                component_ids=(instance.instance_id,),
            )
        )

        components.append(
            ComponentGeometry(
                id=instance.instance_id,
                position=Vector3D(
                    x=float(placement.position_mm[0]),
                    y=float(placement.position_mm[1]),
                    z=float(placement.position_mm[2]),
                ),
                dimensions=Vector3D(x=float(size_mm[0]), y=float(size_mm[1]), z=float(size_mm[2])),
                rotation=Vector3D(
                    x=float(rotation_deg[0]),
                    y=float(rotation_deg[1]),
                    z=float(rotation_deg[2]),
                ),
                mass=float(spec.mass_kg or 0.0),
                power=float(spec.power_w or 0.0),
                category=str(spec.family or "generic"),
                clearance=float(clearance_mm),
                envelope_type=str(spec.geometry_profile.normalized_kind()),
                thermal_contacts=dict(thermal_overrides.get("thermal_contacts", {}) or {}),
                emissivity=(
                    float(thermal_overrides.get("emissivity"))
                    if thermal_overrides.get("emissivity") is not None
                    else 0.8
                ),
                absorptivity=(
                    float(thermal_overrides.get("absorptivity"))
                    if thermal_overrides.get("absorptivity") is not None
                    else 0.3
                ),
                coating_type=str(thermal_overrides.get("coating_type") or "default"),
                shell_mount_conductance=(
                    float(thermal_overrides.get("shell_mount_conductance"))
                    if thermal_overrides.get("shell_mount_conductance") is not None
                    else None
                ),
            )
        )

    outer_x, outer_y, outer_z = (float(value) for value in shell_spec.outer_size_mm())
    thickness = float(shell_spec.thickness_mm or 0.0)
    envelope = Envelope(
        outer_size=Vector3D(x=outer_x, y=outer_y, z=outer_z),
        inner_size=Vector3D(
            x=max(outer_x - 2.0 * thickness, 1.0),
            y=max(outer_y - 2.0 * thickness, 1.0),
            z=max(outer_z - 2.0 * thickness, 1.0),
        ),
        thickness=float(thickness),
        origin="center",
    )

    design_state = DesignState(
        iteration=0,
        components=components,
        envelope=envelope,
        metadata={
            "scenario_id": scenario.scenario_id,
            "satellite_archetype_id": scenario.archetype_id,
            "satellite_default_rule_profile": (
                scenario.rule_profile
                or str(getattr(archetype, "default_rule_profile", "") or "")
            ),
            "shell_spec_file": scenario.shell_spec_file,
            "shell_variant": scenario.shell_variant,
            "catalog_component_files": {
                instance.instance_id: str(instance.catalog_component_file or "")
                for instance in list(scenario.catalog_component_instances or [])
                if str(instance.catalog_component_file or "").strip()
            },
            "catalog_components": {
                component_id: spec.model_dump()
                for component_id, spec in catalog_specs.items()
            },
            "resolved_geometry_truth": resolved_geometry_truth,
            "resolved_geometry_truth_contract_version": "catalog_geometry_truth/v1",
            "resolved_shell_truth": _shell_truth_payload(shell_spec, scenario=scenario),
            "placement_state": [placement.model_dump() for placement in placements],
            "scenario_contract_version": "satellite_scenario/v1",
        },
    )
    return design_state, placements, semantic_zones
