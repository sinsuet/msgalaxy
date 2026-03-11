"""
Contracts for the experimental VOP-MaaS mode.

VOP-MaaS keeps pymoo/MaaS as the numeric executor and constrains the LLM to
emit verified operator-policy payloads rather than final coordinates.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optimization.modes.mass.operator_program import OperatorProgram, validate_operator_program
from optimization.modes.mass.operator_program_v4 import OperatorProgramV4
from optimization.modes.mass.operator_realization_v4 import realize_operator_program_v4
from optimization.modes.mass.operator_rule_engine import evaluate_operator_rules_v4


ALLOWED_CONSTRAINT_FOCUS = frozenset(
    {
        "geometry",
        "thermal",
        "structural",
        "power",
        "mission",
        "collision",
        "clearance",
        "boundary",
        "cg_limit",
        "cg_offset",
        "max_temp",
        "max_stress",
        "first_modal_freq",
        "safety_factor",
        "voltage_drop",
        "power_margin",
        "mission_keepout_violation",
    }
)
ALLOWED_SEARCH_SPACE_PRIORS = frozenset({"coordinate", "operator_program", "hybrid"})
ALLOWED_POLICY_SOURCES = frozenset(
    {
        "llm_api",
        "llm_api_autofill",
        "mock_policy",
        "screened_policy",
        "fallback_mass",
        "unsupported",
        "error",
    }
)
ALLOWED_RUNTIME_KNOBS = frozenset(
    {
        "maas_relax_ratio",
        "mcts_action_prior_weight",
        "mcts_cv_penalty_weight",
        "online_comsol_eval_budget",
        "online_comsol_schedule_mode",
        "online_comsol_schedule_top_fraction",
        "online_comsol_schedule_explore_prob",
        "online_comsol_schedule_uncertainty_weight",
    }
)
ALLOWED_SCHEDULE_MODES = frozenset({"budget_only", "ucb_topk"})


class VOPGNode(BaseModel):
    """One node in the violation-operator provenance graph."""

    node_id: str
    node_type: Literal[
        "constraint",
        "metric",
        "component",
        "operator_family",
        "evidence_source",
    ]
    label: str
    attributes: Dict[str, Any] = Field(default_factory=dict)


class VOPGEdge(BaseModel):
    """Typed relation in the VOP graph."""

    source: str
    target: str
    relation: str
    weight: float = 0.0
    attributes: Dict[str, Any] = Field(default_factory=dict)


class VOPGraph(BaseModel):
    """Structured context consumed by the VOP policy layer."""

    graph_id: str
    iteration: int
    dominant_violation_family: str = ""
    dominant_metric: str = ""
    nodes: List[VOPGNode] = Field(default_factory=list)
    edges: List[VOPGEdge] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class VOPRuntimeKnobPriors(BaseModel):
    """Runtime priors allowed to bias MaaS without bypassing it."""

    model_config = ConfigDict(extra="ignore")

    maas_relax_ratio: Optional[float] = None
    mcts_action_prior_weight: Optional[float] = None
    mcts_cv_penalty_weight: Optional[float] = None
    online_comsol_eval_budget: Optional[int] = None
    online_comsol_schedule_mode: Optional[str] = None
    online_comsol_schedule_top_fraction: Optional[float] = None
    online_comsol_schedule_explore_prob: Optional[float] = None
    online_comsol_schedule_uncertainty_weight: Optional[float] = None

    def to_runtime_overrides(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.maas_relax_ratio is not None:
            payload["maas_relax_ratio"] = max(0.0, min(float(self.maas_relax_ratio), 0.30))
        if self.mcts_action_prior_weight is not None:
            payload["mcts_action_prior_weight"] = max(
                0.0,
                min(float(self.mcts_action_prior_weight), 2.0),
            )
        if self.mcts_cv_penalty_weight is not None:
            payload["mcts_cv_penalty_weight"] = max(
                0.0,
                min(float(self.mcts_cv_penalty_weight), 2.0),
            )
        if self.online_comsol_eval_budget is not None:
            payload["online_comsol_eval_budget"] = max(int(self.online_comsol_eval_budget), 0)
        if self.online_comsol_schedule_mode is not None:
            mode = str(self.online_comsol_schedule_mode).strip().lower()
            if mode in ALLOWED_SCHEDULE_MODES:
                payload["online_comsol_schedule_mode"] = mode
        if self.online_comsol_schedule_top_fraction is not None:
            payload["online_comsol_schedule_top_fraction"] = max(
                0.01,
                min(float(self.online_comsol_schedule_top_fraction), 1.0),
            )
        if self.online_comsol_schedule_explore_prob is not None:
            payload["online_comsol_schedule_explore_prob"] = max(
                0.0,
                min(float(self.online_comsol_schedule_explore_prob), 1.0),
            )
        if self.online_comsol_schedule_uncertainty_weight is not None:
            payload["online_comsol_schedule_uncertainty_weight"] = max(
                0.0,
                min(float(self.online_comsol_schedule_uncertainty_weight), 5.0),
            )
        return payload


class VOPFidelityPlan(BaseModel):
    """Bounded multi-fidelity hints for the delegated MaaS execution."""

    model_config = ConfigDict(extra="ignore")

    thermal_evaluator_mode: Optional[Literal["proxy", "online_comsol"]] = None
    online_comsol_eval_budget: Optional[int] = None
    physics_audit_top_k: Optional[int] = None

    def to_runtime_overrides(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.thermal_evaluator_mode is not None:
            payload["thermal_evaluator_mode"] = str(self.thermal_evaluator_mode)
        if self.online_comsol_eval_budget is not None:
            payload["online_comsol_eval_budget"] = max(int(self.online_comsol_eval_budget), 0)
        if self.physics_audit_top_k is not None:
            payload["physics_audit_top_k"] = max(int(self.physics_audit_top_k), 1)
        return payload


class VOPOperatorCandidate(BaseModel):
    """One candidate operator program emitted by the policy layer."""

    model_config = ConfigDict(extra="ignore")

    candidate_id: str
    priority: float = 1.0
    note: str = ""
    program: Optional[OperatorProgram | Dict[str, Any]] = None
    program_v4: Optional[OperatorProgramV4 | Dict[str, Any]] = None
    dsl_version: str = ""
    rule_engine_report: Dict[str, Any] = Field(default_factory=dict)
    realization: Dict[str, Any] = Field(default_factory=dict)
    screening_score: float = 0.0
    screening_reason: str = ""


class VOPPolicyPack(BaseModel):
    """Validated VOP policy payload."""

    model_config = ConfigDict(extra="ignore")

    policy_id: str
    version: str = "vop-maas-v1"
    constraint_focus: List[str] = Field(default_factory=list)
    operator_candidates: List[VOPOperatorCandidate] = Field(default_factory=list)
    search_space_prior: Literal["coordinate", "operator_program", "hybrid"] = "hybrid"
    runtime_knob_priors: VOPRuntimeKnobPriors = Field(default_factory=VOPRuntimeKnobPriors)
    fidelity_plan: VOPFidelityPlan = Field(default_factory=VOPFidelityPlan)
    confidence: float = 0.0
    rationale: str = ""
    decision_rationale: str = ""
    change_set: Dict[str, Any] = Field(default_factory=dict)
    expected_effects: Dict[str, float] = Field(default_factory=dict)
    policy_source: str = "llm_api"
    validation_state: Literal["valid", "repaired", "rejected"] = "valid"
    rejection_reason: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_runtime_priors(self) -> Dict[str, Any]:
        selected_candidate = (
            self.operator_candidates[0].model_dump()
            if self.operator_candidates
            else {}
        )
        return {
            "policy_id": str(self.policy_id or ""),
            "version": str(self.version or ""),
            "constraint_focus": list(self.constraint_focus or []),
            "search_space_prior": str(self.search_space_prior or ""),
            "runtime_knob_priors": self.runtime_knob_priors.to_runtime_overrides(),
            "fidelity_plan": self.fidelity_plan.to_runtime_overrides(),
            "selected_operator_candidate": dict(selected_candidate or {}),
            "operator_candidates": [
                item.model_dump() for item in list(self.operator_candidates or [])
            ],
            "confidence": float(self.confidence or 0.0),
            "decision_rationale": str(self.decision_rationale or self.rationale or ""),
            "change_set": dict(self.change_set or {}),
            "expected_effects": dict(self.expected_effects or {}),
            "policy_source": str(self.policy_source or ""),
            "validation_state": str(self.validation_state or ""),
            "metadata": dict(self.metadata or {}),
        }


class VOPPolicyFeedback(BaseModel):
    """Observed effect summary for one delegated VOP -> MaaS policy round."""

    model_config = ConfigDict(extra="ignore")

    policy_id: str = ""
    policy_source: str = ""
    applied: bool = False
    constraint_focus: List[str] = Field(default_factory=list)
    requested_search_space: str = ""
    effective_search_space: str = ""
    runtime_overrides: Dict[str, Any] = Field(default_factory=dict)
    fidelity_overrides: Dict[str, Any] = Field(default_factory=dict)
    effective_fidelity: Dict[str, Any] = Field(default_factory=dict)
    selected_operator_program_id: str = ""
    selected_operator_actions: List[str] = Field(default_factory=list)
    selected_semantic_program_id: str = ""
    selected_semantic_actions: List[str] = Field(default_factory=list)
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    feasible_rate: Optional[float] = None
    best_cv_min: Optional[float] = None
    best_cv_min_source: str = ""
    first_feasible_eval: Optional[int] = None
    comsol_calls_to_first_feasible: Optional[int] = None
    trace_alerts: List[str] = Field(default_factory=list)
    runtime_thermal: Dict[str, Any] = Field(default_factory=dict)
    physics_audit: Dict[str, Any] = Field(default_factory=dict)
    fallback_attribution: Dict[str, Any] = Field(default_factory=dict)
    failure_signature: str = ""
    fidelity_escalation_allowed: bool = False
    fidelity_escalation_reason: str = ""
    replan_recommended: bool = False
    replan_reason: str = ""


class VOPReflectiveReplanReport(BaseModel):
    """Bounded reflective replanning bookkeeping for experimental mode."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    triggered: bool = False
    trigger_reason: str = ""
    rounds_requested: int = 0
    rounds_completed: int = 0
    previous_policy_id: str = ""
    candidate_policy_id: str = ""
    final_policy_id: str = ""
    executed_mass_rerun: bool = False
    skipped_reason: str = ""


