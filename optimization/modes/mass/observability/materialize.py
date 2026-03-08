"""
Materialize MaaS event JSONL into analysis-friendly CSV tables.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.path_policy import serialize_run_path

_COMMON_OBSERVABILITY_JOIN_KEYS = [
    "run_id",
    "timestamp",
    "iteration",
    "attempt",
    "vop_round_key",
    "round_index",
    "policy_id",
    "previous_policy_id",
    "run_mode",
    "producer_mode",
    "execution_mode",
    "lifecycle_state",
]

_TABLE_HEADER_PRIORITY: Dict[str, List[str]] = {
    "policy_tuning": _COMMON_OBSERVABILITY_JOIN_KEYS
    + [
        "mode",
        "stage",
        "applied",
        "selected_operator_program_id",
        "replan_reason",
        "feedback_aware_fidelity_reason",
        "decision_rationale",
        "change_summary",
        "expected_effects",
        "confidence",
    ],
    "phases": _COMMON_OBSERVABILITY_JOIN_KEYS
    + [
        "phase",
        "phase_family",
        "phase_mode",
        "stage",
        "status",
        "replan_reason",
        "feedback_aware_fidelity_reason",
    ],
    "vop_rounds": _COMMON_OBSERVABILITY_JOIN_KEYS
    + [
        "stage",
        "policy_id",
        "trigger_reason",
        "feedback_aware_fidelity_plan",
        "feedback_aware_fidelity_reason",
        "candidate_policy_id",
        "final_policy_id",
        "selected_operator_program_id",
        "operator_actions",
        "search_space_override",
        "decision_rationale",
        "change_summary",
        "runtime_overrides",
        "fidelity_plan",
        "expected_effects",
        "observed_effects",
        "effectiveness_summary",
        "confidence",
        "policy_applied",
        "mass_rerun_executed",
        "skipped_reason",
    ],
    "release_audit": [
        "run_id",
        "run_mode",
        "execution_mode",
        "lifecycle_state",
        "status",
        "final_iteration",
        "optimization_mode",
        "simulation_backend",
        "thermal_evaluator_mode",
        "diagnosis_status",
        "diagnosis_reason",
        "final_audit_status",
        "first_feasible_eval",
        "comsol_calls_to_first_feasible",
        "source_gate_passed",
        "operator_family_gate_passed",
        "operator_realization_gate_passed",
        "final_mph_path",
    ],
}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _ordered_headers(rows: List[Dict[str, Any]], preferred_headers: Optional[List[str]] = None) -> List[str]:
    preferred = [str(item) for item in list(preferred_headers or []) if str(item).strip()]
    discovered: List[str] = []
    seen = set()
    for key in preferred:
        if key in seen:
            continue
        seen.add(key)
        discovered.append(key)
    for row in rows:
        for key in row.keys():
            key_str = str(key)
            if key_str in seen:
                continue
            seen.add(key_str)
            discovered.append(key_str)
    return discovered


def _write_csv(
    path: Path,
    rows: Iterable[Dict[str, Any]],
    *,
    preferred_headers: Optional[List[str]] = None,
) -> int:
    materialized = list(rows)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return 0

    headers = _ordered_headers(materialized, preferred_headers=preferred_headers)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in materialized:
            writer.writerow(row)
    return int(len(materialized))


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    return str(value)


def _normalize_vop_round_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    json_columns = {
        "feedback_aware_fidelity_plan",
        "operator_actions",
        "change_summary",
        "runtime_overrides",
        "fidelity_plan",
        "expected_effects",
        "observed_effects",
        "effectiveness_summary",
        "vop_decision_summary",
        "vop_delegated_effect_summary",
    }
    for row in rows:
        payload = dict(row or {})
        if not payload:
            continue
        for column in json_columns:
            if column not in payload:
                continue
            payload[column] = json.dumps(
                _json_ready(payload.get(column)),
                ensure_ascii=False,
                sort_keys=True,
            )
        normalized.append(payload)
    return normalized


def persist_vop_round_events(run_dir: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    base = Path(run_dir)
    events_dir = base / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / "vop_round_events.jsonl"

    materialized = [dict(_json_ready(row) or {}) for row in list(rows or []) if dict(row or {})]
    with path.open("w", encoding="utf-8") as f:
        for row in materialized:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    return {
        "vop_round_events": int(len(materialized)),
        "vop_round_events_path": serialize_run_path(base, path),
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(parsed)


def _resolve_snapshot_path(run_dir: Path, raw_snapshot_path: Any) -> Optional[Path]:
    raw = str(raw_snapshot_path or "").strip()
    if not raw:
        return None
    snapshot_path = Path(raw)
    if snapshot_path.exists():
        return snapshot_path
    if not snapshot_path.is_absolute():
        candidate = run_dir / raw
        if candidate.exists():
            return candidate
    return None


def _component_centers(state: Dict[str, Any]) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for comp in list((state or {}).get("components", []) or []):
        if not isinstance(comp, dict):
            continue
        comp_id = str(comp.get("id", "") or "").strip()
        if not comp_id:
            continue
        pos = dict(comp.get("position", {}) or {})
        out[comp_id] = [
            _safe_float(pos.get("x", 0.0)),
            _safe_float(pos.get("y", 0.0)),
            _safe_float(pos.get("z", 0.0)),
        ]
    return out


def _build_layout_delta_rows(run_dir: Path, layout_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not layout_rows:
        return []

    sorted_rows = sorted(
        layout_rows,
        key=lambda item: (
            int(item.get("sequence", 0) or 0),
            int(item.get("iteration", 0) or 0),
            int(item.get("attempt", 0) or 0),
        ),
    )

    output: List[Dict[str, Any]] = []
    initial_map: Optional[Dict[str, List[float]]] = None
    prev_map: Optional[Dict[str, List[float]]] = None
    prev_sequence = 0

    for row in sorted_rows:
        snapshot_path = _resolve_snapshot_path(run_dir, row.get("snapshot_path"))
        if snapshot_path is None:
            continue
        try:
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(snapshot_payload, dict):
            continue

        state = dict(snapshot_payload.get("design_state", {}) or {})
        curr_map = _component_centers(state)
        if initial_map is None:
            initial_map = dict(curr_map)

        event_moved = set(str(item) for item in list(row.get("moved_components", []) or []))
        snapshot_delta = dict(snapshot_payload.get("delta", {}) or {})
        event_moved.update(
            str(item) for item in list(snapshot_delta.get("moved_components", []) or [])
        )

        sequence = int(row.get("sequence", 0) or 0)
        transition_id = f"{int(prev_sequence):04d}->{int(sequence):04d}"

        for comp_id, curr_pos in curr_map.items():
            prev_pos = curr_pos
            if prev_map and comp_id in prev_map:
                prev_pos = prev_map[comp_id]
            init_pos = curr_pos
            if initial_map and comp_id in initial_map:
                init_pos = initial_map[comp_id]

            dx = float(curr_pos[0] - prev_pos[0])
            dy = float(curr_pos[1] - prev_pos[1])
            dz = float(curr_pos[2] - prev_pos[2])
            dist = float((dx * dx + dy * dy + dz * dz) ** 0.5)

            dx_initial = float(curr_pos[0] - init_pos[0])
            dy_initial = float(curr_pos[1] - init_pos[1])
            dz_initial = float(curr_pos[2] - init_pos[2])
            dist_initial = float(
                (dx_initial * dx_initial + dy_initial * dy_initial + dz_initial * dz_initial) ** 0.5
            )

            output.append(
                {
                    "run_id": row.get("run_id", ""),
                    "timestamp": row.get("timestamp", ""),
                    "iteration": int(row.get("iteration", 0) or 0),
                    "attempt": int(row.get("attempt", 0) or 0),
                    "sequence": int(sequence),
                    "stage": str(row.get("stage", "") or ""),
                    "transition_id": transition_id,
                    "component_id": comp_id,
                    "dx": float(dx),
                    "dy": float(dy),
                    "dz": float(dz),
                    "dist": float(dist),
                    "dx_from_initial": float(dx_initial),
                    "dy_from_initial": float(dy_initial),
                    "dz_from_initial": float(dz_initial),
                    "dist_from_initial": float(dist_initial),
                    "moved_component_flag": bool(comp_id in event_moved),
                    "thermal_source": str(row.get("thermal_source", "") or ""),
                    "diagnosis_status": str(row.get("diagnosis_status", "") or ""),
                    "branch_action": str(row.get("branch_action", "") or ""),
                }
            )

        prev_map = dict(curr_map)
        prev_sequence = int(sequence)

    return output


def _build_release_audit_rows(run_dir: Path) -> List[Dict[str, Any]]:
    summary = _read_json(run_dir / "summary.json")
    if not summary:
        return []

    return [
        {
            "run_id": str(summary.get("run_id", "") or ""),
            "run_mode": str(summary.get("run_mode", "") or ""),
            "execution_mode": str(summary.get("execution_mode", "") or ""),
            "lifecycle_state": str(summary.get("lifecycle_state", "") or ""),
            "status": str(summary.get("status", "") or ""),
            "final_iteration": summary.get("final_iteration"),
            "optimization_mode": str(summary.get("optimization_mode", "") or ""),
            "simulation_backend": str(summary.get("simulation_backend", "") or ""),
            "thermal_evaluator_mode": str(
                summary.get("thermal_evaluator_mode", "") or ""
            ),
            "diagnosis_status": str(summary.get("diagnosis_status", "") or ""),
            "diagnosis_reason": str(summary.get("diagnosis_reason", "") or ""),
            "final_audit_status": str(summary.get("final_audit_status", "") or ""),
            "first_feasible_eval": summary.get("first_feasible_eval"),
            "comsol_calls_to_first_feasible": summary.get(
                "comsol_calls_to_first_feasible"
            ),
            "source_gate_passed": summary.get("source_gate_passed"),
            "operator_family_gate_passed": summary.get("operator_family_gate_passed"),
            "operator_realization_gate_passed": summary.get(
                "operator_realization_gate_passed"
            ),
            "final_mph_path": str(summary.get("final_mph_path", "") or ""),
        }
    ]


def _flatten_attempt_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        payload = dict(row or {})
        violations = dict(payload.pop("constraint_violation_breakdown", {}) or {})
        metrics = dict(payload.pop("best_candidate_metrics", {}) or {})
        seed_population = dict(payload.pop("seed_population_report", {}) or {})
        payload["violation_keys"] = ",".join(sorted(str(k) for k in violations.keys()))
        payload["violation_total"] = float(sum(float(v) for v in violations.values())) if violations else 0.0
        payload["metric_cg_offset"] = metrics.get("cg_offset")
        payload["metric_max_temp"] = metrics.get("max_temp")
        payload["metric_min_clearance"] = metrics.get("min_clearance")
        payload["metric_safety_factor"] = metrics.get("safety_factor")
        payload["metric_first_modal_freq"] = metrics.get("first_modal_freq")
        payload["metric_voltage_drop"] = metrics.get("voltage_drop")
        payload["metric_power_margin"] = metrics.get("power_margin")
        payload["metric_peak_power"] = metrics.get("peak_power")
        payload["seed_population_total_count"] = seed_population.get("total_seed_count_post_dedup")
        payload["layout_seed_generated_count"] = seed_population.get("layout_seed_generated_count")
        payload["layout_seed_unique_count"] = seed_population.get("layout_seed_unique_count")
        payload["layout_seed_requested_count"] = seed_population.get("layout_seed_requested_count")
        payload["seed_population_source_keys"] = ",".join(
            sorted(
                str(key)
                for key in dict(seed_population.get("source_counts_post_dedup", {}) or {}).keys()
                if str(key).strip()
            )
        )
        payload["layout_seed_state_ids"] = ",".join(
            str(item)
            for item in list(seed_population.get("layout_seed_state_ids", []) or [])
            if str(item).strip()
        )
        out.append(payload)
    return out


def materialize_observability_tables(run_dir: str) -> Dict[str, Any]:
    """
    Convert `events/*.jsonl` into `tables/*.csv`.
    """
    base = Path(run_dir)
    events_dir = base / "events"
    tables_dir = base / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    attempts = _read_jsonl(events_dir / "attempt_events.jsonl")
    generations = _read_jsonl(events_dir / "generation_events.jsonl")
    policies = _read_jsonl(events_dir / "policy_events.jsonl")
    physics = _read_jsonl(events_dir / "physics_events.jsonl")
    candidates = _read_jsonl(events_dir / "candidate_events.jsonl")
    phases = _read_jsonl(events_dir / "phase_events.jsonl")
    vop_rounds = _read_jsonl(events_dir / "vop_round_events.jsonl")
    layouts = _read_jsonl(events_dir / "layout_events.jsonl")
    layout_deltas = _build_layout_delta_rows(base, layouts)
    release_audit = _build_release_audit_rows(base)

    counts = {
        "attempts": _write_csv(tables_dir / "attempts.csv", _flatten_attempt_rows(attempts)),
        "generations": _write_csv(tables_dir / "generations.csv", generations),
        "policies": _write_csv(
            tables_dir / "policy_tuning.csv",
            policies,
            preferred_headers=_TABLE_HEADER_PRIORITY.get("policy_tuning"),
        ),
        "physics": _write_csv(tables_dir / "physics_budget.csv", physics),
        "candidates": _write_csv(tables_dir / "candidates.csv", candidates),
        "phases": _write_csv(
            tables_dir / "phases.csv",
            phases,
            preferred_headers=_TABLE_HEADER_PRIORITY.get("phases"),
        ),
        "vop_rounds": _write_csv(
            tables_dir / "vop_rounds.csv",
            _normalize_vop_round_rows(vop_rounds),
            preferred_headers=_TABLE_HEADER_PRIORITY.get("vop_rounds"),
        ),
        "layouts": _write_csv(tables_dir / "layout_timeline.csv", layouts),
        "layout_deltas": _write_csv(tables_dir / "layout_deltas.csv", layout_deltas),
        "release_audit": _write_csv(
            tables_dir / "release_audit.csv",
            release_audit,
            preferred_headers=_TABLE_HEADER_PRIORITY.get("release_audit"),
        ),
    }
    counts["tables_dir"] = serialize_run_path(base, tables_dir)
    counts["vop_rounds_path"] = serialize_run_path(base, tables_dir / "vop_rounds.csv")
    counts["release_audit_path"] = serialize_run_path(
        base, tables_dir / "release_audit.csv"
    )
    return counts
