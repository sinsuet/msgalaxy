"""
Utilities for extracting compact, policy-ready features from MaaS traces.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

FEASIBLE_STATUSES = {"feasible", "feasible_but_stalled"}


def _to_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed


def _safe_ratio(num: float, den: float) -> Optional[float]:
    if den <= 0:
        return None
    return float(num / den)


def _linear_slope(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    x = np.arange(len(values), dtype=float)
    y = np.asarray(values, dtype=float)
    x_centered = x - float(np.mean(x))
    denom = float(np.sum(x_centered * x_centered))
    if denom <= 0.0:
        return 0.0
    numer = float(np.sum(x_centered * (y - float(np.mean(y)))))
    return float(numer / denom)


def extract_maas_trace_features(
    attempts: List[Dict[str, Any]],
    *,
    runtime_thermal_stats: Optional[Dict[str, Any]] = None,
    physics_audit_report: Optional[Dict[str, Any]] = None,
    recent_window: int = 5,
) -> Dict[str, Any]:
    """
    Build compact trace features for policy tuning and diagnostics.
    """
    n_attempts = int(len(attempts))
    window = max(int(recent_window), 1)
    recent_attempts = list(attempts[-window:])

    diagnosis_counts: Dict[str, int] = {}
    dominant_violation_counts: Dict[str, int] = {}
    cg_dominant_count = 0
    feasible_count = 0
    feasible_recent_count = 0
    first_feasible_eval: Optional[int] = None
    comsol_calls_to_first_feasible: Optional[int] = None
    solver_cost_values: List[float] = []
    score_values: List[float] = []
    best_cv_values: List[float] = []
    relaxation_total = 0

    for idx, payload in enumerate(attempts):
        diagnosis = dict(payload.get("diagnosis") or {})
        status = str(diagnosis.get("status") or payload.get("diagnosis_status") or "")
        diagnosis_counts[status] = int(diagnosis_counts.get(status, 0) + 1)

        if status in FEASIBLE_STATUSES:
            feasible_count += 1
            if idx >= max(0, n_attempts - len(recent_attempts)):
                feasible_recent_count += 1
            if first_feasible_eval is None:
                first_feasible_eval = int(payload.get("attempt", idx + 1) or (idx + 1))
                online_comsol_calls = _to_float(payload.get("online_comsol_calls_so_far"))
                if online_comsol_calls is None:
                    runtime_snapshot = dict(payload.get("runtime_thermal_snapshot") or {})
                    online_comsol_calls = _to_float(runtime_snapshot.get("executed_online_comsol"))
                if online_comsol_calls is not None:
                    comsol_calls_to_first_feasible = int(max(0.0, online_comsol_calls))

        solver_cost = _to_float(payload.get("solver_cost"))
        if solver_cost is not None:
            solver_cost_values.append(float(solver_cost))

        score = _to_float(payload.get("score"))
        if score is not None:
            score_values.append(float(score))

        best_cv = _to_float(payload.get("best_cv"))
        if best_cv is None:
            best_cv = _to_float(diagnosis.get("best_cv"))
        if best_cv is not None:
            best_cv_values.append(float(best_cv))

        try:
            relaxation_total += int(payload.get("relaxation_applied_count", 0) or 0)
        except (TypeError, ValueError):
            pass

        dominant_violation = str(payload.get("dominant_violation") or "").strip()
        if dominant_violation:
            dominant_violation_counts[dominant_violation] = int(
                dominant_violation_counts.get(dominant_violation, 0) + 1
            )
            if "cg" in dominant_violation.lower():
                cg_dominant_count += 1

    feasible_rate = _safe_ratio(float(feasible_count), float(n_attempts))
    feasible_rate_recent = _safe_ratio(float(feasible_recent_count), float(len(recent_attempts)))

    best_cv_min = float(np.min(best_cv_values)) if best_cv_values else None
    best_cv_last = float(best_cv_values[-1]) if best_cv_values else None
    best_cv_slope = _linear_slope(best_cv_values)
    cv_decay_rate = None
    if len(best_cv_values) >= 2:
        first = float(best_cv_values[0])
        last = float(best_cv_values[-1])
        base = max(abs(first), 1e-9)
        cv_decay_rate = float((first - last) / base)

    solver_cost_total = float(np.sum(np.asarray(solver_cost_values, dtype=float))) if solver_cost_values else 0.0
    solver_cost_mean = float(np.mean(np.asarray(solver_cost_values, dtype=float))) if solver_cost_values else None
    score_best = float(np.max(np.asarray(score_values, dtype=float))) if score_values else None
    cg_dominant_ratio = _safe_ratio(float(cg_dominant_count), float(n_attempts))

    thermal_stats = dict(runtime_thermal_stats or {})
    requests_total = int(thermal_stats.get("requests_total", 0) or 0)
    cache_hits = int(thermal_stats.get("cache_hits", 0) or 0)
    executed_online_comsol = int(thermal_stats.get("executed_online_comsol", 0) or 0)
    fallback_geometry_infeasible = int(
        thermal_stats.get("fallback_proxy_geometry_infeasible", 0) or 0
    )
    fallback_budget_exhausted = int(
        thermal_stats.get("fallback_proxy_budget_exhausted", 0) or 0
    )
    fallback_scheduler_skipped = int(
        thermal_stats.get("fallback_proxy_scheduler_skipped", 0) or 0
    )
    schedule_mode = str(thermal_stats.get("schedule_mode", "") or "")
    scheduler_candidates_seen = int(
        thermal_stats.get("scheduler_candidates_seen", 0) or 0
    )
    scheduler_selected_warmup = int(
        thermal_stats.get("scheduler_selected_warmup", 0) or 0
    )
    scheduler_selected_rank = int(
        thermal_stats.get("scheduler_selected_rank", 0) or 0
    )
    scheduler_selected_explore = int(
        thermal_stats.get("scheduler_selected_explore", 0) or 0
    )

    cache_hit_rate = _safe_ratio(float(cache_hits), float(requests_total))
    online_comsol_exec_rate = _safe_ratio(float(executed_online_comsol), float(requests_total))
    geometry_infeasible_ratio = _safe_ratio(float(fallback_geometry_infeasible), float(requests_total))
    budget_exhausted_ratio = _safe_ratio(float(fallback_budget_exhausted), float(requests_total))
    scheduler_skipped_ratio = _safe_ratio(float(fallback_scheduler_skipped), float(requests_total))
    comsol_calls_per_feasible_attempt = _safe_ratio(float(executed_online_comsol), float(feasible_count))

    audit = dict(physics_audit_report or {})
    audit_records = list(audit.get("records") or [])
    audit_record_count = int(len(audit_records))
    feasible_candidate_count = int(audit.get("feasible_candidate_count", 0) or 0)
    physics_pass_rate_topk = _safe_ratio(float(feasible_candidate_count), float(audit_record_count))
    comsol_calls_per_audit_feasible = _safe_ratio(float(executed_online_comsol), float(feasible_candidate_count))

    alerts: List[str] = []
    if geometry_infeasible_ratio is not None and geometry_infeasible_ratio >= 0.50:
        alerts.append("geometry_infeasible_ratio_high")
    if budget_exhausted_ratio is not None and budget_exhausted_ratio >= 0.50:
        alerts.append("online_comsol_budget_exhausted_ratio_high")
    if feasible_rate is not None and feasible_rate <= 0.34:
        alerts.append("feasible_rate_low")
    if best_cv_slope is not None and best_cv_slope >= -1e-9:
        alerts.append("best_cv_not_improving")
    if (
        cg_dominant_ratio is not None and
        cg_dominant_ratio >= 0.50 and
        best_cv_min is not None and
        float(best_cv_min) > 0.0
    ):
        alerts.append("cg_dominant_high")

    return {
        "feature_version": "trace_features_v1",
        "attempt_count": n_attempts,
        "recent_window": int(window),
        "diagnosis_counts": diagnosis_counts,
        "feasible_attempt_count": int(feasible_count),
        "feasible_rate": feasible_rate,
        "feasible_rate_recent": feasible_rate_recent,
        "best_cv_min": best_cv_min,
        "best_cv_last": best_cv_last,
        "best_cv_slope": best_cv_slope,
        "cv_decay_rate": cv_decay_rate,
        "first_feasible_eval": first_feasible_eval,
        "comsol_calls_to_first_feasible": comsol_calls_to_first_feasible,
        "solver_cost_total": solver_cost_total,
        "solver_cost_mean": solver_cost_mean,
        "score_best": score_best,
        "relaxation_applied_total": int(relaxation_total),
        "violation_focus": {
            "dominant_violation_counts": dominant_violation_counts,
            "cg_dominant_attempt_count": int(cg_dominant_count),
            "cg_dominant_ratio": cg_dominant_ratio,
        },
        "runtime_thermal": {
            "requests_total": int(requests_total),
            "cache_hits": int(cache_hits),
            "executed_online_comsol": int(executed_online_comsol),
            "fallback_proxy_geometry_infeasible": int(fallback_geometry_infeasible),
            "fallback_proxy_budget_exhausted": int(fallback_budget_exhausted),
            "fallback_proxy_scheduler_skipped": int(fallback_scheduler_skipped),
            "cache_hit_rate": cache_hit_rate,
            "online_comsol_exec_rate": online_comsol_exec_rate,
            "geometry_infeasible_ratio": geometry_infeasible_ratio,
            "budget_exhausted_ratio": budget_exhausted_ratio,
            "scheduler_skipped_ratio": scheduler_skipped_ratio,
            "comsol_calls_per_feasible_attempt": comsol_calls_per_feasible_attempt,
            "comsol_calls_per_audit_feasible": comsol_calls_per_audit_feasible,
            "schedule_mode": schedule_mode,
            "scheduler_candidates_seen": int(scheduler_candidates_seen),
            "scheduler_selected_warmup": int(scheduler_selected_warmup),
            "scheduler_selected_rank": int(scheduler_selected_rank),
            "scheduler_selected_explore": int(scheduler_selected_explore),
        },
        "physics_audit": {
            "selected_reason": str(audit.get("selected_reason", "")),
            "record_count": int(audit_record_count),
            "feasible_candidate_count": int(feasible_candidate_count),
            "physics_pass_rate_topk": physics_pass_rate_topk,
        },
        "alerts": alerts,
    }
