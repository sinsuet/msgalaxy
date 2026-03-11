from __future__ import annotations

import copy
import random
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest

from core.protocol import SimulationResult
from simulation.comsol.field_registry import COMSOL_FIELD_REGISTRY_VERSION, build_field_registry_manifest, get_field_spec
from simulation.comsol.metric_contracts import COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
from simulation.comsol.physics_profiles import PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
from simulation.comsol.physics_profiles import (
    COMSOL_CONTRACT_BUNDLE_VERSION,
    COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
    COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
)
from tools.comsol_field_demo.common import DEFAULT_CONFIG, dump_design_state, write_json
from tools.comsol_field_demo.layout_template import build_demo_design_state
from tools.comsol_field_demo.tool_export_tensors import export_case_tensors
from tools.comsol_field_demo.tool_generate_cases import generate_case_dataset, sample_case_parameters
from tools.comsol_field_demo.tool_render_fields import (
    _build_dataset_gallery,
    _build_shell_panel_boxes,
    _convert_payload_for_display,
    _resolve_field_style,
    render_case_outputs,
)
from tools.comsol_field_demo.tool_run_fields import (
    FIELD_EXPORT_SPECS,
    _build_field_dataset_candidates,
    _build_metric_payload,
    build_driver_config,
    resolve_expression,
    run_case_fields,
)


