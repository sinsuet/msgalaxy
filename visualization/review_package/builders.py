"""
Shared review-package builders for Blender sidecar outputs.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from core.artifact_index import load_artifact_index
from core.path_policy import serialize_repo_path, serialize_run_path
from core.visualization import (
    _compute_component_displacements,
    _load_layout_snapshot_records,
    _operator_action_family,
    _parse_operator_actions,
    _select_best_candidate_record,
)


DEFAULT_KEY_STATES: tuple[str, ...] = ("initial", "best", "final")
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_snapshot_files(run_dir: Path) -> Iterable[Path]:
    index = load_artifact_index(str(run_dir))
    scoped_candidates = [
        dict(dict(index.get("scopes", {}) or {}).get("delegated_mass", {}) or {}).get("snapshots_dir", ""),
        dict(dict(index.get("scopes", {}) or {}).get("mass", {}) or {}).get("snapshots_dir", ""),
        dict(dict(index.get("scopes", {}) or {}).get("agent_loop", {}) or {}).get("snapshots_dir", ""),
    ]
    for raw_dir in scoped_candidates:
        raw = str(raw_dir or "").strip()
        if not raw:
            continue
        snapshots_dir = run_dir / raw
        if snapshots_dir.is_dir():
            return sorted(snapshots_dir.glob("*.json"))
    snapshots_dir = run_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    return sorted(snapshots_dir.glob("*.json"))


def snapshot_sort_key(path: Path) -> Tuple[int, int, str]:
    name = path.stem
    parts = name.split("_")
    sequence = 0
    iteration = 0
    if len(parts) >= 2 and parts[0] == "seq":
        try:
            sequence = int(parts[1])
        except Exception:
            sequence = 0
    if "iter" in parts:
        try:
            iteration = int(parts[parts.index("iter") + 1])
        except Exception:
            iteration = 0
    return sequence, iteration, name


def _parse_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, list, dict)):
        return value

    text = str(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"nan", "none", "null"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if text.startswith(("{", "[", "(")) and text.endswith(("}", "]", ")")):
        try:
            return json.loads(text)
        except Exception:
            try:
                return ast.literal_eval(text)
            except Exception:
                pass
    if _NUMBER_RE.match(text):
        try:
            if "." not in text and "e" not in lowered:
                return int(text)
            return float(text)
        except Exception:
            return text
    return text


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append({str(key): _parse_scalar(value) for key, value in dict(row or {}).items()})
    except Exception:
        return []
    return rows


def read_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
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
    except Exception:
        return []
    return rows


def planned_review_package_paths(output_root: Path) -> Dict[str, Path]:
    root = output_root.resolve()
    return {
        "bundle_path": root / "render_bundle.json",
        "review_payload_path": root / "review_payload.json",
        "manifest_path": root / "render_manifest.json",
        "scene_script_path": root / "blender_scene_builder.py",
        "brief_path": root / "render_brief.md",
        "scene_audit_path": root / "scene_audit.json",
        "scene_readonly_checklist_path": root / "scene_readonly_mcp_checklist.md",
        "review_dashboard_path": Path(),
        "output_image_path": root / "final_satellite_render.png",
        "output_blend_path": root / "final_satellite_scene.blend",
    }


def _serialize_record_snapshot_path(run_dir: Path, record: Dict[str, Any]) -> str:
    raw = str(record.get("snapshot_path", "") or "")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (run_dir / raw).resolve()
        return serialize_run_path(run_dir, path)

    event = dict(record.get("event", {}) or {})
    raw_event_path = str(event.get("snapshot_path", "") or "")
    if raw_event_path:
        return serialize_run_path(run_dir, run_dir / raw_event_path)
    return ""


def _normalize_record(run_dir: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    event = dict(record.get("event", {}) or {})
    snapshot = dict(record.get("snapshot", {}) or {})
    absolute_snapshot_path = str(record.get("snapshot_path", "") or "")
    if absolute_snapshot_path:
        snapshot_path = Path(absolute_snapshot_path)
    else:
        raw_event_path = str(event.get("snapshot_path", "") or "")
        snapshot_path = (run_dir / raw_event_path).resolve() if raw_event_path else Path()

    persisted_snapshot_path = (
        serialize_run_path(run_dir, snapshot_path) if str(snapshot_path) else _serialize_record_snapshot_path(run_dir, record)
    )
    event["snapshot_path"] = persisted_snapshot_path
    return {
        "event": event,
        "snapshot": snapshot,
        "snapshot_path": str(snapshot_path) if str(snapshot_path) else "",
        "persisted_snapshot_path": persisted_snapshot_path,
    }


def _build_fallback_snapshot_records(run_dir: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in sorted(iter_snapshot_files(run_dir), key=snapshot_sort_key):
        payload = load_json(path)
        sequence, iteration, _ = snapshot_sort_key(path)
        record = {
            "event": {
                "sequence": int(payload.get("sequence", sequence) or sequence),
                "iteration": int(payload.get("iteration", iteration) or iteration),
                "attempt": int(payload.get("attempt", 0) or 0),
                "stage": str(payload.get("stage", "") or ""),
                "snapshot_path": serialize_run_path(run_dir, path),
                "thermal_source": str(payload.get("thermal_source", "") or ""),
                "diagnosis_status": str(payload.get("diagnosis_status", "") or ""),
                "diagnosis_reason": str(payload.get("diagnosis_reason", "") or ""),
                "operator_actions": list(payload.get("operator_actions", []) or []),
                "moved_components": list(payload.get("moved_components", []) or []),
                "added_heatsinks": list(payload.get("added_heatsinks", []) or []),
                "added_brackets": list(payload.get("added_brackets", []) or []),
                "changed_contacts": list(payload.get("changed_contacts", []) or []),
                "changed_coatings": list(payload.get("changed_coatings", []) or []),
                "metrics": dict(payload.get("metrics", {}) or {}),
                "metadata": dict(payload.get("metadata", {}) or {}),
            },
            "snapshot": payload,
            "snapshot_path": str(path.resolve()),
            "persisted_snapshot_path": serialize_run_path(run_dir, path),
        }
        records.append(record)
    return records


def load_layout_snapshot_records(run_dir: Path) -> List[Dict[str, Any]]:
    records = [_normalize_record(run_dir, record) for record in list(_load_layout_snapshot_records(str(run_dir)) or [])]
    if not records:
        records = _build_fallback_snapshot_records(run_dir)
    records.sort(
        key=lambda item: (
            int(dict(item.get("event", {}) or {}).get("sequence", 0) or 0),
            int(dict(item.get("event", {}) or {}).get("iteration", 0) or 0),
            int(dict(item.get("event", {}) or {}).get("attempt", 0) or 0),
            str(Path(str(item.get("snapshot_path", "") or "")).name),
        )
    )
    return records


def build_state_selection(run_dir: Path, key_states: Sequence[str] | None = None) -> Dict[str, Any]:
    requested = list(dict.fromkeys([str(item).strip().lower() for item in list(key_states or DEFAULT_KEY_STATES)]))
    unsupported = [item for item in requested if item not in DEFAULT_KEY_STATES]
    if unsupported:
        raise ValueError(f"Unsupported key states requested: {unsupported}")

    records = load_layout_snapshot_records(run_dir)
    if not records:
        raise FileNotFoundError(f"No layout snapshot records found in {run_dir}")

    final_selected = [
        item
        for item in records
        if str(dict(item.get("event", {}) or {}).get("stage", "") or "").strip().lower() == "final_selected"
    ]
    final_record = final_selected[-1] if final_selected else records[-1]
    best_record = _normalize_record(run_dir, _select_best_candidate_record(records))

    selected: Dict[str, Dict[str, Any]] = {}
    for state_name in requested:
        if state_name == "initial":
            selected[state_name] = records[0]
        elif state_name == "best":
            selected[state_name] = best_record
        elif state_name == "final":
            selected[state_name] = final_record

    return {"records": records, "selected": selected}


def _path_if_exists(run_dir: Path, path: Path, *, repo_relative: bool = False) -> str:
    if not path.exists():
        return ""
    return serialize_repo_path(path) if repo_relative else serialize_run_path(run_dir, path)


def _list_visualization_paths(run_dir: Path) -> List[str]:
    visualizations_dir = run_dir / "visualizations"
    if not visualizations_dir.exists():
        return []
    paths: List[str] = []
    for path in sorted(visualizations_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(visualizations_dir)
        if relative.parts and relative.parts[0].lower() == "blender":
            continue
        paths.append(serialize_run_path(run_dir, path))
    return paths


def build_review_package_artifact_links(
    *,
    run_dir: Path,
    output_root: Path,
    step_path: str = "",
) -> Dict[str, Any]:
    planned_paths = planned_review_package_paths(output_root)
    output_image_path = planned_paths["output_image_path"]
    summary = load_json(run_dir / "summary.json")
    runtime_feature_fingerprint_path = str(
        summary.get("runtime_feature_fingerprint_path", "") or ""
    ).strip()
    mass_final_summary_zh_path = str(
        summary.get("mass_final_summary_zh_path", "") or ""
    ).strip()
    mass_final_summary_digest_path = str(
        summary.get("mass_final_summary_digest_path", "") or ""
    ).strip()
    llm_final_summary_zh_path = str(
        summary.get("llm_final_summary_zh_path", "") or ""
    ).strip()
    llm_final_summary_digest_path = str(
        summary.get("llm_final_summary_digest_path", "") or ""
    ).strip()
    return {
        "summary_path": _path_if_exists(run_dir, run_dir / "summary.json"),
        "report_path": _path_if_exists(run_dir, run_dir / "report.md"),
        "release_audit_path": _path_if_exists(run_dir, run_dir / "tables" / "release_audit.csv"),
        "layout_events_path": _path_if_exists(run_dir, run_dir / "events" / "layout_events.jsonl"),
        "runtime_feature_fingerprint_path": _path_if_exists(
            run_dir,
            run_dir / runtime_feature_fingerprint_path,
        )
        if runtime_feature_fingerprint_path
        else _path_if_exists(run_dir, run_dir / "events" / "runtime_feature_fingerprint.json"),
        "mass_final_summary_zh_path": _path_if_exists(
            run_dir,
            run_dir / mass_final_summary_zh_path,
        )
        if mass_final_summary_zh_path
        else _path_if_exists(run_dir, run_dir / "mass_final_summary_zh.md"),
        "mass_final_summary_digest_path": _path_if_exists(
            run_dir,
            run_dir / mass_final_summary_digest_path,
        )
        if mass_final_summary_digest_path
        else _path_if_exists(run_dir, run_dir / "events" / "mass_final_summary_digest.json"),
        "llm_final_summary_zh_path": _path_if_exists(run_dir, run_dir / llm_final_summary_zh_path)
        if llm_final_summary_zh_path
        else _path_if_exists(run_dir, run_dir / "llm_final_summary_zh.md"),
        "llm_final_summary_digest_path": _path_if_exists(
            run_dir,
            run_dir / llm_final_summary_digest_path,
        )
        if llm_final_summary_digest_path
        else _path_if_exists(run_dir, run_dir / "events" / "llm_final_summary_digest.json"),
        "attempts_table_path": _path_if_exists(run_dir, run_dir / "tables" / "attempts.csv"),
        "generations_table_path": _path_if_exists(run_dir, run_dir / "tables" / "generations.csv"),
        "policy_tuning_path": _path_if_exists(run_dir, run_dir / "tables" / "policy_tuning.csv"),
        "layout_timeline_path": _path_if_exists(run_dir, run_dir / "tables" / "layout_timeline.csv"),
        "visualization_paths": _list_visualization_paths(run_dir),
        "bundle_path": serialize_repo_path(planned_paths["bundle_path"]),
        "review_payload_path": serialize_repo_path(planned_paths["review_payload_path"]),
        "render_manifest_path": serialize_repo_path(planned_paths["manifest_path"]),
        "render_brief_path": serialize_repo_path(planned_paths["brief_path"]),
        "scene_script_path": serialize_repo_path(planned_paths["scene_script_path"]),
        "review_dashboard_path": "",
        "scene_audit_path": serialize_repo_path(planned_paths["scene_audit_path"]),
        "scene_readonly_checklist_path": serialize_repo_path(planned_paths["scene_readonly_checklist_path"]),
        "output_image_path": serialize_repo_path(output_image_path),
        "output_image_paths": [serialize_repo_path(output_image_path)],
        "output_blend_path": serialize_repo_path(planned_paths["output_blend_path"]),
        "step_path": str(step_path or ""),
    }


def _compact_state_summary(state_name: str, record: Dict[str, Any]) -> Dict[str, Any]:
    event = dict(record.get("event", {}) or {})
    snapshot = dict(record.get("snapshot", {}) or {})
    metadata = dict(snapshot.get("metadata", {}) or {})
    design_state = dict(snapshot.get("design_state", {}) or {})
    return {
        "name": state_name,
        "snapshot_path": str(record.get("persisted_snapshot_path", "") or ""),
        "stage": str(event.get("stage", snapshot.get("stage", "")) or ""),
        "thermal_source": str(event.get("thermal_source", snapshot.get("thermal_source", "")) or ""),
        "diagnosis_status": str(event.get("diagnosis_status", snapshot.get("diagnosis_status", "")) or ""),
        "diagnosis_reason": str(event.get("diagnosis_reason", snapshot.get("diagnosis_reason", "")) or ""),
        "metrics": dict(snapshot.get("metrics", event.get("metrics", {})) or {}),
        "operator_actions": _parse_operator_actions(event.get("operator_actions", snapshot.get("operator_actions", []))),
        "component_count": len(list(design_state.get("components", []) or [])),
        "layout_state_hash": str(metadata.get("layout_state_hash", "") or ""),
        "moved_components": list(event.get("moved_components", []) or []),
        "added_heatsinks": list(event.get("added_heatsinks", []) or []),
        "added_brackets": list(event.get("added_brackets", []) or []),
        "changed_contacts": list(event.get("changed_contacts", []) or []),
        "changed_coatings": list(event.get("changed_coatings", []) or []),
    }


def _build_attempt_trends(run_dir: Path) -> Dict[str, Any]:
    attempts_path = run_dir / "tables" / "attempts.csv"
    mass_trace_path = run_dir / "mass_trace.csv"
    rows = read_csv_rows(attempts_path)
    source_path = attempts_path if rows else mass_trace_path
    if not rows:
        rows = read_csv_rows(mass_trace_path)
    best_values = [
        float(row.get("best_cv"))
        for row in rows
        if isinstance(row.get("best_cv"), (int, float))
    ]
    return {
        "source_path": _path_if_exists(run_dir, source_path),
        "count": len(rows),
        "best_cv_min": min(best_values) if best_values else None,
        "rows": rows,
    }


def _build_generation_trends(run_dir: Path) -> Dict[str, Any]:
    generations_path = run_dir / "tables" / "generations.csv"
    rows = read_csv_rows(generations_path)
    return {
        "source_path": _path_if_exists(run_dir, generations_path),
        "count": len(rows),
        "rows": rows,
    }


def _build_operator_coverage(
    *,
    run_dir: Path,
    state_records: Dict[str, Dict[str, Any]],
    all_records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    policy_rows = read_csv_rows(run_dir / "tables" / "policy_tuning.csv")
    policy_events = read_jsonl_rows(run_dir / "events" / "policy_events.jsonl")

    family_counts: Dict[str, int] = {}
    action_counts: Dict[str, int] = {}
    state_actions: Dict[str, List[str]] = {}

    for state_name, record in state_records.items():
        event = dict(record.get("event", {}) or {})
        actions = _parse_operator_actions(event.get("operator_actions", []))
        state_actions[state_name] = actions
        for action in actions:
            action_counts[action] = int(action_counts.get(action, 0) or 0) + 1
            family = _operator_action_family(action)
            family_counts[family] = int(family_counts.get(family, 0) or 0) + 1

    timeline_actions = []
    for record in all_records:
        event = dict(record.get("event", {}) or {})
        timeline_actions.extend(_parse_operator_actions(event.get("operator_actions", [])))
    unique_timeline_actions = sorted(set(timeline_actions))

    policy_adjustments: List[Dict[str, Any]] = []
    for row in policy_rows:
        actions = row.get("actions")
        parsed_actions = actions if isinstance(actions, list) else _parse_scalar(actions)
        action_types: List[str] = []
        if isinstance(parsed_actions, list):
            for item in parsed_actions:
                if isinstance(item, dict):
                    action_type = str(item.get("type", "") or "").strip()
                    if action_type:
                        action_types.append(action_type)
        policy_adjustments.append(
            {
                "mode": str(row.get("mode", "") or ""),
                "stage": str(row.get("stage", "") or ""),
                "applied": bool(row.get("applied", False)),
                "action_types": sorted(set(action_types)),
                "selected_operator_program_id": str(row.get("selected_operator_program_id", "") or ""),
            }
        )

    return {
        "state_actions": state_actions,
        "timeline_unique_actions": unique_timeline_actions,
        "operator_action_counts": action_counts,
        "operator_family_counts": family_counts,
        "policy_rows_path": _path_if_exists(run_dir, run_dir / "tables" / "policy_tuning.csv"),
        "policy_event_path": _path_if_exists(run_dir, run_dir / "events" / "policy_events.jsonl"),
        "policy_adjustments": policy_adjustments,
        "policy_event_count": len(policy_events),
    }


def _design_state_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = dict(record.get("snapshot", {}) or {})
    return dict(snapshot.get("design_state", {}) or {})


def _build_layout_displacement(state_records: Dict[str, Dict[str, Any]], run_dir: Path) -> Dict[str, Any]:
    initial_record = state_records.get("initial")
    best_record = state_records.get("best")
    final_record = state_records.get("final")

    if initial_record is None or best_record is None or final_record is None:
        return {
            "layout_timeline_path": _path_if_exists(run_dir, run_dir / "tables" / "layout_timeline.csv"),
            "initial_to_best": {"count": 0, "rows": []},
            "best_to_final": {"count": 0, "rows": []},
        }

    initial_to_best = _compute_component_displacements(
        _design_state_from_record(initial_record),
        _design_state_from_record(best_record),
    )
    best_to_final = _compute_component_displacements(
        _design_state_from_record(best_record),
        _design_state_from_record(final_record),
    )

    def _pack(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        sorted_rows = sorted(rows, key=lambda item: float(item.get("dist", 0.0)), reverse=True)
        return {
            "count": len(rows),
            "max_displacement_mm": float(sorted_rows[0].get("dist", 0.0)) if sorted_rows else 0.0,
            "rows": rows,
            "top_rows": sorted_rows[: min(len(sorted_rows), 20)],
        }

    return {
        "layout_timeline_path": _path_if_exists(run_dir, run_dir / "tables" / "layout_timeline.csv"),
        "initial_to_best": _pack(initial_to_best),
        "best_to_final": _pack(best_to_final),
    }


def _build_timeline(run_dir: Path, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    for record in records:
        event = dict(record.get("event", {}) or {})
        events.append(
            {
                "sequence": int(event.get("sequence", 0) or 0),
                "iteration": int(event.get("iteration", 0) or 0),
                "attempt": int(event.get("attempt", 0) or 0),
                "stage": str(event.get("stage", "") or ""),
                "snapshot_path": str(record.get("persisted_snapshot_path", "") or ""),
                "thermal_source": str(event.get("thermal_source", "") or ""),
                "diagnosis_status": str(event.get("diagnosis_status", "") or ""),
                "diagnosis_reason": str(event.get("diagnosis_reason", "") or ""),
                "operator_actions": _parse_operator_actions(event.get("operator_actions", [])),
                "metrics": dict(event.get("metrics", dict(dict(record.get("snapshot", {}) or {}).get("metrics", {}) or {})) or {}),
            }
        )
    return {
        "layout_events_path": _path_if_exists(run_dir, run_dir / "events" / "layout_events.jsonl"),
        "count": len(events),
        "events": events,
    }


def _build_release_audit(run_dir: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    rows = read_csv_rows(run_dir / "tables" / "release_audit.csv")
    if rows:
        return dict(rows[-1])
    return {
        "run_id": str(summary.get("run_id", run_dir.name) or run_dir.name),
        "status": str(summary.get("status", "") or ""),
        "diagnosis_status": str(summary.get("diagnosis_status", "") or ""),
        "diagnosis_reason": str(summary.get("diagnosis_reason", "") or ""),
        "final_audit_status": str(summary.get("final_audit_status", "") or ""),
        "first_feasible_eval": summary.get("first_feasible_eval"),
        "comsol_calls_to_first_feasible": summary.get("comsol_calls_to_first_feasible"),
        "final_mph_path": str(summary.get("final_mph_path", "") or ""),
    }


def build_review_payload(
    *,
    run_dir: Path,
    output_root: Path,
    bundle_payload: Dict[str, Any],
    state_records: Dict[str, Dict[str, Any]],
    all_records: Sequence[Dict[str, Any]],
    summary: Dict[str, Any],
    notes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    artifact_links = build_review_package_artifact_links(
        run_dir=run_dir,
        output_root=output_root,
        step_path=str(dict(bundle_payload.get("source", {}) or {}).get("step_path", "") or ""),
    )
    payload_notes = [
        "This is the Phase 1 review-package payload; dashboard HTML is not emitted yet.",
        "Visualization-only heuristics must not be interpreted as solver or physics truth.",
        "Best-state selection reuses the existing layout snapshot helper semantics.",
    ]
    payload_notes.extend([str(item) for item in list(notes or []) if str(item).strip()])

    states = {state_name: _compact_state_summary(state_name, record) for state_name, record in state_records.items()}

    return {
        "schema_version": "blender_review_payload/v1",
        "run": {
            "run_id": str(summary.get("run_id", run_dir.name) or run_dir.name),
            "run_label": str(summary.get("run_label", "") or ""),
            "run_dir": serialize_repo_path(run_dir),
            "optimization_mode": str(summary.get("optimization_mode", "") or ""),
            "pymoo_algorithm": str(summary.get("pymoo_algorithm", summary.get("run_algorithm", "")) or ""),
            "status": str(summary.get("status", "") or ""),
            "diagnosis_status": str(summary.get("diagnosis_status", "") or ""),
        },
        "summary": dict(summary or {}),
        "release_audit": _build_release_audit(run_dir, summary),
        "states": states,
        "attempt_trends": _build_attempt_trends(run_dir),
        "generation_trends": _build_generation_trends(run_dir),
        "operator_coverage": _build_operator_coverage(run_dir=run_dir, state_records=state_records, all_records=all_records),
        "layout_displacement": _build_layout_displacement(state_records, run_dir),
        "timeline": _build_timeline(run_dir, all_records),
        "artifacts": artifact_links,
        "notes": payload_notes,
    }
