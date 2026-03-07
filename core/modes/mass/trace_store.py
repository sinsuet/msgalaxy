"""
MASS trace row/materialization helpers.
"""

from __future__ import annotations

import csv
from datetime import datetime
from typing import Any, Dict, List, Optional


MASS_TRACE_HEADERS: List[str] = [
    "iteration",
    "attempt",
    "timestamp",
    "branch_action",
    "branch_source",
    "operator_program_id",
    "operator_actions",
    "operator_bias_strategy",
    "intent_id",
    "thermal_evaluator_mode",
    "diagnosis_status",
    "diagnosis_reason",
    "solver_message",
    "solver_cost",
    "score",
    "best_cv",
    "aocc_cv",
    "aocc_objective",
    "has_candidate_state",
    "relaxation_applied_count",
    "physics_audit_selected_reason",
    "mcts_enabled",
    "is_best_attempt",
    "dominant_violation",
    "dominant_violation_value",
    "best_candidate_cg_offset",
    "best_candidate_max_temp",
    "best_candidate_min_clearance",
    "best_candidate_safety_factor",
    "best_candidate_first_modal_freq",
    "best_candidate_voltage_drop",
    "best_candidate_power_margin",
    "best_candidate_peak_power",
    "seed_population_total_count",
    "layout_seed_generated_count",
    "layout_seed_unique_count",
    "seed_population_source_keys",
]