def _write_grid_file(path: Path, coords, value_fn) -> str:
    rows = []
    for x in coords[0]:
        for y in coords[1]:
            for z in coords[2]:
                rows.append(f"{x},{y},{z},{value_fn(x, y, z)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows), encoding="utf-8")
    return str(path)


def _assert_layout_within_envelope_and_non_overlapping(state) -> None:
    half_x = float(state.envelope.outer_size.x) / 2.0
    half_y = float(state.envelope.outer_size.y) / 2.0
    half_z = float(state.envelope.outer_size.z) / 2.0
    for component in state.components:
        assert abs(float(component.position.x)) + float(component.dimensions.x) / 2.0 <= half_x
        assert abs(float(component.position.y)) + float(component.dimensions.y) / 2.0 <= half_y
        assert abs(float(component.position.z)) + float(component.dimensions.z) / 2.0 <= half_z

    components = list(state.components)
    for left_index, left in enumerate(components):
        left_min_x = float(left.position.x) - float(left.dimensions.x) / 2.0
        left_max_x = float(left.position.x) + float(left.dimensions.x) / 2.0
        left_min_y = float(left.position.y) - float(left.dimensions.y) / 2.0
        left_max_y = float(left.position.y) + float(left.dimensions.y) / 2.0
        left_min_z = float(left.position.z) - float(left.dimensions.z) / 2.0
        left_max_z = float(left.position.z) + float(left.dimensions.z) / 2.0

        for right in components[left_index + 1 :]:
            right_min_x = float(right.position.x) - float(right.dimensions.x) / 2.0
            right_max_x = float(right.position.x) + float(right.dimensions.x) / 2.0
            right_min_y = float(right.position.y) - float(right.dimensions.y) / 2.0
            right_max_y = float(right.position.y) + float(right.dimensions.y) / 2.0
            right_min_z = float(right.position.z) - float(right.dimensions.z) / 2.0
            right_max_z = float(right.position.z) + float(right.dimensions.z) / 2.0

            overlap_x = left_min_x < right_max_x and left_max_x > right_min_x
            overlap_y = left_min_y < right_max_y and left_max_y > right_min_y
            overlap_z = left_min_z < right_max_z and left_max_z > right_min_z
            assert not (overlap_x and overlap_y and overlap_z), f"{left.id} overlaps {right.id}"


def _assert_layout_within_shell_cavity(state) -> None:
    shell_meta = dict(dict(state.metadata or {}).get("shell", {}) or {})
    if not bool(shell_meta.get("enabled", False)):
        return
    outer_half_x = float(state.envelope.outer_size.x) / 2.0
    outer_half_y = float(state.envelope.outer_size.y) / 2.0
    outer_half_z = float(state.envelope.outer_size.z) / 2.0
    thickness = float(state.envelope.thickness)
    for component in state.components:
        assert abs(float(component.position.x)) + float(component.dimensions.x) / 2.0 <= outer_half_x - thickness + 1e-6
        assert abs(float(component.position.y)) + float(component.dimensions.y) / 2.0 <= outer_half_y - thickness + 1e-6
        assert abs(float(component.position.z)) + float(component.dimensions.z) / 2.0 <= outer_half_z - thickness + 1e-6


def _count_shell_touch_components(state, tolerance_mm: float = 0.05) -> int:
    shell_meta = dict(dict(state.metadata or {}).get("shell", {}) or {})
    if not bool(shell_meta.get("enabled", False)):
        return 0
    inner_half = float(shell_meta.get("inner_size_mm", float(state.envelope.outer_size.x) - 2.0 * float(state.envelope.thickness))) / 2.0
    count = 0
    for component in state.components:
        gaps = (
            inner_half - (float(component.position.x) + float(component.dimensions.x) / 2.0),
            (float(component.position.x) - float(component.dimensions.x) / 2.0) + inner_half,
            inner_half - (float(component.position.y) + float(component.dimensions.y) / 2.0),
            (float(component.position.y) - float(component.dimensions.y) / 2.0) + inner_half,
            inner_half - (float(component.position.z) + float(component.dimensions.z) / 2.0),
            (float(component.position.z) - float(component.dimensions.z) / 2.0) + inner_half,
        )
        if min(float(item) for item in gaps) <= float(tolerance_mm):
            count += 1
    return count


def _layout_signature(state) -> tuple[tuple[str, float, float, float, float, float, float], ...]:
    return tuple(
        (
            str(component.id),
            round(float(component.position.x), 3),
            round(float(component.position.y), 3),
            round(float(component.position.z), 3),
            round(float(component.dimensions.x), 3),
            round(float(component.dimensions.y), 3),
            round(float(component.dimensions.z), 3),
        )
        for component in state.components
    )


def test_demo_layout_has_32_components() -> None:
    state = build_demo_design_state(case_id="case_demo")
    assert len(state.components) == 32
    assert len({component.id for component in state.components}) == 32
    _assert_layout_within_envelope_and_non_overlapping(state)
    _assert_layout_within_shell_cavity(state)
    assert dict(state.metadata.get("shell", {}) or {}).get("enabled") is True
    assert len(_build_shell_panel_boxes(state)) == 6


def test_irregular_demo_layout_has_32_components_without_overlap() -> None:
    state = build_demo_design_state(
        case_id="case_demo_irregular",
        layout_style="irregular_perturbed",
    )
    assert len(state.components) == 32
    _assert_layout_within_envelope_and_non_overlapping(state)
    _assert_layout_within_shell_cavity(state)


def test_cavity_staggered_layout_has_32_components_without_overlap() -> None:
    state = build_demo_design_state(
        case_id="case_demo_cavity",
        layout_style="cavity_staggered",
    )
    assert len(state.components) == 32
    _assert_layout_within_envelope_and_non_overlapping(state)
    _assert_layout_within_shell_cavity(state)
    assert _count_shell_touch_components(state) >= 24


def test_cavity_staggered_layout_variant_seed_is_reproducible() -> None:
    state_a = build_demo_design_state(
        case_id="case_demo_cavity_seed_a",
        layout_style="cavity_staggered",
        layout_variant_seed=12345,
    )
    state_b = build_demo_design_state(
        case_id="case_demo_cavity_seed_b",
        layout_style="cavity_staggered",
        layout_variant_seed=12345,
    )
    assert _layout_signature(state_a) == _layout_signature(state_b)


def test_cavity_staggered_layout_variant_seed_changes_layout() -> None:
    state_a = build_demo_design_state(
        case_id="case_demo_cavity_seed_1",
        layout_style="cavity_staggered",
        layout_variant_seed=101,
    )
    state_b = build_demo_design_state(
        case_id="case_demo_cavity_seed_2",
        layout_style="cavity_staggered",
        layout_variant_seed=202,
    )
    assert _layout_signature(state_a) != _layout_signature(state_b)
    _assert_layout_within_envelope_and_non_overlapping(state_a)
    _assert_layout_within_envelope_and_non_overlapping(state_b)
    _assert_layout_within_shell_cavity(state_a)
    _assert_layout_within_shell_cavity(state_b)
    assert _count_shell_touch_components(state_a) >= 20
    assert _count_shell_touch_components(state_b) >= 20


def test_generate_case_dataset_writes_cases(tmp_path: Path) -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    output_root = tmp_path / "dataset"

    def fake_exporter(design_state, output_path: str) -> bool:
        _ = design_state
        Path(output_path).write_text("STEP DEMO", encoding="utf-8")
        return True

    manifest = generate_case_dataset(
        config=config,
        output_root=output_root,
        num_samples=2,
        random_seed=7,
        clean_output=False,
        skip_step=False,
        exporter=fake_exporter,
    )

    assert manifest["num_samples"] == 2
    assert (output_root / "dataset_manifest.json").exists()
    assert (output_root / "cases" / "case_0000" / "design_state.json").exists()
    assert (output_root / "cases" / "case_0001" / "geometry" / "demo_layout.step").exists()


def test_build_driver_config_uses_case_parameters() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["physics"]["structural_lateral_accel_ratio"] = 0.35
    case_parameters = {
        "ambient_temperature_k": 299.15,
        "surface_temperature_k": 271.15,
        "initial_temperature_k": 295.15,
        "structural_load_scale": 1.25,
    }
    driver_config = build_driver_config(config, case_parameters)
    assert driver_config["ambient_temperature_k"] == 299.15
    assert driver_config["surface_temperature_k"] == 271.15
    assert driver_config["initial_temperature_k"] == 295.15
    assert driver_config["structural_launch_accel_g"] == 7.5
    assert driver_config["structural_lateral_accel_ratio"] == pytest.approx(0.35)


def test_tool_run_fields_uses_shared_field_registry_contract() -> None:
    assert FIELD_EXPORT_SPECS["temperature"]["registry_key"] == get_field_spec("temperature").key
    assert FIELD_EXPORT_SPECS["temperature"]["expression_candidates"] == list(
        get_field_spec("temperature").expression_candidates
    )
    assert FIELD_EXPORT_SPECS["stress"]["registry_key"] == get_field_spec("stress").key
    assert FIELD_EXPORT_SPECS["stress"]["expression_candidates"] == list(
        get_field_spec("stress").expression_candidates
    )
    assert FIELD_EXPORT_SPECS["displacement"]["registry_key"] == get_field_spec("displacement").key
    assert FIELD_EXPORT_SPECS["displacement"]["vector_components"]["u"] == list(
        get_field_spec("displacement_u").expression_candidates
    )
    assert FIELD_EXPORT_SPECS["displacement"]["vector_components"]["v"] == list(
        get_field_spec("displacement_v").expression_candidates
    )
    assert FIELD_EXPORT_SPECS["displacement"]["vector_components"]["w"] == list(
        get_field_spec("displacement_w").expression_candidates
    )


def test_run_case_fields_promotes_source_claim_to_manifest(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_0001"
    case_dir.mkdir(parents=True, exist_ok=True)
    state = build_demo_design_state(case_id="case_0001")
    dump_design_state(case_dir / "design_state.json", state)
    write_json(case_dir / "case_parameters.json", {"case_id": "case_0001"})

    class _FakeDriver:
        model = None

        def __init__(self, _config):
            self._config = dict(_config)

        def run_simulation(self, request):
            _ = request
            return SimulationResult(
                success=False,
                metrics={"max_temp": 999.0},
                raw_data={
                    "source_claim": {
                        "requested_physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
                        "physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
                        "requested_profile_release_grade": False,
                        "effective_profile_release_grade": False,
                        "thermal_realness_level": "diagnostic_simplified",
                    },
                    "field_export_registry_version": COMSOL_FIELD_REGISTRY_VERSION,
                    "field_export_registry": build_field_registry_manifest(),
                    "physics_profile_contract_version": COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
                    "profile_audit_digest_version": COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
                    "simulation_metric_unit_contract_version": COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
                    "simulation_metric_unit_contract": {
                        "max_temp": {
                            "summary_unit": "degC",
                            "field_registry_key": "temperature",
                            "field_unit": "K",
                        }
                    },
                    "physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
                    "requested_profile_release_grade": False,
                    "effective_profile_release_grade": False,
                    "thermal_realness_level": "diagnostic_simplified",
                    "structural_realness_level": "disabled",
                    "power_realness_level": "disabled",
                    "degradation_reason": "fixture failure",
                },
                error_message="fixture_failed",
            )

        def disconnect(self):
            return None

    summary = run_case_fields(
        case_dir=case_dir,
        config=copy.deepcopy(DEFAULT_CONFIG),
        driver_factory=lambda config: _FakeDriver(config),
    )

    assert summary["simulation_success"] is False
    assert summary["contract_bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert summary["source_claim"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert summary["contract_bundle"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert summary["contract_bundle"]["structural_realness_level"] == "disabled"
    assert summary["contract_bundle"]["power_realness_level"] == "disabled"
    assert summary["contract_bundle"]["degradation_reason"] == "fixture failure"
    assert summary["field_export_registry_version"] == COMSOL_FIELD_REGISTRY_VERSION
    assert summary["physics_profile_contract_version"] == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    assert summary["profile_audit_digest_version"] == COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    assert (
        summary["simulation_metric_unit_contract_version"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert summary["field_export_registry"]["temperature"]["unit"] == "K"
    assert summary["profile_audit_digest"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert summary["profile_audit_digest"]["canonical_request_degraded"] is False
    assert summary["requested_profile_release_grade"] is False
    assert summary["effective_profile_release_grade"] is False
    assert summary["physics_profile_contract"][PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED]["release_grade"] is False
    assert summary["simulation_metric_unit_contract"]["max_temp"]["summary_unit"] == "degC"
    assert summary["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert summary["degradation_reason"] == "fixture failure"


def test_sample_case_parameters_applies_category_power_bias() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["physics"]["category_power_jitter"] = 0.0
    config["physics"]["category_power_bias"] = {
        "power": 1.8,
        "battery": 1.5,
    }
    params = sample_case_parameters(config, sample_index=0, rng=random.Random(123))
    assert params["category_power_scale"]["power"] == pytest.approx(1.8)
    assert params["category_power_scale"]["battery"] == pytest.approx(1.5)


def test_resolve_field_style_supports_percentile_limits() -> None:
    tensor_payload = {
        "field": np.asarray([0.0, 1.0, 2.0, 3.0, 100.0], dtype=float),
    }
    normalize, _ = _resolve_field_style(
        field_name="stress",
        tensor_payload=tensor_payload,
        render_cfg={
            "field_limits": {
                "stress": {
                    "vmin": 0.0,
                    "percentile_vmax": 80.0,
                    "norm": "power",
                    "gamma": 0.6,
                }
            }
        },
    )
    assert float(normalize.vmin) == pytest.approx(0.0)
    assert float(normalize.vmax) == pytest.approx(np.percentile(tensor_payload["field"], 80.0))
    assert float(getattr(normalize, "gamma", 1.0)) == pytest.approx(0.6)


def test_convert_payload_for_display_supports_independent_vector_scale() -> None:
    payload = _convert_payload_for_display(
        field_name="displacement",
        tensor_payload={
            "field": np.asarray([1.0, 2.0], dtype=float),
            "vectors": np.asarray([[1.0, 0.0, 0.0]], dtype=float),
            "x_coords": np.asarray([0.0, 1.0], dtype=float),
            "y_coords": np.asarray([0.0, 1.0], dtype=float),
            "z_coords": np.asarray([0.0, 1.0], dtype=float),
            "unit": "m",
        },
        render_cfg={
            "field_display": {
                "displacement": {
                    "unit": "μm",
                    "scale": 1_000_000.0,
                    "vector_scale": 1_000.0,
                }
            }
        },
    )
    assert payload["unit"] == "μm"
    assert float(payload["field"][0]) == pytest.approx(1_000_000.0)
    assert float(payload["vectors"][0][0]) == pytest.approx(1_000.0)


def test_build_dataset_gallery_collects_triptychs_and_creates_montage(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    image_root = dataset_root / "cases"
    cases = []
    for index in range(3):
        render_dir = image_root / f"case_{index:04d}" / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        image_path = render_dir / "three_fields_horizontal.png"
        image = np.zeros((18, 32, 3), dtype=float)
        image[..., 0] = float(index + 1) / 3.0
        plt.imsave(image_path, image)
        cases.append(
            {
                "case_id": f"case_{index:04d}",
                "case_dir": str(render_dir.parent),
                "renders": {"three_fields": str(image_path)},
            }
        )

    gallery = _build_dataset_gallery(
        dataset_path=dataset_root,
        cases=cases,
        config={"render": {"gallery_columns": 2}},
    )
    assert gallery["triptych_count"] == 3
    assert Path(gallery["triptych_dir"]).exists()
    assert Path(gallery["montage_path"]).exists()


def test_resolve_expression_prefers_structural_dataset_via_driver_evaluator() -> None:
    class _DummyModel:
        @staticmethod
        def evaluate(expr, unit=None, dataset=None):
            _ = unit
            if dataset is not None:
                raise RuntimeError(f'Dataset "{dataset}" does not exist.')
            if str(expr) == "solid.mises":
                return np.asarray([0.0], dtype=float)
            raise RuntimeError("unexpected")

    class _DummyDriver:
        @staticmethod
        def _evaluate_expression_candidates(*, expressions, unit=None, datasets=None, reducer="max"):
            _ = unit, reducer
            if list(expressions) == ["solid.mises"] and list(datasets or []) == ["dset2"]:
                return 123.0
            return None

        @staticmethod
        def _select_modal_result_dataset(*, dataset_candidates=None):
            _ = dataset_candidates
            return "dset3"

        @staticmethod
        def _select_structural_stationary_dataset(*, dataset_candidates=None):
            _ = dataset_candidates
            return "dset2"

    field_candidates = _build_field_dataset_candidates(_DummyDriver(), [None, "dset1", "dset2", "dset3"])
    expression, dataset = resolve_expression(
        _DummyModel(),
        expression_candidates=["solid.mises", "solid.svm"],
        unit="Pa",
        dataset_candidates=field_candidates["stress"],
        evaluator=_DummyDriver(),
    )
    assert expression == "solid.mises"
    assert dataset == "dset2"


def test_build_metric_payload_normalizes_legacy_driver_units() -> None:
    driver_metrics = {
        "max_temp": -8.389767075681107,
        "min_temp": -8.649849000000017,
        "avg_temp": -8.627036396327696,
        "temp_gradient": 0.26008192431891075,
        "max_stress": 4214.8835688387,
        "max_displacement": 8.454158774996204e-07,
        "safety_factor": 0.03558817166599185,
        "first_modal_freq": 53092.881684718865,
    }
    exports = {
        "temperature": {
            "direct_metrics": {
                "max_temp": 264.7602329243189,
                "min_temp": 264.500151,
                "avg_temp": 264.523,
                "temp_gradient": 0.26008192431891075,
            },
            "grid_statistics": {
                "min": 264.50015099999996,
                "max": 264.7668795869221,
                "mean": 264.523,
            },
        },
        "stress": {
            "direct_metrics": {
                "max_stress": 4214.8835688387,
            },
            "grid_statistics": {
                "max": 3689.413787564051,
            },
        },
        "displacement": {
            "direct_metrics": {
                "max_displacement": 8.454158774996204e-10,
            },
            "grid_statistics": {
                "max": 8.425564668835038e-10,
            },
        },
    }
    canonical_metrics, metric_units, driver_metric_units, metric_audit = _build_metric_payload(
        driver_metrics=driver_metrics,
        exports=exports,
        driver_config={"structural_allowable_stress_mpa": 150.0},
    )

    assert canonical_metrics["max_temp"] == pytest.approx(264.7602329243189)
    assert canonical_metrics["min_temp"] == pytest.approx(264.500151)
    assert canonical_metrics["max_stress"] == pytest.approx(4214.8835688387)
    assert canonical_metrics["max_displacement"] == pytest.approx(8.454158774996204e-10)
    assert canonical_metrics["safety_factor"] == pytest.approx((150.0 * 1e6) / 4214.8835688387)
    assert metric_units["max_temp"] == "K"
    assert metric_units["max_stress"] == "Pa"
    assert metric_units["max_displacement"] == "m"
    assert driver_metric_units["max_temp"] == "degC"
    assert driver_metric_units["max_stress"] == "Pa"
    assert driver_metric_units["max_displacement"] == "mm"
    assert metric_audit["max_displacement"]["driver_value_normalized"] == pytest.approx(8.454158774996204e-10)
    assert metric_audit["max_temp"]["grid_value"] == pytest.approx(264.7668795869221)


def test_build_metric_payload_detects_mpa_driver_stress() -> None:
    canonical_metrics, metric_units, driver_metric_units, metric_audit = _build_metric_payload(
        driver_metrics={
            "max_stress": 152.4,
            "safety_factor": 1.0,
        },
        exports={
            "stress": {
                "grid_statistics": {
                    "max": 1.51e8,
                }
            }
        },
        driver_config={"structural_allowable_stress_mpa": 300.0},
    )

    assert canonical_metrics["max_stress"] == pytest.approx(152.4e6)
    assert metric_units["max_stress"] == "Pa"
    assert driver_metric_units["max_stress"] == "MPa"
    assert metric_audit["max_stress"]["driver_value_normalized"] == pytest.approx(152.4e6)


def test_export_case_tensors_and_render_outputs(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_0000"
    (case_dir / "field_exports" / "grid").mkdir(parents=True, exist_ok=True)
    (case_dir / "tensor").mkdir(parents=True, exist_ok=True)
    (case_dir / "renders").mkdir(parents=True, exist_ok=True)

    state = build_demo_design_state(case_id="case_0000")
    dump_design_state(case_dir / "design_state.json", state)

    x_coords = [-80.0, 0.0, 80.0]
    y_coords = [-80.0, 0.0, 80.0]
    z_coords = [-40.0, 40.0]
    coords = (x_coords, y_coords, z_coords)

    temperature_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "temperature_grid.txt",
        coords,
        lambda x, y, z: 280.0 + 0.1 * x + 0.05 * y + 0.2 * z,
    )
    stress_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "stress_grid.txt",
        coords,
        lambda x, y, z: 2_000_000.0 + 1000.0 * abs(x) + 500.0 * abs(y) + 800.0 * abs(z),
    )
    disp_u_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "displacement_u_grid.txt",
        coords,
        lambda x, y, z: 1.0e-5 + x * 1.0e-8,
    )
    disp_v_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "displacement_v_grid.txt",
        coords,
        lambda x, y, z: 2.0e-5 + y * 1.0e-8,
    )
    disp_w_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "displacement_w_grid.txt",
        coords,
        lambda x, y, z: 3.0e-5 + z * 1.0e-8,
    )
    displacement_grid = _write_grid_file(
        case_dir / "field_exports" / "grid" / "displacement_grid.txt",
        coords,
        lambda x, y, z: (
            (1.0e-5 + x * 1.0e-8) ** 2
            + (2.0e-5 + y * 1.0e-8) ** 2
            + (3.0e-5 + z * 1.0e-8) ** 2
        )
        ** 0.5,
    )

    write_json(
        case_dir / "field_exports" / "manifest.json",
        {
            "case_id": "case_0000",
            "field_export_registry_version": COMSOL_FIELD_REGISTRY_VERSION,
            "field_export_registry": build_field_registry_manifest(),
            "physics_profile_contract_version": COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
            "physics_profile_contract": {
                PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED: {
                    "label": "Diagnostic Simplified",
                    "official_interfaces": ["Heat Transfer in Solids", "TemperatureBoundary"],
                    "release_grade": False,
                    "description": "fixture",
                }
            },
            "profile_audit_digest_version": COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
            "simulation_metric_unit_contract_version": COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
            "simulation_metric_unit_contract": {
                "max_temp": {
                    "summary_unit": "degC",
                    "field_registry_key": "temperature",
                    "field_unit": "K",
                },
                "max_stress": {
                    "summary_unit": "MPa",
                    "field_registry_key": "von_mises",
                    "field_unit": "Pa",
                },
            },
            "source_claim": {
                "requested_physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
                "physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
                "requested_profile_release_grade": False,
                "effective_profile_release_grade": False,
                "thermal_realness_level": "diagnostic_simplified",
            },
            "requested_profile_release_grade": False,
            "effective_profile_release_grade": False,
            "physics_profile": PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED,
            "thermal_realness_level": "diagnostic_simplified",
            "structural_realness_level": "official_interface_thin_slice",
            "power_realness_level": "disabled",
            "degradation_reason": "diagnostic export fixture",
            "exports": {
                "temperature": {"grid_path": temperature_grid},
                "stress": {"grid_path": stress_grid},
                "displacement": {
                    "grid_path": displacement_grid,
                    "vector_component_registry_keys": {
                        "u": "displacement_u",
                        "v": "displacement_v",
                        "w": "displacement_w",
                    },
                    "vector_grid_paths": {
                        "u": disp_u_grid,
                        "v": disp_v_grid,
                        "w": disp_w_grid,
                    },
                },
            },
        },
    )

    tensor_manifest = export_case_tensors(case_dir)
    assert not tensor_manifest["errors"]
    assert tensor_manifest["contract_bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert tensor_manifest["requested_physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert tensor_manifest["source_claim"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert tensor_manifest["contract_bundle"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert tensor_manifest["contract_bundle"]["structural_realness_level"] == "official_interface_thin_slice"
    assert tensor_manifest["contract_bundle"]["degradation_reason"] == "diagnostic export fixture"
    assert tensor_manifest["requested_profile_release_grade"] is False
    assert tensor_manifest["effective_profile_release_grade"] is False
    assert tensor_manifest["field_export_registry_version"] == COMSOL_FIELD_REGISTRY_VERSION
    assert tensor_manifest["physics_profile_contract_version"] == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    assert tensor_manifest["profile_audit_digest_version"] == COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    assert (
        tensor_manifest["simulation_metric_unit_contract_version"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert tensor_manifest["field_export_registry"]["von_mises"]["unit"] == "Pa"
    assert tensor_manifest["profile_audit_digest"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert tensor_manifest["physics_profile_contract"][PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED]["release_grade"] is False
    assert tensor_manifest["simulation_metric_unit_contract"]["max_stress"]["summary_unit"] == "MPa"
    assert tensor_manifest["tensors"]["stress"]["registry_key"] == "von_mises"
    assert tensor_manifest["tensors"]["displacement"]["vector_component_registry_keys"] == {
        "u": "displacement_u",
        "v": "displacement_v",
        "w": "displacement_w",
    }
    displacement_tensor = np.load(case_dir / "tensor" / "displacement_tensor.npz")
    assert displacement_tensor["field"].shape == (3, 3, 2)
    assert displacement_tensor["vectors"].shape == (3, 3, 2, 3)
    assert displacement_tensor["unit"].item() == "m"
    assert displacement_tensor["registry_key"].item() == "displacement_magnitude"
    stress_tensor = np.load(case_dir / "tensor" / "stress_tensor.npz")
    assert stress_tensor["unit"].item() == "Pa"
    assert stress_tensor["registry_key"].item() == "von_mises"

    render_manifest = render_case_outputs(case_dir, copy.deepcopy(DEFAULT_CONFIG))
    assert not render_manifest["errors"]
    assert render_manifest["contract_bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert render_manifest["requested_physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert render_manifest["source_claim"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert render_manifest["contract_bundle"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert render_manifest["contract_bundle"]["structural_realness_level"] == "official_interface_thin_slice"
    assert render_manifest["contract_bundle"]["degradation_reason"] == "diagnostic export fixture"
    assert render_manifest["requested_profile_release_grade"] is False
    assert render_manifest["effective_profile_release_grade"] is False
    assert render_manifest["field_export_registry_version"] == COMSOL_FIELD_REGISTRY_VERSION
    assert render_manifest["physics_profile_contract_version"] == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    assert render_manifest["profile_audit_digest_version"] == COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    assert (
        render_manifest["simulation_metric_unit_contract_version"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert render_manifest["field_export_registry"]["temperature"]["unit"] == "K"
    assert render_manifest["profile_audit_digest"]["physics_profile"] == PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED
    assert render_manifest["physics_profile_contract"][PHYSICS_PROFILE_DIAGNOSTIC_SIMPLIFIED]["release_grade"] is False
    assert render_manifest["simulation_metric_unit_contract"]["max_temp"]["field_unit"] == "K"
    assert (case_dir / "renders" / "geometry_overlay.png").exists()
    assert (case_dir / "renders" / "temperature_field.png").exists()
    assert (case_dir / "renders" / "displacement_field.png").exists()
    assert (case_dir / "renders" / "stress_field.png").exists()
    assert (case_dir / "renders" / "three_fields_horizontal.png").exists()
    assert "three_fields" in render_manifest["renders"]
    assert render_manifest["render_styles"]["displacement"]["registry_key"] == "displacement_magnitude"
    assert render_manifest["render_styles"]["displacement"]["raw_unit"] == "m"
    assert render_manifest["render_styles"]["displacement"]["display_unit"] == "mm"
    assert render_manifest["render_styles"]["stress"]["registry_key"] == "von_mises"
    assert render_manifest["render_styles"]["stress"]["raw_unit"] == "Pa"
    assert render_manifest["render_styles"]["stress"]["display_unit"] == "kPa"
