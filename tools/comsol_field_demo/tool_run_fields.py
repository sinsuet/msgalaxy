from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.protocol import SimulationRequest, SimulationType
from simulation.comsol.field_registry import get_field_spec
from simulation.comsol.physics_profiles import (
    materialize_contract_payload,
)
from tools.comsol_field_demo.common import iter_case_dirs, load_config, load_design_state, read_json, write_json


def _build_field_export_specs() -> Dict[str, Dict[str, Any]]:
    temperature_field = get_field_spec("temperature")
    stress_field = get_field_spec("stress")
    displacement_field = get_field_spec("displacement")
    displacement_u_field = get_field_spec("displacement_u")
    displacement_v_field = get_field_spec("displacement_v")
    displacement_w_field = get_field_spec("displacement_w")
    return {
        "temperature": {
            "registry_key": temperature_field.key,
            "expression_candidates": list(temperature_field.expression_candidates),
            "unit": temperature_field.unit,
            "grid_unit": temperature_field.unit,
        },
        "stress": {
            "registry_key": stress_field.key,
            "expression_candidates": list(stress_field.expression_candidates),
            "unit": stress_field.unit,
            "grid_unit": stress_field.unit,
        },
        "displacement": {
            "registry_key": displacement_field.key,
            "expression_candidates": list(displacement_field.expression_candidates),
            "unit": displacement_field.unit,
            "grid_unit": displacement_field.unit,
            "vector_components": {
                "u": list(displacement_u_field.expression_candidates),
                "v": list(displacement_v_field.expression_candidates),
                "w": list(displacement_w_field.expression_candidates),
            },
        },
    }


FIELD_EXPORT_SPECS: Dict[str, Dict[str, Any]] = _build_field_export_specs()

DIRECT_FIELD_METRICS: Dict[str, Dict[str, str]] = {
    "temperature": {
        "max_temp": "max",
        "min_temp": "min",
        "avg_temp": "mean",
    },
    "stress": {
        "max_stress": "max",
    },
    "displacement": {
        "max_displacement": "max",
    },
}

CANONICAL_METRIC_UNITS: Dict[str, str] = {
    "max_temp": get_field_spec("temperature").unit,
    "min_temp": get_field_spec("temperature").unit,
    "avg_temp": get_field_spec("temperature").unit,
    "temp_gradient": get_field_spec("temperature").unit,
    "max_stress": get_field_spec("stress").unit,
    "max_displacement": get_field_spec("displacement").unit,
    "safety_factor": "1",
    "first_modal_freq": "Hz",
}


def _load_driver_factory() -> Callable[[Mapping[str, Any]], Any]:
    from simulation.comsol_driver import ComsolDriver

    return lambda config: ComsolDriver(config=dict(config))


