"""
Reflection and feedback utilities for MaaS optimization loops (Phase D).
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .protocol import ModelingConstraint, ModelingIntent


def diagnose_solver_outcome(execution_result: Any) -> Dict[str, Any]:
    """
    Classify solver outcome using result payload and convergence traces.
    """
    if execution_result is None:
        return {"status": "missing_result", "reason": "execution_result is None"}

    success = bool(getattr(execution_result, "success", False))
    traceback_text = str(getattr(execution_result, "traceback_text", "") or "")
    pareto_x = getattr(execution_result, "pareto_X", None)
    best_cv_curve = list(getattr(execution_result, "best_cv_curve", []) or [])
    feasible_obj_curve = list(getattr(execution_result, "best_feasible_objective_curve", []) or [])
    aocc_cv = float(getattr(execution_result, "aocc_cv", 0.0) or 0.0)

    if not success:
        return {
            "status": "runtime_error",
            "reason": "solver_failed",
            "traceback": traceback_text,
            "best_cv": float(np.min(best_cv_curve)) if best_cv_curve else float("inf"),
        }

    has_solution = False
    if pareto_x is not None:
        arr = np.asarray(pareto_x)
        has_solution = arr.size > 0

    best_cv = float(np.min(best_cv_curve)) if best_cv_curve else float("inf")
    has_feasible_point = (
        (np.isfinite(best_cv) and best_cv <= 1e-9)
        or any(np.isfinite(float(v)) for v in feasible_obj_curve)
    )

    if not has_solution:
        if np.isfinite(best_cv) and best_cv > 1e-9:
            return {
                "status": "no_feasible",
                "reason": "constraint_violation_persistent",
                "best_cv": best_cv,
                "aocc_cv": aocc_cv,
            }
        return {
            "status": "empty_solution",
            "reason": "solver_returned_no_points",
            "best_cv": best_cv,
            "aocc_cv": aocc_cv,
        }

    if not has_feasible_point:
        return {
            "status": "no_feasible",
            "reason": "least_infeasible_returned",
            "best_cv": best_cv,
            "aocc_cv": aocc_cv,
        }

    if aocc_cv < 0.1:
        return {
            "status": "feasible_but_stalled",
            "reason": "low_convergence_efficiency",
            "best_cv": best_cv,
            "aocc_cv": aocc_cv,
        }

    return {
        "status": "feasible",
        "reason": "ok",
        "best_cv": best_cv,
        "aocc_cv": aocc_cv,
    }


def suggest_constraint_relaxation(
    intent: ModelingIntent,
    diagnosis: Dict[str, Any],
    max_relax_ratio: float = 0.08,
) -> List[Dict[str, Any]]:
    """
    Generate soft relaxation suggestions when model is consistently infeasible.
    """
    status = diagnosis.get("status")
    if status not in {"no_feasible", "empty_solution", "feasible_but_stalled"}:
        return []

    suggestions: List[Dict[str, Any]] = []
    for cons in intent.hard_constraints:
        relaxed = _relax_constraint(cons, max_relax_ratio=max_relax_ratio)
        if relaxed is None:
            continue
        suggestions.append(relaxed)

    return suggestions


def _relax_constraint(cons: ModelingConstraint, max_relax_ratio: float) -> Dict[str, Any] | None:
    value = float(cons.target_value)
    delta = max(abs(value) * max_relax_ratio, 0.5)

    if cons.relation == "<=":
        new_value = value + delta
    elif cons.relation == ">=":
        new_value = value - delta
    elif cons.relation == "==":
        # Equality usually too strict for engineering search; recommend tolerance form.
        return {
            "constraint": cons.name,
            "type": "convert_equality_to_tolerance",
            "original": f"{cons.metric_key} == {value}",
            "suggested": f"|{cons.metric_key} - {value}| <= {delta}",
            "reason": "equality constraints are often too strict in evolutionary search",
        }
    else:
        return None

    return {
        "constraint": cons.name,
        "type": "bound_relaxation",
        "original_target": value,
        "suggested_target": float(new_value),
        "relation": cons.relation,
        "reason": "infeasible or stalled search suggests hard bounds may be too strict",
    }
