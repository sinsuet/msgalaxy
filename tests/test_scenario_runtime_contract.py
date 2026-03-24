from __future__ import annotations

import json
from pathlib import Path

from domain.satellite.scenario import load_satellite_scenario_spec
from domain.satellite.seed import build_seed_design_state
from optimization.modes.mass.pymoo_integration.problem_generator import PymooProblemGenerator
from optimization.modes.mass.pymoo_integration.specs import PymooProblemSpec
from workflow.scenario_runtime import ScenarioRuntime, _layout_top_axis_limits


REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_PATH = REPO_ROOT / "config" / "scenarios" / "optical_remote_sensing_bus.yaml"


def test_scenario_contract_loads_shell_aperture_and_catalog_instances() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    shell_spec = scenario.load_shell_spec()
    catalog_specs = scenario.catalog_specs_by_instance()

    assert scenario.archetype_id == "optical_remote_sensing_microsat"
    assert shell_spec.shell_id == "box_shell_camera_window_v1"
    assert [site.aperture_id for site in shell_spec.aperture_sites] == ["camera_window"]
    assert set(catalog_specs.keys()) == {
        "payload_camera",
        "avionics_core",
        "adcs_module",
        "battery_pack",
        "antenna_panel",
    }


def test_seed_design_state_locks_catalog_dimensions_and_position_only_search_space() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, placements, semantic_zones = build_seed_design_state(scenario)
    problem_spec = PymooProblemSpec(
        base_state=design_state,
        runtime_constraints=scenario.constraints.model_dump(),
        semantic_zones=semantic_zones,
    )
    generator = PymooProblemGenerator(problem_spec)

    assert len(placements) == len(design_state.components)
    assert design_state.metadata["shell_spec_file"] == "shell_box_panel_aperture_min.json"
    assert "payload_camera" in design_state.metadata["catalog_component_files"]
    assert all(spec.name.endswith(("_x", "_y", "_z")) for spec in generator.codec.variable_specs)
    assert all("dimensions" not in spec.name for spec in generator.codec.variable_specs)


def test_seed_design_state_uses_rotation_aware_geometry_truth_contract() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, _, _ = build_seed_design_state(scenario)
    component_map = {comp.id: comp for comp in design_state.components}
    truth_map = dict(design_state.metadata.get("resolved_geometry_truth", {}) or {})

    payload_camera = component_map["payload_camera"]
    antenna_panel = component_map["antenna_panel"]

    assert tuple(round(value, 3) for value in (
        payload_camera.dimensions.x,
        payload_camera.dimensions.y,
        payload_camera.dimensions.z,
    )) == (140.0, 120.0, 170.0)
    assert tuple(round(value, 3) for value in (
        antenna_panel.dimensions.x,
        antenna_panel.dimensions.y,
        antenna_panel.dimensions.z,
    )) == (120.0, 8.0, 60.0)
    assert truth_map["payload_camera"]["declared_proxy_size_mm"] == [140.0, 120.0, 170.0]
    assert truth_map["payload_camera"]["declared_proxy_center_offset_mm"] == [0.0, 0.0, 30.0]
    assert truth_map["payload_camera"]["effective_bbox_center_offset_mm"] == [0.0, 0.0, 30.0]
    assert truth_map["antenna_panel"]["rotation_deg"] == [-90.0, 0.0, 0.0]
    assert [round(value, 3) for value in truth_map["antenna_panel"]["effective_bbox_size_mm"]] == [120.0, 8.0, 60.0]
    assert design_state.metadata["resolved_shell_truth"]["shell_id"] == "box_shell_camera_window_v1"


