from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from core.protocol import DesignState

from .contracts import SatelliteLayoutCandidate
from .geometry_bridge import build_satellite_geometry_integration_report
from .runtime import evaluate_satellite_likeness_for_design_state


SINGLE_ARCHETYPE_DEMO_CASE_ID = "optical_remote_sensing_microsat_teacher_demo"
SINGLE_ARCHETYPE_DEMO_CASE_DIR = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "comsol_field_demo"
    / "demo_cases"
    / SINGLE_ARCHETYPE_DEMO_CASE_ID
)
SINGLE_ARCHETYPE_DEMO_MANIFEST_PATH = SINGLE_ARCHETYPE_DEMO_CASE_DIR / "case_manifest.json"
SINGLE_ARCHETYPE_DEMO_CASE_CONFIG_PATH = SINGLE_ARCHETYPE_DEMO_CASE_DIR / "case_config.json"
SINGLE_ARCHETYPE_DEMO_CASE_PARAMETERS_PATH = SINGLE_ARCHETYPE_DEMO_CASE_DIR / "case_parameters.json"
SINGLE_ARCHETYPE_DEMO_DESIGN_STATE_PATH = SINGLE_ARCHETYPE_DEMO_CASE_DIR / "design_state.json"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_single_archetype_demo_manifest() -> Dict[str, Any]:
    return _read_json(SINGLE_ARCHETYPE_DEMO_MANIFEST_PATH)


def load_single_archetype_demo_case_config() -> Dict[str, Any]:
    return _read_json(SINGLE_ARCHETYPE_DEMO_CASE_CONFIG_PATH)


def load_single_archetype_demo_case_parameters() -> Dict[str, Any]:
    return _read_json(SINGLE_ARCHETYPE_DEMO_CASE_PARAMETERS_PATH)


def load_single_archetype_demo_design_state() -> DesignState:
    return DesignState.model_validate(_read_json(SINGLE_ARCHETYPE_DEMO_DESIGN_STATE_PATH))


def load_single_archetype_demo_candidate() -> SatelliteLayoutCandidate:
    manifest = load_single_archetype_demo_manifest()
    return SatelliteLayoutCandidate.model_validate(dict(manifest.get("likeness_candidate", {}) or {}))


def evaluate_single_archetype_demo_case() -> Dict[str, Any]:
    design_state = load_single_archetype_demo_design_state()
    likeness = evaluate_satellite_likeness_for_design_state(
        design_state,
        bom_file=str(SINGLE_ARCHETYPE_DEMO_CASE_CONFIG_PATH),
        default_gate_mode="strict",
    )
    geometry_integration = build_satellite_geometry_integration_report(
        bom_file=str(SINGLE_ARCHETYPE_DEMO_CASE_CONFIG_PATH),
        design_state=design_state,
    )
    return {
        "case_id": SINGLE_ARCHETYPE_DEMO_CASE_ID,
        "manifest": load_single_archetype_demo_manifest(),
        "case_config": load_single_archetype_demo_case_config(),
        "case_parameters": load_single_archetype_demo_case_parameters(),
        "design_state": design_state,
        "candidate": load_single_archetype_demo_candidate(),
        "likeness": likeness,
        "geometry_integration": geometry_integration,
    }