def _normalize_path(path: str | Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


def _list_dataset_candidates(model: Any) -> List[Optional[str]]:
    tags: List[str] = []
    try:
        raw_tags = list(model.java.result().dataset().tags())
    except Exception:
        raw_tags = []
    for raw in raw_tags:
        tag = str(raw or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return [None] + tags


def _probe_expression(
    model: Any,
    expression: str,
    *,
    unit: Optional[str],
    dataset_candidates: Sequence[Optional[str]],
    evaluator: Any | None = None,
) -> Tuple[bool, Optional[str]]:
    for dataset in dataset_candidates:
        if dataset and evaluator is not None:
            try:
                value = evaluator._evaluate_expression_candidates(
                    expressions=[str(expression)],
                    unit=unit,
                    datasets=[str(dataset)],
                    reducer="max",
                )
                if value is not None:
                    return True, dataset
            except Exception:
                pass
        try:
            if dataset:
                model.evaluate(expression, unit=unit, dataset=dataset)
            else:
                model.evaluate(expression, unit=unit)
            return True, dataset
        except Exception:
            continue
    return False, None


def resolve_expression(
    model: Any,
    *,
    expression_candidates: Sequence[str],
    unit: Optional[str],
    dataset_candidates: Sequence[Optional[str]],
    evaluator: Any | None = None,
) -> Tuple[str, Optional[str]]:
    for expression in expression_candidates:
        ok, dataset = _probe_expression(
            model,
            expression,
            unit=unit,
            dataset_candidates=dataset_candidates,
            evaluator=evaluator,
        )
        if ok:
            return str(expression), dataset
    raise RuntimeError(f"expression_unresolvable:{','.join(expression_candidates)}")


def _set_if_supported(node: Any, key: str, value: Any) -> bool:
    try:
        node.set(key, value)
        return True
    except Exception:
        return False


def _create_export_node(model: Any, *, dataset: Optional[str]) -> Tuple[Any, str]:
    export_root = model.java.result().export()
    export_tag = f"demoexp_{uuid.uuid4().hex[:8]}"
    errors: List[str] = []
    node = None
    if dataset:
        for args in ((export_tag, str(dataset), "Data"), (export_tag, str(dataset), "data")):
            try:
                node = export_root.create(*args)
                break
            except Exception as exc:
                errors.append(str(exc))
    if node is None:
        for args in ((export_tag, "Data"), (export_tag, "data")):
            try:
                node = export_root.create(*args)
                break
            except Exception as exc:
                errors.append(str(exc))
    if node is None:
        raise RuntimeError("export_node_create_failed:" + " | ".join(errors[-3:]))
    if dataset:
        _set_if_supported(node, "data", str(dataset))
        _set_if_supported(node, "dataset", str(dataset))
    return node, export_tag


def export_field_data(
    model: Any,
    *,
    output_path: Path,
    expression: str,
    unit: Optional[str],
    dataset_candidates: Sequence[Optional[str]],
    export_kind: str,
    resolution: Sequence[int],
) -> Dict[str, Any]:
    errors: List[str] = []
    for dataset in dataset_candidates:
        node = None
        export_tag = ""
        try:
            node, export_tag = _create_export_node(model, dataset=dataset)
            _set_if_supported(node, "expr", expression)
            if unit:
                _set_if_supported(node, "unit", unit)
            _set_if_supported(node, "filename", _normalize_path(output_path))

            if export_kind == "vtu":
                _set_if_supported(node, "location", "fromdataset")
                _set_if_supported(node, "exporttype", "vtu")
            elif export_kind == "text":
                _set_if_supported(node, "location", "regulargrid")
                _set_if_supported(node, "exporttype", "text")
                _set_if_supported(node, "regulargridx3", str(int(resolution[0])))
                _set_if_supported(node, "regulargridy3", str(int(resolution[1])))
                _set_if_supported(node, "regulargridz3", str(int(resolution[2])))
                _set_if_supported(node, "gridstruct", "spreadsheet")
                _set_if_supported(node, "includecoords", "on")
                _set_if_supported(node, "header", "off")
                _set_if_supported(node, "separator", ",")
                _set_if_supported(node, "fullprec", "on")
            else:
                raise RuntimeError(f"unsupported_export_kind:{export_kind}")

            try:
                node.run()
            except Exception:
                model.java.result().export(export_tag).run()

            if output_path.exists() and output_path.stat().st_size > 0:
                return {
                    "path": str(output_path),
                    "dataset": dataset,
                    "expression": expression,
                    "unit": unit or "",
                    "export_kind": export_kind,
                }
            raise RuntimeError(f"empty_export:{output_path}")
        except Exception as exc:
            errors.append(f"{dataset or 'default'}:{exc}")
            if output_path.exists() and output_path.stat().st_size == 0:
                output_path.unlink(missing_ok=True)
        finally:
            if export_tag:
                try:
                    model.java.result().export().remove(export_tag)
                except Exception:
                    pass
    raise RuntimeError(f"field_export_failed:{expression}:{' | '.join(errors[:6])}")


def export_registered_field_data(
    *,
    driver: Any,
    registry_key: str,
    output_path: Path,
    export_kind: str,
    resolution: Sequence[int],
) -> Dict[str, Any] | None:
    exporter = getattr(driver, "export_registered_field", None)
    if not callable(exporter):
        return None
    try:
        payload = exporter(
            registry_key,
            str(output_path),
            export_kind=export_kind,
            resolution=resolution,
        )
    except Exception:
        return None
    return dict(payload or {})


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _summarize_regular_grid(path: str | Path) -> Dict[str, Any]:
    rows = np.genfromtxt(path, delimiter=",")
    if rows.size == 0:
        raise ValueError(f"regular_grid_empty:{path}")
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)
    if rows.ndim != 2 or rows.shape[1] < 4:
        raise ValueError(f"regular_grid_invalid_shape:{rows.shape}")
    values = np.asarray(rows[:, 3], dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError(f"regular_grid_no_finite_values:{path}")
    return {
        "finite_count": int(finite.size),
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mean": float(np.mean(finite)),
    }


def _evaluate_direct_field_metrics(
    *,
    driver: Any,
    field_name: str,
    expression: str,
    unit: Optional[str],
    dataset_candidates: Sequence[Optional[str]],
) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    reducers = dict(DIRECT_FIELD_METRICS.get(field_name, {}) or {})
    evaluator = getattr(driver, "_evaluate_expression_candidates", None)
    if evaluator is None:
        return metrics
    for metric_key, reducer in reducers.items():
        try:
            value = evaluator(
                expressions=[str(expression)],
                unit=unit,
                datasets=list(dataset_candidates),
                reducer=str(reducer),
            )
        except Exception:
            value = None
        if value is not None:
            metrics[metric_key] = float(value)
    max_temp = _as_float(metrics.get("max_temp"))
    min_temp = _as_float(metrics.get("min_temp"))
    if max_temp is not None and min_temp is not None:
        metrics["temp_gradient"] = float(max(max_temp - min_temp, 0.0))
    return metrics


def _driver_metric_candidates(metric_key: str, driver_value: float) -> List[Tuple[str, float]]:
    if metric_key in {"max_temp", "min_temp", "avg_temp"}:
        return [("K", driver_value), ("degC", driver_value + 273.15)]
    if metric_key == "temp_gradient":
        return [("K", driver_value)]
    if metric_key == "max_stress":
        return [
            ("Pa", driver_value),
            ("kPa", driver_value * 1e3),
            ("MPa", driver_value * 1e6),
        ]
    if metric_key == "max_displacement":
        return [
            ("m", driver_value),
            ("mm", driver_value / 1000.0),
            ("um", driver_value / 1e6),
        ]
    if metric_key == "first_modal_freq":
        return [("Hz", driver_value)]
    if metric_key == "safety_factor":
        return [("1", driver_value)]
    return [("", driver_value)]


def _relative_error(value: float, reference: float) -> float:
    scale = max(abs(reference), 1e-12)
    return abs(value - reference) / scale


def _infer_driver_metric_unit(
    metric_key: str,
    *,
    driver_value: Any,
    canonical_value: Any,
) -> Tuple[Optional[str], Optional[float]]:
    numeric_driver = _as_float(driver_value)
    numeric_canonical = _as_float(canonical_value)
    if numeric_driver is None:
        return None, None
    candidates = _driver_metric_candidates(metric_key, numeric_driver)
    if numeric_canonical is None:
        return candidates[0]
    best_unit, best_value = min(
        candidates,
        key=lambda item: _relative_error(float(item[1]), numeric_canonical),
    )
    return str(best_unit), float(best_value)


def _prefer_temperature_grid_stats(
    *,
    direct_metrics: Mapping[str, Any],
    grid_statistics: Mapping[str, Any],
) -> bool:
    direct_max = _as_float(dict(direct_metrics or {}).get("max_temp"))
    direct_min = _as_float(dict(direct_metrics or {}).get("min_temp"))
    direct_avg = _as_float(dict(direct_metrics or {}).get("avg_temp"))
    grid_max = _as_float(dict(grid_statistics or {}).get("max"))
    grid_min = _as_float(dict(grid_statistics or {}).get("min"))
    grid_avg = _as_float(dict(grid_statistics or {}).get("mean"))

    if grid_max is None or grid_min is None:
        return False
    if direct_max is None or direct_min is None:
        return True

    max_gap = abs(float(direct_max) - float(grid_max))
    min_gap = abs(float(direct_min) - float(grid_min))
    avg_gap = (
        abs(float(direct_avg) - float(grid_avg))
        if direct_avg is not None and grid_avg is not None
        else 0.0
    )
    max_tol = max(2.0, abs(float(grid_max)) * 0.05)
    min_tol = max(2.0, abs(float(grid_min)) * 0.05)
    avg_tol = max(2.0, abs(float(grid_avg or 0.0)) * 0.05)
    return bool(max_gap > max_tol or min_gap > min_tol or avg_gap > avg_tol)


def _build_metric_payload(
    *,
    driver_metrics: Mapping[str, Any],
    exports: Mapping[str, Any],
    driver_config: Mapping[str, Any],
) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, str], Dict[str, Dict[str, Any]]]:
    canonical_metrics: Dict[str, float] = {}
    metric_units: Dict[str, str] = {}
    driver_metric_units: Dict[str, str] = {}
    metric_audit: Dict[str, Dict[str, Any]] = {}

    def _set_metric(metric_key: str, value: Any) -> None:
        numeric_value = _as_float(value)
        if numeric_value is None:
            return
        canonical_metrics[metric_key] = float(numeric_value)
        metric_units[metric_key] = str(CANONICAL_METRIC_UNITS.get(metric_key, ""))

    temperature_export = dict(dict(exports or {}).get("temperature", {}) or {})
    temperature_direct = dict(temperature_export.get("direct_metrics", {}) or {})
    temperature_grid = dict(temperature_export.get("grid_statistics", {}) or {})
    max_temp_c = _as_float(driver_metrics.get("max_temp"))
    min_temp_c = _as_float(driver_metrics.get("min_temp"))
    avg_temp_c = _as_float(driver_metrics.get("avg_temp"))
    use_grid_temperature = _prefer_temperature_grid_stats(
        direct_metrics=temperature_direct,
        grid_statistics=temperature_grid,
    )
    _set_metric(
        "max_temp",
        temperature_grid.get("max")
        if use_grid_temperature and _as_float(temperature_grid.get("max")) is not None
        else (
            temperature_direct.get("max_temp")
            if _as_float(temperature_direct.get("max_temp")) is not None
            else (max_temp_c + 273.15 if max_temp_c is not None else None)
        ),
    )
    _set_metric(
        "min_temp",
        temperature_grid.get("min")
        if use_grid_temperature and _as_float(temperature_grid.get("min")) is not None
        else (
            temperature_direct.get("min_temp")
            if _as_float(temperature_direct.get("min_temp")) is not None
            else (min_temp_c + 273.15 if min_temp_c is not None else None)
        ),
    )
    _set_metric(
        "avg_temp",
        temperature_grid.get("mean")
        if use_grid_temperature and _as_float(temperature_grid.get("mean")) is not None
        else (
            temperature_direct.get("avg_temp")
            if _as_float(temperature_direct.get("avg_temp")) is not None
            else (avg_temp_c + 273.15 if avg_temp_c is not None else None)
        ),
    )
    direct_temp_gradient = _as_float(temperature_direct.get("temp_gradient"))
    grid_temp_gradient = (
        float(max(float(temperature_grid.get("max")) - float(temperature_grid.get("min")), 0.0))
        if _as_float(temperature_grid.get("max")) is not None
        and _as_float(temperature_grid.get("min")) is not None
        else None
    )
    if use_grid_temperature and grid_temp_gradient is not None:
        _set_metric("temp_gradient", grid_temp_gradient)
    elif direct_temp_gradient is not None:
        _set_metric("temp_gradient", direct_temp_gradient)
    elif max_temp_c is not None and min_temp_c is not None:
        _set_metric("temp_gradient", max(max_temp_c - min_temp_c, 0.0))
    else:
        _set_metric("temp_gradient", driver_metrics.get("temp_gradient"))

    stress_export = dict(dict(exports or {}).get("stress", {}) or {})
    stress_direct = dict(stress_export.get("direct_metrics", {}) or {})
    if stress_direct:
        _set_metric("max_stress", stress_direct.get("max_stress"))
    else:
        driver_stress = _as_float(driver_metrics.get("max_stress"))
        stress_grid_max = _as_float(dict(stress_export.get("grid_statistics", {}) or {}).get("max"))
        _, normalized_stress = _infer_driver_metric_unit(
            "max_stress",
            driver_value=driver_stress,
            canonical_value=stress_grid_max,
        )
        _set_metric("max_stress", normalized_stress if normalized_stress is not None else driver_stress)

    displacement_export = dict(dict(exports or {}).get("displacement", {}) or {})
    displacement_direct = dict(displacement_export.get("direct_metrics", {}) or {})
    if displacement_direct:
        _set_metric("max_displacement", displacement_direct.get("max_displacement"))
    else:
        driver_displacement = _as_float(driver_metrics.get("max_displacement"))
        displacement_grid_max = _as_float(dict(displacement_export.get("grid_statistics", {}) or {}).get("max"))
        _, normalized_displacement = _infer_driver_metric_unit(
            "max_displacement",
            driver_value=driver_displacement,
            canonical_value=displacement_grid_max,
        )
        _set_metric(
            "max_displacement",
            normalized_displacement if normalized_displacement is not None else driver_displacement,
        )

    _set_metric("first_modal_freq", driver_metrics.get("first_modal_freq"))

    allowable_stress_mpa = _as_float(driver_config.get("structural_allowable_stress_mpa"))
    max_stress_pa = _as_float(canonical_metrics.get("max_stress"))
    if allowable_stress_mpa is not None and max_stress_pa is not None and max_stress_pa > 0.0:
        _set_metric("safety_factor", (allowable_stress_mpa * 1e6) / max_stress_pa)
    else:
        _set_metric("safety_factor", driver_metrics.get("safety_factor"))

    grid_lookup = {
        "max_temp": _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("max")),
        "min_temp": _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("min")),
        "avg_temp": _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("mean")),
        "temp_gradient": (
            float(
                max(
                    _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("max"))
                    - _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("min")),
                    0.0,
                )
            )
            if _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("max")) is not None
            and _as_float(dict(temperature_export.get("grid_statistics", {}) or {}).get("min")) is not None
            else None
        ),
        "max_stress": _as_float(dict(stress_export.get("grid_statistics", {}) or {}).get("max")),
        "max_displacement": _as_float(dict(displacement_export.get("grid_statistics", {}) or {}).get("max")),
    }

    for metric_key, canonical_value in canonical_metrics.items():
        driver_value = _as_float(driver_metrics.get(metric_key))
        inferred_unit, normalized_driver_value = _infer_driver_metric_unit(
            metric_key,
            driver_value=driver_value,
            canonical_value=canonical_value,
        )
        if inferred_unit:
            driver_metric_units[metric_key] = str(inferred_unit)
        audit_entry: Dict[str, Any] = {
            "canonical_value": float(canonical_value),
            "canonical_unit": str(metric_units.get(metric_key, "")),
        }
        if driver_value is not None:
            audit_entry["driver_value"] = float(driver_value)
        if inferred_unit is not None:
            audit_entry["driver_unit"] = str(inferred_unit)
        if normalized_driver_value is not None:
            audit_entry["driver_value_normalized"] = float(normalized_driver_value)
            audit_entry["driver_vs_canonical_delta"] = float(normalized_driver_value - canonical_value)
        grid_value = _as_float(grid_lookup.get(metric_key))
        if grid_value is not None:
            audit_entry["grid_value"] = float(grid_value)
            audit_entry["grid_unit"] = str(metric_units.get(metric_key, ""))
            audit_entry["grid_vs_canonical_delta"] = float(grid_value - canonical_value)
        metric_audit[metric_key] = audit_entry

    return canonical_metrics, metric_units, driver_metric_units, metric_audit


