#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Minimal visualization helpers for the rebuilt scenario runtime.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


_OPERATOR_ACTION_FAMILY_MAP: Dict[str, str] = {
    "place_on_panel": "geometry",
    "align_payload_to_aperture": "aperture",
    "reorient_to_allowed_face": "aperture",
    "mount_to_bracket_site": "structural",
    "move_heat_source_to_radiator_zone": "thermal",
    "separate_hot_pair": "thermal",
    "add_heatstrap": "thermal",
    "add_thermal_pad": "thermal",
    "add_mount_bracket": "structural",
    "rebalance_cg_by_group_shift": "geometry",
    "shorten_power_bus": "power",
    "protect_fov_keepout": "mission",
    "activate_aperture_site": "aperture",
    "group_move": "geometry",
    "cg_recenter": "geometry",
    "hot_spread": "thermal",
    "swap": "geometry",
    "set_thermal_contact": "thermal",
    "add_bracket": "structural",
    "stiffener_insert": "structural",
    "bus_proximity_opt": "power",
    "fov_keepout_push": "mission",
}


def _coerce_json_like(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return {}
    try:
        return json.loads(text)
    except Exception:
        try:
            return ast.literal_eval(text)
        except Exception:
            return text


def _normalize_operator_action_name(raw_action: Any) -> str:
    if raw_action is None:
        return ""
    if isinstance(raw_action, float) and not np.isfinite(raw_action):
        return ""
    if isinstance(raw_action, Mapping):
        for key in ("action", "type", "operator", "name"):
            candidate = _normalize_operator_action_name(raw_action.get(key))
            if candidate:
                return candidate
        return ""
    text = str(raw_action or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"nan", "none", "null"}:
        return ""
    if text.startswith("{") and text.endswith("}"):
        parsed = _coerce_json_like(text)
        if isinstance(parsed, Mapping):
            return _normalize_operator_action_name(parsed)
    return text.strip("'\"[]() ").lower()


def _coerce_operator_action_values(raw_value: Any) -> List[Any]:
    if raw_value is None:
        return []
    if isinstance(raw_value, float) and not np.isfinite(raw_value):
        return []
    if isinstance(raw_value, list):
        return list(raw_value)
    if isinstance(raw_value, tuple):
        return list(raw_value)
    if isinstance(raw_value, set):
        return list(raw_value)
    if isinstance(raw_value, Mapping):
        return [raw_value]

    text = str(raw_value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "[]", "{}"}:
        return []

    parsed = _coerce_json_like(text)
    if isinstance(parsed, list):
        return list(parsed)
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, set):
        return list(parsed)
    if isinstance(parsed, Mapping):
        return [parsed]
    return [item for item in text.split(",") if str(item).strip()]


def _parse_operator_actions(raw_value: Any) -> List[str]:
    values = _coerce_operator_action_values(raw_value)
    actions: List[str] = []
    seen = set()
    for item in values:
        action = _normalize_operator_action_name(item)
        if not action or action in seen:
            continue
        seen.add(action)
        actions.append(action)
    return actions


def _merge_operator_actions(*raw_values: Any) -> List[str]:
    actions: List[str] = []
    seen = set()
    for raw_value in raw_values:
        for action in _parse_operator_actions(raw_value):
            if action in seen:
                continue
            seen.add(action)
            actions.append(action)
    return actions