def _safe_float(value: Any, digits: int = 6) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def init_mass_trace_csv(csv_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(MASS_TRACE_HEADERS)


def append_mass_trace_row(csv_path: str, row: List[Any]) -> None:
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def _coerce_nullable_float(value: Any) -> Optional[float]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def materialize_trace_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data or {})
    diagnosis = dict(payload.get("diagnosis") or {})
    dominant_violation = str(payload.get("dominant_violation", "") or "")
    violation_breakdown = dict(payload.get("constraint_violation_breakdown") or {})
    best_candidate_metrics = dict(payload.get("best_candidate_metrics") or {})
    operator_actions = list(payload.get("operator_actions", []) or [])
    operator_bias = dict(payload.get("operator_bias", {}) or {})
    seed_population_report = dict(payload.get("seed_population_report", {}) or {})
    is_best_attempt = bool(payload.get("is_best_attempt", False))

    row: List[Any] = [
        int(payload.get("iteration", 0)),
        int(payload.get("attempt", 0)),
        payload.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        str(payload.get("branch_action", "")),
        str(payload.get("branch_source", "")),
        str(payload.get("operator_program_id", "")),
        ",".join(str(item) for item in operator_actions),
        str(operator_bias.get("strategy", "")),
        str(payload.get("intent_id", "")),
        str(payload.get("thermal_evaluator_mode", "")),
        str(diagnosis.get("status", payload.get("diagnosis_status", ""))),
        str(diagnosis.get("reason", payload.get("diagnosis_reason", ""))),
        str(payload.get("solver_message", "")),
        _safe_float(payload.get("solver_cost"), digits=6),
        _safe_float(payload.get("score"), digits=6),
        _safe_float(payload.get("best_cv"), digits=6),
        _safe_float(payload.get("aocc_cv"), digits=6),
        _safe_float(payload.get("aocc_objective"), digits=6),
        bool(payload.get("has_candidate_state", False)),
        int(payload.get("relaxation_applied_count", 0)),
        str(payload.get("physics_audit_selected_reason", "")),
        bool(payload.get("mcts_enabled", False)),
        bool(payload.get("is_best_attempt", False)),
        dominant_violation,
        _safe_float(violation_breakdown.get(dominant_violation), digits=6),
        _safe_float(best_candidate_metrics.get("cg_offset"), digits=6),
        _safe_float(best_candidate_metrics.get("max_temp"), digits=6),
        _safe_float(best_candidate_metrics.get("min_clearance"), digits=6),
        _safe_float(best_candidate_metrics.get("safety_factor"), digits=6),
        _safe_float(best_candidate_metrics.get("first_modal_freq"), digits=6),
        _safe_float(best_candidate_metrics.get("voltage_drop"), digits=6),
        _safe_float(best_candidate_metrics.get("power_margin"), digits=6),
        _safe_float(best_candidate_metrics.get("peak_power"), digits=6),
        int(seed_population_report.get("total_seed_count_post_dedup", 0) or 0),
        int(seed_population_report.get("layout_seed_generated_count", 0) or 0),
        int(seed_population_report.get("layout_seed_unique_count", 0) or 0),
        ",".join(
            sorted(
                str(key)
                for key in dict(seed_population_report.get("source_counts_post_dedup", {}) or {}).keys()
                if str(key).strip()
            )
        ),
    ]

    attempt_event_payload: Dict[str, Any] = {
        "iteration": int(payload.get("iteration", 0) or 0),
        "attempt": int(payload.get("attempt", 0) or 0),
        "branch_action": str(payload.get("branch_action", "")),
        "branch_source": str(payload.get("branch_source", "")),
        "search_space_mode": str(payload.get("search_space_mode", "")),
        "pymoo_algorithm": str(payload.get("pymoo_algorithm", "")),
        "thermal_evaluator_mode": str(payload.get("thermal_evaluator_mode", "")),
        "diagnosis_status": str(diagnosis.get("status", payload.get("diagnosis_status", ""))),
        "diagnosis_reason": str(diagnosis.get("reason", payload.get("diagnosis_reason", ""))),
        "solver_message": str(payload.get("solver_message", "")),
        "solver_cost": _coerce_nullable_float(_safe_float(payload.get("solver_cost"), digits=6)),
        "score": _coerce_nullable_float(_safe_float(payload.get("score"), digits=6)),
        "best_cv": _coerce_nullable_float(_safe_float(payload.get("best_cv"), digits=6)),
        "aocc_cv": _coerce_nullable_float(_safe_float(payload.get("aocc_cv"), digits=6)),
        "aocc_objective": _coerce_nullable_float(_safe_float(payload.get("aocc_objective"), digits=6)),
        "dominant_violation": dominant_violation,
        "constraint_violation_breakdown": violation_breakdown,
        "best_candidate_metrics": best_candidate_metrics,
        "seed_population_report": seed_population_report,
        "seed_population_total_count": int(
            seed_population_report.get("total_seed_count_post_dedup", 0) or 0
        ),
        "layout_seed_generated_count": int(
            seed_population_report.get("layout_seed_generated_count", 0) or 0
        ),
        "layout_seed_unique_count": int(
            seed_population_report.get("layout_seed_unique_count", 0) or 0
        ),
        "operator_program_id": str(payload.get("operator_program_id", "")),
        "operator_actions": operator_actions,
        "operator_attribution_inferred": bool(payload.get("operator_attribution_inferred", False)),
        "operator_mutation_detected_without_actions": bool(
            payload.get("operator_mutation_detected_without_actions", False)
        ),
        "operator_inference": dict(payload.get("operator_inference", {}) or {}),
        "operator_bias_strategy": str(operator_bias.get("strategy", "")),
        "mcts_enabled": bool(payload.get("mcts_enabled", False)),
        "has_candidate_state": bool(payload.get("has_candidate_state", False)),
        "is_best_attempt": is_best_attempt,
    }

    candidate_event_payload: Optional[Dict[str, Any]] = None
    if is_best_attempt:
        candidate_event_payload = {
            "iteration": int(payload.get("iteration", 0) or 0),
            "attempt": int(payload.get("attempt", 0) or 0),
            "source": "best_attempt_marker",
            "diagnosis_status": attempt_event_payload.get("diagnosis_status", ""),
            "diagnosis_reason": attempt_event_payload.get("diagnosis_reason", ""),
            "best_cv": attempt_event_payload.get("best_cv", None),
            "dominant_violation": dominant_violation,
            "best_candidate_metrics": best_candidate_metrics,
            "physics_audit_selected_reason": str(payload.get("physics_audit_selected_reason", "")),
            "is_selected": True,
        }

    return {
        "row": row,
        "attempt_event_payload": attempt_event_payload,
        "candidate_event_payload": candidate_event_payload,
        "is_best_attempt": is_best_attempt,
    }