def test_seed_design_state_keeps_mount_face_axis_flush_to_shell_panel() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, placements, semantic_zones = build_seed_design_state(scenario)
    problem_spec = PymooProblemSpec(
        base_state=design_state,
        runtime_constraints=scenario.constraints.model_dump(),
        semantic_zones=semantic_zones,
    )
    generator = PymooProblemGenerator(problem_spec)

    placement_map = {placement.instance_id: placement for placement in placements}
    component_map = {comp.id: comp for comp in design_state.components}
    bounds_map = {
        spec.name: (float(spec.lower_bound), float(spec.upper_bound))
        for spec in generator.codec.variable_specs
    }

    assert placement_map["payload_camera"].position_mm[2] == 45.0
    assert placement_map["avionics_core"].position_mm[0] == 137.0
    assert placement_map["adcs_module"].position_mm[0] == -144.5
    assert placement_map["battery_pack"].position_mm[2] == -87.0
    assert placement_map["antenna_panel"].position_mm[1] == 138.0

    assert bounds_map["payload_camera_z"] == (45.0, 45.0)
    assert bounds_map["avionics_core_x"] == (137.0, 137.0)
    assert bounds_map["adcs_module_x"] == (-144.5, -144.5)
    assert bounds_map["battery_pack_z"] == (-87.0, -87.0)
    assert bounds_map["antenna_panel_y"] == (138.0, 138.0)
    assert bounds_map["avionics_core_z"] == (-18.3, 42.699999999999996)
    assert bounds_map["adcs_module_z"] == (-18.3, 42.699999999999996)
    assert placement_map["battery_pack"].position_mm[2] == -87.0
    assert placement_map["payload_camera"].metadata["shell_contact_required"] is True
    assert placement_map["avionics_core"].metadata["shell_contact_required"] is True
    assert placement_map["battery_pack"].metadata["mount_axis_locked"] is True
    assert placement_map["payload_camera"].metadata["shell_mount_conductance_w_m2k"] == 1200.0
    assert component_map["payload_camera"].shell_mount_conductance == 1200.0


