from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import yaml

from core.protocol import DesignState, SimulationRequest, SimulationType
from domain.satellite.runtime import evaluate_satellite_likeness_for_scenario
from domain.satellite.scenario import SatelliteScenarioSpec, load_satellite_scenario_spec
from domain.satellite.seed import build_seed_design_state
from geometry.cad_export_occ import export_design_occ
from optimization.modes.mass.pymoo_integration.problem_generator import PymooProblemGenerator
from optimization.modes.mass.pymoo_integration.runner import PymooNSGA2Runner
from optimization.modes.mass.pymoo_integration.specs import (
    ConstraintSpec,
    ObjectiveSpec,
    PymooProblemSpec,
)
from simulation.contracts import evaluate_constraint_records, normalize_runtime_constraints
from simulation.comsol_driver import ComsolDriver


logger = logging.getLogger(__name__)


def load_runtime_config(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_constraints(config: Mapping[str, Any], scenario: SatelliteScenarioSpec) -> Dict[str, Any]:
    merged = dict(dict(config.get("simulation", {}) or {}).get("constraints", {}) or {})
    merged.update(scenario.constraints.model_dump())
    return merged


def _build_objectives(scenario: SatelliteScenarioSpec) -> List[ObjectiveSpec]:
    if scenario.objectives:
        return [
            ObjectiveSpec(
                name=str(item.metric_key),
                metric_key=str(item.metric_key),
                sense=str(item.sense or "minimize"),
                weight=float(item.weight or 1.0),
            )
            for item in list(scenario.objectives or [])
        ]

    return [
        ObjectiveSpec(name="cg_offset", metric_key="cg_offset", sense="minimize", weight=1.0),
        ObjectiveSpec(name="max_temp", metric_key="max_temp", sense="minimize", weight=1.0),
        ObjectiveSpec(name="voltage_drop", metric_key="voltage_drop", sense="minimize", weight=0.6),
        ObjectiveSpec(name="power_margin", metric_key="power_margin", sense="maximize", weight=0.5),
    ]


def _build_constraints(runtime_constraints: Mapping[str, Any]) -> List[ConstraintSpec]:
    constraints = [
        ConstraintSpec(name="collision", metric_key="collision_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="clearance", metric_key="clearance_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="boundary", metric_key="boundary_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="thermal", metric_key="thermal_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="cg_limit", metric_key="cg_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="safety_factor", metric_key="safety_factor_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="modal_freq", metric_key="modal_freq_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="voltage_drop", metric_key="voltage_drop_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="power_margin", metric_key="power_margin_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="mission_keepout", metric_key="mission_keepout_violation", relation="<=", target_value=0.0),
    ]
    if bool(runtime_constraints.get("enforce_power_budget", False)):
        constraints.append(
            ConstraintSpec(name="peak_power", metric_key="peak_power_violation", relation="<=", target_value=0.0)
        )
    return constraints


def _build_initial_population(
    *,
    base_vector: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    pop_size: int,
    seed: int,
    jitter_mm: float,
) -> np.ndarray:
    rng = np.random.default_rng(int(seed))
    base = np.asarray(base_vector, dtype=float).reshape(1, -1)
    base_clipped = np.clip(base[0], lower, upper)
    injected = [base_clipped]
    sigma = np.full(base.shape[1], max(float(jitter_mm), 1e-6), dtype=float)
    remaining = max(int(pop_size) - 1, 0)
    local_count = max(remaining // 2, 0)
    global_count = max(remaining - local_count, 0)
    for _ in range(local_count):
        candidate = base_clipped + rng.normal(0.0, sigma, size=base.shape[1])
        injected.append(np.clip(candidate, lower, upper))
    for _ in range(global_count):
        injected.append(rng.uniform(lower, upper))
    return np.asarray(injected, dtype=float)


def _constraint_violation_sum(evaluated: Mapping[str, Any]) -> float:
    constraints = dict(evaluated.get("constraints", {}) or {})
    return float(
        sum(max(_safe_float(value), 0.0) for value in constraints.values())
    )


def _select_initial_population(
    *,
    generator: PymooProblemGenerator,
    candidates: np.ndarray,
    pop_size: int,
) -> np.ndarray:
    if candidates.size == 0:
        raise ValueError("empty_initial_candidate_pool")

    ranked: List[tuple[float, float, int, np.ndarray]] = []
    seen: set[tuple[float, ...]] = set()
    for index in range(candidates.shape[0]):
        candidate = np.asarray(candidates[index], dtype=float)
        dedupe_key = tuple(np.round(candidate, decimals=6).tolist())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        state = generator.codec.decode(candidate)
        evaluated = generator.evaluate_state(state)
        cv = _constraint_violation_sum(evaluated)
        objective_sum = float(sum(_safe_float(value) for value in evaluated.get("objectives", {}).values()))
        ranked.append((cv, objective_sum, index, candidate))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = [item[3] for item in ranked[: max(int(pop_size), 1)]]
    return np.asarray(selected, dtype=float)


def _pick_best_candidate(
    *,
    generator: PymooProblemGenerator,
    vectors: np.ndarray,
    raw_cv: np.ndarray,
) -> tuple[np.ndarray, Dict[str, Any]]:
    if vectors.size == 0:
        raise ValueError("empty_candidate_set")

    best_index = 0
    best_key = (float("inf"), float("inf"))
    best_payload: Dict[str, Any] = {}
    for index in range(vectors.shape[0]):
        state = generator.codec.decode(vectors[index])
        evaluated = generator.evaluate_state(state)
        cv = float(raw_cv[index]) if index < raw_cv.shape[0] and np.isfinite(raw_cv[index]) else float("inf")
        objective_sum = float(sum(float(value) for value in evaluated["objectives"].values()))
        key = (cv, objective_sum)
        if key < best_key:
            best_index = index
            best_key = key
            best_payload = evaluated
    return np.asarray(vectors[best_index], dtype=float), best_payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except Exception:
        return float(default)
    if np.isfinite(numeric):
        return float(numeric)
    return float(default)


def _compute_proxy_violation_breakdown(evaluated: Mapping[str, Any]) -> Dict[str, float]:
    constraints = dict(evaluated.get("constraints", {}) or {})
    return {
        str(name): _safe_float(value)
        for name, value in constraints.items()
    }


def _is_proxy_feasible(evaluated: Mapping[str, Any]) -> bool:
    violations = _compute_proxy_violation_breakdown(evaluated)
    return all(float(value) <= 0.0 for value in violations.values())


def _real_constraint_specs(
    *,
    runtime_constraints: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    limits = normalize_runtime_constraints(runtime_constraints)
    specs: List[Dict[str, Any]] = [
        {
            "code": "collision",
            "metric_key": "num_collisions",
            "relation": "<=",
            "threshold": 0.0,
        },
        {
            "code": "clearance",
            "metric_key": "min_clearance",
            "relation": ">=",
            "threshold": float(limits.get("min_clearance_mm", 3.0)),
        },
        {
            "code": "boundary",
            "metric_key": "boundary_violation",
            "relation": "<=",
            "threshold": 0.0,
        },
        {
            "code": "thermal",
            "metric_key": "max_temp",
            "relation": "<=",
            "threshold": float(limits.get("max_temp_c", 60.0)),
        },
        {
            "code": "cg_limit",
            "metric_key": "cg_offset",
            "relation": "<=",
            "threshold": float(limits.get("max_cg_offset_mm", 20.0)),
        },
        {
            "code": "safety_factor",
            "metric_key": "safety_factor",
            "relation": ">=",
            "threshold": float(limits.get("min_safety_factor", 2.0)),
        },
        {
            "code": "modal_freq",
            "metric_key": "first_modal_freq",
            "relation": ">=",
            "threshold": float(limits.get("min_modal_freq_hz", 55.0)),
        },
        {
            "code": "voltage_drop",
            "metric_key": "voltage_drop",
            "relation": "<=",
            "threshold": float(limits.get("max_voltage_drop_v", 0.5)),
        },
        {
            "code": "power_margin",
            "metric_key": "power_margin",
            "relation": ">=",
            "threshold": float(limits.get("min_power_margin_pct", 10.0)),
        },
        {
            "code": "mission_keepout",
            "metric_key": "mission_keepout_violation",
            "relation": "<=",
            "threshold": 0.0,
        },
    ]
    if bool(limits.get("enforce_power_budget", False)):
        specs.append(
            {
                "code": "peak_power",
                "metric_key": "peak_power",
                "relation": "<=",
                "threshold": float(limits.get("max_power_w", 500.0)),
            }
        )
    return specs


def _compute_constraint_g_value(
    *,
    metric_value: float,
    threshold: float,
    relation: str,
) -> float:
    if str(relation) == ">=":
        return float(threshold) - float(metric_value)
    return float(metric_value) - float(threshold)


def _build_real_feasibility_audit(
    *,
    runtime_constraints: Mapping[str, Any],
    proxy_metrics: Mapping[str, Any],
    final_metrics: Mapping[str, Any],
    source_claim: Mapping[str, Any],
    comsol_raw_data: Mapping[str, Any],
    comsol_success: bool,
) -> Dict[str, Any]:
    proxy_payload = dict(proxy_metrics or {})
    final_payload = dict(final_metrics or {})
    claim = dict(source_claim or {})
    raw_data = dict(comsol_raw_data or {})
    metric_sources = dict(raw_data.get("metric_sources", {}) or {})
    dominant_hotspot = dict(
        raw_data.get("dominant_thermal_hotspot", {})
        or dict(raw_data.get("component_thermal_audit", {}) or {}).get("dominant_hotspot", {})
        or {}
    )

    merged_metrics: Dict[str, float] = {}
    for key in (
        "min_clearance",
        "num_collisions",
        "boundary_violation",
        "cg_offset",
        "mission_keepout_violation",
    ):
        if key in proxy_payload:
            merged_metrics[str(key)] = _safe_float(proxy_payload.get(key))
    for key in (
        "max_temp",
        "safety_factor",
        "first_modal_freq",
        "voltage_drop",
        "power_margin",
        "peak_power",
        "total_power",
    ):
        if key in final_payload:
            merged_metrics[str(key)] = _safe_float(final_payload.get(key))

    blockers: List[str] = []
    if not bool(comsol_success):
        blockers.append("comsol_unsolved")
    if not bool(claim.get("thermal_study_solved", False)):
        blockers.append("thermal_study_unsolved")
    if bool(claim.get("structural_enabled", False)) and not bool(claim.get("structural_study_solved", False)):
        blockers.append("structural_study_unsolved")
    if bool(claim.get("power_comsol_enabled", False)) and not bool(claim.get("power_study_solved", False)):
        blockers.append("power_study_unsolved")

    required_metric_keys = [
        "min_clearance",
        "num_collisions",
        "boundary_violation",
        "cg_offset",
        "mission_keepout_violation",
        "max_temp",
    ]
    if bool(claim.get("structural_enabled", False)):
        required_metric_keys.extend(["safety_factor", "first_modal_freq"])
    if bool(claim.get("power_comsol_enabled", False)):
        required_metric_keys.extend(["voltage_drop", "power_margin"])
        if bool(normalize_runtime_constraints(runtime_constraints).get("enforce_power_budget", False)):
            required_metric_keys.append("peak_power")
    for key in required_metric_keys:
        if key not in merged_metrics:
            blockers.append(f"missing_metric:{key}")

    if blockers:
        return {
            "real_feasibility_evaluated": False,
            "real_feasible": None,
            "real_violation_breakdown": {},
            "real_violation_records": [],
            "real_constraint_details": {},
            "real_constraint_sources": {},
            "real_metric_snapshot": dict(merged_metrics),
            "real_feasibility_blockers": sorted(set(str(item) for item in blockers)),
        }

    specs = _real_constraint_specs(runtime_constraints=runtime_constraints)
    source_map = {
        "collision": "layout_geometry",
        "clearance": "layout_geometry",
        "boundary": "layout_geometry",
        "cg_limit": "layout_mass_properties",
        "mission_keepout": str(proxy_payload.get("mission_source", "mission_fov_proxy") or "mission_fov_proxy"),
        "thermal": str(metric_sources.get("thermal_source", "online_comsol") or "online_comsol"),
        "safety_factor": str(
            metric_sources.get("structural_source", "online_comsol_structural")
            or "online_comsol_structural"
        ),
        "modal_freq": str(
            metric_sources.get("structural_source", "online_comsol_structural")
            or "online_comsol_structural"
        ),
        "voltage_drop": str(metric_sources.get("power_source", "online_comsol_power") or "online_comsol_power"),
        "power_margin": str(metric_sources.get("power_source", "online_comsol_power") or "online_comsol_power"),
        "peak_power": str(metric_sources.get("power_source", "online_comsol_power") or "online_comsol_power"),
    }

    breakdown: Dict[str, float] = {}
    details: Dict[str, Dict[str, Any]] = {}
    for spec in specs:
        code = str(spec["code"])
        metric_key = str(spec["metric_key"])
        metric_value = _safe_float(merged_metrics.get(metric_key))
        threshold = _safe_float(spec.get("threshold"), 0.0)
        relation = str(spec.get("relation", "<=") or "<=")
        g_value = _compute_constraint_g_value(
            metric_value=metric_value,
            threshold=threshold,
            relation=relation,
        )
        breakdown[code] = float(g_value)
        details[code] = {
            "metric_key": metric_key,
            "metric_value": float(metric_value),
            "relation": relation,
            "threshold": float(threshold),
            "violation": float(g_value),
            "source": str(source_map.get(code, "") or ""),
        }
        if code == "thermal" and dominant_hotspot:
            details[code]["dominant_hotspot"] = dict(dominant_hotspot)

    records = evaluate_constraint_records(
        scalar_metrics=merged_metrics,
        runtime_constraints=runtime_constraints,
        enforce_power_budget=None,
        power_budget_metric="peak_power",
        include_runtime_multiphysics_rules=True,
        include_mass_rule=False,
    )
    return {
        "real_feasibility_evaluated": True,
        "real_feasible": bool(all(float(value) <= 0.0 for value in breakdown.values())),
        "real_violation_breakdown": dict(breakdown),
        "real_violation_records": list(records),
        "real_constraint_details": dict(details),
        "real_constraint_sources": dict(source_map),
        "real_metric_snapshot": dict(merged_metrics),
        "real_feasibility_blockers": [],
    }


def _existing_path_map(payload: Mapping[str, Any]) -> Dict[str, Any]:
    existing: Dict[str, Any] = {}
    for key, value in dict(payload or {}).items():
        if isinstance(value, Mapping):
            nested = _existing_path_map(value)
            if nested:
                existing[str(key)] = nested
            continue
        text = str(value or "").strip()
        if text and Path(text).exists():
            existing[str(key)] = text
    return existing


def _layout_top_axis_limits(state: DesignState) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    env = state.envelope
    outer_x = float(env.outer_size.x)
    outer_y = float(env.outer_size.y)
    if str(env.origin or "center").strip().lower() == "corner":
        x_min, x_max = 0.0, outer_x
        y_min, y_max = 0.0, outer_y
    else:
        x_min, x_max = -outer_x / 2.0, outer_x / 2.0
        y_min, y_max = -outer_y / 2.0, outer_y / 2.0
    margin_x = max(outer_x * 0.05, 5.0)
    margin_y = max(outer_y * 0.05, 5.0)
    return (x_min - margin_x, x_max + margin_x), (y_min - margin_y, y_max + margin_y)


def _plot_layout_top(state: DesignState, output_path: Path, title: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 6))
    env = state.envelope.outer_size
    ax.add_patch(
        patches.Rectangle(
            (-float(env.x) / 2.0, -float(env.y) / 2.0),
            float(env.x),
            float(env.y),
            fill=False,
            linestyle="--",
            linewidth=1.5,
            edgecolor="#1f2937",
        )
    )
    palette = {
        "payload": "#d97706",
        "avionics": "#2563eb",
        "adcs": "#0891b2",
        "power": "#059669",
        "battery": "#16a34a",
        "communication": "#7c3aed",
    }
    for comp in list(state.components or []):
        color = palette.get(str(comp.category or "").strip().lower(), "#475569")
        width = float(comp.dimensions.x)
        height = float(comp.dimensions.y)
        center_x = float(comp.position.x)
        center_y = float(comp.position.y)
        ax.add_patch(
            patches.Rectangle(
                (center_x - width / 2.0, center_y - height / 2.0),
                width,
                height,
                linewidth=1.2,
                edgecolor="#0f172a",
                facecolor=color,
                alpha=0.70,
            )
        )
        ax.text(center_x, center_y, comp.id, ha="center", va="center", fontsize=8, color="white")

    x_limits, y_limits = _layout_top_axis_limits(state)
    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    ax.set_title(title)
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linewidth=0.3, alpha=0.5)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def _load_regular_grid(path: Path) -> Optional[np.ndarray]:
    try:
        data = np.genfromtxt(path, delimiter=",", dtype=float)
    except Exception:
        return None
    if data.size == 0:
        return None
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 4:
        return None
    return np.asarray(data[:, :4], dtype=float)


def _render_regular_grid(path: Path, output_path: Path, title: str, unit: str) -> None:
    data = _load_regular_grid(path)
    if data is None:
        return
    x, y, z, value = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
    z_target = float(np.median(z))
    band = max(float(np.std(z)), 1.0)
    mask = np.abs(z - z_target) <= band
    if int(np.count_nonzero(mask)) < 16:
        mask = np.ones_like(z, dtype=bool)
    x_sel, y_sel, v_sel = x[mask], y[mask], value[mask]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5))
    scatter = ax.scatter(x_sel, y_sel, c=v_sel, cmap="viridis", s=14)
    ax.set_title(title)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label(unit)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


