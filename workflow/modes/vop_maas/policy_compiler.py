"""
Mock-policy generation and cheap counterfactual screening for VOP-MaaS.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from core.protocol import DesignState
from optimization.modes.mass.operator_physics_matrix import action_family
from optimization.modes.mass.operator_program import OperatorAction, OperatorProgram

from .contracts import (
    VOPOperatorCandidate,
    VOPFidelityPlan,
    VOPPolicyPack,
    VOPRuntimeKnobPriors,
)


def build_mock_policy_pack(
    *,
    graph: Any,
    current_state: DesignState,
    runtime_constraints: Dict[str, Any],
    max_candidates: int = 3,
) -> VOPPolicyPack:
    """Build a deterministic mock policy for smoke tests and offline bring-up."""
    component_ids = [
        str(getattr(comp, "id", "") or "").strip()
        for comp in list(getattr(current_state, "components", []) or [])
        if str(getattr(comp, "id", "") or "").strip()
    ]
    dominant = str(getattr(graph, "dominant_violation_family", "") or "").strip().lower()
    focus = _constraint_focus_for_family(dominant)
    return VOPPolicyPack(
        policy_id=f"VOP_POLICY_MOCK_{str(getattr(graph, 'iteration', 1)).zfill(2)}",
        constraint_focus=focus,
        operator_candidates=_build_operator_candidates(
            dominant_family=dominant,
            component_ids=component_ids,
            limit=max_candidates,
        ),
        search_space_prior=_search_space_for_family(dominant),
        runtime_knob_priors=VOPRuntimeKnobPriors(**_runtime_knobs_for_family(dominant)),
        fidelity_plan=VOPFidelityPlan(
            **_fidelity_plan_for_family(
                dominant_family=dominant,
                graph=graph,
                runtime_constraints=runtime_constraints,
            )
        ),
        confidence=0.62,
        rationale=f"deterministic mock policy for dominant_family={dominant or 'none'}",
        decision_rationale=f"deterministic mock policy for dominant_family={dominant or 'none'}",
        change_set={
            "intent_changes": {},
            "operator_program_ids": [
                str(item.program.program_id or "")
                for item in _build_operator_candidates(
                    dominant_family=dominant,
                    component_ids=component_ids,
                    limit=max_candidates,
                )[:1]
            ],
        },
        expected_effects=_expected_effects_for_focus(focus),
        policy_source="mock_policy",
        metadata={
            "generation_mode": "mock_policy",
            "dominant_family": dominant,
        },
    )


def screen_policy_pack(
    policy_pack: VOPPolicyPack,
    *,
    graph: Any,
    top_k: int = 1,
) -> Dict[str, Any]:
    """Score operator candidates with a cheap counterfactual heuristic."""
    dominant_family = str(getattr(graph, "dominant_violation_family", "") or "").strip().lower()
    focus = {str(item).strip().lower() for item in list(policy_pack.constraint_focus or [])}
    candidate_reports: List[Dict[str, Any]] = []
    ranked_candidates: List[Tuple[float, VOPOperatorCandidate]] = []

    for candidate in list(policy_pack.operator_candidates or []):
        score = float(candidate.priority or 1.0)
        families: List[str] = []
        reasons: List[str] = []
        for action in list(candidate.program.actions or []):
            family = str(action_family(action.action) or "").strip().lower()
            if family:
                families.append(family)
                if family == dominant_family:
                    score += 1.25
                    reasons.append(f"family_match:{family}")
                if family in focus:
                    score += 0.35
                    reasons.append(f"focus_match:{family}")
            if dominant_family == "cg" and action.action in {"cg_recenter", "group_move"}:
                score += 1.10
                reasons.append(f"cg_direct:{action.action}")
        action_count = len(list(candidate.program.actions or []))
        if action_count > 2:
            score -= 0.10 * float(action_count - 2)
            reasons.append("complexity_penalty")
        score += float(policy_pack.confidence or 0.0) * 0.5
        updated = candidate.model_copy(
            update={
                "screening_score": float(score),
                "screening_reason": ",".join(reasons) or "baseline_priority",
            },
            deep=True,
        )
        ranked_candidates.append((float(score), updated))
        candidate_reports.append(
            {
                "candidate_id": str(updated.candidate_id or ""),
                "program_id": str(updated.program.program_id or ""),
                "score": float(score),
                "families": families,
                "reason": str(updated.screening_reason or ""),
            }
        )

    ranked_candidates.sort(key=lambda item: item[0], reverse=True)
    selected_count = max(1, int(top_k))
    selected = [item[1] for item in ranked_candidates[:selected_count]]
    screened_pack = policy_pack.model_copy(
        update={
            "operator_candidates": selected,
            "policy_source": "screened_policy"
            if str(policy_pack.policy_source or "") != "mock_policy"
            else "mock_policy",
        },
        deep=True,
    )
    return {
        "policy": screened_pack,
        "report": {
            "dominant_family": dominant_family,
            "requested_top_k": int(top_k),
            "candidate_count": int(len(candidate_reports)),
            "selected_candidate_ids": [
                str(item.candidate_id or "") for item in list(selected or [])
            ],
            "candidate_scores": candidate_reports,
        },
    }


def _constraint_focus_for_family(family: str) -> List[str]:
    mapping = {
        "geometry": ["geometry", "clearance", "collision", "boundary"],
        "thermal": ["thermal", "max_temp"],
        "structural": ["structural", "max_stress", "safety_factor"],
        "power": ["power", "voltage_drop", "power_margin"],
        "mission": ["mission", "mission_keepout_violation"],
        "cg": ["geometry", "cg_limit", "cg_offset"],
    }
    return list(mapping.get(str(family or "").strip().lower(), ["geometry", "thermal"]))


def _search_space_for_family(family: str) -> str:
    family = str(family or "").strip().lower()
    if family in {"geometry", "mission"}:
        return "operator_program"
    if family in {"thermal", "structural", "power"}:
        return "hybrid"
    if family == "cg":
        return "coordinate"
    return "hybrid"


def _runtime_knobs_for_family(family: str) -> Dict[str, Any]:
    family = str(family or "").strip().lower()
    knobs: Dict[str, Any] = {
        "maas_relax_ratio": 0.08,
        "mcts_action_prior_weight": 0.05,
        "mcts_cv_penalty_weight": 0.28,
    }
    if family in {"geometry", "mission"}:
        knobs["mcts_action_prior_weight"] = 0.12
        knobs["mcts_cv_penalty_weight"] = 0.24
    elif family == "thermal":
        knobs["online_comsol_schedule_mode"] = "ucb_topk"
        knobs["online_comsol_schedule_top_fraction"] = 0.40
        knobs["online_comsol_schedule_explore_prob"] = 0.12
        knobs["online_comsol_schedule_uncertainty_weight"] = 0.42
        knobs["online_comsol_eval_budget"] = 6
    elif family == "power":
        knobs["online_comsol_schedule_mode"] = "ucb_topk"
        knobs["online_comsol_schedule_top_fraction"] = 0.35
        knobs["online_comsol_eval_budget"] = 4
    elif family == "structural":
        knobs["online_comsol_schedule_mode"] = "ucb_topk"
        knobs["online_comsol_schedule_top_fraction"] = 0.30
        knobs["online_comsol_eval_budget"] = 4
    elif family == "cg":
        knobs["maas_relax_ratio"] = 0.05
        knobs["mcts_action_prior_weight"] = 0.03
    return knobs


def _fidelity_plan_for_family(
    *,
    dominant_family: str,
    graph: Any,
    runtime_constraints: Dict[str, Any],
) -> Dict[str, Any]:
    backend = str(getattr(graph, "metadata", {}).get("simulation_backend", "") or "").strip().lower()
    family = str(dominant_family or "").strip().lower()
    plan: Dict[str, Any] = {
        "physics_audit_top_k": 1,
    }
    if backend == "comsol" and family in {"thermal", "structural", "power"}:
        plan["thermal_evaluator_mode"] = "online_comsol"
        plan["online_comsol_eval_budget"] = max(
            int(runtime_constraints.get("online_comsol_eval_budget", 0) or 0),
            4,
        )
    return plan


def _expected_effects_for_focus(focus: Sequence[str]) -> Dict[str, float]:
    payload: Dict[str, float] = {}
    focus_set = {str(item).strip().lower() for item in list(focus or [])}
    if "max_temp" in focus_set or "thermal" in focus_set:
        payload["max_temp"] = -2.5
    if "clearance" in focus_set or "geometry" in focus_set:
        payload["min_clearance"] = 2.0
    if "cg_offset" in focus_set or "cg_limit" in focus_set:
        payload["cg_offset"] = -3.0
    if "voltage_drop" in focus_set:
        payload["voltage_drop"] = -0.08
    if "power_margin" in focus_set:
        payload["power_margin"] = 3.0
    if "mission_keepout_violation" in focus_set:
        payload["mission_keepout_violation"] = -1.0
    return payload


def _build_operator_candidates(
    *,
    dominant_family: str,
    component_ids: Sequence[str],
    limit: int,
) -> List[VOPOperatorCandidate]:
    family = str(dominant_family or "").strip().lower()
    if family == "thermal":
        programs = _thermal_programs(component_ids)
    elif family == "structural":
        programs = _structural_programs(component_ids)
    elif family == "power":
        programs = _power_programs(component_ids)
    elif family == "mission":
        programs = _mission_programs(component_ids)
    elif family == "cg":
        programs = _cg_programs(component_ids)
    else:
        programs = _geometry_programs(component_ids)
    candidates: List[VOPOperatorCandidate] = []
    for idx, program in enumerate(programs[: max(1, int(limit))], start=1):
        candidates.append(
            VOPOperatorCandidate(
                candidate_id=f"{program.program_id}_c{idx}",
                priority=max(0.1, 1.0 - (idx - 1) * 0.15),
                note=f"heuristic_{family or 'geometry'}_candidate_{idx}",
                program=program,
            )
        )
    return candidates


def _geometry_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    selected = _selected_components(component_ids, minimum=2)
    first = selected[0]
    second = selected[1] if len(selected) > 1 else selected[0]
    return [
        OperatorProgram(
            program_id="vop_geom_spacing",
            rationale="Increase spacing for geometry bottlenecks",
            actions=[
                OperatorAction(
                    action="hot_spread",
                    params={
                        "component_ids": selected,
                        "axis": "x",
                        "min_pair_distance_mm": 14.0,
                        "spread_strength": 0.8,
                        "focus_ratio": 0.55,
                    },
                ),
                OperatorAction(
                    action="swap",
                    params={"component_a": first, "component_b": second},
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_geom_center",
            rationale="Recover feasibility with mild recenter + group shift",
            actions=[
                OperatorAction(
                    action="cg_recenter",
                    params={"axes": ["x", "y"], "strength": 0.42, "focus_ratio": 0.72},
                ),
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected,
                        "axis": "x",
                        "delta_mm": 8.0,
                        "focus_ratio": 0.60,
                    },
                ),
            ],
        ),
    ]


def _thermal_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    hot = _selected_components(component_ids, minimum=2)
    source = hot[0]
    targets = hot[1:] or hot[:1]
    return [
        OperatorProgram(
            program_id="vop_thermal_spread",
            rationale="Spread hot components and strengthen thermal path",
            actions=[
                OperatorAction(
                    action="hot_spread",
                    params={
                        "component_ids": hot,
                        "axis": "y",
                        "min_pair_distance_mm": 12.0,
                        "spread_strength": 0.72,
                        "focus_ratio": 0.58,
                    },
                ),
                OperatorAction(
                    action="add_heatstrap",
                    params={
                        "component_ids": hot[:2],
                        "conductance": 120.0,
                        "focus_ratio": 0.68,
                    },
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_thermal_contact",
            rationale="Move toward cooling side and raise thermal conductance",
            actions=[
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": hot,
                        "axis": "y",
                        "delta_mm": 10.0,
                        "focus_ratio": 0.56,
                    },
                ),
                OperatorAction(
                    action="set_thermal_contact",
                    params={
                        "source_component": source,
                        "target_component_ids": targets[: min(3, len(targets))],
                        "conductance": 110.0,
                        "update_mode": "max",
                        "focus_ratio": 0.70,
                    },
                ),
            ],
        ),
    ]


def _structural_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    selected = _selected_components(component_ids, minimum=2)
    return [
        OperatorProgram(
            program_id="vop_struct_support",
            rationale="Insert support priors for structural bottlenecks",
            actions=[
                OperatorAction(
                    action="add_bracket",
                    params={
                        "component_ids": selected,
                        "axes": ["x", "y"],
                        "stiffness_gain": 0.35,
                        "focus_ratio": 0.67,
                    },
                ),
                OperatorAction(
                    action="stiffener_insert",
                    params={
                        "component_ids": selected,
                        "axes": ["x", "y", "z"],
                        "stiffness_gain": 0.42,
                        "focus_ratio": 0.62,
                    },
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_struct_compact",
            rationale="Compact critical cluster before structural reinforcement",
            actions=[
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected,
                        "axis": "x",
                        "delta_mm": 6.0,
                        "focus_ratio": 0.60,
                    },
                ),
                OperatorAction(
                    action="add_bracket",
                    params={
                        "component_ids": selected,
                        "axes": ["x", "y"],
                        "stiffness_gain": 0.30,
                        "focus_ratio": 0.65,
                    },
                ),
            ],
        ),
    ]


def _power_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    selected = _selected_components(component_ids, minimum=2)
    source = selected[0]
    targets = selected[1:] or selected[:1]
    return [
        OperatorProgram(
            program_id="vop_power_bus",
            rationale="Bias search toward shorter bus proximity path",
            actions=[
                OperatorAction(
                    action="bus_proximity_opt",
                    params={
                        "source_component": source,
                        "target_component_ids": targets[: min(3, len(targets))],
                        "axes": ["x", "y"],
                        "focus_ratio": 0.66,
                    },
                ),
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected,
                        "axis": "y",
                        "delta_mm": 6.0,
                        "focus_ratio": 0.55,
                    },
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_power_centered_bus",
            rationale="Combine CG-friendly recenter with bus proximity prior",
            actions=[
                OperatorAction(
                    action="cg_recenter",
                    params={"axes": ["x", "y"], "strength": 0.30, "focus_ratio": 0.75},
                ),
                OperatorAction(
                    action="bus_proximity_opt",
                    params={
                        "source_component": source,
                        "target_component_ids": targets[: min(4, len(targets))],
                        "axes": ["x", "y"],
                        "focus_ratio": 0.68,
                    },
                ),
            ],
        ),
    ]


def _mission_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    selected = _selected_components(component_ids, minimum=1)
    first = selected[0]
    second = selected[1] if len(selected) > 1 else selected[0]
    return [
        OperatorProgram(
            program_id="vop_mission_keepout",
            rationale="Push payload cluster away from keepout region",
            actions=[
                OperatorAction(
                    action="fov_keepout_push",
                    params={
                        "component_ids": selected,
                        "axis": "z",
                        "keepout_center_mm": 0.0,
                        "min_separation_mm": 18.0,
                        "preferred_side": "auto",
                        "focus_ratio": 0.68,
                    },
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_mission_swap_push",
            rationale="Diversify before keepout push",
            actions=[
                OperatorAction(
                    action="swap",
                    params={"component_a": first, "component_b": second},
                ),
                OperatorAction(
                    action="fov_keepout_push",
                    params={
                        "component_ids": selected,
                        "axis": "z",
                        "keepout_center_mm": 0.0,
                        "min_separation_mm": 20.0,
                        "preferred_side": "auto",
                        "focus_ratio": 0.66,
                    },
                ),
            ],
        ),
    ]


def _cg_programs(component_ids: Sequence[str]) -> List[OperatorProgram]:
    selected = _selected_components(component_ids, minimum=2)
    return [
        OperatorProgram(
            program_id="vop_cg_center",
            rationale="Explicit recentering policy for CG-dominant violations",
            actions=[
                OperatorAction(
                    action="cg_recenter",
                    params={"axes": ["x", "y"], "strength": 0.78, "focus_ratio": 0.60},
                ),
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected,
                        "axis": "x",
                        "delta_mm": 0.0,
                        "focus_ratio": 0.62,
                    },
                ),
            ],
        ),
        OperatorProgram(
            program_id="vop_cg_balance",
            rationale="Balance heavy cluster around the centerline",
            actions=[
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected,
                        "axis": "y",
                        "delta_mm": 6.0,
                        "focus_ratio": 0.60,
                    },
                ),
                OperatorAction(
                    action="cg_recenter",
                    params={"axes": ["x", "y"], "strength": 0.55, "focus_ratio": 0.68},
                ),
            ],
        ),
    ]


def _selected_components(component_ids: Sequence[str], *, minimum: int = 2) -> List[str]:
    selected = [str(item).strip() for item in list(component_ids or []) if str(item).strip()]
    if not selected:
        selected = ["component_a", "component_b"]
    if len(selected) < minimum:
        while len(selected) < minimum:
            selected.append(selected[-1])
    return selected[: max(minimum, min(len(selected), 4))]
