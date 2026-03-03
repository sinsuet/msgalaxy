"""
MaaS pipeline service.

Extracts the pymoo_maas A/B/C/D orchestration flow out of WorkflowOrchestrator
to keep orchestrator focused on global workflow control.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from core.exceptions import SatelliteDesignError
from core.protocol import DesignState
from optimization.maas_mcts import MCTSEvaluation, MCTSNode, MCTSVariant, MaaSMCTSPlanner
from optimization.meta_policy import propose_meta_policy_actions
from optimization.modeling_validator import validate_modeling_intent
from optimization.observability.materialize import materialize_observability_tables
from optimization.protocol import ModelingIntent, PowerMetrics, StructuralMetrics, ThermalMetrics
from optimization.trace_features import extract_maas_trace_features

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


class MaaSPipelineService:
    """Encapsulates pymoo_maas closed-loop execution."""

    def __init__(self, host: "WorkflowOrchestrator") -> None:
        self.host = host

    def run_pipeline(
        self,
        *,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        pymoo_maas 模式：
        A. Understanding: 建模意图生成与校验
        B. Formulation: 约束标准化
        C. Coding/Execution: 编译并运行 NSGA-II
        D. Reflection: 诊断失败并自动松弛后重求解
        """
        host = self.host

        host.logger.logger.info(
            "Entering pymoo_maas pipeline: "
            "Understanding -> Formulation -> Coding -> Reflection"
        )
        host.logger.save_run_manifest(
            {
                "optimization_mode": "pymoo_maas",
                "pymoo_algorithm": str(host._resolve_pymoo_algorithm()),
                "status": "RUNNING",
            }
        )

        iteration = 1
        current_state.state_id = "state_iter_01_maas"
        current_state.iteration = iteration
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "A",
                "status": "started",
                "details": {"step": "understanding"},
            }
        )

        try:
            current_metrics, violations = host._evaluate_design(current_state, iteration)
        except Exception as exc:
            host.logger.logger.warning(
                f"Phase A physics evaluation failed, fallback to geometry-only context: {exc}"
            )
            geometry_metrics = host._evaluate_geometry(current_state)
            current_metrics = {
                "geometry": geometry_metrics,
                "thermal": ThermalMetrics(max_temp=0.0, min_temp=0.0, avg_temp=0.0, temp_gradient=0.0),
                "structural": StructuralMetrics(
                    max_stress=0.0, max_displacement=0.0, first_modal_freq=0.0, safety_factor=2.0
                ),
                "power": PowerMetrics(
                    total_power=sum(c.power for c in current_state.components),
                    peak_power=sum(c.power for c in current_state.components),
                    power_margin=0.0,
                    voltage_drop=0.0,
                ),
                "diagnostics": {"solver_cost": 0.0},
            }
            violations = host._check_violations(
                current_metrics["geometry"],
                current_metrics["thermal"],
                current_metrics["structural"],
                current_metrics["power"],
            )

        def _metrics_from_runtime_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
            geometry_obj = bundle.get("geometry")
            thermal_obj = bundle.get("thermal")

            if isinstance(geometry_obj, dict):
                geometry = dict(geometry_obj)
            elif hasattr(geometry_obj, "model_dump"):
                geometry = dict(geometry_obj.model_dump() or {})
            elif hasattr(geometry_obj, "__dict__"):
                geometry = dict(getattr(geometry_obj, "__dict__", {}) or {})
            else:
                geometry = {}

            if isinstance(thermal_obj, dict):
                thermal = dict(thermal_obj)
            elif hasattr(thermal_obj, "model_dump"):
                thermal = dict(thermal_obj.model_dump() or {})
            elif hasattr(thermal_obj, "__dict__"):
                thermal = dict(getattr(thermal_obj, "__dict__", {}) or {})
            else:
                thermal = {}
            return {
                "cg_offset": float(getattr(geometry_obj, "cg_offset_magnitude", geometry.get("cg_offset_magnitude", geometry.get("cg_offset", 0.0))) or 0.0),
                "max_temp": float(getattr(thermal_obj, "max_temp", thermal.get("max_temp", 0.0)) or 0.0),
                "min_clearance": float(getattr(geometry_obj, "min_clearance", geometry.get("min_clearance", 0.0)) or 0.0),
                "num_collisions": float(getattr(geometry_obj, "num_collisions", geometry.get("num_collisions", 0.0)) or 0.0),
            }

        layout_snapshot_seq = {"n": 0}
        layout_snapshot_state = {"last": current_state.model_copy(deep=True)}
        layout_snapshot_hash_state = {"last": ""}
        layout_snapshot_hash_counts: Dict[str, int] = {}

        def _layout_state_hash(design_state: Optional[DesignState]) -> str:
            if design_state is None:
                return ""
            if hasattr(design_state, "model_dump"):
                state_payload = dict(design_state.model_dump() or {})
            elif isinstance(design_state, dict):
                state_payload = dict(design_state)
            else:
                state_payload = {}

            components = list(state_payload.get("components", []) or [])
            normalized_components: List[Dict[str, Any]] = []
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                position = dict(comp.get("position", {}) or {})
                dimensions = dict(comp.get("dimensions", {}) or {})
                rotation = dict(comp.get("rotation", {}) or {})
                contacts = dict(comp.get("thermal_contacts", {}) or {})
                normalized_components.append(
                    {
                        "id": str(comp.get("id", "") or ""),
                        "position": [
                            round(float(position.get("x", 0.0) or 0.0), 6),
                            round(float(position.get("y", 0.0) or 0.0), 6),
                            round(float(position.get("z", 0.0) or 0.0), 6),
                        ],
                        "dimensions": [
                            round(float(dimensions.get("x", 0.0) or 0.0), 6),
                            round(float(dimensions.get("y", 0.0) or 0.0), 6),
                            round(float(dimensions.get("z", 0.0) or 0.0), 6),
                        ],
                        "rotation": [
                            round(float(rotation.get("x", 0.0) or 0.0), 6),
                            round(float(rotation.get("y", 0.0) or 0.0), 6),
                            round(float(rotation.get("z", 0.0) or 0.0), 6),
                        ],
                        "heatsink": comp.get("heatsink"),
                        "bracket": comp.get("bracket"),
                        "coating_type": str(comp.get("coating_type", "default") or "default"),
                        "emissivity": round(float(comp.get("emissivity", 0.8) or 0.8), 6),
                        "absorptivity": round(float(comp.get("absorptivity", 0.3) or 0.3), 6),
                        "thermal_contacts": sorted(
                            (
                                str(key),
                                round(float(value or 0.0), 6),
                            )
                            for key, value in contacts.items()
                        ),
                    }
                )
            normalized_components.sort(key=lambda item: str(item.get("id", "")))

            envelope = dict(state_payload.get("envelope", {}) or {})
            envelope_size = dict(envelope.get("outer_size", {}) or {})
            normalized = {
                "components": normalized_components,
                "envelope": {
                    "origin": str(envelope.get("origin", "center") or "center"),
                    "outer_size": [
                        round(float(envelope_size.get("x", 0.0) or 0.0), 6),
                        round(float(envelope_size.get("y", 0.0) or 0.0), 6),
                        round(float(envelope_size.get("z", 0.0) or 0.0), 6),
                    ],
                },
            }
            raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return hashlib.sha1(raw.encode("utf-8")).hexdigest()

        def _save_layout_snapshot(
            *,
            stage: str,
            attempt: int,
            design_state: Optional[DesignState],
            metrics: Optional[Dict[str, Any]] = None,
            branch_action: str = "",
            branch_source: str = "",
            diagnosis_status: str = "",
            diagnosis_reason: str = "",
            operator_program_id: str = "",
            operator_actions: Optional[List[str]] = None,
            thermal_source: str = "",
            metadata: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            if design_state is None:
                return {
                    "layout_state_hash": "",
                    "duplicate_with_previous_snapshot": False,
                    "duplicate_occurrence_index": 0,
                }
            layout_snapshot_seq["n"] += 1
            prev_state = layout_snapshot_state.get("last")
            current_hash = _layout_state_hash(design_state)
            previous_hash = str(layout_snapshot_hash_state.get("last", "") or "")
            duplicate_prev = bool(previous_hash and current_hash and previous_hash == current_hash)
            duplicate_occurrence = int(layout_snapshot_hash_counts.get(current_hash, 0) + 1)
            layout_snapshot_hash_counts[current_hash] = int(duplicate_occurrence)
            merged_metadata = dict(metadata or {})
            merged_metadata.update(
                {
                    "layout_state_hash": str(current_hash or ""),
                    "duplicate_with_previous_snapshot": bool(duplicate_prev),
                    "duplicate_occurrence_index": int(duplicate_occurrence),
                }
            )
            snapshot_result = host.logger.save_layout_snapshot(
                iteration=int(iteration),
                attempt=int(attempt),
                sequence=int(layout_snapshot_seq["n"]),
                stage=str(stage),
                design_state=design_state,
                thermal_source=str(thermal_source or ""),
                metrics=dict(metrics or {}),
                branch_action=str(branch_action or ""),
                branch_source=str(branch_source or ""),
                diagnosis_status=str(diagnosis_status or ""),
                diagnosis_reason=str(diagnosis_reason or ""),
                operator_program_id=str(operator_program_id or ""),
                operator_actions=list(operator_actions or []),
                previous_design_state=prev_state,
                metadata=merged_metadata,
            )
            layout_snapshot_hash_state["last"] = str(current_hash or "")
            layout_snapshot_state["last"] = design_state.model_copy(deep=True)
            return {
                "layout_state_hash": str(current_hash or ""),
                "duplicate_with_previous_snapshot": bool(duplicate_prev),
                "duplicate_occurrence_index": int(duplicate_occurrence),
                "snapshot_path": str(snapshot_result.get("snapshot_path", "") or ""),
                "sequence": int(layout_snapshot_seq["n"]),
            }

        _save_layout_snapshot(
            stage="initial",
            attempt=0,
            design_state=current_state.model_copy(deep=True),
            metrics=_metrics_from_runtime_bundle(current_metrics),
            diagnosis_status="baseline",
            diagnosis_reason="before_solver",
            thermal_source=str(thermal_evaluator_mode if "thermal_evaluator_mode" in locals() else "proxy"),
            metadata={"source": "maas_pipeline_start"},
        )

        context = host._build_global_context(
            iteration=iteration,
            design_state=current_state,
            metrics=current_metrics,
            violations=violations,
        )
        host.logger.save_trace_data(
            iteration=iteration,
            context_pack=context.model_dump() if hasattr(context, "model_dump") else context.__dict__,
        )

        requirement_text = host._build_maas_requirement_text(bom_file)
        modeling_intent: ModelingIntent = host.meta_reasoner.generate_modeling_intent(
            context=context,
            runtime_constraints=host.runtime_constraints,
            requirement_text=requirement_text,
        )
        validation = validate_modeling_intent(modeling_intent, host.runtime_constraints)

        if validation.get("warnings"):
            for warning in validation["warnings"]:
                host.logger.logger.warning(f"ModelingIntent warning: {warning}")

        host.logger.log_llm_interaction(
            iteration=iteration,
            role="model_agent_validation",
            request={
                "runtime_constraints": host.runtime_constraints,
                "optimization_mode": host.optimization_mode,
            },
            response=validation,
        )

        if not validation.get("is_valid", False):
            errors = validation.get("errors", [])
            raise SatelliteDesignError(
                "ModelingIntent validation failed: " + " | ".join(errors)
            )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "A",
                "status": "completed",
                "details": {
                    "intent_id": str(modeling_intent.intent_id),
                    "warning_count": int(len(validation.get("warnings", []) or [])),
                },
            }
        )

        opt_cfg = host.config.get("optimization", {})
        pymoo_algorithm = host._resolve_pymoo_algorithm()
        pop_size = int(opt_cfg.get("pymoo_pop_size", 96))
        n_generations = int(opt_cfg.get("pymoo_n_gen", max(max_iterations, 40)))
        seed = int(opt_cfg.get("pymoo_seed", 42))
        verbose = bool(opt_cfg.get("pymoo_verbose", False))
        return_least_infeasible = bool(opt_cfg.get("pymoo_return_least_infeasible", True))
        maas_max_attempts = max(1, int(opt_cfg.get("pymoo_maas_max_attempts", 3)))
        maas_relax_ratio = max(0.0, float(opt_cfg.get("pymoo_maas_relax_ratio", 0.08)))
        maas_auto_relax = bool(opt_cfg.get("pymoo_maas_auto_relax", True))
        maas_retry_on_stall = bool(opt_cfg.get("pymoo_maas_retry_on_stall", True))
        enable_mcts = bool(opt_cfg.get("pymoo_maas_enable_mcts", True))
        mcts_budget = max(1, int(opt_cfg.get("pymoo_maas_mcts_budget", maas_max_attempts)))
        mcts_max_depth = max(1, int(opt_cfg.get("pymoo_maas_mcts_max_depth", 2)))
        mcts_c_uct = float(opt_cfg.get("pymoo_maas_mcts_c_uct", 1.2))
        mcts_stagnation_rounds = max(0, int(opt_cfg.get("pymoo_maas_mcts_stagnation_rounds", 2)))
        mcts_min_score_improvement = float(opt_cfg.get("pymoo_maas_mcts_min_score_improvement", 1.0))
        mcts_min_cv_improvement = float(opt_cfg.get("pymoo_maas_mcts_min_cv_improvement", 0.1))
        mcts_prune_margin = float(opt_cfg.get("pymoo_maas_mcts_prune_margin", 100.0))
        mcts_action_prior_weight = float(opt_cfg.get("pymoo_maas_mcts_action_prior_weight", 0.02))
        mcts_cv_penalty_weight = float(opt_cfg.get("pymoo_maas_mcts_cv_penalty_weight", 0.2))
        thermal_evaluator_mode = str(
            opt_cfg.get("pymoo_maas_thermal_evaluator_mode", "proxy")
        ).strip().lower()
        trace_feature_window = max(
            1,
            int(opt_cfg.get("pymoo_maas_trace_feature_window", 5)),
        )
        enable_meta_policy = bool(opt_cfg.get("pymoo_maas_enable_meta_policy", True))
        meta_policy_apply_runtime = bool(
            opt_cfg.get("pymoo_maas_meta_policy_apply_runtime", True)
        )
        meta_policy_min_attempts = max(
            1,
            int(opt_cfg.get("pymoo_maas_meta_policy_min_attempts", 2)),
        )
        enable_physics_audit = bool(opt_cfg.get("pymoo_maas_enable_physics_audit", True))
        audit_top_k = max(1, int(opt_cfg.get("pymoo_maas_audit_top_k", 3)))
        enforce_audit_feasible = bool(
            opt_cfg.get("pymoo_maas_enforce_audit_feasible", True)
        )
        simulation_backend = str(host.config.get("simulation", {}).get("backend", "")).strip().lower()
        runtime_thermal_evaluator = host._build_maas_runtime_thermal_evaluator(
            mode=thermal_evaluator_mode,
            base_iteration=iteration,
        )

        active_intent = modeling_intent
        candidate_state: Optional[DesignState] = None
        maas_attempts: List[Dict[str, Any]] = []
        total_solver_cost = 0.0
        last_formulation_report: Dict[str, Any] = {}
        last_compile_report: Dict[str, Any] = {}
        last_execution_result = None
        last_solver_exception = None
        last_diagnosis: Dict[str, Any] = {"status": "missing_result", "reason": "not_run"}
        last_relaxation_suggestions: List[Dict[str, Any]] = []
        last_problem_generator = None
        meta_policy_events: List[Dict[str, Any]] = []
        meta_policy_report: Dict[str, Any] = {
            "enabled": bool(enable_meta_policy),
            "runtime_apply_enabled": bool(meta_policy_apply_runtime),
            "min_attempts": int(meta_policy_min_attempts),
            "events": [],
            "next_run_recommendation": {},
        }

        def _current_runtime_knobs() -> Dict[str, Any]:
            budget = int(opt_cfg.get("pymoo_maas_online_comsol_eval_budget", 0))
            scheduler_knobs: Dict[str, Any] = {
                "online_comsol_schedule_mode": str(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_mode", "budget_only")
                ).strip().lower(),
                "online_comsol_schedule_top_fraction": float(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_top_fraction", 0.20)
                ),
                "online_comsol_schedule_min_observations": int(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_min_observations", 8)
                ),
                "online_comsol_schedule_warmup_calls": int(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_warmup_calls", 2)
                ),
                "online_comsol_schedule_explore_prob": float(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_explore_prob", 0.05)
                ),
                "online_comsol_schedule_uncertainty_weight": float(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_uncertainty_weight", 0.35)
                ),
                "online_comsol_schedule_uncertainty_scale_mm": float(
                    opt_cfg.get("pymoo_maas_online_comsol_schedule_uncertainty_scale_mm", 25.0)
                ),
            }
            if runtime_thermal_evaluator is not None and hasattr(runtime_thermal_evaluator, "get_eval_budget"):
                try:
                    budget = int(runtime_thermal_evaluator.get_eval_budget())  # type: ignore[attr-defined]
                except Exception:
                    pass
            if runtime_thermal_evaluator is not None and hasattr(runtime_thermal_evaluator, "get_scheduler_params"):
                try:
                    scheduler_params = dict(runtime_thermal_evaluator.get_scheduler_params())  # type: ignore[attr-defined]
                    if scheduler_params:
                        scheduler_knobs.update({
                            "online_comsol_schedule_mode": str(
                                scheduler_params.get(
                                    "mode",
                                    scheduler_knobs["online_comsol_schedule_mode"],
                                )
                            ).strip().lower(),
                            "online_comsol_schedule_top_fraction": float(
                                scheduler_params.get(
                                    "top_fraction",
                                    scheduler_knobs["online_comsol_schedule_top_fraction"],
                                )
                            ),
                            "online_comsol_schedule_min_observations": int(
                                scheduler_params.get(
                                    "min_observations",
                                    scheduler_knobs["online_comsol_schedule_min_observations"],
                                )
                            ),
                            "online_comsol_schedule_warmup_calls": int(
                                scheduler_params.get(
                                    "warmup_calls",
                                    scheduler_knobs["online_comsol_schedule_warmup_calls"],
                                )
                            ),
                            "online_comsol_schedule_explore_prob": float(
                                scheduler_params.get(
                                    "explore_prob",
                                    scheduler_knobs["online_comsol_schedule_explore_prob"],
                                )
                            ),
                            "online_comsol_schedule_uncertainty_weight": float(
                                scheduler_params.get(
                                    "uncertainty_weight",
                                    scheduler_knobs["online_comsol_schedule_uncertainty_weight"],
                                )
                            ),
                            "online_comsol_schedule_uncertainty_scale_mm": float(
                                scheduler_params.get(
                                    "uncertainty_scale_mm",
                                    scheduler_knobs["online_comsol_schedule_uncertainty_scale_mm"],
                                )
                            ),
                        })
                except Exception:
                    pass
            return {
                "maas_relax_ratio": float(maas_relax_ratio),
                "mcts_action_prior_weight": float(mcts_action_prior_weight),
                "mcts_cv_penalty_weight": float(mcts_cv_penalty_weight),
                "online_comsol_eval_budget": int(max(budget, 0)),
                **scheduler_knobs,
            }

        def _apply_runtime_knobs(next_knobs: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal maas_relax_ratio, mcts_action_prior_weight, mcts_cv_penalty_weight
            applied: Dict[str, Any] = {}
            if "maas_relax_ratio" in next_knobs:
                next_ratio = max(0.0, float(next_knobs["maas_relax_ratio"]))
                if abs(next_ratio - float(maas_relax_ratio)) > 1e-12:
                    applied["maas_relax_ratio"] = (float(maas_relax_ratio), float(next_ratio))
                    maas_relax_ratio = float(next_ratio)

            if "mcts_action_prior_weight" in next_knobs:
                next_prior = float(next_knobs["mcts_action_prior_weight"])
                if abs(next_prior - float(mcts_action_prior_weight)) > 1e-12:
                    applied["mcts_action_prior_weight"] = (
                        float(mcts_action_prior_weight),
                        float(next_prior),
                    )
                    mcts_action_prior_weight = float(next_prior)

            if "mcts_cv_penalty_weight" in next_knobs:
                next_cv_penalty = float(next_knobs["mcts_cv_penalty_weight"])
                if abs(next_cv_penalty - float(mcts_cv_penalty_weight)) > 1e-12:
                    applied["mcts_cv_penalty_weight"] = (
                        float(mcts_cv_penalty_weight),
                        float(next_cv_penalty),
                    )
                    mcts_cv_penalty_weight = float(next_cv_penalty)

            if (
                "online_comsol_eval_budget" in next_knobs and
                runtime_thermal_evaluator is not None and
                hasattr(runtime_thermal_evaluator, "set_eval_budget")
            ):
                try:
                    old_budget = int(runtime_thermal_evaluator.get_eval_budget())  # type: ignore[attr-defined]
                except Exception:
                    old_budget = int(opt_cfg.get("pymoo_maas_online_comsol_eval_budget", 0))
                new_budget = max(int(next_knobs["online_comsol_eval_budget"]), 0)
                if new_budget != old_budget:
                    runtime_thermal_evaluator.set_eval_budget(new_budget)  # type: ignore[attr-defined]
                    applied["online_comsol_eval_budget"] = (int(old_budget), int(new_budget))

            scheduler_key_map = {
                "online_comsol_schedule_mode": "mode",
                "online_comsol_schedule_top_fraction": "top_fraction",
                "online_comsol_schedule_min_observations": "min_observations",
                "online_comsol_schedule_warmup_calls": "warmup_calls",
                "online_comsol_schedule_explore_prob": "explore_prob",
                "online_comsol_schedule_uncertainty_weight": "uncertainty_weight",
                "online_comsol_schedule_uncertainty_scale_mm": "uncertainty_scale_mm",
            }
            scheduler_updates = {
                target_key: next_knobs[source_key]
                for source_key, target_key in scheduler_key_map.items()
                if source_key in next_knobs
            }
            if (
                scheduler_updates and
                runtime_thermal_evaluator is not None and
                hasattr(runtime_thermal_evaluator, "set_scheduler_params")
            ):
                try:
                    raw_changes = runtime_thermal_evaluator.set_scheduler_params(  # type: ignore[attr-defined]
                        **scheduler_updates
                    )
                    if isinstance(raw_changes, dict):
                        reverse_map = {v: k for k, v in scheduler_key_map.items()}
                        for changed_key, values in raw_changes.items():
                            knob_key = reverse_map.get(str(changed_key), str(changed_key))
                            if isinstance(values, (list, tuple)) and len(values) == 2:
                                applied[knob_key] = (values[0], values[1])
                except Exception:
                    pass

            return applied

        mcts_config = {
            "budget": int(min(mcts_budget, maas_max_attempts)),
            "max_depth": int(mcts_max_depth),
            "c_uct": float(mcts_c_uct),
            "stagnation_rounds": int(mcts_stagnation_rounds),
            "min_score_improvement": float(mcts_min_score_improvement),
            "min_cv_improvement": float(mcts_min_cv_improvement),
            "prune_margin": float(mcts_prune_margin),
            "action_prior_weight": float(mcts_action_prior_weight),
            "cv_penalty_weight": float(mcts_cv_penalty_weight),
        }
        mcts_report: Dict[str, Any] = {
            "enabled": bool(enable_mcts),
            "iterations": 0,
            "stop_reason": "disabled" if not enable_mcts else "not_run",
            "best_action": "",
            "best_score": None,
            "best_cv": None,
            "best_aocc_cv": None,
            "best_intent_id": "",
            "pruning_events": 0,
            "branch_stats": {},
            "action_stats": {},
            "config": dict(mcts_config),
            "records": [],
        }
        best_attempt_payload: Dict[str, Any] = {}

        def _log_maas_attempt_trace(
            payload: Dict[str, Any],
            *,
            is_best_attempt: bool = False,
            physics_audit_selected_reason: str = "",
        ) -> None:
            trace_payload = dict(payload or {})
            trace_payload["iteration"] = int(iteration)
            trace_payload["timestamp"] = datetime.now().isoformat()
            trace_payload["mcts_enabled"] = bool(enable_mcts)
            trace_payload["is_best_attempt"] = bool(is_best_attempt)
            trace_payload["physics_audit_selected_reason"] = str(physics_audit_selected_reason or "")
            trace_payload["pymoo_algorithm"] = str(pymoo_algorithm)
            host.logger.log_pymoo_maas_trace(trace_payload)

        def _attach_runtime_thermal_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
            patched = dict(payload or {})
            if runtime_thermal_evaluator is None:
                return patched
            try:
                stats_snapshot = dict(getattr(runtime_thermal_evaluator, "stats", {}) or {})
            except Exception:
                stats_snapshot = {}
            if not stats_snapshot:
                return patched

            executed_online_comsol = int(stats_snapshot.get("executed_online_comsol", 0) or 0)
            requests_total = int(stats_snapshot.get("requests_total", 0) or 0)
            patched["runtime_thermal_snapshot"] = {
                "executed_online_comsol": executed_online_comsol,
                "requests_total": requests_total,
            }
            patched["online_comsol_calls_so_far"] = executed_online_comsol
            host.logger.log_maas_physics_event(
                {
                    "iteration": int(iteration),
                    "attempt": int(patched.get("attempt", 0) or 0),
                    "event_type": "runtime_thermal_snapshot",
                    "simulation_backend": str(simulation_backend or ""),
                    "thermal_mode": str(thermal_evaluator_mode or ""),
                    "stats": {
                        "executed_online_comsol": int(executed_online_comsol),
                        "requests_total": int(requests_total),
                    },
                }
            )
            return patched

        def _save_attempt_layout_snapshot(payload: Dict[str, Any], decoded_state: Optional[DesignState]) -> None:
            stage = "attempt_candidate" if decoded_state is not None else "attempt_no_candidate"
            best_metrics = dict(payload.get("best_candidate_metrics", {}) or {})
            best_metrics["best_cv"] = payload.get("best_cv")
            best_metrics["aocc_cv"] = payload.get("aocc_cv")
            best_metrics["aocc_objective"] = payload.get("aocc_objective")
            thermal_source = "comsol" if str(thermal_evaluator_mode) == "online_comsol" else "proxy"
            snapshot_meta = _save_layout_snapshot(
                stage=stage,
                attempt=int(payload.get("attempt", 0) or 0),
                design_state=(decoded_state.model_copy(deep=True) if decoded_state is not None else current_state.model_copy(deep=True)),
                metrics=best_metrics,
                branch_action=str(payload.get("branch_action", "")),
                branch_source=str(payload.get("branch_source", "")),
                diagnosis_status=str(dict(payload.get("diagnosis", {}) or {}).get("status", "")),
                diagnosis_reason=str(dict(payload.get("diagnosis", {}) or {}).get("reason", "")),
                operator_program_id=str(payload.get("operator_program_id", "")),
                operator_actions=list(payload.get("operator_actions", []) or []),
                thermal_source=thermal_source,
                metadata={
                    "runtime_thermal_snapshot": dict(payload.get("runtime_thermal_snapshot", {}) or {}),
                    "constraint_violation_breakdown": dict(
                        payload.get("constraint_violation_breakdown", {}) or {}
                    ),
                    "dominant_violation": str(payload.get("dominant_violation", "")),
                },
            )
            payload["layout_state_hash"] = str(snapshot_meta.get("layout_state_hash", "") or "")
            payload["layout_duplicate_with_previous"] = bool(
                snapshot_meta.get("duplicate_with_previous_snapshot", False)
            )
            payload["layout_duplicate_occurrence_index"] = int(
                snapshot_meta.get("duplicate_occurrence_index", 0) or 0
            )
            payload["layout_snapshot_sequence"] = int(
                snapshot_meta.get("sequence", 0) or 0
            )

        def _inject_policy_context(raw_features: Dict[str, Any]) -> Dict[str, Any]:
            enriched = dict(raw_features or {})
            enriched["pymoo_algorithm"] = str(pymoo_algorithm)
            latest_payload = dict(maas_attempts[-1] or {}) if maas_attempts else {}
            search_space_mode = str(
                latest_payload.get(
                    "search_space_mode",
                    (last_compile_report or {}).get("search_space_mode", ""),
                )
                or ""
            ).strip().lower()
            if search_space_mode:
                enriched["search_space_mode"] = search_space_mode
            return enriched

        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "B",
                "status": "started",
                "details": {"step": "formulation_and_compile"},
            }
        )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "C",
                "status": "started",
                "details": {"step": "solver_execution"},
            }
        )

        if enable_mcts:
            planner = MaaSMCTSPlanner(
                max_depth=mcts_max_depth,
                budget=min(mcts_budget, maas_max_attempts),
                c_uct=mcts_c_uct,
                stagnation_rounds=mcts_stagnation_rounds,
                min_score_improvement=mcts_min_score_improvement,
                min_cv_improvement=mcts_min_cv_improvement,
                prune_margin=mcts_prune_margin,
                action_prior_weight=mcts_action_prior_weight,
                cv_penalty_weight=mcts_cv_penalty_weight,
            )
            evaluated_packs: List[Dict[str, Any]] = []
            attempt_counter = {"n": 0}

            def _propose(node: MCTSNode) -> List[MCTSVariant]:
                return host._propose_maas_mcts_variants(
                    node=node,
                    relax_ratio=maas_relax_ratio,
                )

            def _evaluate(node: MCTSNode, rollout: int) -> MCTSEvaluation:
                attempt_counter["n"] += 1
                attempt = int(attempt_counter["n"])
                eval_pack = host._evaluate_maas_intent_once(
                    iteration=iteration,
                    attempt=attempt,
                    intent=node.intent,
                    branch_action=node.action_from_parent,
                    branch_metadata=dict(node.metadata or {}),
                    current_state=current_state,
                    runtime_thermal_evaluator=runtime_thermal_evaluator,
                    pop_size=pop_size,
                    n_generations=n_generations,
                    seed=seed,
                    verbose=verbose,
                    return_least_infeasible=return_least_infeasible,
                    maas_relax_ratio=maas_relax_ratio,
                    thermal_evaluator_mode=thermal_evaluator_mode,
                )
                host.logger.save_maas_diagnostic_event(
                    iteration=iteration,
                    attempt=attempt,
                    payload=eval_pack["attempt_payload"],
                )
                attempt_payload = _attach_runtime_thermal_snapshot(eval_pack["attempt_payload"])
                eval_pack["attempt_payload"] = attempt_payload
                maas_attempts.append(attempt_payload)
                _log_maas_attempt_trace(attempt_payload)
                _save_attempt_layout_snapshot(attempt_payload, eval_pack.get("decoded_state"))
                evaluated_packs.append(eval_pack)

                if (
                    enable_meta_policy and
                    meta_policy_apply_runtime and
                    attempt >= meta_policy_min_attempts
                ):
                    runtime_thermal_stats_snapshot: Dict[str, Any] = {}
                    if runtime_thermal_evaluator is not None:
                        try:
                            runtime_thermal_stats_snapshot = dict(
                                getattr(runtime_thermal_evaluator, "stats", {}) or {}
                            )
                        except Exception:
                            runtime_thermal_stats_snapshot = {}

                    interim_features = extract_maas_trace_features(
                        maas_attempts,
                        runtime_thermal_stats=runtime_thermal_stats_snapshot,
                        physics_audit_report=None,
                        recent_window=trace_feature_window,
                    )
                    policy_pack = propose_meta_policy_actions(
                        trace_features=_inject_policy_context(interim_features),
                        current_knobs=_current_runtime_knobs(),
                        online_comsol_enabled=(thermal_evaluator_mode == "online_comsol"),
                    )
                    applied_knobs = _apply_runtime_knobs(policy_pack.get("next_knobs", {}))
                    planner_weights = planner.get_policy_weights()
                    if (
                        "mcts_action_prior_weight" in applied_knobs or
                        "mcts_cv_penalty_weight" in applied_knobs
                    ):
                        planner_weights = planner.update_policy_weights(
                            action_prior_weight=float(mcts_action_prior_weight),
                            cv_penalty_weight=float(mcts_cv_penalty_weight),
                        )
                        mcts_config["action_prior_weight"] = float(mcts_action_prior_weight)
                        mcts_config["cv_penalty_weight"] = float(mcts_cv_penalty_weight)

                    event = {
                        "trigger_attempt": int(attempt),
                        "trigger_rollout": int(rollout),
                        "mode": "mcts_runtime",
                        "applied": bool(applied_knobs),
                        "actions": list(policy_pack.get("actions", [])),
                        "applied_knobs": applied_knobs,
                        "planner_policy_weights": dict(planner_weights),
                    }
                    meta_policy_events.append(event)
                    host.logger.log_maas_policy_event(
                        {
                            "iteration": int(iteration),
                            "attempt": int(attempt),
                            "mode": str(event.get("mode", "")),
                            "applied": bool(event.get("applied", False)),
                            "actions": list(event.get("actions", []) or []),
                            "applied_knobs": dict(event.get("applied_knobs", {}) or {}),
                            "planner_policy_weights": dict(
                                event.get("planner_policy_weights", {}) or {}
                            ),
                            "metadata": {
                                "trigger_rollout": int(rollout),
                            },
                        }
                    )
                    if applied_knobs:
                        host.logger.logger.info(
                            "Meta policy runtime update at attempt=%d rollout=%d: %s",
                            int(attempt),
                            int(rollout),
                            applied_knobs,
                        )

                eval_payload = dict(eval_pack)
                eval_payload["best_cv"] = attempt_payload.get("best_cv")
                eval_payload["aocc_cv"] = attempt_payload.get("aocc_cv")
                eval_payload["dominant_violation"] = str(
                    attempt_payload.get("dominant_violation", "")
                )
                eval_payload["constraint_violation_breakdown"] = dict(
                    attempt_payload.get("constraint_violation_breakdown", {}) or {}
                )
                eval_payload["best_candidate_metrics"] = dict(
                    attempt_payload.get("best_candidate_metrics", {}) or {}
                )
                eval_payload["attempt_payload"] = dict(attempt_payload)
                return MCTSEvaluation(
                    score=float(eval_pack["score"]),
                    payload=eval_payload,
                )

            mcts_result = planner.search(
                root_intent=active_intent,
                propose_variants=_propose,
                evaluate_node=_evaluate,
            )
            mcts_report = {
                "enabled": True,
                "iterations": int(mcts_result.iterations),
                "stop_reason": str(mcts_result.stop_reason),
                "best_score": mcts_result.best_score,
                "best_cv": mcts_result.best_cv,
                "pruning_events": int(mcts_result.pruning_events),
                "branch_stats": dict(mcts_result.branch_stats),
                "action_stats": dict(mcts_result.action_stats),
                "records": list(mcts_result.records),
                "best_action": "",
                "best_aocc_cv": None,
                "best_intent_id": "",
                "config": dict(mcts_config),
            }

            best_pack: Optional[Dict[str, Any]] = None
            if (
                mcts_result.best_node is not None and
                mcts_result.best_node.evaluation is not None
            ):
                best_pack = mcts_result.best_node.evaluation.payload
                mcts_report["best_action"] = mcts_result.best_node.action_from_parent
                mcts_report["best_score"] = float(mcts_result.best_node.evaluation.score)
            elif evaluated_packs:
                best_pack = max(evaluated_packs, key=lambda item: float(item.get("score", float("-inf"))))
                mcts_report["best_action"] = str(best_pack["attempt_payload"].get("branch_action", "unknown"))
                mcts_report["best_score"] = float(best_pack.get("score", 0.0))
            if best_pack is not None:
                attempt_payload = dict(best_pack.get("attempt_payload", {}) or {})
                best_attempt_payload = dict(attempt_payload)
                mcts_report["best_cv"] = attempt_payload.get("best_cv")
                mcts_report["best_aocc_cv"] = attempt_payload.get("aocc_cv")
                intent_obj = best_pack.get("intent")
                mcts_report["best_intent_id"] = str(
                    getattr(intent_obj, "intent_id", attempt_payload.get("intent_id", ""))
                )

            total_solver_cost = float(sum(float(item.get("solver_cost", 0.0)) for item in evaluated_packs))
            if best_pack is not None:
                active_intent = best_pack["intent"]
                candidate_state = best_pack["decoded_state"]
                last_formulation_report = dict(best_pack["formulation_report"])
                last_compile_report = dict(best_pack["compile_report"])
                last_execution_result = best_pack["execution_result"]
                last_solver_exception = best_pack["solver_exception"]
                last_diagnosis = dict(best_pack["diagnosis"])
                last_relaxation_suggestions = list(best_pack["relaxation_suggestions"])
                last_problem_generator = best_pack["problem_generator"]
        else:
            for attempt in range(1, maas_max_attempts + 1):
                host.logger.logger.info(
                    f"MaaS solve attempt {attempt}/{maas_max_attempts} (intent_id={active_intent.intent_id})"
                )
                eval_pack = host._evaluate_maas_intent_once(
                    iteration=iteration,
                    attempt=attempt,
                    intent=active_intent,
                    branch_action=f"retry_attempt_{attempt}",
                    branch_metadata={"source": "retry"},
                    current_state=current_state,
                    runtime_thermal_evaluator=runtime_thermal_evaluator,
                    pop_size=pop_size,
                    n_generations=n_generations,
                    seed=seed,
                    verbose=verbose,
                    return_least_infeasible=return_least_infeasible,
                    maas_relax_ratio=maas_relax_ratio,
                    thermal_evaluator_mode=thermal_evaluator_mode,
                )
                host.logger.save_maas_diagnostic_event(
                    iteration=iteration,
                    attempt=attempt,
                    payload=eval_pack["attempt_payload"],
                )
                attempt_payload = _attach_runtime_thermal_snapshot(eval_pack["attempt_payload"])
                eval_pack["attempt_payload"] = attempt_payload
                maas_attempts.append(attempt_payload)
                best_attempt_payload = dict(attempt_payload)
                _log_maas_attempt_trace(attempt_payload)
                _save_attempt_layout_snapshot(attempt_payload, eval_pack.get("decoded_state"))

                total_solver_cost += float(eval_pack["solver_cost"])
                last_formulation_report = dict(eval_pack["formulation_report"])
                last_compile_report = dict(eval_pack["compile_report"])
                last_execution_result = eval_pack["execution_result"]
                last_solver_exception = eval_pack["solver_exception"]
                last_diagnosis = dict(eval_pack["diagnosis"])
                last_relaxation_suggestions = list(eval_pack["relaxation_suggestions"])
                last_problem_generator = eval_pack["problem_generator"]
                if eval_pack["decoded_state"] is not None:
                    candidate_state = eval_pack["decoded_state"]

                should_retry = (
                    maas_auto_relax and
                    attempt < maas_max_attempts and
                    host._is_maas_retryable(last_diagnosis, retry_on_stall=maas_retry_on_stall)
                )
                if not should_retry:
                    break

                if (
                    enable_meta_policy and
                    meta_policy_apply_runtime and
                    attempt >= meta_policy_min_attempts
                ):
                    runtime_thermal_stats_snapshot: Dict[str, Any] = {}
                    if runtime_thermal_evaluator is not None:
                        try:
                            runtime_thermal_stats_snapshot = dict(
                                getattr(runtime_thermal_evaluator, "stats", {}) or {}
                            )
                        except Exception:
                            runtime_thermal_stats_snapshot = {}

                    interim_features = extract_maas_trace_features(
                        maas_attempts,
                        runtime_thermal_stats=runtime_thermal_stats_snapshot,
                        physics_audit_report=None,
                        recent_window=trace_feature_window,
                    )
                    policy_pack = propose_meta_policy_actions(
                        trace_features=_inject_policy_context(interim_features),
                        current_knobs=_current_runtime_knobs(),
                        online_comsol_enabled=(thermal_evaluator_mode == "online_comsol"),
                    )
                    applied_knobs = _apply_runtime_knobs(policy_pack.get("next_knobs", {}))
                    event = {
                        "trigger_attempt": int(attempt),
                        "applied": bool(applied_knobs),
                        "actions": list(policy_pack.get("actions", [])),
                        "applied_knobs": applied_knobs,
                    }
                    meta_policy_events.append(event)
                    host.logger.log_maas_policy_event(
                        {
                            "iteration": int(iteration),
                            "attempt": int(attempt),
                            "mode": "retry_runtime",
                            "applied": bool(event.get("applied", False)),
                            "actions": list(event.get("actions", []) or []),
                            "applied_knobs": dict(event.get("applied_knobs", {}) or {}),
                        }
                    )
                    if applied_knobs:
                        host.logger.logger.info(
                            "Meta policy runtime update at attempt=%d: %s",
                            int(attempt),
                            applied_knobs,
                        )

                next_intent, applied_count = host._apply_relaxation_suggestions_to_intent(
                    intent=active_intent,
                    suggestions=last_relaxation_suggestions,
                    attempt=attempt + 1,
                )
                maas_attempts[-1]["relaxation_applied_count"] = int(applied_count)
                if int(best_attempt_payload.get("attempt", -1)) == int(attempt):
                    best_attempt_payload["relaxation_applied_count"] = int(applied_count)
                if applied_count > 0:
                    host.logger.logger.warning(
                        f"MaaS attempt {attempt}: applying {applied_count} relaxation(s) and retrying."
                    )
                    active_intent = next_intent
                else:
                    host.logger.logger.info(
                        f"MaaS attempt {attempt}: no applicable relaxation, stopping retry loop."
                    )
                    break

        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "B",
                "status": "completed",
                "details": {
                    "has_formulation": bool(last_formulation_report),
                    "has_compile_report": bool(last_compile_report),
                },
            }
        )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "C",
                "status": "completed",
                "details": {
                    "solver_success": bool(last_execution_result is not None and last_execution_result.success),
                    "attempt_count": int(len(maas_attempts)),
                    "solver_message": (
                        str(last_execution_result.message)
                        if last_execution_result is not None
                        else str(last_solver_exception or "")
                    ),
                },
            }
        )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "D",
                "status": "started",
                "details": {"step": "reflection_and_audit"},
            }
        )

        physics_audit_report: Dict[str, Any] = {
            "enabled": bool(enable_physics_audit),
            "simulation_backend": simulation_backend,
            "selected_reason": "disabled" if not enable_physics_audit else "not_run",
            "requested_top_k": int(audit_top_k),
            "records": [],
        }
        if enable_physics_audit and simulation_backend != "comsol":
            physics_audit_report["selected_reason"] = f"skipped_non_comsol_backend:{simulation_backend or 'unknown'}"
        elif enable_physics_audit and last_execution_result is not None and bool(last_execution_result.success):
            physics_audit_report, audited_state = host._run_maas_topk_physics_audit(
                execution_result=last_execution_result,
                problem_generator=last_problem_generator,
                base_state=current_state,
                top_k=audit_top_k,
                base_iteration=iteration,
            )
            host.logger.save_maas_diagnostic_event(
                iteration=iteration,
                attempt=len(maas_attempts) + 1,
                payload={"physics_audit": physics_audit_report},
            )
            if audited_state is not None:
                candidate_state = audited_state
            elif enforce_audit_feasible and str(physics_audit_report.get("selected_reason", "")) == "no_feasible_after_audit":
                candidate_state = None
                last_diagnosis = {
                    "status": "no_feasible",
                    "reason": "audit_no_feasible_candidate",
                    "aocc_cv": float(last_diagnosis.get("aocc_cv", 0.0) or 0.0),
                    "best_cv": float(last_diagnosis.get("best_cv", float("inf"))),
                }
                host.logger.logger.warning(
                    "MaaS physics audit found no feasible candidate. "
                    "Fallback to pre-optimization state due to enforce_audit_feasible=true."
                )

        host.logger.log_maas_physics_event(
            {
                "iteration": int(iteration),
                "attempt": int(len(maas_attempts)),
                "event_type": "physics_audit",
                "simulation_backend": str(simulation_backend or ""),
                "thermal_mode": str(thermal_evaluator_mode or ""),
                "selected_reason": str(physics_audit_report.get("selected_reason", "")),
                "records_count": int(len(list(physics_audit_report.get("records", []) or []))),
                "stats": {
                    "enabled": bool(physics_audit_report.get("enabled", False)),
                    "requested_top_k": int(physics_audit_report.get("requested_top_k", 0) or 0),
                },
            }
        )

        if best_attempt_payload:
            _log_maas_attempt_trace(
                best_attempt_payload,
                is_best_attempt=True,
                physics_audit_selected_reason=str(physics_audit_report.get("selected_reason", "")),
            )

        final_state = candidate_state or current_state
        if candidate_state is not None:
            try:
                final_metrics, final_violations = host._evaluate_design(final_state, iteration + 1)
            except Exception as exc:
                host.logger.logger.warning(
                    f"Final candidate evaluation failed, keep pre-solver metrics fallback: {exc}"
                )
                final_metrics, final_violations = current_metrics, violations
        else:
            final_metrics, final_violations = current_metrics, violations

        final_mph_path = ""
        if simulation_backend == "comsol":
            force_save_fn = getattr(host.sim_driver, "force_save_current_model", None)
            if callable(force_save_fn):
                try:
                    final_mph_path = str(
                        force_save_fn(
                            final_state.model_copy(deep=True),
                            host.logger.run_dir,
                            "final_selected",
                        )
                        or ""
                    ).strip()
                except Exception as exc:
                    host.logger.logger.warning("Final .mph save failed: %s", exc)
            if not final_mph_path:
                final_mph_path = str(
                    getattr(host.sim_driver, "last_saved_mph_path", "") or ""
                ).strip()

        _save_layout_snapshot(
            stage="final_selected",
            attempt=int(len(maas_attempts)),
            design_state=final_state.model_copy(deep=True),
            metrics=_metrics_from_runtime_bundle(final_metrics),
            diagnosis_status=str(last_diagnosis.get("status", "")),
            diagnosis_reason=str(last_diagnosis.get("reason", "")),
            thermal_source=("comsol" if str(thermal_evaluator_mode) == "online_comsol" else "proxy"),
            metadata={
                "physics_audit_selected_reason": str(physics_audit_report.get("selected_reason", "")),
                "final_mph_path": final_mph_path,
                "is_success": bool(
                    last_diagnosis.get("status") in {"feasible", "feasible_but_stalled"}
                    and len(final_violations) == 0
                ),
            },
        )

        runtime_thermal_evaluator_stats: Dict[str, Any] = {}
        if runtime_thermal_evaluator is not None:
            try:
                runtime_thermal_evaluator_stats = dict(
                    getattr(runtime_thermal_evaluator, "stats", {}) or {}
                )
            except Exception:
                runtime_thermal_evaluator_stats = {}

        maas_trace_features = extract_maas_trace_features(
            maas_attempts,
            runtime_thermal_stats=runtime_thermal_evaluator_stats,
            physics_audit_report=physics_audit_report,
            recent_window=trace_feature_window,
        )

        def _as_finite_float(value: Any) -> Optional[float]:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if not np.isfinite(parsed):
                return None
            return float(parsed)

        resolved_best_cv_min = _as_finite_float(maas_trace_features.get("best_cv_min"))
        best_cv_min_source = "trace_features"

        if resolved_best_cv_min is None:
            if last_execution_result is not None:
                curve = np.asarray(getattr(last_execution_result, "best_cv_curve", []), dtype=float).reshape(-1)
                finite_curve = curve[np.isfinite(curve)]
                if finite_curve.size > 0:
                    resolved_best_cv_min = float(np.min(finite_curve))
                    best_cv_min_source = "execution_best_cv_curve"

            if resolved_best_cv_min is None:
                resolved_best_cv_min = _as_finite_float(last_diagnosis.get("best_cv"))
                if resolved_best_cv_min is not None:
                    best_cv_min_source = "solver_diagnosis_best_cv"

            if resolved_best_cv_min is None:
                attempt_best_cv_values: List[float] = []
                for payload in maas_attempts:
                    payload_best_cv = _as_finite_float(payload.get("best_cv"))
                    if payload_best_cv is not None:
                        attempt_best_cv_values.append(float(payload_best_cv))
                        continue
                    diagnosis_best_cv = _as_finite_float(
                        dict(payload.get("diagnosis") or {}).get("best_cv")
                    )
                    if diagnosis_best_cv is not None:
                        attempt_best_cv_values.append(float(diagnosis_best_cv))
                if attempt_best_cv_values:
                    resolved_best_cv_min = float(np.min(np.asarray(attempt_best_cv_values, dtype=float)))
                    best_cv_min_source = "attempt_payload_best_cv"

            if resolved_best_cv_min is None and str(last_diagnosis.get("status", "")) in {"feasible", "feasible_but_stalled"}:
                resolved_best_cv_min = 0.0
                best_cv_min_source = "feasible_inferred_zero"

            if resolved_best_cv_min is not None:
                maas_trace_features["best_cv_min"] = float(resolved_best_cv_min)
            else:
                best_cv_min_source = "missing"

        maas_trace_features["best_cv_min_source"] = str(best_cv_min_source)
        maas_trace_features = _inject_policy_context(maas_trace_features)
        if enable_meta_policy:
            meta_policy_report["events"] = meta_policy_events
            meta_policy_report["event_count"] = int(len(meta_policy_events))
            meta_policy_report["applied_event_count"] = int(
                sum(1 for item in meta_policy_events if bool(item.get("applied", False)))
            )
            meta_policy_report["next_run_recommendation"] = propose_meta_policy_actions(
                trace_features=_inject_policy_context(maas_trace_features),
                current_knobs=_current_runtime_knobs(),
                online_comsol_enabled=(thermal_evaluator_mode == "online_comsol"),
            )

        final_state.metadata["optimization_mode"] = host.optimization_mode
        final_state.metadata["pymoo_algorithm"] = str(pymoo_algorithm)
        final_state.metadata["modeling_intent_initial"] = modeling_intent.model_dump()
        final_state.metadata["modeling_intent_final"] = active_intent.model_dump()
        final_state.metadata["modeling_validation"] = validation
        final_state.metadata["formulation_report"] = last_formulation_report
        final_state.metadata["compile_report"] = last_compile_report
        final_state.metadata["thermal_evaluator_mode"] = thermal_evaluator_mode
        final_state.metadata["enforce_audit_feasible"] = bool(enforce_audit_feasible)
        final_state.metadata["solver_diagnosis"] = last_diagnosis
        final_state.metadata["relaxation_suggestions"] = last_relaxation_suggestions
        final_state.metadata["maas_attempts"] = maas_attempts
        final_state.metadata["layout_unique_state_count"] = int(
            len(
                {
                    str(item.get("layout_state_hash", "") or "").strip()
                    for item in maas_attempts
                    if str(item.get("layout_state_hash", "") or "").strip()
                }
            )
        )
        final_state.metadata["layout_duplicate_attempts"] = int(
            sum(
                1
                for item in maas_attempts
                if bool(item.get("layout_duplicate_with_previous", False))
            )
        )
        final_state.metadata["mcts_report"] = mcts_report
        final_state.metadata["physics_audit"] = physics_audit_report
        final_state.metadata["runtime_thermal_evaluator_stats"] = runtime_thermal_evaluator_stats
        final_state.metadata["maas_trace_features"] = maas_trace_features
        final_state.metadata["meta_policy_report"] = meta_policy_report
        final_state.metadata["final_mph_path"] = str(final_mph_path or "")
        final_state.metadata["phase_a_completed"] = True
        final_state.metadata["phase_b_completed"] = bool(last_formulation_report)
        final_state.metadata["phase_c_completed"] = last_execution_result is not None
        final_state.metadata["phase_d_completed"] = True
        final_state.metadata["max_iterations_requested"] = int(max_iterations)
        final_state.metadata["convergence_threshold_requested"] = float(convergence_threshold)
        final_state.metadata["maas_attempt_count"] = len(maas_attempts)

        if last_execution_result is not None:
            final_state.metadata["pymoo_execution"] = {
                "success": bool(last_execution_result.success),
                "message": last_execution_result.message,
                "traceback_text": last_execution_result.traceback_text,
                "n_gen_completed": int(last_execution_result.n_gen_completed),
                "aocc_cv": float(last_execution_result.aocc_cv),
                "aocc_objective": float(last_execution_result.aocc_objective),
                "best_cv_curve": [float(v) for v in last_execution_result.best_cv_curve],
                "best_feasible_objective_curve": [
                    float(v) for v in last_execution_result.best_feasible_objective_curve
                ],
                "pareto_shape": (
                    list(np.asarray(last_execution_result.pareto_X).shape)
                    if last_execution_result.pareto_X is not None
                    else [0, 0]
                ),
                "metadata": last_execution_result.metadata,
                "solver_cost": float(total_solver_cost),
            }
            if getattr(last_execution_result, "pareto_F", None) is not None:
                pareto_f = np.asarray(last_execution_result.pareto_F, dtype=float)
                if pareto_f.size > 0:
                    final_state.metadata["pymoo_execution"]["pareto_front_preview"] = (
                        pareto_f[: min(len(pareto_f), 5)].tolist()
                    )
            if getattr(last_execution_result, "pareto_CV", None) is not None:
                pareto_cv = np.asarray(last_execution_result.pareto_CV, dtype=float).reshape(-1)
                if pareto_cv.size > 0:
                    final_state.metadata["pymoo_execution"]["pareto_cv_preview"] = (
                        pareto_cv[: min(pareto_cv.size, 10)].tolist()
                    )
        elif last_solver_exception:
            final_state.metadata["pymoo_execution"] = {
                "success": False,
                "message": last_solver_exception,
                "solver_cost": float(total_solver_cost),
            }

        penalty_breakdown = host._calculate_penalty_breakdown(final_metrics, final_violations)
        host.logger.log_metrics({
            "iteration": iteration,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "max_temp": float(final_metrics["thermal"].max_temp),
            "avg_temp": float(final_metrics["thermal"].avg_temp),
            "min_temp": float(final_metrics["thermal"].min_temp),
            "temp_gradient": float(final_metrics["thermal"].temp_gradient),
            "min_clearance": float(final_metrics["geometry"].min_clearance),
            "cg_offset": float(final_metrics["geometry"].cg_offset_magnitude),
            "num_collisions": int(final_metrics["geometry"].num_collisions),
            "total_mass": sum(c.mass for c in final_state.components),
            "total_power": float(final_metrics["power"].total_power),
            "num_violations": len(final_violations),
            "is_safe": len(final_violations) == 0,
            "solver_cost": float(final_metrics.get("diagnostics", {}).get("solver_cost", 0.0)) + float(total_solver_cost),
            "llm_tokens": 0,
            "penalty_score": float(penalty_breakdown["total"]),
            "penalty_violation": float(penalty_breakdown["violation"]),
            "penalty_temp": float(penalty_breakdown["temp"]),
            "penalty_clearance": float(penalty_breakdown["clearance"]),
            "penalty_cg": float(penalty_breakdown["cg"]),
            "penalty_collision": float(penalty_breakdown["collision"]),
            "delta_penalty": 0.0,
            "delta_cg_offset": 0.0,
            "delta_max_temp": 0.0,
            "delta_min_clearance": 0.0,
            "effectiveness_score": 0.0,
            "state_id": final_state.state_id,
        })

        host.logger.save_trace_data(
            iteration=iteration,
            strategic_plan={
                "modeling_intent_initial": modeling_intent.model_dump(),
                "modeling_intent_final": active_intent.model_dump(),
                "validation": validation,
                "solver_diagnosis": last_diagnosis,
                "relaxation_suggestions": last_relaxation_suggestions,
                "maas_attempts": maas_attempts,
                "mcts_report": mcts_report,
                "physics_audit": physics_audit_report,
                "runtime_thermal_evaluator_stats": runtime_thermal_evaluator_stats,
                "maas_trace_features": maas_trace_features,
                "meta_policy_report": meta_policy_report,
            },
        )

        host.logger.logger.info(
            "MaaS trace features: feasible_rate=%s, best_cv_min=%s, comsol_per_feasible=%s, physics_pass_rate=%s",
            maas_trace_features.get("feasible_rate"),
            maas_trace_features.get("best_cv_min"),
            (maas_trace_features.get("runtime_thermal", {}) or {}).get("comsol_calls_per_feasible_attempt"),
            (maas_trace_features.get("physics_audit", {}) or {}).get("physics_pass_rate_topk"),
        )

        host.logger.save_design_state(iteration, final_state.model_dump())
        is_success = (
            last_diagnosis.get("status") in {"feasible", "feasible_but_stalled"} and
            len(final_violations) == 0
        )
        summary_status = "SUCCESS" if is_success else "PARTIAL_SUCCESS"
        summary_attempt_payload: Dict[str, Any] = {}
        if best_attempt_payload:
            summary_attempt_payload = dict(best_attempt_payload)
        elif maas_attempts:
            summary_attempt_payload = dict(maas_attempts[-1] or {})
        summary_search_space = str(
            summary_attempt_payload.get(
                "search_space_mode",
                (last_compile_report or {}).get("search_space_mode", ""),
            )
            or ""
        )
        summary_layout_hashes = sorted(
            {
                str(item.get("layout_state_hash", "") or "").strip()
                for item in maas_attempts
                if str(item.get("layout_state_hash", "") or "").strip()
            }
        )
        summary_layout_duplicate_attempts = int(
            sum(
                1
                for item in maas_attempts
                if bool(item.get("layout_duplicate_with_previous", False))
            )
        )
        host.logger.save_summary(
            status=summary_status,
            final_iteration=iteration,
            notes=(
                "pymoo_maas pipeline completed. "
                f"attempts={len(maas_attempts)}, "
                f"diagnosis={last_diagnosis.get('status')}, "
                f"reason={last_diagnosis.get('reason')}, "
                f"solver_message={last_execution_result.message if last_execution_result else last_solver_exception}"
            ),
            extra={
                "optimization_mode": "pymoo_maas",
                "pymoo_algorithm": str(pymoo_algorithm),
                "thermal_evaluator_mode": thermal_evaluator_mode,
                "diagnosis_status": last_diagnosis.get("status"),
                "diagnosis_reason": last_diagnosis.get("reason"),
                "maas_attempt_count": len(maas_attempts),
                "search_space": summary_search_space or None,
                "dominant_violation": summary_attempt_payload.get("dominant_violation"),
                "constraint_violation_breakdown": summary_attempt_payload.get("constraint_violation_breakdown"),
                "best_candidate_metrics": summary_attempt_payload.get("best_candidate_metrics"),
                "operator_bias": summary_attempt_payload.get("operator_bias"),
                "operator_credit_snapshot": summary_attempt_payload.get("operator_credit_snapshot"),
                "layout_state_hash": summary_attempt_payload.get("layout_state_hash"),
                "layout_duplicate_with_previous": summary_attempt_payload.get("layout_duplicate_with_previous"),
                "layout_duplicate_occurrence_index": summary_attempt_payload.get("layout_duplicate_occurrence_index"),
                "layout_unique_state_count": int(len(summary_layout_hashes)),
                "layout_duplicate_attempts": int(summary_layout_duplicate_attempts),
                "feasible_rate": maas_trace_features.get("feasible_rate"),
                "best_cv_min": maas_trace_features.get("best_cv_min"),
                "best_cv_min_source": maas_trace_features.get("best_cv_min_source"),
                "first_feasible_eval": maas_trace_features.get("first_feasible_eval"),
                "comsol_calls_to_first_feasible": maas_trace_features.get("comsol_calls_to_first_feasible"),
                "comsol_calls_per_feasible_attempt": (
                    maas_trace_features.get("runtime_thermal", {}) or {}
                ).get("comsol_calls_per_feasible_attempt"),
                "physics_pass_rate_topk": (
                    maas_trace_features.get("physics_audit", {}) or {}
                ).get("physics_pass_rate_topk"),
                "meta_policy_runtime_events": len(meta_policy_events),
                "meta_policy_runtime_applied_events": int(
                    sum(1 for item in meta_policy_events if bool(item.get("applied", False)))
                ),
                "meta_policy_next_run_actions": len(
                    list(
                        (meta_policy_report.get("next_run_recommendation", {}) or {}).get("actions", [])
                    )
                ),
                "final_mph_path": str(final_mph_path or ""),
            },
        )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(iteration),
                "phase": "D",
                "status": "completed",
                "details": {
                    "diagnosis_status": str(last_diagnosis.get("status", "")),
                    "diagnosis_reason": str(last_diagnosis.get("reason", "")),
                    "physics_audit_selected_reason": str(
                        physics_audit_report.get("selected_reason", "")
                    ),
                },
            }
        )
        observability_tables: Dict[str, Any] = {}
        observability_tables_error = ""
        try:
            observability_tables = materialize_observability_tables(host.logger.run_dir)
        except Exception as exc:
            observability_tables_error = str(exc)
            host.logger.logger.warning(
                "Observability table materialization failed: %s", exc
            )

        final_state.metadata["observability_tables"] = observability_tables
        if observability_tables_error:
            final_state.metadata["observability_tables_error"] = observability_tables_error

        summary_path = Path(host.logger.run_dir) / "summary.json"
        if summary_path.exists():
            try:
                summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_payload["observability_tables"] = observability_tables
                if observability_tables_error:
                    summary_payload["observability_tables_error"] = observability_tables_error
                summary_path.write_text(
                    json.dumps(summary_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                host.logger.logger.debug(
                    "summary observability update failed: %s", exc
                )

        host.logger.save_run_manifest(
            {
                "optimization_mode": "pymoo_maas",
                "pymoo_algorithm": str(pymoo_algorithm),
                "thermal_evaluator_mode": str(thermal_evaluator_mode),
                "search_space_mode": summary_search_space or "",
                "status": str(summary_status),
                "final_iteration": int(iteration),
                "extra": {
                    "diagnosis_status": str(last_diagnosis.get("status", "")),
                    "diagnosis_reason": str(last_diagnosis.get("reason", "")),
                    "attempt_count": int(len(maas_attempts)),
                    "final_mph_path": str(final_mph_path or ""),
                    "observability_tables": observability_tables,
                    "observability_tables_error": observability_tables_error,
                },
            }
        )
        host._generate_final_report(final_state, iteration)
        host.logger.logger.info(
            "pymoo_maas pipeline completed. "
            f"attempts={len(maas_attempts)}, "
            f"diagnosis={last_diagnosis.get('status')}"
        )
        return final_state
