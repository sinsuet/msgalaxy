"""
Runtime facade for mode services.

This module exposes an explicit service surface so mode implementations do not
depend on host private methods directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.protocol import DesignState, EvaluationResult
    from optimization.protocol import ModelingIntent, ViolationItem
    from workflow.orchestrator import WorkflowOrchestrator


@dataclass
class RuntimeFacade:
    """Typed adapter over orchestrator runtime internals."""

    host: "WorkflowOrchestrator"

    # Shared
    def resolve_pymoo_algorithm(self) -> str:
        return self.host._resolve_pymoo_algorithm()

    def evaluate_design(self, design_state: "DesignState", iteration: int):
        return self.host._evaluate_design(design_state, iteration)

    def evaluate_geometry(self, design_state: "DesignState"):
        return self.host._evaluate_geometry(design_state)

    def check_violations(self, geometry_metrics: Any, thermal_metrics: Any, structural_metrics: Any, power_metrics: Any):
        return self.host._check_violations(geometry_metrics, thermal_metrics, structural_metrics, power_metrics)

    def build_global_context(
        self,
        iteration: int,
        design_state: "DesignState",
        metrics: Dict[str, Any],
        violations: List["ViolationItem"],
        *,
        phase: str = "A",
    ):
        return self.host._build_global_context(
            iteration,
            design_state,
            metrics,
            violations,
            phase=phase,
        )

    def build_maas_requirement_text(self, bom_file: Optional[str]) -> str:
        return self.host._build_maas_requirement_text(bom_file)

    def calculate_penalty_breakdown(self, metrics: Dict[str, Any], violations: List["ViolationItem"]) -> Dict[str, float]:
        return self.host._calculate_penalty_breakdown(metrics, violations)

    def calculate_penalty_score(self, metrics: Dict[str, Any], violations: List["ViolationItem"]) -> float:
        return self.host._calculate_penalty_score(metrics, violations)

    def generate_final_report(self, final_state: "DesignState", iterations: int) -> None:
        self.host._generate_final_report(final_state, iterations)

    # Shared runtime state slots (used by migrated mode services)
    def get_last_trace_metrics(self) -> Optional[Dict[str, float]]:
        value = getattr(self.host, "_last_trace_metrics", None)
        if value is None:
            return None
        return dict(value)

    def set_last_trace_metrics(self, snapshot: Dict[str, float]) -> None:
        self.host._last_trace_metrics = dict(snapshot)

    def append_snapshot(self, *, iteration: int, snapshot: Dict[str, float], max_history: int = 40) -> None:
        history = list(getattr(self.host, "_snapshot_history", []) or [])
        history.append({"iteration": float(iteration), **dict(snapshot)})
        if max_history > 0 and len(history) > int(max_history):
            history = history[-int(max_history):]
        self.host._snapshot_history = history

    def get_cg_rescue_last_iter(self) -> int:
        return int(getattr(self.host, "_cg_rescue_last_iter", -999))

    def set_cg_rescue_last_iter(self, iteration: int) -> None:
        self.host._cg_rescue_last_iter = int(iteration)

    # Agent-loop
    def is_cg_plateau(self, iteration: int, current_snapshot: Dict[str, float], violations: List["ViolationItem"]) -> bool:
        return self.host._is_cg_plateau(iteration, current_snapshot, violations)

    def compute_effectiveness_score(self, previous: Optional[Dict[str, float]], current: Dict[str, float]) -> float:
        return self.host._compute_effectiveness_score(previous, current)

    def should_rollback(self, iteration: int, current_eval: "EvaluationResult") -> tuple[bool, str]:
        return self.host._should_rollback(iteration, current_eval)

    def execute_rollback(self):
        return self.host._execute_rollback()

    def run_cg_plateau_rescue(self, current_state: "DesignState", current_metrics: Dict[str, Any], violations: List["ViolationItem"], iteration: int):
        return self.host._run_cg_plateau_rescue(
            current_state=current_state,
            current_metrics=current_metrics,
            violations=violations,
            iteration=iteration,
        )

    def inject_runtime_constraints_to_plan(self, strategic_plan: Any) -> None:
        self.host._inject_runtime_constraints_to_plan(strategic_plan)

    def execute_plan(self, execution_plan: Any, current_state: "DesignState") -> "DesignState":
        return self.host._execute_plan(execution_plan, current_state)

    def is_geometry_feasible(self, design_state: "DesignState") -> tuple[bool, float, int]:
        return self.host._is_geometry_feasible(design_state)

    def should_accept(
        self,
        old_metrics: Dict[str, Any],
        new_metrics: Dict[str, Any],
        old_violations: List["ViolationItem"],
        new_violations: List["ViolationItem"],
        *,
        allow_penalty_regression: float = 0.0,
        require_cg_improve_on_regression: bool = False,
    ) -> bool:
        return self.host._should_accept(
            old_metrics,
            new_metrics,
            old_violations,
            new_violations,
            allow_penalty_regression=allow_penalty_regression,
            require_cg_improve_on_regression=require_cg_improve_on_regression,
        )

    def learn_from_iteration(
        self,
        iteration: int,
        strategic_plan: Any,
        execution_plan: Any,
        old_metrics: Dict[str, Any],
        new_metrics: Dict[str, Any],
        *,
        success: bool,
    ) -> None:
        self.host._learn_from_iteration(
            iteration,
            strategic_plan,
            execution_plan,
            old_metrics,
            new_metrics,
            success=success,
        )

    # MaaS
    def build_maas_runtime_thermal_evaluator(self, mode: str, base_iteration: int):
        return self.host._build_maas_runtime_thermal_evaluator(mode, base_iteration)

    def propose_maas_mcts_variants(self, *args: Any, **kwargs: Any):
        return self.host._propose_maas_mcts_variants(*args, **kwargs)

    def evaluate_maas_intent_once(self, **kwargs: Any):
        return self.host._evaluate_maas_intent_once(**kwargs)

    def is_maas_retryable(self, diagnosis: Dict[str, Any], *, retry_on_stall: bool) -> bool:
        return self.host._is_maas_retryable(diagnosis, retry_on_stall)

    def apply_relaxation_suggestions_to_intent(self, intent: "ModelingIntent", suggestions: List[Dict[str, Any]], attempt: int):
        return self.host._apply_relaxation_suggestions_to_intent(intent, suggestions, attempt)

    def run_maas_topk_physics_audit(self, **kwargs: Any):
        return self.host._run_maas_topk_physics_audit(**kwargs)