def _resolve_record_operator_actions(
    event: Mapping[str, Any],
    snapshot: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    snapshot_payload = dict(snapshot or {})
    snapshot_metadata = dict(snapshot_payload.get("metadata", {}) or {})
    event_payload = dict(event or {})
    event_metadata = dict(event_payload.get("metadata", {}) or {})
    return _merge_operator_actions(
        snapshot_metadata.get("semantic_operator_actions", []),
        event_metadata.get("semantic_operator_actions", []),
        event_payload.get("operator_actions", snapshot_payload.get("operator_actions", [])),
        snapshot_metadata.get("selected_candidate_stubbed_actions", []),
        event_metadata.get("selected_candidate_stubbed_actions", []),
    )


def _operator_action_family(action: str) -> str:
    name = _normalize_operator_action_name(action)
    return str(_OPERATOR_ACTION_FAMILY_MAP.get(name, "other"))


def _load_layout_snapshot_records(experiment_dir: str) -> List[Dict[str, Any]]:
    run_path = Path(experiment_dir)
    candidates = [
        run_path / "artifacts" / "mass" / "snapshots",
        run_path / "snapshots",
    ]
    snapshot_dir = next((path for path in candidates if path.is_dir()), None)
    if snapshot_dir is None:
        return []

    records: List[Dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _component_state_map(state: Mapping[str, Any]) -> Dict[str, Dict[str, float]]:
    mapped: Dict[str, Dict[str, float]] = {}
    for component in list(state.get("components", []) or []):
        if not isinstance(component, Mapping):
            continue
        component_id = str(component.get("id", "") or "").strip()
        position = dict(component.get("position", {}) or {})
        if component_id:
            mapped[component_id] = {
                "x": float(position.get("x", 0.0) or 0.0),
                "y": float(position.get("y", 0.0) or 0.0),
                "z": float(position.get("z", 0.0) or 0.0),
            }
    return mapped


def _compute_component_displacements(
    initial_state: Mapping[str, Any],
    final_state: Mapping[str, Any],
) -> Dict[str, float]:
    initial = _component_state_map(initial_state)
    final = _component_state_map(final_state)
    result: Dict[str, float] = {}
    for component_id, initial_pos in initial.items():
        final_pos = final.get(component_id)
        if not final_pos:
            continue
        delta = np.asarray(
            [
                float(final_pos["x"]) - float(initial_pos["x"]),
                float(final_pos["y"]) - float(initial_pos["y"]),
                float(final_pos["z"]) - float(initial_pos["z"]),
            ],
            dtype=float,
        )
        result[component_id] = float(np.linalg.norm(delta))
    return result


def _select_best_candidate_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {}
    candidates = []
    for record in records:
        metrics = dict(record.get("metrics", {}) or {})
        best_cv = metrics.get("best_cv", record.get("best_cv"))
        try:
            cv = float(best_cv)
        except Exception:
            cv = float("inf")
        candidates.append((cv, record))
    candidates.sort(key=lambda item: item[0])
    return dict(candidates[0][1] or {})


def _load_summary_safely(experiment_dir: str) -> Dict[str, Any]:
    summary_path = Path(experiment_dir) / "summary.json"
    if not summary_path.exists():
        return {}
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _write_summary_plot(path: Path, metrics: Dict[str, Any]) -> None:
    numeric_items = []
    for key, value in metrics.items():
        try:
            numeric_items.append((str(key), float(value)))
        except Exception:
            continue
    if not numeric_items:
        return

    labels = [item[0] for item in numeric_items]
    values = [item[1] for item in numeric_items]
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.8), 4))
    ax.bar(labels, values, color="#2563eb")
    ax.set_title("Final Metrics")
    ax.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def generate_visualizations(experiment_dir: str):
    run_path = Path(experiment_dir)
    viz_dir = run_path / "visualizations"
    viz_dir.mkdir(parents=True, exist_ok=True)

    summary = _load_summary_safely(experiment_dir)
    figures_dir = run_path / "figures"
    fields_dir = run_path / "fields"
    summary_plot_path = viz_dir / "final_metrics.png"
    _write_summary_plot(summary_plot_path, dict(summary.get("final_metrics", {}) or {}))

    lines = [
        "=== Scenario Visualization Summary ===",
        f"- Run dir: {run_path}",
        f"- Status: {str(summary.get('status', '') or 'UNKNOWN')}",
        f"- Stack: {str(summary.get('stack', '') or summary.get('run_mode', '') or 'mass')}",
        f"- Scenario: {str(summary.get('scenario_id', '') or 'n/a')}",
        f"- Archetype: {str(summary.get('archetype_id', '') or 'n/a')}",
        f"- Requested physics profile: {str(summary.get('requested_physics_profile', '') or 'n/a')}",
        f"- Effective physics profile: {str(summary.get('effective_physics_profile', '') or 'n/a')}",
    ]

    figure_paths = sorted(str(path.name) for path in figures_dir.glob("*.png")) if figures_dir.is_dir() else []
    if figure_paths:
        lines.append(f"- Figures: {', '.join(figure_paths)}")

    field_paths = sorted(str(path.name) for path in fields_dir.glob("*")) if fields_dir.is_dir() else []
    if field_paths:
        lines.append(f"- Field exports: {', '.join(field_paths)}")

    if summary_plot_path.exists():
        lines.append(f"- Visualization metrics plot: {summary_plot_path.name}")

    final_metrics = dict(summary.get("final_metrics", {}) or {})
    if final_metrics:
        lines.append("=== Final Metrics ===")
        for key, value in sorted(final_metrics.items()):
            lines.append(f"- {key}: {value}")

    (viz_dir / "visualization_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        generate_visualizations(sys.argv[1])
    else:
        print("Usage: python core/visualization.py <experiment_dir>")
