"""
Experimental VOP-MaaS mode service.

Current behavior:
- build a structured VOP graph from the current multiphysics state,
- generate / validate / screen a bounded operator-policy pack,
- inject policy priors into the delegated MaaS executor,
- optionally run one bounded reflective replanning round,
- fall back to plain mass execution when policy generation is unavailable.
"""

from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Tuple

from core.final_summary_zh import generate_vop_final_summary_zh
from core.protocol import DesignState
from optimization.modes.mass.observability.materialize import (
    materialize_observability_tables,
    persist_vop_round_events,
)

from .contracts import (
    VOPGraph,
    VOPPolicyFeedback,
    VOPReflectiveReplanReport,
    validate_vop_policy_pack,
)
from .policy_compiler import build_mock_policy_pack, screen_policy_pack
from .policy_context import build_vop_graph

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


@dataclass
class PolicyRoundArtifacts:
    """One bounded VOP policy round before delegating to MaaS."""

    round_index: int
    stage: str
    vop_graph: VOPGraph
    generation: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    screening: Dict[str, Any] = field(default_factory=dict)
    runtime_policy_priors: Dict[str, Any] = field(default_factory=dict)
    policy_applied: bool = False
    fallback_reason: str = ""
    bootstrap_error: str = ""
    policy_pack_payload: Dict[str, Any] = field(default_factory=dict)
    feedback_aware_fidelity_plan: Dict[str, Any] = field(default_factory=dict)
    feedback_aware_fidelity_reason: str = ""

    @property
    def policy_id(self) -> str:
        for payload in (
            self.runtime_policy_priors,
            self.policy_pack_payload,
            dict(self.generation.get("policy_pack", {}) or {}),
        ):
            policy_id = str(dict(payload or {}).get("policy_id", "") or "").strip()
            if policy_id:
                return policy_id
        return ""


