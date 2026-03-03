"""
Dynamic `ElementwiseProblem` generation for satellite layout optimization.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from core.protocol import DesignState
from simulation.physics_engine import estimate_proxy_thermal_metrics
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

        thermal = self._evaluate_thermal_metrics(
            state=state,
            total_power_w=total_power,
            min_clearance_mm=geom_metrics["min_clearance"],
            num_collisions=int(geom_metrics["num_collisions"]),
        )
        max_temp = thermal["max_temp"]

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
            "total_power": total_power,
        }

        objectives = self._build_objective_values(scalar_metrics)
        constraints = self._build_constraint_values(scalar_metrics)
        return {
            "metrics": scalar_metrics,
            "objectives": objectives,
            "constraints": constraints,
        }

    def _evaluate_thermal_metrics(
        self,
        state: DesignState,
        total_power_w: float,
        min_clearance_mm: float,
        num_collisions: int = 0,
    ) -> Dict[str, float]:
        """
        Thermal metric adapter.

        If `spec.thermal_evaluator` is provided, it is used as source of truth.
        Otherwise, fallback to a smooth proxy that is differentiable enough for
        evolutionary search and keeps execution cheap.
        """

        if callable(self.spec.thermal_evaluator):
            try:
                raw = self.spec.thermal_evaluator(state) or {}
                max_temp = _to_float(raw.get("max_temp"), np.nan)
                if np.isfinite(max_temp):
                    min_temp = _to_float(raw.get("min_temp"), max_temp - 10.0)
                    avg_temp = _to_float(raw.get("avg_temp"), (max_temp + min_temp) / 2.0)
                    return {
                        "max_temp": float(max_temp),
                        "min_temp": float(min_temp),
                        "avg_temp": float(avg_temp),
                    }
            except Exception as exc:
                logger.warning("thermal_evaluator failed, fallback to proxy: %s", exc)

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
