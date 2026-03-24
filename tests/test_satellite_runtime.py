from pathlib import Path

from domain.satellite.runtime import evaluate_satellite_likeness_for_scenario
from domain.satellite.scenario import load_satellite_scenario_spec
from domain.satellite.seed import build_seed_design_state


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = REPO_ROOT / "config" / "scenarios" / "optical_remote_sensing_bus.yaml"


def test_scenario_likeness_resolves_payload_face_from_mount_face() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, _, _ = build_seed_design_state(scenario)

    result = evaluate_satellite_likeness_for_scenario(
        design_state,
        scenario=scenario,
        default_gate_mode="strict",
    )
    task_face_resolution = {
        item["semantic"]: item
        for item in list(result.get("task_face_resolution", []) or [])
    }

    assert task_face_resolution["payload_face"]["face_id"] == "+Z"


def test_strict_scenario_likeness_passes_for_active_optical_seed() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, _, _ = build_seed_design_state(scenario)

    result = evaluate_satellite_likeness_for_scenario(
        design_state,
        scenario=scenario,
        default_gate_mode="strict",
    )
    failed_rules = [
        check["rule_id"]
        for check in list(dict(result.get("gate_report", {}) or {}).get("checks", []) or [])
        if not check.get("passed")
    ]

    assert result["gate_passed"] is True, failed_rules
    assert failed_rules == []