def build_driver_config(config: Mapping[str, Any], case_parameters: Mapping[str, Any]) -> Dict[str, Any]:
    physics = dict(config.get("physics", {}) or {})
    structural_base = float(physics.get("structural_launch_accel_g", 6.0))
    structural_scale = float(case_parameters.get("structural_load_scale", 1.0))
    structural_lateral_accel_ratio = float(physics.get("structural_lateral_accel_ratio", 0.0))
    physics_profile = str(
        case_parameters.get(
            "physics_profile",
            physics.get("physics_profile", ""),
        )
        or ""
    ).strip()
    driver_config = {
        "save_mph_each_eval": True,
        "save_mph_on_failure": True,
        "save_mph_only_latest": True,
        "enable_structural_real": True,
        "enable_power_network_real": False,
        "enable_power_comsol_real": False,
        "enable_coupled_multiphysics_real": False,
        "ambient_temperature_k": float(
            case_parameters.get(
                "ambient_temperature_k",
                physics.get("ambient_temperature_k", 293.15),
            )
        ),
        "surface_temperature_k": float(
            case_parameters.get(
                "surface_temperature_k",
                physics.get("surface_temperature_k", 273.15),
            )
        ),
        "initial_temperature_k": float(
            case_parameters.get(
                "initial_temperature_k",
                physics.get("initial_temperature_k", 293.15),
            )
        ),
        "structural_launch_accel_g": structural_base * structural_scale,
        "structural_lateral_accel_ratio": structural_lateral_accel_ratio,
        "structural_allowable_stress_mpa": float(
            physics.get("structural_allowable_stress_mpa", 150.0)
        ),
        "structural_youngs_modulus_gpa": float(
            physics.get("structural_youngs_modulus_gpa", 70.0)
        ),
        "structural_poissons_ratio": float(
            physics.get("structural_poissons_ratio", 0.33)
        ),
        "structural_density_kg_m3": float(
            physics.get("structural_density_kg_m3", 2700.0)
        ),
    }
    if physics_profile:
        driver_config["physics_profile"] = physics_profile
    if "enable_canonical_thermal_path" in physics:
        driver_config["enable_canonical_thermal_path"] = bool(
            physics.get("enable_canonical_thermal_path")
        )
    if "orbital_thermal_loads_available" in physics:
        driver_config["orbital_thermal_loads_available"] = bool(
            physics.get("orbital_thermal_loads_available")
        )
    if "enable_power_continuation_ramp" in physics:
        driver_config["enable_power_continuation_ramp"] = bool(
            physics.get("enable_power_continuation_ramp")
        )
    return driver_config


