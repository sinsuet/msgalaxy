"""
MaaS pipeline service.

Extracts the mass A/B/C/D orchestration flow out of WorkflowOrchestrator
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
from optimization.knowledge.mass import MassEvidence
from optimization.modes.mass.maas_mcts import (
    MCTSEvaluation,
    MCTSNode,
    MCTSVariant,
    MaaSMCTSPlanner,
)
from optimization.modes.mass.meta_policy import propose_meta_policy_actions
from optimization.modes.mass.modeling_validator import validate_modeling_intent
from optimization.modes.mass.observability.materialize import materialize_observability_tables
from optimization.modes.mass.operator_physics_matrix import (
    evaluate_operator_family_coverage,
    evaluate_operator_realization,
    parse_required_families,
)
from optimization.protocol import ModelingIntent, PowerMetrics, StructuralMetrics, ThermalMetrics
from optimization.modes.mass.trace_features import extract_maas_trace_features

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


class MaaSPipelineService:
    """Encapsulates mass closed-loop execution."""

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
        mass 模式：
        A. Understanding: 建模意图生成与校验
        B. Formulation: 约束标准化
        C. Coding/Execution: 编译并运行 NSGA-II
        D. Reflection: 诊断失败并自动松弛后重求解
        """
        host = self.host
        runtime = getattr(host, "runtime_facade", None)
        if runtime is None:
            raise RuntimeError("runtime_facade is not configured")
        runtime_mode = str(getattr(host, "optimization_mode", "mass") or "mass")

        host.logger.logger.info(
            f"Entering {runtime_mode} pipeline: "
            "Understanding -> Formulation -> Coding -> Reflection"
        )
        host.logger.save_run_manifest(
            {
                "optimization_mode": runtime_mode,
                "pymoo_algorithm": str(runtime.resolve_pymoo_algorithm()),
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
            current_metrics, violations = runtime.evaluate_design(current_state, iteration)
        except Exception as exc:
            host.logger.logger.warning(
                f"Phase A physics evaluation failed, fallback to geometry-only context: {exc}"
            )
            geometry_metrics = runtime.evaluate_geometry(current_state)
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
            violations = runtime.check_violations(
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

        def _serialize_retrieved_knowledge(items: Any) -> List[Dict[str, Any]]:
            serialized: List[Dict[str, Any]] = []
            for item in list(items or []):
                category = str(getattr(item, "category", "") or "")
                title = str(getattr(item, "title", "") or "")
                item_id = str(getattr(item, "item_id", "") or "")
                citation = ""
                to_citation = getattr(item, "to_citation", None)
                if callable(to_citation):
                    try:
                        citation = str(to_citation() or "")
                    except Exception:
                        citation = ""
                relevance_score = 0.0
                try:
                    relevance_score = float(getattr(item, "relevance_score", 0.0) or 0.0)
                except Exception:
                    relevance_score = 0.0
                serialized.append(
                    {
                        "item_id": item_id,
                        "category": category,
                        "title": title,
                        "citation": citation,
                        "relevance_score": relevance_score,
                    }
                )
            return serialized

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
                "delta": dict(snapshot_result.get("delta", {}) or {}),
                "layout_event": dict(snapshot_result.get("event", {}) or {}),
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

        context = runtime.build_global_context(
            iteration=iteration,
            design_state=current_state,
            metrics=current_metrics,
            violations=violations,
            phase="A",
        )
        phase_a_retrieval = _serialize_retrieved_knowledge(
            getattr(context, "retrieved_knowledge", []) or []
        )
        host.logger.save_trace_data(
            iteration=iteration,
            context_pack=context.model_dump() if hasattr(context, "model_dump") else context.__dict__,
        )

        requirement_text = runtime.build_maas_requirement_text(bom_file)
        intent_modeler = getattr(host, "intent_modeler", None)
        if intent_modeler is None or not hasattr(intent_modeler, "generate_modeling_intent"):
            raise RuntimeError("intent_modeler controller is not configured")
        modeling_intent: ModelingIntent = intent_modeler.generate_modeling_intent(
            context=context,
            runtime_constraints=host.runtime_constraints,
            requirement_text=requirement_text,
        )
        modeling_intent_diagnostics: Dict[str, Any] = {}
        diagnostics_getter = getattr(intent_modeler, "get_modeling_intent_diagnostics", None)
        if callable(diagnostics_getter):
            try:
                modeling_intent_diagnostics = dict(diagnostics_getter() or {})
            except Exception:
                modeling_intent_diagnostics = {}
        intent_notes = str(getattr(modeling_intent, "notes", "") or "").strip().lower()
        if str(modeling_intent_diagnostics.get("source", "") or "").strip().lower() in {
            "",
            "unknown",
            "not_called",
            "pre_call",
        }:
            if "deterministic" in intent_notes:
                modeling_intent_diagnostics["source"] = "deterministic_patch"
            elif "fallback_modeling_intent" in intent_notes:
                modeling_intent_diagnostics["source"] = "fallback_modeling_intent"
        if "source" not in modeling_intent_diagnostics:
            modeling_intent_diagnostics["source"] = "unknown"
        modeling_intent_diagnostics["intent_id"] = str(
            getattr(modeling_intent, "intent_id", "") or ""
        )
        modeling_intent_diagnostics["intent_notes"] = str(getattr(modeling_intent, "notes", "") or "")

        validation_opt_cfg = host.config.get("optimization", {})
        validation = validate_modeling_intent(
            modeling_intent,
            host.runtime_constraints,
            mandatory_hard_constraint_groups=validation_opt_cfg.get(
                "mass_mandatory_hard_constraints",
                ["collision", "clearance", "boundary", "thermal", "cg_limit"],
            ),
            hard_constraint_coverage_mode=str(
                validation_opt_cfg.get("mass_hard_constraint_coverage_mode", "warn")
            ),
            metric_registry_mode=str(
                validation_opt_cfg.get("mass_metric_registry_mode", "warn")
            ),
        )

        if validation.get("warnings"):
            for warning in validation["warnings"]:
                host.logger.logger.warning(f"ModelingIntent warning: {warning}")

        host.logger.log_llm_interaction(
            iteration=iteration,
            role="model_agent_validation",
            request={
                "runtime_constraints": host.runtime_constraints,
                "optimization_mode": host.optimization_mode,
                "modeling_intent_diagnostics": modeling_intent_diagnostics,
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
        pymoo_algorithm = runtime.resolve_pymoo_algorithm()
        pop_size = int(opt_cfg.get("pymoo_pop_size", 96))
        n_generations = int(opt_cfg.get("pymoo_n_gen", max(max_iterations, 40)))
        seed = int(opt_cfg.get("pymoo_seed", 42))
        verbose = bool(opt_cfg.get("pymoo_verbose", False))
        return_least_infeasible = bool(opt_cfg.get("pymoo_return_least_infeasible", True))
        maas_base_attempts = max(1, int(opt_cfg.get("mass_max_attempts", 3)))
        maas_max_attempts = int(maas_base_attempts)
        maas_relax_ratio = max(0.0, float(opt_cfg.get("mass_relax_ratio", 0.08)))
        maas_auto_relax = bool(opt_cfg.get("mass_auto_relax", True))
        maas_retry_on_stall = bool(opt_cfg.get("mass_retry_on_stall", True))
        enable_mcts = bool(opt_cfg.get("mass_enable_mcts", True))
        mcts_max_depth = max(1, int(opt_cfg.get("mass_mcts_max_depth", 2)))
        mcts_c_uct = float(opt_cfg.get("mass_mcts_c_uct", 1.2))
        mcts_stagnation_rounds = max(0, int(opt_cfg.get("mass_mcts_stagnation_rounds", 2)))
        mcts_min_score_improvement = float(opt_cfg.get("mass_mcts_min_score_improvement", 1.0))
        mcts_min_cv_improvement = float(opt_cfg.get("mass_mcts_min_cv_improvement", 0.1))
        mcts_prune_margin = float(opt_cfg.get("mass_mcts_prune_margin", 100.0))
        mcts_action_prior_weight = float(opt_cfg.get("mass_mcts_action_prior_weight", 0.02))
        mcts_cv_penalty_weight = float(opt_cfg.get("mass_mcts_cv_penalty_weight", 0.2))
        thermal_evaluator_mode = str(
            opt_cfg.get("mass_thermal_evaluator_mode", "proxy")
        ).strip().lower()
        mass_physics_real_only = bool(opt_cfg.get("mass_physics_real_only", False))
        if mass_physics_real_only and thermal_evaluator_mode != "online_comsol":
            host.logger.logger.warning(
                "mass_physics_real_only=true but mass_thermal_evaluator_mode=%s. "
                "source gate will block non-real thermal path.",
                thermal_evaluator_mode,
            )
        dynamic_attempt_report: Dict[str, Any] = {
            "enabled": False,
            "base_budget": int(maas_base_attempts),
            "final_budget": int(maas_base_attempts),
            "component_count": int(len(getattr(current_state, "components", []) or [])),
            "component_bonus": 0,
            "strictness_bonus": 0,
            "online_comsol_bonus": 0,
            "enforce_power_bonus": 0,
            "strictness_count": 0,
            "strictness_flags": {},
            "max_cap": int(maas_base_attempts),
        }
        if bool(opt_cfg.get("mass_dynamic_attempts_enabled", False)):
            component_count = int(len(getattr(current_state, "components", []) or []))
            component_step = max(1, int(opt_cfg.get("mass_dynamic_attempts_component_step", 4)))
            component_step_bonus = max(0, int(opt_cfg.get("mass_dynamic_attempts_per_step", 1)))
            component_pivot = max(0, int(opt_cfg.get("mass_dynamic_attempts_component_pivot", 5)))
            dynamic_cap = max(
                int(maas_base_attempts),
                int(opt_cfg.get("mass_dynamic_attempts_max", maas_base_attempts + 4)),
            )

            strict_flags = {
                "max_temp_c_tight": float(host.runtime_constraints.get("max_temp_c", 50.0)) <= float(
                    opt_cfg.get("mass_dynamic_attempts_temp_threshold_c", 52.0)
                ),
                "min_clearance_mm_tight": float(
                    host.runtime_constraints.get("min_clearance_mm", 5.0)
                ) >= float(opt_cfg.get("mass_dynamic_attempts_clearance_threshold_mm", 6.0)),
                "max_cg_offset_mm_tight": float(
                    host.runtime_constraints.get("max_cg_offset_mm", 20.0)
                ) <= float(opt_cfg.get("mass_dynamic_attempts_cg_threshold_mm", 18.0)),
                "min_safety_factor_tight": float(
                    host.runtime_constraints.get("min_safety_factor", 2.0)
                ) >= float(opt_cfg.get("mass_dynamic_attempts_safety_factor_threshold", 2.1)),
                "min_modal_freq_hz_tight": float(
                    host.runtime_constraints.get("min_modal_freq_hz", 55.0)
                ) >= float(opt_cfg.get("mass_dynamic_attempts_modal_freq_threshold_hz", 60.0)),
                "max_voltage_drop_v_tight": float(
                    host.runtime_constraints.get("max_voltage_drop_v", 0.5)
                ) <= float(opt_cfg.get("mass_dynamic_attempts_voltage_drop_threshold_v", 0.45)),
                "min_power_margin_pct_tight": float(
                    host.runtime_constraints.get("min_power_margin_pct", 10.0)
                ) >= float(opt_cfg.get("mass_dynamic_attempts_power_margin_threshold_pct", 8.0)),
            }
            strict_count = int(sum(1 for value in strict_flags.values() if bool(value)))
            strict_count_threshold = max(
                1,
                int(opt_cfg.get("mass_dynamic_attempts_strict_count_threshold", 3)),
            )
            strictness_bonus = (
                int(max(0, int(opt_cfg.get("mass_dynamic_attempts_strictness_bonus", 1))))
                if strict_count >= strict_count_threshold
                else 0
            )

            component_bonus = 0
            if component_count > component_pivot:
                component_bonus = int(
                    ((component_count - component_pivot) // component_step) * component_step_bonus
                )

            online_comsol_bonus = (
                int(max(0, int(opt_cfg.get("mass_dynamic_attempts_online_comsol_bonus", 1))))
                if thermal_evaluator_mode == "online_comsol"
                else 0
            )
            enforce_power_bonus = (
                int(max(0, int(opt_cfg.get("mass_dynamic_attempts_enforce_power_bonus", 1))))
                if bool(host.runtime_constraints.get("enforce_power_budget", False))
                else 0
            )

            raw_budget = int(
                maas_base_attempts +
                component_bonus +
                strictness_bonus +
                online_comsol_bonus +
                enforce_power_bonus
            )
            maas_max_attempts = int(max(1, min(dynamic_cap, raw_budget)))
            dynamic_attempt_report = {
                "enabled": True,
                "base_budget": int(maas_base_attempts),
                "final_budget": int(maas_max_attempts),
                "component_count": int(component_count),
                "component_bonus": int(component_bonus),
                "strictness_bonus": int(strictness_bonus),
                "online_comsol_bonus": int(online_comsol_bonus),
                "enforce_power_bonus": int(enforce_power_bonus),
                "strictness_count": int(strict_count),
                "strictness_flags": strict_flags,
                "strictness_count_threshold": int(strict_count_threshold),
                "component_pivot": int(component_pivot),
                "component_step": int(component_step),
                "component_step_bonus": int(component_step_bonus),
                "max_cap": int(dynamic_cap),
            }

        mcts_budget = max(1, int(opt_cfg.get("mass_mcts_budget", maas_max_attempts)))
        if (
            bool(dynamic_attempt_report.get("enabled", False)) and
            bool(opt_cfg.get("mass_dynamic_attempts_align_mcts_budget", True)) and
            int(mcts_budget) < int(maas_max_attempts)
        ):
            host.logger.logger.info(
                "Dynamic attempts align mcts_budget: %d -> %d",
                int(mcts_budget),
                int(maas_max_attempts),
            )
            mcts_budget = int(maas_max_attempts)
        if bool(dynamic_attempt_report.get("enabled", False)):
            host.logger.logger.info(
                "MaaS dynamic attempt budget resolved: base=%d -> final=%d "
                "(component_bonus=%d, strictness_bonus=%d, online_comsol_bonus=%d, enforce_power_bonus=%d)",
                int(dynamic_attempt_report.get("base_budget", maas_base_attempts)),
                int(dynamic_attempt_report.get("final_budget", maas_max_attempts)),
                int(dynamic_attempt_report.get("component_bonus", 0)),
                int(dynamic_attempt_report.get("strictness_bonus", 0)),
                int(dynamic_attempt_report.get("online_comsol_bonus", 0)),
                int(dynamic_attempt_report.get("enforce_power_bonus", 0)),
            )
        else:
            host.logger.logger.info(
                "MaaS attempt budget fixed at %d (dynamic disabled).",
                int(maas_max_attempts),
            )
        trace_feature_window = max(
            1,
            int(opt_cfg.get("mass_trace_feature_window", 5)),
        )
        rag_runtime_ingest_enabled = bool(
            opt_cfg.get("mass_rag_runtime_ingest_enabled", True)
        )
        rag_runtime_ingest_max_items = max(
            1,
            int(opt_cfg.get("mass_rag_runtime_ingest_max_items", 4)),
        )
        enable_meta_policy = bool(opt_cfg.get("mass_enable_meta_policy", True))
        meta_policy_apply_runtime = bool(
            opt_cfg.get("mass_meta_policy_apply_runtime", True)
        )
        meta_policy_min_attempts = max(
            1,
            int(opt_cfg.get("mass_meta_policy_min_attempts", 2)),
        )
        enable_physics_audit = bool(opt_cfg.get("mass_enable_physics_audit", True))
        audit_top_k = max(1, int(opt_cfg.get("mass_audit_top_k", 3)))
        enforce_audit_feasible = bool(
            opt_cfg.get("mass_enforce_audit_feasible", True)
        )
        simulation_backend = str(host.config.get("simulation", {}).get("backend", "")).strip().lower()
        runtime_thermal_evaluator = runtime.build_maas_runtime_thermal_evaluator(
            mode=thermal_evaluator_mode,
            base_iteration=iteration,
        )
        online_comsol_budget_slice_enabled = bool(
            opt_cfg.get("mass_online_comsol_budget_slice_enabled", True)
        )
        configured_online_budget = max(
            int(opt_cfg.get("mass_online_comsol_eval_budget", 0)),
            0,
        )
        attempt_budget_slices: List[int] = []
        attempt_budget_cumulative: List[int] = []
        if configured_online_budget > 0 and maas_max_attempts > 0:
            base_slice = configured_online_budget // maas_max_attempts
            remainder = configured_online_budget % maas_max_attempts
            running_budget = 0
            for idx in range(maas_max_attempts):
                piece = int(base_slice + (1 if idx < remainder else 0))
                attempt_budget_slices.append(piece)
                running_budget += piece
                attempt_budget_cumulative.append(int(running_budget))
        online_budget_slice_report: Dict[str, Any] = {
            "enabled": bool(
                thermal_evaluator_mode == "online_comsol" and
                online_comsol_budget_slice_enabled and
                runtime_thermal_evaluator is not None and
                hasattr(runtime_thermal_evaluator, "set_eval_budget") and
                hasattr(runtime_thermal_evaluator, "get_eval_budget") and
                configured_online_budget > 0 and
                bool(attempt_budget_cumulative)
            ),
            "configured_total_budget": int(configured_online_budget),
            "attempt_count": int(maas_max_attempts),
            "slices": [int(v) for v in attempt_budget_slices],
            "cumulative_targets": [int(v) for v in attempt_budget_cumulative],
            "applied_records": [],
        }
        if online_budget_slice_report["enabled"]:
            host.logger.logger.info(
                "online_comsol attempt budget slicing enabled: total=%d, attempts=%d, slices=%s",
                int(configured_online_budget),
                int(maas_max_attempts),
                attempt_budget_slices,
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
        rag_ingest_report: Dict[str, Any] = {
            "enabled": bool(rag_runtime_ingest_enabled),
            "max_items": int(rag_runtime_ingest_max_items),
            "candidate_count": int(len(maas_attempts)),
            "selected_attempt_count": 0,
            "selected_attempts": [],
            "selected_reasons": {},
            "attempted_count": 0,
            "ingested_count": 0,
            "before_total": None,
            "after_total": None,
            "store_path": "",
            "error": "",
            "skipped_reason": "",
        }

        def _runtime_thermal_executed_calls() -> int:
            if runtime_thermal_evaluator is None:
                return 0
            try:
                stats = dict(getattr(runtime_thermal_evaluator, "stats", {}) or {})
            except Exception:
                stats = {}
            try:
                return max(int(stats.get("executed_online_comsol", 0) or 0), 0)
            except Exception:
                return 0

        def _allocate_attempt_online_budget(*, attempt: int, stage: str) -> Dict[str, Any]:
            if not bool(online_budget_slice_report.get("enabled", False)):
                return {}
            if not attempt_budget_cumulative:
                return {}
            if runtime_thermal_evaluator is None:
                return {}
            idx = min(max(int(attempt), 1), len(attempt_budget_cumulative)) - 1
            target_cumulative = int(attempt_budget_cumulative[idx])
            attempt_slice = int(attempt_budget_slices[idx]) if idx < len(attempt_budget_slices) else 0
            executed_before = int(_runtime_thermal_executed_calls())
            target_budget = max(int(target_cumulative), int(executed_before))
            try:
                previous_budget = int(runtime_thermal_evaluator.get_eval_budget())  # type: ignore[attr-defined]
            except Exception:
                previous_budget = int(target_budget)
            applied = False
            if previous_budget != target_budget:
                try:
                    runtime_thermal_evaluator.set_eval_budget(target_budget)  # type: ignore[attr-defined]
                    applied = True
                except Exception:
                    applied = False
            payload = {
                "attempt": int(attempt),
                "stage": str(stage),
                "slice_budget": int(attempt_slice),
                "cumulative_target": int(target_cumulative),
                "executed_before": int(executed_before),
                "budget_before": int(previous_budget),
                "budget_after": int(target_budget),
                "applied": bool(applied),
            }
            online_budget_slice_report.setdefault("applied_records", []).append(dict(payload))
            host.logger.log_maas_physics_event(
                {
                    "iteration": int(iteration),
                    "attempt": int(attempt),
                    "event_type": "runtime_thermal_budget_slice",
                    "simulation_backend": str(simulation_backend or ""),
                    "thermal_mode": str(thermal_evaluator_mode or ""),
                    "stage": str(stage),
                    "payload": dict(payload),
                }
            )
            if applied:
                host.logger.logger.info(
                    "online_comsol attempt budget slice applied: attempt=%d stage=%s budget=%d->%d "
                    "(slice=%d cumulative=%d executed_before=%d)",
                    int(attempt),
                    str(stage),
                    int(previous_budget),
                    int(target_budget),
                    int(attempt_slice),
                    int(target_cumulative),
                    int(executed_before),
                )
            return payload

        def _current_runtime_knobs() -> Dict[str, Any]:
            budget = int(opt_cfg.get("mass_online_comsol_eval_budget", 0))
            scheduler_knobs: Dict[str, Any] = {
                "online_comsol_schedule_mode": str(
                    opt_cfg.get("mass_online_comsol_schedule_mode", "budget_only")
                ).strip().lower(),
                "online_comsol_schedule_top_fraction": float(
                    opt_cfg.get("mass_online_comsol_schedule_top_fraction", 0.20)
                ),
                "online_comsol_schedule_min_observations": int(
                    opt_cfg.get("mass_online_comsol_schedule_min_observations", 8)
                ),
                "online_comsol_schedule_warmup_calls": int(
                    opt_cfg.get("mass_online_comsol_schedule_warmup_calls", 2)
                ),
                "online_comsol_schedule_explore_prob": float(
                    opt_cfg.get("mass_online_comsol_schedule_explore_prob", 0.05)
                ),
                "online_comsol_schedule_uncertainty_weight": float(
                    opt_cfg.get("mass_online_comsol_schedule_uncertainty_weight", 0.35)
                ),
                "online_comsol_schedule_uncertainty_scale_mm": float(
                    opt_cfg.get("mass_online_comsol_schedule_uncertainty_scale_mm", 25.0)
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
                    old_budget = int(opt_cfg.get("mass_online_comsol_eval_budget", 0))
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
            host.logger.log_mass_trace(trace_payload)

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

        def _build_layout_delta_inference(delta: Dict[str, Any]) -> Dict[str, Any]:
            moved_components = list(delta.get("moved_components", []) or [])
            added_heatsinks = list(delta.get("added_heatsinks", []) or [])
            added_brackets = list(delta.get("added_brackets", []) or [])
            changed_contacts = list(delta.get("changed_contacts", []) or [])
            changed_coatings = list(delta.get("changed_coatings", []) or [])
            has_layout_mutation = bool(
                moved_components
                or added_heatsinks
                or added_brackets
                or changed_contacts
                or changed_coatings
            )
            hints: List[str] = []
            if moved_components:
                hints.append("position_changed")
            if changed_contacts:
                hints.append("thermal_contact_changed")
            if added_heatsinks:
                hints.append("heatsink_added")
            if added_brackets:
                hints.append("bracket_added")
            if changed_coatings:
                hints.append("coating_changed")
            return {
                "has_layout_mutation": bool(has_layout_mutation),
                "moved_components": moved_components,
                "added_heatsinks": added_heatsinks,
                "added_brackets": added_brackets,
                "changed_contacts": changed_contacts,
                "changed_coatings": changed_coatings,
                "inferred_hints": hints,
            }

        def _save_attempt_layout_snapshot(payload: Dict[str, Any], decoded_state: Optional[DesignState]) -> None:
            stage = "attempt_candidate" if decoded_state is not None else "attempt_no_candidate"
            best_metrics = dict(payload.get("best_candidate_metrics", {}) or {})
            best_metrics["best_cv"] = payload.get("best_cv")
            best_metrics["aocc_cv"] = payload.get("aocc_cv")
            best_metrics["aocc_objective"] = payload.get("aocc_objective")
            thermal_source = "comsol" if str(thermal_evaluator_mode) == "online_comsol" else "proxy"
            operator_actions = list(payload.get("operator_actions", []) or [])
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
                operator_actions=operator_actions,
                thermal_source=thermal_source,
                metadata={
                    "runtime_thermal_snapshot": dict(payload.get("runtime_thermal_snapshot", {}) or {}),
                    "constraint_violation_breakdown": dict(
                        payload.get("constraint_violation_breakdown", {}) or {}
                    ),
                    "dominant_violation": str(payload.get("dominant_violation", "")),
                },
            )
            snapshot_delta = dict(snapshot_meta.get("delta", {}) or {})
            inference = _build_layout_delta_inference(snapshot_delta)
            inferred_attribution = bool(
                (not operator_actions) and bool(inference.get("has_layout_mutation", False))
            )
            payload["operator_attribution_inferred"] = bool(inferred_attribution)
            if inferred_attribution:
                payload["operator_inference"] = dict(inference)
                payload["operator_mutation_detected_without_actions"] = True
                snapshot_path = Path(str(snapshot_meta.get("snapshot_path", "") or ""))
                if snapshot_path.exists():
                    try:
                        snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                        metadata = dict(snapshot_payload.get("metadata", {}) or {})
                        metadata["operator_attribution_inferred"] = True
                        metadata["operator_inference"] = dict(inference)
                        snapshot_payload["metadata"] = metadata
                        snapshot_path.write_text(
                            json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception as exc:
                        host.logger.logger.debug(
                            "snapshot operator attribution patch failed: %s", exc
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
                if (
                    node.evaluation is None and
                    not dict(node.metadata or {}).get("latest_rollout_payload") and
                    evaluated_packs
                ):
                    last_attempt_payload = dict(
                        evaluated_packs[-1].get("attempt_payload", {}) or {}
                    )
                    if last_attempt_payload:
                        node.metadata = dict(node.metadata or {})
                        node.metadata["latest_rollout_payload"] = {
                            "dominant_violation": str(
                                last_attempt_payload.get("dominant_violation", "")
                            ),
                            "constraint_violation_breakdown": dict(
                                last_attempt_payload.get(
                                    "constraint_violation_breakdown", {}
                                )
                                or {}
                            ),
                            "best_candidate_metrics": dict(
                                last_attempt_payload.get("best_candidate_metrics", {}) or {}
                            ),
                            "best_cv": last_attempt_payload.get("best_cv"),
                        }
                return runtime.propose_maas_mcts_variants(
                    node=node,
                    relax_ratio=maas_relax_ratio,
                )

            def _evaluate(node: MCTSNode, rollout: int) -> MCTSEvaluation:
                attempt_counter["n"] += 1
                attempt = int(attempt_counter["n"])
                budget_slice_payload = _allocate_attempt_online_budget(
                    attempt=attempt,
                    stage="mcts",
                )
                eval_pack = runtime.evaluate_maas_intent_once(
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
                attempt_payload = _attach_runtime_thermal_snapshot(eval_pack["attempt_payload"])
                if budget_slice_payload:
                    attempt_payload["online_comsol_attempt_budget"] = dict(budget_slice_payload)
                eval_pack["attempt_payload"] = attempt_payload
                maas_attempts.append(attempt_payload)
                _save_attempt_layout_snapshot(attempt_payload, eval_pack.get("decoded_state"))
                _log_maas_attempt_trace(attempt_payload)
                host.logger.save_maas_diagnostic_event(
                    iteration=iteration,
                    attempt=attempt,
                    payload=attempt_payload,
                )
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
                budget_slice_payload = _allocate_attempt_online_budget(
                    attempt=attempt,
                    stage="retry_loop",
                )
                eval_pack = runtime.evaluate_maas_intent_once(
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
                attempt_payload = _attach_runtime_thermal_snapshot(eval_pack["attempt_payload"])
                if budget_slice_payload:
                    attempt_payload["online_comsol_attempt_budget"] = dict(budget_slice_payload)
                eval_pack["attempt_payload"] = attempt_payload
                maas_attempts.append(attempt_payload)
                best_attempt_payload = dict(attempt_payload)
                _save_attempt_layout_snapshot(attempt_payload, eval_pack.get("decoded_state"))
                _log_maas_attempt_trace(attempt_payload)
                host.logger.save_maas_diagnostic_event(
                    iteration=iteration,
                    attempt=attempt,
                    payload=attempt_payload,
                )

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
                    runtime.is_maas_retryable(last_diagnosis, retry_on_stall=maas_retry_on_stall)
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

                next_intent, applied_count = runtime.apply_relaxation_suggestions_to_intent(
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
            physics_audit_report, audited_state = runtime.run_maas_topk_physics_audit(
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
                final_metrics, final_violations = runtime.evaluate_design(final_state, iteration + 1)
            except Exception as exc:
                host.logger.logger.warning(
                    f"Final candidate evaluation failed, keep pre-solver metrics fallback: {exc}"
                )
                final_metrics, final_violations = current_metrics, violations
        else:
            final_metrics, final_violations = current_metrics, violations

        phase_d_retrieval: List[Dict[str, Any]] = []
        try:
            reflection_context = runtime.build_global_context(
                iteration=iteration,
                design_state=final_state,
                metrics=final_metrics,
                violations=final_violations,
                phase="D",
            )
            phase_d_retrieval = _serialize_retrieved_knowledge(
                getattr(reflection_context, "retrieved_knowledge", []) or []
            )
        except Exception as exc:
            host.logger.logger.warning(
                "Phase D retrieval context build failed: %s", exc
            )

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
        persisted_final_mph_path = host.logger.serialize_artifact_path(final_mph_path)

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
                "final_mph_path": persisted_final_mph_path,
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
        final_state.metadata["modeling_intent_diagnostics"] = dict(
            modeling_intent_diagnostics
        )
        final_state.metadata["rag_retrieval"] = {
            "phase_a": list(phase_a_retrieval),
            "phase_d": list(phase_d_retrieval),
            "phase_a_count": int(len(phase_a_retrieval)),
            "phase_d_count": int(len(phase_d_retrieval)),
        }
        final_state.metadata["modeling_validation"] = validation
        final_state.metadata["formulation_report"] = last_formulation_report
        final_state.metadata["compile_report"] = last_compile_report
        final_state.metadata["thermal_evaluator_mode"] = thermal_evaluator_mode
        final_state.metadata["enforce_audit_feasible"] = bool(enforce_audit_feasible)
        final_state.metadata["solver_diagnosis"] = last_diagnosis
        final_state.metadata["relaxation_suggestions"] = last_relaxation_suggestions
        final_state.metadata["maas_attempts"] = maas_attempts
        final_state.metadata["maas_attempt_budget"] = int(maas_max_attempts)
        final_state.metadata["maas_attempt_budget_base"] = int(maas_base_attempts)
        final_state.metadata["maas_attempt_budget_report"] = dict(dynamic_attempt_report)
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
        final_state.metadata["online_comsol_attempt_budget"] = dict(online_budget_slice_report)
        final_state.metadata["maas_trace_features"] = maas_trace_features
        final_state.metadata["meta_policy_report"] = meta_policy_report
        final_state.metadata["final_mph_path"] = str(persisted_final_mph_path or "")
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

        penalty_breakdown = runtime.calculate_penalty_breakdown(final_metrics, final_violations)
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
                "modeling_intent_diagnostics": dict(modeling_intent_diagnostics),
                "validation": validation,
                "solver_diagnosis": last_diagnosis,
                "relaxation_suggestions": last_relaxation_suggestions,
                "maas_attempts": maas_attempts,
                "mcts_report": mcts_report,
                "physics_audit": physics_audit_report,
                "runtime_thermal_evaluator_stats": runtime_thermal_evaluator_stats,
                "maas_trace_features": maas_trace_features,
                "meta_policy_report": meta_policy_report,
                "rag_retrieval": {
                    "phase_a": list(phase_a_retrieval),
                    "phase_d": list(phase_d_retrieval),
                },
                "rag_ingest": dict(rag_ingest_report),
            },
        )

        host.logger.logger.info(
            "MaaS trace features: feasible_rate=%s, best_cv_min=%s, comsol_per_feasible=%s, physics_pass_rate=%s",
            maas_trace_features.get("feasible_rate"),
            maas_trace_features.get("best_cv_min"),
            (maas_trace_features.get("runtime_thermal", {}) or {}).get("comsol_calls_per_feasible_attempt"),
            (maas_trace_features.get("physics_audit", {}) or {}).get("physics_pass_rate_topk"),
        )

        def _normalize_violation_key(value: Any) -> str:
            text = str(value or "").strip().lower()
            if not text:
                return ""
            if text.startswith("g_"):
                text = text[2:]
            return text

        def _resolve_dominant_violation(payload: Dict[str, Any]) -> str:
            dominant = _normalize_violation_key(payload.get("dominant_violation"))
            if dominant:
                return dominant

            breakdown = dict(payload.get("constraint_violation_breakdown", {}) or {})
            if not breakdown:
                return ""

            best_key = ""
            best_abs_value = float("-inf")
            for key, value in breakdown.items():
                parsed = _as_finite_float(value)
                score = abs(float(parsed)) if parsed is not None else 0.0
                if score > best_abs_value:
                    best_abs_value = float(score)
                    best_key = _normalize_violation_key(key)
            return best_key

        def _normalize_tag(value: Any) -> str:
            raw = str(value or "").strip().lower()
            if not raw:
                return ""
            normalized_chars: List[str] = []
            for ch in raw:
                if ch.isalnum() or ch in {"_", "-", "."}:
                    normalized_chars.append(ch)
                elif ch in {" ", ":", "/", "\\"}:
                    normalized_chars.append("_")
            normalized = "".join(normalized_chars).strip("_")
            while "__" in normalized:
                normalized = normalized.replace("__", "_")
            return normalized

        def _select_runtime_evidence_attempts(
            payloads: List[Dict[str, Any]],
            *,
            best_payload: Dict[str, Any],
            max_items: int,
        ) -> Dict[int, List[str]]:
            if not payloads:
                return {}

            ordered_attempts: List[tuple[int, Dict[str, Any]]] = []
            payload_by_attempt: Dict[int, Dict[str, Any]] = {}
            for idx, raw_payload in enumerate(list(payloads or []), start=1):
                payload = dict(raw_payload or {})
                attempt_no = int(payload.get("attempt", idx) or idx)
                ordered_attempts.append((attempt_no, payload))
                payload_by_attempt[attempt_no] = payload

            selected: Dict[int, set[str]] = {}

            def _mark(attempt_no: int, reason: str) -> None:
                if attempt_no not in payload_by_attempt:
                    return
                selected.setdefault(int(attempt_no), set()).add(str(reason))

            first_attempt_no = int(ordered_attempts[0][0])
            final_attempt_no = int(ordered_attempts[-1][0])
            _mark(first_attempt_no, "baseline_attempt")
            _mark(final_attempt_no, "final_attempt")

            best_attempt_no = int(best_payload.get("attempt", 0) or 0)
            if best_attempt_no <= 0 or best_attempt_no not in payload_by_attempt:
                best_attempt_no = 0
                best_cv_value: Optional[float] = None
                for attempt_no, payload in ordered_attempts:
                    attempt_best_cv = _as_finite_float(payload.get("best_cv"))
                    if attempt_best_cv is None:
                        attempt_best_cv = _as_finite_float(
                            dict(payload.get("diagnosis", {}) or {}).get("best_cv")
                        )
                    if attempt_best_cv is None:
                        continue
                    if best_cv_value is None or float(attempt_best_cv) < float(best_cv_value):
                        best_cv_value = float(attempt_best_cv)
                        best_attempt_no = int(attempt_no)
                if best_attempt_no <= 0:
                    best_attempt_no = int(final_attempt_no)
            _mark(best_attempt_no, "best_cv_attempt")

            for attempt_no, payload in ordered_attempts:
                diagnosis = dict(payload.get("diagnosis", {}) or {})
                status = str(diagnosis.get("status", "") or "").strip().lower()
                if status in {"feasible", "feasible_but_stalled"}:
                    _mark(attempt_no, "first_feasible_milestone")
                    break

            last_violation = ""
            for attempt_no, payload in ordered_attempts:
                dominant = _resolve_dominant_violation(payload)
                if not dominant:
                    continue
                if not last_violation:
                    _mark(attempt_no, "initial_violation_signature")
                elif dominant != last_violation:
                    _mark(attempt_no, "dominant_violation_shift")
                last_violation = dominant

            if not selected:
                _mark(final_attempt_no, "fallback_last_attempt")

            selected_items = list(selected.items())
            max_items = max(1, int(max_items))
            if len(selected_items) > max_items:
                reason_priority = {
                    "best_cv_attempt": 100,
                    "first_feasible_milestone": 90,
                    "dominant_violation_shift": 75,
                    "final_attempt": 60,
                    "baseline_attempt": 35,
                    "initial_violation_signature": 20,
                    "fallback_last_attempt": 10,
                }
                attempt_order = {
                    int(attempt_no): int(idx)
                    for idx, (attempt_no, _) in enumerate(ordered_attempts)
                }
                selected_items.sort(
                    key=lambda item: (
                        -max(reason_priority.get(reason, 1) for reason in item[1]),
                        -sum(reason_priority.get(reason, 1) for reason in item[1]),
                        attempt_order.get(int(item[0]), 10**6),
                    )
                )
                keep_attempts = {
                    int(attempt_no)
                    for attempt_no, _ in selected_items[:max_items]
                }
            else:
                keep_attempts = {int(attempt_no) for attempt_no, _ in selected_items}

            output: Dict[int, List[str]] = {}
            for attempt_no, _ in ordered_attempts:
                attempt_no = int(attempt_no)
                if attempt_no not in keep_attempts:
                    continue
                reason_list = sorted(selected.get(attempt_no, set()))
                if reason_list:
                    output[attempt_no] = reason_list
            return output

        def _build_runtime_evidence_from_attempts(
            *,
            payloads: List[Dict[str, Any]],
            best_payload: Dict[str, Any],
            max_items: int,
            source_gate: Dict[str, Any],
            operator_family_gate: Dict[str, Any],
        ) -> tuple[List[MassEvidence], Dict[str, Any]]:
            if not payloads:
                return [], {
                    "candidate_count": 0,
                    "selected_attempt_count": 0,
                    "selected_attempts": [],
                    "selected_reasons": {},
                    "evidence_count": 0,
                }

            payload_by_attempt: Dict[int, Dict[str, Any]] = {}
            for idx, raw_payload in enumerate(list(payloads or []), start=1):
                payload = dict(raw_payload or {})
                attempt_no = int(payload.get("attempt", idx) or idx)
                payload_by_attempt[attempt_no] = payload

            baseline_best_cv = _as_finite_float(
                dict(payloads[0] if payloads else {}).get("best_cv")
            )
            selected_map = _select_runtime_evidence_attempts(
                payloads,
                best_payload=best_payload,
                max_items=max_items,
            )

            evidence_items: List[MassEvidence] = []
            for attempt_no, reasons in selected_map.items():
                payload = dict(payload_by_attempt.get(int(attempt_no), {}) or {})
                diagnosis = dict(payload.get("diagnosis", {}) or {})
                diagnosis_status = str(
                    diagnosis.get("status", "") or payload.get("diagnosis_status", "")
                ).strip().lower()
                diagnosis_reason = str(diagnosis.get("reason", "") or "").strip()
                dominant_violation = _resolve_dominant_violation(payload)

                breakdown_raw = dict(
                    payload.get("constraint_violation_breakdown", {}) or {}
                )
                normalized_violation_types: List[str] = []
                if dominant_violation:
                    normalized_violation_types.append(dominant_violation)
                for key in breakdown_raw.keys():
                    normalized = _normalize_violation_key(key)
                    if normalized and normalized not in normalized_violation_types:
                        normalized_violation_types.append(normalized)

                branch_source = str(payload.get("branch_source", "") or "").strip().lower()
                branch_action = str(payload.get("branch_action", "") or "").strip()
                search_space_mode = str(payload.get("search_space_mode", "") or "").strip().lower()
                operator_program_id = str(
                    payload.get("operator_program_id", "") or ""
                ).strip()
                operator_actions = [
                    str(item).strip().lower()
                    for item in list(payload.get("operator_actions", []) or [])
                    if str(item).strip()
                ]
                best_cv_value = _as_finite_float(payload.get("best_cv"))
                aocc_cv_value = _as_finite_float(payload.get("aocc_cv"))
                aocc_objective_value = _as_finite_float(payload.get("aocc_objective"))
                strict_proxy_feasible = bool(
                    diagnosis_status in {"feasible", "feasible_but_stalled"}
                    and bool(source_gate.get("passed", True))
                    and bool(operator_family_gate.get("passed", True))
                    and (best_cv_value is None or float(best_cv_value) <= 1e-9)
                )

                best_cv_delta = None
                if baseline_best_cv is not None and best_cv_value is not None:
                    best_cv_delta = float(best_cv_value - baseline_best_cv)

                evidence_title = (
                    f"runtime_attempt_{int(attempt_no):02d}_"
                    f"{diagnosis_status or 'unknown'}_"
                    f"{dominant_violation or 'none'}"
                )
                evidence_content = "\n".join(
                    [
                        f"Iteration: {int(iteration)}",
                        f"Attempt: {int(attempt_no)}",
                        f"Diagnosis: {diagnosis_status or 'unknown'}",
                        f"Reason: {diagnosis_reason or 'n/a'}",
                        f"Dominant violation: {dominant_violation or 'none'}",
                        f"best_cv: {best_cv_value if best_cv_value is not None else 'nan'}",
                        f"aocc_cv: {aocc_cv_value if aocc_cv_value is not None else 'nan'}",
                        f"Branch source: {branch_source or 'unknown'}",
                        f"Selector reasons: {', '.join(reasons)}",
                    ]
                )
                tags = [
                    _normalize_tag("runtime_attempt_ingest"),
                    _normalize_tag(f"diagnosis_{diagnosis_status or 'unknown'}"),
                    _normalize_tag(
                        f"violation_{dominant_violation or 'none'}"
                    ),
                    _normalize_tag(f"branch_{branch_source or 'unknown'}"),
                ]
                if search_space_mode:
                    tags.append(_normalize_tag(f"search_space_{search_space_mode}"))
                tags.extend(
                    _normalize_tag(f"selector_{reason}") for reason in reasons
                )
                tags = [tag for tag in tags if tag]

                evidence_items.append(
                    MassEvidence(
                        evidence_id="",
                        phase_hint="D",
                        category="case",
                        title=evidence_title,
                        content=evidence_content,
                        query_signature={
                            "violation_types": list(normalized_violation_types),
                            "dominant_violations": (
                                [dominant_violation] if dominant_violation else []
                            ),
                        },
                        action_signature={
                            "operator_family": (
                                search_space_mode or branch_source or "runtime_retry"
                            ),
                            "branch_action": branch_action,
                            "branch_source": branch_source,
                            "operator_program_id": operator_program_id,
                            "operator_actions": list(operator_actions),
                        },
                        outcome_signature={
                            "diagnosis_status": diagnosis_status,
                            "diagnosis_reason": diagnosis_reason,
                            "strict_proxy_feasible": bool(strict_proxy_feasible),
                            "best_cv": best_cv_value,
                            "aocc_cv": aocc_cv_value,
                            "aocc_objective": aocc_objective_value,
                            "relaxation_applied_count": int(
                                payload.get("relaxation_applied_count", 0) or 0
                            ),
                        },
                        physics_provenance={
                            "simulation_backend": str(simulation_backend or ""),
                            "thermal_evaluator_mode": str(thermal_evaluator_mode or ""),
                            "source_gate_mode": str(source_gate.get("mode", "")),
                            "source_gate_passed": bool(
                                source_gate.get("passed", True)
                            ),
                            "operator_family_gate_mode": str(
                                operator_family_gate.get("mode", "")
                            ),
                            "operator_family_gate_passed": bool(
                                operator_family_gate.get("passed", True)
                            ),
                        },
                        tags=tags,
                        metadata={
                            "iteration": int(iteration),
                            "attempt": int(attempt_no),
                            "selector_reasons": list(reasons),
                            "run_dir": host.logger.serialize_artifact_path(
                                str(getattr(host.logger, "run_dir", "") or "")
                            ),
                            "layout_state_hash": str(
                                payload.get("layout_state_hash", "") or ""
                            ),
                            "constraint_violation_breakdown": dict(breakdown_raw),
                            "best_candidate_metrics": dict(
                                payload.get("best_candidate_metrics", {}) or {}
                            ),
                            "runtime_thermal_snapshot": dict(
                                payload.get("runtime_thermal_snapshot", {}) or {}
                            ),
                            "metrics_improvement": (
                                {"best_cv": float(best_cv_delta)}
                                if best_cv_delta is not None
                                else {}
                            ),
                        },
                    )
                )

            return evidence_items, {
                "candidate_count": int(len(payloads)),
                "selected_attempt_count": int(len(selected_map)),
                "selected_attempts": [int(item) for item in list(selected_map.keys())],
                "selected_reasons": {
                    str(attempt_no): list(reasons)
                    for attempt_no, reasons in selected_map.items()
                },
                "evidence_count": int(len(evidence_items)),
            }

        def _is_real_metric_source(value: Any) -> bool:
            text = str(value or "").strip().lower()
            if not text:
                return False
            reject_tokens = (
                "proxy",
                "penalty",
                "alias",
                "unavailable",
                "fallback",
                "mixed",
                "partial",
            )
            if any(token in text for token in reject_tokens):
                return False
            if text in {"unknown", "none", "null", "n/a", "na"}:
                return False
            return True

        def _is_structural_comsol_source(value: Any) -> bool:
            text = str(value or "").strip().lower()
            return text.startswith("online_comsol_structural")

        def _is_power_comsol_source(value: Any) -> bool:
            text = str(value or "").strip().lower()
            return text.startswith("online_comsol_power")

        def _is_thermal_comsol_source(value: Any) -> bool:
            text = str(value or "").strip().lower()
            return text == "online_comsol"

        def _evaluate_source_gate() -> Dict[str, Any]:
            mode = str(opt_cfg.get("mass_source_gate_mode", "off") or "off").strip().lower()
            if mode not in {"off", "warn", "strict"}:
                mode = "off"
            if mass_physics_real_only and mode == "off":
                mode = "strict"

            require_structural_real = bool(
                opt_cfg.get("mass_source_gate_require_structural_real", False)
            ) or bool(mass_physics_real_only)
            require_power_real = bool(
                opt_cfg.get("mass_source_gate_require_power_real", False)
            ) or bool(mass_physics_real_only)
            require_thermal_real = bool(
                opt_cfg.get("mass_source_gate_require_thermal_real", False)
            ) or bool(mass_physics_real_only)
            require_mission_real = bool(
                opt_cfg.get("mass_source_gate_require_mission_real", False)
            ) or bool(mass_physics_real_only)

            diagnostics = dict(final_metrics.get("diagnostics", {}) or {})
            structural_metric_sources = dict(diagnostics.get("structural_metric_sources", {}) or {})
            power_metric_sources = dict(diagnostics.get("power_metric_sources", {}) or {})
            metric_sources = dict(diagnostics.get("metric_sources", {}) or {})

            if not structural_metric_sources:
                fallback_structural_source = str(diagnostics.get("structural_source", "") or "")
                structural_metric_sources = {
                    key: fallback_structural_source
                    for key in ("max_stress", "max_displacement", "first_modal_freq", "safety_factor")
                }
            if not power_metric_sources:
                fallback_power_source = str(diagnostics.get("power_source", "") or "")
                power_metric_sources = {
                    key: fallback_power_source
                    for key in ("total_power", "peak_power", "power_margin", "voltage_drop")
                }
            thermal_source = str(
                metric_sources.get("thermal_source", diagnostics.get("thermal_source", "")) or ""
            )
            if not thermal_source:
                thermal_source = "online_comsol" if thermal_evaluator_mode == "online_comsol" else "proxy"
            mission_source = str(
                diagnostics.get("mission_source", "")
                or dict(diagnostics.get("mission_metrics", {}) or {}).get("mission_source", "")
            )
            comsol_feature_domain_audit_mode = str(
                opt_cfg.get("mass_comsol_feature_domain_audit_mode", "off") or "off"
            ).strip().lower()
            if comsol_feature_domain_audit_mode not in {"off", "warn", "strict"}:
                comsol_feature_domain_audit_mode = "off"
            comsol_feature_domain_audit = dict(
                diagnostics.get("comsol_feature_domain_audit", {}) or {}
            )
            comsol_feature_domain_audit_present = bool(comsol_feature_domain_audit)
            comsol_feature_domain_audit_required = bool(
                comsol_feature_domain_audit_mode in {"warn", "strict"}
            )
            comsol_feature_domain_audit_backend_applicable = (
                str(simulation_backend or "").strip().lower() == "comsol"
            )
            comsol_feature_domain_audit_failed_checks = list(
                comsol_feature_domain_audit.get("failed_checks", []) or []
            )
            if not comsol_feature_domain_audit_backend_applicable:
                comsol_feature_domain_audit_passed = True
            elif not comsol_feature_domain_audit_required:
                comsol_feature_domain_audit_passed = True
            elif not comsol_feature_domain_audit_present:
                comsol_feature_domain_audit_passed = False
                if not comsol_feature_domain_audit_failed_checks:
                    comsol_feature_domain_audit_failed_checks = [
                        "audit_payload_missing",
                    ]
            else:
                comsol_feature_domain_audit_passed = bool(
                    comsol_feature_domain_audit.get("passed", False)
                )
                if (
                    not comsol_feature_domain_audit_passed
                    and not comsol_feature_domain_audit_failed_checks
                ):
                    comsol_feature_domain_audit_failed_checks = [
                        "audit_checks_failed",
                    ]

            structural_missing_real = [
                key
                for key in ("max_stress", "max_displacement", "first_modal_freq", "safety_factor")
                if not _is_real_metric_source(structural_metric_sources.get(key))
            ]
            power_missing_real = [
                key
                for key in ("total_power", "peak_power", "power_margin", "voltage_drop")
                if not _is_real_metric_source(power_metric_sources.get(key))
            ]
            if mass_physics_real_only:
                for key in ("max_stress", "max_displacement", "first_modal_freq", "safety_factor"):
                    if not _is_structural_comsol_source(structural_metric_sources.get(key)):
                        if key not in structural_missing_real:
                            structural_missing_real.append(key)
                for key in ("total_power", "peak_power", "power_margin", "voltage_drop"):
                    if not _is_power_comsol_source(power_metric_sources.get(key)):
                        if key not in power_missing_real:
                            power_missing_real.append(key)

            structural_passed = (
                True if not require_structural_real else len(structural_missing_real) == 0
            )
            power_passed = (
                True if not require_power_real else len(power_missing_real) == 0
            )
            thermal_passed = (
                True if not require_thermal_real else _is_real_metric_source(thermal_source)
            )
            if mass_physics_real_only and require_thermal_real:
                thermal_passed = bool(_is_thermal_comsol_source(thermal_source))
            mission_passed = (
                True if not require_mission_real else _is_real_metric_source(mission_source)
            )
            real_only_reasons: List[str] = []
            if mass_physics_real_only and thermal_evaluator_mode != "online_comsol":
                real_only_reasons.append(
                    f"thermal_evaluator_mode={thermal_evaluator_mode} (requires online_comsol)"
                )
            passed = bool(
                structural_passed and
                power_passed and
                thermal_passed and
                mission_passed and
                bool(comsol_feature_domain_audit_passed) and
                len(real_only_reasons) == 0
            )
            strict_blocked = bool(mode == "strict" and not passed)

            return {
                "mode": str(mode),
                "real_only": bool(mass_physics_real_only),
                "require_structural_real": bool(require_structural_real),
                "require_power_real": bool(require_power_real),
                "require_thermal_real": bool(require_thermal_real),
                "require_mission_real": bool(require_mission_real),
                "comsol_feature_domain_audit_mode": str(comsol_feature_domain_audit_mode),
                "comsol_feature_domain_audit_required": bool(comsol_feature_domain_audit_required),
                "comsol_feature_domain_audit_present": bool(comsol_feature_domain_audit_present),
                "comsol_feature_domain_audit_backend_applicable": bool(
                    comsol_feature_domain_audit_backend_applicable
                ),
                "comsol_feature_domain_audit_passed": bool(comsol_feature_domain_audit_passed),
                "comsol_feature_domain_audit_failed_checks": list(
                    comsol_feature_domain_audit_failed_checks
                ),
                "comsol_feature_domain_audit": dict(comsol_feature_domain_audit),
                "structural_passed": bool(structural_passed),
                "power_passed": bool(power_passed),
                "thermal_passed": bool(thermal_passed),
                "mission_passed": bool(mission_passed),
                "passed": bool(passed),
                "strict_blocked": bool(strict_blocked),
                "structural_missing_real_metrics": list(structural_missing_real),
                "power_missing_real_metrics": list(power_missing_real),
                "thermal_source": str(thermal_source),
                "mission_source": str(mission_source),
                "thermal_missing_real": bool(not thermal_passed),
                "mission_missing_real": bool(not mission_passed),
                "real_only_reasons": list(real_only_reasons),
                "structural_metric_sources": structural_metric_sources,
                "power_metric_sources": power_metric_sources,
            }

        def _evaluate_operator_family_gate() -> Dict[str, Any]:
            mode = str(opt_cfg.get("mass_operator_family_gate_mode", "off") or "off").strip().lower()
            if mode not in {"off", "warn", "strict"}:
                mode = "off"
            if mass_physics_real_only and mode == "off":
                mode = "strict"

            required_families = parse_required_families(
                opt_cfg.get(
                    "mass_operator_family_required",
                    "geometry,thermal,structural,power,mission",
                )
            )
            covered_families: set[str] = set()
            family_breakdown: Dict[str, int] = {}
            implementation_breakdown: Dict[str, int] = {}
            unknown_actions: set[str] = set()

            for attempt in list(maas_attempts or []):
                attempt_payload = dict(attempt or {})
                action_seq = list(attempt_payload.get("operator_actions", []) or [])
                if not action_seq:
                    continue

                gate_report = dict(attempt_payload.get("operator_family_gate", {}) or {})
                if not gate_report:
                    gate_report = evaluate_operator_family_coverage(
                        actions=action_seq,
                        required_families=required_families,
                    )

                for family in list(gate_report.get("covered_families", []) or []):
                    text = str(family).strip().lower()
                    if text:
                        covered_families.add(text)
                for family, count in dict(gate_report.get("family_breakdown", {}) or {}).items():
                    family_key = str(family).strip().lower()
                    if not family_key:
                        continue
                    family_breakdown[family_key] = int(family_breakdown.get(family_key, 0)) + int(count)
                for impl, count in dict(gate_report.get("implementation_breakdown", {}) or {}).items():
                    impl_key = str(impl).strip().lower()
                    if not impl_key:
                        continue
                    implementation_breakdown[impl_key] = int(
                        implementation_breakdown.get(impl_key, 0)
                    ) + int(count)
                for unknown in list(gate_report.get("unknown_actions", []) or []):
                    unknown_text = str(unknown).strip().lower()
                    if unknown_text:
                        unknown_actions.add(unknown_text)

            missing_families = [
                str(family)
                for family in required_families
                if str(family) not in covered_families
            ]
            passed = len(missing_families) == 0
            strict_blocked = bool(mode == "strict" and not passed)

            return {
                "mode": str(mode),
                "required_families": list(required_families),
                "covered_families": sorted(covered_families),
                "missing_families": list(missing_families),
                "family_breakdown": dict(family_breakdown),
                "implementation_breakdown": dict(implementation_breakdown),
                "unknown_actions": sorted(unknown_actions),
                "passed": bool(passed),
                "strict_blocked": bool(strict_blocked),
            }

        def _evaluate_operator_realization_gate(*, source_gate_report: Dict[str, Any]) -> Dict[str, Any]:
            mode = str(
                opt_cfg.get("mass_operator_realization_gate_mode", "off") or "off"
            ).strip().lower()
            if mode not in {"off", "warn", "strict"}:
                mode = "off"
            if mass_physics_real_only and mode == "off":
                mode = "strict"

            required_families = parse_required_families(
                opt_cfg.get(
                    "mass_operator_family_required",
                    "geometry,thermal,structural,power,mission",
                )
            )
            required_set = set(required_families)
            thermal_actions = {"hot_spread", "add_heatstrap", "set_thermal_contact"}
            thermal_evidence_missing_actions: set[str] = set()
            thermal_action_observed: set[str] = set()
            thermal_action_realized: set[str] = set()
            realization_context = {
                "thermal_real": bool(source_gate_report.get("thermal_passed", False)),
                "structural_real": bool(source_gate_report.get("structural_passed", False)),
                "power_real": bool(source_gate_report.get("power_passed", False)),
                "mission_real": bool(source_gate_report.get("mission_passed", False)),
                "real_only": bool(mass_physics_real_only),
            }

            realized_families: set[str] = set()
            realized_family_breakdown: Dict[str, int] = {}
            non_real_family_breakdown: Dict[str, int] = {}
            non_real_actions: List[Dict[str, Any]] = []
            unknown_actions: set[str] = set()

            for attempt in list(maas_attempts or []):
                attempt_payload = dict(attempt or {})
                action_seq = list(attempt_payload.get("operator_actions", []) or [])
                if not action_seq:
                    continue

                gate_report = dict(attempt_payload.get("operator_realization_gate", {}) or {})
                if not gate_report:
                    gate_report = evaluate_operator_realization(
                        actions=action_seq,
                        realization_context=realization_context,
                        required_families=required_families,
                    )
                thermal_realization = dict(
                    attempt_payload.get("operator_thermal_realization", {}) or {}
                )
                action_evidence_map = dict(
                    thermal_realization.get("action_evidence", {}) or {}
                )
                missing_heat_actions: List[str] = []
                for action in list(action_seq or []):
                    action_name = str(action or "").strip().lower()
                    if action_name not in thermal_actions:
                        continue
                    thermal_action_observed.add(action_name)
                    evidence = dict(action_evidence_map.get(action_name, {}) or {})
                    if bool(evidence.get("realized", False)):
                        thermal_action_realized.add(action_name)
                    else:
                        missing_heat_actions.append(action_name)
                if missing_heat_actions:
                    for action_name in missing_heat_actions:
                        thermal_evidence_missing_actions.add(action_name)

                    realized_families_report = [
                        str(item).strip().lower()
                        for item in list(gate_report.get("realized_families", []) or [])
                        if str(item).strip().lower() != "thermal"
                    ]
                    gate_report["realized_families"] = list(
                        sorted(set(realized_families_report))
                    )

                    realized_family_breakdown_report = dict(
                        gate_report.get("realized_family_breakdown", {}) or {}
                    )
                    realized_family_breakdown_report.pop("thermal", None)
                    gate_report["realized_family_breakdown"] = realized_family_breakdown_report

                    non_real_family_breakdown_report = dict(
                        gate_report.get("non_real_family_breakdown", {}) or {}
                    )
                    non_real_family_breakdown_report["thermal"] = int(
                        non_real_family_breakdown_report.get("thermal", 0)
                    ) + int(len(missing_heat_actions))
                    gate_report["non_real_family_breakdown"] = non_real_family_breakdown_report

                    non_real_actions_report = list(gate_report.get("non_real_actions", []) or [])
                    for action_name in missing_heat_actions:
                        non_real_actions_report.append(
                            {
                                "action": str(action_name),
                                "family": "thermal",
                                "implementation": "conditional_real",
                                "physics_path": "thermal_contacts + online_comsol",
                                "boundary_note": "热学算子缺少真实落地证据",
                                "real_requirements": ["thermal_real", "thermal_effect_evidence"],
                                "missing_requirements": ["thermal_effect_evidence"],
                                "realized": False,
                                "evidence": dict(action_evidence_map.get(action_name, {}) or {}),
                            }
                        )
                    gate_report["non_real_actions"] = non_real_actions_report

                for family in list(gate_report.get("realized_families", []) or []):
                    family_name = str(family).strip().lower()
                    if family_name:
                        realized_families.add(family_name)
                for family, count in dict(gate_report.get("realized_family_breakdown", {}) or {}).items():
                    family_name = str(family).strip().lower()
                    if not family_name:
                        continue
                    realized_family_breakdown[family_name] = int(
                        realized_family_breakdown.get(family_name, 0)
                    ) + int(count)
                for family, count in dict(gate_report.get("non_real_family_breakdown", {}) or {}).items():
                    family_name = str(family).strip().lower()
                    if not family_name:
                        continue
                    non_real_family_breakdown[family_name] = int(
                        non_real_family_breakdown.get(family_name, 0)
                    ) + int(count)
                for item in list(gate_report.get("non_real_actions", []) or []):
                    if isinstance(item, dict):
                        non_real_actions.append(dict(item))
                for unknown in list(gate_report.get("unknown_actions", []) or []):
                    text = str(unknown).strip().lower()
                    if text:
                        unknown_actions.add(text)

            dedup_non_real: List[Dict[str, Any]] = []
            seen_non_real: set[tuple[str, str]] = set()
            for item in non_real_actions:
                action = str(item.get("action", "")).strip().lower()
                family = str(item.get("family", "")).strip().lower()
                key = (action, family)
                if not action or key in seen_non_real:
                    continue
                seen_non_real.add(key)
                dedup_non_real.append(item)

            unresolved_thermal_actions = sorted(
                action
                for action in thermal_action_observed
                if action not in thermal_action_realized
            )
            unresolved_thermal_set = set(unresolved_thermal_actions)
            thermal_evidence_missing_actions = set(unresolved_thermal_actions)

            filtered_non_real: List[Dict[str, Any]] = []
            for item in dedup_non_real:
                family = str(item.get("family", "")).strip().lower()
                action = str(item.get("action", "")).strip().lower()
                if family == "thermal" and action and action not in unresolved_thermal_set:
                    continue
                filtered_non_real.append(item)
            dedup_non_real = filtered_non_real

            missing_realized_families = [
                str(family)
                for family in required_families
                if str(family) not in realized_families
            ]
            non_real_required_actions = [
                item
                for item in dedup_non_real
                if str(item.get("family", "")).strip().lower() in required_set
                or not str(item.get("family", "")).strip().lower()
            ]
            non_real_actions_by_family: Dict[str, List[str]] = {}
            for item in non_real_required_actions:
                family = str(item.get("family", "")).strip().lower() or "unknown"
                action = str(item.get("action", "")).strip().lower()
                if not action:
                    continue
                non_real_actions_by_family.setdefault(family, [])
                non_real_actions_by_family[family].append(action)
            non_real_actions_by_family = {
                key: sorted(set(values))
                for key, values in non_real_actions_by_family.items()
            }
            non_real_family_breakdown_filtered: Dict[str, int] = {}
            for item in non_real_required_actions:
                family = str(item.get("family", "")).strip().lower() or "unknown"
                non_real_family_breakdown_filtered[family] = int(
                    non_real_family_breakdown_filtered.get(family, 0)
                ) + 1

            passed = (
                len(missing_realized_families) == 0 and
                len(non_real_required_actions) == 0 and
                len(unknown_actions) == 0
            )
            strict_blocked = bool(mode == "strict" and not passed)
            return {
                "mode": str(mode),
                "required_families": list(required_families),
                "realized_families": sorted(realized_families),
                "missing_realized_families": list(missing_realized_families),
                "realization_context": dict(realization_context),
                "realized_family_breakdown": dict(realized_family_breakdown),
                "non_real_family_breakdown": dict(non_real_family_breakdown_filtered),
                "non_real_actions": list(non_real_required_actions),
                "non_real_actions_by_family": dict(non_real_actions_by_family),
                "unknown_actions": sorted(unknown_actions),
                "thermal_evidence_missing_actions": sorted(thermal_evidence_missing_actions),
                "passed": bool(passed),
                "strict_blocked": bool(strict_blocked),
            }

        source_gate_report = _evaluate_source_gate()
        operator_family_gate_report = _evaluate_operator_family_gate()
        operator_realization_gate_report = _evaluate_operator_realization_gate(
            source_gate_report=source_gate_report,
        )
        final_state.metadata["source_gate"] = source_gate_report
        final_state.metadata["operator_family_gate"] = operator_family_gate_report
        final_state.metadata["operator_realization_gate"] = operator_realization_gate_report
        if source_gate_report.get("mode") in {"warn", "strict"} and not source_gate_report.get("passed", True):
            host.logger.logger.warning(
                "MaaS source gate failed: mode=%s, structural_missing=%s, power_missing=%s, thermal_missing=%s, mission_missing=%s, feature_domain_audit_failed_checks=%s",
                source_gate_report.get("mode"),
                source_gate_report.get("structural_missing_real_metrics"),
                source_gate_report.get("power_missing_real_metrics"),
                source_gate_report.get("thermal_missing_real"),
                source_gate_report.get("mission_missing_real"),
                source_gate_report.get("comsol_feature_domain_audit_failed_checks"),
            )
        if (
            operator_family_gate_report.get("mode") in {"warn", "strict"}
            and not operator_family_gate_report.get("passed", True)
        ):
            host.logger.logger.warning(
                "MaaS operator-family gate failed: mode=%s, missing=%s",
                operator_family_gate_report.get("mode"),
                operator_family_gate_report.get("missing_families"),
            )
        if (
            operator_realization_gate_report.get("mode") in {"warn", "strict"}
            and not operator_realization_gate_report.get("passed", True)
        ):
            host.logger.logger.warning(
                "MaaS operator-realization gate failed: mode=%s, missing_realized=%s, non_real=%s",
                operator_realization_gate_report.get("mode"),
                operator_realization_gate_report.get("missing_realized_families"),
                list(
                    (operator_realization_gate_report.get("non_real_actions_by_family", {}) or {}).keys()
                ),
            )
        rag_ingest_report["candidate_count"] = int(len(maas_attempts))
        if not bool(rag_runtime_ingest_enabled):
            rag_ingest_report["skipped_reason"] = "disabled_by_config"
        elif not maas_attempts:
            rag_ingest_report["skipped_reason"] = "no_attempt_payloads"
        else:
            try:
                runtime_evidence_items, selection_report = _build_runtime_evidence_from_attempts(
                    payloads=maas_attempts,
                    best_payload=best_attempt_payload,
                    max_items=rag_runtime_ingest_max_items,
                    source_gate=source_gate_report,
                    operator_family_gate=operator_family_gate_report,
                )
                rag_ingest_report.update(selection_report)
                rag_ingest_report["attempted_count"] = int(len(runtime_evidence_items))
                if not runtime_evidence_items:
                    rag_ingest_report["skipped_reason"] = "no_selected_evidence"
                else:
                    rag_system = getattr(host, "rag_system", None)
                    ingest_fn = (
                        getattr(rag_system, "ingest_evidence", None)
                        if rag_system is not None
                        else None
                    )
                    stats_fn = (
                        getattr(rag_system, "stats", None)
                        if rag_system is not None
                        else None
                    )
                    if not callable(ingest_fn):
                        rag_ingest_report["error"] = "rag_system_ingest_not_available"
                    else:
                        before_stats = (
                            dict(stats_fn() or {})
                            if callable(stats_fn)
                            else {}
                        )
                        if before_stats:
                            rag_ingest_report["before_total"] = int(
                                before_stats.get("total", 0) or 0
                            )
                            rag_ingest_report["store_path"] = str(
                                before_stats.get("path", "") or ""
                            )
                        ingested_count = int(ingest_fn(runtime_evidence_items) or 0)
                        rag_ingest_report["ingested_count"] = int(max(ingested_count, 0))
                        if callable(stats_fn):
                            after_stats = dict(stats_fn() or {})
                            if after_stats:
                                rag_ingest_report["after_total"] = int(
                                    after_stats.get("total", 0) or 0
                                )
                                if not rag_ingest_report["store_path"]:
                                    rag_ingest_report["store_path"] = str(
                                        after_stats.get("path", "") or ""
                                    )
            except Exception as exc:
                rag_ingest_report["error"] = str(exc)
                host.logger.logger.warning("Runtime evidence ingest failed: %s", exc)
        final_state.metadata["rag_ingest"] = dict(rag_ingest_report)

        host.logger.save_design_state(iteration, final_state.model_dump())
        is_success = (
            last_diagnosis.get("status") in {"feasible", "feasible_but_stalled"} and
            len(final_violations) == 0
        )
        if bool(source_gate_report.get("strict_blocked", False)):
            is_success = False
        if bool(operator_family_gate_report.get("strict_blocked", False)):
            is_success = False
        if bool(operator_realization_gate_report.get("strict_blocked", False)):
            is_success = False
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
        compile_report_for_summary = dict(last_compile_report or {})
        llm_compile_warnings = list(compile_report_for_summary.get("warnings", []) or [])
        llm_dropped_constraints = list(
            compile_report_for_summary.get("dropped_constraints", []) or []
        )
        llm_unsupported_metrics = list(
            compile_report_for_summary.get("unsupported_metrics", []) or []
        )
        llm_parsed_variables = int(
            compile_report_for_summary.get("parsed_variables", 0) or 0
        )
        llm_variable_mapping_warning = any(
            "No valid variable mapping found in ModelingIntent" in str(item)
            for item in llm_compile_warnings
        )
        llm_effective_variable_mapping_passed = bool(
            llm_parsed_variables > 0 and not llm_variable_mapping_warning
        )
        llm_effective_metric_mapping_passed = bool(
            len(llm_dropped_constraints) == 0 and len(llm_unsupported_metrics) == 0
        )
        llm_effective_passed = bool(
            llm_effective_variable_mapping_passed
            and llm_effective_metric_mapping_passed
        )
        llm_effective_report = {
            "passed": bool(llm_effective_passed),
            "variable_mapping_passed": bool(llm_effective_variable_mapping_passed),
            "metric_mapping_passed": bool(llm_effective_metric_mapping_passed),
            "parsed_variables": int(llm_parsed_variables),
            "dropped_constraints_count": int(len(llm_dropped_constraints)),
            "unsupported_metrics_count": int(len(llm_unsupported_metrics)),
            "variable_mapping_warning": bool(llm_variable_mapping_warning),
            "dropped_constraints": list(llm_dropped_constraints),
            "unsupported_metrics": list(llm_unsupported_metrics),
        }
        final_state.metadata["llm_effective_report"] = dict(llm_effective_report)
        persisted_rag_ingest_store_path = host.logger.serialize_artifact_path(
            str(rag_ingest_report.get("store_path", "") or "")
        )
        host.logger.save_summary(
            status=summary_status,
            final_iteration=iteration,
            notes=(
                f"{runtime_mode} pipeline completed. "
                f"attempts={len(maas_attempts)}, "
                f"diagnosis={last_diagnosis.get('status')}, "
                f"reason={last_diagnosis.get('reason')}, "
                f"solver_message={last_execution_result.message if last_execution_result else last_solver_exception}"
            ),
            extra={
                "optimization_mode": runtime_mode,
                "pymoo_algorithm": str(pymoo_algorithm),
                "thermal_evaluator_mode": thermal_evaluator_mode,
                "diagnosis_status": last_diagnosis.get("status"),
                "diagnosis_reason": last_diagnosis.get("reason"),
                "maas_attempt_count": len(maas_attempts),
                "maas_attempt_budget": int(maas_max_attempts),
                "maas_attempt_budget_base": int(maas_base_attempts),
                "maas_attempt_budget_report": dict(dynamic_attempt_report),
                "mcts_stop_reason": str(mcts_report.get("stop_reason", "")),
                "search_space": summary_search_space or None,
                "dominant_violation": summary_attempt_payload.get("dominant_violation"),
                "constraint_violation_breakdown": summary_attempt_payload.get("constraint_violation_breakdown"),
                "best_candidate_metrics": summary_attempt_payload.get("best_candidate_metrics"),
                "operator_bias": summary_attempt_payload.get("operator_bias"),
                "operator_credit_snapshot": summary_attempt_payload.get("operator_credit_snapshot"),
                "modeling_intent_source": str(modeling_intent_diagnostics.get("source", "")),
                "modeling_intent_called": bool(modeling_intent_diagnostics.get("called", False)),
                "modeling_intent_api_call_attempted": bool(
                    modeling_intent_diagnostics.get("api_call_attempted", False)
                ),
                "modeling_intent_api_call_succeeded": bool(
                    modeling_intent_diagnostics.get("api_call_succeeded", False)
                ),
                "modeling_intent_used_fallback": bool(
                    modeling_intent_diagnostics.get("used_fallback", False)
                ),
                "modeling_intent_fallback_reason": str(
                    modeling_intent_diagnostics.get("fallback_reason", "")
                ),
                "modeling_intent_autofill_used": bool(
                    modeling_intent_diagnostics.get("autofill_used", False)
                ),
                "llm_effective_passed": bool(llm_effective_passed),
                "llm_effective_variable_mapping_passed": bool(
                    llm_effective_variable_mapping_passed
                ),
                "llm_effective_metric_mapping_passed": bool(
                    llm_effective_metric_mapping_passed
                ),
                "llm_effective_parsed_variables": int(llm_parsed_variables),
                "llm_effective_dropped_constraints_count": int(
                    len(llm_dropped_constraints)
                ),
                "llm_effective_unsupported_metrics_count": int(
                    len(llm_unsupported_metrics)
                ),
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
                "rag_phase_a_retrieval_count": int(len(phase_a_retrieval)),
                "rag_phase_d_retrieval_count": int(len(phase_d_retrieval)),
                "rag_phase_a_top_item_id": (
                    phase_a_retrieval[0]["item_id"] if phase_a_retrieval else ""
                ),
                "rag_phase_d_top_item_id": (
                    phase_d_retrieval[0]["item_id"] if phase_d_retrieval else ""
                ),
                "rag_runtime_ingest_enabled": bool(
                    rag_ingest_report.get("enabled", False)
                ),
                "rag_ingest_candidate_count": int(
                    rag_ingest_report.get("candidate_count", 0) or 0
                ),
                "rag_ingest_selected_attempt_count": int(
                    rag_ingest_report.get("selected_attempt_count", 0) or 0
                ),
                "rag_ingest_attempted_count": int(
                    rag_ingest_report.get("attempted_count", 0) or 0
                ),
                "rag_ingested_count": int(
                    rag_ingest_report.get("ingested_count", 0) or 0
                ),
                "rag_ingest_selected_attempts": list(
                    rag_ingest_report.get("selected_attempts", []) or []
                ),
                "rag_ingest_selected_reasons": dict(
                    rag_ingest_report.get("selected_reasons", {}) or {}
                ),
                "rag_ingest_store_path": persisted_rag_ingest_store_path,
                "rag_ingest_before_total": rag_ingest_report.get("before_total", None),
                "rag_ingest_after_total": rag_ingest_report.get("after_total", None),
                "rag_ingest_error": str(rag_ingest_report.get("error", "") or ""),
                "rag_ingest_skipped_reason": str(
                    rag_ingest_report.get("skipped_reason", "") or ""
                ),
                "final_mph_path": persisted_final_mph_path,
                "source_gate_mode": str(source_gate_report.get("mode", "")),
                "source_gate_passed": bool(source_gate_report.get("passed", True)),
                "source_gate_strict_blocked": bool(source_gate_report.get("strict_blocked", False)),
                "source_gate_real_only": bool(source_gate_report.get("real_only", False)),
                "source_gate_require_structural_real": bool(
                    source_gate_report.get("require_structural_real", False)
                ),
                "source_gate_require_power_real": bool(
                    source_gate_report.get("require_power_real", False)
                ),
                "source_gate_require_thermal_real": bool(
                    source_gate_report.get("require_thermal_real", False)
                ),
                "source_gate_require_mission_real": bool(
                    source_gate_report.get("require_mission_real", False)
                ),
                "source_gate_structural_passed": bool(
                    source_gate_report.get("structural_passed", True)
                ),
                "source_gate_power_passed": bool(source_gate_report.get("power_passed", True)),
                "source_gate_thermal_passed": bool(source_gate_report.get("thermal_passed", True)),
                "source_gate_mission_passed": bool(source_gate_report.get("mission_passed", True)),
                "source_gate_thermal_source": str(source_gate_report.get("thermal_source", "")),
                "source_gate_mission_source": str(source_gate_report.get("mission_source", "")),
                "source_gate_structural_missing_real_metrics": list(
                    source_gate_report.get("structural_missing_real_metrics", []) or []
                ),
                "source_gate_power_missing_real_metrics": list(
                    source_gate_report.get("power_missing_real_metrics", []) or []
                ),
                "source_gate_real_only_reasons": list(
                    source_gate_report.get("real_only_reasons", []) or []
                ),
                "source_gate_comsol_feature_domain_audit_mode": str(
                    source_gate_report.get("comsol_feature_domain_audit_mode", "")
                ),
                "source_gate_comsol_feature_domain_audit_required": bool(
                    source_gate_report.get("comsol_feature_domain_audit_required", False)
                ),
                "source_gate_comsol_feature_domain_audit_present": bool(
                    source_gate_report.get("comsol_feature_domain_audit_present", False)
                ),
                "source_gate_comsol_feature_domain_audit_passed": bool(
                    source_gate_report.get("comsol_feature_domain_audit_passed", True)
                ),
                "source_gate_comsol_feature_domain_audit_failed_checks": list(
                    source_gate_report.get("comsol_feature_domain_audit_failed_checks", []) or []
                ),
                "operator_family_gate_mode": str(operator_family_gate_report.get("mode", "")),
                "operator_family_gate_passed": bool(operator_family_gate_report.get("passed", True)),
                "operator_family_gate_strict_blocked": bool(
                    operator_family_gate_report.get("strict_blocked", False)
                ),
                "operator_family_gate_required": list(
                    operator_family_gate_report.get("required_families", []) or []
                ),
                "operator_family_gate_covered": list(
                    operator_family_gate_report.get("covered_families", []) or []
                ),
                "operator_family_gate_missing": list(
                    operator_family_gate_report.get("missing_families", []) or []
                ),
                "operator_realization_gate_mode": str(
                    operator_realization_gate_report.get("mode", "")
                ),
                "operator_realization_gate_passed": bool(
                    operator_realization_gate_report.get("passed", True)
                ),
                "operator_realization_gate_strict_blocked": bool(
                    operator_realization_gate_report.get("strict_blocked", False)
                ),
                "operator_realization_gate_required": list(
                    operator_realization_gate_report.get("required_families", []) or []
                ),
                "operator_realization_gate_realized": list(
                    operator_realization_gate_report.get("realized_families", []) or []
                ),
                "operator_realization_gate_missing": list(
                    operator_realization_gate_report.get("missing_realized_families", []) or []
                ),
                "operator_realization_gate_non_real_actions_by_family": dict(
                    operator_realization_gate_report.get("non_real_actions_by_family", {}) or {}
                ),
                "operator_realization_gate_thermal_evidence_missing_actions": list(
                    operator_realization_gate_report.get("thermal_evidence_missing_actions", []) or []
                ),
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
                "optimization_mode": runtime_mode,
                "pymoo_algorithm": str(pymoo_algorithm),
                "thermal_evaluator_mode": str(thermal_evaluator_mode),
                "search_space_mode": summary_search_space or "",
                "status": str(summary_status),
                "final_iteration": int(iteration),
                "extra": {
                    "diagnosis_status": str(last_diagnosis.get("status", "")),
                    "diagnosis_reason": str(last_diagnosis.get("reason", "")),
                    "attempt_count": int(len(maas_attempts)),
                    "final_mph_path": persisted_final_mph_path,
                    "source_gate_mode": str(source_gate_report.get("mode", "")),
                    "source_gate_passed": bool(source_gate_report.get("passed", True)),
                    "source_gate_strict_blocked": bool(
                        source_gate_report.get("strict_blocked", False)
                    ),
                    "source_gate_real_only": bool(source_gate_report.get("real_only", False)),
                    "source_gate_require_thermal_real": bool(
                        source_gate_report.get("require_thermal_real", False)
                    ),
                    "source_gate_require_mission_real": bool(
                        source_gate_report.get("require_mission_real", False)
                    ),
                    "source_gate_thermal_passed": bool(
                        source_gate_report.get("thermal_passed", True)
                    ),
                    "source_gate_mission_passed": bool(
                        source_gate_report.get("mission_passed", True)
                    ),
                    "modeling_intent_source": str(
                        modeling_intent_diagnostics.get("source", "")
                    ),
                    "modeling_intent_api_call_succeeded": bool(
                        modeling_intent_diagnostics.get("api_call_succeeded", False)
                    ),
                    "modeling_intent_used_fallback": bool(
                        modeling_intent_diagnostics.get("used_fallback", False)
                    ),
                    "llm_effective_passed": bool(llm_effective_passed),
                    "llm_effective_variable_mapping_passed": bool(
                        llm_effective_variable_mapping_passed
                    ),
                    "llm_effective_metric_mapping_passed": bool(
                        llm_effective_metric_mapping_passed
                    ),
                    "llm_effective_parsed_variables": int(llm_parsed_variables),
                    "llm_effective_dropped_constraints_count": int(
                        len(llm_dropped_constraints)
                    ),
                    "llm_effective_unsupported_metrics_count": int(
                        len(llm_unsupported_metrics)
                    ),
                    "rag_phase_a_retrieval_count": int(len(phase_a_retrieval)),
                    "rag_phase_d_retrieval_count": int(len(phase_d_retrieval)),
                    "rag_runtime_ingest_enabled": bool(
                        rag_ingest_report.get("enabled", False)
                    ),
                    "rag_ingest_candidate_count": int(
                        rag_ingest_report.get("candidate_count", 0) or 0
                    ),
                    "rag_ingest_selected_attempt_count": int(
                        rag_ingest_report.get("selected_attempt_count", 0) or 0
                    ),
                    "rag_ingest_attempted_count": int(
                        rag_ingest_report.get("attempted_count", 0) or 0
                    ),
                    "rag_ingested_count": int(
                        rag_ingest_report.get("ingested_count", 0) or 0
                    ),
                    "rag_ingest_selected_attempts": list(
                        rag_ingest_report.get("selected_attempts", []) or []
                    ),
                    "rag_ingest_selected_reasons": dict(
                        rag_ingest_report.get("selected_reasons", {}) or {}
                    ),
                    "rag_ingest_store_path": persisted_rag_ingest_store_path,
                    "rag_ingest_before_total": rag_ingest_report.get(
                        "before_total", None
                    ),
                    "rag_ingest_after_total": rag_ingest_report.get(
                        "after_total", None
                    ),
                    "rag_ingest_error": str(rag_ingest_report.get("error", "") or ""),
                    "rag_ingest_skipped_reason": str(
                        rag_ingest_report.get("skipped_reason", "") or ""
                    ),
                    "source_gate_structural_missing_real_metrics": list(
                        source_gate_report.get("structural_missing_real_metrics", []) or []
                    ),
                    "source_gate_power_missing_real_metrics": list(
                        source_gate_report.get("power_missing_real_metrics", []) or []
                    ),
                    "source_gate_real_only_reasons": list(
                        source_gate_report.get("real_only_reasons", []) or []
                    ),
                    "source_gate_comsol_feature_domain_audit_mode": str(
                        source_gate_report.get("comsol_feature_domain_audit_mode", "")
                    ),
                    "source_gate_comsol_feature_domain_audit_required": bool(
                        source_gate_report.get("comsol_feature_domain_audit_required", False)
                    ),
                    "source_gate_comsol_feature_domain_audit_present": bool(
                        source_gate_report.get("comsol_feature_domain_audit_present", False)
                    ),
                    "source_gate_comsol_feature_domain_audit_passed": bool(
                        source_gate_report.get("comsol_feature_domain_audit_passed", True)
                    ),
                    "source_gate_comsol_feature_domain_audit_failed_checks": list(
                        source_gate_report.get("comsol_feature_domain_audit_failed_checks", []) or []
                    ),
                    "operator_family_gate_mode": str(
                        operator_family_gate_report.get("mode", "")
                    ),
                    "operator_family_gate_passed": bool(
                        operator_family_gate_report.get("passed", True)
                    ),
                    "operator_family_gate_strict_blocked": bool(
                        operator_family_gate_report.get("strict_blocked", False)
                    ),
                    "operator_family_gate_required": list(
                        operator_family_gate_report.get("required_families", []) or []
                    ),
                    "operator_family_gate_covered": list(
                        operator_family_gate_report.get("covered_families", []) or []
                    ),
                    "operator_family_gate_missing": list(
                        operator_family_gate_report.get("missing_families", []) or []
                    ),
                    "operator_realization_gate_mode": str(
                        operator_realization_gate_report.get("mode", "")
                    ),
                    "operator_realization_gate_passed": bool(
                        operator_realization_gate_report.get("passed", True)
                    ),
                    "operator_realization_gate_strict_blocked": bool(
                        operator_realization_gate_report.get("strict_blocked", False)
                    ),
                    "operator_realization_gate_missing": list(
                        operator_realization_gate_report.get("missing_realized_families", []) or []
                    ),
                    "operator_realization_gate_non_real_actions_by_family": dict(
                        operator_realization_gate_report.get("non_real_actions_by_family", {}) or {}
                    ),
                    "operator_realization_gate_thermal_evidence_missing_actions": list(
                        operator_realization_gate_report.get("thermal_evidence_missing_actions", []) or []
                    ),
                    "observability_tables": observability_tables,
                    "observability_tables_error": observability_tables_error,
                },
            }
        )
        runtime.generate_final_report(final_state, iteration)
        host.logger.logger.info(
            "mass pipeline completed. "
            f"attempts={len(maas_attempts)}, "
            f"diagnosis={last_diagnosis.get('status')}"
        )
        return final_state

