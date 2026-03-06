"""
Rule-based meta policy for MaaS runtime knob adaptation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clip(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _set_knob(
    next_knobs: Dict[str, Any],
    actions: List[Dict[str, Any]],
    *,
    key: str,
    value: Any,
    reason: str,
    action_type: str,
) -> None:
    old = next_knobs.get(key)
    if old == value:
        return
    next_knobs[key] = value
    actions.append(
        {
            "type": action_type,
            "knob": key,
            "from": old,
            "to": value,
            "reason": reason,
        }
    )


def propose_meta_policy_actions(
    *,
    trace_features: Dict[str, Any],
    current_knobs: Dict[str, Any],
    online_comsol_enabled: bool,
) -> Dict[str, Any]:
    """
    Generate runtime knob updates from compact trace features.

    Current controllable knobs:
    - maas_relax_ratio
    - mcts_action_prior_weight
    - mcts_cv_penalty_weight
    - online_comsol_eval_budget
    - online_comsol_schedule_mode
    - online_comsol_schedule_top_fraction
    - online_comsol_schedule_explore_prob
    - online_comsol_schedule_uncertainty_weight
    """
    next_knobs = dict(current_knobs or {})
    actions: List[Dict[str, Any]] = []

    feasible_rate = trace_features.get("feasible_rate")
    feasible_rate_recent = trace_features.get("feasible_rate_recent")
    best_cv_min = trace_features.get("best_cv_min")
    best_cv_slope = trace_features.get("best_cv_slope")
    alerts = set(str(item) for item in (trace_features.get("alerts") or []))
    pymoo_algorithm = str(trace_features.get("pymoo_algorithm", "")).strip().lower()
    if pymoo_algorithm not in {"nsga2", "nsga3", "moead"}:
        pymoo_algorithm = "unknown"
    search_space_mode = str(trace_features.get("search_space_mode", "")).strip().lower()
    if search_space_mode not in {"coordinate", "operator_program", "hybrid"}:
        search_space_mode = "unknown"

    thermal = dict(trace_features.get("runtime_thermal") or {})
    geometry_infeasible_ratio = thermal.get("geometry_infeasible_ratio")
    budget_exhausted_ratio = thermal.get("budget_exhausted_ratio")
    comsol_calls_per_feasible = thermal.get("comsol_calls_per_feasible_attempt")
    online_comsol_exec_rate = thermal.get("online_comsol_exec_rate")
    scheduler_skipped_ratio = thermal.get("scheduler_skipped_ratio")

    audit = dict(trace_features.get("physics_audit") or {})
    physics_pass_rate_topk = audit.get("physics_pass_rate_topk")
    violation_focus = dict(trace_features.get("violation_focus") or {})
    cg_dominant_ratio = violation_focus.get("cg_dominant_ratio")

    relax_ratio = _to_float(next_knobs.get("maas_relax_ratio"), 0.08)
    action_prior_weight = _to_float(next_knobs.get("mcts_action_prior_weight"), 0.02)
    cv_penalty_weight = _to_float(next_knobs.get("mcts_cv_penalty_weight"), 0.2)
    eval_budget = _to_int(next_knobs.get("online_comsol_eval_budget"), 0)
    schedule_mode = str(next_knobs.get("online_comsol_schedule_mode", "budget_only")).strip().lower()
    if schedule_mode not in {"budget_only", "ucb_topk"}:
        schedule_mode = "budget_only"
    schedule_top_fraction = _clip(
        _to_float(next_knobs.get("online_comsol_schedule_top_fraction"), 0.20),
        0.01,
        1.0,
    )
    schedule_explore_prob = _clip(
        _to_float(next_knobs.get("online_comsol_schedule_explore_prob"), 0.05),
        0.0,
        1.0,
    )
    schedule_uncertainty_weight = _clip(
        _to_float(next_knobs.get("online_comsol_schedule_uncertainty_weight"), 0.35),
        0.0,
        5.0,
    )

    # R1: Feasibility too low -> increase relaxation and emphasize CV penalties.
    if feasible_rate is not None and _to_float(feasible_rate) < 0.35:
        _set_knob(
            next_knobs,
            actions,
            key="maas_relax_ratio",
            value=_clip(relax_ratio + 0.03, 0.02, 0.25),
            reason="feasible_rate_low",
            action_type="relax_constraint_bounded",
        )
        _set_knob(
            next_knobs,
            actions,
            key="mcts_cv_penalty_weight",
            value=_clip(cv_penalty_weight + 0.08, 0.05, 1.0),
            reason="feasible_rate_low",
            action_type="retune_mcts_cv_penalty",
        )

    # R2: Geometry infeasible candidates dominate -> de-prioritize history bias.
    if geometry_infeasible_ratio is not None and _to_float(geometry_infeasible_ratio) >= 0.50:
        _set_knob(
            next_knobs,
            actions,
            key="mcts_action_prior_weight",
            value=_clip(action_prior_weight * 0.8, 0.005, 0.2),
            reason="geometry_infeasible_ratio_high",
            action_type="retune_mcts_action_prior",
        )

    # R3: CV not improving while recent feasibility still low -> stronger CV pressure.
    if (
        best_cv_slope is not None and
        _to_float(best_cv_slope) >= -1e-9 and
        feasible_rate_recent is not None and
        _to_float(feasible_rate_recent) <= 0.5
    ):
        _set_knob(
            next_knobs,
            actions,
            key="mcts_cv_penalty_weight",
            value=_clip(_to_float(next_knobs.get("mcts_cv_penalty_weight"), cv_penalty_weight) + 0.05, 0.05, 1.0),
            reason="best_cv_not_improving_and_recent_feasible_low",
            action_type="retune_mcts_cv_penalty",
        )

    # R4: CG-dominant failures -> prioritize CV pressure over history bias.
    if (
        "cg_dominant_high" in alerts or
        (cg_dominant_ratio is not None and _to_float(cg_dominant_ratio) >= 0.50)
    ):
        _set_knob(
            next_knobs,
            actions,
            key="mcts_action_prior_weight",
            value=_clip(
                _to_float(next_knobs.get("mcts_action_prior_weight"), action_prior_weight) * 0.75,
                0.005,
                0.2,
            ),
            reason="cg_dominant_high",
            action_type="retune_mcts_action_prior",
        )
        _set_knob(
            next_knobs,
            actions,
            key="mcts_cv_penalty_weight",
            value=_clip(
                _to_float(next_knobs.get("mcts_cv_penalty_weight"), cv_penalty_weight) + 0.12,
                0.05,
                1.0,
            ),
            reason="cg_dominant_high",
            action_type="retune_mcts_cv_penalty",
        )

    # R5: Highly feasible and stable -> gradually tighten relaxation.
    if (
        feasible_rate is not None and _to_float(feasible_rate) >= 0.8 and
        best_cv_slope is not None and _to_float(best_cv_slope) < -1e-6
    ):
        _set_knob(
            next_knobs,
            actions,
            key="maas_relax_ratio",
            value=_clip(_to_float(next_knobs.get("maas_relax_ratio"), relax_ratio) - 0.015, 0.02, 0.25),
            reason="feasible_high_and_cv_improving",
            action_type="tighten_constraint_back",
        )

    # R6: online_comsol budget heuristic.
    if online_comsol_enabled and eval_budget > 0:
        if (
            comsol_calls_per_feasible is not None and
            _to_float(comsol_calls_per_feasible) > 12.0
        ):
            scaled = max(8, int(round(eval_budget * 0.8)))
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_eval_budget",
                value=scaled,
                reason="comsol_calls_per_feasible_high",
                action_type="reallocate_comsol_budget",
            )
        elif (
            budget_exhausted_ratio is not None and _to_float(budget_exhausted_ratio) >= 0.50 and
            physics_pass_rate_topk is not None and _to_float(physics_pass_rate_topk) < 0.50
        ):
            scaled = min(256, int(round(eval_budget * 1.25)))
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_eval_budget",
                value=scaled,
                reason="budget_exhausted_and_physics_pass_low",
                action_type="reallocate_comsol_budget",
            )

    # R7: budget mode under stress -> upgrade to selective scheduling.
    if (
        online_comsol_enabled and
        schedule_mode == "budget_only" and
        budget_exhausted_ratio is not None and
        _to_float(budget_exhausted_ratio) >= 0.35 and
        feasible_rate_recent is not None and
        _to_float(feasible_rate_recent) <= 0.6
    ):
        _set_knob(
            next_knobs,
            actions,
            key="online_comsol_schedule_mode",
            value="ucb_topk",
            reason="budget_exhausted_with_recent_feasible_low",
            action_type="retune_comsol_schedule_mode",
        )
        schedule_mode = "ucb_topk"

    # R8-R10: scheduler-specific adaptation.
    if online_comsol_enabled and schedule_mode == "ucb_topk":
        if (
            scheduler_skipped_ratio is not None and
            _to_float(scheduler_skipped_ratio) >= 0.70 and
            feasible_rate_recent is not None and
            _to_float(feasible_rate_recent) <= 0.40
        ):
            next_top_fraction = _clip(schedule_top_fraction + 0.08, 0.05, 0.90)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_top_fraction",
                value=next_top_fraction,
                reason="scheduler_skipped_high_and_recent_feasible_low",
                action_type="retune_comsol_schedule_top_fraction",
            )
            schedule_top_fraction = next_top_fraction

            next_explore_prob = _clip(schedule_explore_prob + 0.04, 0.0, 0.50)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_explore_prob",
                value=next_explore_prob,
                reason="scheduler_skipped_high_and_recent_feasible_low",
                action_type="retune_comsol_schedule_explore",
            )
            schedule_explore_prob = next_explore_prob

        if (
            online_comsol_exec_rate is not None and
            _to_float(online_comsol_exec_rate) >= 0.70 and
            physics_pass_rate_topk is not None and
            _to_float(physics_pass_rate_topk) < 0.50
        ):
            next_top_fraction = _clip(schedule_top_fraction * 0.85, 0.05, 0.90)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_top_fraction",
                value=next_top_fraction,
                reason="online_comsol_exec_rate_high_and_physics_pass_low",
                action_type="retune_comsol_schedule_top_fraction",
            )
            schedule_top_fraction = next_top_fraction

            next_uncertainty_weight = _clip(schedule_uncertainty_weight * 0.90, 0.0, 5.0)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_uncertainty_weight",
                value=next_uncertainty_weight,
                reason="online_comsol_exec_rate_high_and_physics_pass_low",
                action_type="retune_comsol_schedule_uncertainty_weight",
            )
            schedule_uncertainty_weight = next_uncertainty_weight

        if (
            budget_exhausted_ratio is not None and
            _to_float(budget_exhausted_ratio) >= 0.50 and
            physics_pass_rate_topk is not None and
            _to_float(physics_pass_rate_topk) < 0.50
        ):
            next_top_fraction = _clip(schedule_top_fraction - 0.05, 0.05, 0.90)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_top_fraction",
                value=next_top_fraction,
                reason="budget_exhausted_and_physics_pass_low_under_scheduler",
                action_type="retune_comsol_schedule_top_fraction",
            )
            schedule_top_fraction = next_top_fraction

            next_explore_prob = _clip(schedule_explore_prob + 0.03, 0.0, 0.50)
            _set_knob(
                next_knobs,
                actions,
                key="online_comsol_schedule_explore_prob",
                value=next_explore_prob,
                reason="budget_exhausted_and_physics_pass_low_under_scheduler",
                action_type="retune_comsol_schedule_explore",
            )
            schedule_explore_prob = next_explore_prob

    # R11: Algorithm-aware tuning (NSGA-II/NSGA-III/MOEA/D).
    if feasible_rate is not None and _to_float(feasible_rate) < 0.35:
        if pymoo_algorithm == "moead":
            _set_knob(
                next_knobs,
                actions,
                key="maas_relax_ratio",
                value=_clip(_to_float(next_knobs.get("maas_relax_ratio"), relax_ratio) + 0.02, 0.02, 0.25),
                reason="moead_feasible_low",
                action_type="algo_retune_relax_ratio",
            )
            _set_knob(
                next_knobs,
                actions,
                key="mcts_cv_penalty_weight",
                value=_clip(
                    _to_float(next_knobs.get("mcts_cv_penalty_weight"), cv_penalty_weight) + 0.06,
                    0.05,
                    1.0,
                ),
                reason="moead_feasible_low",
                action_type="algo_retune_mcts_cv_penalty",
            )
        elif pymoo_algorithm == "nsga2":
            _set_knob(
                next_knobs,
                actions,
                key="mcts_action_prior_weight",
                value=_clip(
                    _to_float(next_knobs.get("mcts_action_prior_weight"), action_prior_weight) * 0.85,
                    0.005,
                    0.2,
                ),
                reason="nsga2_feasible_low_explore_more",
                action_type="algo_retune_mcts_action_prior",
            )
            if best_cv_min is not None and 0.0 < _to_float(best_cv_min) <= 1.5:
                _set_knob(
                    next_knobs,
                    actions,
                    key="maas_relax_ratio",
                    value=_clip(_to_float(next_knobs.get("maas_relax_ratio"), relax_ratio) + 0.04, 0.02, 0.25),
                    reason="nsga2_near_feasible_boost_relax",
                    action_type="algo_retune_relax_ratio",
                )
        elif pymoo_algorithm == "nsga3":
            if best_cv_min is not None and 0.0 < _to_float(best_cv_min) <= 1.0:
                _set_knob(
                    next_knobs,
                    actions,
                    key="maas_relax_ratio",
                    value=_clip(_to_float(next_knobs.get("maas_relax_ratio"), relax_ratio) + 0.01, 0.02, 0.25),
                    reason="nsga3_near_feasible",
                    action_type="algo_retune_relax_ratio",
                )

    # R12: search-space-aware prior floor for operator-program branches.
    if search_space_mode in {"operator_program", "hybrid"}:
        min_prior_floor = 0.015
        current_prior = _to_float(next_knobs.get("mcts_action_prior_weight"), action_prior_weight)
        if current_prior < min_prior_floor:
            _set_knob(
                next_knobs,
                actions,
                key="mcts_action_prior_weight",
                value=float(min_prior_floor),
                reason=f"{search_space_mode}_prior_floor",
                action_type="search_space_prior_floor",
            )

    return {
        "policy_version": "meta_policy_v3_algo_aware",
        "input_alerts": sorted(alerts),
        "policy_context": {
            "pymoo_algorithm": pymoo_algorithm,
            "search_space_mode": search_space_mode,
        },
        "applied": bool(actions),
        "actions": actions,
        "next_knobs": next_knobs,
    }