def _build_field_dataset_candidates(
    driver: Any,
    dataset_candidates: Sequence[Optional[str]],
) -> Dict[str, List[Optional[str]]]:
    ordered_all = list(dataset_candidates)
    modal_dataset = None
    structural_dataset = None

    if driver is not None:
        try:
            modal_dataset = driver._select_modal_result_dataset(
                dataset_candidates=list(dataset_candidates)
            )
        except Exception:
            modal_dataset = None
        try:
            structural_dataset = driver._select_structural_stationary_dataset(
                dataset_candidates=list(dataset_candidates)
            )
        except Exception:
            structural_dataset = None

    def ordered_preferred(preferred: Optional[str], *, exclude: Sequence[Optional[str]] = ()) -> List[Optional[str]]:
        excluded = {item for item in exclude if item is not None}
        result: List[Optional[str]] = []
        if preferred is not None:
            result.append(preferred)
        for item in ordered_all:
            if item == preferred:
                continue
            if item is not None and item in excluded:
                continue
            result.append(item)
        return result

    return {
        "temperature": ordered_preferred(None),
        "stress": ordered_preferred(structural_dataset, exclude=[modal_dataset]),
        "displacement": ordered_preferred(structural_dataset, exclude=[modal_dataset]),
        "_meta": {
            "structural_dataset": structural_dataset,
            "modal_dataset": modal_dataset,
        },
    }