@dataclass
class ScenarioExecutionResult:
    run_dir: Path
    summary: Dict[str, Any]


class ScenarioRuntime:
    def __init__(
        self,
        *,
        stack: str,
        config: Mapping[str, Any],
        scenario_path: str | Path,
        run_label: str = "",
    ) -> None:
        self.stack = str(stack or "mass").strip()
        self.config = dict(config or {})
        self.scenario_path = Path(scenario_path).resolve()
        self.scenario = load_satellite_scenario_spec(self.scenario_path)
        self.run_label = str(run_label or "").strip()
        self.run_dir = self._prepare_run_dir()

    def _prepare_run_dir(self) -> Path:
        logging_cfg = dict(self.config.get("logging", {}) or {})
        base_dir = Path(str(logging_cfg.get("base_dir", "experiments"))).resolve()
        date_dir = base_dir / datetime.now().strftime("%Y%m%d")
        short_tag = self.run_label or self.scenario.scenario_id
        run_name = f"{datetime.now().strftime('%H%M%S')}_{self.stack}_{short_tag}"
        run_dir = date_dir / run_name
        (run_dir / "figures").mkdir(parents=True, exist_ok=True)
        (run_dir / "fields").mkdir(parents=True, exist_ok=True)
        (run_dir / "step").mkdir(parents=True, exist_ok=True)
        return run_dir

    def _build_problem_spec(
        self,
        *,
        seed_state: DesignState,
        semantic_zones,
    ) -> PymooProblemSpec:
        runtime_constraints = _merge_constraints(self.config, self.scenario)
        tags = {
            "scenario_id": self.scenario.scenario_id,
            "shell_variant": self.scenario.shell_variant,
        }
        return PymooProblemSpec(
            base_state=seed_state,
            runtime_constraints=runtime_constraints,
            objective_specs=_build_objectives(self.scenario),
            constraint_specs=_build_constraints(runtime_constraints),
            semantic_zones=list(semantic_zones or []),
            tags=tags,
        )

    def _run_optimizer(
        self,
        *,
        problem_spec: PymooProblemSpec,
    ) -> tuple[DesignState, Dict[str, Any], Dict[str, Any]]:
        optimization_cfg = dict(self.config.get("optimization", {}) or {})
        generator = PymooProblemGenerator(problem_spec)
        problem = generator.create_problem()
        base_vector = generator.codec.encode(problem_spec.base_state)
        pop_size = max(int(optimization_cfg.get("pymoo_pop_size", 12) or 12), 4)
        candidate_pool = _build_initial_population(
            base_vector=base_vector,
            lower=generator.codec.xl,
            upper=generator.codec.xu,
            pop_size=max(pop_size * 16, 256),
            seed=int(optimization_cfg.get("pymoo_seed", 42) or 42),
            jitter_mm=float(self.scenario.seed_profile.jitter_mm or 4.0),
        )
        initial_population = _select_initial_population(
            generator=generator,
            candidates=candidate_pool,
            pop_size=pop_size,
        )
        runner = PymooNSGA2Runner(
            pop_size=pop_size,
            n_generations=max(int(optimization_cfg.get("pymoo_n_gen", 6) or 6), 1),
            seed=int(optimization_cfg.get("pymoo_seed", 42) or 42),
            verbose=bool(optimization_cfg.get("pymoo_verbose", False)),
            return_least_infeasible=True,
            initial_population=initial_population,
            algorithm=str(optimization_cfg.get("pymoo_algorithm", "nsga2") or "nsga2"),
            nsga3_ref_dirs_partitions=int(optimization_cfg.get("pymoo_nsga3_ref_dirs_partitions", 0) or 0),
        )
        result = runner.run(problem)
        candidate_vectors = np.asarray(result.pareto_X if result.pareto_X is not None else initial_population, dtype=float)
        candidate_cv = np.asarray(result.pareto_CV if result.pareto_CV is not None else np.full(candidate_vectors.shape[0], np.nan), dtype=float).reshape(-1)
        best_vector, evaluated = _pick_best_candidate(generator=generator, vectors=candidate_vectors, raw_cv=candidate_cv)
        final_state = generator.codec.decode(best_vector)
        return final_state, evaluated, {
            "runner_success": bool(result.success),
            "runner_message": str(result.message),
            "n_gen_completed": int(result.n_gen_completed),
            "best_cv_curve": list(result.best_cv_curve),
            "best_feasible_objective_curve": list(result.best_feasible_objective_curve),
            "aocc_cv": float(result.aocc_cv),
            "aocc_objective": float(result.aocc_objective),
            "metadata": dict(result.metadata or {}),
            "variable_names": [spec.name for spec in list(generator.codec.variable_specs or [])],
            "variable_count": int(generator.codec.n_var),
        }

    def _export_step(self, design_state: DesignState) -> Dict[str, str]:
        step_path = self.run_dir / "step" / f"{self.scenario.scenario_id}.step"
        export_design_occ(design_state, str(step_path))
        return {
            "step_path": str(step_path),
            "geometry_manifest_path": str(step_path.with_suffix(".geometry_manifest.json")),
            "geometry_proxy_manifest_path": str(step_path.with_suffix(".geometry_proxy_manifest.json")),
        }

    def _build_driver_config(self) -> Dict[str, Any]:
        simulation_cfg = dict(self.config.get("simulation", {}) or {})
        constraints = _merge_constraints(self.config, self.scenario)
        simulation_cfg["constraints"] = constraints
        simulation_cfg["backend"] = "comsol"
        simulation_cfg["physics_profile"] = str(self.scenario.comsol_physics_profile)
        simulation_cfg["enable_canonical_thermal_path"] = True
        simulation_cfg["canonical_strict_mode"] = True
        return simulation_cfg

    def _satellite_likeness_gate_mode(self) -> str:
        configured = str(self.config.get("satellite_likeness_gate_mode", "strict") or "strict").strip().lower()
        if configured in {"off", "diagnostic", "strict"}:
            return configured
        return "strict"

    def _evaluate_satellite_likeness(self, design_state: DesignState) -> Dict[str, Any]:
        return evaluate_satellite_likeness_for_scenario(
            design_state,
            scenario=self.scenario,
            default_gate_mode=self._satellite_likeness_gate_mode(),
        )

    def _run_canonical_comsol(
        self,
        *,
        design_state: DesignState,
        step_path: str,
        geometry_manifest_path: str = "",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        driver_cfg = self._build_driver_config()
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={
                "experiment_dir": str(self.run_dir),
                "step_file": step_path,
                "geometry_manifest_path": str(geometry_manifest_path or ""),
            },
        )
        driver = ComsolDriver(driver_cfg)
        try:
            result = driver.run_simulation(request)
            raw_data = dict(result.raw_data or {})
            source_claim = dict(raw_data.get("source_claim", {}) or {})
            requested = str(source_claim.get("requested_physics_profile", "") or "")
            effective = str(source_claim.get("physics_profile", "") or "")
            if requested and requested != effective:
                raise RuntimeError(f"canonical_profile_degraded:{requested}->{effective}")
            simulation_payload = {
                "success": bool(result.success),
                "metrics": dict(result.metrics or {}),
                "raw_data": raw_data,
                "error_message": str(result.error_message or ""),
                "mph_model_path": str(raw_data.get("mph_model_path", "") or ""),
                "model_build_succeeded": bool(raw_data.get("model_build_succeeded", False)),
                "solve_attempted": bool(raw_data.get("solve_attempted", False)),
                "solve_succeeded": bool(raw_data.get("solve_succeeded", False)),
                "field_export_attempted": False,
                "field_export_success": False,
                "field_export_error": "",
                "comsol_execution_stage": str(raw_data.get("comsol_execution_stage", "") or ""),
            }
            field_payload: Dict[str, Any] = {}
            if bool(simulation_payload["solve_succeeded"]):
                simulation_payload["field_export_attempted"] = True
                try:
                    field_payload = self._export_fields(driver=driver)
                    simulation_payload["field_export_success"] = bool(field_payload)
                except Exception as field_exc:
                    simulation_payload["field_export_error"] = str(field_exc)
            return simulation_payload, field_payload
        finally:
            try:
                driver.disconnect()
            except Exception:
                pass

    def _export_fields(self, *, driver: ComsolDriver) -> Dict[str, Any]:
        fields_root = self.run_dir / "fields"
        figures_root = self.run_dir / "figures"
        exports: Dict[str, Any] = {}
        unit_map = {
            "temperature": "K",
            "stress": "Pa",
            "displacement": "m",
        }
        for field_name in list(self.scenario.field_exports or []):
            grid_path = fields_root / f"{field_name}_grid.txt"
            exported = driver.export_registered_field(
                field_name,
                str(grid_path),
                export_kind="text",
                resolution=(48, 48, 32),
            )
            figure_path = figures_root / f"{field_name}.png"
            _render_regular_grid(
                grid_path,
                figure_path,
                title=f"{field_name} field",
                unit=unit_map.get(field_name, str(exported.get("unit", "") or "")),
            )
            exports[field_name] = {
                "grid_path": str(grid_path),
                "figure_path": str(figure_path),
                "dataset": str(exported.get("dataset", "") or ""),
                "expression": str(exported.get("expression", "") or ""),
                "unit": str(exported.get("unit", "") or ""),
            }
        return exports

    def _write_report(
        self,
        *,
        summary: Mapping[str, Any],
    ) -> str:
        report_lines = [
            f"# Scenario Run Report",
            "",
            f"- stack: `{self.stack}`",
            f"- scenario: `{self.scenario.scenario_id}`",
            f"- archetype: `{self.scenario.archetype_id}`",
            f"- status: `{summary.get('status', 'UNKNOWN')}`",
            f"- execution_success: `{summary.get('execution_success', False)}`",
            f"- execution_stage: `{summary.get('execution_stage', '')}`",
            f"- requested_profile: `{summary.get('requested_physics_profile', '')}`",
            f"- effective_profile: `{summary.get('effective_physics_profile', '')}`",
            "",
            "## Optimization",
            f"- variable_count: `{summary.get('variable_count', 0)}`",
            f"- variable_coverage: `{', '.join(list(summary.get('variable_coverage', []) or []))}`",
            f"- best_cv: `{summary.get('best_cv', 'n/a')}`",
            f"- proxy_feasible: `{summary.get('proxy_feasible', False)}`",
            f"- max_temp_proxy: `{summary.get('proxy_metrics', {}).get('max_temp', 'n/a')}`",
            "",
            "## Proxy Violations",
        ]
        for key, value in dict(summary.get("proxy_violation_breakdown", {}) or {}).items():
            report_lines.append(f"- {key}: `{value}`")
        report_lines.extend(
            [
                "",
                "## Satellite Likeness Gate",
                f"- gate_mode: `{summary.get('satellite_likeness_gate_mode', '')}`",
                f"- gate_passed: `{summary.get('satellite_likeness_gate_passed', None)}`",
                f"- candidate_archetype_id: `{dict(summary.get('satellite_layout_candidate', {}) or {}).get('archetype_id', '')}`",
            ]
        )
        for item in list(summary.get("satellite_task_face_resolution", []) or []):
            report_lines.append(
                f"- task_face `{item.get('semantic', '')}` -> `{item.get('face_id', '')}` "
                f"(source=`{item.get('source', '')}`)"
            )
        for item in list(summary.get("satellite_interior_zone_resolution", []) or []):
            report_lines.append(
                f"- zone `{item.get('component_id', '')}` -> `{item.get('zone_id', '')}` "
                f"(category=`{item.get('component_category', '')}`, source=`{item.get('source', '')}`)"
            )
        gate_report = dict(summary.get("satellite_likeness_report", {}) or {})
        for check in list(gate_report.get("checks", []) or []):
            report_lines.append(
                f"- check `{check.get('rule_id', '')}`: passed=`{check.get('passed', False)}` "
                f"message=`{check.get('message', '')}`"
            )
        report_lines.extend(
            [
                "",
                "## COMSOL",
                f"- attempted: `{summary.get('comsol_attempted', False)}`",
                f"- block_reason: `{summary.get('comsol_block_reason', '')}`",
                f"- field_export_attempted: `{summary.get('field_export_attempted', False)}`",
                f"- comsol_error_message: `{summary.get('comsol_error_message', '')}`",
                "",
                "## Real Feasibility",
                f"- evaluated: `{summary.get('real_feasibility_evaluated', False)}`",
                f"- real_feasible: `{summary.get('real_feasible', None)}`",
            ]
        )
        for blocker in list(summary.get("real_feasibility_blockers", []) or []):
            report_lines.append(f"- blocker: `{blocker}`")
        for key, value in dict(summary.get("real_violation_breakdown", {}) or {}).items():
            detail = dict(summary.get("real_constraint_details", {}) or {}).get(str(key), {})
            source = str(detail.get("source", "") or "")
            report_lines.append(
                f"- {key}: `{value}`"
                + (f" (source=`{source}`)" if source else "")
            )
        shell_contact_audit = dict(summary.get("shell_contact_audit", {}) or {})
        report_lines.extend(
            [
                "",
                "## Shell Contact Audit",
                f"- geometry_is_assembly: `{shell_contact_audit.get('geometry_is_assembly', False)}`",
                f"- required_count: `{shell_contact_audit.get('required_count', 0)}`",
                f"- applied_count: `{shell_contact_audit.get('applied_count', 0)}`",
                f"- unresolved_count: `{shell_contact_audit.get('unresolved_count', 0)}`",
            ]
        )
        for item in list(shell_contact_audit.get("components", []) or []):
            report_lines.append(
                f"- {item.get('component_id', '')}: "
                f"status=`{item.get('selection_status', '')}` "
                f"mount_face=`{item.get('mount_face', '')}` "
                f"applied=`{item.get('applied', False)}` "
                f"shared_boundaries=`{','.join(str(boundary_id) for boundary_id in list(item.get('effective_boundary_ids', []) or []))}` "
                f"component_domains=`{','.join(str(domain_id) for domain_id in list(item.get('component_domain_ids', []) or []))}` "
                f"shell_domains=`{','.join(str(domain_id) for domain_id in list(item.get('shell_domain_ids', []) or []))}`"
            )
        component_thermal_audit = dict(summary.get("component_thermal_audit", {}) or {})
        dominant_hotspot = dict(
            summary.get("dominant_thermal_hotspot", {})
            or component_thermal_audit.get("dominant_hotspot", {})
            or {}
        )
        report_lines.extend(
            [
                "",
                "## Component Thermal Audit",
                f"- evaluated_components: `{component_thermal_audit.get('evaluated_component_count', 0)}` / "
                f"`{component_thermal_audit.get('expected_component_count', 0)}`",
            ]
        )
        if dominant_hotspot:
            report_lines.append(
                f"- dominant_hotspot: `{dominant_hotspot.get('component_id', '')}` "
                f"max_temp_c=`{dominant_hotspot.get('max_temp_c', 'n/a')}` "
                f"avg_temp_c=`{dominant_hotspot.get('avg_temp_c', 'n/a')}` "
                f"domains=`{','.join(str(item) for item in list(dominant_hotspot.get('domain_ids', []) or []))}`"
            )
        for item in list(component_thermal_audit.get("components", []) or []):
            report_lines.append(
                f"- {item.get('component_id', '')}: "
                f"evaluated=`{item.get('evaluated', False)}` "
                f"max_temp_c=`{item.get('max_temp_c', 'n/a')}` "
                f"avg_temp_c=`{item.get('avg_temp_c', 'n/a')}` "
                f"domains=`{','.join(str(domain_id) for domain_id in list(item.get('domain_ids', []) or []))}` "
                f"status=`{item.get('selection_status', '')}`"
            )
        report_lines.extend(
            [
                "",
                "## Final Metrics",
            ]
        )
        for key, value in dict(summary.get("final_metrics", {}) or {}).items():
            report_lines.append(f"- {key}: `{value}`")
        report = "\n".join(report_lines) + "\n"
        report_path = self.run_dir / "report.md"
        report_path.write_text(report, encoding="utf-8")
        return str(report_path)

    def execute(
        self,
    ) -> ScenarioExecutionResult:
        scenario_spec_path = self.run_dir / "scenario_spec.json"
        seed_design_state_path = self.run_dir / "design_state.seed.json"
        placement_state_path = self.run_dir / "placement_state.json"
        final_design_state_path = self.run_dir / "design_state.final.json"
        layout_seed_figure = self.run_dir / "figures" / "layout_seed_top.png"
        layout_final_figure = self.run_dir / "figures" / "layout_final_top.png"
        summary_path = self.run_dir / "summary.json"
        result_index_path = self.run_dir / "result_index.json"

        artifact_candidates: Dict[str, Any] = {
            "scenario_spec_path": str(scenario_spec_path),
            "seed_design_state_path": str(seed_design_state_path),
            "placement_state_path": str(placement_state_path),
            "final_design_state_path": str(final_design_state_path),
            "layout_seed_figure": str(layout_seed_figure),
            "layout_final_figure": str(layout_final_figure),
        }
        field_payload: Dict[str, Any] = {}
        source_claim: Dict[str, Any] = {}
        simulation_payload: Dict[str, Any] = {}
        summary: Dict[str, Any] = {
            "status": "FAILED",
            "execution_success": False,
            "stack": self.stack,
            "scenario_id": self.scenario.scenario_id,
            "archetype_id": self.scenario.archetype_id,
            "scenario_path": str(self.scenario_path),
            "rule_profile": self.scenario.rule_profile,
            "requested_physics_profile": str(self.scenario.comsol_physics_profile),
            "effective_physics_profile": "",
            "execution_stage": "",
            "proxy_feasible": False,
            "proxy_violation_breakdown": {},
            "proxy_metrics": {},
            "satellite_likeness_gate_mode": self._satellite_likeness_gate_mode(),
            "satellite_likeness_gate_passed": None,
            "satellite_layout_candidate": {},
            "satellite_likeness_report": {},
            "satellite_task_face_resolution": [],
            "satellite_interior_zone_resolution": [],
            "real_feasibility_evaluated": False,
            "real_feasible": None,
            "real_violation_breakdown": {},
            "real_violation_records": [],
            "real_constraint_details": {},
            "real_constraint_sources": {},
            "real_metric_snapshot": {},
            "real_feasibility_blockers": [],
            "final_metrics": {},
            "shell_contact_audit": {},
            "component_thermal_audit": {},
            "dominant_thermal_hotspot": {},
            "comsol_success": False,
            "comsol_attempted": False,
            "comsol_block_reason": "",
            "comsol_error_message": "",
            "field_export_attempted": False,
            "field_export_error": "",
            "final_mph_path": "",
            "variable_count": 0,
            "variable_names": [],
            "variable_coverage": ["position"],
            "catalog_dimensions_locked": True,
            "best_cv": None,
            "pymoo": {},
            "source_claim": {},
            "comsol_raw_data": {},
        }

        try:
            _write_json(scenario_spec_path, self.scenario.model_dump())

            seed_state, placements, semantic_zones = build_seed_design_state(self.scenario)
            _write_json(seed_design_state_path, seed_state.model_dump(mode="json"))
            _write_json(
                placement_state_path,
                {"placements": [item.model_dump() for item in placements]},
            )
            _plot_layout_top(seed_state, layout_seed_figure, "Seed Layout Top View")
            summary["execution_stage"] = "seed_built"

            problem_spec = self._build_problem_spec(
                seed_state=seed_state,
                semantic_zones=semantic_zones,
            )
            optimized_state, evaluated, optimization_payload = self._run_optimizer(problem_spec=problem_spec)
            optimized_state.iteration = 1
            _write_json(final_design_state_path, optimized_state.model_dump(mode="json"))
            _plot_layout_top(optimized_state, layout_final_figure, "Final Layout Top View")

            summary["execution_stage"] = "proxy_optimized"
            summary["proxy_metrics"] = dict(evaluated.get("metrics", {}) or {})
            summary["proxy_violation_breakdown"] = _compute_proxy_violation_breakdown(evaluated)
            summary["proxy_feasible"] = bool(_is_proxy_feasible(evaluated))
            summary["variable_count"] = int(optimization_payload.get("variable_count", 0))
            summary["variable_names"] = list(optimization_payload.get("variable_names", []) or [])
            summary["best_cv"] = (
                float(min(list(optimization_payload.get("best_cv_curve", [float("inf")]) or [float("inf")])))
                if list(optimization_payload.get("best_cv_curve", []) or [])
                else None
            )
            summary["pymoo"] = optimization_payload

            if not bool(summary["proxy_feasible"]):
                summary["comsol_block_reason"] = "proxy_infeasible"
            else:
                summary["execution_stage"] = "proxy_feasible"
                likeness_payload = self._evaluate_satellite_likeness(optimized_state)
                summary["satellite_likeness_gate_mode"] = str(
                    likeness_payload.get("gate_mode", self._satellite_likeness_gate_mode()) or self._satellite_likeness_gate_mode()
                )
                summary["satellite_likeness_gate_passed"] = likeness_payload.get("gate_passed", None)
                summary["satellite_layout_candidate"] = dict(likeness_payload.get("candidate", {}) or {})
                summary["satellite_likeness_report"] = dict(likeness_payload.get("gate_report", {}) or {})
                summary["satellite_task_face_resolution"] = list(
                    likeness_payload.get("task_face_resolution", []) or []
                )
                summary["satellite_interior_zone_resolution"] = list(
                    likeness_payload.get("interior_zone_resolution", []) or []
                )
                if (
                    summary["satellite_likeness_gate_mode"] != "off"
                    and summary["satellite_likeness_gate_passed"] is False
                ):
                    summary["comsol_block_reason"] = "satellite_likeness_failed"
                else:
                    try:
                        step_payload = self._export_step(optimized_state)
                        artifact_candidates.update(step_payload)
                        summary["execution_stage"] = "step_exported"
                    except Exception as step_exc:
                        summary["comsol_block_reason"] = "step_export_failed"
                        summary["comsol_error_message"] = str(step_exc)
                    else:
                        summary["comsol_attempted"] = True
                        simulation_payload, field_payload = self._run_canonical_comsol(
                            design_state=optimized_state,
                            step_path=step_payload["step_path"],
                            geometry_manifest_path=step_payload.get("geometry_manifest_path", ""),
                        )
                        source_claim = dict(
                            dict(simulation_payload.get("raw_data", {}) or {}).get("source_claim", {}) or {}
                        )
                        summary["requested_physics_profile"] = str(
                            source_claim.get("requested_physics_profile", "") or self.scenario.comsol_physics_profile
                        )
                        summary["effective_physics_profile"] = str(source_claim.get("physics_profile", "") or "")
                        summary["final_metrics"] = dict(simulation_payload.get("metrics", {}) or {})
                        summary["shell_contact_audit"] = dict(
                            dict(simulation_payload.get("raw_data", {}) or {}).get("shell_contact_report", {}) or {}
                        )
                        summary["comsol_success"] = bool(simulation_payload.get("success", False))
                        summary["comsol_error_message"] = str(simulation_payload.get("error_message", "") or "")
                        summary["field_export_attempted"] = bool(simulation_payload.get("field_export_attempted", False))
                        summary["field_export_error"] = str(simulation_payload.get("field_export_error", "") or "")
                        summary["final_mph_path"] = str(simulation_payload.get("mph_model_path", "") or "")
                        summary["source_claim"] = source_claim
                        summary["comsol_raw_data"] = dict(simulation_payload.get("raw_data", {}) or {})
                        summary["component_thermal_audit"] = dict(
                            dict(simulation_payload.get("raw_data", {}) or {}).get("component_thermal_audit", {}) or {}
                        )
                        summary["dominant_thermal_hotspot"] = dict(
                            dict(simulation_payload.get("raw_data", {}) or {}).get("dominant_thermal_hotspot", {})
                            or dict(summary["component_thermal_audit"]).get("dominant_hotspot", {})
                            or {}
                        )
                        if str(summary["final_mph_path"] or "").strip():
                            artifact_candidates["final_mph_path"] = str(summary["final_mph_path"])
                        if bool(simulation_payload.get("model_build_succeeded", False)):
                            summary["execution_stage"] = "comsol_model_built"
                        if bool(simulation_payload.get("solve_succeeded", False)):
                            summary["execution_stage"] = "comsol_solved"
                        if bool(simulation_payload.get("field_export_success", False)):
                            summary["execution_stage"] = "fields_exported"

            field_exports_present = bool(field_payload) or not bool(self.scenario.field_exports)
            summary["execution_success"] = bool(
                bool(summary["proxy_feasible"])
                and bool(summary["comsol_success"])
                and field_exports_present
            )
            summary["status"] = "SUCCESS" if bool(summary["execution_success"]) else "FAILED"
            real_audit = _build_real_feasibility_audit(
                runtime_constraints=_merge_constraints(self.config, self.scenario),
                proxy_metrics=summary.get("proxy_metrics", {}),
                final_metrics=summary.get("final_metrics", {}),
                source_claim=summary.get("source_claim", {}),
                comsol_raw_data=summary.get("comsol_raw_data", {}),
                comsol_success=bool(summary.get("comsol_success", False)),
            )
            summary.update(real_audit)
            if (
                bool(summary["comsol_attempted"])
                and not bool(summary["comsol_success"])
                and not str(summary["comsol_error_message"] or "").strip()
            ):
                summary["comsol_error_message"] = str(
                    simulation_payload.get("comsol_execution_stage", "") or "comsol_failed"
                )
        except Exception as exc:
            logger.exception("Scenario runtime failed")
            summary["status"] = "FAILED"
            summary["runtime_error_type"] = type(exc).__name__
            summary["runtime_error_message"] = str(exc)
            if not str(summary.get("comsol_block_reason", "") or "").strip() and not bool(summary.get("comsol_attempted", False)):
                summary["comsol_block_reason"] = f"runtime_error:{type(exc).__name__}"
            if not str(summary.get("comsol_error_message", "") or "").strip():
                summary["comsol_error_message"] = str(exc)
        finally:
            summary["field_exports"] = {
                field_name: {
                    **dict(payload or {}),
                    **_existing_path_map(
                        {
                            "grid_path": dict(payload or {}).get("grid_path", ""),
                            "figure_path": dict(payload or {}).get("figure_path", ""),
                        }
                    ),
                }
                for field_name, payload in dict(field_payload or {}).items()
            }
            summary["artifacts"] = _existing_path_map(artifact_candidates)
            summary["report_path"] = self._write_report(summary=summary)
            summary["source_claim"] = dict(summary.get("source_claim", {}) or source_claim or {})
            _write_json(summary_path, summary)

            result_index = {
                "scenario_id": self.scenario.scenario_id,
                "run_dir": str(self.run_dir),
                "status": str(summary.get("status", "FAILED")),
                "execution_success": bool(summary.get("execution_success", False)),
                "execution_stage": str(summary.get("execution_stage", "") or ""),
                "satellite_likeness_gate_mode": str(summary.get("satellite_likeness_gate_mode", "") or ""),
                "satellite_likeness_gate_passed": summary.get("satellite_likeness_gate_passed", None),
                "satellite_layout_candidate": dict(summary.get("satellite_layout_candidate", {}) or {}),
                "satellite_likeness_report": dict(summary.get("satellite_likeness_report", {}) or {}),
                "real_feasibility_evaluated": bool(summary.get("real_feasibility_evaluated", False)),
                "real_feasible": summary.get("real_feasible", None),
                "dominant_thermal_hotspot": dict(summary.get("dominant_thermal_hotspot", {}) or {}),
                "shell_contact_audit": dict(summary.get("shell_contact_audit", {}) or {}),
                "summary_path": str(summary_path),
                "report_path": str(summary.get("report_path", "") or ""),
                "artifacts": dict(summary.get("artifacts", {}) or {}),
                "field_exports": {
                    field_name: _existing_path_map(
                        {
                            "grid_path": dict(payload or {}).get("grid_path", ""),
                            "figure_path": dict(payload or {}).get("figure_path", ""),
                        }
                    )
                    for field_name, payload in dict(field_payload or {}).items()
                    if _existing_path_map(
                        {
                            "grid_path": dict(payload or {}).get("grid_path", ""),
                            "figure_path": dict(payload or {}).get("figure_path", ""),
                        }
                    )
                },
            }
            _write_json(result_index_path, result_index)
        return ScenarioExecutionResult(run_dir=self.run_dir, summary=summary)