@dataclass
class VOPPolicyProgramService:
    """Experimental LLM/operator-policy adapter with safe mass fallback."""

    host: "WorkflowOrchestrator"

    @staticmethod
    def _normalize_focus_tokens(raw_items: Any) -> set[str]:
        items = raw_items if isinstance(raw_items, (list, tuple, set)) else [raw_items]
        normalized: set[str] = set()
        for raw in items:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            normalized.add(token)
            if any(key in token for key in ("thermal", "temp", "heat")):
                normalized.update({"thermal", "max_temp"})
            if (
                any(key in token for key in ("struct", "stress", "modal", "safety"))
                or token in {"max_stress", "first_modal_freq", "safety_factor"}
            ):
                normalized.add("structural")
                if "stress" in token or token == "max_stress":
                    normalized.add("max_stress")
                if "modal" in token or token == "first_modal_freq":
                    normalized.add("first_modal_freq")
                if "safety" in token or token == "safety_factor":
                    normalized.add("safety_factor")
            if (
                any(key in token for key in ("power", "voltage", "current", "vdrop"))
                or token in {"voltage_drop", "power_margin", "peak_power"}
            ):
                normalized.add("power")
                if "voltage" in token or "vdrop" in token or token == "voltage_drop":
                    normalized.add("voltage_drop")
                if "margin" in token or token == "power_margin":
                    normalized.add("power_margin")
                if "peak" in token or token == "peak_power":
                    normalized.add("peak_power")
            if any(key in token for key in ("mission", "keepout", "fov")):
                normalized.update({"mission", "mission_keepout_violation"})
            if (
                token in {"geometry", "collision", "clearance", "boundary", "cg", "cg_limit", "cg_offset"}
                or any(key in token for key in ("clearance", "collision", "boundary", "cg_"))
            ):
                normalized.add("geometry")
        return normalized

    def _scenario_level_focus_hints(self) -> set[str]:
        level_tag = str(
            dict(self.host.config.get("optimization", {}) or {}).get("mass_level_tag", "") or ""
        ).strip().upper()
        mapping = {
            "L2": {"thermal", "max_temp", "power", "voltage_drop", "power_margin"},
            "L3": {
                "structural",
                "max_stress",
                "safety_factor",
                "first_modal_freq",
                "power",
                "voltage_drop",
                "power_margin",
            },
            "L4": {
                "thermal",
                "max_temp",
                "structural",
                "max_stress",
                "safety_factor",
                "first_modal_freq",
                "power",
                "voltage_drop",
                "power_margin",
            },
        }
        return set(mapping.get(level_tag, set()))

    def _current_runtime_fidelity_floor(self) -> Dict[str, Any]:
        opt_cfg = dict(self.host.config.get("optimization", {}) or {})
        simulation_backend = str(
            dict(self.host.config.get("simulation", {}) or {}).get("backend", "") or ""
        ).strip().lower()
        floor: Dict[str, Any] = {}
        thermal_mode = str(opt_cfg.get("mass_thermal_evaluator_mode", "") or "").strip().lower()
        if simulation_backend == "comsol" and thermal_mode == "online_comsol":
            floor["thermal_evaluator_mode"] = "online_comsol"
            floor["online_comsol_eval_budget"] = max(
                0,
                int(opt_cfg.get("mass_online_comsol_eval_budget", 0) or 0),
            )
        if bool(opt_cfg.get("mass_enable_physics_audit", True)):
            floor["physics_audit_top_k"] = max(int(opt_cfg.get("mass_audit_top_k", 1) or 1), 1)
        return floor

    def _apply_runtime_fidelity_floor(self, policy_payload: Dict[str, Any]) -> Dict[str, Any]:
        target = dict(policy_payload or {})
        floor = dict(self._current_runtime_fidelity_floor() or {})
        if not floor:
            return target

        fidelity_plan = dict(target.get("fidelity_plan", {}) or {})
        metadata = dict(target.get("metadata", {}) or {})
        applied: Dict[str, Any] = {}

        floor_mode = str(floor.get("thermal_evaluator_mode", "") or "").strip().lower()
        current_mode = str(fidelity_plan.get("thermal_evaluator_mode", "") or "").strip().lower()
        if floor_mode == "online_comsol" and current_mode != "online_comsol":
            fidelity_plan["thermal_evaluator_mode"] = "online_comsol"
            applied["thermal_evaluator_mode"] = "online_comsol"

        for key in ("online_comsol_eval_budget", "physics_audit_top_k"):
            if key not in floor:
                continue
            floor_value = int(floor.get(key, 0) or 0)
            current_value = int(fidelity_plan.get(key, 0) or 0)
            if floor_value > current_value:
                fidelity_plan[key] = int(floor_value)
                applied[key] = int(floor_value)

        if fidelity_plan:
            target["fidelity_plan"] = fidelity_plan
        if applied:
            metadata["runtime_fidelity_floor_applied"] = dict(applied)
            target["metadata"] = metadata
        return target

    def _load_runtime_artifact_snapshot(self) -> Dict[str, Any]:
        run_dir = Path(str(getattr(self.host.logger, "run_dir", "") or "").strip())
        if not run_dir:
            return {}

        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return dict(payload)
            except Exception:
                pass

        manifest_path = run_dir / "events" / "run_manifest.json"
        if manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                extra = dict(payload.get("extra", {}) or {})
                if extra:
                    return extra
            except Exception:
                pass

        return {}

    def run_pipeline(
        self,
        *,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        host = self.host
        runtime = getattr(host, "runtime_facade", None)
        if runtime is None:
            raise RuntimeError("runtime_facade is not configured")

        host.logger.logger.info(
            "Entering vop_maas experimental mode: "
            "VOP graph -> policy pack -> screened mass execution -> bounded reflective replanning"
        )

        requirement_text = runtime.build_maas_requirement_text(bom_file)
        opt_cfg = dict(host.config.get("optimization", {}) or {})
        max_candidates = max(1, int(opt_cfg.get("vop_policy_max_candidates", 3)))
        screening_enabled = bool(opt_cfg.get("vop_policy_screening_enabled", True))
        screening_top_k = max(1, int(opt_cfg.get("vop_policy_screening_top_k", 1)))
        strict_validation = bool(opt_cfg.get("vop_policy_validation_strict", False))
        mock_policy_enabled = bool(opt_cfg.get("vop_mock_policy_enabled", False))
        reflective_replan_enabled = bool(opt_cfg.get("vop_reflective_replan_enabled", True))
        feedback_aware_fidelity_enabled = bool(
            opt_cfg.get("vop_feedback_aware_fidelity_enabled", True)
        )
        reflective_replan_max_rounds = min(
            1,
            max(0, int(opt_cfg.get("vop_reflective_replan_max_rounds", 1) or 1)),
        )

        bootstrap_context, bootstrap_graph, bootstrap_error = self._bootstrap_vop_context(
            current_state=current_state,
            iteration=1,
            phase_label="V0",
        )
        initial_round = self._prepare_policy_round(
            current_state=current_state,
            context=bootstrap_context,
            vop_graph=bootstrap_graph,
            requirement_text=requirement_text,
            round_index=0,
            stage="bootstrap",
            max_candidates=max_candidates,
            screening_enabled=screening_enabled,
            screening_top_k=screening_top_k,
            strict_validation=strict_validation,
            mock_policy_enabled=mock_policy_enabled,
            bootstrap_error=bootstrap_error,
        )
        self._log_policy_round(
            policy_round=initial_round,
            requirement_text=requirement_text,
            previous_policy_pack=None,
            policy_effect_summary=None,
            replan_reason="",
        )

        active_round = initial_round
        self._emit_vop_run_log(
            "[VOP][DELEGATE]",
            "delegating bootstrap policy to mass",
            round_index=active_round.round_index,
            policy_id=active_round.policy_id,
            execution_mode="mass",
            search_space=(
                dict(active_round.runtime_policy_priors or {}).get("search_space_prior", "")
            ),
        )
        final_state = self._run_mass_with_isolated_policy(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
            policy_priors=(active_round.runtime_policy_priors if active_round.policy_applied else None),
        )
        initial_feedback = self._build_policy_feedback(
            final_state=final_state,
            policy_priors=active_round.runtime_policy_priors,
            fallback_reason=active_round.fallback_reason,
        )
        self._emit_vop_run_log(
            "[VOP][EFFECT]",
            "bootstrap delegated mass completed",
            policy_id=active_round.policy_id,
            diagnosis=initial_feedback.diagnosis_status,
            reason=initial_feedback.diagnosis_reason,
            search_space_effect=self._derive_search_space_effect(initial_feedback),
            first_feasible_eval=initial_feedback.first_feasible_eval,
            comsol_calls=initial_feedback.comsol_calls_to_first_feasible,
        )
        policy_round_records = [
            self._serialize_policy_round(
                initial_round,
                policy_feedback=initial_feedback,
                mass_rerun_executed=True,
                skipped_reason="",
                previous_policy_pack=None,
                replan_reason="",
            )
        ]

        replan_report = VOPReflectiveReplanReport(
            enabled=bool(reflective_replan_enabled),
            rounds_requested=int(reflective_replan_max_rounds),
            previous_policy_id=str(initial_round.policy_id or ""),
            final_policy_id=str(initial_round.policy_id or ""),
        )
        active_feedback = initial_feedback

        if reflective_replan_enabled and reflective_replan_max_rounds > 0:
            should_replan, trigger_reason = self._should_trigger_reflective_replan(
                policy_round=initial_round,
                policy_feedback=initial_feedback,
            )
            if should_replan:
                self._emit_vop_run_log(
                    "[VOP][REPLAN]",
                    "reflective replan triggered",
                    previous_policy_id=initial_round.policy_id,
                    reason=trigger_reason,
                    failure_signature=initial_feedback.failure_signature,
                )
                replan_report.triggered = True
                replan_report.trigger_reason = str(trigger_reason or "")
                replan_state = final_state.model_copy(deep=True)
                replan_state.metadata = {}
                replan_context, replan_graph, replan_bootstrap_error = self._bootstrap_vop_context(
                    current_state=replan_state,
                    iteration=2,
                    phase_label="V1",
                )
                feedback_aware_fidelity = self._derive_feedback_aware_fidelity_plan(
                    policy_feedback=initial_feedback,
                    previous_policy_pack=initial_round.policy_pack_payload,
                    vop_graph=replan_graph,
                    enabled=feedback_aware_fidelity_enabled,
                )
                reflective_round = self._prepare_policy_round(
                    current_state=replan_state,
                    context=replan_context,
                    vop_graph=replan_graph,
                    requirement_text=requirement_text,
                    round_index=1,
                    stage="reflective_replan",
                    max_candidates=max_candidates,
                    screening_enabled=screening_enabled,
                    screening_top_k=screening_top_k,
                    strict_validation=strict_validation,
                    mock_policy_enabled=mock_policy_enabled,
                    bootstrap_error=replan_bootstrap_error,
                    previous_policy_pack=initial_round.policy_pack_payload,
                    policy_effect_summary=initial_feedback.model_dump(),
                    replan_reason=trigger_reason,
                    feedback_aware_fidelity_plan=dict(
                        feedback_aware_fidelity.get("plan", {}) or {}
                    ),
                    feedback_aware_fidelity_reason=str(
                        feedback_aware_fidelity.get("reason", "") or ""
                    ),
                )
                replan_report.candidate_policy_id = str(reflective_round.policy_id or "")
                self._log_policy_round(
                    policy_round=reflective_round,
                    requirement_text=requirement_text,
                    previous_policy_pack=initial_round.policy_pack_payload,
                    policy_effect_summary=initial_feedback.model_dump(),
                    replan_reason=trigger_reason,
                )

                reflective_feedback: Optional[VOPPolicyFeedback] = None
                mass_rerun_executed = False
                skipped_reason = ""
                if not reflective_round.policy_applied:
                    skipped_reason = (
                        str(reflective_round.fallback_reason or "")
                        or "reflective_policy_not_applied"
                    )
                elif not self._has_material_policy_change(
                    initial_round.runtime_policy_priors,
                    reflective_round.runtime_policy_priors,
                ):
                    skipped_reason = "reflective_policy_no_material_change"
                else:
                    self._mark_vop_run_in_progress(
                        stage="reflective_replan",
                        round_index=int(reflective_round.round_index),
                        trigger_reason=str(trigger_reason or ""),
                    )
                    mass_rerun_executed = True
                    self._emit_vop_run_log(
                        "[VOP][DELEGATE]",
                        "delegating reflective policy to mass",
                        round_index=reflective_round.round_index,
                        policy_id=reflective_round.policy_id,
                        execution_mode="mass",
                        search_space=(
                            dict(reflective_round.runtime_policy_priors or {}).get(
                                "search_space_prior", ""
                            )
                        ),
                    )
                    final_state = self._run_mass_with_isolated_policy(
                        current_state=replan_state,
                        bom_file=bom_file,
                        max_iterations=max_iterations,
                        convergence_threshold=convergence_threshold,
                        policy_priors=reflective_round.runtime_policy_priors,
                    )
                    active_round = reflective_round
                    active_feedback = self._build_policy_feedback(
                        final_state=final_state,
                        policy_priors=active_round.runtime_policy_priors,
                        fallback_reason=active_round.fallback_reason,
                    )
                    reflective_feedback = active_feedback
                    self._emit_vop_run_log(
                        "[VOP][EFFECT]",
                        "reflective delegated mass completed",
                        policy_id=active_round.policy_id,
                        diagnosis=active_feedback.diagnosis_status,
                        reason=active_feedback.diagnosis_reason,
                        search_space_effect=self._derive_search_space_effect(active_feedback),
                        first_feasible_eval=active_feedback.first_feasible_eval,
                        comsol_calls=active_feedback.comsol_calls_to_first_feasible,
                    )
                    replan_report.rounds_completed = 1
                    replan_report.executed_mass_rerun = True
                    replan_report.final_policy_id = str(active_round.policy_id or "")

                if not mass_rerun_executed:
                    self._emit_vop_run_log(
                        "[VOP][REPLAN]",
                        "reflective rerun skipped",
                        policy_id=reflective_round.policy_id,
                        reason=skipped_reason or trigger_reason,
                    )
                    replan_report.skipped_reason = str(skipped_reason or "")
                    replan_report.final_policy_id = str(active_round.policy_id or "")
                policy_round_records.append(
                    self._serialize_policy_round(
                        reflective_round,
                        policy_feedback=reflective_feedback,
                        mass_rerun_executed=mass_rerun_executed,
                        skipped_reason=skipped_reason,
                        previous_policy_pack=initial_round.policy_pack_payload,
                        replan_reason=trigger_reason,
                    )
                )
            else:
                replan_report.skipped_reason = str(trigger_reason or "")
        else:
            replan_report.skipped_reason = "reflective_replan_disabled"

        round_audit_rows = self._build_round_audit_rows(
            active_round=active_round,
            replan_report=replan_report,
            policy_round_records=policy_round_records,
        )
        observability_tables, observability_tables_error = self._refresh_vop_round_observability(
            round_audit_rows
        )
        round_audit_table_path = str(
            observability_tables.get("vop_rounds_path", "") or "tables/vop_rounds.csv"
        )
        metadata = dict(getattr(final_state, "metadata", {}) or {})
        metadata["optimization_mode"] = "vop_maas"
        metadata["vop_maas_experimental_mode"] = True
        metadata["vop_execution_backend_mode"] = "mass"
        metadata["vop_policy_graph"] = bootstrap_graph.model_dump()
        metadata["vop_active_policy_graph"] = active_round.vop_graph.model_dump()
        metadata["vop_policy_generation"] = self._to_jsonable(active_round.generation)
        metadata["vop_policy_validation"] = self._to_jsonable(active_round.validation)
        metadata["vop_policy_screening"] = self._to_jsonable(active_round.screening)
        metadata["vop_policy_priors"] = self._to_jsonable(active_round.runtime_policy_priors)
        metadata["vop_policy_applied"] = bool(active_round.policy_applied)
        metadata["vop_policy_fallback_reason"] = str(active_round.fallback_reason or "")
        metadata["vop_policy_feedback"] = self._to_jsonable(active_feedback.model_dump())
        metadata["vop_policy_rounds"] = self._to_jsonable(policy_round_records)
        metadata["delegated_execution_mode"] = "mass"
        if observability_tables:
            metadata["observability_tables"] = dict(observability_tables)
        if observability_tables_error:
            metadata["observability_tables_error"] = str(observability_tables_error)
        active_previous_policy_pack = (
            initial_round.policy_pack_payload if int(active_round.round_index) > 0 else None
        )
        vop_audit_summary = self._build_vop_audit_summary(
            active_round=active_round,
            active_feedback=active_feedback,
            previous_policy_pack=active_previous_policy_pack,
            replan_report=replan_report,
            round_audit_rows=round_audit_rows,
            round_audit_table_path=round_audit_table_path,
            final_audit_status=str(metadata.get("final_audit_status", "") or ""),
        )
        metadata.update(vop_audit_summary)
        final_state.metadata = metadata

        summary_update_payload = dict(vop_audit_summary)
        summary_update_payload.update(
            {
                "optimization_mode": "vop_maas",
                "run_mode": "vop_maas",
                "execution_mode": "mass",
                "delegated_execution_mode": "mass",
            }
        )
        if observability_tables:
            summary_update_payload["observability_tables"] = dict(observability_tables)
        if observability_tables_error:
            summary_update_payload["observability_tables_error"] = str(
                observability_tables_error
            )
        self._persist_vop_summary_fields(summary_update_payload)
        self._persist_vop_report_section(vop_audit_summary)
        manifest_extra = self._load_existing_manifest_extra()
        manifest_extra.update(
            {
                "delegated_execution_mode": "mass",
                "policy_status": str(active_round.generation.get("status", "")),
                "policy_validation_state": str(active_round.validation.get("state", "")),
                "policy_applied": bool(active_round.policy_applied),
                "policy_fallback_reason": str(active_round.fallback_reason or ""),
                "vop_policy_applied": bool(active_round.policy_applied),
                "vop_policy_id": str(active_round.policy_id or ""),
                "vop_policy_primary_round_index": int(active_round.round_index),
                "vop_policy_primary_round_key": str(
                    vop_audit_summary.get("vop_policy_primary_round_key", "") or ""
                ),
                "vop_round_count": int(vop_audit_summary.get("vop_round_count", 0) or 0),
                "vop_round_audit_table": str(
                    vop_audit_summary.get("vop_round_audit_table", "") or ""
                ),
                "vop_round_audit_digest": list(
                    vop_audit_summary.get("vop_round_audit_digest", []) or []
                ),
                "vop_feedback_aware_fidelity_plan": dict(
                    vop_audit_summary.get("vop_feedback_aware_fidelity_plan", {}) or {}
                ),
                "vop_feedback_aware_fidelity_reason": str(
                    vop_audit_summary.get("vop_feedback_aware_fidelity_reason", "") or ""
                ),
                "vop_reflective_replanning": dict(
                    vop_audit_summary.get("vop_reflective_replanning", {}) or {}
                ),
                "vop_decision_summary": dict(
                    vop_audit_summary.get("vop_decision_summary", {}) or {}
                ),
                "vop_delegated_effect_summary": dict(
                    vop_audit_summary.get("vop_delegated_effect_summary", {}) or {}
                ),
                "feedback_aware_fidelity_plan": dict(
                    active_round.feedback_aware_fidelity_plan or {}
                ),
                "feedback_aware_fidelity_reason": str(
                    active_round.feedback_aware_fidelity_reason or ""
                ),
                "reflective_replan_triggered": bool(replan_report.triggered),
                "reflective_replan_trigger_reason": str(replan_report.trigger_reason or ""),
                "reflective_replan_rounds_completed": int(replan_report.rounds_completed),
                "reflective_replan_skipped_reason": str(replan_report.skipped_reason or ""),
                "final_policy_id": str(replan_report.final_policy_id or active_round.policy_id),
                "observability_tables": dict(observability_tables or {}),
                "observability_tables_error": str(observability_tables_error or ""),
            }
        )
        host.logger.save_run_manifest(
            {
                "optimization_mode": "vop_maas",
                "status": "COMPLETED",
                "extra": manifest_extra,
            }
        )
        try:
            final_summary_result = generate_vop_final_summary_zh(
                host.logger.run_dir,
                llm_gateway=getattr(host, "llm_gateway", None),
                llm_profile_name=str(
                    getattr(getattr(host, "active_text_llm_profile", None), "name", "") or ""
                ),
                log_llm_interaction=host.logger.log_llm_interaction,
                runtime_config=dict(getattr(host, "config", {}) or {}),
            )
            if bool(final_summary_result.get("generated", False)):
                final_summary_fields = dict(
                    final_summary_result.get("summary_fields", {}) or {}
                )
                if final_summary_fields:
                    metadata.update(final_summary_fields)
                    final_state.metadata = metadata
                host.logger.append_llm_final_summary_report_section(
                    final_summary_result.get("summary", {})
                )
        except Exception as exc:
            host.logger.logger.warning(
                "vop llm_final_summary_zh generation failed: %s", exc
            )
        return final_state

    def _bootstrap_vop_context(
        self,
        *,
        current_state: DesignState,
        iteration: int,
        phase_label: str,
    ) -> Tuple[Any, VOPGraph, str]:
        host = self.host
        runtime = getattr(host, "runtime_facade", None)
        if runtime is None:
            raise RuntimeError("runtime_facade is not configured")

        context: Any = {
            "iteration": int(iteration),
            "mode": "vop_maas",
            "phase": str(phase_label or ""),
        }
        bootstrap_error = ""
        try:
            metrics, violations = runtime.evaluate_design(current_state, int(iteration))
            context = runtime.build_global_context(
                iteration=int(iteration),
                design_state=current_state,
                metrics=metrics,
                violations=violations,
                phase=str(phase_label or "A"),
            )
            vop_graph = build_vop_graph(
                context=context,
                metrics=dict(metrics or {}),
                runtime_constraints=dict(host.runtime_constraints or {}),
                component_ids=self._collect_component_ids(current_state),
                simulation_backend=str(
                    host.config.get("simulation", {}).get("backend", "") or ""
                ).strip().lower(),
                retrieval_items=len(list(getattr(context, "retrieved_knowledge", []) or [])),
            )
            vop_graph.metadata["level_focus_hint"] = sorted(self._scenario_level_focus_hints())
            vop_graph.metadata["fidelity_floor_hint"] = dict(self._current_runtime_fidelity_floor())
            return context, vop_graph, bootstrap_error
        except Exception as exc:  # pragma: no cover - defensive path
            bootstrap_error = str(exc)
            host.logger.logger.warning(
                "vop_maas context bootstrap failed at %s, fallback to minimal graph: %s",
                phase_label,
                exc,
            )
            vop_graph = VOPGraph(
                graph_id=f"vopg_{str(phase_label or 'bootstrap').lower()}_failed",
                iteration=int(iteration),
                summary=f"bootstrap_failed:{exc}",
                metadata={
                    "simulation_backend": str(
                        host.config.get("simulation", {}).get("backend", "") or ""
                    ).strip().lower(),
                    "level_focus_hint": sorted(self._scenario_level_focus_hints()),
                    "fidelity_floor_hint": dict(self._current_runtime_fidelity_floor()),
                },
            )
            return context, vop_graph, bootstrap_error

    def _prepare_policy_round(
        self,
        *,
        current_state: DesignState,
        context: Any,
        vop_graph: VOPGraph,
        requirement_text: str,
        round_index: int,
        stage: str,
        max_candidates: int,
        screening_enabled: bool,
        screening_top_k: int,
        strict_validation: bool,
        mock_policy_enabled: bool,
        bootstrap_error: str,
        previous_policy_pack: Optional[Dict[str, Any]] = None,
        policy_effect_summary: Optional[Dict[str, Any]] = None,
        replan_reason: str = "",
        feedback_aware_fidelity_plan: Optional[Dict[str, Any]] = None,
        feedback_aware_fidelity_reason: str = "",
    ) -> PolicyRoundArtifacts:
        host = self.host
        raw_policy_payload: Dict[str, Any] = {}
        policy_generation: Dict[str, Any] = {
            "status": "not_called",
            "reason": "",
        }
        policy_validation: Dict[str, Any] = {
            "is_valid": False,
            "state": "rejected",
            "errors": [],
            "warnings": [],
        }
        screening_report: Dict[str, Any] = {}
        runtime_policy_priors: Dict[str, Any] = {}
        fallback_reason = ""
        policy_applied = False

        policy_programmer = getattr(host, "policy_programmer", None)
        if mock_policy_enabled:
            mock_policy = build_mock_policy_pack(
                graph=vop_graph,
                current_state=current_state,
                runtime_constraints=dict(host.runtime_constraints or {}),
                max_candidates=max_candidates,
            )
            if round_index > 0:
                mock_policy.policy_id = (
                    f"{str(mock_policy.policy_id or '').strip()}_R{int(round_index)}"
                )
                mock_policy.metadata["reflective_replan_round"] = int(round_index)
                if replan_reason:
                    mock_policy.metadata["reflective_replan_reason"] = str(replan_reason)
            raw_policy_payload = mock_policy.model_dump()
            policy_generation = {
                "status": "ok",
                "source": "mock_policy",
                "policy_pack": dict(raw_policy_payload),
                "stage": str(stage or ""),
                "round_index": int(round_index),
            }
        elif policy_programmer is None or not hasattr(policy_programmer, "generate_policy_program"):
            policy_generation = {
                "status": "unsupported",
                "reason": "policy_programmer_not_configured",
                "stage": str(stage or ""),
                "round_index": int(round_index),
            }
            fallback_reason = "policy_programmer_not_configured"
        else:
            try:
                generated = policy_programmer.generate_policy_program(
                    context=context,
                    runtime_constraints=dict(host.runtime_constraints or {}),
                    requirement_text=requirement_text,
                    mode="vop_maas",
                    vop_graph=vop_graph.model_dump(),
                    max_candidates=int(max_candidates),
                    previous_policy_pack=dict(previous_policy_pack or {}),
                    policy_effect_summary=dict(policy_effect_summary or {}),
                    replan_reason=str(replan_reason or ""),
                    replan_round=int(round_index),
                    feedback_aware_fidelity_plan=dict(
                        feedback_aware_fidelity_plan or {}
                    ),
                    feedback_aware_fidelity_reason=str(
                        feedback_aware_fidelity_reason or ""
                    ),
                )
                if isinstance(generated, dict):
                    policy_generation = dict(generated)
                else:
                    policy_generation = {
                        "status": "ok",
                        "policy_pack": generated,
                    }
                policy_generation["stage"] = str(stage or "")
                policy_generation["round_index"] = int(round_index)
            except Exception as exc:  # pragma: no cover - defensive path
                policy_generation = {
                    "status": "error",
                    "reason": f"policy_program_generation_failed: {exc}",
                    "stage": str(stage or ""),
                    "round_index": int(round_index),
                }
                fallback_reason = str(exc)

        if not raw_policy_payload:
            raw_policy_payload = self._extract_policy_pack_payload(policy_generation)
        raw_policy_payload = self._merge_feedback_aware_fidelity_plan(
            raw_policy_payload,
            feedback_aware_fidelity_plan=feedback_aware_fidelity_plan,
            feedback_aware_fidelity_reason=feedback_aware_fidelity_reason,
        )
        raw_policy_payload = self._apply_runtime_fidelity_floor(raw_policy_payload)

        policy_pack_payload: Dict[str, Any] = dict(raw_policy_payload or {})
        if raw_policy_payload:
            policy_validation = validate_vop_policy_pack(
                raw_policy_payload,
                component_ids=self._collect_component_ids(current_state),
                strict=strict_validation,
            )
            if policy_validation.get("is_valid", False):
                policy_pack = policy_validation["policy"]
                if screening_enabled and len(list(policy_pack.operator_candidates or [])) > 1:
                    screened = screen_policy_pack(
                        policy_pack,
                        graph=vop_graph,
                        top_k=screening_top_k,
                    )
                    policy_pack = screened["policy"]
                    screening_report = dict(screened.get("report", {}) or {})
                runtime_policy_priors = policy_pack.to_runtime_priors()
                policy_pack_payload = dict(policy_pack.model_dump())
                policy_applied = bool(runtime_policy_priors)
                if not screening_report:
                    screening_report = {
                        "dominant_family": str(vop_graph.dominant_violation_family or ""),
                        "requested_top_k": int(screening_top_k),
                        "candidate_count": int(len(list(policy_pack.operator_candidates or []))),
                        "selected_candidate_ids": [
                            str(item.candidate_id or "")
                            for item in list(policy_pack.operator_candidates or [])
                        ],
                        "candidate_scores": [],
                    }
            else:
                fallback_reason = (
                    " | ".join(list(policy_validation.get("errors", []) or []))
                    or "policy_validation_failed"
                )
        elif not fallback_reason:
            fallback_reason = str(policy_generation.get("reason", "") or "no_policy_pack")

        return PolicyRoundArtifacts(
            round_index=int(round_index),
            stage=str(stage or ""),
            vop_graph=vop_graph,
            generation=policy_generation,
            validation=policy_validation,
            screening=screening_report,
            runtime_policy_priors=runtime_policy_priors,
            policy_applied=bool(policy_applied),
            fallback_reason=str(fallback_reason or ""),
            bootstrap_error=str(bootstrap_error or ""),
            policy_pack_payload=policy_pack_payload,
            feedback_aware_fidelity_plan=dict(feedback_aware_fidelity_plan or {}),
            feedback_aware_fidelity_reason=str(feedback_aware_fidelity_reason or ""),
        )

    def _log_policy_round(
        self,
        *,
        policy_round: PolicyRoundArtifacts,
        requirement_text: str,
        previous_policy_pack: Optional[Dict[str, Any]],
        policy_effect_summary: Optional[Dict[str, Any]],
        replan_reason: str,
    ) -> None:
        host = self.host
        decision_summary = self._build_vop_decision_summary(
            policy_round,
            previous_policy_pack=previous_policy_pack,
        )
        change_summary = self._build_vop_change_summary(decision_summary)
        selected_candidate = dict(
            policy_round.runtime_policy_priors.get("selected_operator_candidate", {}) or {}
        )
        selected_program = dict(selected_candidate.get("program", {}) or {})
        round_key = self._build_round_key(
            round_index=policy_round.round_index,
            stage=policy_round.stage,
            policy_id=policy_round.policy_id,
        )
        host.logger.log_llm_interaction(
            iteration=int(policy_round.round_index + 1),
            role="vop_policy_programmer",
            mode="vop_maas",
            request={
                "mode": "vop_maas",
                "stage": str(policy_round.stage or ""),
                "round_index": int(policy_round.round_index),
                "requirement_text": requirement_text,
                "runtime_constraints": dict(host.runtime_constraints or {}),
                "bootstrap_error": str(policy_round.bootstrap_error or ""),
                "replan_reason": str(replan_reason or ""),
                "previous_policy_pack": self._to_jsonable(previous_policy_pack),
                "policy_effect_summary": self._to_jsonable(policy_effect_summary),
                "feedback_aware_fidelity_plan": self._to_jsonable(
                    policy_round.feedback_aware_fidelity_plan
                ),
                "feedback_aware_fidelity_reason": str(
                    policy_round.feedback_aware_fidelity_reason or ""
                ),
                "vop_graph": policy_round.vop_graph.model_dump(),
            },
            response={
                "generation": self._to_jsonable(policy_round.generation),
                "validation": self._to_jsonable(policy_round.validation),
                "screening": self._to_jsonable(policy_round.screening),
                "runtime_policy_priors": self._to_jsonable(policy_round.runtime_policy_priors),
                "applied_feedback_aware_fidelity_plan": self._to_jsonable(
                    policy_round.feedback_aware_fidelity_plan
                ),
            },
        )
        host.logger.log_maas_phase_event(
            {
                "iteration": int(policy_round.round_index + 1),
                "phase": f"V{int(policy_round.round_index)}",
                "status": "completed",
                "vop_round_key": str(round_key),
                "phase_family": "vop_maas",
                "phase_mode": "vop_maas",
                "producer_mode": "vop_maas",
                "round_index": int(policy_round.round_index),
                "stage": str(policy_round.stage or ""),
                "policy_id": str(policy_round.policy_id or ""),
                "previous_policy_id": str(
                    dict(previous_policy_pack or {}).get("policy_id", "") or ""
                ),
                "replan_reason": str(replan_reason or ""),
                "feedback_aware_fidelity_plan": dict(
                    policy_round.feedback_aware_fidelity_plan or {}
                ),
                "feedback_aware_fidelity_reason": str(
                    policy_round.feedback_aware_fidelity_reason or ""
                ),
                "decision_rationale": str(
                    decision_summary.get("decision_rationale", "") or ""
                ),
                "change_summary": self._to_jsonable(change_summary),
                "expected_effects": self._to_jsonable(
                    decision_summary.get("expected_effects", {})
                ),
                "delegated_execution_mode": "mass",
                "details": {
                    "mode": "vop_maas",
                    "stage": str(policy_round.stage or ""),
                    "policy_status": str(policy_round.generation.get("status", "")),
                    "policy_validation_state": str(policy_round.validation.get("state", "")),
                    "policy_applied": bool(policy_round.policy_applied),
                    "bootstrap_error": str(policy_round.bootstrap_error or ""),
                    "fallback_reason": str(policy_round.fallback_reason or ""),
                    "replan_reason": str(replan_reason or ""),
                    "feedback_aware_fidelity_plan": dict(
                        policy_round.feedback_aware_fidelity_plan or {}
                    ),
                    "decision_rationale": str(
                        decision_summary.get("decision_rationale", "") or ""
                    ),
                    "change_summary": self._to_jsonable(change_summary),
                    "expected_effects": self._to_jsonable(
                        decision_summary.get("expected_effects", {})
                    ),
                    "delegated_execution_mode": "mass",
                },
            }
        )
        host.logger.log_maas_policy_event(
            {
                "iteration": int(policy_round.round_index + 1),
                "attempt": int(policy_round.round_index),
                "round_index": int(policy_round.round_index),
                "vop_round_key": str(round_key),
                "producer_mode": "vop_maas",
                "mode": (
                    "vop_maas_bootstrap"
                    if int(policy_round.round_index) == 0
                    else f"vop_maas_reflective_round_{int(policy_round.round_index)}"
                ),
                "stage": str(policy_round.stage or ""),
                "policy_id": str(policy_round.policy_id or ""),
                "previous_policy_id": str(
                    dict(previous_policy_pack or {}).get("policy_id", "") or ""
                ),
                "policy_source": str(
                    policy_round.runtime_policy_priors.get("policy_source", "")
                    or policy_round.policy_pack_payload.get("policy_source", "")
                    or ""
                ),
                "applied": bool(policy_round.policy_applied),
                "actions": list(
                    selected_program.get("actions", []) or []
                ),
                "applied_knobs": dict(
                    policy_round.runtime_policy_priors.get("runtime_knob_priors", {}) or {}
                ),
                "constraint_focus": list(
                    policy_round.runtime_policy_priors.get("constraint_focus", []) or []
                ),
                "selected_operator_program_id": str(
                    selected_program.get("program_id", "")
                    or selected_candidate.get("candidate_id", "")
                    or ""
                ),
                "replan_reason": str(replan_reason or ""),
                "feedback_aware_fidelity_plan": dict(
                    policy_round.feedback_aware_fidelity_plan or {}
                ),
                "feedback_aware_fidelity_reason": str(
                    policy_round.feedback_aware_fidelity_reason or ""
                ),
                "search_space_override": str(
                    decision_summary.get("search_space_override", "") or ""
                ),
                "runtime_overrides": self._to_jsonable(
                    decision_summary.get("runtime_overrides", {})
                ),
                "fidelity_plan": self._to_jsonable(
                    decision_summary.get("fidelity_plan", {})
                ),
                "decision_rationale": str(
                    decision_summary.get("decision_rationale", "") or ""
                ),
                "change_summary": self._to_jsonable(change_summary),
                "expected_effects": self._to_jsonable(
                    decision_summary.get("expected_effects", {})
                ),
                "confidence": float(decision_summary.get("confidence", 0.0) or 0.0),
                "delegated_execution_mode": "mass",
            }
        )
        stage = str(policy_round.stage or "").strip().lower()
        tag = "[VOP][BOOTSTRAP]" if stage == "bootstrap" else "[VOP][REPLAN]"
        self._emit_vop_run_log(
            tag,
            "policy round prepared",
            round_index=policy_round.round_index,
            stage=policy_round.stage,
            policy_id=policy_round.policy_id,
            program=decision_summary.get("selected_operator_program_id", ""),
            search_space=decision_summary.get("search_space_override", ""),
            changes=change_summary,
            expected=decision_summary.get("expected_effects", {}),
            confidence=decision_summary.get("confidence", 0.0),
        )

    def _emit_vop_run_log(self, tag: str, message: str, **fields: Any) -> None:
        emit = getattr(self.host.logger, "_emit_run_log_milestone", None)
        if callable(emit):
            try:
                emit(tag, message, **fields)
                return
            except Exception:
                pass
        details = []
        for key, value in fields.items():
            text = self._compact_text(value)
            if text in {"", "n/a"}:
                continue
            details.append(f"{str(key).strip()}={text}")
        suffix = f" | {', '.join(details)}" if details else ""
        self.host.logger.logger.info(
            "%s %s%s",
            str(tag or "").strip(),
            str(message or "").strip(),
            suffix,
        )

    def _run_mass_with_isolated_policy(
        self,
        *,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
        policy_priors: Optional[Dict[str, Any]],
    ) -> DesignState:
        with self._isolated_optimization_config():
            return self.host.maas_pipeline_service.run_pipeline(
                current_state=current_state,
                bom_file=bom_file,
                max_iterations=int(max_iterations),
                convergence_threshold=float(convergence_threshold),
                policy_priors=(dict(policy_priors or {}) or None),
            )

    @contextmanager
    def _isolated_optimization_config(self) -> Iterator[None]:
        host = self.host
        original_optimization = deepcopy(dict(host.config.get("optimization", {}) or {}))
        try:
            yield
        finally:
            host.config["optimization"] = original_optimization

    def _build_policy_feedback(
        self,
        *,
        final_state: DesignState,
        policy_priors: Dict[str, Any],
        fallback_reason: str,
    ) -> VOPPolicyFeedback:
        metadata = dict(getattr(final_state, "metadata", {}) or {})
        artifact_snapshot = self._load_runtime_artifact_snapshot()
        for key in (
            "final_audit_status",
            "operator_family_gate_passed",
            "operator_realization_gate_passed",
            "thermal_evaluator_mode",
            "online_comsol_attempt_budget",
            "physics_audit",
        ):
            current_value = metadata.get(key, None)
            if current_value in (None, "", {}, []):
                snapshot_value = artifact_snapshot.get(key, None)
                if snapshot_value not in (None, "", {}, []):
                    metadata[key] = snapshot_value
        effect_summary = dict(metadata.get("vop_policy_effect_summary", {}) or {})
        trace_features = dict(metadata.get("maas_trace_features", {}) or {})
        compile_report = dict(metadata.get("compile_report", {}) or {})
        solver_diagnosis = dict(metadata.get("solver_diagnosis", {}) or {})
        modeling_intent_diagnostics = dict(
            metadata.get("modeling_intent_diagnostics", {}) or {}
        )
        runtime_thermal = dict(trace_features.get("runtime_thermal", {}) or {})
        physics_audit = dict(trace_features.get("physics_audit", {}) or {})
        policy_signature = self._policy_material_signature(policy_priors)
        effective_search_space = str(
            compile_report.get("search_space_mode", "")
            or metadata.get("search_space_mode", "")
            or effect_summary.get("search_space_override", "")
            or policy_priors.get("search_space_prior", "")
            or ""
        ).strip()
        diagnosis_status = str(solver_diagnosis.get("status", "") or "").strip().lower()
        trace_alerts = [
            str(item).strip().lower()
            for item in list(trace_features.get("alerts", []) or [])
            if str(item).strip()
        ]
        replan_reason = ""
        if diagnosis_status in {"no_feasible", "empty_solution", "runtime_error"}:
            replan_reason = f"diagnosis:{diagnosis_status}"
        elif diagnosis_status == "feasible_but_stalled":
            replan_reason = "diagnosis:feasible_but_stalled"
        elif trace_features.get("first_feasible_eval") is None:
            actionable_alerts = [
                item for item in trace_alerts if item in {"feasible_rate_low", "best_cv_not_improving"}
            ]
            if actionable_alerts:
                replan_reason = f"trace_alerts:{','.join(actionable_alerts)}"
        operator_family_gate_passed = bool(metadata.get("operator_family_gate_passed", True))
        operator_family_gate_strict_blocked = bool(
            metadata.get("operator_family_gate_strict_blocked", False)
        )
        operator_realization_gate_passed = bool(
            metadata.get("operator_realization_gate_passed", True)
        )
        operator_realization_gate_strict_blocked = bool(
            metadata.get("operator_realization_gate_strict_blocked", False)
        )
        if not replan_reason:
            if operator_family_gate_strict_blocked and not operator_family_gate_passed:
                replan_reason = "audit:operator_family_gate_blocked"
            elif operator_realization_gate_strict_blocked and not operator_realization_gate_passed:
                replan_reason = "audit:operator_realization_gate_blocked"

        fallback_attribution = {
            "policy_round_fallback_reason": str(fallback_reason or ""),
            "modeling_intent_used_fallback": bool(
                modeling_intent_diagnostics.get("used_fallback", False)
            ),
            "modeling_intent_fallback_reason": str(
                modeling_intent_diagnostics.get("fallback_reason", "") or ""
            ),
            "fallback_proxy_geometry_infeasible": int(
                runtime_thermal.get("fallback_proxy_geometry_infeasible", 0) or 0
            ),
            "fallback_proxy_budget_exhausted": int(
                runtime_thermal.get("fallback_proxy_budget_exhausted", 0) or 0
            ),
            "fallback_proxy_scheduler_skipped": int(
                runtime_thermal.get("fallback_proxy_scheduler_skipped", 0) or 0
            ),
        }
        failure_signature, fidelity_escalation_allowed, fidelity_escalation_reason = (
            self._derive_fidelity_escalation_boundary(
                diagnosis_status=diagnosis_status,
                trace_alerts=trace_alerts,
                first_feasible_eval=trace_features.get("first_feasible_eval"),
                fallback_attribution=fallback_attribution,
                replan_reason=replan_reason,
            )
        )
        effective_fidelity = {
            "thermal_evaluator_mode": str(metadata.get("thermal_evaluator_mode", "") or ""),
            "online_comsol_attempt_budget": dict(
                metadata.get("online_comsol_attempt_budget", {}) or {}
            ),
            "runtime_thermal_evaluator_stats": dict(
                metadata.get("runtime_thermal_evaluator_stats", {}) or {}
            ),
            "physics_audit": dict(metadata.get("physics_audit", {}) or {}),
        }
        return VOPPolicyFeedback(
            policy_id=str(policy_priors.get("policy_id", "") or ""),
            policy_source=str(policy_priors.get("policy_source", "") or ""),
            applied=bool(effect_summary.get("applied", False) or policy_priors),
            constraint_focus=list(policy_priors.get("constraint_focus", []) or []),
            requested_search_space=str(
                effect_summary.get("search_space_override", "")
                or policy_priors.get("search_space_prior", "")
                or ""
            ),
            effective_search_space=effective_search_space,
            runtime_overrides=dict(effect_summary.get("runtime_overrides", {}) or {}),
            fidelity_overrides=dict(effect_summary.get("fidelity_overrides", {}) or {}),
            effective_fidelity=effective_fidelity,
            selected_operator_program_id=str(
                effect_summary.get("selected_operator_program_id", "")
                or policy_signature.get("selected_operator_program_id", "")
                or ""
            ),
            selected_operator_actions=list(
                effect_summary.get("selected_operator_actions", [])
                or policy_signature.get("selected_operator_actions", [])
                or []
            ),
            diagnosis_status=str(solver_diagnosis.get("status", "") or ""),
            diagnosis_reason=str(solver_diagnosis.get("reason", "") or ""),
            feasible_rate=trace_features.get("feasible_rate"),
            best_cv_min=trace_features.get("best_cv_min"),
            best_cv_min_source=str(trace_features.get("best_cv_min_source", "") or ""),
            first_feasible_eval=trace_features.get("first_feasible_eval"),
            comsol_calls_to_first_feasible=trace_features.get("comsol_calls_to_first_feasible"),
            trace_alerts=trace_alerts,
            runtime_thermal=runtime_thermal,
            physics_audit=physics_audit,
            fallback_attribution=fallback_attribution,
            failure_signature=str(failure_signature or ""),
            fidelity_escalation_allowed=bool(fidelity_escalation_allowed),
            fidelity_escalation_reason=str(fidelity_escalation_reason or ""),
            replan_recommended=bool(replan_reason),
            replan_reason=str(replan_reason or ""),
        )

    def _should_trigger_reflective_replan(
        self,
        *,
        policy_round: PolicyRoundArtifacts,
        policy_feedback: VOPPolicyFeedback,
    ) -> Tuple[bool, str]:
        if not policy_round.policy_applied:
            return False, "initial_policy_not_applied"
        if not policy_feedback.replan_recommended:
            return False, "no_reflective_replan_condition"
        return True, str(policy_feedback.replan_reason or "reflective_replan_recommended")

    def _serialize_policy_round(
        self,
        policy_round: PolicyRoundArtifacts,
        *,
        policy_feedback: Optional[VOPPolicyFeedback],
        mass_rerun_executed: bool,
        skipped_reason: str,
        previous_policy_pack: Optional[Dict[str, Any]],
        replan_reason: str,
    ) -> Dict[str, Any]:
        round_key = self._build_round_key(
            round_index=policy_round.round_index,
            stage=policy_round.stage,
            policy_id=policy_round.policy_id,
        )
        decision_summary = self._build_vop_decision_summary(
            policy_round,
            previous_policy_pack=previous_policy_pack,
        )
        delegated_effect_summary = self._build_vop_delegated_effect_summary(policy_feedback)
        change_summary = self._build_vop_change_summary(decision_summary)
        effectiveness_summary = self._build_vop_effectiveness_summary(
            delegated_effect_summary
        )
        payload = {
            "vop_round_key": str(round_key),
            "round_index": int(policy_round.round_index),
            "stage": str(policy_round.stage or ""),
            "policy_id": str(policy_round.policy_id or ""),
            "previous_policy_id": str(
                dict(previous_policy_pack or {}).get("policy_id", "") or ""
            ),
            "replan_reason": str(replan_reason or ""),
            "vop_graph": policy_round.vop_graph.model_dump(),
            "generation": self._to_jsonable(policy_round.generation),
            "validation": self._to_jsonable(policy_round.validation),
            "screening": self._to_jsonable(policy_round.screening),
            "runtime_policy_priors": self._to_jsonable(policy_round.runtime_policy_priors),
            "policy_applied": bool(policy_round.policy_applied),
            "fallback_reason": str(policy_round.fallback_reason or ""),
            "bootstrap_error": str(policy_round.bootstrap_error or ""),
            "mass_rerun_executed": bool(mass_rerun_executed),
            "skipped_reason": str(skipped_reason or ""),
            "feedback_aware_fidelity_plan": dict(
                policy_round.feedback_aware_fidelity_plan or {}
            ),
            "feedback_aware_fidelity_reason": str(
                policy_round.feedback_aware_fidelity_reason or ""
            ),
            "selected_operator_program_id": str(
                decision_summary.get("selected_operator_program_id", "") or ""
            ),
            "operator_actions": list(decision_summary.get("operator_actions", []) or []),
            "search_space_override": str(
                decision_summary.get("search_space_override", "") or ""
            ),
            "runtime_overrides": self._to_jsonable(
                decision_summary.get("runtime_overrides", {})
            ),
            "fidelity_plan": self._to_jsonable(
                decision_summary.get("fidelity_plan", {})
            ),
            "decision_rationale": str(
                decision_summary.get("decision_rationale", "") or ""
            ),
            "change_summary": self._to_jsonable(change_summary),
            "expected_effects": self._to_jsonable(
                decision_summary.get("expected_effects", {})
            ),
            "observed_effects": self._to_jsonable(
                delegated_effect_summary.get("observed_effects", {})
            ),
            "effectiveness_summary": self._to_jsonable(effectiveness_summary),
            "confidence": float(decision_summary.get("confidence", 0.0) or 0.0),
        }
        payload["policy_feedback"] = (
            self._to_jsonable(policy_feedback.model_dump())
            if policy_feedback is not None
            else {}
        )
        payload["vop_decision_summary"] = self._to_jsonable(decision_summary)
        payload["vop_delegated_effect_summary"] = self._to_jsonable(
            delegated_effect_summary
        )
        return payload

    def _build_vop_audit_summary(
        self,
        *,
        active_round: PolicyRoundArtifacts,
        active_feedback: Optional[VOPPolicyFeedback],
        previous_policy_pack: Optional[Dict[str, Any]],
        replan_report: VOPReflectiveReplanReport,
        round_audit_rows: list[Dict[str, Any]],
        round_audit_table_path: str,
        final_audit_status: str,
    ) -> Dict[str, Any]:
        primary_round_key = self._build_round_key(
            round_index=active_round.round_index,
            stage=active_round.stage,
            policy_id=active_round.policy_id,
        )
        decision_summary = self._build_vop_decision_summary(
            active_round,
            previous_policy_pack=previous_policy_pack,
        )
        delegated_effect_summary = self._build_vop_delegated_effect_summary(
            active_feedback,
            final_audit_status=final_audit_status,
        )
        return {
            "vop_policy_primary_round_index": int(active_round.round_index),
            "vop_policy_primary_round_key": str(primary_round_key),
            "vop_feedback_aware_fidelity_plan": self._to_jsonable(
                active_round.feedback_aware_fidelity_plan
            ),
            "vop_feedback_aware_fidelity_reason": str(
                active_round.feedback_aware_fidelity_reason or ""
            ),
            "vop_round_count": int(len(round_audit_rows)),
            "vop_round_audit_table": str(round_audit_table_path or ""),
            "vop_reflective_replanning": self._to_jsonable(replan_report.model_dump()),
            "vop_round_audit_digest": self._build_round_audit_digest(round_audit_rows),
            "vop_decision_summary": self._to_jsonable(decision_summary),
            "vop_delegated_effect_summary": self._to_jsonable(
                delegated_effect_summary
            ),
        }

    @classmethod
    def _build_round_key(
        cls,
        *,
        round_index: int,
        stage: str,
        policy_id: str,
    ) -> str:
        stage_token = str(stage or "").strip().lower().replace(" ", "_") or "unknown"
        policy_token = str(policy_id or "").strip() or "no_policy"
        return f"vop_r{int(round_index)}_{stage_token}_{policy_token}"

    @classmethod
    def _build_round_audit_digest(
        cls,
        round_audit_rows: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        digest: list[Dict[str, Any]] = []
        for item in list(round_audit_rows or []):
            record = dict(item or {})
            digest.append(
                {
                    "vop_round_key": str(record.get("vop_round_key", "") or ""),
                    "round_index": int(record.get("round_index", 0) or 0),
                    "stage": str(record.get("stage", "") or ""),
                    "policy_id": str(record.get("policy_id", "") or ""),
                    "previous_policy_id": str(
                        record.get("previous_policy_id", "") or ""
                    ),
                    "candidate_policy_id": str(
                        record.get("candidate_policy_id", "") or ""
                    ),
                    "final_policy_id": str(record.get("final_policy_id", "") or ""),
                    "trigger_reason": str(record.get("trigger_reason", "") or ""),
                    "feedback_aware_fidelity_plan": cls._to_jsonable(
                        record.get("feedback_aware_fidelity_plan", {})
                    ),
                    "feedback_aware_fidelity_reason": str(
                        record.get("feedback_aware_fidelity_reason", "") or ""
                    ),
                    "selected_operator_program_id": str(
                        record.get("selected_operator_program_id", "") or ""
                    ),
                    "search_space_override": str(
                        record.get("search_space_override", "") or ""
                    ),
                    "decision_rationale": str(
                        record.get("decision_rationale", "") or ""
                    ),
                    "change_summary": cls._to_jsonable(
                        record.get("change_summary", {})
                    ),
                    "runtime_overrides": cls._to_jsonable(
                        record.get("runtime_overrides", {})
                    ),
                    "fidelity_plan": cls._to_jsonable(
                        record.get("fidelity_plan", {})
                    ),
                    "expected_effects": cls._to_jsonable(
                        record.get("expected_effects", {})
                    ),
                    "observed_effects": cls._to_jsonable(
                        record.get("observed_effects", {})
                    ),
                    "effectiveness_summary": cls._to_jsonable(
                        record.get("effectiveness_summary", {})
                    ),
                    "confidence": record.get("confidence", None),
                    "policy_applied": bool(record.get("policy_applied", False)),
                    "mass_rerun_executed": bool(record.get("mass_rerun_executed", False)),
                    "skipped_reason": str(record.get("skipped_reason", "") or ""),
                }
            )
        return digest

    def _persist_vop_summary_fields(self, payload: Dict[str, Any]) -> None:
        summary_path = Path(self.host.logger.run_dir) / "summary.json"
        if not summary_path.exists():
            return
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.host.logger.logger.warning("vop summary enrichment failed: %s", exc)
            return
        if not isinstance(summary, dict):
            self.host.logger.logger.warning(
                "vop summary enrichment skipped: summary payload is not an object"
            )
            return
        summary.update(self._to_jsonable(payload))
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.host.logger._generate_markdown_report(summary)
        except Exception as exc:
            self.host.logger.logger.debug(
                "vop summary report regeneration failed: %s",
                exc,
            )

    def _mark_vop_run_in_progress(
        self,
        *,
        stage: str,
        round_index: int,
        trigger_reason: str,
    ) -> None:
        summary_path = Path(self.host.logger.run_dir) / "summary.json"
        if not summary_path.exists():
            return
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.host.logger.logger.warning("vop running-state summary load failed: %s", exc)
            return
        if not isinstance(summary, dict):
            self.host.logger.logger.warning(
                "vop running-state summary update skipped: summary payload is not an object"
            )
            return

        summary.update(
            {
                "status": "RUNNING",
                "timestamp": datetime.now().isoformat(),
                "notes": (
                    "vop_maas reflective replan in progress. "
                    f"stage={str(stage or '')}, round_index={int(round_index)}, "
                    f"reason={str(trigger_reason or '') or 'n/a'}"
                ),
                "optimization_mode": "vop_maas",
                "execution_mode": "mass",
                "delegated_execution_mode": "mass",
                "reflective_replan_triggered": True,
                "reflective_replan_trigger_reason": str(trigger_reason or ""),
                "vop_active_stage": str(stage or ""),
                "vop_active_round_index": int(round_index),
            }
        )
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.host.logger._generate_markdown_report(summary)
        except Exception as exc:
            self.host.logger.logger.debug("vop running-state report update failed: %s", exc)
        self.host.logger.save_run_manifest(
            {
                "status": "RUNNING",
                "final_iteration": int(summary.get("final_iteration", 0) or 0),
                "optimization_mode": "vop_maas",
                "extra": {
                    "reflective_replan_triggered": True,
                    "reflective_replan_trigger_reason": str(trigger_reason or ""),
                    "vop_active_stage": str(stage or ""),
                    "vop_active_round_index": int(round_index),
                },
            }
        )

    def _load_existing_manifest_extra(self) -> Dict[str, Any]:
        manifest_path = Path(self.host.logger.run_dir) / "events" / "run_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.host.logger.logger.warning("vop manifest merge failed: %s", exc)
            return {}
        if not isinstance(manifest, dict):
            return {}
        return dict(manifest.get("extra", {}) or {})

    def _persist_vop_report_section(self, payload: Dict[str, Any]) -> None:
        report_path = Path(self.host.logger.run_dir) / "report.md"
        if not report_path.exists():
            return
        try:
            content = report_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.host.logger.logger.warning("vop report enrichment failed: %s", exc)
            return

        payload = self._hydrate_vop_report_payload(payload)
        reflective = dict(payload.get("vop_reflective_replanning", {}) or {})
        decision = dict(payload.get("vop_decision_summary", {}) or {})
        delegated = dict(payload.get("vop_delegated_effect_summary", {}) or {})
        operator_actions = [
            str(item).strip()
            for item in list(decision.get("operator_actions", []) or [])
            if str(item).strip()
        ]
        block = "\n".join(
            [
                "<!-- VOP_ROUND_AUDIT:START -->",
                "## VOP Controller Decision Flow",
                "",
                (
                    f"- Primary round index: "
                    f"`{int(payload.get('vop_policy_primary_round_index', -1) or -1)}`"
                ),
                (
                    f"- Primary round key: "
                    f"`{str(payload.get('vop_policy_primary_round_key', '') or 'n/a')}`"
                ),
                f"- Round count: `{int(payload.get('vop_round_count', 0) or 0)}`",
                f"- Policy id: `{str(decision.get('policy_id', '') or reflective.get('final_policy_id', '') or 'n/a')}`",
                f"- Decision rationale: `{self._compact_text(decision.get('decision_rationale', ''))}`",
                (
                    f"- Selected operator program: "
                    f"`{str(decision.get('selected_operator_program_id', '') or 'n/a')}`"
                ),
                f"- Operator actions: `{', '.join(operator_actions) or 'n/a'}`",
                (
                    "- Reflective replan: "
                    f"`triggered={bool(reflective.get('triggered', False))}`"
                    f", reason=`{str(reflective.get('trigger_reason', '') or reflective.get('skipped_reason', '') or 'n/a')}`"
                ),
                "",
                "## Decision Changes",
                "",
                (
                    f"- Search-space override: "
                    f"`{str(decision.get('search_space_override', '') or 'n/a')}`"
                ),
                f"- Intent changes: `{self._compact_text(decision.get('intent_changes', {}))}`",
                f"- Runtime overrides: `{self._compact_text(decision.get('runtime_overrides', {}))}`",
                f"- Fidelity plan: `{self._compact_text(decision.get('fidelity_plan', {}))}`",
                f"- Expected effects: `{self._compact_text(decision.get('expected_effects', {}))}`",
                "",
                "## Observed Effects",
                "",
                (
                    f"- Diagnosis: "
                    f"`{str(delegated.get('diagnosis_status', '') or 'n/a')}`"
                    f" / `{self._compact_text(delegated.get('diagnosis_reason', ''))}`"
                ),
                (
                    f"- Search-space effect: "
                    f"`{str(delegated.get('search_space_effect', '') or 'n/a')}`"
                ),
                (
                    f"- Expected vs observed: "
                    f"expected=`{self._compact_text(decision.get('expected_effects', {}))}`"
                    f", observed=`{self._compact_text(delegated.get('observed_effects', {}))}`"
                ),
                (
                    f"- Effectiveness verdict: "
                    f"`{str(delegated.get('effectiveness_verdict', '') or 'n/a')}`"
                ),
                "",
                "## Delegated Mass Execution Summary",
                "",
                (
                    f"- Delegated execution mode: "
                    f"`{str(payload.get('delegated_execution_mode', '') or 'mass')}`"
                ),
                f"- Audit status: `{str(delegated.get('audit_status', '') or 'n/a')}`",
                f"- First feasible eval: `{self._compact_text(delegated.get('first_feasible_eval'))}`",
                (
                    f"- COMSOL calls to first feasible: "
                    f"`{self._compact_text(delegated.get('comsol_calls_to_first_feasible'))}`"
                ),
                (
                    "- Feedback-aware fidelity: "
                    f"`{str(payload.get('vop_feedback_aware_fidelity_reason', '') or 'n/a')}`"
                ),
                "",
                "## VOP Round Audit",
                "",
                (
                    f"- Round audit table: "
                    f"`{str(payload.get('vop_round_audit_table', '') or 'tables/vop_rounds.csv')}`"
                ),
                f"- Final policy id: `{str(reflective.get('final_policy_id', '') or decision.get('policy_id', '') or 'n/a')}`",
                "<!-- VOP_ROUND_AUDIT:END -->",
                "",
            ]
        )
        start_marker = "<!-- VOP_ROUND_AUDIT:START -->"
        end_marker = "<!-- VOP_ROUND_AUDIT:END -->"
        if start_marker in content and end_marker in content:
            prefix, remainder = content.split(start_marker, 1)
            _, suffix = remainder.split(end_marker, 1)
            updated = f"{prefix}{block}{suffix.lstrip()}"
        else:
            separator = "" if content.endswith("\n") else "\n"
            updated = f"{content}{separator}\n{block}"
        report_path.write_text(updated, encoding="utf-8")

    @classmethod
    def _parse_jsonish(cls, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            return cls._to_jsonable(value)
        text = str(value or "").strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return {}
        try:
            return cls._to_jsonable(json.loads(text))
        except Exception:
            return text

    @classmethod
    def _compact_text(cls, value: Any) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or "n/a"
        payload = cls._to_jsonable(value)
        if payload in ({}, [], ""):
            return "n/a"
        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(payload)
        if len(text) > 240:
            return text[:237] + "..."
        return text

    @classmethod
    def _build_vop_change_summary(
        cls,
        decision_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "search_space_override": str(
                decision_summary.get("search_space_override", "") or ""
            ),
            "operator_actions": list(
                decision_summary.get("operator_actions", []) or []
            ),
            "intent_changes": cls._to_jsonable(
                decision_summary.get("intent_changes", {})
            ),
            "runtime_overrides": cls._to_jsonable(
                decision_summary.get("runtime_overrides", {})
            ),
            "fidelity_plan": cls._to_jsonable(
                decision_summary.get("fidelity_plan", {})
            ),
            "change_set": cls._to_jsonable(decision_summary.get("change_set", {})),
        }

    @classmethod
    def _build_vop_effectiveness_summary(
        cls,
        delegated_effect_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "diagnosis_status": str(
                delegated_effect_summary.get("diagnosis_status", "") or ""
            ),
            "diagnosis_reason": str(
                delegated_effect_summary.get("diagnosis_reason", "") or ""
            ),
            "search_space_effect": str(
                delegated_effect_summary.get("search_space_effect", "") or ""
            ),
            "first_feasible_eval": delegated_effect_summary.get(
                "first_feasible_eval", None
            ),
            "comsol_calls_to_first_feasible": delegated_effect_summary.get(
                "comsol_calls_to_first_feasible", None
            ),
            "audit_status": str(
                delegated_effect_summary.get("audit_status", "") or ""
            ),
            "effectiveness_verdict": str(
                delegated_effect_summary.get("effectiveness_verdict", "") or ""
            ),
        }

    def _hydrate_vop_report_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(self._to_jsonable(payload))
        round_audit_table = str(
            merged.get("vop_round_audit_table", "") or "tables/vop_rounds.csv"
        )
        table_path = Path(self.host.logger.run_dir) / round_audit_table
        rows: list[Dict[str, Any]] = []
        if table_path.exists():
            try:
                with table_path.open("r", encoding="utf-8", newline="") as f:
                    rows = [
                        dict(item or {})
                        for item in csv.DictReader(f)
                        if dict(item or {})
                    ]
            except Exception as exc:
                self.host.logger.logger.warning(
                    "vop report round audit fallback failed: %s",
                    exc,
                )
                rows = []
        if not rows:
            return merged

        def _round_order_key(row: Dict[str, Any]) -> tuple[int, str]:
            raw_round = row.get("round_index", 0)
            try:
                round_index = int(float(raw_round or 0))
            except (TypeError, ValueError):
                round_index = -1
            return round_index, str(row.get("vop_round_key", "") or "")

        ordered = sorted(rows, key=_round_order_key)
        latest = dict(ordered[-1] or {})
        try:
            current_round_count = int(float(merged.get("vop_round_count", 0) or 0))
        except (TypeError, ValueError):
            current_round_count = 0
        if current_round_count <= 0:
            merged["vop_round_count"] = int(len(ordered))
        if not str(merged.get("vop_policy_primary_round_key", "") or "").strip():
            merged["vop_policy_primary_round_key"] = str(
                latest.get("vop_round_key", "") or ""
            )
        raw_primary_index = merged.get("vop_policy_primary_round_index", None)
        if raw_primary_index in (None, "", -1) or str(raw_primary_index).strip() == "-1":
            try:
                merged["vop_policy_primary_round_index"] = int(
                    float(latest.get("round_index", 0) or 0)
                )
            except (TypeError, ValueError):
                merged["vop_policy_primary_round_index"] = -1
        if not list(merged.get("vop_round_audit_digest", []) or []):
            merged["vop_round_audit_digest"] = self._build_round_audit_digest(ordered)

        decision = dict(merged.get("vop_decision_summary", {}) or {})
        if not decision:
            change_summary = self._parse_jsonish(latest.get("change_summary", {}))
            change_summary = dict(change_summary or {}) if isinstance(change_summary, dict) else {}
            decision = {
                "policy_id": str(latest.get("policy_id", "") or ""),
                "selected_operator_program_id": str(
                    latest.get("selected_operator_program_id", "") or ""
                ),
                "operator_actions": list(change_summary.get("operator_actions", []) or []),
                "search_space_override": str(
                    latest.get("search_space_override", "")
                    or change_summary.get("search_space_override", "")
                    or ""
                ),
                "intent_changes": self._parse_jsonish(
                    change_summary.get("intent_changes", {})
                ),
                "runtime_overrides": self._parse_jsonish(
                    latest.get("runtime_overrides", {})
                    or change_summary.get("runtime_overrides", {})
                ),
                "fidelity_plan": self._parse_jsonish(
                    latest.get("fidelity_plan", {})
                    or change_summary.get("fidelity_plan", {})
                ),
                "expected_effects": self._parse_jsonish(
                    latest.get("expected_effects", {})
                ),
                "decision_rationale": str(latest.get("decision_rationale", "") or ""),
                "confidence": latest.get("confidence", None),
            }
            merged["vop_decision_summary"] = self._to_jsonable(decision)

        delegated = dict(merged.get("vop_delegated_effect_summary", {}) or {})
        if not delegated:
            effectiveness = self._parse_jsonish(
                latest.get("effectiveness_summary", {})
            )
            effectiveness = dict(effectiveness or {}) if isinstance(effectiveness, dict) else {}
            delegated = {
                "diagnosis_status": str(effectiveness.get("diagnosis_status", "") or ""),
                "diagnosis_reason": str(effectiveness.get("diagnosis_reason", "") or ""),
                "search_space_effect": str(
                    effectiveness.get("search_space_effect", "") or ""
                ),
                "first_feasible_eval": effectiveness.get("first_feasible_eval", None),
                "comsol_calls_to_first_feasible": effectiveness.get(
                    "comsol_calls_to_first_feasible", None
                ),
                "audit_status": str(effectiveness.get("audit_status", "") or ""),
                "effectiveness_verdict": str(
                    effectiveness.get("effectiveness_verdict", "") or ""
                ),
                "observed_effects": self._parse_jsonish(
                    latest.get("observed_effects", {})
                ),
            }
            merged["vop_delegated_effect_summary"] = self._to_jsonable(delegated)
        return merged

    def _refresh_vop_round_observability(
        self,
        round_audit_rows: list[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:
        try:
            persist_vop_round_events(self.host.logger.run_dir, round_audit_rows)
            return materialize_observability_tables(self.host.logger.run_dir), ""
        except Exception as exc:
            self.host.logger.logger.warning(
                "vop round observability materialization failed: %s",
                exc,
            )
            return {}, str(exc)

    def _build_round_audit_rows(
        self,
        *,
        active_round: PolicyRoundArtifacts,
        replan_report: VOPReflectiveReplanReport,
        policy_round_records: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        run_id = Path(self.host.logger.run_dir).name
        timestamp = datetime.now().isoformat()
        final_policy_id = str(replan_report.final_policy_id or active_round.policy_id or "")
        reflective_candidate_policy_id = str(replan_report.candidate_policy_id or "")
        rows: list[Dict[str, Any]] = []
        for item in list(policy_round_records or []):
            record = dict(item or {})
            round_index = int(record.get("round_index", 0) or 0)
            policy_id = str(record.get("policy_id", "") or "")
            rows.append(
                {
                    "run_id": str(run_id),
                    "timestamp": str(timestamp),
                    "iteration": int(round_index + 1),
                    "attempt": int(round_index),
                    "vop_round_key": str(record.get("vop_round_key", "") or ""),
                    "round_index": int(round_index),
                    "stage": str(record.get("stage", "") or ""),
                    "policy_id": str(policy_id),
                    "previous_policy_id": str(record.get("previous_policy_id", "") or ""),
                    "candidate_policy_id": str(
                        policy_id
                        or (reflective_candidate_policy_id if round_index > 0 else "")
                    ),
                    "final_policy_id": str(final_policy_id),
                    "trigger_reason": str(
                        record.get("replan_reason", "")
                        or (replan_report.trigger_reason if round_index > 0 else "")
                        or ""
                    ),
                    "feedback_aware_fidelity_plan": dict(
                        record.get("feedback_aware_fidelity_plan", {}) or {}
                    ),
                    "feedback_aware_fidelity_reason": str(
                        record.get("feedback_aware_fidelity_reason", "") or ""
                    ),
                    "selected_operator_program_id": str(
                        record.get("selected_operator_program_id", "") or ""
                    ),
                    "operator_actions": list(record.get("operator_actions", []) or []),
                    "search_space_override": str(
                        record.get("search_space_override", "") or ""
                    ),
                    "decision_rationale": str(
                        record.get("decision_rationale", "") or ""
                    ),
                    "change_summary": self._to_jsonable(
                        record.get("change_summary", {})
                    ),
                    "runtime_overrides": self._to_jsonable(
                        record.get("runtime_overrides", {})
                    ),
                    "fidelity_plan": self._to_jsonable(
                        record.get("fidelity_plan", {})
                    ),
                    "expected_effects": self._to_jsonable(
                        record.get("expected_effects", {})
                    ),
                    "observed_effects": self._to_jsonable(
                        record.get("observed_effects", {})
                    ),
                    "effectiveness_summary": self._to_jsonable(
                        record.get("effectiveness_summary", {})
                    ),
                    "confidence": record.get("confidence", None),
                    "policy_applied": bool(record.get("policy_applied", False)),
                    "mass_rerun_executed": bool(record.get("mass_rerun_executed", False)),
                    "skipped_reason": str(record.get("skipped_reason", "") or ""),
                    "run_mode": "vop_maas",
                    "producer_mode": "vop_maas",
                    "execution_mode": "mass",
                    "lifecycle_state": "experimental",
                }
            )
        return rows

    @staticmethod
    def _derive_fidelity_escalation_boundary(
        *,
        diagnosis_status: str,
        trace_alerts: list[str],
        first_feasible_eval: Any,
        fallback_attribution: Dict[str, Any],
        replan_reason: str = "",
    ) -> Tuple[str, bool, str]:
        status = str(diagnosis_status or "").strip().lower()
        normalized_replan_reason = str(replan_reason or "").strip().lower()
        alerts = [
            str(item).strip().lower()
            for item in list(trace_alerts or [])
            if str(item).strip()
        ]
        actionable_alerts = [
            item for item in alerts if item in {"feasible_rate_low", "best_cv_not_improving"}
        ]
        fallback = dict(fallback_attribution or {})
        budget_exhausted = int(fallback.get("fallback_proxy_budget_exhausted", 0) or 0) > 0
        scheduler_skipped = int(fallback.get("fallback_proxy_scheduler_skipped", 0) or 0) > 0
        no_first_feasible = first_feasible_eval is None

        signature_parts: list[str] = []
        if status:
            signature_parts.append(status)
        if no_first_feasible:
            signature_parts.append("no_first_feasible")
        if budget_exhausted:
            signature_parts.append("proxy_budget_exhausted")
        if scheduler_skipped:
            signature_parts.append("proxy_scheduler_skipped")
        if actionable_alerts:
            signature_parts.append(f"alerts:{'+'.join(actionable_alerts)}")
        if normalized_replan_reason == "audit:operator_family_gate_blocked":
            signature_parts.append("audit_operator_family_gate_blocked")
        elif normalized_replan_reason == "audit:operator_realization_gate_blocked":
            signature_parts.append("audit_operator_realization_gate_blocked")
        failure_signature = "+".join(signature_parts) or "stable"

        if normalized_replan_reason == "audit:operator_family_gate_blocked":
            return failure_signature, True, "allow:audit_operator_family_gate_blocked"
        if normalized_replan_reason == "audit:operator_realization_gate_blocked":
            return failure_signature, True, "allow:audit_operator_realization_gate_blocked"
        if budget_exhausted and (status in {"no_feasible", "feasible_but_stalled"} or no_first_feasible):
            return (
                failure_signature,
                True,
                "allow:proxy_budget_exhausted_after_unresolved_failure",
            )
        if scheduler_skipped and no_first_feasible:
            return (
                failure_signature,
                True,
                "allow:proxy_scheduler_skipped_without_first_feasible",
            )
        if status == "no_feasible":
            return failure_signature, True, "allow:diagnosis_no_feasible"
        if status == "feasible_but_stalled":
            return failure_signature, True, "allow:diagnosis_feasible_but_stalled"
        if no_first_feasible and actionable_alerts:
            return (
                failure_signature,
                True,
                f"allow:trace_alerts_{'_'.join(actionable_alerts)}",
            )
        if status == "runtime_error":
            return (
                failure_signature,
                False,
                "blocked:diagnosis_runtime_error_requires_root_cause_fix",
            )
        if status == "empty_solution":
            return (
                failure_signature,
                False,
                "blocked:diagnosis_empty_solution_requires_policy_or_constraint_change",
            )
        return failure_signature, False, "blocked:no_escalatable_failure_signature"

    def _derive_feedback_aware_fidelity_plan(
        self,
        *,
        policy_feedback: VOPPolicyFeedback,
        previous_policy_pack: Dict[str, Any],
        vop_graph: VOPGraph,
        enabled: bool,
    ) -> Dict[str, Any]:
        if not enabled:
            return {"enabled": False, "plan": {}, "reason": "feedback_aware_fidelity_disabled"}

        host = self.host
        opt_cfg = dict(host.config.get("optimization", {}) or {})
        simulation_backend = str(
            host.config.get("simulation", {}).get("backend", "") or ""
        ).strip().lower()
        previous_plan = dict(previous_policy_pack.get("fidelity_plan", {}) or {})
        previous_focus = self._normalize_focus_tokens(previous_policy_pack.get("constraint_focus", []))
        feedback_focus = self._normalize_focus_tokens(policy_feedback.constraint_focus or [])
        graph_focus = self._normalize_focus_tokens(
            dict(vop_graph.metadata or {}).get("level_focus_hint", [])
        )
        level_focus = self._scenario_level_focus_hints()
        focus = feedback_focus | previous_focus | graph_focus | level_focus
        dominant_family = str(vop_graph.dominant_violation_family or "").strip().lower()
        dominant_focus = self._normalize_focus_tokens(
            [dominant_family, str(vop_graph.dominant_metric or "")]
        )
        multiphysics_focus = bool(
            focus.intersection(
                {
                    "thermal",
                    "max_temp",
                    "structural",
                    "max_stress",
                    "safety_factor",
                    "first_modal_freq",
                    "power",
                    "voltage_drop",
                    "power_margin",
                }
            )
            or dominant_focus.intersection({"thermal", "structural", "power"})
        )
        boundary_reason = str(policy_feedback.fidelity_escalation_reason or "")
        actionable_alerts = {
            str(item).strip().lower()
            for item in list(policy_feedback.trace_alerts or [])
            if str(item).strip().lower() in {"feasible_rate_low", "best_cv_not_improving"}
        }
        if simulation_backend != "comsol":
            return {
                "enabled": True,
                "plan": {},
                "reason": ",".join(
                    item
                    for item in [boundary_reason, "blocked:non_comsol_backend"]
                    if str(item).strip()
                )
                or "blocked:non_comsol_backend",
            }
        if not multiphysics_focus:
            return {
                "enabled": True,
                "plan": {},
                "reason": ",".join(
                    item
                    for item in [boundary_reason, "blocked:no_multiphysics_focus"]
                    if str(item).strip()
                )
                or "blocked:no_multiphysics_focus",
            }
        if not bool(policy_feedback.fidelity_escalation_allowed):
            return {
                "enabled": True,
                "plan": {},
                "reason": boundary_reason or "blocked:no_escalation_signal",
            }

        recommended: Dict[str, Any] = {}
        reasons: list[str] = [boundary_reason] if boundary_reason else []
        base_audit_top_k = max(int(opt_cfg.get("mass_audit_top_k", 1) or 1), 1)
        effective_fidelity = dict(policy_feedback.effective_fidelity or {})
        previous_effective_mode = str(
            effective_fidelity.get("thermal_evaluator_mode", "")
            or previous_plan.get("thermal_evaluator_mode", "")
            or opt_cfg.get("mass_thermal_evaluator_mode", "proxy")
            or ""
        ).strip().lower()
        audit_gate_signal = str(boundary_reason or "").strip().lower() in {
            "allow:audit_operator_family_gate_blocked",
            "allow:audit_operator_realization_gate_blocked",
        }
        budget_report = dict(effective_fidelity.get("online_comsol_attempt_budget", {}) or {})
        fallback = dict(policy_feedback.fallback_attribution or {})
        current_budget = max(
            int(previous_plan.get("online_comsol_eval_budget", 0) or 0),
            int(opt_cfg.get("mass_online_comsol_eval_budget", 0) or 0),
            int(budget_report.get("requested_budget", 0) or 0),
        )
        mode_escalated = False
        budget_escalated = False

        if previous_effective_mode != "online_comsol":
            recommended["thermal_evaluator_mode"] = "online_comsol"
            mode_escalated = True
            reasons.append("apply:thermal_mode_online_comsol")

        budget_signal = False
        budget_target = max(current_budget, 4)
        if int(fallback.get("fallback_proxy_budget_exhausted", 0) or 0) > 0:
            budget_signal = True
            budget_target = max(budget_target, min(current_budget + 2, 16))
            reasons.append("apply:online_comsol_budget_after_proxy_budget_exhausted")
        elif int(fallback.get("fallback_proxy_scheduler_skipped", 0) or 0) > 0:
            budget_signal = True
            budget_target = max(budget_target, min(current_budget + 1, 16))
            reasons.append("apply:online_comsol_budget_after_scheduler_skip")
        elif (
            previous_effective_mode == "online_comsol"
            and policy_feedback.first_feasible_eval is None
            and str(policy_feedback.diagnosis_status or "").strip().lower()
            in {"no_feasible", "feasible_but_stalled"}
        ):
            budget_signal = True
            budget_target = max(budget_target, min(current_budget + 1, 16))
            reasons.append("apply:online_comsol_budget_after_unresolved_online_comsol_round")
        elif previous_effective_mode == "online_comsol" and policy_feedback.first_feasible_eval is None and actionable_alerts:
            budget_signal = True
            budget_target = max(
                budget_target,
                min(current_budget + (2 if "feasible_rate_low" in actionable_alerts else 1), 16),
            )
            reasons.append(
                "apply:online_comsol_budget_after_trace_alerts_" + "_".join(sorted(actionable_alerts))
            )
        elif previous_effective_mode == "online_comsol" and audit_gate_signal:
            budget_signal = True
            budget_target = max(budget_target, min(current_budget + 1, 16))
            reasons.append("apply:online_comsol_budget_after_audit_gate")
        else:
            reasons.append("bounded:no_online_comsol_budget_signal")

        if budget_signal and budget_target > current_budget:
            recommended["online_comsol_eval_budget"] = int(budget_target)
            budget_escalated = True

        if mode_escalated or budget_escalated:
            recommended["physics_audit_top_k"] = max(
                int(previous_plan.get("physics_audit_top_k", 0) or 0),
                base_audit_top_k,
                2,
            )
            reasons.append("apply:physics_audit_top_k")
        else:
            reasons.append("bounded:no_physics_audit_escalation")

        if not recommended:
            reasons.append("no_change:bounded_fidelity_already_sufficient")
        return {
            "enabled": True,
            "plan": recommended,
            "reason": ",".join(
                item for item in reasons if str(item).strip()
            )
            or "no_feedback_aware_fidelity_change",
        }

    @staticmethod
    def _merge_feedback_aware_fidelity_plan(
        raw_policy_payload: Dict[str, Any],
        *,
        feedback_aware_fidelity_plan: Optional[Dict[str, Any]],
        feedback_aware_fidelity_reason: str,
    ) -> Dict[str, Any]:
        payload = deepcopy(dict(raw_policy_payload or {}))
        recommended = dict(feedback_aware_fidelity_plan or {})
        if not payload or not recommended:
            return payload

        policy_pack = payload.get("policy_pack")
        if isinstance(policy_pack, dict):
            target = policy_pack
        else:
            target = payload
        fidelity_plan = dict(target.get("fidelity_plan", {}) or {})

        thermal_mode = str(recommended.get("thermal_evaluator_mode", "") or "").strip().lower()
        if thermal_mode == "online_comsol":
            current_mode = str(
                fidelity_plan.get("thermal_evaluator_mode", "") or ""
            ).strip().lower()
            if current_mode != "online_comsol":
                fidelity_plan["thermal_evaluator_mode"] = "online_comsol"

        for key in ("online_comsol_eval_budget", "physics_audit_top_k"):
            if key not in recommended:
                continue
            try:
                recommended_value = int(recommended.get(key, 0) or 0)
            except Exception:
                continue
            current_value = 0
            try:
                current_value = int(fidelity_plan.get(key, 0) or 0)
            except Exception:
                current_value = 0
            if recommended_value > current_value:
                fidelity_plan[key] = int(recommended_value)

        if fidelity_plan:
            target["fidelity_plan"] = fidelity_plan
            metadata = dict(target.get("metadata", {}) or {})
            metadata["feedback_aware_fidelity_plan"] = dict(recommended)
            metadata["feedback_aware_fidelity_reason"] = str(
                feedback_aware_fidelity_reason or ""
            )
            target["metadata"] = metadata
        return payload

    @staticmethod
    def _collect_component_ids(state: DesignState) -> list[str]:
        return [
            str(getattr(comp, "id", "") or "").strip()
            for comp in list(getattr(state, "components", []) or [])
            if str(getattr(comp, "id", "") or "").strip()
        ]

    @staticmethod
    def _policy_material_signature(runtime_policy_priors: Dict[str, Any]) -> Dict[str, Any]:
        priors = dict(runtime_policy_priors or {})
        selected_candidate = dict(priors.get("selected_operator_candidate", {}) or {})
        selected_program = dict(selected_candidate.get("program", {}) or {})
        selected_actions = []
        selected_action_payloads = []
        for action_payload in list(selected_program.get("actions", []) or []):
            if not isinstance(action_payload, dict):
                continue
            action_name = str(action_payload.get("action", "") or "").strip().lower()
            if action_name:
                selected_actions.append(action_name)
                selected_action_payloads.append(
                    {
                        "action": action_name,
                        "params": VOPPolicyProgramService._to_jsonable(
                            dict(action_payload.get("params", {}) or {})
                        ),
                    }
                )
        return {
            "constraint_focus": [
                str(item).strip().lower()
                for item in list(priors.get("constraint_focus", []) or [])
                if str(item).strip()
            ],
            "search_space_prior": str(priors.get("search_space_prior", "") or "").strip().lower(),
            "runtime_knob_priors": dict(priors.get("runtime_knob_priors", {}) or {}),
            "fidelity_plan": dict(priors.get("fidelity_plan", {}) or {}),
            "selected_operator_program_id": str(
                selected_program.get("program_id", "")
                or selected_candidate.get("candidate_id", "")
                or ""
            ).strip(),
            "selected_operator_actions": selected_actions,
            "selected_operator_action_payloads": selected_action_payloads,
        }

    @classmethod
    def _build_vop_decision_summary(
        cls,
        policy_round: PolicyRoundArtifacts,
        *,
        previous_policy_pack: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        priors = dict(policy_round.runtime_policy_priors or {})
        pack = dict(policy_round.policy_pack_payload or {})
        signature = cls._policy_material_signature(priors)
        metadata = dict(pack.get("metadata", {}) or {})
        raw_change_set = dict(pack.get("change_set", {}) or metadata.get("change_set", {}) or {})
        intent_changes = dict(
            raw_change_set.get("intent_changes", {})
            or metadata.get("intent_changes", {})
            or {}
        )
        decision_rationale = str(
            pack.get("decision_rationale", "")
            or pack.get("rationale", "")
            or metadata.get("decision_rationale", "")
            or ""
        )
        runtime_overrides = dict(priors.get("runtime_knob_priors", {}) or {})
        fidelity_plan = dict(priors.get("fidelity_plan", {}) or {})
        expected_effects = dict(pack.get("expected_effects", {}) or {})
        confidence = float(pack.get("confidence", priors.get("confidence", 0.0)) or 0.0)
        previous_policy_id = str(dict(previous_policy_pack or {}).get("policy_id", "") or "")
        return {
            "policy_id": str(policy_round.policy_id or ""),
            "policy_source": str(
                priors.get("policy_source", "")
                or pack.get("policy_source", "")
                or policy_round.generation.get("source", "")
                or ""
            ),
            "selected_operator_program_id": str(signature.get("selected_operator_program_id", "") or ""),
            "operator_actions": list(signature.get("selected_operator_actions", []) or []),
            "search_space_override": str(priors.get("search_space_prior", "") or ""),
            "intent_changes": cls._to_jsonable(intent_changes),
            "runtime_overrides": cls._to_jsonable(runtime_overrides),
            "fidelity_plan": cls._to_jsonable(fidelity_plan),
            "expected_effects": cls._to_jsonable(expected_effects),
            "confidence": float(confidence),
            "decision_rationale": str(decision_rationale or ""),
            "change_set": cls._to_jsonable(raw_change_set),
            "previous_policy_id": previous_policy_id,
        }

    @staticmethod
    def _derive_search_space_effect(policy_feedback: Optional[VOPPolicyFeedback]) -> str:
        if policy_feedback is None:
            return ""
        requested = str(policy_feedback.requested_search_space or "").strip()
        effective = str(policy_feedback.effective_search_space or "").strip()
        if requested and effective and requested != effective:
            return f"{requested}->{effective}"
        return effective or requested

    @staticmethod
    def _derive_effectiveness_verdict(policy_feedback: Optional[VOPPolicyFeedback]) -> str:
        if policy_feedback is None:
            return "not_observed"
        diagnosis = str(policy_feedback.diagnosis_status or "").strip().lower()
        if diagnosis == "feasible":
            return "feasible_improved"
        if diagnosis == "feasible_but_stalled":
            return "feasible_but_stalled"
        if diagnosis == "no_feasible":
            return "no_feasible"
        if diagnosis == "runtime_error":
            return "runtime_error"
        return diagnosis or "observed"

    @classmethod
    def _build_vop_delegated_effect_summary(
        cls,
        policy_feedback: Optional[VOPPolicyFeedback],
        *,
        final_audit_status: str = "",
    ) -> Dict[str, Any]:
        if policy_feedback is None:
            return {
                "diagnosis_status": "",
                "diagnosis_reason": "",
                "search_space_effect": "",
                "first_feasible_eval": None,
                "comsol_calls_to_first_feasible": None,
                "audit_status": str(final_audit_status or ""),
                "effectiveness_verdict": "not_observed",
            }
        return {
            "diagnosis_status": str(policy_feedback.diagnosis_status or ""),
            "diagnosis_reason": str(policy_feedback.diagnosis_reason or ""),
            "search_space_effect": cls._derive_search_space_effect(policy_feedback),
            "first_feasible_eval": policy_feedback.first_feasible_eval,
            "comsol_calls_to_first_feasible": policy_feedback.comsol_calls_to_first_feasible,
            "audit_status": str(final_audit_status or ""),
            "effectiveness_verdict": cls._derive_effectiveness_verdict(policy_feedback),
            "observed_effects": {
                "effective_search_space": str(policy_feedback.effective_search_space or ""),
                "runtime_overrides": cls._to_jsonable(policy_feedback.runtime_overrides),
                "fidelity_overrides": cls._to_jsonable(policy_feedback.fidelity_overrides),
                "effective_fidelity": cls._to_jsonable(policy_feedback.effective_fidelity),
                "trace_alerts": list(policy_feedback.trace_alerts or []),
            },
        }

    @classmethod
    def _has_material_policy_change(
        cls,
        previous_priors: Dict[str, Any],
        current_priors: Dict[str, Any],
    ) -> bool:
        return cls._policy_material_signature(previous_priors) != cls._policy_material_signature(
            current_priors
        )

    @staticmethod
    def _extract_policy_pack_payload(generation_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(generation_payload, dict):
            return {}
        candidate = generation_payload.get("policy_pack")
        if isinstance(candidate, dict):
            return dict(candidate)
        if "policy_id" in generation_payload and "search_space_prior" in generation_payload:
            return dict(generation_payload)
        return {}

    @staticmethod
    def _to_jsonable(payload: Any) -> Any:
        if payload is None or isinstance(payload, (str, int, float, bool)):
            return payload
        if hasattr(payload, "model_dump"):
            return VOPPolicyProgramService._to_jsonable(payload.model_dump())
        if isinstance(payload, dict):
            return {
                str(key): VOPPolicyProgramService._to_jsonable(value)
                for key, value in payload.items()
            }
        if isinstance(payload, (list, tuple, set)):
            return [VOPPolicyProgramService._to_jsonable(item) for item in payload]
        return str(payload)
