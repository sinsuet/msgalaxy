"""
Dynamic `ElementwiseProblem` generation for satellite layout optimization.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from core.protocol import DesignState
from simulation.engineering_proxy import (
    estimate_power_proxy_metrics,
    estimate_structural_proxy_metrics,
)
from simulation.mission_proxy import evaluate_mission_fov_interface
from simulation.thermal_proxy import estimate_proxy_thermal_metrics
from simulation.structural_physics import calculate_cg_offset, calculate_moment_of_inertia

from .codec import DesignStateVectorCodec
from .constraints import compute_boundary_violation, compute_geometry_violation_metrics
from .specs import (
    ConstraintSpec,
    ObjectiveSpec,
    PymooProblemSpec,
    default_constraint_specs,
    default_objective_specs,
)

logger = logging.getLogger(__name__)

try:
    from pymoo.core.problem import ElementwiseProblem

    _PYMOO_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - only raised when pymoo is absent
    ElementwiseProblem = object  # type: ignore[misc, assignment]
    _PYMOO_IMPORT_ERROR = exc


def _require_pymoo() -> None:
    if _PYMOO_IMPORT_ERROR is not None:
        raise ImportError(
            "pymoo is required for optimization/pymoo_integration. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from _PYMOO_IMPORT_ERROR


def _to_float(value: Any, default: float) -> float:
    try:
        numeric = float(value)
        if np.isfinite(numeric):
            return numeric
    except Exception:
        pass
    return float(default)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(default)


class PymooProblemGenerator:
    """
    Build runtime pymoo problem classes from task specs.

    The resulting problem always uses explicit bounds (`xl`, `xu`) and
    inequality constraints in `g(x) <= 0` form.
    """

    def __init__(
        self,
        spec: PymooProblemSpec,
        codec: Optional[DesignStateVectorCodec] = None,
    ) -> None:
        self.spec = spec
        self.codec = codec or DesignStateVectorCodec(
            base_state=spec.base_state,
            variable_specs=spec.variable_specs or None,
            semantic_zones=spec.semantic_zones or None,
        )
        self.objective_specs: List[ObjectiveSpec] = (
            list(spec.objective_specs) if spec.objective_specs else default_objective_specs()
        )
        self.constraint_specs: List[ConstraintSpec] = (
            list(spec.constraint_specs) if spec.constraint_specs else default_constraint_specs()
        )

        constraints = spec.runtime_constraints or {}
        self.min_clearance_mm = _to_float(constraints.get("min_clearance_mm"), 3.0)
        self.max_temp_c = _to_float(constraints.get("max_temp_c"), 60.0)
        self.max_cg_offset_mm = _to_float(constraints.get("max_cg_offset_mm"), 20.0)
        self.min_safety_factor = _to_float(constraints.get("min_safety_factor"), 2.0)
        self.min_modal_freq_hz = _to_float(constraints.get("min_modal_freq_hz"), 55.0)
        self.max_voltage_drop_v = _to_float(constraints.get("max_voltage_drop_v"), 0.5)
        self.min_power_margin_pct = _to_float(constraints.get("min_power_margin_pct"), 10.0)
        self.max_power_w = _to_float(constraints.get("max_power_w"), 500.0)
        self.bus_voltage_v = _to_float(constraints.get("bus_voltage_v"), 28.0)
        self.enforce_power_budget = _to_bool(constraints.get("enforce_power_budget"), False)
        tags = dict(spec.tags or {})
        self.mission_fov_evaluator = tags.get("mission_fov_evaluator", None)
        self.mission_keepout_axis = str(
            tags.get("mission_keepout_axis", constraints.get("mission_keepout_axis", "z"))
        ).strip().lower() or "z"
        self.mission_keepout_center_mm = _to_float(
            tags.get("mission_keepout_center_mm", constraints.get("mission_keepout_center_mm", 0.0)),
            0.0,
        )
        self.mission_min_separation_mm = _to_float(
            tags.get("mission_min_separation_mm", constraints.get("mission_min_separation_mm", 0.0)),
            0.0,
        )
        self.mass_physics_real_only = _to_bool(tags.get("mass_physics_real_only", False), False)
        self.require_structural_real = _to_bool(tags.get("mass_require_structural_real", False), False)
        self.require_power_real = _to_bool(tags.get("mass_require_power_real", False), False)
        self.require_thermal_real = _to_bool(tags.get("mass_require_thermal_real", False), False)
        self.require_mission_real = _to_bool(tags.get("mission_real_required", False), False)

    @property
    def n_var(self) -> int:
        return self.codec.n_var

    @property
    def n_obj(self) -> int:
        return len(self.objective_specs)

    @property
    def n_ieq_constr(self) -> int:
        return len(self.constraint_specs)

    def evaluate_state(self, state: DesignState) -> Dict[str, Dict[str, float]]:
        """
        Evaluate one design state and return objective/constraint metric dictionaries.
        """

        centers, half_sizes = self.codec.geometry_arrays_from_state(state)
        env_min, env_max = self.codec.envelope_bounds

        geom_metrics = compute_geometry_violation_metrics(
            centers=centers,
            half_sizes=half_sizes,
            min_clearance_mm=self.min_clearance_mm,
        )
        boundary_violation = compute_boundary_violation(
            centers=centers,
            half_sizes=half_sizes,
            envelope_min=env_min,
            envelope_max=env_max,
        )

        cg_offset = float(calculate_cg_offset(state))
        moi = np.asarray(calculate_moment_of_inertia(state), dtype=float)
        moi_imbalance = float(np.std(moi))
        total_power = float(sum(comp.power for comp in state.components))
        external_payload = self._evaluate_external_metrics(state=state)

        thermal = self._evaluate_thermal_metrics(
            state=state,
            total_power_w=total_power,
            min_clearance_mm=geom_metrics["min_clearance"],
            num_collisions=int(geom_metrics["num_collisions"]),
            external_payload=external_payload,
        )
        max_temp = thermal["max_temp"]
        structural = self._extract_external_structural_metrics(external_payload)
        if structural is None and bool(self.require_structural_real):
            structural = {
                "max_stress": 1e9,
                "max_displacement": 1e9,
                "first_modal_freq": 0.0,
                "safety_factor": 0.0,
            }
        elif structural is None:
            structural = estimate_structural_proxy_metrics(
                state,
                cg_offset_mm=cg_offset,
                min_clearance_mm=float(geom_metrics["min_clearance"]),
                num_collisions=int(geom_metrics["num_collisions"]),
                boundary_violation_mm=boundary_violation,
            )
        power = self._extract_external_power_metrics(
            external_payload,
            total_power_w=total_power,
        )
        if power is None and bool(self.require_power_real):
            power = {
                "total_power": float(total_power),
                "peak_power": float(max(total_power, self.max_power_w + abs(total_power))),
                "power_margin": -1e6,
                "voltage_drop": 1e6,
            }
        elif power is None:
            power = estimate_power_proxy_metrics(
                state,
                max_power_w=self.max_power_w,
                bus_voltage_v=self.bus_voltage_v,
            )
        safety_factor = _to_float(structural.get("safety_factor"), 0.0)
        first_modal_freq = _to_float(structural.get("first_modal_freq"), 0.0)
        voltage_drop = _to_float(power.get("voltage_drop"), 0.0)
        power_margin = _to_float(power.get("power_margin"), 0.0)
        total_power_metric = _to_float(power.get("total_power"), total_power)
        peak_power = _to_float(power.get("peak_power"), total_power_metric)
        mission = evaluate_mission_fov_interface(
            state,
            evaluator=self.mission_fov_evaluator if callable(self.mission_fov_evaluator) else None,
            axis=self.mission_keepout_axis,
            keepout_center_mm=self.mission_keepout_center_mm,
            min_separation_mm=self.mission_min_separation_mm,
            require_real=bool(self.require_mission_real),
        )
        mission_keepout_violation = _to_float(
            mission.get("mission_keepout_violation"),
            boundary_violation,
        )
        fov_occlusion_proxy = _to_float(mission.get("fov_occlusion_proxy"), mission_keepout_violation)
        emc_separation_proxy = _to_float(mission.get("emc_separation_proxy"), 0.0)

        scalar_metrics = {
            "cg_offset": cg_offset,
            "max_temp": max_temp,
            "moi_imbalance": moi_imbalance,
            "min_clearance": float(geom_metrics["min_clearance"]),
            "num_collisions": float(geom_metrics["num_collisions"]),
            "boundary_violation": boundary_violation,
            "collision_violation": float(geom_metrics["collision_violation"]),
            "clearance_violation": float(geom_metrics["clearance_violation"]),
            "thermal_violation": float(max_temp - self.max_temp_c),
            "cg_violation": float(cg_offset - self.max_cg_offset_mm),
            "total_power": total_power_metric,
            "max_stress": _to_float(structural.get("max_stress"), 0.0),
            "max_displacement": _to_float(structural.get("max_displacement"), 0.0),
            "first_modal_freq": first_modal_freq,
            "safety_factor": safety_factor,
            "peak_power": peak_power,
            "power_margin": power_margin,
            "voltage_drop": voltage_drop,
            "safety_factor_violation": float(self.min_safety_factor - safety_factor),
            "modal_freq_violation": float(self.min_modal_freq_hz - first_modal_freq),
            "voltage_drop_violation": float(voltage_drop - self.max_voltage_drop_v),
            "power_margin_violation": float(self.min_power_margin_pct - power_margin),
            "peak_power_violation": float(peak_power - self.max_power_w),
            "power_budget_enforced": float(1.0 if self.enforce_power_budget else 0.0),
            "mission_keepout_violation": mission_keepout_violation,
            "fov_occlusion_proxy": fov_occlusion_proxy,
            "emc_separation_proxy": emc_separation_proxy,
            "mission_source": str(mission.get("mission_source", "")),
            "mission_interface_status": str(mission.get("interface_status", "")),
        }

        objectives = self._build_objective_values(scalar_metrics)
        constraints = self._build_constraint_values(scalar_metrics)
        return {
            "metrics": scalar_metrics,
            "objectives": objectives,
            "constraints": constraints,
        }

    def _evaluate_external_metrics(self, *, state: DesignState) -> Dict[str, float]:
        if not callable(self.spec.thermal_evaluator):
            return {}
        try:
            raw = self.spec.thermal_evaluator(state) or {}
            if not isinstance(raw, dict):
                return {}
            return dict(raw)
        except Exception as exc:
            if bool(self.require_thermal_real):
                logger.warning("external evaluator failed under real-only requirement: %s", exc)
            else:
                logger.warning("external evaluator failed, fallback to proxy: %s", exc)
            return {}

    def _extract_external_structural_metrics(
        self,
        payload: Optional[Dict[str, float]],
    ) -> Optional[Dict[str, float]]:
        data = dict(payload or {})
        required = ("max_stress", "max_displacement", "first_modal_freq", "safety_factor")
        parsed: Dict[str, float] = {}
        for key in required:
            value = _to_float(data.get(key), np.nan)
            if not np.isfinite(value):
                return None
            parsed[key] = float(value)
        return parsed

    def _extract_external_power_metrics(
        self,
        payload: Optional[Dict[str, float]],
        *,
        total_power_w: float,
    ) -> Optional[Dict[str, float]]:
        data = dict(payload or {})
        required = ("peak_power", "power_margin", "voltage_drop")
        parsed: Dict[str, float] = {}
        for key in required:
            value = _to_float(data.get(key), np.nan)
            if not np.isfinite(value):
                return None
            parsed[key] = float(value)
        total_power = _to_float(data.get("total_power"), total_power_w)
        parsed["total_power"] = float(total_power)
        return parsed

    def _evaluate_thermal_metrics(
        self,
        state: DesignState,
        total_power_w: float,
        min_clearance_mm: float,
        num_collisions: int = 0,
        external_payload: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Thermal metric adapter.

        If `spec.thermal_evaluator` is provided, it is used as source of truth.
        Otherwise, fallback to a smooth proxy that is differentiable enough for
        evolutionary search and keeps execution cheap.
        """
        payload = dict(external_payload or {})
        max_temp = _to_float(payload.get("max_temp"), np.nan)
        if np.isfinite(max_temp):
            min_temp = _to_float(payload.get("min_temp"), max_temp - 10.0)
            avg_temp = _to_float(payload.get("avg_temp"), (max_temp + min_temp) / 2.0)
            return {
                "max_temp": float(max_temp),
                "min_temp": float(min_temp),
                "avg_temp": float(avg_temp),
            }

        if bool(self.require_thermal_real):
            return {
                "max_temp": 9999.0,
                "min_temp": 9999.0,
                "avg_temp": 9999.0,
            }

        # Proxy model used only when no thermal callback is available.
        thermal = estimate_proxy_thermal_metrics(
            state,
            min_clearance_mm=float(min_clearance_mm),
            num_collisions=int(max(num_collisions, 0)),
        )
        return {
            "max_temp": float(thermal["max_temp"]),
            "min_temp": float(thermal["min_temp"]),
            "avg_temp": float(thermal["avg_temp"]),
        }

    def _build_objective_values(self, metrics: Dict[str, float]) -> Dict[str, float]:
        values: Dict[str, float] = {}
        for spec in self.objective_specs:
            raw_value = _to_float(metrics.get(spec.metric_key), 0.0)
            weighted = raw_value * float(spec.weight)
            if spec.sense == "maximize":
                weighted = -weighted
            values[spec.name] = float(weighted)
        return values

    def _build_constraint_values(self, metrics: Dict[str, float]) -> Dict[str, float]:
        values: Dict[str, float] = {}
        for spec in self.constraint_specs:
            metric_value = _to_float(metrics.get(spec.metric_key), 0.0)
            if spec.relation == "<=":
                g_value = metric_value - float(spec.target_value)
            elif spec.relation == ">=":
                g_value = float(spec.target_value) - metric_value
            else:
                g_value = abs(metric_value - float(spec.target_value)) - float(spec.eq_tolerance)
            values[spec.name] = float(g_value)
        return values

    def create_problem(self):
        """Create an executable dynamic `ElementwiseProblem` instance."""

        _require_pymoo()
        generator = self
        objective_order = [obj.name for obj in self.objective_specs]
        constraint_order = [cons.name for cons in self.constraint_specs]

        class GeneratedSatelliteProblem(ElementwiseProblem):
            def __init__(self):
                super().__init__(
                    n_var=generator.n_var,
                    n_obj=generator.n_obj,
                    n_ieq_constr=generator.n_ieq_constr,
                    xl=generator.codec.xl.copy(),
                    xu=generator.codec.xu.copy(),
                )

            def _evaluate(self, x, out, *args, **kwargs):
                state = generator.codec.decode(np.asarray(x, dtype=float))
                evaluated = generator.evaluate_state(state)
                out["F"] = np.asarray(
                    [evaluated["objectives"][name] for name in objective_order],
                    dtype=float,
                )
                out["G"] = np.asarray(
                    [evaluated["constraints"][name] for name in constraint_order],
                    dtype=float,
                )
                out["metrics"] = evaluated["metrics"]

        return GeneratedSatelliteProblem()


