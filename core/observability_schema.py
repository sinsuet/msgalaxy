"""
Typed event schemas for MaaS observability.

Phase-1 scope:
- run manifest
- phase events (A/B/C/D)
- attempt events
- policy events
- physics events
- selected candidate events
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now().isoformat()


class _BaseEvent(BaseModel):
    """Shared fields for line-delimited MaaS events."""

    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    timestamp: str = Field(default_factory=_now_iso)
    iteration: int = 0
    attempt: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunManifestEvent(BaseModel):
    """Single-file run-level manifest."""

    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    run_dir: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    optimization_mode: str = ""
    pymoo_algorithm: str = ""
    thermal_evaluator_mode: str = ""
    search_space_mode: str = ""
    profile: str = ""
    level: str = ""
    seed: Optional[int] = None
    status: str = ""
    final_iteration: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class PhaseEvent(_BaseEvent):
    """A/B/C/D phase transitions."""

    phase: Literal["A", "B", "C", "D"] = "A"
    status: Literal["started", "completed", "failed"] = "started"
    details: Dict[str, Any] = Field(default_factory=dict)


class AttemptEvent(_BaseEvent):
    """Attempt-level objective/constraint diagnostics."""

    branch_action: str = ""
    branch_source: str = ""
    search_space_mode: str = ""
    pymoo_algorithm: str = ""
    thermal_evaluator_mode: str = ""
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    solver_message: str = ""
    solver_cost: Optional[float] = None
    score: Optional[float] = None
    best_cv: Optional[float] = None
    aocc_cv: Optional[float] = None
    aocc_objective: Optional[float] = None
    dominant_violation: str = ""
    constraint_violation_breakdown: Dict[str, float] = Field(default_factory=dict)
    best_candidate_metrics: Dict[str, float] = Field(default_factory=dict)
    operator_program_id: str = ""
    operator_actions: List[str] = Field(default_factory=list)
    operator_bias_strategy: str = ""
    mcts_enabled: bool = False
    has_candidate_state: bool = False
    is_best_attempt: bool = False


class GenerationEvent(_BaseEvent):
    """Solver generation-level convergence snapshots."""

    generation: int = 0
    pymoo_algorithm: str = ""
    branch_action: str = ""
    branch_source: str = ""
    search_space_mode: str = ""
    population_size: int = 0
    feasible_count: int = 0
    feasible_ratio: Optional[float] = None
    best_cv: Optional[float] = None
    mean_cv: Optional[float] = None
    best_feasible_sum_f: Optional[float] = None


class PolicyEvent(_BaseEvent):
    """Runtime meta-policy updates."""

    mode: str = ""
    applied: bool = False
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    applied_knobs: Dict[str, Any] = Field(default_factory=dict)
    planner_policy_weights: Dict[str, Any] = Field(default_factory=dict)


class PhysicsEvent(_BaseEvent):
    """Physics/multi-fidelity runtime records."""

    event_type: str = ""
    simulation_backend: str = ""
    selected_reason: str = ""
    thermal_mode: str = ""
    stats: Dict[str, Any] = Field(default_factory=dict)
    records_count: Optional[int] = None


class CandidateEvent(_BaseEvent):
    """Selected candidate snapshots (best attempt / final)."""

    source: str = ""
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    best_cv: Optional[float] = None
    dominant_violation: str = ""
    best_candidate_metrics: Dict[str, float] = Field(default_factory=dict)
    physics_audit_selected_reason: str = ""
    is_selected: bool = False


class LayoutEvent(_BaseEvent):
    """Layout snapshot timeline event for frame-by-frame visualization."""

    sequence: int = 0
    stage: str = ""  # initial | attempt_candidate | final_selected | ...
    snapshot_path: str = ""
    thermal_source: str = ""  # proxy | comsol | unknown
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    branch_action: str = ""
    branch_source: str = ""
    operator_program_id: str = ""
    operator_actions: List[str] = Field(default_factory=list)
    moved_components: List[str] = Field(default_factory=list)
    added_heatsinks: List[str] = Field(default_factory=list)
    added_brackets: List[str] = Field(default_factory=list)
    changed_contacts: List[str] = Field(default_factory=list)
    changed_coatings: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
