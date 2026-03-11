"""
Mock-policy generation and cheap counterfactual screening for VOP-MaaS.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from core.protocol import DesignState
from optimization.modes.mass.operator_physics_matrix import action_family
from optimization.modes.mass.operator_program import OperatorAction, OperatorProgram
from optimization.modes.mass.operator_program_v4 import DSL_V4_VERSION

from .contracts import (
    VOPOperatorCandidate,
    VOPFidelityPlan,
    VOPPolicyPack,
    VOPRuntimeKnobPriors,
)

SEMANTIC_ACTION_FAMILY: Dict[str, str] = {
    "place_on_panel": "geometry",
    "align_payload_to_aperture": "mission",
    "reorient_to_allowed_face": "geometry",
    "mount_to_bracket_site": "structural",
    "move_heat_source_to_radiator_zone": "thermal",
    "separate_hot_pair": "thermal",
    "add_heatstrap": "thermal",
    "add_thermal_pad": "thermal",
    "add_mount_bracket": "structural",
    "rebalance_cg_by_group_shift": "cg",
    "shorten_power_bus": "power",
    "protect_fov_keepout": "mission",
    "activate_aperture_site": "mission",
}


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
    operator_candidates = _build_operator_candidates(
        dominant_family=dominant,
        component_ids=component_ids,
        graph=graph,
        runtime_constraints=runtime_constraints,
        limit=max_candidates,
    )
    return VOPPolicyPack(
        policy_id=f"VOP_POLICY_MOCK_{str(getattr(graph, 'iteration', 1)).zfill(2)}",
        constraint_focus=focus,
        operator_candidates=operator_candidates,
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
                for item in operator_candidates[:1]
                if item.program is not None
            ],
            "semantic_program_ids": [
                str(dict(item.program_v4 or {}).get("program_id", "") or "")
                for item in operator_candidates[:1]
                if dict(item.program_v4 or {}).get("program_id")
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
    ranked_candidates: List[Tuple[float, VOPOperatorCandidate]] = []

    for candidate in list(policy_pack.operator_candidates or []):
        report = _score_candidate_report(
            candidate,
            dominant_family=dominant_family,
            focus=focus,
            confidence=float(policy_pack.confidence or 0.0),
        )
        score = float(report.get("score", 0.0) or 0.0)
        updated = candidate.model_copy(
            update={
                "screening_score": float(score),
                "screening_reason": str(report.get("reason", "") or "baseline_priority"),
            },
            deep=True,
        )
        ranked_candidates.append((float(score), updated))

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
    report = build_policy_candidate_report(
        policy_pack,
        graph=graph,
        requested_top_k=top_k,
        selected_candidates=selected,
    )
    return {
        "policy": screened_pack,
        "report": report,
    }


def build_policy_candidate_report(
    policy_pack: VOPPolicyPack,
    *,
    graph: Any,
    requested_top_k: int,
    selected_candidates: Sequence[VOPOperatorCandidate],
) -> Dict[str, Any]:
    dominant_family = str(getattr(graph, "dominant_violation_family", "") or "").strip().lower()
    focus = {str(item).strip().lower() for item in list(policy_pack.constraint_focus or [])}
    candidate_reports: List[Dict[str, Any]] = []
    selected_ids = [
        str(item.candidate_id or "").strip()
        for item in list(selected_candidates or [])
        if str(item.candidate_id or "").strip()
    ]
    selected_operator_program_ids: List[str] = []
    selected_semantic_program_ids: List[str] = []
    selected_operator_actions: List[str] = []
    selected_semantic_actions: List[str] = []
    seen_operator_ids: set[str] = set()
    seen_semantic_ids: set[str] = set()
    seen_operator_actions: set[str] = set()
    seen_semantic_actions: set[str] = set()

    for candidate in list(policy_pack.operator_candidates or []):
        report = _score_candidate_report(
            candidate,
            dominant_family=dominant_family,
            focus=focus,
            confidence=float(policy_pack.confidence or 0.0),
        )
        candidate_reports.append(report)
        if str(report.get("candidate_id", "") or "") not in selected_ids:
            continue
        operator_program_id = str(report.get("operator_program_id", "") or "")
        semantic_program_id = str(report.get("semantic_program_id", "") or "")
        if operator_program_id and operator_program_id not in seen_operator_ids:
            seen_operator_ids.add(operator_program_id)
            selected_operator_program_ids.append(operator_program_id)
        if semantic_program_id and semantic_program_id not in seen_semantic_ids:
            seen_semantic_ids.add(semantic_program_id)
            selected_semantic_program_ids.append(semantic_program_id)
        for action_name in list(report.get("operator_actions", []) or []):
            normalized = str(action_name or "").strip().lower()
            if normalized and normalized not in seen_operator_actions:
                seen_operator_actions.add(normalized)
                selected_operator_actions.append(normalized)
        for action_name in list(report.get("semantic_actions", []) or []):
            normalized = str(action_name or "").strip().lower()
            if normalized and normalized not in seen_semantic_actions:
                seen_semantic_actions.add(normalized)
                selected_semantic_actions.append(normalized)

    return {
        "dominant_family": dominant_family,
        "requested_top_k": int(requested_top_k),
        "candidate_count": int(len(candidate_reports)),
        "selected_candidate_ids": selected_ids,
        "selected_operator_program_ids": selected_operator_program_ids,
        "selected_semantic_program_ids": selected_semantic_program_ids,
        "selected_operator_actions": selected_operator_actions,
        "selected_semantic_actions": selected_semantic_actions,
        "candidate_scores": candidate_reports,
    }


def _score_candidate_report(
    candidate: VOPOperatorCandidate,
    *,
    dominant_family: str,
    focus: set[str],
    confidence: float,
) -> Dict[str, Any]:
    score = float(candidate.priority or 1.0)
    operator_action_names = _extract_candidate_operator_action_names(candidate)
    semantic_action_names = _extract_candidate_semantic_action_names(candidate)
    scoring_action_names = (
        list(semantic_action_names) if semantic_action_names else list(operator_action_names)
    )
    families = _extract_candidate_families(scoring_action_names)
    reasons: List[str] = []
    seen_family_matches: set[str] = set()
    seen_focus_matches: set[str] = set()
    for family in families:
        if family == dominant_family and family not in seen_family_matches:
            score += 1.25
            reasons.append(f"family_match:{family}")
            seen_family_matches.add(family)
        if family in focus and family not in seen_focus_matches:
            score += 0.35
            reasons.append(f"focus_match:{family}")
            seen_focus_matches.add(family)
    if dominant_family == "cg":
        for action_name in scoring_action_names:
            if action_name in {
                "cg_recenter",
                "group_move",
                "rebalance_cg_by_group_shift",
            }:
                score += 1.10
                reasons.append(f"cg_direct:{action_name}")
                break
    action_count = len(scoring_action_names)
    if action_count > 2:
        score -= 0.10 * float(action_count - 2)
        reasons.append("complexity_penalty")
    stubbed_actions = _candidate_stubbed_actions(candidate)
    if stubbed_actions:
        score -= 0.85 + 0.10 * float(max(len(stubbed_actions) - 1, 0))
        reasons.append("stub_penalty")
    score += float(confidence) * 0.5
    return {
        "candidate_id": str(candidate.candidate_id or ""),
        "program_id": _candidate_program_id(candidate),
        "operator_program_id": _candidate_operator_program_id(candidate),
        "semantic_program_id": _candidate_semantic_program_id(candidate),
        "dsl_version": _candidate_dsl_version(candidate),
        "score": float(score),
        "families": families,
        "actions": scoring_action_names,
        "operator_actions": operator_action_names,
        "semantic_actions": semantic_action_names,
        "stubbed_actions": stubbed_actions,
        "has_stub_realization": bool(stubbed_actions),
        "realization_status": str(
            dict(candidate.realization or {}).get("realization_status", "") or ""
        ),
        "scoring_basis": "semantic_v4" if semantic_action_names else "legacy_v3",
        "reason": ",".join(reasons) or "baseline_priority",
    }


def _extract_candidate_families(action_names: Sequence[str]) -> List[str]:
    families: List[str] = []
    seen_families: set[str] = set()
    for action_name in list(action_names or []):
        family = _action_family_from_name(action_name)
        if family and family not in seen_families:
            seen_families.add(family)
            families.append(family)
    return families


def _extract_candidate_operator_action_names(candidate: VOPOperatorCandidate) -> List[str]:
    action_names: List[str] = []
    program = candidate.program
    if isinstance(program, OperatorProgram):
        for action in list(program.actions or []):
            normalized = str(getattr(action, "action", "") or "").strip().lower()
            if normalized:
                action_names.append(normalized)
    if isinstance(program, Mapping):
        for action in list(dict(program).get("actions", []) or []):
            normalized = str(dict(action or {}).get("action", "") or "").strip().lower()
            if normalized:
                action_names.append(normalized)
    return action_names


def _extract_candidate_semantic_action_names(candidate: VOPOperatorCandidate) -> List[str]:
    action_names: List[str] = []
    program_v4 = candidate.program_v4
    if hasattr(program_v4, "actions"):
        raw_actions = list(getattr(program_v4, "actions", []) or [])
    else:
        raw_actions = list(dict(program_v4 or {}).get("actions", []) or [])
    for action in raw_actions:
        if isinstance(action, dict):
            normalized = str(action.get("action", "") or "").strip().lower()
        else:
            normalized = str(getattr(action, "action", "") or "").strip().lower()
        if normalized:
            action_names.append(normalized)
    return action_names


def _action_family_from_name(action_name: str) -> str:
    normalized_action = str(action_name or "").strip().lower()
    if not normalized_action:
        return ""
    if normalized_action in SEMANTIC_ACTION_FAMILY:
        return str(SEMANTIC_ACTION_FAMILY.get(normalized_action, "") or "")
    return str(action_family(normalized_action) or "").strip().lower()


def _candidate_program_id(candidate: VOPOperatorCandidate) -> str:
    semantic_program_id = _candidate_semantic_program_id(candidate)
    if semantic_program_id:
        return semantic_program_id
    return _candidate_operator_program_id(candidate)


def _candidate_operator_program_id(candidate: VOPOperatorCandidate) -> str:
    program = candidate.program
    if isinstance(program, OperatorProgram):
        program_id = str(program.program_id or "").strip()
        if program_id:
            return program_id
    if isinstance(program, Mapping):
        program_id = str(dict(program).get("program_id", "") or "").strip()
        if program_id:
            return program_id
    return ""


def _candidate_semantic_program_id(candidate: VOPOperatorCandidate) -> str:
    program_v4 = candidate.program_v4
    if hasattr(program_v4, "program_id"):
        program_id = str(getattr(program_v4, "program_id", "") or "").strip()
        if program_id:
            return program_id
    return str(dict(program_v4 or {}).get("program_id", "") or "").strip()


def _candidate_dsl_version(candidate: VOPOperatorCandidate) -> str:
    explicit = str(candidate.dsl_version or "").strip().lower()
    if explicit:
        return explicit
    if candidate.program_v4 not in (None, {}):
        return "v4"
    if candidate.program is not None:
        return "v3"
    return ""


def _candidate_stubbed_actions(candidate: VOPOperatorCandidate) -> List[str]:
    realization = dict(candidate.realization or {})
    return [
        str(item).strip().lower()
        for item in list(realization.get("stubbed_actions", []) or [])
        if str(item).strip()
    ]


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
    graph: Any,
    runtime_constraints: Dict[str, Any],
    limit: int,
) -> List[VOPOperatorCandidate]:
    family = str(dominant_family or "").strip().lower()
    binding_catalog = _binding_catalog_from_graph(graph)
    if family == "thermal":
        programs = _thermal_programs(component_ids)
        semantic_programs = _thermal_programs_v4(
            component_ids,
            binding_catalog=binding_catalog,
        )
    elif family == "structural":
        programs = _structural_programs(component_ids)
        semantic_programs = _structural_programs_v4(
            component_ids,
            binding_catalog=binding_catalog,
        )
    elif family == "power":
        programs = _power_programs(component_ids)
        semantic_programs = _power_programs_v4(component_ids)
    elif family == "mission":
        programs = _mission_programs(component_ids)
        semantic_programs = _mission_programs_v4(
            component_ids,
            binding_catalog=binding_catalog,
            runtime_constraints=runtime_constraints,
        )
    elif family == "cg":
        programs = _cg_programs(component_ids)
        semantic_programs = _cg_programs_v4(component_ids)
    else:
        programs = _geometry_programs(component_ids)
        semantic_programs = _geometry_programs_v4(
            component_ids,
            binding_catalog=binding_catalog,
        )
    candidates: List[VOPOperatorCandidate] = []
    for idx, program in enumerate(programs[: max(1, int(limit))], start=1):
        program_v4 = semantic_programs[idx - 1] if idx - 1 < len(semantic_programs) else None
        candidates.append(
            VOPOperatorCandidate(
                candidate_id=f"{program.program_id}_c{idx}",
                priority=max(0.1, 1.0 - (idx - 1) * 0.15),
                note=f"heuristic_{family or 'geometry'}_candidate_{idx}",
                program=program,
                program_v4=program_v4,
                dsl_version="v4" if isinstance(program_v4, dict) else "v3",
            )
        )
    return candidates


def _binding_catalog_from_graph(graph: Any) -> Dict[str, Any]:
    metadata = dict(getattr(graph, "metadata", {}) or {})
    hint = dict(metadata.get("binding_catalog_hint", {}) or {})
    return hint


def _semantic_group_target(
    component_ids: Sequence[str],
    *,
    group_id: str = "affected_cluster",
) -> Dict[str, Any]:
    return {
        "object_type": "component_group",
        "object_id": str(group_id or "affected_cluster"),
        "role": "subject",
        "attributes": {
            "component_ids": [
                str(item).strip()
                for item in list(component_ids or [])
                if str(item).strip()
            ]
        },
    }


def _semantic_object_target(
    *,
    binding_catalog: Mapping[str, Any],
    object_type: str,
    object_id: str,
    role: str = "target",
    fallback_attributes: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    attributes = dict(fallback_attributes or {})
    catalog_payload = dict(dict(binding_catalog.get(object_type, {}) or {}).get(object_id, {}) or {})
    attributes.update(catalog_payload)
    return {
        "object_type": str(object_type or ""),
        "object_id": str(object_id or ""),
        "role": str(role or ""),
        "attributes": attributes,
    }


def _first_catalog_object_id(
    binding_catalog: Mapping[str, Any],
    object_type: str,
    default: str,
) -> str:
    payload = dict(binding_catalog.get(object_type, {}) or {})
    for object_id in payload.keys():
        normalized = str(object_id or "").strip()
        if normalized:
            return normalized
    return str(default or "")


def _mission_axis(runtime_constraints: Mapping[str, Any]) -> str:
    axis = str(runtime_constraints.get("mission_keepout_axis", "") or "").strip().lower()
    for token in ("x", "y", "z"):
        if token in axis:
            return token
    return "z"


def _mission_face(runtime_constraints: Mapping[str, Any]) -> str:
    raw = str(runtime_constraints.get("mission_keepout_axis", "") or "").strip().lower()
    axis = _mission_axis(runtime_constraints)
    sign = "-" if any(token in raw for token in ("-", "negative", "minus")) else "+"
    return f"{sign}{axis}"


def _geometry_programs_v4(
    component_ids: Sequence[str],
    *,
    binding_catalog: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=2)
    panel_id = _first_catalog_object_id(binding_catalog, "panel", "mission_panel")
    aperture_id = _first_catalog_object_id(binding_catalog, "aperture", "mission_aperture")
    return [
        {
            "program_id": "vop_geom_place_panel_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Place affected cluster on a valid panel with clearance bias",
            "actions": [
                {
                    "action": "place_on_panel",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="panel",
                            object_id=panel_id,
                        ),
                    ],
                    "hard_rules": ["minimum_clearance", "mount_site_allowed"],
                    "soft_preferences": ["layout_symmetry"],
                    "params": {"axis": "x", "delta_mm": 8.0, "focus_ratio": 0.60},
                }
            ],
        },
        {
            "program_id": "vop_geom_reorient_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Reorient payload cluster toward an allowed mission-facing side",
            "actions": [
                {
                    "action": "reorient_to_allowed_face",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="aperture",
                            object_id=aperture_id,
                        ),
                    ],
                    "hard_rules": ["allowed_face"],
                    "soft_preferences": ["serviceability"],
                    "params": {"delta_mm": 4.0, "focus_ratio": 0.62},
                }
            ],
        },
    ]


def _thermal_programs_v4(
    component_ids: Sequence[str],
    *,
    binding_catalog: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=2)
    zone_id = _first_catalog_object_id(binding_catalog, "zone", "radiator_zone_primary")
    panel_id = _first_catalog_object_id(binding_catalog, "panel", "thermal_panel")
    return [
        {
            "program_id": "vop_thermal_spread_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Move hot cluster toward radiator zone and preserve thermal margin",
            "actions": [
                {
                    "action": "move_heat_source_to_radiator_zone",
                    "targets": [
                        _semantic_group_target(selected, group_id="hot_cluster"),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="zone",
                            object_id=zone_id,
                        ),
                    ],
                    "hard_rules": ["thermal_boundary"],
                    "soft_preferences": ["heat_source_to_radiator"],
                    "params": {"delta_mm": 8.0, "focus_ratio": 0.58},
                }
            ],
        },
        {
            "program_id": "vop_thermal_pad_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Add thermal interface support on a cooling-side panel",
            "actions": [
                {
                    "action": "add_thermal_pad",
                    "targets": [
                        _semantic_group_target(selected, group_id="hot_cluster"),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="panel",
                            object_id=panel_id,
                        ),
                    ],
                    "hard_rules": ["thermal_boundary", "catalog_interface"],
                    "soft_preferences": ["heat_source_to_radiator"],
                    "params": {"conductance": 110.0, "focus_ratio": 0.70},
                }
            ],
        },
    ]


def _structural_programs_v4(
    component_ids: Sequence[str],
    *,
    binding_catalog: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=2)
    mount_site_id = _first_catalog_object_id(
        binding_catalog,
        "mount_site",
        "structural_mount_site",
    )
    return [
        {
            "program_id": "vop_struct_support_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Mount critical cluster to an allowed bracket site",
            "actions": [
                {
                    "action": "mount_to_bracket_site",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="mount_site",
                            object_id=mount_site_id,
                        ),
                    ],
                    "hard_rules": ["mount_site_allowed", "catalog_interface"],
                    "soft_preferences": ["serviceability"],
                    "params": {"delta_mm": 5.0, "stiffness_gain": 0.35, "focus_ratio": 0.67},
                }
            ],
        },
        {
            "program_id": "vop_struct_bracket_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Add mounting bracket support before structural compaction",
            "actions": [
                {
                    "action": "add_mount_bracket",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="mount_site",
                            object_id=mount_site_id,
                        ),
                    ],
                    "hard_rules": ["mount_site_allowed", "catalog_interface"],
                    "soft_preferences": ["serviceability"],
                    "params": {"stiffness_gain": 0.30, "focus_ratio": 0.65},
                }
            ],
        },
    ]


def _power_programs_v4(component_ids: Sequence[str]) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=2)
    return [
        {
            "program_id": "vop_power_bus_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Shorten power bus distance inside the critical cluster",
            "actions": [
                {
                    "action": "shorten_power_bus",
                    "targets": [_semantic_group_target(selected)],
                    "hard_rules": ["power_boundary"],
                    "soft_preferences": ["short_power_bus"],
                    "params": {"axes": ["x", "y"], "focus_ratio": 0.66},
                }
            ],
        },
        {
            "program_id": "vop_power_bus_compact_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Use a tighter bus-shortening prior on the same power cluster",
            "actions": [
                {
                    "action": "shorten_power_bus",
                    "targets": [_semantic_group_target(selected)],
                    "hard_rules": ["power_boundary"],
                    "soft_preferences": ["short_power_bus"],
                    "params": {"axes": ["x", "y"], "focus_ratio": 0.68},
                }
            ],
        },
    ]


def _mission_programs_v4(
    component_ids: Sequence[str],
    *,
    binding_catalog: Mapping[str, Any],
    runtime_constraints: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=1)
    aperture_id = _first_catalog_object_id(binding_catalog, "aperture", "mission_aperture")
    zone_id = _first_catalog_object_id(binding_catalog, "zone", "mission_keepout_zone")
    mission_attrs = {
        "axis": _mission_axis(runtime_constraints),
        "face": _mission_face(runtime_constraints),
        "center_mm": float(runtime_constraints.get("mission_keepout_center_mm", 0.0) or 0.0),
        "min_separation_mm": float(
            runtime_constraints.get("mission_min_separation_mm", 18.0) or 18.0
        ),
        "preferred_side": "auto",
    }
    return [
        {
            "program_id": "vop_mission_keepout_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Protect mission keepout using explicit mission zone binding",
            "actions": [
                {
                    "action": "protect_fov_keepout",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="zone",
                            object_id=zone_id,
                            fallback_attributes=mission_attrs,
                        ),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="aperture",
                            object_id=aperture_id,
                            fallback_attributes={
                                "axis": mission_attrs["axis"],
                                "face": mission_attrs["face"],
                            },
                        ),
                    ],
                    "hard_rules": ["fov_keepout"],
                    "soft_preferences": ["payload_on_mission_face"],
                }
            ],
        },
        {
            "program_id": "vop_mission_aperture_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Activate a mission aperture site as a bounded alignment prior",
            "actions": [
                {
                    "action": "activate_aperture_site",
                    "targets": [
                        _semantic_group_target(selected),
                        _semantic_object_target(
                            binding_catalog=binding_catalog,
                            object_type="aperture",
                            object_id=aperture_id,
                            fallback_attributes={
                                "axis": mission_attrs["axis"],
                                "face": mission_attrs["face"],
                            },
                        ),
                    ],
                    "hard_rules": ["shell_aperture_match"],
                    "soft_preferences": ["payload_on_mission_face"],
                    "params": {"focus_ratio": 0.68},
                }
            ],
        },
    ]


def _cg_programs_v4(component_ids: Sequence[str]) -> List[Dict[str, Any]]:
    selected = _selected_components(component_ids, minimum=2)
    return [
        {
            "program_id": "vop_cg_center_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Rebalance the affected cluster around the centroid neighborhood",
            "actions": [
                {
                    "action": "rebalance_cg_by_group_shift",
                    "targets": [_semantic_group_target(selected)],
                    "hard_rules": ["cg_limit"],
                    "soft_preferences": ["adcs_near_cg", "layout_symmetry"],
                    "params": {"axes": ["x", "y"], "strength": 0.78, "focus_ratio": 0.60},
                }
            ],
        },
        {
            "program_id": "vop_cg_balance_v4",
            "version": DSL_V4_VERSION,
            "rationale": "Apply a lighter CG rebalance pass to preserve symmetry",
            "actions": [
                {
                    "action": "rebalance_cg_by_group_shift",
                    "targets": [_semantic_group_target(selected)],
                    "hard_rules": ["cg_limit"],
                    "soft_preferences": ["layout_symmetry"],
                    "params": {"axes": ["x", "y"], "strength": 0.55, "focus_ratio": 0.68},
                }
            ],
        },
    ]


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