def synthesize_problem_class_code(
    n_var: int,
    objective_specs: List[ObjectiveSpec],
    constraint_specs: List[ConstraintSpec],
    class_name: str = "GeneratedSatelliteProblem",
) -> str:
    """
    Return Python source code for a dynamic pymoo problem class.

    The generated class expects an `evaluator(x)` callback that returns:
    {
      "objectives": {"name": value},
      "constraints": {"name": value}
    }
    """

    n_obj = len(objective_specs)
    n_ieq = len(constraint_specs)
    objective_names = [spec.name for spec in objective_specs]
    constraint_names = [spec.name for spec in constraint_specs]

    objective_expr = ", ".join([f"evaluated['objectives']['{name}']" for name in objective_names])
    constraint_expr = ", ".join([f"evaluated['constraints']['{name}']" for name in constraint_names])

    return f'''import numpy as np
from pymoo.core.problem import ElementwiseProblem


class {class_name}(ElementwiseProblem):
    def __init__(self, xl, xu, evaluator):
        super().__init__(
            n_var={n_var},
            n_obj={n_obj},
            n_ieq_constr={n_ieq},
            xl=np.asarray(xl, dtype=float),
            xu=np.asarray(xu, dtype=float),
        )
        self.evaluator = evaluator

    def _evaluate(self, x, out, *args, **kwargs):
        evaluated = self.evaluator(np.asarray(x, dtype=float))
        out["F"] = np.asarray([{objective_expr}], dtype=float)
        out["G"] = np.asarray([{constraint_expr}], dtype=float)
'''
