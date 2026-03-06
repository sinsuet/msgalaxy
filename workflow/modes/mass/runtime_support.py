"""
Runtime support mixin for mass mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

import numpy as np

from core.exceptions import SatelliteDesignError
from core.protocol import DesignState
from optimization.modes.mass.maas_audit import select_top_pareto_indices
from optimization.modes.mass.maas_compiler import (
    compile_intent_to_problem_spec,
    formulate_modeling_intent,
)
from optimization.modes.mass.maas_mcts import (
    MCTSEvaluation,
    MCTSNode,
    MCTSVariant,
    MaaSMCTSPlanner,
)
from optimization.modes.mass.maas_reflection import (
    diagnose_solver_outcome,
    suggest_constraint_relaxation,
)
from optimization.modes.mass.operator_actions import (
    apply_operator_program_to_intent,
    build_operator_program_from_context,
)
from optimization.modes.mass.operator_physics_matrix import (
    action_family,
    build_operator_implementation_report,
    evaluate_operator_family_coverage,
    evaluate_operator_realization,
    parse_required_families,
)
from optimization.modes.mass.pymoo_integration import (
    CentroidPushApartRepair,
    OperatorProgramProblemGenerator,
    PymooMOEADRunner,
    PymooNSGA2Runner,
    PymooNSGA3Runner,
    PymooProblemGenerator,
)
from optimization.protocol import ModelingIntent
from simulation.comsol_driver import ComsolDriver
try:
    from simulation.physics_engine import estimate_proxy_thermal_metrics
except ImportError:
    estimate_proxy_thermal_metrics = None

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


class MassRuntimeSupport:
    """Behavior mixin extracted from orchestrator for mass runtime."""
    def _is_maas_retryable(
        self,
        diagnosis: Dict[str, Any],
        retry_on_stall: bool,
    ) -> bool:
        """判断 MaaS 是否应触发下一轮自动松弛重求解。"""
        explicit_retryable = diagnosis.get("retryable", None)
        if explicit_retryable is False:
            return False
        status = str(diagnosis.get("status", ""))
        if status in {"runtime_error", "no_feasible", "empty_solution"}:
            return True
        if retry_on_stall and status == "feasible_but_stalled":
            return True
        return False

    @staticmethod
    def _envelope_bounds_for_state(state: DesignState) -> Tuple[np.ndarray, np.ndarray]:
        env = state.envelope
        size = np.asarray(
            [env.outer_size.x, env.outer_size.y, env.outer_size.z],
            dtype=float,
        )
        if str(env.origin).strip().lower() == "center":
            return (-0.5 * size, 0.5 * size)
        return (np.zeros(3, dtype=float), size)

    @staticmethod
    def _mission_axis_index(axis: str) -> int:
        text = str(axis or "z").strip().lower()
        if text == "x":
            return 0
        if text == "y":
            return 1
        return 2

    @staticmethod
    def _is_mission_critical_component(comp: Any) -> bool:
        text = (
            str(getattr(comp, "category", "") or "").lower()
            + " "
            + str(getattr(comp, "id", "") or "").lower()
        )
        tokens = (
            "payload",
            "camera",
            "optic",
            "star",
            "tracker",
            "sensor",
            "antenna",
        )
        return any(token in text for token in tokens)

    @staticmethod
    def _infer_axis_from_variable_name(name: Any) -> str:
        var_name = str(name or "").strip().lower()
        if var_name.endswith("_x") or var_name.endswith(".x"):
            return "x"
        if var_name.endswith("_y") or var_name.endswith(".y"):
            return "y"
        if var_name.endswith("_z") or var_name.endswith(".z"):
            return "z"
        return ""

    def _precheck_mission_keepout_feasibility(
        self,
        *,
        intent: ModelingIntent,
        base_state: DesignState,
        axis: str,
        keepout_center_mm: float,
        min_separation_mm: float,
    ) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "checked": False,
            "feasible": True,
            "reason": "",
            "axis": str(axis or "z"),
            "keepout_center_mm": float(keepout_center_mm),
            "min_separation_mm": float(max(min_separation_mm, 0.0)),
            "critical_component_ids": [],
            "infeasible_component_ids": [],
            "component_reports": [],
        }

        min_sep = max(float(min_separation_mm), 0.0)

        has_mission_constraint = False
        for cons in list(getattr(intent, "hard_constraints", []) or []):
            metric = str(getattr(cons, "metric_key", "") or "").strip().lower()
            name = str(getattr(cons, "name", "") or "").strip().lower()
            if "mission_keepout" in metric or "fov_keepout" in metric or "mission_keepout" in name:
                has_mission_constraint = True
                break
        if not has_mission_constraint:
            report["reason"] = "mission_constraint_missing"
            return report

        axis_name = str(axis or "z").strip().lower()
        if axis_name not in {"x", "y", "z"}:
            axis_name = "z"
        axis_id = self._mission_axis_index(axis_name)

        bounds_by_component: Dict[str, Dict[str, Tuple[float, float]]] = {}
        for var in list(getattr(intent, "variables", []) or []):
            comp_id = str(getattr(var, "component_id", "") or "").strip()
            if not comp_id:
                continue
            parsed_axis = self._infer_axis_from_variable_name(getattr(var, "name", ""))
            if not parsed_axis:
                continue
            lb_raw = getattr(var, "lower_bound", None)
            ub_raw = getattr(var, "upper_bound", None)
            if lb_raw is None and ub_raw is None:
                continue
            lb = float(lb_raw) if lb_raw is not None else float("-inf")
            ub = float(ub_raw) if ub_raw is not None else float("inf")
            if not np.isfinite(lb):
                lb = float("-1e9")
            if not np.isfinite(ub):
                ub = float("1e9")
            component_bounds = bounds_by_component.setdefault(comp_id, {})
            old = component_bounds.get(parsed_axis, None)
            if old is None:
                component_bounds[parsed_axis] = (float(lb), float(ub))
            else:
                component_bounds[parsed_axis] = (
                    max(float(old[0]), float(lb)),
                    min(float(old[1]), float(ub)),
                )

        env_min, env_max = self._envelope_bounds_for_state(base_state)
        all_components = list(getattr(base_state, "components", []) or [])
        critical = [comp for comp in all_components if self._is_mission_critical_component(comp)]
        if not critical:
            critical = list(all_components)
        report["critical_component_ids"] = [str(getattr(comp, "id", "") or "") for comp in critical]
        report["checked"] = True

        infeasible_ids: List[str] = []
        component_reports: List[Dict[str, Any]] = []
        for comp in critical:
            comp_id = str(getattr(comp, "id", "") or "")
            pos = getattr(comp, "position", None)
            dim = getattr(comp, "dimensions", None)
            if pos is None or dim is None:
                continue

            center_default = np.asarray(
                [
                    float(getattr(pos, "x", 0.0)),
                    float(getattr(pos, "y", 0.0)),
                    float(getattr(pos, "z", 0.0)),
                ],
                dtype=float,
            )
            half = 0.5 * np.asarray(
                [
                    float(getattr(dim, "x", 0.0)),
                    float(getattr(dim, "y", 0.0)),
                    float(getattr(dim, "z", 0.0)),
                ],
                dtype=float,
            )
            axis_half = max(float(half[axis_id]), 0.0)

            raw_bounds = bounds_by_component.get(comp_id, {}).get(axis_name, None)
            if raw_bounds is None:
                lower = float(center_default[axis_id])
                upper = float(center_default[axis_id])
            else:
                lower = float(raw_bounds[0])
                upper = float(raw_bounds[1])

            axis_min = float(env_min[axis_id] + axis_half)
            axis_max = float(env_max[axis_id] - axis_half)
            feasible_lower = max(float(lower), axis_min)
            feasible_upper = min(float(upper), axis_max)

            max_axis_sep = float("-inf")
            feasible = False
            if feasible_lower <= feasible_upper:
                max_center_sep = max(
                    abs(float(feasible_lower) - float(keepout_center_mm)),
                    abs(float(feasible_upper) - float(keepout_center_mm)),
                )
                max_axis_sep = float(max_center_sep - axis_half)
                feasible = bool(max_axis_sep + 1e-9 >= min_sep)

            if not feasible:
                infeasible_ids.append(comp_id)
            component_reports.append(
                {
                    "component_id": comp_id,
                    "axis_half_size_mm": float(axis_half),
                    "candidate_lower_mm": float(feasible_lower),
                    "candidate_upper_mm": float(feasible_upper),
                    "required_min_separation_mm": float(min_sep),
                    "max_achievable_separation_mm": float(max_axis_sep),
                    "feasible": bool(feasible),
                }
            )

        report["component_reports"] = component_reports
        report["infeasible_component_ids"] = list(infeasible_ids)
        report["feasible"] = len(infeasible_ids) == 0
        if report["feasible"]:
            report["reason"] = "feasible"
        else:
            report["reason"] = "mission_keepout_geometrically_infeasible"
        return report

    def _repair_mission_precheck_intent_bounds(
        self,
        *,
        intent: ModelingIntent,
        base_state: DesignState,
        axis: str,
        keepout_center_mm: float,
        min_separation_mm: float,
        precheck: Dict[str, Any],
    ) -> tuple[ModelingIntent, Dict[str, Any]]:
        opt_cfg = dict(self.config.get("optimization", {}) or {})
        enabled = bool(opt_cfg.get("mass_mission_precheck_repair_enabled", True))
        max_expand_mm = max(
            float(opt_cfg.get("mass_mission_precheck_repair_max_expand_mm", 48.0)),
            0.0,
        )
        repair_band_mm = max(
            float(opt_cfg.get("mass_mission_precheck_repair_band_mm", 8.0)),
            0.0,
        )

        precheck_before = dict(precheck or {})
        report: Dict[str, Any] = {
            "attempted": False,
            "enabled": bool(enabled),
            "applied": False,
            "feasible_after": bool(precheck_before.get("feasible", True)),
            "reason": "",
            "axis": str(axis or "z"),
            "keepout_center_mm": float(keepout_center_mm),
            "min_separation_mm": float(max(min_separation_mm, 0.0)),
            "max_expand_mm": float(max_expand_mm),
            "repair_band_mm": float(repair_band_mm),
            "touched_variables": 0,
            "touched_components": [],
            "still_infeasible_component_ids": list(
                precheck_before.get("infeasible_component_ids", []) or []
            ),
            "events": [],
            "precheck_before": precheck_before,
            "precheck_after": precheck_before,
        }
        if not enabled:
            report["reason"] = "repair_disabled"
            return intent, report

        if not bool(precheck_before.get("checked", False)):
            report["reason"] = "precheck_not_checked"
            return intent, report

        if bool(precheck_before.get("feasible", True)):
            report["reason"] = "precheck_already_feasible"
            return intent, report

        min_sep = max(float(min_separation_mm), 0.0)

        axis_name = str(axis or "z").strip().lower()
        if axis_name not in {"x", "y", "z"}:
            axis_name = "z"
        axis_id = self._mission_axis_index(axis_name)
        report["axis"] = axis_name
        report["attempted"] = True

        infeasible_component_ids = [
            str(item).strip()
            for item in list(precheck_before.get("infeasible_component_ids", []) or [])
            if str(item).strip()
        ]
        if not infeasible_component_ids:
            report["reason"] = "no_infeasible_components"
            return intent, report

        component_map = {
            str(getattr(comp, "id", "") or "").strip(): comp
            for comp in list(getattr(base_state, "components", []) or [])
            if str(getattr(comp, "id", "") or "").strip()
        }
        env_min, env_max = self._envelope_bounds_for_state(base_state)

        repaired_intent = intent.model_copy(deep=True)
        axis_vars_by_component: Dict[str, List[Any]] = {}
        for var in list(getattr(repaired_intent, "variables", []) or []):
            comp_id = str(getattr(var, "component_id", "") or "").strip()
            if not comp_id:
                continue
            parsed_axis = self._infer_axis_from_variable_name(getattr(var, "name", ""))
            if parsed_axis != axis_name:
                continue
            axis_vars_by_component.setdefault(comp_id, []).append(var)

        touched_variables = 0
        touched_components: List[str] = []
        events: List[Dict[str, Any]] = []

        for comp_id in infeasible_component_ids:
            comp = component_map.get(comp_id)
            vars_for_component = list(axis_vars_by_component.get(comp_id, []) or [])
            if comp is None:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "reason": "component_missing_in_state",
                    }
                )
                continue
            if not vars_for_component:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "reason": "axis_variable_missing",
                    }
                )
                continue

            pos = getattr(comp, "position", None)
            dim = getattr(comp, "dimensions", None)
            if pos is None or dim is None:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "reason": "component_geometry_missing",
                    }
                )
                continue

            axis_half = max(float(getattr(dim, axis_name, 0.0)) * 0.5, 0.0)
            axis_min = float(env_min[axis_id] + axis_half)
            axis_max = float(env_max[axis_id] - axis_half)
            if axis_min > axis_max:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "reason": "invalid_axis_envelope_interval",
                    }
                )
                continue

            required_center_distance = float(min_sep + axis_half)
            target_positive = float(keepout_center_mm + required_center_distance)
            target_negative = float(keepout_center_mm - required_center_distance)
            candidate_targets: List[tuple[str, float]] = []
            if axis_min <= target_negative <= axis_max:
                candidate_targets.append(("negative", target_negative))
            if axis_min <= target_positive <= axis_max:
                candidate_targets.append(("positive", target_positive))
            if not candidate_targets:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "reason": "no_reachable_keepout_side",
                        "axis_min_mm": float(axis_min),
                        "axis_max_mm": float(axis_max),
                    }
                )
                continue

            current_center = float(getattr(pos, axis_name, keepout_center_mm))
            preferred_side = "positive" if current_center >= float(keepout_center_mm) else "negative"
            chosen_side = preferred_side
            chosen_target = None
            for side, target in candidate_targets:
                if side == preferred_side:
                    chosen_target = float(target)
                    break
            if chosen_target is None:
                candidate_targets.sort(key=lambda item: abs(float(item[1]) - current_center))
                chosen_side = str(candidate_targets[0][0])
                chosen_target = float(candidate_targets[0][1])
            if chosen_target is None:
                continue

            touched_on_component = 0
            for var in vars_for_component:
                lb_raw = getattr(var, "lower_bound", None)
                ub_raw = getattr(var, "upper_bound", None)
                current_lb = float(lb_raw) if lb_raw is not None else float(axis_min)
                current_ub = float(ub_raw) if ub_raw is not None else float(axis_max)
                if not np.isfinite(current_lb):
                    current_lb = float(axis_min)
                if not np.isfinite(current_ub):
                    current_ub = float(axis_max)
                if current_lb > current_ub:
                    current_lb, current_ub = current_ub, current_lb
                current_lb = float(np.clip(current_lb, axis_min, axis_max))
                current_ub = float(np.clip(current_ub, axis_min, axis_max))
                if current_lb > current_ub:
                    anchor = float(np.clip(chosen_target, axis_min, axis_max))
                    current_lb = anchor
                    current_ub = anchor

                reachable_lb = float(max(axis_min, current_lb - max_expand_mm))
                reachable_ub = float(min(axis_max, current_ub + max_expand_mm))
                if chosen_target < reachable_lb - 1e-9 or chosen_target > reachable_ub + 1e-9:
                    continue

                half_band = 0.5 * float(repair_band_mm)
                desired_lb = float(max(axis_min, chosen_target - half_band))
                desired_ub = float(min(axis_max, chosen_target + half_band))
                new_lb = float(min(current_lb, desired_lb))
                new_ub = float(max(current_ub, desired_ub))
                new_lb = float(max(new_lb, reachable_lb))
                new_ub = float(min(new_ub, reachable_ub))
                if chosen_target < new_lb:
                    new_lb = float(chosen_target)
                if chosen_target > new_ub:
                    new_ub = float(chosen_target)
                if new_lb > new_ub:
                    anchor = float(np.clip(chosen_target, reachable_lb, reachable_ub))
                    new_lb = anchor
                    new_ub = anchor

                changed = False
                if abs(new_lb - current_lb) > 1e-9 or lb_raw is None:
                    var.lower_bound = float(new_lb)
                    changed = True
                if abs(new_ub - current_ub) > 1e-9 or ub_raw is None:
                    var.upper_bound = float(new_ub)
                    changed = True
                if not changed:
                    continue

                touched_variables += 1
                touched_on_component += 1

            if touched_on_component > 0:
                touched_components.append(comp_id)
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": True,
                        "target_side": str(chosen_side),
                        "target_center_mm": float(chosen_target),
                        "touched_variables": int(touched_on_component),
                    }
                )
            else:
                events.append(
                    {
                        "component_id": comp_id,
                        "applied": False,
                        "target_side": str(chosen_side),
                        "target_center_mm": float(chosen_target),
                        "reason": "bounded_expand_limit",
                    }
                )

        report["touched_variables"] = int(touched_variables)
        report["touched_components"] = list(touched_components)
        report["events"] = events
        report["applied"] = touched_variables > 0

        if touched_variables > 0:
            repaired_intent.assumptions.append(
                "mission_precheck_repair:"
                f"axis={axis_name},touched_components={len(touched_components)},"
                f"touched_variables={int(touched_variables)}"
            )
            post_precheck = self._precheck_mission_keepout_feasibility(
                intent=repaired_intent,
                base_state=base_state,
                axis=axis_name,
                keepout_center_mm=float(keepout_center_mm),
                min_separation_mm=float(min_sep),
            )
        else:
            post_precheck = precheck_before

        report["precheck_after"] = dict(post_precheck)
        report["feasible_after"] = bool(post_precheck.get("feasible", True))
        report["still_infeasible_component_ids"] = list(
            post_precheck.get("infeasible_component_ids", []) or []
        )
        if bool(report["applied"]) and bool(report["feasible_after"]):
            report["reason"] = "repaired"
        elif bool(report["applied"]):
            report["reason"] = "repair_applied_but_still_infeasible"
        else:
            report["reason"] = "repair_not_applicable"
        return repaired_intent if touched_variables > 0 else intent, report

    def _apply_relaxation_suggestions_to_intent(
        self,
        intent: ModelingIntent,
        suggestions: List[Dict[str, Any]],
        attempt: int,
    ) -> tuple[ModelingIntent, int]:
        """
        把反射阶段建议应用到下一轮 intent（仅处理 bound_relaxation）。
        """
        if not suggestions:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        by_name = {cons.name: cons for cons in next_intent.hard_constraints}
        applied_count = 0
        applied_notes: List[str] = []

        for suggestion in suggestions:
            if str(suggestion.get("type")) != "bound_relaxation":
                continue
            constraint_name = str(suggestion.get("constraint", ""))
            target = suggestion.get("suggested_target")
            if constraint_name not in by_name:
                continue
            try:
                numeric_target = float(target)
            except (TypeError, ValueError):
                continue
            by_name[constraint_name].target_value = numeric_target
            applied_count += 1
            applied_notes.append(f"{constraint_name}={numeric_target:.6g}")

        if applied_count > 0:
            next_intent.assumptions.append(
                f"auto_relax_attempt_{attempt}: " + ", ".join(applied_notes)
            )
        return next_intent, applied_count

    def _decode_maas_candidate_state(
        self,
        execution_result: Any,
        problem_generator: Any,
        base_state: DesignState,
        attempt: int,
    ) -> Optional[DesignState]:
        """从 Pareto 前沿选择代表解并解码。"""
        if execution_result is None or problem_generator is None:
            return None
        if not bool(getattr(execution_result, "success", False)):
            return None
        if getattr(execution_result, "pareto_X", None) is None:
            return None

        try:
            pareto_x = np.asarray(execution_result.pareto_X, dtype=float)
            if pareto_x.ndim == 1:
                best_x = pareto_x
            elif pareto_x.ndim >= 2 and pareto_x.shape[0] > 0:
                n_points = int(pareto_x.shape[0])
                best_idx = 0
                pareto_f_raw = getattr(execution_result, "pareto_F", None)
                pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
                pareto_f: Optional[np.ndarray] = None
                pareto_cv: Optional[np.ndarray] = None

                if pareto_f_raw is not None:
                    parsed_f = np.asarray(pareto_f_raw, dtype=float)
                    if parsed_f.ndim == 2 and parsed_f.shape[0] == n_points:
                        pareto_f = parsed_f

                if pareto_cv_raw is not None:
                    parsed_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
                    if parsed_cv.size == n_points:
                        pareto_cv = parsed_cv

                obj_sum: Optional[np.ndarray] = None
                if pareto_f is not None:
                    obj_sum = np.sum(pareto_f, axis=1)

                # Feasibility-first representative selection:
                # 1) prefer feasible points by CV<=0
                # 2) fallback to least CV if no feasible
                # 3) use objective sum as tie-breaker
                if pareto_cv is not None:
                    finite_cv_idx = np.where(np.isfinite(pareto_cv))[0]
                    feasible_idx = finite_cv_idx[pareto_cv[finite_cv_idx] <= 1e-9]
                    if feasible_idx.size > 0:
                        if obj_sum is not None:
                            finite_obj = feasible_idx[np.isfinite(obj_sum[feasible_idx])]
                            if finite_obj.size > 0:
                                best_idx = int(finite_obj[np.argmin(obj_sum[finite_obj])])
                            else:
                                best_idx = int(feasible_idx[0])
                        else:
                            best_idx = int(feasible_idx[0])
                    elif finite_cv_idx.size > 0:
                        min_cv = float(np.min(pareto_cv[finite_cv_idx]))
                        min_cv_idx = finite_cv_idx[np.isclose(pareto_cv[finite_cv_idx], min_cv)]
                        if obj_sum is not None:
                            finite_obj = min_cv_idx[np.isfinite(obj_sum[min_cv_idx])]
                            if finite_obj.size > 0:
                                best_idx = int(finite_obj[np.argmin(obj_sum[finite_obj])])
                            else:
                                best_idx = int(min_cv_idx[0])
                        else:
                            best_idx = int(min_cv_idx[0])
                elif obj_sum is not None:
                    finite_idx = np.where(np.isfinite(obj_sum))[0]
                    if finite_idx.size > 0:
                        best_idx = int(finite_idx[np.argmin(obj_sum[finite_idx])])

                best_x = pareto_x[best_idx]
            else:
                return None

            candidate = problem_generator.codec.decode(best_x)
            candidate.parent_id = base_state.state_id
            candidate.state_id = f"state_iter_02_maas_candidate_attempt_{attempt:02d}"
            candidate.iteration = 2
            return candidate
        except Exception as exc:
            self.logger.logger.warning(f"Failed to decode best pymoo solution: {exc}")
            return None

    def _extract_operator_program_actions_from_execution(
        self,
        *,
        execution_result: Any,
        problem_generator: Any,
    ) -> List[str]:
        """
        Best-effort extraction of decoded operator actions from Pareto representative.
        """
        if execution_result is None or problem_generator is None:
            return []
        if not bool(getattr(execution_result, "success", False)):
            return []
        codec = getattr(problem_generator, "codec", None)
        if codec is None:
            return []
        decode_program = getattr(codec, "decode_program", None)
        if not callable(decode_program):
            return []

        pareto_x_raw = getattr(execution_result, "pareto_X", None)
        if pareto_x_raw is None:
            return []

        try:
            pareto_x = np.asarray(pareto_x_raw, dtype=float)
        except Exception:
            return []

        if pareto_x.ndim == 1:
            best_x = pareto_x
        elif pareto_x.ndim >= 2 and pareto_x.shape[0] > 0:
            best_idx = 0
            pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
            if pareto_cv_raw is not None:
                try:
                    pareto_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
                    if pareto_cv.size == int(pareto_x.shape[0]):
                        finite = np.where(np.isfinite(pareto_cv))[0]
                        if finite.size > 0:
                            feasible = finite[pareto_cv[finite] <= 1e-9]
                            if feasible.size > 0:
                                best_idx = int(feasible[0])
                            else:
                                min_cv = float(np.min(pareto_cv[finite]))
                                best_idx = int(finite[np.argmin(np.abs(pareto_cv[finite] - min_cv))])
                except Exception:
                    best_idx = 0
            best_x = pareto_x[best_idx]
        else:
            return []

        try:
            program = decode_program(best_x)
        except Exception:
            return []

        actions: List[str] = []
        seen: set[str] = set()
        for action in list(getattr(program, "actions", []) or []):
            name = str(getattr(action, "action", action) or "").strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            actions.append(name)
        return actions

    def _build_maas_runtime_thermal_evaluator(
        self,
        mode: str,
        base_iteration: int,
    ):
        """
        构建运行时热评估回调。

        - `proxy`: 返回 None，使用 problem_generator 内部热代理模型。
        - `online_comsol`: 在 _evaluate_design 上做缓存包装，作为高保真热评估回调。
        """
        normalized = str(mode or "proxy").strip().lower()
        if normalized != "online_comsol":
            return None

        opt_cfg = self.config.get("optimization", {})
        eval_budget = int(opt_cfg.get("mass_online_comsol_eval_budget", 0))
        eval_budget = max(eval_budget, 0)
        budget_control = {"eval_budget": int(eval_budget)}
        geometry_gate_enabled = bool(
            opt_cfg.get("mass_online_comsol_geometry_gate", True)
        )
        progress_log_interval = int(
            opt_cfg.get("mass_online_comsol_stats_log_interval", 0)
        )
        progress_log_interval = max(progress_log_interval, 0)
        cache_quantize_mm = max(
            float(opt_cfg.get("mass_online_comsol_cache_quantize_mm", 0.0)),
            0.0,
        )
        def _safe_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)

        def _safe_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        def _normalize_schedule_mode(value: Any) -> str:
            parsed = str(value or "budget_only").strip().lower()
            if parsed not in {"budget_only", "ucb_topk"}:
                return "budget_only"
            return parsed

        def _sanitize_scheduler_params(params: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "mode": _normalize_schedule_mode(params.get("mode", "budget_only")),
                "top_fraction": float(
                    np.clip(
                        _safe_float(params.get("top_fraction", 0.20), 0.20),
                        0.01,
                        1.0,
                    )
                ),
                "min_observations": max(
                    1,
                    _safe_int(params.get("min_observations", 8), 8),
                ),
                "warmup_calls": max(
                    0,
                    _safe_int(params.get("warmup_calls", 2), 2),
                ),
                "explore_prob": float(
                    np.clip(
                        _safe_float(params.get("explore_prob", 0.05), 0.05),
                        0.0,
                        1.0,
                    )
                ),
                "uncertainty_weight": float(
                    np.clip(
                        _safe_float(params.get("uncertainty_weight", 0.35), 0.35),
                        0.0,
                        5.0,
                    )
                ),
                "uncertainty_scale_mm": max(
                    _safe_float(params.get("uncertainty_scale_mm", 25.0), 25.0),
                    1e-6,
                ),
            }

        schedule_control = _sanitize_scheduler_params({
            "mode": opt_cfg.get("mass_online_comsol_schedule_mode", "budget_only"),
            "top_fraction": opt_cfg.get("mass_online_comsol_schedule_top_fraction", 0.20),
            "min_observations": opt_cfg.get("mass_online_comsol_schedule_min_observations", 8),
            "warmup_calls": opt_cfg.get("mass_online_comsol_schedule_warmup_calls", 2),
            "explore_prob": opt_cfg.get("mass_online_comsol_schedule_explore_prob", 0.05),
            "uncertainty_weight": opt_cfg.get("mass_online_comsol_schedule_uncertainty_weight", 0.35),
            "uncertainty_scale_mm": opt_cfg.get("mass_online_comsol_schedule_uncertainty_scale_mm", 25.0),
        })
        scheduler_rng = np.random.default_rng(
            int(opt_cfg.get("pymoo_seed", 42)) + int(base_iteration)
        )
        scheduler_state: Dict[str, Any] = {
            "scores": [],
            "vectors": [],
        }

        cache: Dict[Any, Dict[str, float]] = {}
        counter = {"n": 0}
        stats = {
            "requests_total": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "executed_online_comsol": 0,
            "fallback_proxy_budget_exhausted": 0,
            "fallback_proxy_geometry_infeasible": 0,
            "fallback_proxy_exceptions": 0,
            "eval_budget": int(budget_control["eval_budget"]),
            "budget_exhausted": False,
            "geometry_gate_enabled": bool(geometry_gate_enabled),
            "cache_quantize_mm": float(cache_quantize_mm),
            "stats_log_interval": int(progress_log_interval),
            "stats_progress_logs": 0,
            "schedule_mode": str(schedule_control["mode"]),
            "schedule_top_fraction": float(schedule_control["top_fraction"]),
            "schedule_min_observations": int(schedule_control["min_observations"]),
            "schedule_warmup_calls": int(schedule_control["warmup_calls"]),
            "schedule_explore_prob": float(schedule_control["explore_prob"]),
            "schedule_uncertainty_weight": float(schedule_control["uncertainty_weight"]),
            "schedule_uncertainty_scale_mm": float(schedule_control["uncertainty_scale_mm"]),
            "scheduler_candidates_seen": 0,
            "scheduler_selected_warmup": 0,
            "scheduler_selected_rank": 0,
            "scheduler_selected_explore": 0,
            "fallback_proxy_scheduler_skipped": 0,
        }
        budget_text = "unlimited" if int(budget_control["eval_budget"]) <= 0 else str(int(budget_control["eval_budget"]))
        self.logger.logger.info(
            "MaaS thermal evaluator mode=online_comsol "
            "(cached, eval_budget=%s, geometry_gate=%s, cache_quantize_mm=%.3f, "
            "schedule=%s, stats_log_interval=%s)",
            budget_text,
            "on" if geometry_gate_enabled else "off",
            cache_quantize_mm,
            str(schedule_control["mode"]),
            str(progress_log_interval) if progress_log_interval > 0 else "off",
        )

        def _proxy_thermal_payload(
            design_state: DesignState,
            *,
            min_clearance_hint: Optional[float] = None,
            num_collisions_hint: Optional[int] = None,
            source: str,
            acq_score: Optional[float] = None,
            uncertainty: Optional[float] = None,
        ) -> Dict[str, Any]:
            min_clearance_value = min_clearance_hint
            if min_clearance_value is None:
                min_clearance_value, _ = self._calculate_pairwise_clearance(design_state)
            num_collisions_value = int(num_collisions_hint or 0)
            if callable(estimate_proxy_thermal_metrics):
                thermal_proxy = estimate_proxy_thermal_metrics(
                    design_state,
                    min_clearance_mm=float(min_clearance_value),
                    num_collisions=int(num_collisions_value),
                )
                max_temp = float(thermal_proxy["max_temp"])
                min_temp = float(thermal_proxy["min_temp"])
                avg_temp = float(thermal_proxy["avg_temp"])
            else:
                total_power = float(sum(float(comp.power) for comp in design_state.components))
                clearance_term = 30.0 / max(float(min_clearance_value) + 10.0, 1.0)
                max_temp = 18.0 + 0.09 * total_power + clearance_term
                min_temp = float(max_temp - 8.0)
                avg_temp = float(max_temp - 3.0)
            payload: Dict[str, Any] = {
                "max_temp": float(max_temp),
                "min_temp": float(min_temp),
                "avg_temp": float(avg_temp),
                "_source": str(source),
                "_proxy_min_clearance": float(min_clearance_value),
            }
            if callable(estimate_proxy_thermal_metrics):
                payload["_proxy_num_collisions"] = int(num_collisions_value)
                payload["_proxy_hotspot_compaction"] = float(thermal_proxy.get("hotspot_compaction", 0.0))
                payload["_proxy_wall_cooling_score"] = float(thermal_proxy.get("wall_cooling_score", 0.0))
                payload["_proxy_power_spread_score"] = float(thermal_proxy.get("power_spread_score", 0.0))
            if acq_score is not None:
                payload["_schedule_acq_score"] = float(acq_score)
            if uncertainty is not None:
                payload["_schedule_uncertainty"] = float(uncertainty)
            return payload

        def _position_vector(design_state: DesignState) -> np.ndarray:
            vec: List[float] = []
            for comp in design_state.components:
                vec.extend([float(comp.position.x), float(comp.position.y), float(comp.position.z)])
            return np.asarray(vec, dtype=float)

        def _thermal_evaluator(state: DesignState) -> Dict[str, float]:
            stats["requests_total"] += 1

            def _maybe_log_progress() -> None:
                if (
                    progress_log_interval > 0 and
                    int(stats["requests_total"]) % progress_log_interval == 0
                ):
                    stats["stats_progress_logs"] += 1
                    cache_hits = int(stats["cache_hits"])
                    requests_total = int(stats["requests_total"])
                    hit_rate = (cache_hits / requests_total) if requests_total > 0 else 0.0
                    self.logger.logger.info(
                        "online_comsol evaluator progress: requests=%d, executed_online=%d, "
                        "cache_hits=%d, hit_rate=%.3f, geom_fallback=%d, budget_fallback=%d",
                        requests_total,
                        int(stats["executed_online_comsol"]),
                        cache_hits,
                        float(hit_rate),
                        int(stats["fallback_proxy_geometry_infeasible"]),
                        int(stats["fallback_proxy_budget_exhausted"]),
                    )

            fp = self._state_fingerprint_with_options(
                state,
                position_quantization_mm=cache_quantize_mm,
            )
            cached_payload = cache.get(fp)
            if cached_payload is not None:
                stats["cache_hits"] += 1
                _maybe_log_progress()
                return dict(cached_payload)

            stats["cache_misses"] += 1
            min_clearance_hint: Optional[float] = None
            num_collisions_hint: Optional[int] = None
            if geometry_gate_enabled:
                feasible, min_clearance, num_collisions = self._is_geometry_feasible(state)
                min_clearance_hint = float(min_clearance)
                num_collisions_hint = int(num_collisions)
                if not feasible:
                    stats["fallback_proxy_geometry_infeasible"] += 1
                    cache[fp] = {}
                    self.logger.logger.debug(
                        "online_comsol thermal evaluator skip (geometry infeasible): "
                        "min_clearance=%.3fmm, collisions=%d",
                        float(min_clearance),
                        int(num_collisions),
                    )
                    _maybe_log_progress()
                    return {}

            should_run_online = True
            proxy_payload: Optional[Dict[str, float]] = None
            schedule_mode = str(schedule_control["mode"])
            schedule_top_fraction = float(schedule_control["top_fraction"])
            schedule_min_observations = int(schedule_control["min_observations"])
            schedule_warmup_calls = int(schedule_control["warmup_calls"])
            schedule_explore_prob = float(schedule_control["explore_prob"])
            schedule_uncertainty_weight = float(schedule_control["uncertainty_weight"])
            schedule_uncertainty_scale_mm = max(
                float(schedule_control["uncertainty_scale_mm"]),
                1e-6,
            )

            if schedule_mode == "ucb_topk":
                position_vec = _position_vector(state)
                scores = list(scheduler_state.get("scores", []))
                vectors = list(scheduler_state.get("vectors", []))
                if vectors:
                    dist = min(float(np.linalg.norm(position_vec - prev)) for prev in vectors)
                else:
                    dist = float(schedule_uncertainty_scale_mm)
                uncertainty = float(np.clip(dist / schedule_uncertainty_scale_mm, 0.0, 1.0))
                proxy_payload = _proxy_thermal_payload(
                    state,
                    min_clearance_hint=min_clearance_hint,
                    num_collisions_hint=num_collisions_hint,
                    source="proxy_scheduler",
                    uncertainty=uncertainty,
                )
                thermal_limit = max(float(self.runtime_constraints.get("max_temp_c", 60.0)), 1.0)
                proxy_margin = (thermal_limit - float(proxy_payload["max_temp"])) / thermal_limit
                acq_score = float(proxy_margin + schedule_uncertainty_weight * uncertainty)
                proxy_payload["_schedule_acq_score"] = float(acq_score)

                scores.append(float(acq_score))
                vectors.append(position_vec)
                scheduler_state["scores"] = scores
                scheduler_state["vectors"] = vectors
                stats["scheduler_candidates_seen"] = int(len(scores))

                rank = int(1 + sum(1 for item in scores if float(item) > float(acq_score)))
                k_target = max(1, int(np.ceil(schedule_top_fraction * float(len(scores)))))
                if int(stats["executed_online_comsol"]) < schedule_warmup_calls:
                    should_run_online = True
                    stats["scheduler_selected_warmup"] += 1
                else:
                    rank_enabled = len(scores) >= schedule_min_observations
                    selected_by_rank = bool(rank_enabled and rank <= k_target)
                    selected_by_explore = bool(
                        (not selected_by_rank) and
                        (float(schedule_explore_prob) > 0.0) and
                        (float(scheduler_rng.random()) < float(schedule_explore_prob))
                    )
                    if selected_by_rank:
                        should_run_online = True
                        stats["scheduler_selected_rank"] += 1
                    elif selected_by_explore:
                        should_run_online = True
                        stats["scheduler_selected_explore"] += 1
                    else:
                        should_run_online = False

            if schedule_mode == "ucb_topk" and not should_run_online:
                stats["fallback_proxy_scheduler_skipped"] += 1
                payload = dict(proxy_payload or _proxy_thermal_payload(state, source="proxy_scheduler"))
                cache[fp] = payload
                _maybe_log_progress()
                return payload

            current_budget = int(budget_control.get("eval_budget", 0))
            current_budget = max(current_budget, 0)
            stats["eval_budget"] = int(current_budget)
            if current_budget > 0 and int(stats["executed_online_comsol"]) >= current_budget:
                stats["fallback_proxy_budget_exhausted"] += 1
                if not bool(stats["budget_exhausted"]):
                    stats["budget_exhausted"] = True
                    self.logger.logger.warning(
                        "online_comsol thermal evaluator budget exhausted (%s). "
                        "Remaining candidates fallback to proxy thermal model.",
                        current_budget,
                    )
                if schedule_mode == "ucb_topk":
                    payload = dict(
                        proxy_payload or _proxy_thermal_payload(
                            state,
                            min_clearance_hint=min_clearance_hint,
                            num_collisions_hint=num_collisions_hint,
                            source="proxy_budget_exhausted",
                        )
                    )
                    cache[fp] = payload
                    _maybe_log_progress()
                    return payload
                _maybe_log_progress()
                return {}

            counter["n"] += 1
            stats["executed_online_comsol"] += 1
            eval_iteration = int(base_iteration * 100000 + counter["n"])
            try:
                metrics, _ = self._evaluate_design(state, eval_iteration)
                thermal = metrics["thermal"]

                def _metric_field(obj: Any, key: str, default: float = 0.0) -> float:
                    if obj is None:
                        return float(default)
                    if isinstance(obj, dict):
                        raw = obj.get(key, default)
                    else:
                        raw = getattr(obj, key, default)
                    try:
                        return float(raw)
                    except Exception:
                        return float(default)

                structural = metrics.get("structural")
                power = metrics.get("power")
                diagnostics = dict(metrics.get("diagnostics", {}) or {})
                payload = {
                    "max_temp": float(thermal.max_temp),
                    "min_temp": float(thermal.min_temp),
                    "avg_temp": float(thermal.avg_temp),
                    "_source": "online_comsol",
                }
                if structural is not None:
                    payload["max_stress"] = _metric_field(structural, "max_stress")
                    payload["max_displacement"] = _metric_field(structural, "max_displacement")
                    payload["first_modal_freq"] = _metric_field(structural, "first_modal_freq")
                    payload["safety_factor"] = _metric_field(structural, "safety_factor")
                if power is not None:
                    payload["total_power"] = _metric_field(power, "total_power")
                    payload["peak_power"] = _metric_field(power, "peak_power")
                    payload["power_margin"] = _metric_field(power, "power_margin")
                    payload["voltage_drop"] = _metric_field(power, "voltage_drop")

                structural_source = diagnostics.get("structural_source", None)
                power_source = diagnostics.get("power_source", None)
                if structural_source:
                    payload["_structural_source"] = str(structural_source)
                if power_source:
                    payload["_power_source"] = str(power_source)

                cache[fp] = payload
                _maybe_log_progress()
                return payload
            except Exception as exc:
                stats["fallback_proxy_exceptions"] += 1
                self.logger.logger.warning(
                    "online_comsol thermal evaluator failed for candidate; "
                    f"fallback to proxy thermal model: {exc}"
                )
                if schedule_mode == "ucb_topk":
                    payload = dict(
                        proxy_payload or _proxy_thermal_payload(
                            state,
                            min_clearance_hint=min_clearance_hint,
                            num_collisions_hint=num_collisions_hint,
                            source="proxy_exception",
                        )
                    )
                    cache[fp] = payload
                    _maybe_log_progress()
                    return payload
                _maybe_log_progress()
                return {}

        def _set_scheduler_params(
            *,
            mode: Optional[str] = None,
            top_fraction: Optional[float] = None,
            min_observations: Optional[int] = None,
            warmup_calls: Optional[int] = None,
            explore_prob: Optional[float] = None,
            uncertainty_weight: Optional[float] = None,
            uncertainty_scale_mm: Optional[float] = None,
        ) -> Dict[str, Any]:
            updates: Dict[str, Any] = {}
            if mode is not None:
                updates["mode"] = mode
            if top_fraction is not None:
                updates["top_fraction"] = top_fraction
            if min_observations is not None:
                updates["min_observations"] = min_observations
            if warmup_calls is not None:
                updates["warmup_calls"] = warmup_calls
            if explore_prob is not None:
                updates["explore_prob"] = explore_prob
            if uncertainty_weight is not None:
                updates["uncertainty_weight"] = uncertainty_weight
            if uncertainty_scale_mm is not None:
                updates["uncertainty_scale_mm"] = uncertainty_scale_mm

            if not updates:
                return {}

            previous = dict(schedule_control)
            normalized = _sanitize_scheduler_params({**schedule_control, **updates})
            changes: Dict[str, Any] = {}
            for key, old_value in previous.items():
                new_value = normalized[key]
                if old_value != new_value:
                    changes[key] = (old_value, new_value)

            if not changes:
                return {}

            schedule_control.update(normalized)
            stats["schedule_mode"] = str(schedule_control["mode"])
            stats["schedule_top_fraction"] = float(schedule_control["top_fraction"])
            stats["schedule_min_observations"] = int(schedule_control["min_observations"])
            stats["schedule_warmup_calls"] = int(schedule_control["warmup_calls"])
            stats["schedule_explore_prob"] = float(schedule_control["explore_prob"])
            stats["schedule_uncertainty_weight"] = float(schedule_control["uncertainty_weight"])
            stats["schedule_uncertainty_scale_mm"] = float(schedule_control["uncertainty_scale_mm"])

            if "mode" in changes:
                scheduler_state["scores"] = []
                scheduler_state["vectors"] = []
                stats["scheduler_candidates_seen"] = 0

            self.logger.logger.info(
                "online_comsol evaluator scheduler updated: %s",
                changes,
            )
            return changes

        def _get_scheduler_params() -> Dict[str, Any]:
            return {
                "mode": str(schedule_control["mode"]),
                "top_fraction": float(schedule_control["top_fraction"]),
                "min_observations": int(schedule_control["min_observations"]),
                "warmup_calls": int(schedule_control["warmup_calls"]),
                "explore_prob": float(schedule_control["explore_prob"]),
                "uncertainty_weight": float(schedule_control["uncertainty_weight"]),
                "uncertainty_scale_mm": float(schedule_control["uncertainty_scale_mm"]),
            }

        def _set_eval_budget(new_budget: int) -> None:
            parsed = max(int(new_budget), 0)
            old_budget = int(budget_control.get("eval_budget", 0))
            if parsed == old_budget:
                return
            budget_control["eval_budget"] = int(parsed)
            stats["eval_budget"] = int(parsed)
            if parsed <= 0 or int(stats.get("executed_online_comsol", 0)) < parsed:
                stats["budget_exhausted"] = False
            self.logger.logger.info(
                "online_comsol evaluator budget updated: %s -> %s",
                old_budget,
                parsed,
            )

        def _get_eval_budget() -> int:
            return int(budget_control.get("eval_budget", 0))

        _thermal_evaluator.stats = stats  # type: ignore[attr-defined]
        _thermal_evaluator.set_eval_budget = _set_eval_budget  # type: ignore[attr-defined]
        _thermal_evaluator.get_eval_budget = _get_eval_budget  # type: ignore[attr-defined]
        _thermal_evaluator.set_scheduler_params = _set_scheduler_params  # type: ignore[attr-defined]
        _thermal_evaluator.get_scheduler_params = _get_scheduler_params  # type: ignore[attr-defined]
        return _thermal_evaluator

    def _run_maas_topk_physics_audit(
        self,
        execution_result: Any,
        problem_generator: Any,
        base_state: DesignState,
        top_k: int,
        base_iteration: int,
    ) -> tuple[Dict[str, Any], Optional[DesignState]]:
        """
        对最后一轮 Pareto 前沿做 Top-K 物理审计并返回候选状态。
        """
        report: Dict[str, Any] = {
            "enabled": True,
            "requested_top_k": int(top_k),
            "records": [],
            "selected_rank": None,
            "selected_pareto_index": None,
            "selected_reason": "",
        }

        if execution_result is None or problem_generator is None:
            report["enabled"] = False
            report["selected_reason"] = "missing_solver_result_or_problem_generator"
            return report, None

        pareto_x_raw = getattr(execution_result, "pareto_X", None)
        if pareto_x_raw is None:
            report["selected_reason"] = "pareto_X_missing"
            return report, None

        pareto_x = np.asarray(pareto_x_raw, dtype=float)
        if pareto_x.ndim == 1:
            pareto_x = pareto_x.reshape(1, -1)
        if pareto_x.ndim != 2 or pareto_x.shape[0] == 0:
            report["selected_reason"] = "pareto_X_empty"
            return report, None

        pareto_f_raw = getattr(execution_result, "pareto_F", None)
        if pareto_f_raw is not None:
            pareto_f = np.asarray(pareto_f_raw, dtype=float)
            if pareto_f.ndim == 1:
                pareto_f = pareto_f.reshape(1, -1)
            if pareto_f.ndim != 2 or pareto_f.shape[0] != pareto_x.shape[0]:
                pareto_f = np.zeros((pareto_x.shape[0], 1), dtype=float)
        else:
            pareto_f = np.zeros((pareto_x.shape[0], 1), dtype=float)

        selected_indices = select_top_pareto_indices(pareto_f, top_k=top_k)
        if not selected_indices:
            report["selected_reason"] = "no_indices_selected"
            return report, None

        pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
        pareto_cv = None
        if pareto_cv_raw is not None:
            parsed_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
            if parsed_cv.size == pareto_x.shape[0]:
                pareto_cv = parsed_cv

        feasible_selected = 0
        if pareto_cv is not None:
            feasible_bucket: List[int] = []
            infeasible_bucket: List[int] = []
            for idx in selected_indices:
                cv_val = float(pareto_cv[int(idx)])
                if np.isfinite(cv_val) and cv_val <= 1e-9:
                    feasible_bucket.append(int(idx))
                else:
                    infeasible_bucket.append(int(idx))
            infeasible_bucket = sorted(
                infeasible_bucket,
                key=lambda i: float(pareto_cv[i]) if np.isfinite(pareto_cv[i]) else float("inf"),
            )
            selected_indices = feasible_bucket + infeasible_bucket
            feasible_selected = len(feasible_bucket)

        report["selected_indices"] = [int(idx) for idx in selected_indices]
        report["feasible_candidate_count"] = int(feasible_selected)

        best_feasible_state: Optional[DesignState] = None
        best_any_state: Optional[DesignState] = None
        best_feasible_penalty = float("inf")
        best_any_penalty = float("inf")
        best_feasible_meta: tuple[int, int] | None = None
        best_any_meta: tuple[int, int] | None = None

        for rank, pareto_idx in enumerate(selected_indices, start=1):
            record: Dict[str, Any] = {
                "rank": int(rank),
                "pareto_index": int(pareto_idx),
                "success": False,
            }
            if pareto_cv is not None:
                record["solver_cv"] = float(pareto_cv[int(pareto_idx)])
            try:
                candidate = problem_generator.codec.decode(pareto_x[int(pareto_idx)])
                eval_iteration = int(base_iteration * 100 + rank)
                metrics, violations = self._evaluate_design(candidate, eval_iteration)
                penalty = float(self._calculate_penalty_score(metrics, violations))
                record.update({
                    "success": True,
                    "max_temp": float(metrics["thermal"].max_temp),
                    "min_clearance": float(metrics["geometry"].min_clearance),
                    "cg_offset": float(metrics["geometry"].cg_offset_magnitude),
                    "num_collisions": int(metrics["geometry"].num_collisions),
                    "num_violations": int(len(violations)),
                    "penalty_score": penalty,
                })

                if penalty < best_any_penalty:
                    best_any_penalty = penalty
                    best_any_state = candidate.model_copy(deep=True)
                    best_any_meta = (rank, int(pareto_idx))

                if len(violations) == 0 and penalty < best_feasible_penalty:
                    best_feasible_penalty = penalty
                    best_feasible_state = candidate.model_copy(deep=True)
                    best_feasible_meta = (rank, int(pareto_idx))
            except Exception as exc:
                record["error"] = str(exc)
            report["records"].append(record)

        selected_state: Optional[DesignState] = None
        opt_cfg = self.config.get("optimization", {})
        allow_infeasible_fallback = bool(
            opt_cfg.get("mass_audit_allow_infeasible_fallback", False)
        )
        if best_feasible_state is not None and best_feasible_meta is not None:
            selected_state = best_feasible_state
            report["selected_rank"] = int(best_feasible_meta[0])
            report["selected_pareto_index"] = int(best_feasible_meta[1])
            report["selected_reason"] = "best_feasible_penalty"
        elif allow_infeasible_fallback and best_any_state is not None and best_any_meta is not None:
            selected_state = best_any_state
            report["selected_rank"] = int(best_any_meta[0])
            report["selected_pareto_index"] = int(best_any_meta[1])
            report["selected_reason"] = "best_any_penalty"
        elif best_any_state is not None:
            report["selected_reason"] = "no_feasible_after_audit"
            report["best_infeasible_penalty"] = float(best_any_penalty)
            return report, None
        else:
            report["selected_reason"] = "all_audit_failed"
            return report, None

        selected_state.parent_id = base_state.state_id
        selected_state.state_id = "state_iter_02_maas_candidate_audited"
        selected_state.iteration = base_iteration + 1
        return report, selected_state

    def _apply_uniform_relaxation_to_intent(
        self,
        intent: ModelingIntent,
        relax_ratio: float,
        tag: str,
    ) -> tuple[ModelingIntent, int]:
        """
        对全部 hard constraints 做统一幅度松弛。
        """
        ratio = max(0.0, float(relax_ratio))
        if ratio <= 0.0:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        applied = 0
        for cons in next_intent.hard_constraints:
            base = float(cons.target_value)
            delta = max(abs(base) * ratio, 0.2)
            if cons.relation == "<=":
                cons.target_value = float(base + delta)
                applied += 1
            elif cons.relation == ">=":
                cons.target_value = float(base - delta)
                applied += 1

        if applied > 0:
            next_intent.assumptions.append(f"{tag}: uniform_relax_count={applied}, ratio={ratio:.4f}")
        return next_intent, applied

    def _apply_objective_focus_to_intent(
        self,
        intent: ModelingIntent,
        focus_keywords: List[str],
        tag: str,
    ) -> tuple[ModelingIntent, int]:
        """
        将目标权重重分配到指定关注方向（温度/质心等）。
        """
        if not focus_keywords:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        hits = 0
        for obj in next_intent.objectives:
            metric = str(obj.metric_key or "").lower()
            focused = any(k in metric for k in focus_keywords)
            if focused:
                obj.weight = float(max(obj.weight * 1.8, 1e-6))
                hits += 1
            else:
                obj.weight = float(max(obj.weight * 0.85, 1e-6))

        if hits > 0:
            next_intent.assumptions.append(
                f"{tag}: objective_focus={','.join(focus_keywords)}, hits={hits}"
            )
        return next_intent, hits

    def _propose_maas_mcts_variants(
        self,
        node: MCTSNode,
        relax_ratio: float,
    ) -> List[MCTSVariant]:
        """
        为 MCTS 节点生成建模分支候选。
        """
        variants: List[MCTSVariant] = []
        opt_cfg = getattr(self, "config", {}).get("optimization", {})
        enable_operator_program = bool(
            opt_cfg.get("mass_enable_operator_program", True)
        )
        evaluation_payload = (
            dict(node.evaluation.payload)
            if node.evaluation is not None and isinstance(node.evaluation.payload, dict)
            else {}
        )
        dominant_violation = str(
            evaluation_payload.get("dominant_violation", "")
        ).strip().lower()
        prefer_operator_first = any(
            token in dominant_violation
            for token in ("mission", "keepout", "fov", "clearance", "collision", "boundary")
        )

        # 1) Identity branch
        identity_intent = node.intent.model_copy(deep=True)
        identity_intent.assumptions.append(f"mcts_action=identity_d{node.depth+1}")
        identity_variant = MCTSVariant(
            action=f"identity_d{node.depth+1}",
            intent=identity_intent,
            metadata={"source": "identity"},
        )

        # 2) Operator program branch (R1 baseline)
        operator_variant: Optional[MCTSVariant] = None
        if enable_operator_program:
            operator_program = build_operator_program_from_context(
                intent=node.intent,
                depth=node.depth + 1,
                evaluation_payload=(node.evaluation.payload if node.evaluation is not None else None),
                max_components=int(opt_cfg.get("mass_operator_program_max_components", 6)),
            )
            operator_intent, operator_report = apply_operator_program_to_intent(
                intent=node.intent,
                program=operator_program,
            )
            if (
                bool(operator_report.get("is_valid", False)) and
                int(operator_report.get("applied_actions", 0)) > 0
            ):
                operator_intent.assumptions.append(
                    "mcts_action=operator_program_d"
                    f"{node.depth+1}, program_id={operator_program.program_id}, "
                    f"applied={int(operator_report.get('applied_actions', 0))}"
                )
                operator_variant = MCTSVariant(
                    action=f"operator_program_d{node.depth+1}",
                    intent=operator_intent,
                    metadata={
                        "source": "operator_program",
                        "program_id": operator_program.program_id,
                        "action_sequence": [
                            str(action.action) for action in list(operator_program.actions or [])
                        ],
                        "operator_program": dict(operator_report.get("program", {}) or {}),
                        "applied": int(operator_report.get("applied_actions", 0)),
                        "warnings": list(operator_report.get("warnings", []) or []),
                        "dominant_violation": str(
                            operator_program.metadata.get("dominant_violation", "")
                        ),
                    },
                )

        if prefer_operator_first:
            if operator_variant is not None:
                variants.append(operator_variant)
            variants.append(identity_variant)
        else:
            variants.append(identity_variant)
            if operator_variant is not None:
                variants.append(operator_variant)

        # 3) Reflection-based relaxation from parent evaluation, if available
        if node.evaluation is not None:
            suggestions = list(node.evaluation.payload.get("relaxation_suggestions", []) or [])
            reflection_intent, reflection_applied = self._apply_relaxation_suggestions_to_intent(
                intent=node.intent,
                suggestions=suggestions,
                attempt=node.depth + 1,
            )
            if reflection_applied > 0:
                reflection_intent.assumptions.append(
                    f"mcts_action=reflection_relax_d{node.depth+1}, applied={reflection_applied}"
                )
                variants.append(MCTSVariant(
                    action=f"reflection_relax_d{node.depth+1}",
                    intent=reflection_intent,
                    metadata={"source": "reflection", "applied": reflection_applied},
                ))

        # 4) Uniform relaxation
        uniform_intent, uniform_applied = self._apply_uniform_relaxation_to_intent(
            intent=node.intent,
            relax_ratio=relax_ratio * 0.5,
            tag=f"mcts_uniform_relax_d{node.depth+1}",
        )
        if uniform_applied > 0:
            variants.append(MCTSVariant(
                action=f"uniform_relax_d{node.depth+1}",
                intent=uniform_intent,
                metadata={"source": "uniform_relax", "applied": uniform_applied},
            ))

        # 5) CG-focused objective reweight
        cg_focus_intent, cg_hits = self._apply_objective_focus_to_intent(
            intent=node.intent,
            focus_keywords=["cg", "centroid", "com_offset"],
            tag=f"mcts_cg_focus_d{node.depth+1}",
        )
        if cg_hits > 0:
            variants.append(MCTSVariant(
                action=f"cg_focus_d{node.depth+1}",
                intent=cg_focus_intent,
                metadata={"source": "objective_focus", "hits": cg_hits},
            ))

        # 6) Thermal-focused objective reweight
        thermal_focus_intent, thermal_hits = self._apply_objective_focus_to_intent(
            intent=node.intent,
            focus_keywords=["temp", "thermal", "hotspot"],
            tag=f"mcts_thermal_focus_d{node.depth+1}",
        )
        if thermal_hits > 0:
            variants.append(MCTSVariant(
                action=f"thermal_focus_d{node.depth+1}",
                intent=thermal_focus_intent,
                metadata={"source": "objective_focus", "hits": thermal_hits},
            ))

        if len(variants) <= 4:
            return variants

        # Keep branch diversity while ensuring CG branch is not truncated out.
        prioritized_prefixes = (
            (
                "operator_program_d",
                "identity_d",
            )
            if prefer_operator_first
            else (
                "identity_d",
                "operator_program_d",
            )
        ) + (
            "uniform_relax_d",
            "cg_focus_d",
            "reflection_relax_d",
            "thermal_focus_d",
        )
        selected: List[MCTSVariant] = []
        selected_actions: set[str] = set()

        for prefix in prioritized_prefixes:
            for variant in variants:
                if variant.action in selected_actions:
                    continue
                if variant.action.startswith(prefix):
                    selected.append(variant)
                    selected_actions.add(variant.action)
                    break
            if len(selected) >= 4:
                return selected[:4]

        for variant in variants:
            if variant.action in selected_actions:
                continue
            selected.append(variant)
            selected_actions.add(variant.action)
            if len(selected) >= 4:
                break

        return selected[:4]

    @staticmethod
    def _normalize_operator_action_name(action: Any) -> str:
        normalized = str(action or "").strip().lower()
        return normalized

    def _operator_action_family(self, action: Any) -> str:
        return action_family(self._normalize_operator_action_name(action))

    def _operator_credit_table(self) -> Dict[str, Dict[str, Any]]:
        table = getattr(self, "_maas_operator_credit_stats", None)
        if not isinstance(table, dict):
            table = {}
            self._maas_operator_credit_stats = table
        return table

    def _extract_operator_action_sequence(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
        attempt_payload: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        metadata = dict(branch_metadata or {})
        payload = dict(attempt_payload or {})
        source = str(metadata.get("source", payload.get("branch_source", "")) or "").strip().lower()
        action_name = str(branch_action or payload.get("branch_action", "")).strip().lower()
        if source != "operator_program" and not action_name.startswith("operator_program_d"):
            return []

        candidates = list(metadata.get("action_sequence", []) or [])
        if not candidates:
            candidates = list(payload.get("operator_actions", []) or [])

        operator_program = dict(metadata.get("operator_program", {}) or {})
        if not candidates:
            for action in list(operator_program.get("actions", []) or []):
                if isinstance(action, dict):
                    name = self._normalize_operator_action_name(action.get("action", ""))
                else:
                    name = self._normalize_operator_action_name(action)
                if name:
                    candidates.append(name)

        unique_actions: List[str] = []
        seen_actions: set[str] = set()
        for item in candidates:
            name = self._normalize_operator_action_name(item)
            if not name or name in seen_actions:
                continue
            seen_actions.add(name)
            unique_actions.append(name)
        return unique_actions

    def _operator_family_requirements(self) -> tuple[str, ...]:
        opt_cfg = self.config.get("optimization", {})
        return parse_required_families(
            opt_cfg.get(
                "mass_operator_family_required",
                "geometry,thermal,structural,power,mission",
            )
        )

    def _build_operator_realization_context(
        self,
        *,
        thermal_evaluator_mode: str,
    ) -> Dict[str, bool]:
        opt_cfg = dict(self.config.get("optimization", {}) or {})
        sim_cfg = dict(self.config.get("simulation", {}) or {})
        mission_evaluator = None
        resolver = getattr(self, "_resolve_mission_fov_evaluator", None)
        if callable(resolver):
            try:
                mission_evaluator = resolver()
            except Exception:
                mission_evaluator = None
        return {
            "thermal_real": str(thermal_evaluator_mode or "").strip().lower() == "online_comsol",
            "structural_real": bool(sim_cfg.get("enable_structural_real", False)),
            "power_real": bool(sim_cfg.get("enable_power_network_real", False)),
            "mission_real": bool(callable(mission_evaluator)),
            "real_only": bool(opt_cfg.get("mass_physics_real_only", False)),
        }

    def _collect_thermal_contact_map(
        self,
        state: Optional[DesignState],
    ) -> Dict[tuple[str, str], float]:
        mapping: Dict[tuple[str, str], float] = {}
        if state is None:
            return mapping
        for comp in list(getattr(state, "components", []) or []):
            source_id = str(getattr(comp, "id", "") or "").strip()
            if not source_id:
                continue
            contacts = dict(getattr(comp, "thermal_contacts", {}) or {})
            for target_id_raw, value in contacts.items():
                target_id = str(target_id_raw or "").strip()
                if not target_id:
                    continue
                try:
                    conductance = float(value)
                except Exception:
                    continue
                mapping[(source_id, target_id)] = float(conductance)
        return mapping

    def _collect_component_positions(
        self,
        state: Optional[DesignState],
    ) -> Dict[str, tuple[float, float, float]]:
        positions: Dict[str, tuple[float, float, float]] = {}
        if state is None:
            return positions
        for comp in list(getattr(state, "components", []) or []):
            comp_id = str(getattr(comp, "id", "") or "").strip()
            if not comp_id:
                continue
            pos = getattr(comp, "position", None)
            if pos is None:
                continue
            try:
                positions[comp_id] = (
                    float(getattr(pos, "x", 0.0)),
                    float(getattr(pos, "y", 0.0)),
                    float(getattr(pos, "z", 0.0)),
                )
            except Exception:
                continue
        return positions

    def _build_operator_thermal_realization_evidence(
        self,
        *,
        action_sequence: List[str],
        base_state: Optional[DesignState],
        decoded_state: Optional[DesignState],
        thermal_evaluator_mode: str,
        runtime_thermal_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        heat_actions = {"hot_spread", "add_heatstrap", "set_thermal_contact"}
        ordered_actions: List[str] = []
        seen: set[str] = set()
        for action in list(action_sequence or []):
            name = str(action or "").strip().lower()
            if name in heat_actions and name not in seen:
                seen.add(name)
                ordered_actions.append(name)

        report: Dict[str, Any] = {
            "heat_actions_present": list(ordered_actions),
            "candidate_available": bool(decoded_state is not None),
            "thermal_real_path_active": (
                str(thermal_evaluator_mode or "").strip().lower() == "online_comsol"
            ),
            "online_comsol_calls": 0,
            "moved_component_count": 0,
            "base_contact_edge_count": 0,
            "candidate_contact_edge_count": 0,
            "new_contact_edge_count": 0,
            "updated_contact_edge_count": 0,
            "action_evidence": {},
            "all_heat_actions_realized": True,
        }
        if not ordered_actions:
            return report

        runtime_snapshot = dict(runtime_thermal_snapshot or {})
        executed_online_comsol = 0
        requests_total = 0
        try:
            executed_online_comsol = int(
                runtime_snapshot.get("executed_online_comsol", 0) or 0
            )
        except Exception:
            executed_online_comsol = 0
        try:
            requests_total = int(
                runtime_snapshot.get("requests_total", 0) or 0
            )
        except Exception:
            requests_total = 0
        report["online_comsol_calls"] = int(max(executed_online_comsol, requests_total))

        base_contacts = self._collect_thermal_contact_map(base_state)
        cand_contacts = self._collect_thermal_contact_map(decoded_state)
        report["base_contact_edge_count"] = int(len(base_contacts))
        report["candidate_contact_edge_count"] = int(len(cand_contacts))

        new_edges = [
            edge for edge in cand_contacts.keys()
            if edge not in base_contacts
        ]
        updated_edges = [
            edge
            for edge in cand_contacts.keys()
            if edge in base_contacts and abs(float(cand_contacts[edge]) - float(base_contacts[edge])) > 1e-9
        ]
        report["new_contact_edge_count"] = int(len(new_edges))
        report["updated_contact_edge_count"] = int(len(updated_edges))

        base_positions = self._collect_component_positions(base_state)
        cand_positions = self._collect_component_positions(decoded_state)
        moved = 0
        for comp_id, base_xyz in base_positions.items():
            cand_xyz = cand_positions.get(comp_id)
            if cand_xyz is None:
                continue
            delta = np.linalg.norm(
                np.asarray(cand_xyz, dtype=float) - np.asarray(base_xyz, dtype=float)
            )
            if np.isfinite(delta) and float(delta) > 1e-3:
                moved += 1
        report["moved_component_count"] = int(moved)

        thermal_real_active = bool(report["thermal_real_path_active"])
        if runtime_snapshot and (
            ("executed_online_comsol" in runtime_snapshot) or
            ("requests_total" in runtime_snapshot)
        ):
            thermal_real_active = bool(
                thermal_real_active and int(report["online_comsol_calls"]) > 0
            )
        contact_changed = bool((len(new_edges) + len(updated_edges)) > 0)
        hot_spread_effect = bool(int(report["moved_component_count"]) > 0)

        action_evidence: Dict[str, Dict[str, Any]] = {}
        all_realized = True
        for action in ordered_actions:
            if action == "hot_spread":
                realized = bool(thermal_real_active and hot_spread_effect)
                evidence = {
                    "realized": bool(realized),
                    "thermal_real_active": bool(thermal_real_active),
                    "moved_component_count": int(report["moved_component_count"]),
                    "reason": (
                        ""
                        if realized
                        else (
                            "thermal_real_path_inactive"
                            if not thermal_real_active
                            else "no_position_change_detected"
                        )
                    ),
                }
            else:
                realized = bool(thermal_real_active and contact_changed)
                evidence = {
                    "realized": bool(realized),
                    "thermal_real_active": bool(thermal_real_active),
                    "new_contact_edge_count": int(report["new_contact_edge_count"]),
                    "updated_contact_edge_count": int(report["updated_contact_edge_count"]),
                    "reason": (
                        ""
                        if realized
                        else (
                            "thermal_real_path_inactive"
                            if not thermal_real_active
                            else "no_thermal_contact_change_detected"
                        )
                    ),
                }
            action_evidence[action] = evidence
            if not bool(realized):
                all_realized = False

        report["action_evidence"] = action_evidence
        report["all_heat_actions_realized"] = bool(all_realized)
        return report

    def _build_operator_action_reports(
        self,
        *,
        action_sequence: List[str],
        thermal_evaluator_mode: str,
    ) -> Dict[str, Any]:
        implementation = build_operator_implementation_report(action_sequence)
        family_gate = evaluate_operator_family_coverage(
            actions=action_sequence,
            required_families=self._operator_family_requirements(),
        )
        realization_context = self._build_operator_realization_context(
            thermal_evaluator_mode=thermal_evaluator_mode,
        )
        realization_gate = evaluate_operator_realization(
            actions=action_sequence,
            realization_context=realization_context,
            required_families=self._operator_family_requirements(),
        )
        return {
            "implementation": implementation,
            "family_gate": family_gate,
            "realization_gate": realization_gate,
            "realization_context": realization_context,
        }

    def _summarize_operator_credit(
        self,
        *,
        action_sequence: List[str],
    ) -> Dict[str, Any]:
        table = self._operator_credit_table()
        if not action_sequence:
            return {}

        selected_actions = [action for action in action_sequence if action in table]
        stats = [dict(table[action]) for action in selected_actions]
        if not stats:
            return {}

        observations = int(sum(int(item.get("count", 0) or 0) for item in stats))
        if observations <= 0:
            return {}

        family_breakdown: Dict[str, int] = {}
        weighted_score = 0.0
        weighted_feasible = 0.0
        weighted_cv = 0.0
        cv_weight = 0
        best_cv = float("inf")
        for action, item in zip(selected_actions, stats):
            count = int(item.get("count", 0) or 0)
            mean_score = float(item.get("mean_score", 0.0) or 0.0)
            feasible_rate = float(item.get("feasible_rate", 0.0) or 0.0)
            weighted_score += float(count) * mean_score
            weighted_feasible += float(count) * feasible_rate

            family = self._operator_action_family(action)
            if family:
                family_breakdown[family] = int(family_breakdown.get(family, 0)) + int(count)

            mean_cv = item.get("mean_best_cv", None)
            if mean_cv is not None:
                try:
                    parsed_mean_cv = float(mean_cv)
                    if np.isfinite(parsed_mean_cv):
                        weighted_cv += float(count) * parsed_mean_cv
                        cv_weight += count
                except Exception:
                    pass

            item_best_cv = item.get("best_cv", None)
            if item_best_cv is not None:
                try:
                    parsed_best_cv = float(item_best_cv)
                    if np.isfinite(parsed_best_cv):
                        best_cv = min(best_cv, parsed_best_cv)
                except Exception:
                    pass

        return {
            "observations": int(observations),
            "mean_score": float(weighted_score / max(float(observations), 1.0)),
            "feasible_rate": float(weighted_feasible / max(float(observations), 1.0)),
            "mean_best_cv": (
                float(weighted_cv / max(float(cv_weight), 1.0))
                if cv_weight > 0
                else None
            ),
            "best_cv": float(best_cv) if np.isfinite(best_cv) else None,
            "action_count": int(len(stats)),
            "family_breakdown": dict(family_breakdown),
        }

    def _update_operator_credit_from_attempt(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
        attempt_payload: Optional[Dict[str, Any]] = None,
        score: float = 0.0,
    ) -> None:
        payload = dict(attempt_payload or {})
        actions = self._extract_operator_action_sequence(
            branch_action=branch_action,
            branch_metadata=branch_metadata,
            attempt_payload=payload,
        )
        if not actions:
            return

        diagnosis = dict(payload.get("diagnosis", {}) or {})
        status = str(diagnosis.get("status", "")).strip().lower()
        is_feasible = status in {"feasible", "feasible_but_stalled"}
        best_cv_raw = payload.get("best_cv", None)
        best_cv: Optional[float] = None
        if best_cv_raw is not None:
            try:
                parsed = float(best_cv_raw)
                if np.isfinite(parsed):
                    best_cv = max(parsed, 0.0)
            except Exception:
                best_cv = None

        table = self._operator_credit_table()
        for action in actions:
            stat = table.get(action)
            if stat is None:
                stat = {
                    "action": action,
                    "count": 0,
                    "sum_score": 0.0,
                    "mean_score": 0.0,
                    "best_score": float("-inf"),
                    "feasible_count": 0,
                    "feasible_rate": 0.0,
                    "best_cv": None,
                    "cv_count": 0,
                    "sum_best_cv": 0.0,
                    "mean_best_cv": None,
                    "last_score": None,
                    "last_status": "",
                    "last_attempt": None,
                }
                table[action] = stat

            stat["count"] = int(stat.get("count", 0) or 0) + 1
            stat["sum_score"] = float(stat.get("sum_score", 0.0) or 0.0) + float(score)
            stat["mean_score"] = float(stat["sum_score"]) / float(stat["count"])
            stat["best_score"] = max(float(stat.get("best_score", float("-inf"))), float(score))
            if is_feasible:
                stat["feasible_count"] = int(stat.get("feasible_count", 0) or 0) + 1
            stat["feasible_rate"] = (
                float(stat["feasible_count"]) / max(float(stat["count"]), 1.0)
            )

            if best_cv is not None:
                prev_best_cv = stat.get("best_cv", None)
                if prev_best_cv is None:
                    stat["best_cv"] = float(best_cv)
                else:
                    try:
                        prev_numeric = float(prev_best_cv)
                        stat["best_cv"] = float(min(prev_numeric, float(best_cv)))
                    except Exception:
                        stat["best_cv"] = float(best_cv)
                stat["cv_count"] = int(stat.get("cv_count", 0) or 0) + 1
                stat["sum_best_cv"] = float(stat.get("sum_best_cv", 0.0) or 0.0) + float(best_cv)
                stat["mean_best_cv"] = float(stat["sum_best_cv"]) / float(stat["cv_count"])

            stat["last_score"] = float(score)
            stat["last_status"] = status
            try:
                stat["last_attempt"] = int(payload.get("attempt", None))
            except Exception:
                stat["last_attempt"] = None

    def _build_operator_bias_from_branch(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Compile branch/operator metadata into runner-level operator bias config.
        """
        metadata = dict(branch_metadata or {})
        source = str(metadata.get("source", "") or "").strip().lower()
        action_name = str(branch_action or "").strip().lower()
        if source != "operator_program" and not action_name.startswith("operator_program_d"):
            return {}
        opt_cfg = getattr(self, "config", {}).get("optimization", {})
        enable_credit_bias = bool(
            opt_cfg.get("mass_enable_operator_credit_bias", True)
        )

        unique_actions = self._extract_operator_action_sequence(
            branch_action=branch_action,
            branch_metadata=metadata,
            attempt_payload=None,
        )
        operator_program = dict(metadata.get("operator_program", {}) or {})

        bias: Dict[str, Any] = {
            "enabled": True,
            "strategy": "operator_program",
            "source": source or "operator_program",
            "branch_action": str(branch_action or ""),
            "program_id": str(
                metadata.get("program_id", "") or operator_program.get("program_id", "")
            ),
            "action_sequence": unique_actions,
            "sampling_sigma_ratio": 0.02,
            "sampling_jitter_count": 4,
            "crossover_prob": 0.90,
            "crossover_eta": 15.0,
            "mutation_prob": None,
            "mutation_eta": 20.0,
            "repair_push_ratio": None,
            "repair_cg_nudge_ratio": None,
            "repair_max_passes": None,
            "credit_bias_enabled": bool(enable_credit_bias),
        }

        if "hot_spread" in unique_actions:
            bias["sampling_sigma_ratio"] = max(float(bias["sampling_sigma_ratio"]), 0.04)
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 8)
            bias["mutation_prob"] = 0.28
            bias["mutation_eta"] = 14.0
            bias["crossover_eta"] = 12.0

        if "swap" in unique_actions:
            bias["mutation_prob"] = max(float(bias.get("mutation_prob") or 0.0), 0.30)
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 10)
            bias["crossover_prob"] = 0.95

        if "cg_recenter" in unique_actions:
            bias["sampling_sigma_ratio"] = min(float(bias["sampling_sigma_ratio"]), 0.02)
            bias["mutation_prob"] = 0.12
            bias["mutation_eta"] = 26.0
            bias["repair_cg_nudge_ratio"] = 1.15
            bias["repair_max_passes"] = 3

        if "group_move" in unique_actions:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 6)
            if bias.get("mutation_prob", None) is None:
                bias["mutation_prob"] = 0.18

        action_families = {
            family
            for family in (self._operator_action_family(action) for action in unique_actions)
            if family
        }
        if "thermal" in action_families:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 8)
            bias["sampling_sigma_ratio"] = max(float(bias["sampling_sigma_ratio"]), 0.035)
        if "structural" in action_families:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 8)
            bias["mutation_eta"] = max(float(bias["mutation_eta"]), 24.0)
            bias["crossover_prob"] = max(float(bias["crossover_prob"]), 0.92)
        if "power" in action_families:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 8)
            bias["mutation_eta"] = max(float(bias["mutation_eta"]), 24.0)
            bias["crossover_prob"] = max(float(bias["crossover_prob"]), 0.92)
        if "mission" in action_families:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 7)
            bias["mutation_eta"] = max(float(bias["mutation_eta"]), 22.0)

        credit_summary = self._summarize_operator_credit(action_sequence=unique_actions)
        if credit_summary:
            bias["credit_summary"] = dict(credit_summary)
        if enable_credit_bias and credit_summary:
            observations = int(credit_summary.get("observations", 0) or 0)
            feasible_rate = float(credit_summary.get("feasible_rate", 0.0) or 0.0)
            mean_best_cv = credit_summary.get("mean_best_cv", None)

            if observations >= 2:
                if feasible_rate >= 0.50:
                    bias["sampling_sigma_ratio"] = max(
                        0.01,
                        float(bias["sampling_sigma_ratio"]) * 0.85,
                    )
                    bias["sampling_jitter_count"] = max(
                        3,
                        int(bias["sampling_jitter_count"]) - 1,
                    )
                    if bias.get("mutation_prob", None) is not None:
                        bias["mutation_prob"] = max(
                            0.10,
                            float(bias["mutation_prob"]) * 0.85,
                        )
                    bias["mutation_eta"] = min(float(bias["mutation_eta"]) + 4.0, 45.0)
                elif feasible_rate <= 0.20:
                    bias["sampling_sigma_ratio"] = min(
                        0.09,
                        max(float(bias["sampling_sigma_ratio"]), 0.03) * 1.25,
                    )
                    bias["sampling_jitter_count"] = min(
                        16,
                        int(bias["sampling_jitter_count"]) + 2,
                    )
                    if bias.get("mutation_prob", None) is None:
                        bias["mutation_prob"] = 0.24
                    else:
                        bias["mutation_prob"] = min(
                            0.40,
                            max(float(bias["mutation_prob"]), 0.24),
                        )
                    bias["mutation_eta"] = max(float(bias["mutation_eta"]) - 3.0, 10.0)

            if mean_best_cv is not None:
                try:
                    parsed_cv = float(mean_best_cv)
                    if np.isfinite(parsed_cv) and parsed_cv > 0.0 and "cg_recenter" in unique_actions:
                        current = bias.get("repair_cg_nudge_ratio", None)
                        current_numeric = float(current) if current is not None else 1.0
                        bias["repair_cg_nudge_ratio"] = max(current_numeric, 1.15)
                except Exception:
                    pass

        return bias

    def _score_maas_attempt_result(
        self,
        diagnosis: Dict[str, Any],
        execution_result: Any,
        decoded_state: Optional[DesignState],
        attempt_payload: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        给单次 MaaS 求解结果打分，用于 MCTS 回传值。
        """
        payload = attempt_payload if isinstance(attempt_payload, dict) else {}
        status = str(diagnosis.get("status", ""))
        score_breakdown: Dict[str, float] = {
            "status_base": float({
                "feasible": 1500.0,
                "feasible_but_stalled": 1100.0,
                "no_feasible": 320.0,
                "empty_solution": 180.0,
                "runtime_error": -520.0,
                "missing_result": -320.0,
            }.get(status, -220.0))
        }

        if execution_result is not None:
            score_breakdown["aocc_cv_bonus"] = float(getattr(execution_result, "aocc_cv", 0.0) or 0.0) * 140.0
            score_breakdown["aocc_obj_bonus"] = float(
                getattr(execution_result, "aocc_objective", 0.0) or 0.0
            ) * 90.0
            best_cv_curve = list(getattr(execution_result, "best_cv_curve", []) or [])
            if best_cv_curve:
                best_cv = float(np.min(np.asarray(best_cv_curve, dtype=float)))
                if np.isfinite(best_cv):
                    score_breakdown["best_cv_penalty"] = -max(best_cv, 0.0) * 65.0

        if decoded_state is not None:
            try:
                geom = self._evaluate_geometry(decoded_state)
                score_breakdown["clearance_bonus"] = min(float(geom.min_clearance), 30.0) * 3.0
                score_breakdown["cg_penalty"] = -float(geom.cg_offset_magnitude) * 1.8
                score_breakdown["collision_penalty"] = -float(geom.num_collisions) * 320.0
            except Exception:
                pass

        if payload:
            attempt = payload.get("attempt", None)
            attempt_idx = None
            try:
                attempt_idx = int(attempt)
            except Exception:
                attempt_idx = None

            solver_cost_raw = payload.get("solver_cost", None)
            try:
                solver_cost = float(solver_cost_raw)
            except Exception:
                solver_cost = 0.0
            if np.isfinite(solver_cost) and solver_cost > 0.0:
                score_breakdown["solver_cost_penalty"] = -min(solver_cost, 1800.0) * 0.15

            is_feasible = status in {"feasible", "feasible_but_stalled"}
            if is_feasible and attempt_idx is not None and attempt_idx > 0:
                score_breakdown["first_feasible_bonus"] = max(0.0, 8.0 - float(attempt_idx)) * 95.0

            online_calls = payload.get("online_comsol_calls_so_far", None)
            if online_calls is None:
                runtime_snapshot = dict(payload.get("runtime_thermal_snapshot", {}) or {})
                online_calls = runtime_snapshot.get("executed_online_comsol", None)
            if online_calls is not None:
                try:
                    calls = max(int(online_calls), 0)
                    if is_feasible:
                        score_breakdown["comsol_efficiency_bonus"] = max(0.0, 40.0 - float(calls)) * 8.0
                    elif calls > 0:
                        score_breakdown["comsol_burn_penalty"] = -min(float(calls), 120.0) * 1.2
                except Exception:
                    pass

        score = float(sum(float(v) for v in score_breakdown.values()))
        if isinstance(attempt_payload, dict):
            attempt_payload["score_breakdown"] = {
                key: float(value) for key, value in score_breakdown.items()
            }
            attempt_payload["score_total"] = float(score)
        return float(score)

    def _resolve_pymoo_algorithm(self) -> str:
        """
        Resolve configured pymoo multi-objective algorithm.

        Supported values:
        - nsga2 (default)
        - nsga3
        - moead
        """
        opt_cfg = self.config.get("optimization", {})
        requested = str(opt_cfg.get("pymoo_algorithm", "nsga2")).strip().lower()
        if requested not in {"nsga2", "nsga3", "moead"}:
            self.logger.logger.warning(
                "Unknown pymoo_algorithm=%s, fallback to nsga2",
                requested,
            )
            return "nsga2"
        return requested

    def _create_pymoo_runner(
        self,
        *,
        pop_size: int,
        n_generations: int,
        seed: int,
        repair: Optional[Any],
        verbose: bool,
        return_least_infeasible: bool,
        initial_population: Optional[np.ndarray],
        operator_bias: Optional[Dict[str, Any]] = None,
    ) -> Any:
        opt_cfg = self.config.get("optimization", {})
        algorithm_key = self._resolve_pymoo_algorithm()
        shared_kwargs: Dict[str, Any] = {
            "pop_size": int(pop_size),
            "n_generations": int(n_generations),
            "seed": int(seed),
            "repair": repair,
            "verbose": bool(verbose),
            "return_least_infeasible": bool(return_least_infeasible),
            "initial_population": initial_population,
            "operator_bias": dict(operator_bias or {}),
            "nsga3_ref_dirs_partitions": int(
                opt_cfg.get("pymoo_nsga3_ref_dirs_partitions", 0)
            ),
            "moead_n_neighbors": int(
                opt_cfg.get("pymoo_moead_n_neighbors", 20)
            ),
            "moead_prob_neighbor_mating": float(
                opt_cfg.get("pymoo_moead_prob_neighbor_mating", 0.9)
            ),
            "moead_constraint_penalty": float(
                opt_cfg.get("pymoo_moead_constraint_penalty", 1000.0)
            ),
        }

        if algorithm_key == "nsga3":
            return PymooNSGA3Runner(**shared_kwargs)
        if algorithm_key == "moead":
            return PymooMOEADRunner(**shared_kwargs)
        return PymooNSGA2Runner(**shared_kwargs)

    def _resolve_maas_search_space_mode(self) -> str:
        """
        Resolve mass search-space mode.

        Supported modes:
        - coordinate
        - operator_program
        - hybrid
        """
        opt_cfg = self.config.get("optimization", {})
        mode = str(
            opt_cfg.get("mass_search_space", "coordinate")
        ).strip().lower()
        if mode not in {"coordinate", "operator_program", "hybrid"}:
            self.logger.logger.warning(
                "Unknown mass_search_space=%s, fallback to coordinate",
                mode,
            )
            return "coordinate"
        return mode

    def _create_maas_problem_generator(
        self,
        *,
        spec: Any,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, str]:
        """
        Create problem generator according to configured MaaS search space.
        """
        mode = self._resolve_maas_search_space_mode()
        metadata = dict(branch_metadata or {})
        branch_source = str(metadata.get("source", "")).strip().lower()
        action_name = str(branch_action or "").strip().lower()

        use_operator_program = False
        resolved_mode = mode
        if mode == "operator_program":
            use_operator_program = True
        elif mode == "hybrid":
            use_operator_program = (
                branch_source == "operator_program" or
                action_name.startswith("operator_program_d")
            )
            resolved_mode = (
                "hybrid_operator_program"
                if use_operator_program
                else "hybrid_coordinate"
            )

        if use_operator_program:
            opt_cfg = self.config.get("optimization", {})
            n_action_slots = max(
                1,
                int(opt_cfg.get("mass_operator_program_action_slots", 3)),
            )
            max_group_delta_mm = float(
                opt_cfg.get("mass_operator_program_max_group_delta_mm", 10.0)
            )
            max_hot_distance_mm = float(
                opt_cfg.get("mass_operator_program_max_hot_distance_mm", 12.0)
            )
            action_safety_tolerance = float(
                opt_cfg.get("mass_operator_program_action_safety_tolerance", 0.5)
            )
            forced_slot_actions: List[str] = []
            forced_slot_action_params: List[Dict[str, Any]] = []
            configured_forced_actions_raw = opt_cfg.get(
                "mass_operator_program_forced_slot_actions",
                [],
            )
            configured_forced_params_raw = opt_cfg.get(
                "mass_operator_program_forced_slot_action_params",
                [],
            )
            if isinstance(configured_forced_actions_raw, str):
                configured_forced_actions = [
                    item.strip().lower()
                    for item in configured_forced_actions_raw.split(",")
                    if item.strip()
                ]
            else:
                configured_forced_actions = [
                    str(item).strip().lower()
                    for item in list(configured_forced_actions_raw or [])
                    if str(item).strip()
                ]
            configured_forced_params: List[Dict[str, Any]] = []
            for item in list(configured_forced_params_raw or []):
                if isinstance(item, dict):
                    configured_forced_params.append(dict(item))
                else:
                    configured_forced_params.append({})

            if branch_source == "operator_program" or action_name.startswith("operator_program_d"):
                branch_forced_actions = [
                    str(item).strip().lower()
                    for item in list(metadata.get("action_sequence", []) or [])
                    if str(item).strip()
                ]
                program_actions: List[tuple[str, Dict[str, Any]]] = []
                operator_program_payload = dict(metadata.get("operator_program", {}) or {})
                for action_payload in list(operator_program_payload.get("actions", []) or []):
                    if not isinstance(action_payload, dict):
                        continue
                    action_text = str(action_payload.get("action", "")).strip().lower()
                    if not action_text:
                        continue
                    params_payload = action_payload.get("params", {})
                    params = dict(params_payload) if isinstance(params_payload, dict) else {}
                    program_actions.append((action_text, params))
                if not branch_forced_actions and program_actions:
                    branch_forced_actions = [item[0] for item in program_actions]
                if branch_forced_actions:
                    forced_slot_actions = branch_forced_actions
                if forced_slot_actions and program_actions:
                    used_indices: set[int] = set()
                    for slot_action in forced_slot_actions:
                        selected_params: Dict[str, Any] = {}
                        for idx, payload in enumerate(program_actions):
                            if idx in used_indices:
                                continue
                            if payload[0] != slot_action:
                                continue
                            selected_params = dict(payload[1] or {})
                            used_indices.add(idx)
                            break
                        forced_slot_action_params.append(selected_params)
            if not forced_slot_actions and configured_forced_actions:
                forced_slot_actions = configured_forced_actions
            if not forced_slot_action_params and configured_forced_params:
                forced_slot_action_params = list(configured_forced_params)
            return (
                OperatorProgramProblemGenerator(
                    spec=spec,
                    n_action_slots=n_action_slots,
                    max_group_delta_mm=max_group_delta_mm,
                    max_hot_distance_mm=max_hot_distance_mm,
                    action_safety_tolerance=action_safety_tolerance,
                    forced_slot_actions=forced_slot_actions,
                    forced_slot_action_params=forced_slot_action_params,
                ),
                resolved_mode,
            )

        return PymooProblemGenerator(spec=spec), resolved_mode

    def _build_maas_seed_population(
        self,
        *,
        problem_generator: PymooProblemGenerator,
        current_state: DesignState,
    ) -> np.ndarray:
        """
        构建 NSGA-II 初始种群注入向量。

        策略:
        1) 始终注入当前布局（warm-start）。
        2) 追加整体平移种子（不改变相对布局）以快速降低 CG 偏移。
        3) 在高维场景追加重组件定向种子，提升大规模 BOM 可行化概率。
        """
        opt_cfg = self.config.get("optimization", {})
        if not bool(opt_cfg.get("mass_enable_seed_population", True)):
            seed = problem_generator.codec.clip(problem_generator.codec.encode(current_state))
            return np.asarray([seed], dtype=float)

        codec = problem_generator.codec
        base_state = current_state.model_copy(deep=True)
        base_vector = codec.clip(codec.encode(base_state))
        seeds: List[np.ndarray] = [base_vector]

        seed_population_max = max(
            1,
            int(opt_cfg.get("mass_seed_population_max", 8)),
        )
        component_threshold = max(
            2,
            int(opt_cfg.get("mass_cg_seed_component_threshold", 12)),
        )

        try:
            centers, half_sizes = codec.geometry_arrays_from_state(base_state)
            masses = np.asarray(
                [max(float(comp.mass), 1e-9) for comp in base_state.components],
                dtype=float,
            )
            if centers.shape[0] == 0:
                return np.asarray([base_vector], dtype=float)

            env_min, env_max = codec.envelope_bounds
            lower_centers = env_min.reshape(1, 3) + half_sizes
            upper_centers = env_max.reshape(1, 3) - half_sizes
            comp_index = {comp.id: idx for idx, comp in enumerate(base_state.components)}
            axis_index = {"x": 0, "y": 1, "z": 2}
            for spec in codec.variable_specs:
                comp_i = comp_index.get(spec.component_id)
                axis_i = axis_index.get(spec.axis)
                if comp_i is None or axis_i is None:
                    continue
                lower_centers[comp_i, axis_i] = max(
                    float(lower_centers[comp_i, axis_i]),
                    float(spec.lower_bound),
                )
                upper_centers[comp_i, axis_i] = min(
                    float(upper_centers[comp_i, axis_i]),
                    float(spec.upper_bound),
                )
            lower_centers = np.minimum(lower_centers, upper_centers)

            # 每轴允许的全局平移区间（保证所有组件不越界）。
            shift_low = np.max(lower_centers - centers, axis=0)
            shift_high = np.min(upper_centers - centers, axis=0)

            total_mass = float(np.sum(masses))
            if total_mass <= 1e-9:
                return np.asarray([base_vector], dtype=float)

            if base_state.envelope.origin == "center":
                target_center = np.zeros(3, dtype=float)
            else:
                target_center = np.asarray(
                    [
                        float(base_state.envelope.outer_size.x) * 0.5,
                        float(base_state.envelope.outer_size.y) * 0.5,
                        float(base_state.envelope.outer_size.z) * 0.5,
                    ],
                    dtype=float,
                )

            com = np.sum(centers * masses.reshape(-1, 1), axis=0) / total_mass
            com_offset_vec = com - target_center

            # A) 全局平移种子：保持相对布局，优先快速拉回 CG。
            desired_shift = -com_offset_vec
            feasible_shift = np.clip(desired_shift, shift_low, shift_high)
            shift_norm = float(np.linalg.norm(feasible_shift))
            if shift_norm > 1e-9:
                for ratio in (0.35, 0.70, 1.00):
                    shifted_centers = centers + feasible_shift.reshape(1, 3) * ratio
                    state_shifted = base_state.model_copy(deep=True)
                    for idx, comp in enumerate(state_shifted.components):
                        comp.position.x = float(shifted_centers[idx, 0])
                        comp.position.y = float(shifted_centers[idx, 1])
                        comp.position.z = float(shifted_centers[idx, 2])
                    seeds.append(codec.clip(codec.encode(state_shifted)))

            # B) 重组件定向种子：在高组件数场景提升 CG 收敛概率。
            if (
                centers.shape[0] >= component_threshold and
                float(np.linalg.norm(com_offset_vec)) > 1e-9
            ):
                heavy_count = min(max(2, centers.shape[0] // 4), centers.shape[0])
                heavy_idx = np.argsort(-masses)[:heavy_count]
                heavy_mass = float(np.sum(masses[heavy_idx]))
                if heavy_mass > 1e-9:
                    ideal_delta = -(total_mass / heavy_mass) * com_offset_vec
                    for ratio in (0.20, 0.35, 0.50):
                        candidate_centers = np.array(centers, dtype=float, copy=True)
                        candidate_centers[heavy_idx, :] += ideal_delta.reshape(1, 3) * ratio
                        candidate_centers = np.minimum(
                            np.maximum(candidate_centers, lower_centers),
                            upper_centers,
                        )
                        state_shifted = base_state.model_copy(deep=True)
                        for idx, comp in enumerate(state_shifted.components):
                            comp.position.x = float(candidate_centers[idx, 0])
                            comp.position.y = float(candidate_centers[idx, 1])
                            comp.position.z = float(candidate_centers[idx, 2])
                        seeds.append(codec.clip(codec.encode(state_shifted)))
        except Exception as exc:
            self.logger.logger.warning("MaaS seed population generation failed, fallback warm-start: %s", exc)
            seeds = [base_vector]

        unique_seeds: List[np.ndarray] = []
        seen: set[tuple[float, ...]] = set()
        for vec in seeds:
            key = tuple(np.round(np.asarray(vec, dtype=float), 6).tolist())
            if key in seen:
                continue
            seen.add(key)
            unique_seeds.append(np.asarray(vec, dtype=float))
            if len(unique_seeds) >= seed_population_max:
                break

        return np.asarray(unique_seeds, dtype=float)

    def _extract_maas_candidate_diagnostics(
        self,
        *,
        execution_result: Any,
        problem_generator: Optional[Any],
    ) -> Dict[str, Any]:
        """
        从求解结果中提取候选主导违规项，供日志与 meta policy 分析使用。
        """
        payload: Dict[str, Any] = {
            "dominant_violation": "",
            "constraint_violation_breakdown": {},
            "best_candidate_metrics": {},
        }
        if execution_result is None or problem_generator is None:
            return payload

        try:
            pareto_x = np.asarray(getattr(execution_result, "pareto_X", np.empty((0, 0))), dtype=float)
            if pareto_x.size == 0:
                return payload
            if pareto_x.ndim == 1:
                pareto_x = pareto_x.reshape(1, -1)

            pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
            if pareto_cv_raw is not None:
                pareto_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
                if pareto_cv.size == pareto_x.shape[0] and np.any(np.isfinite(pareto_cv)):
                    best_idx = int(np.nanargmin(pareto_cv))
                else:
                    best_idx = 0
            else:
                best_idx = 0

            candidate_state = problem_generator.codec.decode(pareto_x[best_idx, :])
            evaluated = problem_generator.evaluate_state(candidate_state)
            metrics = dict(evaluated.get("metrics") or {})
            constraints = dict(evaluated.get("constraints") or {})

            violation_breakdown: Dict[str, float] = {}
            for name, value in constraints.items():
                numeric = float(value)
                if np.isfinite(numeric) and numeric > 0.0:
                    violation_breakdown[str(name)] = float(numeric)
            dominant_violation = ""
            if violation_breakdown:
                dominant_violation = max(
                    violation_breakdown.items(),
                    key=lambda item: float(item[1]),
                )[0]

            payload["dominant_violation"] = str(dominant_violation)
            payload["constraint_violation_breakdown"] = violation_breakdown
            payload["best_candidate_metrics"] = {
                "cg_offset": float(metrics.get("cg_offset", 0.0)),
                "max_temp": float(metrics.get("max_temp", 0.0)),
                "min_clearance": float(metrics.get("min_clearance", 0.0)),
                "num_collisions": float(metrics.get("num_collisions", 0.0)),
                "boundary_violation": float(metrics.get("boundary_violation", 0.0)),
                "mission_keepout_violation": float(metrics.get("mission_keepout_violation", 0.0)),
                "max_stress": float(metrics.get("max_stress", 0.0)),
                "max_displacement": float(metrics.get("max_displacement", 0.0)),
                "first_modal_freq": float(metrics.get("first_modal_freq", 0.0)),
                "safety_factor": float(metrics.get("safety_factor", 0.0)),
                "total_power": float(metrics.get("total_power", 0.0)),
                "peak_power": float(metrics.get("peak_power", 0.0)),
                "power_margin": float(metrics.get("power_margin", 0.0)),
                "voltage_drop": float(metrics.get("voltage_drop", 0.0)),
                "fov_occlusion_proxy": float(metrics.get("fov_occlusion_proxy", 0.0)),
                "emc_separation_proxy": float(metrics.get("emc_separation_proxy", 0.0)),
            }
            return payload
        except Exception as exc:
            self.logger.logger.warning("extract_maas_candidate_diagnostics failed: %s", exc)
            return payload

    def _evaluate_maas_intent_once(
        self,
        *,
        iteration: int,
        attempt: int,
        intent: ModelingIntent,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]],
        current_state: DesignState,
        runtime_thermal_evaluator: Any,
        pop_size: int,
        n_generations: int,
        seed: int,
        verbose: bool,
        return_least_infeasible: bool,
        maas_relax_ratio: float,
        thermal_evaluator_mode: str,
    ) -> Dict[str, Any]:
        """
        执行单次 intent 编译/求解/诊断并返回结构化结果。
        """
        branch_meta = dict(branch_metadata or {})
        operator_bias = self._build_operator_bias_from_branch(
            branch_action=branch_action,
            branch_metadata=branch_meta,
        )
        mission_cfg = dict(self.config.get("optimization", {}) or {})
        mass_real_only = bool(mission_cfg.get("mass_physics_real_only", False))
        require_structural_real = bool(
            mission_cfg.get("mass_source_gate_require_structural_real", False)
        ) or bool(mass_real_only)
        require_power_real = bool(
            mission_cfg.get("mass_source_gate_require_power_real", False)
        ) or bool(mass_real_only)
        require_thermal_real = bool(
            mission_cfg.get("mass_source_gate_require_thermal_real", False)
        ) or bool(mass_real_only)
        require_mission_real = bool(
            mission_cfg.get("mass_source_gate_require_mission_real", False)
        ) or bool(mass_real_only)
        mission_axis = str(
            mission_cfg.get("mission_keepout_axis", "z")
        ).strip().lower() or "z"
        if mission_axis not in {"x", "y", "z"}:
            mission_axis = "z"
        mission_keepout_center_mm = float(
            mission_cfg.get("mission_keepout_center_mm", 0.0)
        )
        mission_min_separation_mm = float(
            mission_cfg.get("mission_min_separation_mm", 0.0)
        )

        effective_intent = intent
        mission_precheck = self._precheck_mission_keepout_feasibility(
            intent=effective_intent,
            base_state=current_state,
            axis=mission_axis,
            keepout_center_mm=mission_keepout_center_mm,
            min_separation_mm=mission_min_separation_mm,
        )
        mission_precheck_repair: Dict[str, Any] = {}
        if bool(mission_precheck.get("checked", False)) and not bool(mission_precheck.get("feasible", True)):
            repaired_intent, mission_precheck_repair = self._repair_mission_precheck_intent_bounds(
                intent=effective_intent,
                base_state=current_state,
                axis=mission_axis,
                keepout_center_mm=mission_keepout_center_mm,
                min_separation_mm=mission_min_separation_mm,
                precheck=mission_precheck,
            )
            if bool(mission_precheck_repair.get("applied", False)):
                effective_intent = repaired_intent
            mission_precheck_after = dict(
                mission_precheck_repair.get("precheck_after", {}) or {}
            )
            if mission_precheck_after:
                mission_precheck = mission_precheck_after
        mission_precheck_blocked = bool(
            mission_precheck.get("checked", False) and
            not mission_precheck.get("feasible", True)
        )

        formulation_report = formulate_modeling_intent(effective_intent)
        if mission_precheck_repair:
            formulation_report = dict(formulation_report)
            formulation_report["mission_precheck_repair"] = dict(mission_precheck_repair)
        self.logger.log_llm_interaction(
            iteration=iteration,
            role=f"model_agent_formulation_attempt_{attempt:02d}",
            request={
                "intent_id": effective_intent.intent_id,
                "branch_action": branch_action,
                "branch_source": str(branch_meta.get("source", "")),
                "operator_program_id": str(branch_meta.get("program_id", "")),
            },
            response=formulation_report,
        )

        spec, compile_report = compile_intent_to_problem_spec(
            intent=effective_intent,
            base_state=current_state,
            runtime_constraints=self.runtime_constraints,
            thermal_evaluator=runtime_thermal_evaluator,
            enable_semantic_zones=bool(
                self.config.get("optimization", {}).get("mass_enable_semantic_zones", True)
            ),
        )
        mission_fov_evaluator = None
        evaluator_resolver = getattr(self, "_resolve_mission_fov_evaluator", None)
        if callable(evaluator_resolver):
            try:
                mission_fov_evaluator = evaluator_resolver()
            except Exception:
                mission_fov_evaluator = None
        spec.tags["mission_keepout_axis"] = str(mission_axis)
        spec.tags["mission_keepout_center_mm"] = float(mission_keepout_center_mm)
        spec.tags["mission_min_separation_mm"] = float(mission_min_separation_mm)
        spec.tags["mass_physics_real_only"] = bool(mass_real_only)
        spec.tags["mass_require_structural_real"] = bool(require_structural_real)
        spec.tags["mass_require_power_real"] = bool(require_power_real)
        spec.tags["mass_require_thermal_real"] = bool(require_thermal_real)
        spec.tags["mission_real_required"] = bool(require_mission_real)
        if callable(mission_fov_evaluator):
            spec.tags["mission_fov_evaluator"] = mission_fov_evaluator

        execution_result = None
        solver_exception = None
        solver_cost = 0.0
        problem_generator = None
        search_space_mode = self._resolve_maas_search_space_mode()
        if mission_precheck_blocked:
            diagnosis = {
                "status": "precheck_mission_infeasible",
                "reason": "mission_keepout_geometrically_infeasible",
                "retryable": False,
                "mission_precheck": dict(mission_precheck),
                "mission_precheck_repair": dict(mission_precheck_repair),
            }
            relaxation_suggestions = []
        else:
            try:
                solver_tic = time.perf_counter()
                problem_generator, search_space_mode = self._create_maas_problem_generator(
                    spec=spec,
                    branch_action=branch_action,
                    branch_metadata=branch_meta,
                )
                problem = problem_generator.create_problem()
                opt_cfg = self.config.get("optimization", {})
                enable_coordinate_repair = search_space_mode in {"coordinate", "hybrid_coordinate"}
                repair = None
                if enable_coordinate_repair:
                    repair = CentroidPushApartRepair(
                        codec=problem_generator.codec,
                        cg_limit_mm=float(problem_generator.max_cg_offset_mm),
                        cg_nudge_ratio=float(opt_cfg.get("mass_repair_cg_nudge_ratio", 0.90)),
                    )

                if bool(opt_cfg.get("mass_enable_seed_population", True)):
                    if enable_coordinate_repair:
                        seed_population = self._build_maas_seed_population(
                            problem_generator=problem_generator,
                            current_state=current_state,
                        )
                    else:
                        codec = problem_generator.codec
                        if (
                            hasattr(codec, "build_seed_population") and
                            bool(opt_cfg.get("mass_enable_operator_seed_population", True))
                        ):
                            try:
                                seed_population = np.asarray(
                                    codec.build_seed_population(
                                        reference_state=current_state,
                                        max_count=max(
                                            1,
                                            int(opt_cfg.get("mass_seed_population_max", 8)),
                                        ),
                                    ),
                                    dtype=float,
                                )
                            except Exception:
                                seed_population = np.asarray(
                                    [
                                        codec.clip(
                                            codec.encode(current_state)
                                        )
                                    ],
                                    dtype=float,
                                )
                        else:
                            seed_population = np.asarray(
                                [
                                    codec.clip(
                                        codec.encode(current_state)
                                    )
                                ],
                                dtype=float,
                            )
                else:
                    seed_population = None
                runner = self._create_pymoo_runner(
                    pop_size=pop_size,
                    n_generations=n_generations,
                    seed=seed + (attempt - 1),
                    repair=repair,
                    verbose=verbose,
                    return_least_infeasible=return_least_infeasible,
                    initial_population=seed_population,
                    operator_bias=operator_bias,
                )
                execution_result = runner.run(problem)
                solver_cost = time.perf_counter() - solver_tic
            except Exception as exc:
                solver_exception = str(exc)
                self.logger.logger.warning(f"pymoo solve phase failed: {exc}")

        if (not mission_precheck_blocked) and execution_result is not None:
            diagnosis = diagnose_solver_outcome(execution_result)
            relaxation_suggestions = suggest_constraint_relaxation(
                effective_intent,
                diagnosis,
                max_relax_ratio=maas_relax_ratio,
            )
        elif not mission_precheck_blocked:
            diagnosis = {
                "status": "runtime_error",
                "reason": "solver_exception",
                "traceback": solver_exception or "",
            }
            relaxation_suggestions = []

        best_cv = float("inf")
        aocc_cv = 0.0
        aocc_obj = 0.0
        if execution_result is not None:
            curve = list(getattr(execution_result, "best_cv_curve", []) or [])
            if curve:
                try:
                    best_cv = float(np.min(np.asarray(curve, dtype=float)))
                except Exception:
                    best_cv = float("inf")
            aocc_cv = float(getattr(execution_result, "aocc_cv", 0.0) or 0.0)
            aocc_obj = float(getattr(execution_result, "aocc_objective", 0.0) or 0.0)

        decoded_state = self._decode_maas_candidate_state(
            execution_result=execution_result,
            problem_generator=problem_generator,
            base_state=current_state,
            attempt=attempt,
        )
        candidate_diagnostics = self._extract_maas_candidate_diagnostics(
            execution_result=execution_result,
            problem_generator=problem_generator,
        )

        runtime_thermal_snapshot: Dict[str, Any] = {}
        online_comsol_calls_so_far: Optional[int] = None
        if runtime_thermal_evaluator is not None:
            try:
                runtime_thermal_snapshot = dict(
                    getattr(runtime_thermal_evaluator, "stats", {}) or {}
                )
            except Exception:
                runtime_thermal_snapshot = {}
            if runtime_thermal_snapshot:
                try:
                    online_comsol_calls_so_far = int(
                        runtime_thermal_snapshot.get("executed_online_comsol", 0) or 0
                    )
                except Exception:
                    online_comsol_calls_so_far = None

        attempt_payload: Dict[str, Any] = {
            "attempt": attempt,
            "intent_id": effective_intent.intent_id,
            "branch_action": branch_action,
            "branch_source": str(branch_meta.get("source", "")),
            "pymoo_algorithm_requested": self._resolve_pymoo_algorithm(),
            "search_space_mode": str(search_space_mode),
            "operator_program_id": str(branch_meta.get("program_id", "")),
            "operator_actions": list(branch_meta.get("action_sequence", []) or []),
            "operator_bias": dict(operator_bias or {}),
            "thermal_evaluator_mode": thermal_evaluator_mode,
            "formulation": formulation_report,
            "compile_report": compile_report.to_dict(),
            "diagnosis": diagnosis,
            "relaxation_suggestions": relaxation_suggestions,
            "solver_message": execution_result.message if execution_result is not None else solver_exception,
            "solver_metadata": (
                dict(getattr(execution_result, "metadata", {}) or {})
                if execution_result is not None else {}
            ),
            "solver_cost": float(solver_cost),
            "has_candidate_state": decoded_state is not None,
            "relaxation_applied_count": 0,
            "score": 0.0,
            "best_cv": best_cv if np.isfinite(best_cv) else None,
            "aocc_cv": float(aocc_cv),
            "aocc_objective": float(aocc_obj),
            "dominant_violation": str(candidate_diagnostics.get("dominant_violation", "")),
            "constraint_violation_breakdown": dict(
                candidate_diagnostics.get("constraint_violation_breakdown", {}) or {}
            ),
            "best_candidate_metrics": dict(
                candidate_diagnostics.get("best_candidate_metrics", {}) or {}
            ),
        }

        generation_records: List[Dict[str, Any]] = []
        if execution_result is not None:
            try:
                generation_records = list(
                    (getattr(execution_result, "metadata", {}) or {}).get("generation_records", [])
                    or []
                )
            except Exception:
                generation_records = []
        if generation_records:
            self.logger.log_maas_generation_events(
                {
                    "iteration": int(iteration),
                    "attempt": int(attempt),
                    "branch_action": str(branch_action),
                    "branch_source": str(branch_meta.get("source", "")),
                    "search_space_mode": str(search_space_mode),
                    "pymoo_algorithm": str(self._resolve_pymoo_algorithm()),
                    "records": generation_records,
                }
            )

        if runtime_thermal_snapshot:
            attempt_payload["runtime_thermal_snapshot"] = {
                key: value for key, value in runtime_thermal_snapshot.items()
            }
        if mission_precheck.get("checked", False):
            attempt_payload["mission_precheck"] = dict(mission_precheck)
        if mission_precheck_repair.get("attempted", False):
            attempt_payload["mission_precheck_repair"] = dict(mission_precheck_repair)
        if online_comsol_calls_so_far is not None:
            attempt_payload["online_comsol_calls_so_far"] = int(online_comsol_calls_so_far)

        decoded_operator_actions = self._extract_operator_program_actions_from_execution(
            execution_result=execution_result,
            problem_generator=problem_generator,
        )
        if decoded_operator_actions:
            attempt_payload["decoded_operator_actions"] = list(decoded_operator_actions)

        operator_action_sequence = self._extract_operator_action_sequence(
            branch_action=branch_action,
            branch_metadata=branch_meta,
            attempt_payload=attempt_payload,
        )
        if not operator_action_sequence and decoded_operator_actions:
            operator_action_sequence = list(decoded_operator_actions)
        if not operator_action_sequence:
            operator_action_sequence = list(branch_meta.get("action_sequence", []) or [])
        thermal_realization = self._build_operator_thermal_realization_evidence(
            action_sequence=list(operator_action_sequence),
            base_state=current_state,
            decoded_state=decoded_state,
            thermal_evaluator_mode=thermal_evaluator_mode,
            runtime_thermal_snapshot=runtime_thermal_snapshot,
        )
        operator_reports = self._build_operator_action_reports(
            action_sequence=operator_action_sequence,
            thermal_evaluator_mode=thermal_evaluator_mode,
        )
        attempt_payload["operator_actions"] = list(operator_action_sequence)
        attempt_payload["operator_implementation"] = dict(
            operator_reports.get("implementation", {}) or {}
        )
        attempt_payload["operator_family_gate"] = dict(
            operator_reports.get("family_gate", {}) or {}
        )
        attempt_payload["operator_realization_gate"] = dict(
            operator_reports.get("realization_gate", {}) or {}
        )
        attempt_payload["operator_realization_context"] = dict(
            operator_reports.get("realization_context", {}) or {}
        )
        attempt_payload["operator_thermal_realization"] = dict(thermal_realization)

        score = self._score_maas_attempt_result(
            diagnosis=diagnosis,
            execution_result=execution_result,
            decoded_state=decoded_state,
            attempt_payload=attempt_payload,
        )
        attempt_payload["score"] = float(score)

        self._update_operator_credit_from_attempt(
            branch_action=branch_action,
            branch_metadata=branch_meta,
            attempt_payload=attempt_payload,
            score=float(score),
        )
        attempt_payload["operator_credit_snapshot"] = self._summarize_operator_credit(
            action_sequence=list(operator_action_sequence),
        )

        return {
            "intent": effective_intent,
            "formulation_report": formulation_report,
            "compile_report": compile_report.to_dict(),
            "execution_result": execution_result,
            "solver_exception": solver_exception,
            "solver_cost": float(solver_cost),
            "problem_generator": problem_generator,
            "diagnosis": diagnosis,
            "relaxation_suggestions": relaxation_suggestions,
            "decoded_state": decoded_state,
            "attempt_payload": attempt_payload,
            "score": float(score),
        }

    def _run_mass_pipeline(
        self,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        mass 入口：委托给 MaaSPipelineService 执行闭环建模流程。
        """
        return self.maas_pipeline_service.run_pipeline(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
        )

    def _run_mass_phase_a(
        self,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        兼容旧入口，转发到新的 mass 闭环实现。
        """
        return self._run_mass_pipeline(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
        )



