"""
Materialize MaaS event JSONL into analysis-friendly CSV tables.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.path_policy import serialize_run_path


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


def _write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = list(rows)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return 0

    headers: List[str] = []
    seen = set()
    for row in materialized:
        for key in row.keys():
            if key in seen:
                continue
            seen.add(key)
            headers.append(str(key))

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in materialized:
            writer.writerow(row)
    return int(len(materialized))


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
    layouts = _read_jsonl(events_dir / "layout_events.jsonl")
    layout_deltas = _build_layout_delta_rows(base, layouts)

    counts = {
        "attempts": _write_csv(tables_dir / "attempts.csv", _flatten_attempt_rows(attempts)),
        "generations": _write_csv(tables_dir / "generations.csv", generations),
        "policies": _write_csv(tables_dir / "policy_tuning.csv", policies),
        "physics": _write_csv(tables_dir / "physics_budget.csv", physics),
        "candidates": _write_csv(tables_dir / "candidates.csv", candidates),
        "phases": _write_csv(tables_dir / "phases.csv", phases),
        "layouts": _write_csv(tables_dir / "layout_timeline.csv", layouts),
        "layout_deltas": _write_csv(tables_dir / "layout_deltas.csv", layout_deltas),
    }
    counts["tables_dir"] = serialize_run_path(base, tables_dir)
    return counts