def run_case_fields(
    *,
    case_dir: Path,
    config: Mapping[str, Any],
    driver_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    resolution: Sequence[int] | None = None,
) -> Dict[str, Any]:
    design_state = load_design_state(case_dir / "design_state.json")
    case_parameters = read_json(case_dir / "case_parameters.json")
    tensor_cfg = dict(config.get("tensor", {}) or {})
    export_resolution = resolution or tensor_cfg.get("resolution", [64, 64, 64])
    driver_cfg = build_driver_config(config, case_parameters)
    driver = (driver_factory or _load_driver_factory())(driver_cfg)

    field_root = case_dir / "field_exports"
    vtu_dir = field_root / "vtu"
    grid_dir = field_root / "grid"
    vtu_dir.mkdir(parents=True, exist_ok=True)
    grid_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "case_id": case_parameters.get("case_id", case_dir.name),
        "case_dir": str(case_dir),
        "driver_config": driver_cfg,
        "exports": {},
        "errors": [],
    }
    summary = materialize_contract_payload(summary)

    try:
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={"experiment_dir": str(case_dir)},
        )
        result = driver.run_simulation(request)
        write_json(field_root / "simulation_result.json", result.model_dump(mode="json"))
        summary["simulation_success"] = bool(result.success)
        summary["driver_metrics"] = dict(result.metrics)
        summary["metrics"] = dict(result.metrics)
        raw_data = dict(result.raw_data or {})
        summary["raw_data"] = dict(raw_data)
        source_claim = dict(raw_data.get("source_claim", {}) or {})
        if source_claim:
            summary["source_claim"] = source_claim
        if raw_data.get("field_export_registry"):
            summary["field_export_registry"] = dict(
                dict(raw_data.get("field_export_registry", {}) or {})
            )
        if raw_data.get("physics_profile_contract"):
            summary["physics_profile_contract"] = dict(
                dict(raw_data.get("physics_profile_contract", {}) or {})
            )
        if raw_data.get("simulation_metric_unit_contract"):
            summary["simulation_metric_unit_contract"] = dict(
                dict(raw_data.get("simulation_metric_unit_contract", {}) or {})
            )
        summary = materialize_contract_payload(
            summary,
            claim=source_claim,
            contract_bundle=dict(raw_data.get("contract_bundle", {}) or {}),
            source_payload=raw_data,
        )
        if not result.success:
            summary["errors"].append(str(result.error_message or "simulation_failed"))
            write_json(field_root / "manifest.json", summary)
            return summary

        model = driver.model
        dataset_candidates = _list_dataset_candidates(model)
        summary["dataset_candidates"] = [item for item in dataset_candidates if item is not None]
        field_dataset_candidates = _build_field_dataset_candidates(driver, dataset_candidates)
        summary["resolved_datasets"] = dict(field_dataset_candidates.get("_meta", {}) or {})

        for field_name, spec in FIELD_EXPORT_SPECS.items():
            field_entry: Dict[str, Any] = {}
            try:
                registry_key = str(spec.get("registry_key", field_name) or field_name)
                preferred_dataset_candidates = field_dataset_candidates.get(field_name, dataset_candidates)
                vtu_export = export_registered_field_data(
                    driver=driver,
                    registry_key=registry_key,
                    output_path=vtu_dir / f"{field_name}.vtu",
                    export_kind="vtu",
                    resolution=export_resolution,
                )
                grid_export = export_registered_field_data(
                    driver=driver,
                    registry_key=registry_key,
                    output_path=grid_dir / f"{field_name}_grid.txt",
                    export_kind="text",
                    resolution=export_resolution,
                )

                if vtu_export and grid_export:
                    expression = str(vtu_export.get("expression", grid_export.get("expression", "")) or "")
                    preferred_dataset = vtu_export.get("dataset", grid_export.get("dataset"))
                    preferred_candidates = list(vtu_export.get("dataset_candidates", []) or [])
                    field_entry.update(
                        {
                            "registry_key": registry_key,
                            "expression": expression,
                            "unit": str(vtu_export.get("unit", spec.get("unit", "")) or ""),
                            "grid_unit": str(grid_export.get("unit", spec.get("grid_unit", spec.get("unit", ""))) or ""),
                            "preferred_dataset": preferred_dataset,
                            "dataset_candidates": [candidate for candidate in preferred_candidates if candidate is not None],
                            "vtu_dataset": vtu_export.get("dataset"),
                            "grid_dataset": grid_export.get("dataset"),
                            "vtu_path": vtu_export["path"],
                            "grid_path": grid_export["path"],
                        }
                    )
                else:
                    expression, preferred_dataset = resolve_expression(
                        model,
                        expression_candidates=list(spec.get("expression_candidates", [])),
                        unit=str(spec.get("unit", "") or "") or None,
                        dataset_candidates=preferred_dataset_candidates,
                        evaluator=driver,
                    )
                    preferred_candidates = [preferred_dataset] + [
                        candidate for candidate in preferred_dataset_candidates if candidate != preferred_dataset
                    ]
                    vtu_export = export_field_data(
                        model,
                        output_path=vtu_dir / f"{field_name}.vtu",
                        expression=expression,
                        unit=str(spec.get("unit", "") or "") or None,
                        dataset_candidates=preferred_candidates,
                        export_kind="vtu",
                        resolution=export_resolution,
                    )
                    grid_export = export_field_data(
                        model,
                        output_path=grid_dir / f"{field_name}_grid.txt",
                        expression=expression,
                        unit=str(spec.get("grid_unit", spec.get("unit", "")) or "") or None,
                        dataset_candidates=preferred_candidates,
                        export_kind="text",
                        resolution=export_resolution,
                    )
                    field_entry.update(
                        {
                            "registry_key": registry_key,
                            "expression": expression,
                            "unit": str(spec.get("unit", "") or ""),
                            "grid_unit": str(spec.get("grid_unit", spec.get("unit", "")) or ""),
                            "preferred_dataset": preferred_dataset,
                            "dataset_candidates": [candidate for candidate in preferred_candidates if candidate is not None],
                            "vtu_dataset": vtu_export.get("dataset"),
                            "grid_dataset": grid_export.get("dataset"),
                            "vtu_path": vtu_export["path"],
                            "grid_path": grid_export["path"],
                        }
                    )
                try:
                    field_entry["grid_statistics"] = _summarize_regular_grid(grid_export["path"])
                except Exception as exc:
                    field_entry["grid_statistics_error"] = str(exc)
                direct_metrics = _evaluate_direct_field_metrics(
                    driver=driver,
                    field_name=field_name,
                    expression=expression,
                    unit=str(spec.get("unit", "") or "") or None,
                    dataset_candidates=preferred_candidates,
                )
                if direct_metrics:
                    field_entry["direct_metrics"] = direct_metrics
                if "vector_components" in spec:
                    field_entry["vector_grid_paths"] = {}
                    field_entry["vector_component_registry_keys"] = {
                        axis_name: f"displacement_{axis_name}"
                        for axis_name in dict(spec.get("vector_components", {}) or {})
                    }
                    for axis_name, candidates in dict(spec.get("vector_components", {}) or {}).items():
                        component_registry_key = f"displacement_{axis_name}"
                        component_export = export_registered_field_data(
                            driver=driver,
                            registry_key=component_registry_key,
                            output_path=grid_dir / f"{field_name}_{axis_name}_grid.txt",
                            export_kind="text",
                            resolution=export_resolution,
                        )
                        if component_export is None:
                            component_expr, component_dataset = resolve_expression(
                                model,
                                expression_candidates=list(candidates),
                                unit=str(spec.get("grid_unit", "") or "") or None,
                                dataset_candidates=preferred_candidates,
                                evaluator=driver,
                            )
                            component_export = export_field_data(
                                model,
                                output_path=grid_dir / f"{field_name}_{axis_name}_grid.txt",
                                expression=component_expr,
                                unit=str(spec.get("grid_unit", "") or "") or None,
                                dataset_candidates=[component_dataset]
                                + [candidate for candidate in preferred_candidates if candidate != component_dataset],
                                export_kind="text",
                                resolution=export_resolution,
                            )
                        field_entry["vector_grid_paths"][axis_name] = component_export["path"]
            except Exception as exc:
                field_entry["error"] = str(exc)
                summary["errors"].append(f"{field_name}:{exc}")
            summary["exports"][field_name] = field_entry

        canonical_metrics, metric_units, driver_metric_units, metric_audit = _build_metric_payload(
            driver_metrics=dict(summary.get("driver_metrics", {}) or {}),
            exports=dict(summary.get("exports", {}) or {}),
            driver_config=driver_cfg,
        )
        summary["metrics"] = canonical_metrics
        summary["metric_units"] = metric_units
        summary["driver_metric_units"] = driver_metric_units
        summary["metric_audit"] = metric_audit
        metric_audit_payload = {
            "case_id": summary.get("case_id"),
            "case_dir": summary.get("case_dir"),
            "metrics": canonical_metrics,
            "metric_units": metric_units,
            "driver_metrics": dict(summary.get("driver_metrics", {}) or {}),
            "driver_metric_units": driver_metric_units,
            "metric_audit": metric_audit,
        }
        metric_audit_path = field_root / "metric_audit.json"
        write_json(metric_audit_path, metric_audit_payload)
        summary["metric_audit_path"] = str(metric_audit_path)

        write_json(field_root / "manifest.json", summary)
        return summary
    finally:
        try:
            driver.disconnect()
        except Exception:
            pass