def test_scenario_runtime_blocks_proxy_infeasible_before_comsol_and_writes_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    seed_state, _, _ = build_seed_design_state(scenario)
    runtime = ScenarioRuntime(
        stack="mass",
        config={
            "logging": {"base_dir": str(tmp_path)},
            "simulation": {"constraints": {}},
            "optimization": {},
        },
        scenario_path=SCENARIO_PATH,
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("COMSOL stage should not be reached for proxy-infeasible runs")

    monkeypatch.setattr(
        runtime,
        "_run_optimizer",
        lambda problem_spec: (
            seed_state.model_copy(deep=True),
            {
                "metrics": {
                    "max_temp": 84.0,
                    "collision_violation": 12.0,
                    "clearance_violation": 5.0,
                },
                "constraints": {
                    "collision": 12.0,
                    "clearance": 5.0,
                    "boundary": -1.0,
                    "thermal": 19.0,
                    "cg_limit": 3.5,
                    "safety_factor": -0.2,
                    "modal_freq": -5.0,
                    "voltage_drop": -0.1,
                    "power_margin": -8.0,
                    "mission_keepout": 7.0,
                },
            },
            {
                "variable_count": 15,
                "variable_names": ["x0"],
                "best_cv_curve": [46.5],
            },
        ),
    )
    monkeypatch.setattr(runtime, "_export_step", _fail_if_called)
    monkeypatch.setattr(runtime, "_run_canonical_comsol", _fail_if_called)

    result = runtime.execute()
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    result_index = json.loads((result.run_dir / "result_index.json").read_text(encoding="utf-8"))

    assert summary["status"] == "FAILED"
    assert summary["execution_stage"] == "proxy_optimized"
    assert summary["proxy_feasible"] is False
    assert summary["execution_success"] is False
    assert summary["comsol_attempted"] is False
    assert summary["comsol_block_reason"] == "proxy_infeasible"
    assert summary["field_export_attempted"] is False
    assert summary["real_feasibility_evaluated"] is False
    assert summary["real_feasible"] is None
    assert Path(summary["report_path"]).exists()
    assert Path(result_index["summary_path"]).exists()
    assert Path(result_index["report_path"]).exists()
    assert "step_path" not in dict(result_index.get("artifacts", {}) or {})


def test_scenario_runtime_blocks_satellite_likeness_before_step_and_comsol(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    seed_state, _, _ = build_seed_design_state(scenario)
    runtime = ScenarioRuntime(
        stack="mass",
        config={
            "logging": {"base_dir": str(tmp_path)},
            "simulation": {"constraints": {}},
            "optimization": {},
            "satellite_likeness_gate_mode": "strict",
        },
        scenario_path=SCENARIO_PATH,
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("STEP/COMSOL stages should not be reached for satellite-likeness failures")

    monkeypatch.setattr(
        runtime,
        "_run_optimizer",
        lambda problem_spec: (
            seed_state.model_copy(deep=True),
            {
                "metrics": {
                    "max_temp": 20.0,
                    "min_clearance": 14.0,
                    "num_collisions": 0.0,
                    "collision_violation": 0.0,
                    "clearance_violation": -6.0,
                    "boundary_violation": 0.0,
                    "cg_offset": 12.0,
                    "cg_violation": -5.0,
                    "safety_factor": 2.5,
                    "safety_factor_violation": -0.5,
                    "first_modal_freq": 65.0,
                    "modal_freq_violation": -10.0,
                    "voltage_drop": 0.4,
                    "voltage_drop_violation": -0.1,
                    "power_margin": 15.0,
                    "power_margin_violation": -5.0,
                    "mission_keepout_violation": 0.0,
                },
                "constraints": {
                    "collision": 0.0,
                    "clearance": -6.0,
                    "boundary": 0.0,
                    "thermal": -45.0,
                    "cg_limit": -5.0,
                    "safety_factor": -0.5,
                    "modal_freq": -10.0,
                    "voltage_drop": -0.1,
                    "power_margin": -5.0,
                    "mission_keepout": 0.0,
                },
            },
            {
                "variable_count": 15,
                "variable_names": ["x0"],
                "best_cv_curve": [0.0],
            },
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_evaluate_satellite_likeness",
        lambda design_state: {
            "gate_mode": "strict",
            "gate_passed": False,
            "candidate": {
                "archetype_id": "optical_remote_sensing_microsat",
                "bus_span_mm": [400.0, 290.0, 220.0],
            },
            "task_face_resolution": [
                {"semantic": "payload_face", "face_id": "+Z", "source": "layout_component_boundary"}
            ],
            "interior_zone_resolution": [
                {
                    "component_id": "payload_camera",
                    "zone_id": "optical_payload_tube",
                    "component_category": "payload",
                    "source": "scenario_contract",
                }
            ],
            "gate_report": {
                "passed": False,
                "candidate_archetype_id": "optical_remote_sensing_microsat",
                "expected_archetype_id": "optical_remote_sensing_microsat",
                "checks": [
                    {
                        "rule_id": "task_face_semantics",
                        "passed": False,
                        "message": "payload face mismatch",
                        "details": {"semantic": "payload_face", "expected_face_id": "+Z", "actual_face_id": "-Z"},
                    }
                ],
            },
        },
        raising=False,
    )
    monkeypatch.setattr(runtime, "_export_step", _fail_if_called)
    monkeypatch.setattr(runtime, "_run_canonical_comsol", _fail_if_called)

    result = runtime.execute()
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    result_index = json.loads((result.run_dir / "result_index.json").read_text(encoding="utf-8"))

    assert summary["status"] == "FAILED"
    assert summary["execution_stage"] == "proxy_feasible"
    assert summary["proxy_feasible"] is True
    assert summary["satellite_likeness_gate_mode"] == "strict"
    assert summary["satellite_likeness_gate_passed"] is False
    assert summary["comsol_attempted"] is False
    assert summary["comsol_block_reason"] == "satellite_likeness_failed"
    assert summary["satellite_layout_candidate"]["archetype_id"] == "optical_remote_sensing_microsat"
    assert summary["satellite_likeness_report"]["checks"][0]["rule_id"] == "task_face_semantics"
    assert result_index["satellite_likeness_gate_passed"] is False
    assert result_index["satellite_likeness_report"]["checks"][0]["rule_id"] == "task_face_semantics"
    report_text = Path(summary["report_path"]).read_text(encoding="utf-8")
    assert "## Satellite Likeness Gate" in report_text
    assert "task_face_semantics" in report_text


def test_scenario_runtime_indexes_successful_mph_and_field_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    seed_state, _, _ = build_seed_design_state(scenario)
    runtime = ScenarioRuntime(
        stack="mass",
        config={
            "logging": {"base_dir": str(tmp_path)},
            "simulation": {"constraints": {}, "save_mph_each_eval": True},
            "optimization": {},
        },
        scenario_path=SCENARIO_PATH,
    )

    step_path = runtime.run_dir / "step" / "optical_remote_sensing_bus.step"
    step_manifest = step_path.with_suffix(".geometry_manifest.json")
    step_proxy_manifest = step_path.with_suffix(".geometry_proxy_manifest.json")
    mph_path = runtime.run_dir / "mph_models" / "solve_success.mph"
    temperature_grid = runtime.run_dir / "fields" / "temperature_grid.txt"
    temperature_figure = runtime.run_dir / "figures" / "temperature.png"

    for path in (step_path, step_manifest, step_proxy_manifest, mph_path, temperature_grid, temperature_figure):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("artifact", encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "_run_optimizer",
        lambda problem_spec: (
            seed_state.model_copy(deep=True),
            {
                "metrics": {
                    "max_temp": 20.0,
                    "min_clearance": 14.0,
                    "num_collisions": 0.0,
                    "collision_violation": 0.0,
                    "clearance_violation": -6.0,
                    "boundary_violation": 0.0,
                    "cg_offset": 12.0,
                    "cg_violation": -5.0,
                    "safety_factor": 2.5,
                    "safety_factor_violation": -0.5,
                    "first_modal_freq": 65.0,
                    "modal_freq_violation": -10.0,
                    "voltage_drop": 0.4,
                    "voltage_drop_violation": -0.1,
                    "power_margin": 15.0,
                    "power_margin_violation": -5.0,
                    "mission_keepout_violation": 0.0,
                    "mission_source": "mission_fov_proxy",
                },
                "constraints": {
                    "collision": 0.0,
                    "clearance": -6.0,
                    "boundary": 0.0,
                    "thermal": -45.0,
                    "cg_limit": -5.0,
                    "safety_factor": -0.5,
                    "modal_freq": -10.0,
                    "voltage_drop": -0.1,
                    "power_margin": -5.0,
                    "mission_keepout": 0.0,
                },
            },
            {
                "variable_count": 15,
                "variable_names": ["x0"],
                "best_cv_curve": [0.0],
            },
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_evaluate_satellite_likeness",
        lambda design_state: {
            "gate_mode": "strict",
            "gate_passed": True,
            "candidate": {
                "archetype_id": "optical_remote_sensing_microsat",
                "bus_span_mm": [400.0, 290.0, 220.0],
            },
            "task_face_resolution": [
                {"semantic": "payload_face", "face_id": "+Z", "source": "layout_component_boundary"},
                {"semantic": "solar_array_mount", "face_id": "+Y", "source": "archetype_default"},
            ],
            "interior_zone_resolution": [
                {
                    "component_id": "payload_camera",
                    "zone_id": "optical_payload_tube",
                    "component_category": "payload",
                    "source": "scenario_contract",
                }
            ],
            "gate_report": {
                "passed": True,
                "candidate_archetype_id": "optical_remote_sensing_microsat",
                "expected_archetype_id": "optical_remote_sensing_microsat",
                "checks": [
                    {
                        "rule_id": "archetype_match",
                        "passed": True,
                        "message": "candidate matches expected archetype",
                        "details": {},
                    }
                ],
            },
        },
        raising=False,
    )
    monkeypatch.setattr(
        runtime,
        "_export_step",
        lambda design_state: {
            "step_path": str(step_path),
            "geometry_manifest_path": str(step_manifest),
            "geometry_proxy_manifest_path": str(step_proxy_manifest),
        },
    )
    monkeypatch.setattr(
        runtime,
        "_run_canonical_comsol",
        lambda design_state, step_path, geometry_manifest_path="": (
            {
                "success": True,
                "metrics": {
                    "max_temp": 90.0,
                    "safety_factor": 20.0,
                    "first_modal_freq": 120.0,
                    "voltage_drop": 0.1,
                    "power_margin": 25.0,
                    "peak_power": 65.0,
                },
                "raw_data": {
                    "source_claim": {
                        "requested_physics_profile": "electro_thermo_structural_canonical",
                        "physics_profile": "electro_thermo_structural_canonical",
                        "thermal_study_solved": True,
                        "structural_enabled": True,
                        "structural_study_solved": True,
                        "power_comsol_enabled": True,
                        "power_study_solved": True,
                    },
                    "metric_sources": {
                        "thermal_source": "online_comsol",
                        "structural_source": "online_comsol_structural",
                        "power_source": "online_comsol_power",
                    },
                    "component_thermal_audit": {
                        "enabled": True,
                        "expected_component_count": 2,
                        "evaluated_component_count": 2,
                        "components": [
                            {
                                "component_id": "payload_camera",
                                "evaluated": True,
                                "max_temp_c": 90.0,
                                "avg_temp_c": 84.0,
                                "domain_ids": [2],
                                "selection_status": "resolved_ambiguous_domain",
                            },
                            {
                                "component_id": "battery_pack",
                                "evaluated": True,
                                "max_temp_c": 38.0,
                                "avg_temp_c": 35.0,
                                "domain_ids": [5],
                                "selection_status": "inside_box_exact",
                            },
                        ],
                        "dominant_hotspot": {
                            "component_id": "payload_camera",
                            "max_temp_c": 90.0,
                            "avg_temp_c": 84.0,
                            "domain_ids": [2],
                            "selection_status": "resolved_ambiguous_domain",
                        },
                    },
                    "dominant_thermal_hotspot": {
                        "component_id": "payload_camera",
                        "max_temp_c": 90.0,
                        "avg_temp_c": 84.0,
                        "domain_ids": [2],
                        "selection_status": "resolved_ambiguous_domain",
                    },
                },
                "error_message": "",
                "mph_model_path": str(mph_path),
                "model_build_succeeded": True,
                "solve_attempted": True,
                "solve_succeeded": True,
                "field_export_attempted": True,
                "field_export_success": True,
                "field_export_error": "",
                "comsol_execution_stage": "solve_succeeded",
            },
            {
                "temperature": {
                    "grid_path": str(temperature_grid),
                    "figure_path": str(temperature_figure),
                    "dataset": "dset1",
                    "expression": "T",
                    "unit": "K",
                }
            },
        ),
    )

    result = runtime.execute()
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    result_index = json.loads((result.run_dir / "result_index.json").read_text(encoding="utf-8"))

    assert summary["status"] == "SUCCESS"
    assert summary["execution_success"] is True
    assert summary["execution_stage"] == "fields_exported"
    assert summary["satellite_likeness_gate_mode"] == "strict"
    assert summary["satellite_likeness_gate_passed"] is True
    assert summary["satellite_layout_candidate"]["archetype_id"] == "optical_remote_sensing_microsat"
    assert summary["satellite_likeness_report"]["checks"][0]["rule_id"] == "archetype_match"
    assert summary["real_feasibility_evaluated"] is True
    assert summary["real_feasible"] is False
    assert summary["real_violation_breakdown"]["thermal"] == 25.0
    assert summary["real_constraint_details"]["thermal"]["source"] == "online_comsol"
    assert summary["real_constraint_details"]["thermal"]["dominant_hotspot"]["component_id"] == "payload_camera"
    assert summary["dominant_thermal_hotspot"]["component_id"] == "payload_camera"
    assert summary["component_thermal_audit"]["evaluated_component_count"] == 2
    assert summary["final_mph_path"] == str(mph_path)
    assert summary["artifacts"]["final_mph_path"] == str(mph_path)
    assert result_index["execution_success"] is True
    assert result_index["real_feasibility_evaluated"] is True
    assert result_index["real_feasible"] is False
    assert result_index["satellite_likeness_gate_passed"] is True
    assert result_index["satellite_likeness_report"]["checks"][0]["rule_id"] == "archetype_match"
    assert result_index["dominant_thermal_hotspot"]["component_id"] == "payload_camera"
    assert result_index["artifacts"]["final_mph_path"] == str(mph_path)
    assert result_index["field_exports"]["temperature"]["grid_path"] == str(temperature_grid)
    assert result_index["field_exports"]["temperature"]["figure_path"] == str(temperature_figure)
    report_text = Path(summary["report_path"]).read_text(encoding="utf-8")
    assert "## Satellite Likeness Gate" in report_text
    assert "## Component Thermal Audit" in report_text
    assert "payload_camera" in report_text


def test_layout_top_axis_limits_follow_envelope_instead_of_default_unit_square() -> None:
    scenario = load_satellite_scenario_spec(SCENARIO_PATH)
    design_state, _, _ = build_seed_design_state(scenario)

    x_limits, y_limits = _layout_top_axis_limits(design_state)

    assert x_limits == (-220.0, 220.0)
    assert y_limits == (-165.0, 165.0)
