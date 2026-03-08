"""
VOP 中文最终结果总结产物生成。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.path_policy import serialize_run_path
from core.runtime_feature_fingerprint import (
    fingerprint_display_rows,
    fingerprint_rel_path,
    load_runtime_feature_fingerprint,
    persist_runtime_feature_fingerprint,
)


SUMMARY_MD_FILENAME = "llm_final_summary_zh.md"
DIGEST_REL_PATH = Path("events") / "llm_final_summary_digest.json"
DIGEST_SCHEMA_VERSION = 1
SUMMARY_LANGUAGE = "zh-CN"
MAX_QUOTE_LENGTH = 160
KEY_METRIC_ORDER = [
    "max_temp",
    "min_clearance",
    "cg_offset",
    "num_collisions",
    "boundary_violation",
    "safety_factor",
    "first_modal_freq",
    "power_margin",
    "voltage_drop",
    "peak_power",
]


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    try:
        reader = csv.DictReader(lines)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for row in reader:
        if isinstance(row, dict):
            rows.append({str(key): value for key, value in row.items()})
    return rows


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _to_jsonable(tolist_fn())
        except Exception:
            pass
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _to_jsonable(item_fn())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    return str(value)


def _collapse_text(value: Any) -> str:
    text = str(value or "").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    collapsed = " / ".join(lines)
    return re.sub(r"\s+", " ", collapsed).strip()


def _truncate_text(value: Any, limit: int = 240) -> str:
    text = _collapse_text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _parse_jsonish(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return _to_jsonable(value)
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return {}
    try:
        return _to_jsonable(json.loads(text))
    except Exception:
        return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _json_text(value: Any) -> str:
    payload = _to_jsonable(value)
    if payload in ({}, [], "", None):
        return ""
    if isinstance(payload, str):
        return _collapse_text(payload)
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(payload)
    return _collapse_text(text)


def _compact_mapping(value: Any) -> str:
    text = _json_text(value)
    return text or "n/a"


def _listify(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _relative_path(run_path: Path, path: Path) -> str:
    return serialize_run_path(str(run_path), str(path))


def _latest_release_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    return dict(rows[-1] or {})


def _round_sort_key(row: Dict[str, Any]) -> tuple[int, str, str]:
    return (
        _safe_int(row.get("round_index", 0), 0),
        str(row.get("stage", "") or ""),
        str(row.get("policy_id", "") or ""),
    )


def _rows_from_summary(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    delegated = dict(summary.get("vop_delegated_effect_summary", {}) or {})
    if not decision and not delegated:
        return []
    effectiveness_summary = {
        "verdict": str(delegated.get("effectiveness_verdict", "") or ""),
        "diagnosis_status": str(delegated.get("diagnosis_status", "") or ""),
        "diagnosis_reason": str(delegated.get("diagnosis_reason", "") or ""),
    }
    return [
        {
            "round_index": int(summary.get("vop_policy_primary_round_index", 0) or 0),
            "stage": "bootstrap",
            "policy_id": str(decision.get("policy_id", "") or summary.get("vop_policy_id", "") or ""),
            "selected_operator_program_id": str(
                decision.get("selected_operator_program_id", "")
                or summary.get("vop_selected_operator_program_id", "")
                or ""
            ),
            "operator_actions": list(decision.get("operator_actions", []) or []),
            "search_space_override": str(
                decision.get("search_space_override", "")
                or summary.get("vop_search_space_override", "")
                or ""
            ),
            "decision_rationale": str(decision.get("decision_rationale", "") or ""),
            "change_summary": _to_jsonable(
                decision.get("change_set", {}) or decision.get("intent_changes", {})
            ),
            "runtime_overrides": _to_jsonable(decision.get("runtime_overrides", {})),
            "fidelity_plan": _to_jsonable(decision.get("fidelity_plan", {})),
            "expected_effects": _to_jsonable(decision.get("expected_effects", {})),
            "observed_effects": _to_jsonable(delegated.get("observed_effects", {})),
            "effectiveness_summary": _to_jsonable(effectiveness_summary),
            "replan_reason": str(
                (summary.get("vop_reflective_replanning", {}) or {}).get("trigger_reason", "")
                or ""
            ),
        }
    ]


def _load_round_rows(run_path: Path, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _load_csv_rows(run_path / "tables" / "vop_rounds.csv")
    if not rows:
        rows = list(summary.get("vop_round_audit_digest", []) or [])
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        payload = dict(row or {})
        for json_column in (
            "operator_actions",
            "change_summary",
            "runtime_overrides",
            "fidelity_plan",
            "expected_effects",
            "observed_effects",
            "effectiveness_summary",
            "vop_decision_summary",
            "vop_delegated_effect_summary",
        ):
            if json_column in payload:
                payload[json_column] = _parse_jsonish(payload.get(json_column))
        normalized.append(payload)
    if not normalized:
        normalized = _rows_from_summary(summary)
    normalized.sort(key=_round_sort_key)
    return normalized


def _find_first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def _load_policy_artifacts(run_path: Path) -> Dict[str, Any]:
    vop_dir = run_path / "artifacts" / "vop_maas" / "llm_interactions"
    delegated_dir = run_path / "artifacts" / "vop_maas" / "delegated_mass" / "llm_interactions"
    policy_req_path = _find_first_existing(sorted(vop_dir.glob("*vop_policy_programmer_req.json")))
    policy_resp_path = _find_first_existing(sorted(vop_dir.glob("*vop_policy_programmer_resp.json")))
    delegated_formulation_path = _find_first_existing(
        sorted(delegated_dir.glob("*model_agent_formulation*_resp.json"))
    )
    return {
        "policy_request_path": policy_req_path,
        "policy_request": _read_json(policy_req_path) if policy_req_path is not None else {},
        "policy_response_path": policy_resp_path,
        "policy_response": _read_json(policy_resp_path) if policy_resp_path is not None else {},
        "delegated_formulation_path": delegated_formulation_path,
        "delegated_formulation": _read_json(delegated_formulation_path)
        if delegated_formulation_path is not None
        else {},
    }


def _extract_constraint_focus(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
    policy_response: Dict[str, Any],
) -> List[str]:
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    candidates: List[Any] = []
    policy_pack = dict(policy_response.get("policy_pack", {}) or {})
    candidates.extend(_listify(policy_pack.get("constraint_focus")))
    for row in round_rows:
        candidates.extend(_listify(row.get("constraint_focus")))
    candidates.extend(_listify(decision.get("constraint_focus")))
    normalized: List[str] = []
    seen = set()
    for item in candidates:
        token = str(item or "").strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(token)
    return normalized


def _extract_hard_constraints(formulation_payload: Dict[str, Any]) -> List[str]:
    rows = list(formulation_payload.get("normalized_hard_constraints", []) or [])
    rendered: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric_key = str(row.get("metric_key", "") or row.get("name", "") or "").strip()
        relation = str(row.get("original_relation", "") or "").strip()
        target = row.get("target_value")
        if metric_key and relation:
            rendered.append(f"{metric_key} {relation} {target}")
        elif metric_key:
            rendered.append(metric_key)
    return rendered[:10]


def _extract_objectives_brief(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
    formulation_payload: Dict[str, Any],
) -> List[str]:
    objective_rows = list(formulation_payload.get("objectives", []) or [])
    rendered: List[str] = []
    for row in objective_rows:
        if not isinstance(row, dict):
            continue
        metric_key = str(row.get("metric_key", "") or row.get("name", "") or "").strip()
        sense = str(row.get("sense", "") or row.get("direction", "") or "").strip()
        if metric_key and sense:
            rendered.append(f"{sense} {metric_key}")
        elif metric_key:
            rendered.append(metric_key)
    if rendered:
        return rendered[:8]

    for row in round_rows:
        expected = _parse_jsonish(row.get("expected_effects"))
        if isinstance(expected, dict) and expected:
            return [f"{key}: {value}" for key, value in list(expected.items())[:6]]

    best_metrics = dict(summary.get("best_candidate_metrics", {}) or {})
    return [key for key in KEY_METRIC_ORDER if key in best_metrics][:4]


def _extract_requirement_full(policy_request: Dict[str, Any]) -> str:
    return str(policy_request.get("requirement_text", "") or "").strip()


def _extract_requirement_lines(policy_request: Dict[str, Any]) -> List[str]:
    requirement = _extract_requirement_full(policy_request)
    if not requirement:
        return []
    return [line.strip() for line in requirement.splitlines() if line.strip()]


def _extract_requirement_brief(policy_request: Dict[str, Any]) -> str:
    lines = _extract_requirement_lines(policy_request)
    return _truncate_text("；".join(lines[:3]), 220) if lines else ""


def _extract_hard_constraint_rows(formulation_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = list(formulation_payload.get("normalized_hard_constraints", []) or [])
    rendered: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rendered.append(
            {
                "约束项": str(row.get("metric_key", "") or row.get("name", "") or "").strip()
                or "n/a",
                "关系": str(row.get("original_relation", "") or "").strip() or "n/a",
                "目标值": str(row.get("target_value", "") or "n/a"),
                "规范形式": str(row.get("normalized_g_leq_0", "") or "").strip() or "n/a",
            }
        )
    return rendered[:12]


def _extract_objective_rows(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
    formulation_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    objective_rows = list(formulation_payload.get("objectives", []) or [])
    rendered: List[Dict[str, Any]] = []
    for row in objective_rows:
        if not isinstance(row, dict):
            continue
        rendered.append(
            {
                "目标项": str(row.get("metric_key", "") or row.get("name", "") or "").strip()
                or "n/a",
                "方向": str(row.get("sense", "") or row.get("direction", "") or "").strip()
                or "n/a",
            }
        )
    if rendered:
        return rendered[:10]
    fallback = _extract_objectives_brief(summary, round_rows, formulation_payload)
    return [{"目标项": str(item or "n/a"), "方向": "n/a"} for item in fallback]


def _flatten_mapping_rows(
    value: Any,
    *,
    prefix: str = "",
    max_depth: int = 3,
) -> List[Dict[str, str]]:
    payload = _to_jsonable(value)
    rows: List[Dict[str, str]] = []

    def _walk(node: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            rows.append({"字段": path or "value", "内容": _truncate_text(node, 220) or "n/a"})
            return
        if isinstance(node, dict):
            if not node:
                rows.append({"字段": path or "value", "内容": "n/a"})
                return
            for key, item in node.items():
                next_path = f"{path}.{key}" if path else str(key)
                _walk(item, next_path, depth + 1)
            return
        if isinstance(node, list):
            if not node:
                rows.append({"字段": path or "value", "内容": "n/a"})
                return
            if all(not isinstance(item, (dict, list)) for item in node):
                rows.append(
                    {"字段": path or "value", "内容": ", ".join(str(item) for item in node) or "n/a"}
                )
                return
            for index, item in enumerate(node):
                next_path = f"{path}[{index}]" if path else f"[{index}]"
                _walk(item, next_path, depth + 1)
            return
        rows.append({"字段": path or "value", "内容": _truncate_text(node, 220) or "n/a"})

    _walk(payload, prefix, 0)
    normalized: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        key = (row.get("字段", ""), row.get("内容", ""))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return normalized


def _cell_text(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "n/a"
        if all(not isinstance(item, (dict, list)) for item in value):
            return "<br>".join(str(item) for item in value)
    if isinstance(value, dict):
        return _json_text(value).replace("|", "\\|").replace("\n", "<br>") or "n/a"
    text = str(value).strip()
    return text.replace("|", "\\|").replace("\n", "<br>") or "n/a"


def _render_markdown_table(headers: List[str], rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["| 字段 | 内容 |", "| --- | --- |", "| n/a | n/a |"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell_text(row.get(header, "")) for header in headers) + " |")
    return lines


def _select_initial_metrics(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
    policy_request: Dict[str, Any],
) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    vop_graph = dict(policy_request.get("vop_graph", {}) or {})
    for node in list(vop_graph.get("nodes", []) or []):
        if not isinstance(node, dict):
            continue
        if str(node.get("node_type", "") or "") != "metric":
            continue
        label = str(node.get("label", "") or "").strip()
        value = (dict(node.get("attributes", {}) or {})).get("value")
        if label:
            metrics[label] = value
    if metrics:
        return metrics

    if round_rows:
        observed = _parse_jsonish(dict(round_rows[0] or {}).get("observed_effects"))
        if isinstance(observed, dict):
            for key in KEY_METRIC_ORDER:
                if key in observed:
                    metrics[key] = observed[key]
    if metrics:
        return metrics

    best_metrics = dict(summary.get("best_candidate_metrics", {}) or {})
    for key in KEY_METRIC_ORDER:
        if key in best_metrics:
            metrics[key] = best_metrics[key]
    return metrics


def _infer_initial_state_summary(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
    release_row: Dict[str, Any],
    policy_request: Dict[str, Any],
) -> Dict[str, Any]:
    first_row = dict(round_rows[0] or {}) if round_rows else {}
    effectiveness = _parse_jsonish(first_row.get("effectiveness_summary"))
    diagnosis_status = str(
        (effectiveness.get("diagnosis_status", "") if isinstance(effectiveness, dict) else "")
        or summary.get("diagnosis_status", "")
        or release_row.get("diagnosis_status", "")
        or ""
    )
    diagnosis_reason = str(
        (effectiveness.get("diagnosis_reason", "") if isinstance(effectiveness, dict) else "")
        or first_row.get("replan_reason", "")
        or summary.get("diagnosis_reason", "")
        or release_row.get("diagnosis_reason", "")
        or ""
    )
    return {
        "bootstrap_round_index": int(first_row.get("round_index", 0) or 0),
        "bootstrap_stage": str(first_row.get("stage", "") or "bootstrap"),
        "key_metrics": _to_jsonable(
            _select_initial_metrics(summary, round_rows, policy_request)
        ),
        "dominant_problem": diagnosis_reason,
        "is_feasible": diagnosis_status == "feasible",
        "initial_audit_status": str(
            release_row.get("final_audit_status", "")
            or summary.get("final_audit_status", "")
            or ""
        ),
        "diagnosis_status": diagnosis_status,
    }


def _build_what_changed(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "search_space_override": str(row.get("search_space_override", "") or ""),
        "operator_actions": list(_listify(_parse_jsonish(row.get("operator_actions")))),
        "runtime_overrides": _to_jsonable(_parse_jsonish(row.get("runtime_overrides"))),
        "fidelity_plan": _to_jsonable(_parse_jsonish(row.get("fidelity_plan"))),
        "change_summary": _to_jsonable(_parse_jsonish(row.get("change_summary"))),
    }


def _extract_verdict(effectiveness_summary: Any) -> str:
    payload = _parse_jsonish(effectiveness_summary)
    if isinstance(payload, dict):
        for key in ("verdict", "effectiveness_verdict", "status"):
            value = str(payload.get(key, "") or "").strip()
            if value:
                return value
    return _truncate_text(payload, 120)


def _build_decision_flow(
    summary: Dict[str, Any],
    round_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in round_rows:
        policy_id = str(
            row.get("final_policy_id", "")
            or row.get("policy_id", "")
            or row.get("candidate_policy_id", "")
            or ""
        )
        rows.append(
            {
                "round_index": int(row.get("round_index", 0) or 0),
                "stage": str(row.get("stage", "") or ""),
                "policy_id": policy_id,
                "selected_operator_program_id": str(
                    row.get("selected_operator_program_id", "") or ""
                ),
                "decision_rationale": str(row.get("decision_rationale", "") or ""),
                "what_changed": _to_jsonable(_build_what_changed(row)),
                "expected_effects": _to_jsonable(_parse_jsonish(row.get("expected_effects"))),
                "observed_effects": _to_jsonable(_parse_jsonish(row.get("observed_effects"))),
                "replan_reason": str(
                    row.get("replan_reason", "") or row.get("trigger_reason", "") or ""
                ),
                "effectiveness_verdict": _extract_verdict(row.get("effectiveness_summary")),
            }
        )
    if rows:
        return rows

    decision = dict(summary.get("vop_decision_summary", {}) or {})
    delegated = dict(summary.get("vop_delegated_effect_summary", {}) or {})
    return [
        {
            "round_index": int(summary.get("vop_policy_primary_round_index", 0) or 0),
            "stage": "bootstrap",
            "policy_id": str(decision.get("policy_id", "") or summary.get("vop_policy_id", "") or ""),
            "selected_operator_program_id": str(
                decision.get("selected_operator_program_id", "")
                or summary.get("vop_selected_operator_program_id", "")
                or ""
            ),
            "decision_rationale": str(decision.get("decision_rationale", "") or ""),
            "what_changed": {
                "search_space_override": str(
                    decision.get("search_space_override", "")
                    or summary.get("vop_search_space_override", "")
                    or ""
                ),
                "operator_actions": list(decision.get("operator_actions", []) or []),
                "runtime_overrides": _to_jsonable(decision.get("runtime_overrides", {})),
                "fidelity_plan": _to_jsonable(decision.get("fidelity_plan", {})),
                "change_summary": _to_jsonable(
                    decision.get("change_set", {}) or decision.get("intent_changes", {})
                ),
            },
            "expected_effects": _to_jsonable(decision.get("expected_effects", {})),
            "observed_effects": _to_jsonable(delegated.get("observed_effects", {})),
            "replan_reason": str(
                (summary.get("vop_reflective_replanning", {}) or {}).get("trigger_reason", "")
                or ""
            ),
            "effectiveness_verdict": str(
                delegated.get("effectiveness_verdict", "") or "not_observed"
            ),
        }
    ]


def _build_operator_summary(
    summary: Dict[str, Any],
    decision_flow: List[Dict[str, Any]],
) -> Dict[str, Any]:
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    selected_operator_program_id = str(
        decision.get("selected_operator_program_id", "")
        or summary.get("vop_selected_operator_program_id", "")
        or ""
    )
    operator_actions = list(decision.get("operator_actions", []) or [])
    all_programs: List[Dict[str, Any]] = []

    for item in decision_flow:
        what_changed = dict(item.get("what_changed", {}) or {})
        actions = list(_listify(what_changed.get("operator_actions")))
        all_programs.append(
            {
                "round_index": int(item.get("round_index", 0) or 0),
                "stage": str(item.get("stage", "") or ""),
                "policy_id": str(item.get("policy_id", "") or ""),
                "selected_operator_program_id": str(
                    item.get("selected_operator_program_id", "") or ""
                ),
                "operator_actions": actions,
            }
        )
        if not operator_actions and actions:
            operator_actions = actions

    return {
        "selected_operator_program_id": selected_operator_program_id,
        "operator_actions": operator_actions,
        "all_selected_programs_by_round": _to_jsonable(all_programs),
    }


def _build_optimization_scheme(summary: Dict[str, Any]) -> Dict[str, Any]:
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    return {
        "search_space_override": str(
            decision.get("search_space_override", "")
            or summary.get("vop_search_space_override", "")
            or ""
        ),
        "runtime_overrides": _to_jsonable(
            decision.get("runtime_overrides", {}) or summary.get("vop_runtime_overrides", {})
        ),
        "fidelity_plan": _to_jsonable(
            decision.get("fidelity_plan", {}) or summary.get("vop_fidelity_overrides", {})
        ),
        "delegated_execution_mode": str(
            summary.get("delegated_execution_mode", "")
            or summary.get("execution_mode", "")
            or "mass"
        ),
    }


def _build_delegated_mass_result(
    summary: Dict[str, Any],
    release_row: Dict[str, Any],
) -> Dict[str, Any]:
    delegated = dict(summary.get("vop_delegated_effect_summary", {}) or {})
    return {
        "diagnosis_status": str(
            delegated.get("diagnosis_status", "")
            or summary.get("diagnosis_status", "")
            or release_row.get("diagnosis_status", "")
            or ""
        ),
        "diagnosis_reason": str(
            delegated.get("diagnosis_reason", "")
            or summary.get("diagnosis_reason", "")
            or release_row.get("diagnosis_reason", "")
            or ""
        ),
        "search_space_effect": str(delegated.get("search_space_effect", "") or ""),
        "first_feasible_eval": delegated.get(
            "first_feasible_eval",
            summary.get("first_feasible_eval", release_row.get("first_feasible_eval")),
        ),
        "comsol_calls_to_first_feasible": delegated.get(
            "comsol_calls_to_first_feasible",
            summary.get(
                "comsol_calls_to_first_feasible",
                release_row.get("comsol_calls_to_first_feasible"),
            ),
        ),
        "final_audit_status": str(
            delegated.get("audit_status", "")
            or summary.get("final_audit_status", "")
            or release_row.get("final_audit_status", "")
            or ""
        ),
        "effectiveness_verdict": str(
            delegated.get("effectiveness_verdict", "") or "not_observed"
        ),
    }


def _select_final_key_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics = dict(summary.get("best_candidate_metrics", {}) or {})
    selected: Dict[str, Any] = {}
    for key in KEY_METRIC_ORDER:
        if key in metrics:
            selected[key] = metrics[key]
    return selected


def _build_final_result_summary(
    summary: Dict[str, Any],
    delegated_mass_result: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "status": str(summary.get("status", "") or ""),
        "final_iteration": int(summary.get("final_iteration", 0) or 0),
        "effectiveness_verdict": str(
            delegated_mass_result.get("effectiveness_verdict", "") or "not_observed"
        ),
        "final_mph_path": str(summary.get("final_mph_path", "") or ""),
        "key_metrics": _to_jsonable(_select_final_key_metrics(summary)),
    }


def _quote_entry(stage: str, source: str, speaker: str, text: Any) -> Dict[str, Any]:
    return {
        "stage": str(stage or ""),
        "source": str(source or ""),
        "speaker": str(speaker or ""),
        "quote_text": _truncate_text(text, MAX_QUOTE_LENGTH),
    }


def _build_evidence_quotes(
    run_path: Path,
    decision_flow: List[Dict[str, Any]],
    policy_response_path: Optional[Path],
) -> List[Dict[str, Any]]:
    quotes: List[Dict[str, Any]] = []
    response_source = (
        _relative_path(run_path, policy_response_path) if policy_response_path else "summary.json"
    )
    for item in decision_flow:
        stage = f"V{int(item.get('round_index', 0) or 0)}:{str(item.get('stage', '') or 'round')}"
        rationale = str(item.get("decision_rationale", "") or "").strip()
        if rationale:
            quotes.append(_quote_entry(stage, response_source, "VOP Controller", rationale))
        observed_text = _json_text(item.get("observed_effects", {}))
        if observed_text:
            quotes.append(_quote_entry(stage, "tables/vop_rounds.csv", "Delegated Mass", observed_text))
    return quotes[:12]


def _build_source_artifacts(run_path: Path, extra_paths: Iterable[Path]) -> List[str]:
    rendered: List[str] = []
    seen = set()
    for path in extra_paths:
        if not path or not str(path):
            continue
        candidate = Path(path)
        if not candidate.exists():
            continue
        rel = _relative_path(run_path, candidate)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        rendered.append(rel)
    return rendered


def _format_key_metric_rows(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    rendered: List[Dict[str, Any]] = []
    for key, value in dict(metrics or {}).items():
        rendered.append({"指标": key, "数值": value})
    return rendered or [{"指标": "n/a", "数值": "n/a"}]


def _render_blockquote(lines: List[str]) -> List[str]:
    if not lines:
        return ["> n/a"]
    return ["> " + line for line in lines]


def _build_conclusion_sentence(
    delegated_mass_result: Dict[str, Any],
    final_result_summary: Dict[str, Any],
) -> str:
    diagnosis = str(delegated_mass_result.get("diagnosis_status", "") or "")
    audit_status = str(delegated_mass_result.get("final_audit_status", "") or "")
    verdict = str(final_result_summary.get("effectiveness_verdict", "") or "")
    status = str(final_result_summary.get("status", "") or "")
    if diagnosis == "feasible":
        if audit_status and audit_status not in {"passed", "ok", "success"}:
            return f"已得到可行解，但最终审计状态为 {audit_status}，仍需继续收口。"
        return "已得到可行且可交付的委托执行结果，控制层策略与观测结果基本一致。"
    if diagnosis:
        return f"本次运行状态为 {status or diagnosis}，delegated mass 诊断为 {diagnosis}，仍需继续调整策略。"
    if verdict:
        return f"本次运行已完成，但效果判断为 {verdict}，建议继续做下一轮策略迭代。"
    return "本次运行已完成，但缺少足够的最终诊断信息来形成更强结论。"


def build_vop_final_summary_digest(
    run_dir: str,
    *,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary = _read_json(run_path / "summary.json")
    manifest = _read_json(run_path / "events" / "run_manifest.json")
    run_mode = str(summary.get("run_mode", "") or manifest.get("run_mode", "") or "").strip()
    if run_mode != "vop_maas":
        raise ValueError(
            f"llm_final_summary_zh only supports vop_maas runs, got: {run_mode or 'unknown'}"
        )

    policy_artifacts = _load_policy_artifacts(run_path)
    round_rows = _load_round_rows(run_path, summary)
    release_rows = _load_csv_rows(run_path / "tables" / "release_audit.csv")
    release_row = _latest_release_row(release_rows)
    decision_flow = _build_decision_flow(summary, round_rows)
    delegated_mass_result = _build_delegated_mass_result(summary, release_row)
    final_result_summary = _build_final_result_summary(summary, delegated_mass_result)
    runtime_fingerprint = (
        persist_runtime_feature_fingerprint(str(run_path), runtime_config=runtime_config)
        if runtime_config is not None
        else load_runtime_feature_fingerprint(str(run_path))
    )
    if not runtime_fingerprint:
        runtime_fingerprint = persist_runtime_feature_fingerprint(
            str(run_path),
            runtime_config=runtime_config,
        )

    source_artifacts = _build_source_artifacts(
        run_path,
        [
            run_path / "summary.json",
            run_path / "events" / "run_manifest.json",
            run_path / "tables" / "vop_rounds.csv",
            run_path / "tables" / "policy_tuning.csv",
            run_path / "tables" / "phases.csv",
            run_path / "tables" / "release_audit.csv",
            run_path / "events" / "runtime_feature_fingerprint.json",
            policy_artifacts.get("policy_request_path"),
            policy_artifacts.get("policy_response_path"),
            policy_artifacts.get("delegated_formulation_path"),
        ],
    )

    return _to_jsonable(
        {
            "schema_version": int(DIGEST_SCHEMA_VERSION),
            "run_identity": {
                "run_id": str(summary.get("run_id", "") or manifest.get("run_id", "") or ""),
                "run_mode": run_mode,
                "execution_mode": str(
                    summary.get("execution_mode", "")
                    or manifest.get("execution_mode", "")
                    or ""
                ),
                "timestamp": str(
                    summary.get("timestamp", "") or manifest.get("timestamp", "") or ""
                ),
                "level": str(summary.get("run_label", "") or manifest.get("run_label", "") or ""),
                "algorithm": str(
                    summary.get("run_algorithm", "")
                    or summary.get("pymoo_algorithm", "")
                    or manifest.get("run_algorithm", "")
                    or ""
                ),
            },
            "runtime_feature_fingerprint_path": fingerprint_rel_path(str(run_path)),
            "runtime_feature_fingerprint": runtime_fingerprint,
            "goal_summary": {
                "requirement_text_brief": _extract_requirement_brief(policy_artifacts["policy_request"]),
                "requirement_text_full": _extract_requirement_full(policy_artifacts["policy_request"]),
                "requirement_text_lines": _extract_requirement_lines(policy_artifacts["policy_request"]),
                "constraint_focus": _extract_constraint_focus(
                    summary,
                    round_rows,
                    policy_artifacts["policy_response"],
                ),
                "hard_constraints_brief": _extract_hard_constraints(
                    policy_artifacts["delegated_formulation"]
                ),
                "hard_constraint_rows": _extract_hard_constraint_rows(
                    policy_artifacts["delegated_formulation"]
                ),
                "objectives_brief": _extract_objectives_brief(
                    summary,
                    round_rows,
                    policy_artifacts["delegated_formulation"],
                ),
                "objective_rows": _extract_objective_rows(
                    summary,
                    round_rows,
                    policy_artifacts["delegated_formulation"],
                ),
            },
            "initial_state_summary": _infer_initial_state_summary(
                summary,
                round_rows,
                release_row,
                policy_artifacts["policy_request"],
            ),
            "decision_flow": _to_jsonable(decision_flow),
            "operator_summary": _build_operator_summary(summary, decision_flow),
            "optimization_scheme": _build_optimization_scheme(summary),
            "delegated_mass_result": delegated_mass_result,
            "final_result_summary": final_result_summary,
            "evidence_quotes": _build_evidence_quotes(
                run_path,
                decision_flow,
                policy_artifacts.get("policy_response_path"),
            ),
            "source_artifacts": source_artifacts,
        }
    )


def render_vop_final_summary_markdown(digest: Dict[str, Any]) -> str:
    payload = dict(digest or {})
    run_identity = dict(payload.get("run_identity", {}) or {})
    goal_summary = dict(payload.get("goal_summary", {}) or {})
    initial_state_summary = dict(payload.get("initial_state_summary", {}) or {})
    operator_summary = dict(payload.get("operator_summary", {}) or {})
    optimization_scheme = dict(payload.get("optimization_scheme", {}) or {})
    delegated_mass_result = dict(payload.get("delegated_mass_result", {}) or {})
    final_result_summary = dict(payload.get("final_result_summary", {}) or {})
    decision_flow = list(payload.get("decision_flow", []) or [])
    evidence_quotes = list(payload.get("evidence_quotes", []) or [])
    source_artifacts = list(payload.get("source_artifacts", []) or [])
    runtime_feature_fingerprint = dict(payload.get("runtime_feature_fingerprint", {}) or {})
    fingerprint_tables = fingerprint_display_rows(runtime_feature_fingerprint)
    llm_narrative_summary = str(payload.get("llm_narrative_summary", "") or "").strip()

    lines: List[str] = [
        "# LLM 最终结果总结（中文）",
        "",
        "## 运行身份",
        "",
        *_render_markdown_table(
            ["字段", "内容"],
            [
                {"字段": "运行 ID", "内容": run_identity.get("run_id", "") or "n/a"},
                {"字段": "运行模式", "内容": run_identity.get("run_mode", "") or "n/a"},
                {"字段": "执行核心", "内容": run_identity.get("execution_mode", "") or "n/a"},
                {"字段": "时间戳", "内容": run_identity.get("timestamp", "") or "n/a"},
                {"字段": "Level", "内容": run_identity.get("level", "") or "n/a"},
                {"字段": "算法", "内容": run_identity.get("algorithm", "") or "n/a"},
            ],
        ),
        "",
        "## 运行基线与功能线指纹",
        "",
        "### Requested Baseline vs Effective Runtime",
        "",
        *_render_markdown_table(
            ["Feature", "Requested", "Effective", "Notes"],
            list(fingerprint_tables.get("baseline_table", []) or []),
        ),
        "",
        "### Gate Audit",
        "",
        *_render_markdown_table(
            ["Gate", "Mode", "Passed", "Strict Blocked", "Notes"],
            [
                {
                    "Gate": row.get("gate", ""),
                    "Mode": row.get("mode", ""),
                    "Passed": row.get("passed", ""),
                    "Strict Blocked": row.get("strict_blocked", ""),
                    "Notes": row.get("notes", ""),
                }
                for row in list(fingerprint_tables.get("gate_table", []) or [])
            ],
        ),
        "",
        "### VOP Controller Overlay",
        "",
        *_render_markdown_table(
            ["Feature", "Value", "Notes"],
            [
                {
                    "Feature": row.get("feature", ""),
                    "Value": row.get("value", ""),
                    "Notes": row.get("notes", ""),
                }
                for row in list(fingerprint_tables.get("vop_table", []) or [])
            ],
        ),
        "",
        "## 原始任务说明（完整）",
        "",
        *_render_blockquote(list(goal_summary.get("requirement_text_lines", []) or [])),
        "",
        "## 优化目标与约束",
        "",
        *_render_markdown_table(
            ["字段", "内容"],
            [
                {"字段": "目标摘要", "内容": goal_summary.get("requirement_text_brief", "") or "n/a"},
                {"字段": "关注约束族", "内容": goal_summary.get("constraint_focus", []) or ["n/a"]},
            ],
        ),
        "",
        "### 硬约束表",
        "",
        *_render_markdown_table(
            ["约束项", "关系", "目标值", "规范形式"],
            list(goal_summary.get("hard_constraint_rows", []) or []),
        ),
        "",
        "### 目标方向表",
        "",
        *_render_markdown_table(
            ["目标项", "方向"],
            list(goal_summary.get("objective_rows", []) or []),
        ),
        "",
        "## 初始状态",
        "",
        *_render_markdown_table(
            ["字段", "内容"],
            [
                {
                    "字段": "Bootstrap round",
                    "内容": f"V{int(initial_state_summary.get('bootstrap_round_index', 0) or 0)} / {str(initial_state_summary.get('bootstrap_stage', '') or 'bootstrap')}",
                },
                {"字段": "初始是否已可行", "内容": bool(initial_state_summary.get("is_feasible", False))},
                {"字段": "初始诊断", "内容": str(initial_state_summary.get("diagnosis_status", "") or "n/a")},
                {"字段": "主导问题", "内容": _truncate_text(initial_state_summary.get("dominant_problem", ""), 220) or "n/a"},
                {"字段": "初始审计状态", "内容": str(initial_state_summary.get("initial_audit_status", "") or "n/a")},
            ],
        ),
        "",
        "### 初始关键指标",
        "",
        *_render_markdown_table(
            ["指标", "数值"],
            _format_key_metric_rows(dict(initial_state_summary.get("key_metrics", {}) or {})),
        ),
        "",
        "## LLM 决策流程",
        "",
    ]

    if decision_flow:
        for item in decision_flow:
            what_changed = dict(item.get("what_changed", {}) or {})
            lines.extend(
                [
                    f"### V{int(item.get('round_index', 0) or 0)} / {str(item.get('stage', '') or 'round')}",
                    "",
                    *_render_markdown_table(
                        ["字段", "内容"],
                        [
                            {"字段": "Policy ID", "内容": str(item.get("policy_id", "") or "n/a")},
                            {"字段": "算子程序", "内容": str(item.get("selected_operator_program_id", "") or "n/a")},
                            {"字段": "决策原因", "内容": _truncate_text(item.get("decision_rationale", ""), 260) or "n/a"},
                            {"字段": "Replan 原因", "内容": _truncate_text(item.get("replan_reason", ""), 200) or "n/a"},
                            {"字段": "效果判断", "内容": str(item.get("effectiveness_verdict", "") or "n/a")},
                        ],
                    ),
                    "",
                    "**改了什么**",
                    "",
                    *_render_markdown_table(["字段", "内容"], _flatten_mapping_rows(what_changed)),
                    "",
                    "**预期效果**",
                    "",
                    *_render_markdown_table(
                        ["字段", "内容"],
                        _flatten_mapping_rows(item.get("expected_effects", {})),
                    ),
                    "",
                    "**观察效果**",
                    "",
                    *_render_markdown_table(
                        ["字段", "内容"],
                        _flatten_mapping_rows(item.get("observed_effects", {})),
                    ),
                    "",
                ]
            )
    else:
        lines.extend(["| 字段 | 内容 |", "| --- | --- |", "| n/a | n/a |", ""])

    lines.extend(
        [
            "## 关键变更与算子调用",
            "",
            *_render_markdown_table(
                ["字段", "内容"],
                [
                    {
                        "字段": "主算子程序",
                        "内容": str(operator_summary.get("selected_operator_program_id", "") or "n/a"),
                    },
                    {
                        "字段": "算子动作",
                        "内容": list(operator_summary.get("operator_actions", []) or []) or ["n/a"],
                    },
                ],
            ),
            "",
            "### 各轮选中程序",
            "",
            *_render_markdown_table(
                ["Round", "Stage", "Policy", "Program", "Actions"],
                [
                    {
                        "Round": f"V{int(item.get('round_index', 0) or 0)}",
                        "Stage": item.get("stage", ""),
                        "Policy": item.get("policy_id", ""),
                        "Program": item.get("selected_operator_program_id", ""),
                        "Actions": item.get("operator_actions", []),
                    }
                    for item in list(operator_summary.get("all_selected_programs_by_round", []) or [])
                ],
            ),
            "",
            "## 优化方案与搜索策略",
            "",
            *_render_markdown_table(
                ["字段", "内容"],
                [
                    {"字段": "search_space_override", "内容": optimization_scheme.get("search_space_override", "") or "n/a"},
                    {"字段": "runtime_overrides", "内容": optimization_scheme.get("runtime_overrides", {})},
                    {"字段": "fidelity_plan", "内容": optimization_scheme.get("fidelity_plan", {})},
                    {"字段": "delegated_execution_mode", "内容": optimization_scheme.get("delegated_execution_mode", "") or "n/a"},
                ],
            ),
            "",
            "## Delegated Mass 执行结果",
            "",
            *_render_markdown_table(
                ["字段", "内容"],
                [
                    {"字段": "诊断状态", "内容": delegated_mass_result.get("diagnosis_status", "") or "n/a"},
                    {"字段": "诊断原因", "内容": delegated_mass_result.get("diagnosis_reason", "") or "n/a"},
                    {"字段": "搜索空间效果", "内容": delegated_mass_result.get("search_space_effect", "") or "n/a"},
                    {"字段": "First feasible eval", "内容": delegated_mass_result.get("first_feasible_eval", "n/a")},
                    {"字段": "COMSOL calls to first feasible", "内容": delegated_mass_result.get("comsol_calls_to_first_feasible", "n/a")},
                    {"字段": "最终审计状态", "内容": delegated_mass_result.get("final_audit_status", "") or "n/a"},
                    {"字段": "最终效果判断", "内容": delegated_mass_result.get("effectiveness_verdict", "") or "n/a"},
                ],
            ),
            "",
            "## 严格门禁与审计结果",
            "",
            *_render_markdown_table(
                ["Gate", "Mode", "Passed", "Strict Blocked", "Notes"],
                [
                    {
                        "Gate": row.get("gate", ""),
                        "Mode": row.get("mode", ""),
                        "Passed": row.get("passed", ""),
                        "Strict Blocked": row.get("strict_blocked", ""),
                        "Notes": row.get("notes", ""),
                    }
                    for row in list(fingerprint_tables.get("gate_table", []) or [])
                ],
            ),
            "",
            "## 最终结果与结论",
            "",
            *_render_markdown_table(
                ["字段", "内容"],
                [
                    {"字段": "是否可行", "内容": delegated_mass_result.get("diagnosis_status", "") == "feasible"},
                    {"字段": "审计状态", "内容": delegated_mass_result.get("final_audit_status", "") or "n/a"},
                    {"字段": "最终效果判断", "内容": final_result_summary.get("effectiveness_verdict", "") or "n/a"},
                    {"字段": "主流程状态", "内容": final_result_summary.get("status", "") or "n/a"},
                    {"字段": "最终迭代", "内容": int(final_result_summary.get("final_iteration", 0) or 0)},
                    {"字段": "最终 MPH", "内容": final_result_summary.get("final_mph_path", "") or "n/a"},
                    {"字段": "一句话结论", "内容": _build_conclusion_sentence(delegated_mass_result, final_result_summary)},
                ],
            ),
            "",
            "### 最终关键指标",
            "",
            *_render_markdown_table(
                ["指标", "数值"],
                _format_key_metric_rows(dict(final_result_summary.get("key_metrics", {}) or {})),
            ),
            "",
            "## 关键证据摘录",
            "",
        ]
    )

    if evidence_quotes:
        quote_rows: List[Dict[str, Any]] = []
        grouped_counts: Dict[str, int] = {}
        for item in evidence_quotes:
            stage = str(item.get("stage", "") or "round")
            count = grouped_counts.get(stage, 0)
            if count >= 2:
                continue
            grouped_counts[stage] = count + 1
            quote_rows.append(
                {
                    "Stage": stage,
                    "Speaker": str(item.get("speaker", "") or "LLM"),
                    "Quote": f"“{_truncate_text(item.get('quote_text', ''), MAX_QUOTE_LENGTH)}”",
                    "Source": str(item.get("source", "") or "n/a"),
                }
            )
        lines.extend(_render_markdown_table(["Stage", "Speaker", "Quote", "Source"], quote_rows))
    else:
        lines.extend(["| Stage | Speaker | Quote | Source |", "| --- | --- | --- | --- |", "| n/a | n/a | n/a | n/a |"])

    lines.extend(["", "## 产物索引", ""])
    if source_artifacts:
        lines.extend(_render_markdown_table(["路径"], [{"路径": item} for item in source_artifacts]))
    else:
        lines.extend(["| 路径 |", "| --- |", "| n/a |"])

    if llm_narrative_summary:
        lines.extend(["", "## LLM 叙事版总结", "", llm_narrative_summary])

    return "\n".join(lines).rstrip() + "\n"


def _is_placeholder_api_key(api_key: str) -> bool:
    normalized = str(api_key or "").strip().lower()
    if not normalized:
        return True
    return any(token in normalized for token in ("dummy", "unit_test", "placeholder", "changeme"))


def _resolve_llm_profile_metadata(llm_gateway: Any, llm_profile_name: str) -> Dict[str, str]:
    if llm_gateway is None:
        return {"attempt_enabled": "false", "model": "", "profile": ""}
    try:
        profile = llm_gateway.resolve_text_profile(llm_profile_name)
    except Exception:
        return {"attempt_enabled": "false", "model": "", "profile": ""}
    api_key = str(getattr(profile, "api_key", "") or "")
    return {
        "attempt_enabled": "true" if not _is_placeholder_api_key(api_key) else "false",
        "model": str(getattr(profile, "model", "") or ""),
        "profile": str(getattr(profile, "name", "") or ""),
    }


def _strip_code_fence(text: str) -> str:
    normalized = str(text or "").strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", normalized)
        normalized = re.sub(r"\s*```$", "", normalized)
    return normalized.strip()


def _build_llm_messages(digest: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是卫星布局仿真优化复盘助手。请基于提供的 digest 生成简体中文 Markdown 叙事总结。"
                "必须忠于事实，不要编造；未知就明确写未知；不要输出代码块，不要重复标题。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请把这次 vop_maas 优化过程写成一段结构清晰的中文叙事，总结以下维度："
                "目标、初始状态、关键决策、改了什么、为什么改、预期效果、观察效果、最终结果、后续建议。"
                "控制在 6-10 条 Markdown 要点 + 1 段结论性文字。\n\n"
                f"digest:\n{json.dumps(_to_jsonable(digest), ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def _merge_summary_fields(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    current = _read_json(path)
    current.update(_to_jsonable(payload))
    _write_json(path, current)
    return current


def generate_vop_final_summary_zh(
    run_dir: str,
    *,
    llm_gateway: Any = None,
    llm_profile_name: str = "",
    log_llm_interaction: Optional[Callable[..., Any]] = None,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    manifest_path = run_path / "events" / "run_manifest.json"
    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path)
    run_mode = str(summary.get("run_mode", "") or manifest.get("run_mode", "") or "").strip()
    if run_mode != "vop_maas":
        return {"generated": False, "reason": f"unsupported_run_mode:{run_mode or 'unknown'}"}

    digest = build_vop_final_summary_digest(str(run_path), runtime_config=runtime_config)
    digest_path = run_path / DIGEST_REL_PATH
    markdown_path = run_path / SUMMARY_MD_FILENAME
    _write_json(digest_path, digest)
    markdown_path.write_text(render_vop_final_summary_markdown(digest), encoding="utf-8")

    profile_metadata = _resolve_llm_profile_metadata(llm_gateway, llm_profile_name)
    status = "template_only"
    model_name = ""
    error_message = ""
    narrative_text = ""

    if profile_metadata.get("attempt_enabled") == "true" and llm_gateway is not None:
        request_payload = {
            "mode": "vop_maas",
            "language": SUMMARY_LANGUAGE,
            "role": "vop_final_summary_zh",
            "schema_version": DIGEST_SCHEMA_VERSION,
            "messages": _build_llm_messages(digest),
        }
        try:
            if callable(log_llm_interaction):
                log_llm_interaction(
                    iteration=int(summary.get("final_iteration", 1) or 1),
                    role="vop_final_summary_zh",
                    request=request_payload,
                    response=None,
                    mode="vop_maas",
                )
            result = llm_gateway.generate_text(
                request_payload["messages"],
                profile_name=llm_profile_name,
                expects_json=False,
                temperature=0.2,
                max_tokens=900,
            )
            response_payload = {
                "content": str(getattr(result, "content", "") or ""),
                "profile": str(getattr(result, "profile", "") or ""),
                "provider": str(getattr(result, "provider", "") or ""),
                "model": str(getattr(result, "model", "") or ""),
                "api_style": str(getattr(result, "api_style", "") or ""),
                "status_code": int(getattr(result, "status_code", 0) or 0),
            }
            if callable(log_llm_interaction):
                log_llm_interaction(
                    iteration=int(summary.get("final_iteration", 1) or 1),
                    role="vop_final_summary_zh",
                    request=None,
                    response=response_payload,
                    mode="vop_maas",
                )
            narrative_text = _strip_code_fence(str(getattr(result, "content", "") or ""))
            if narrative_text:
                digest_with_narrative = dict(digest)
                digest_with_narrative["llm_narrative_summary"] = narrative_text
                markdown_path.write_text(
                    render_vop_final_summary_markdown(digest_with_narrative),
                    encoding="utf-8",
                )
                status = "llm_enriched"
                model_name = str(
                    getattr(result, "model", "") or profile_metadata.get("model", "") or ""
                )
        except Exception as exc:
            status = "llm_failed"
            error_message = _truncate_text(str(exc), 240)

    summary_fields = {
        "llm_final_summary_zh_path": serialize_run_path(str(run_path), str(markdown_path)),
        "llm_final_summary_digest_path": serialize_run_path(str(run_path), str(digest_path)),
        "runtime_feature_fingerprint_path": fingerprint_rel_path(str(run_path)),
        "llm_final_summary_status": status,
        "llm_final_summary_language": SUMMARY_LANGUAGE,
        "llm_final_summary_model": model_name,
    }
    if error_message:
        summary_fields["llm_final_summary_error"] = error_message

    updated_summary = _merge_summary_fields(summary_path, summary_fields)
    existing_extra = dict(manifest.get("extra", {}) or {})
    existing_extra.update(summary_fields)
    manifest_payload = dict(summary_fields)
    manifest_payload["extra"] = existing_extra
    updated_manifest = _merge_summary_fields(manifest_path, manifest_payload)

    return {
        "generated": True,
        "digest": digest,
        "markdown_path": serialize_run_path(str(run_path), str(markdown_path)),
        "digest_path": serialize_run_path(str(run_path), str(digest_path)),
        "status": status,
        "model": model_name,
        "error": error_message,
        "summary_fields": summary_fields,
        "summary": updated_summary,
        "manifest": updated_manifest,
        "narrative_generated": bool(narrative_text),
    }