def run_dataset_fields(
    *,
    dataset_root: str | Path,
    config: Mapping[str, Any],
    config_path: str | None = None,
    driver_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    resolution: Sequence[int] | None = None,
) -> Dict[str, Any]:
    dataset_path = Path(dataset_root)
    cases = []
    for case_dir in iter_case_dirs(dataset_path):
        if driver_factory is None:
            command = [sys.executable, str(Path(__file__).resolve()), "--case-dir", str(case_dir)]
            if config_path:
                command.extend(["--config", str(config_path)])
            if resolution is not None:
                command.extend(["--resolution", *[str(int(item)) for item in resolution]])
            result = subprocess.run(command, cwd=str(PROJECT_ROOT))
            manifest_path = Path(case_dir) / "field_exports" / "manifest.json"
            if manifest_path.exists():
                case_summary = read_json(manifest_path)
                case_summary["subprocess_returncode"] = int(result.returncode)
            else:
                case_summary = {
                    "case_id": Path(case_dir).name,
                    "case_dir": str(case_dir),
                    "exports": {},
                    "errors": [f"subprocess_failed:{result.returncode}"],
                    "subprocess_returncode": int(result.returncode),
                }
            cases.append(case_summary)
            continue

        cases.append(
            run_case_fields(
                case_dir=case_dir,
                config=config,
                driver_factory=driver_factory,
                resolution=resolution,
            )
        )
    summary = {
        "dataset_root": str(dataset_path),
        "case_count": len(cases),
        "cases": cases,
    }
    write_json(dataset_path / "field_run_summary.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run COMSOL fields for independent demo cases.")
    parser.add_argument("--dataset-root", type=str, default=None, help="Root created by tool_generate_cases.py")
    parser.add_argument("--case-dir", type=str, default=None, help="Run a single case directory.")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    parser.add_argument("--resolution", type=int, nargs=3, default=None, help="Regular-grid export resolution.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    if args.case_dir:
        summary = run_case_fields(
            case_dir=Path(args.case_dir),
            config=config,
            resolution=args.resolution,
        )
        print(f"Processed single case at {args.case_dir} (success={summary.get('simulation_success')})")
        return 0 if not summary.get("errors") else 1

    if not args.dataset_root:
        raise SystemExit("Either --dataset-root or --case-dir is required.")

    summary = run_dataset_fields(
        dataset_root=args.dataset_root,
        config=config,
        config_path=args.config,
        resolution=args.resolution,
    )
    print(f"Processed {summary['case_count']} cases under {args.dataset_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