def validate_vop_policy_pack(
    policy: VOPPolicyPack | Mapping[str, Any],
    *,
    component_ids: Iterable[str],
    object_catalog: Optional[Mapping[str, Any]] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Validate and normalize a VOP policy pack.

    This function never silently accepts unsupported operator actions or
    non-allowlisted runtime knobs.
    """
    errors: List[str] = []
    warnings: List[str] = []
    raw_payload = dict(policy or {})
    allowed_keys = {
        "policy_id",
        "version",
        "constraint_focus",
        "operator_candidates",
        "search_space_prior",
        "runtime_knob_priors",
        "fidelity_plan",
        "confidence",
        "rationale",
        "decision_rationale",
        "change_set",
        "expected_effects",
        "policy_source",
        "validation_state",
        "rejection_reason",
        "metadata",
    }
    extra_keys = sorted(set(raw_payload.keys()) - allowed_keys)
    if extra_keys:
        warnings.append(f"unknown_policy_keys_ignored={','.join(extra_keys)}")

    try:
        parsed = (
            policy
            if isinstance(policy, VOPPolicyPack)
            else VOPPolicyPack.model_validate(raw_payload)
        )
    except ValidationError as exc:
        return {
            "is_valid": False,
            "state": "rejected",
            "errors": [str(exc)],
            "warnings": warnings,
            "policy": None,
        }
    except Exception as exc:
        return {
            "is_valid": False,
            "state": "rejected",
            "errors": [str(exc)],
            "warnings": warnings,
            "policy": None,
        }

    normalized_focus: List[str] = []
    for raw_focus in list(parsed.constraint_focus or []):
        focus = str(raw_focus or "").strip().lower()
        if not focus:
            continue
        if focus not in ALLOWED_CONSTRAINT_FOCUS:
            warnings.append(f"unknown_constraint_focus_ignored={focus}")
            continue
        if focus not in normalized_focus:
            normalized_focus.append(focus)

    policy_source = str(parsed.policy_source or "").strip().lower()
    if policy_source and policy_source not in ALLOWED_POLICY_SOURCES:
        warnings.append(f"unknown_policy_source_repaired={policy_source}")
        parsed.policy_source = "llm_api"

    runtime_overrides = parsed.runtime_knob_priors.to_runtime_overrides()
    unknown_runtime_keys = sorted(
        set((parsed.runtime_knob_priors.model_dump(exclude_none=True) or {}).keys())
        - ALLOWED_RUNTIME_KNOBS
    )
    if unknown_runtime_keys:
        warnings.append(
            f"unknown_runtime_knobs_ignored={','.join(unknown_runtime_keys)}"
        )
    parsed.runtime_knob_priors = VOPRuntimeKnobPriors(**runtime_overrides)
    parsed.fidelity_plan = VOPFidelityPlan(
        **parsed.fidelity_plan.to_runtime_overrides()
    )

    normalized_candidates: List[VOPOperatorCandidate] = []
    component_set = [str(item).strip() for item in component_ids if str(item).strip()]
    v4_object_catalog = _merge_v4_object_catalog(
        component_ids=component_set,
        object_catalog=object_catalog,
    )
    for index, candidate in enumerate(list(parsed.operator_candidates or []), start=1):
        candidate_id = str(candidate.candidate_id or f"candidate_{index:02d}")
        priority = float(candidate.priority or 1.0)
        raw_program_v4 = candidate.program_v4
        raw_program = candidate.program

        if raw_program_v4 in (None, {}) and _looks_like_v4_program(raw_program):
            raw_program_v4 = raw_program

        if raw_program_v4 not in (None, {}):
            rule_report = evaluate_operator_rules_v4(
                raw_program_v4,
                object_catalog=v4_object_catalog,
                strict=bool(strict),
            )
            if not rule_report.get("is_valid", False):
                message = " | ".join(list(rule_report.get("errors", []) or []))
                warnings.append(f"candidate[{index}] dropped: {message}")
                continue
            realization = realize_operator_program_v4(
                raw_program_v4,
                binding_catalog=v4_object_catalog,
                allow_stub=not bool(strict),
            )
            if not realization.get("is_valid", False):
                message = " | ".join(list(realization.get("errors", []) or []))
                warnings.append(f"candidate[{index}] dropped: {message}")
                continue
            stubbed_actions = list(
                dict(realization.get("summary", {}) or {}).get("stubbed_actions", []) or []
            )
            if strict and stubbed_actions:
                warnings.append(
                    "candidate[{}] dropped: strict_v4_stubbed_realization={}".format(
                        index,
                        ",".join(
                            str(item).strip()
                            for item in stubbed_actions
                            if str(item).strip()
                        ),
                    )
                )
                continue
            normalized_candidates.append(
                candidate.model_copy(
                    update={
                        "program": realization["program"],
                        "program_v4": rule_report.get("program"),
                        "dsl_version": "v4",
                        "rule_engine_report": _strip_program_from_report(rule_report),
                        "realization": dict(realization.get("summary", {}) or {}),
                        "candidate_id": candidate_id,
                        "priority": priority,
                    },
                    deep=True,
                )
            )
            warnings.extend(
                f"candidate[{index}] {item}"
                for item in list(rule_report.get("warnings", []) or [])
            )
            warnings.extend(
                f"candidate[{index}] {item}"
                for item in list(realization.get("warnings", []) or [])
            )
            continue

        if raw_program in (None, {}):
            warnings.append(f"candidate[{index}] dropped: missing_program_payload")
            continue

        validated_program = validate_operator_program(
            raw_program,
            component_ids=component_set,
            max_actions=10,
        )
        if not validated_program.get("is_valid", False):
            message = " | ".join(list(validated_program.get("errors", []) or []))
            warnings.append(f"candidate[{index}] dropped: {message}")
            continue
        normalized_program = validated_program["program"]
        normalized_candidates.append(
            candidate.model_copy(
                update={
                    "program": normalized_program,
                    "program_v4": None,
                    "dsl_version": str(candidate.dsl_version or "v3"),
                    "candidate_id": candidate_id,
                    "priority": priority,
                },
                deep=True,
            )
        )

    parsed.constraint_focus = normalized_focus
    parsed.operator_candidates = normalized_candidates[:4]
    parsed.confidence = max(0.0, min(float(parsed.confidence or 0.0), 1.0))

    if not parsed.operator_candidates and parsed.search_space_prior == "operator_program":
        if strict:
            errors.append("operator_program prior requires at least one valid operator candidate")
        else:
            parsed.search_space_prior = "hybrid"
            warnings.append("search_space_prior repaired: operator_program -> hybrid (no valid candidates)")

    if not str(parsed.policy_id or "").strip():
        errors.append("policy_id must not be empty")

    if errors:
        parsed.validation_state = "rejected"
        parsed.rejection_reason = " | ".join(errors)
        return {
            "is_valid": False,
            "state": "rejected",
            "errors": errors,
            "warnings": warnings,
            "policy": parsed,
        }

    state = "repaired" if warnings else "valid"
    parsed.validation_state = state
    parsed.rejection_reason = ""
    return {
        "is_valid": True,
        "state": state,
        "errors": [],
        "warnings": warnings,
        "policy": parsed,
    }


def _looks_like_v4_program(payload: Any) -> bool:
    if isinstance(payload, OperatorProgramV4):
        return True
    if not isinstance(payload, dict):
        return False
    version = str(
        payload.get("version")
        or payload.get("dsl_version")
        or payload.get("semantic_version")
        or ""
    ).strip().lower()
    if version.endswith("r4") or version.endswith("v4"):
        return True
    for action in list(payload.get("actions", []) or []):
        if not isinstance(action, dict):
            continue
        if "targets" in action or "hard_rules" in action or "soft_preferences" in action:
            return True
        if any(
            key in action
            for key in (
                "panel_id",
                "aperture_id",
                "zone_id",
                "mount_site_id",
                "component_group_id",
            )
        ):
            return True
    return False


def _strip_program_from_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(report or {}).items()
        if str(key) != "program"
    }


def _merge_v4_object_catalog(
    *,
    component_ids: Iterable[str],
    object_catalog: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if object_catalog:
        for object_type, payload in dict(object_catalog).items():
            normalized_type = str(object_type or "").strip().lower()
            if not normalized_type:
                continue
            if isinstance(payload, Mapping):
                merged[normalized_type] = dict(payload)
                continue
            merged[normalized_type] = _normalize_catalog_values(payload)

    normalized_components = _normalize_catalog_values(component_ids)
    current_components = merged.get("component")
    if isinstance(current_components, Mapping):
        component_map = dict(current_components)
        for component_id in normalized_components:
            component_map.setdefault(component_id, {})
        merged["component"] = component_map
    else:
        merged["component"] = sorted(
            set(_normalize_catalog_values(current_components)).union(normalized_components)
        )
    return merged


def _normalize_catalog_values(payload: Any) -> List[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        raw_items = [item.strip() for item in payload.split(",")]
    else:
        try:
            raw_items = [str(item).strip() for item in list(payload or [])]
        except Exception:
            return []
    deduped: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
