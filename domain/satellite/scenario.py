from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, field_validator

from geometry.catalog_geometry import CatalogComponentSpec, load_catalog_component_spec
from geometry.shell_spec import ShellSpec, load_shell_spec


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_DIR = PROJECT_ROOT / "config" / "scenarios"
DEFAULT_CATALOG_DIR = PROJECT_ROOT / "config" / "catalog_components"


def _model_validate(model_cls: Any, payload: Any) -> Any:
    validator = getattr(model_cls, "model_validate", None)
    if callable(validator):
        return validator(payload)
    return model_cls.parse_obj(payload)


def _read_structured_file(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return dict(json.loads(path.read_text(encoding="utf-8")) or {})
    if suffix in {".yaml", ".yml"}:
        return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    raise ValueError(f"unsupported_scenario_file:{path}")


class ScenarioConstraintProfile(BaseModel):
    max_temp_c: float = 65.0
    min_clearance_mm: float = 6.0
    max_cg_offset_mm: float = 20.0
    min_safety_factor: float = 2.0
    min_modal_freq_hz: float = 55.0
    max_voltage_drop_v: float = 0.5
    min_power_margin_pct: float = 10.0
    max_power_w: float = 200.0
    bus_voltage_v: float = 28.0
    enforce_power_budget: bool = False
    mission_keepout_axis: str = "z"
    mission_keepout_center_mm: float = 0.0
    mission_min_separation_mm: float = 0.0


class ScenarioSeedProfile(BaseModel):
    clearance_buffer_mm: float = 6.0
    zone_margin_mm: float = 8.0
    jitter_mm: float = 4.0
    grid_cols: int = 2


class ScenarioComponentInstance(BaseModel):
    instance_id: str
    catalog_component_file: str = ""
    catalog_component_path: str = ""
    catalog_component_spec: Dict[str, Any] = Field(default_factory=dict)
    zone_id: str = ""
    mount_face: str = ""
    shell_contact_required: bool = False
    aperture_site: str = ""
    preferred_orientation: str = ""
    thermal_role: str = ""
    group_id: str = "core_bus"
    offset_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    tolerance_mm: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("instance_id")
    @classmethod
    def _validate_instance_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("instance_id 不能为空")
        return normalized

    def load_catalog_spec(self) -> CatalogComponentSpec:
        if self.catalog_component_spec:
            return _model_validate(CatalogComponentSpec, dict(self.catalog_component_spec))
        if self.catalog_component_path:
            return load_catalog_component_spec(Path(self.catalog_component_path))
        if self.catalog_component_file:
            return load_catalog_component_spec(DEFAULT_CATALOG_DIR / self.catalog_component_file)
        raise ValueError(f"scenario_component_missing_catalog_spec:{self.instance_id}")


class ScenarioObjectiveSpec(BaseModel):
    metric_key: str
    sense: str = "minimize"
    weight: float = 1.0


class SatelliteScenarioSpec(BaseModel):
    scenario_id: str
    description: str = ""
    archetype_id: str
    shell_spec_file: str = ""
    shell_spec_path: str = ""
    shell_spec: Dict[str, Any] = Field(default_factory=dict)
    shell_variant: str = ""
    rule_profile: str = ""
    comsol_physics_profile: str = "electro_thermo_structural_canonical"
    catalog_component_instances: List[ScenarioComponentInstance] = Field(default_factory=list)
    constraints: ScenarioConstraintProfile = Field(default_factory=ScenarioConstraintProfile)
    objectives: List[ScenarioObjectiveSpec] = Field(default_factory=list)
    seed_profile: ScenarioSeedProfile = Field(default_factory=ScenarioSeedProfile)
    field_exports: List[str] = Field(default_factory=lambda: ["temperature", "stress", "displacement"])
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("scenario_id", "archetype_id")
    @classmethod
    def _validate_required_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("required_id 不能为空")
        return normalized

    def load_shell_spec(self) -> ShellSpec:
        if self.shell_spec:
            return _model_validate(ShellSpec, dict(self.shell_spec))
        if self.shell_spec_path:
            return load_shell_spec(Path(self.shell_spec_path))
        if self.shell_spec_file:
            return load_shell_spec(DEFAULT_CATALOG_DIR / self.shell_spec_file)
        raise ValueError(f"scenario_missing_shell_spec:{self.scenario_id}")

    def catalog_specs_by_instance(self) -> Dict[str, CatalogComponentSpec]:
        return {
            instance.instance_id: instance.load_catalog_spec()
            for instance in list(self.catalog_component_instances or [])
        }


class PlacementState(BaseModel):
    instance_id: str
    position_mm: Tuple[float, float, float]
    rotation_deg: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    mount_face: str = ""
    aperture_site: str = ""
    zone_id: str = ""
    tolerance_mm: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


def load_satellite_scenario_spec(path: str | Path) -> SatelliteScenarioSpec:
    scenario_path = Path(path)
    if not scenario_path.is_absolute():
        scenario_path = (DEFAULT_SCENARIO_DIR / scenario_path).resolve()
    payload = _read_structured_file(scenario_path)
    spec = _model_validate(SatelliteScenarioSpec, payload)
    if not spec.metadata.get("scenario_path"):
        spec.metadata["scenario_path"] = str(scenario_path)
    return spec


def build_v4_object_catalog(
    scenario: SatelliteScenarioSpec,
    *,
    shell_spec: Optional[ShellSpec] = None,
) -> Dict[str, List[str]]:
    shell = shell_spec or scenario.load_shell_spec()
    zone_ids: List[str] = []
    for instance in list(scenario.catalog_component_instances or []):
        zone_id = str(instance.zone_id or "").strip()
        if zone_id and zone_id not in zone_ids:
            zone_ids.append(zone_id)
    for panel in list(shell.resolved_panels() or []):
        semantic_zone = str(dict(panel.metadata or {}).get("zone_id", "") or "").strip()
        if semantic_zone and semantic_zone not in zone_ids:
            zone_ids.append(semantic_zone)
    return {
        "component": [item.instance_id for item in list(scenario.catalog_component_instances or [])],
        "component_group": sorted(
            {
                str(item.group_id or "").strip()
                for item in list(scenario.catalog_component_instances or [])
                if str(item.group_id or "").strip()
            }
        ),
        "panel": [str(panel.panel_id) for panel in list(shell.resolved_panels() or [])],
        "aperture": [str(site.aperture_id) for site in list(shell.aperture_sites or [])],
        "zone": zone_ids,
        "mount_site": [str(panel.panel_id) for panel in list(shell.resolved_panels() or [])],
    }


def resolve_scenario_path_from_registry_entry(entry: Mapping[str, Any]) -> Path:
    scenario_file = str(dict(entry or {}).get("scenario", "") or "").strip()
    if not scenario_file:
        raise ValueError("scenario_registry_entry_missing_scenario")
    scenario_path = Path(scenario_file)
    if not scenario_path.is_absolute():
        scenario_path = (PROJECT_ROOT / scenario_path).resolve()
    return scenario_path
