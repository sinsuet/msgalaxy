"""
Deterministic Chinese final summary for traditional MASS optimization runs.
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.path_policy import serialize_run_path


SUMMARY_MD_FILENAME = "mass_final_summary_zh.md"
DIGEST_REL_PATH = Path("events") / "mass_final_summary_digest.json"
DIGEST_SCHEMA_VERSION = 1
SUMMARY_LANGUAGE = "zh-CN"
KNOWN_METRIC_ORDER = [
    "max_temp",
    "min_clearance",
    "cg_offset",
    "mission_keepout_violation",
    "power_margin",
    "voltage_drop",
    "safety_factor",
    "first_modal_freq",
    "peak_power",
    "num_collisions",
    "boundary_violation",
]
FEASIBLE_STATUSES = {"feasible", "success", "passed", "ok", "final_state_recheck_feasible"}


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


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
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
        if math.isnan(value) or math.isinf(value):
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


def _truncate_text(value: Any, limit: int = 200) -> str:
    text = _collapse_text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return _to_jsonable(value)
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return {}
    try:
        return _to_jsonable(json.loads(text))
    except Exception:
        return text


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        parsed = float(value)
    except Exception:
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return float(parsed)


def _safe_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _format_number(value: Any, digits: int = 4) -> str:
    numeric = _safe_float(value, None)
    if numeric is None:
        return "n/a"
    if abs(numeric) >= 1000:
        return f"{numeric:.1f}"
    if float(int(numeric)) == numeric:
        return str(int(numeric))
    return f"{numeric:.{digits}f}".rstrip("0").rstrip(".")


def _format_bool(value: Any) -> str:
    parsed = _safe_bool(value, None)
    if parsed is None:
        return "n/a"
    return "是" if parsed else "否"


def _format_scalar(value: Any) -> str:
    if value in (None, ""):
        return "n/a"
    if isinstance(value, bool):
        return _format_bool(value)
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, list):
        items = [_collapse_text(item) for item in value if _collapse_text(item)]
        return ", ".join(items) if items else "n/a"
    if isinstance(value, dict):
        parts = [
            f"{str(key)}={_format_scalar(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        ]
        return "; ".join(parts) if parts else "n/a"
    return _collapse_text(value) or "n/a"


def _display_algorithm(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "nsga2": "NSGA-II",
        "nsgaii": "NSGA-II",
        "nsga3": "NSGA-III",
        "nsgaiii": "NSGA-III",
        "moead": "MOEA/D",
        "moea/d": "MOEA/D",
    }
    compact = re.sub(r"[^a-z0-9/]+", "", normalized)
    mapped = aliases.get(compact, "")
    return mapped or (str(value or "").strip() or "n/a")


def _serialize_rel(run_path: Path, path: Path) -> str:
    return serialize_run_path(str(run_path), str(path))


def _merge_summary_fields(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    current = _read_json(path)
    current.update(_to_jsonable(payload))
    _write_json(path, current)
    return current


def _coalesce_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _lookup(containers: Iterable[Dict[str, Any]], *keys: str) -> Any:
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            if key not in container:
                continue
            value = container.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            return value
    return None


def _derive_intent_mode(summary: Dict[str, Any], manifest: Dict[str, Any]) -> str:
    source = str(
        _coalesce_value(
            summary.get("modeling_intent_source"),
            dict(manifest.get("extra", {}) or {}).get("modeling_intent_source"),
        )
        or ""
    ).strip().lower()
    if "deterministic" in source:
        return "deterministic_intent"
    if source in {"api", "llm", "llm_api", "llm_api_autofill"}:
        return "llm_intent"
    if source:
        return "fallback_intent"
    return "unknown"


def _derive_genome_representation(search_space_mode: Any) -> str:
    mode = str(search_space_mode or "").strip().lower()
    if mode == "operator_program":
        return "operator_program_genome"
    if mode == "hybrid":
        return "hybrid_genome"
    return "coordinate_vector"


def _derive_release_grade_verdict(final_audit_status: Any) -> str:
    status = str(final_audit_status or "").strip().lower()
    if status == "release_grade_real_comsol_validated":
        return "已达到 release-grade 审计门槛"
    if not status:
        return "未形成明确 release audit 结论"
    if status.startswith("diagnostic_only"):
        return "仅形成 diagnostic 级结论，不能宣称 release-grade"
    return f"当前审计状态为 {status}"


def _compute_gap(final_value: Any, relation: Any, target_value: Any) -> Optional[float]:
    metric = _safe_float(final_value, None)
    target = _safe_float(target_value, None)
    relation_text = str(relation or "").strip()
    if metric is None or target is None or not relation_text:
        return None
    if relation_text == "<=":
        return metric - target
    if relation_text == ">=":
        return target - metric
    if relation_text == "==":
        return abs(metric - target)
    return None


def _normalize_problem_rows(rows: Iterable[Any], *, kind: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in list(rows or []):
        payload = dict(item or {}) if isinstance(item, dict) else {}
        if not payload:
            continue
        if kind == "constraint":
            normalized.append(
                {
                    "name": str(payload.get("name", "") or payload.get("constraint_name", "") or ""),
                    "metric_key": str(payload.get("metric_key", "") or ""),
                    "relation": str(
                        payload.get("relation", "")
                        or payload.get("original_relation", "")
                        or payload.get("normalized_relation", "")
                        or ""
                    ),
                    "target_value": payload.get("target_value", payload.get("threshold")),
                    "category": str(payload.get("category", "") or ""),
                    "normalized_g_leq_0": str(payload.get("normalized_g_leq_0", "") or ""),
                }
            )
        else:
            normalized.append(
                {
                    "name": str(payload.get("name", "") or payload.get("objective_name", "") or ""),
                    "metric_key": str(payload.get("metric_key", "") or ""),
                    "direction": str(payload.get("direction", "") or ""),
                    "weight": payload.get("weight"),
                    "normalized_text": str(payload.get("normalized_text", "") or ""),
                }
            )
    return normalized


def _extract_problem_definition(
    summary: Dict[str, Any],
    manifest: Dict[str, Any],
    attempts: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    manifest_extra = dict(manifest.get("extra", {}) or {})
    containers = [summary, manifest_extra, manifest]
    requirement_text_full = str(
        _lookup(
            containers,
            "requirement_text_full",
            "requirement_text",
            "requirements_text",
            "problem_statement",
        )
        or ""
    ).strip()
    requirement_text_brief = str(
        _lookup(containers, "requirement_text_brief", "requirement_brief") or ""
    ).strip()
    if not requirement_text_brief:
        requirement_text_brief = _truncate_text(requirement_text_full, 120)
    formulation_report = dict(summary.get("formulation_report", {}) or {})
    manifest_formulation = dict(manifest_extra.get("formulation_report", {}) or {})
    hard_constraint_rows = _normalize_problem_rows(
        _coalesce_value(
            summary.get("hard_constraint_rows"),
            formulation_report.get("normalized_hard_constraints"),
            manifest_extra.get("hard_constraint_rows"),
            manifest_formulation.get("normalized_hard_constraints"),
            summary.get("normalized_hard_constraints"),
        )
        or [],
        kind="constraint",
    )
    objective_rows = _normalize_problem_rows(
        _coalesce_value(
            summary.get("objective_rows"),
            formulation_report.get("normalized_objectives"),
            manifest_extra.get("objective_rows"),
            manifest_formulation.get("normalized_objectives"),
            summary.get("normalized_objectives"),
        )
        or [],
        kind="objective",
    )
    component_count = _safe_int(
        _coalesce_value(
            summary.get("component_count"),
            manifest_extra.get("component_count"),
            len(summary.get("component_ids", []) or []),
            len(summary.get("best_component_ids", []) or []),
        ),
        0,
    ) or 0
    if component_count <= 0:
        latest_candidate = dict(candidates[-1] or {}) if candidates else {}
        component_count = _safe_int(latest_candidate.get("component_count"), 0) or 0
    if component_count <= 0 and attempts:
        component_count = len(
            {
                str(key)[len("best_candidate_") :]
                for row in attempts
                for key in dict(row.get("raw", {}) or {}).keys()
                if str(key).startswith("best_candidate_component_")
            }
        )
    search_space_mode = str(
        _coalesce_value(
            summary.get("search_space"),
            summary.get("search_space_mode"),
            manifest_extra.get("search_space_mode"),
            manifest.get("search_space_mode"),
        )
        or ""
    ).strip()
    decision_variable_summary = {
        "parsed_variables": _safe_int(
            _coalesce_value(
                summary.get("llm_effective_parsed_variables"),
                dict(summary.get("compile_report", {}) or {}).get("parsed_variables"),
                manifest_extra.get("llm_effective_parsed_variables"),
            ),
            0,
        )
        or 0,
        "search_space_mode": search_space_mode or "coordinate",
        "supported_variable_types": ["continuous", "integer", "binary"],
    }
    return {
        "requirement_text_brief": requirement_text_brief or "n/a",
        "requirement_text_full": requirement_text_full or requirement_text_brief or "n/a",
        "hard_constraint_rows": hard_constraint_rows,
        "objective_rows": objective_rows,
        "component_count": int(component_count),
        "decision_variable_summary": decision_variable_summary,
    }


def _normalize_attempt_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        payload = dict(row or {})
        notes = " / ".join(
            item
            for item in [
                _collapse_text(payload.get("diagnosis_reason")),
                _collapse_text(payload.get("solver_message")),
                _collapse_text(payload.get("branch_action")),
            ]
            if item
        )
        normalized.append(
            {
                "attempt": _safe_int(payload.get("attempt"), 0) or 0,
                "diagnosis_status": str(payload.get("diagnosis_status", "") or ""),
                "diagnosis_reason": str(payload.get("diagnosis_reason", "") or ""),
                "best_cv": _safe_float(payload.get("best_cv"), None),
                "is_best_attempt": bool(_safe_bool(payload.get("is_best_attempt"), False)),
                "dominant_violation": str(payload.get("dominant_violation", "") or ""),
                "operator_program_id": str(payload.get("operator_program_id", "") or ""),
                "operator_actions_brief": _format_scalar(
                    _parse_jsonish(payload.get("operator_actions"))
                ),
                "notes": notes or "n/a",
                "raw": payload,
            }
        )
    normalized.sort(key=lambda item: int(item.get("attempt", 0) or 0))
    if normalized and not any(bool(item.get("is_best_attempt", False)) for item in normalized):
        candidate = min(
            normalized,
            key=lambda item: (
                float("inf") if item.get("best_cv") is None else float(item["best_cv"]),
                int(item.get("attempt", 0) or 0),
            ),
        )
        candidate["is_best_attempt"] = True
    return normalized


def _normalize_generation_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        payload = dict(row or {})
        normalized.append(
            {
                "attempt": _safe_int(payload.get("attempt"), 0) or 0,
                "generation": _safe_int(payload.get("generation"), 0) or 0,
                "population_size": _safe_int(payload.get("population_size"), 0) or 0,
                "feasible_count": _safe_int(payload.get("feasible_count"), 0) or 0,
                "feasible_ratio": _safe_float(payload.get("feasible_ratio"), None),
                "best_cv": _safe_float(payload.get("best_cv"), None),
                "mean_cv": _safe_float(payload.get("mean_cv"), None),
                "best_feasible_sum_f": _safe_float(payload.get("best_feasible_sum_f"), None),
                "raw": payload,
            }
        )
    normalized.sort(key=lambda item: (item["attempt"], item["generation"]))
    return normalized


def _best_attempt_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    tagged = [row for row in rows if bool(row.get("is_best_attempt", False))]
    if tagged:
        return dict(tagged[-1] or {})
    return dict(
        min(
            rows,
            key=lambda item: (
                float("inf") if item.get("best_cv") is None else float(item["best_cv"]),
                int(item.get("attempt", 0) or 0),
            ),
        )
        or {}
    )


def _best_generation_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    with_cv = [row for row in rows if row.get("best_cv") is not None]
    if with_cv:
        return dict(
            min(
                with_cv,
                key=lambda item: (
                    float(item.get("best_cv", 0.0)),
                    -float(item.get("feasible_ratio", 0.0) or 0.0),
                    int(item.get("attempt", 0) or 0),
                    int(item.get("generation", 0) or 0),
                ),
            )
            or {}
        )
    return dict(
        max(
            rows,
            key=lambda item: (
                float(item.get("feasible_ratio", 0.0) or 0.0),
                int(item.get("generation", 0) or 0),
            ),
        )
        or {}
    )


def _sample_generation_rows(rows: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    if len(rows) <= limit:
        return [dict(item or {}) for item in rows]
    head = rows[: limit // 2]
    tail = rows[-(limit - len(head)) :]
    return [dict(item or {}) for item in head + tail]


def _metric_from_attempt_row(row: Dict[str, Any], metric_key: str) -> Any:
    raw = dict(row.get("raw", {}) or {})
    candidates = [
        raw.get(f"metric_{metric_key}"),
        raw.get(f"best_candidate_{metric_key}"),
    ]
    parsed_metrics = _parse_jsonish(raw.get("best_candidate_metrics"))
    if isinstance(parsed_metrics, dict):
        candidates.append(parsed_metrics.get(metric_key))
    candidates.append(row.get(metric_key))
    return _coalesce_value(*candidates)


def _extract_final_metrics(summary: Dict[str, Any], best_attempt: Dict[str, Any]) -> Dict[str, Any]:
    metrics = dict(summary.get("best_candidate_metrics", {}) or {})
    if metrics:
        return {str(key): _to_jsonable(value) for key, value in metrics.items()}
    raw = dict(best_attempt.get("raw", {}) or {})
    parsed_metrics = _parse_jsonish(raw.get("best_candidate_metrics"))
    if isinstance(parsed_metrics, dict):
        return {str(key): _to_jsonable(value) for key, value in parsed_metrics.items()}
    out: Dict[str, Any] = {}
    for key in KNOWN_METRIC_ORDER:
        value = _metric_from_attempt_row(best_attempt, key)
        if value not in (None, ""):
            out[key] = value
    return out


def _ordered_metric_keys(
    initial_metrics: Dict[str, Any],
    best_metrics: Dict[str, Any],
    final_metrics: Dict[str, Any],
) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for key in KNOWN_METRIC_ORDER:
        if key in initial_metrics or key in best_metrics or key in final_metrics:
            seen.add(key)
            ordered.append(key)
    for mapping in (initial_metrics, best_metrics, final_metrics):
        for key in mapping.keys():
            key_text = str(key)
            if key_text in seen:
                continue
            seen.add(key_text)
            ordered.append(key_text)
    return ordered


def _build_final_layout_summary(
    summary: Dict[str, Any],
    layout_rows: List[Dict[str, Any]],
    visualization_summary_text: str,
) -> str:
    if layout_rows:
        latest = dict(layout_rows[-1] or {})
        parts = [
            f"attempt={_format_scalar(latest.get('attempt'))}",
            f"sequence={_format_scalar(latest.get('sequence'))}",
        ]
        snapshot_path = str(latest.get("snapshot_path", "") or "").strip()
        if snapshot_path:
            parts.append(f"snapshot={snapshot_path}")
        return ", ".join(parts)
    if visualization_summary_text:
        for line in visualization_summary_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- Best attempt:"):
                return stripped.removeprefix("- ").strip()
    component_count = _safe_int(summary.get("component_count"), 0) or 0
    search_space = str(summary.get("search_space", "") or "")
    if component_count or search_space:
        return f"component_count={component_count or 'n/a'}, search_space={search_space or 'n/a'}"
    return "n/a"


def _build_conclusion(
    *,
    diagnosis_status: Any,
    diagnosis_reason: Any,
    final_audit_status: Any,
) -> str:
    diagnosis = str(diagnosis_status or "").strip().lower()
    reason = _collapse_text(diagnosis_reason)
    audit = str(final_audit_status or "").strip()
    if diagnosis in FEASIBLE_STATUSES:
        if audit == "release_grade_real_comsol_validated":
            return "本次 MASS 运行已收敛到可行解，且 release audit 达到 release-grade。"
        if audit:
            return f"本次 MASS 运行已得到可行解，但最终 audit 状态仍为 {audit}。"
        return "本次 MASS 运行已得到可行解，建议继续复核最终审计与布局导出产物。"
    if reason:
        return f"本次 MASS 运行尚未形成可行解，主因是：{reason}。"
    if audit:
        return f"本次 MASS 运行尚未形成 release-grade 结果，当前 audit 状态为 {audit}。"
    return "本次 MASS 运行已完成，但仍需结合诊断与审计结果继续复核。"


def build_mass_final_summary_digest(run_dir: str) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    manifest_path = run_path / "events" / "run_manifest.json"
    attempts_path = run_path / "tables" / "attempts.csv"
    generations_path = run_path / "tables" / "generations.csv"
    release_audit_path = run_path / "tables" / "release_audit.csv"
    candidates_path = run_path / "tables" / "candidates.csv"
    layout_timeline_path = run_path / "tables" / "layout_timeline.csv"
    visualization_summary_path = run_path / "visualizations" / "visualization_summary.txt"
    artifact_index_path = run_path / "events" / "artifact_index.json"
    report_path = run_path / "report.md"

    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path)
    manifest_extra = dict(manifest.get("extra", {}) or {})
    attempts = _normalize_attempt_rows(_load_csv_rows(attempts_path))
    generations = _normalize_generation_rows(_load_csv_rows(generations_path))
    release_audit_rows = _load_csv_rows(release_audit_path)
    release_audit_row = dict(release_audit_rows[-1] or {}) if release_audit_rows else {}
    candidates = _load_csv_rows(candidates_path)
    layout_rows = _load_csv_rows(layout_timeline_path)
    visualization_summary_text = _read_text(visualization_summary_path)
    artifact_index = _read_json(artifact_index_path)

    run_mode = str(
        _coalesce_value(
            summary.get("run_mode"),
            manifest.get("run_mode"),
            summary.get("optimization_mode"),
        )
        or ""
    ).strip()
    execution_mode = str(
        _coalesce_value(summary.get("execution_mode"), manifest.get("execution_mode")) or ""
    ).strip()
    algorithm_raw = _coalesce_value(
        summary.get("pymoo_algorithm"),
        summary.get("run_algorithm"),
        manifest.get("pymoo_algorithm"),
        manifest.get("run_algorithm"),
    )
    best_attempt = _best_attempt_row(attempts)
    best_generation = _best_generation_row(generations)
    final_metrics = _extract_final_metrics(summary, best_attempt)
    initial_metrics = {
        key: _metric_from_attempt_row(attempts[0], key)
        for key in KNOWN_METRIC_ORDER
        if attempts and _metric_from_attempt_row(attempts[0], key) not in (None, "")
    }
    best_metrics = {
        key: _metric_from_attempt_row(best_attempt, key)
        for key in KNOWN_METRIC_ORDER
        if best_attempt and _metric_from_attempt_row(best_attempt, key) not in (None, "")
    }
    metric_keys = _ordered_metric_keys(initial_metrics, best_metrics, final_metrics)

    problem_definition = _extract_problem_definition(summary, manifest, attempts, candidates)
    search_space_mode = str(
        _coalesce_value(
            summary.get("search_space"),
            summary.get("search_space_mode"),
            manifest_extra.get("search_space_mode"),
            manifest.get("search_space_mode"),
            problem_definition.get("decision_variable_summary", {}).get("search_space_mode"),
        )
        or "coordinate"
    ).strip()
    population_size = next(
        (
            int(item.get("population_size", 0) or 0)
            for item in generations
            if int(item.get("population_size", 0) or 0) > 0
        ),
        0,
    )
    generation_ids = [int(item.get("generation", 0) or 0) for item in generations]
    unique_generations = sorted(set(generation_ids))
    if unique_generations:
        if unique_generations[0] == 0:
            n_generations = int(unique_generations[-1] + 1)
        else:
            n_generations = int(len(unique_generations))
    else:
        n_generations = 0
    attempt_count = int(len(attempts))
    best_cv_min = _coalesce_value(
        summary.get("best_cv_min"),
        best_attempt.get("best_cv"),
        best_generation.get("best_cv"),
    )
    diagnosis_status = _coalesce_value(
        summary.get("diagnosis_status"),
        release_audit_row.get("diagnosis_status"),
        best_attempt.get("diagnosis_status"),
    )
    diagnosis_reason = _coalesce_value(
        summary.get("diagnosis_reason"),
        release_audit_row.get("diagnosis_reason"),
        best_attempt.get("diagnosis_reason"),
    )
    final_audit_status = _coalesce_value(
        release_audit_row.get("final_audit_status"),
        summary.get("final_audit_status"),
        manifest_extra.get("final_audit_status"),
    )

    key_metric_rows = [
        {
            "metric_key": key,
            "initial_value": initial_metrics.get(key),
            "best_value": best_metrics.get(key),
            "final_value": final_metrics.get(key),
            "notes": "最终最优候选指标"
            if key in final_metrics
            else ("attempt 主线指标" if key in best_metrics else "initial snapshot"),
        }
        for key in metric_keys
    ]

    hard_constraint_rows = list(problem_definition.get("hard_constraint_rows", []) or [])
    if hard_constraint_rows:
        constraint_progress_rows = [
            {
                "metric_key": str(item.get("metric_key", "") or ""),
                "relation": str(item.get("relation", "") or ""),
                "target_value": item.get("target_value"),
                "final_value": final_metrics.get(str(item.get("metric_key", "") or "")),
                "final_gap": _compute_gap(
                    final_metrics.get(str(item.get("metric_key", "") or "")),
                    item.get("relation"),
                    item.get("target_value"),
                ),
                "status": (
                    "满足"
                    if (_compute_gap(
                        final_metrics.get(str(item.get("metric_key", "") or "")),
                        item.get("relation"),
                        item.get("target_value"),
                    ) or 0.0)
                    <= 0.0
                    else "未满足"
                )
                if _compute_gap(
                    final_metrics.get(str(item.get("metric_key", "") or "")),
                    item.get("relation"),
                    item.get("target_value"),
                )
                is not None
                else "未知",
            }
            for item in hard_constraint_rows
        ]
    else:
        violation_breakdown = dict(summary.get("constraint_violation_breakdown", {}) or {})
        constraint_progress_rows = [
            {
                "metric_key": str(key),
                "relation": "g(x) <= 0",
                "target_value": 0.0,
                "final_value": value,
                "final_gap": value,
                "status": "满足" if (_safe_float(value, 0.0) or 0.0) <= 0.0 else "未满足",
            }
            for key, value in violation_breakdown.items()
        ]

    objective_progress_rows = [
        {
            "metric_key": str(item.get("metric_key", "") or ""),
            "direction": str(item.get("direction", "") or ""),
            "weight": item.get("weight"),
            "best_value": best_metrics.get(str(item.get("metric_key", "") or "")),
            "final_value": final_metrics.get(str(item.get("metric_key", "") or "")),
        }
        for item in list(problem_definition.get("objective_rows", []) or [])
    ]

    runtime_baseline = {
        "entry_stack": str(
            _coalesce_value(
                summary.get("entry_stack"),
                manifest_extra.get("entry_stack"),
                run_mode or "mass",
            )
            or "mass"
        ),
        "run_mode": run_mode or "mass",
        "execution_mode": execution_mode or "mass",
        "intent_mode": _derive_intent_mode(summary, manifest),
        "search_space_mode": search_space_mode or "coordinate",
        "genome_representation": _derive_genome_representation(search_space_mode),
        "simulation_backend": str(
            _coalesce_value(
                summary.get("simulation_backend"),
                release_audit_row.get("simulation_backend"),
                manifest_extra.get("simulation_backend"),
            )
            or "n/a"
        ),
        "thermal_evaluator_mode": str(
            _coalesce_value(
                summary.get("thermal_evaluator_mode"),
                release_audit_row.get("thermal_evaluator_mode"),
                manifest_extra.get("thermal_evaluator_mode"),
            )
            or "n/a"
        ),
        "mcts_enabled": any(
            bool(_safe_bool(dict(item.get("raw", {}) or {}).get("mcts_enabled"), False))
            for item in attempts
        ),
        "meta_policy_enabled": bool(
            (_safe_int(summary.get("meta_policy_runtime_events"), 0) or 0) > 0
            or (_safe_int(summary.get("meta_policy_next_run_actions"), 0) or 0) > 0
        ),
        "physics_audit_enabled": str(final_audit_status or "").strip().lower()
        != "diagnostic_only_audit_disabled",
        "operator_program_enabled": search_space_mode in {"operator_program", "hybrid"},
        "seed_population_enabled": bool(
            (_safe_int(summary.get("seed_population_total_count"), 0) or 0) > 0
            or (_safe_int(summary.get("layout_seed_generated_count"), 0) or 0) > 0
        ),
        "source_gate_mode": str(summary.get("source_gate_mode", "") or "n/a"),
        "operator_family_gate_mode": str(
            summary.get("operator_family_gate_mode", "") or "n/a"
        ),
        "operator_realization_gate_mode": str(
            summary.get("operator_realization_gate_mode", "") or "n/a"
        ),
        "source_gate_real_only": bool(summary.get("source_gate_real_only", False)),
    }

    algorithm_setup = {
        "pymoo_algorithm": _display_algorithm(algorithm_raw),
        "population_size": population_size,
        "n_generations": int(
            _coalesce_value(
                summary.get("n_generations"),
                manifest_extra.get("n_generations"),
                n_generations,
            )
            or 0
        ),
        "termination_summary": (
            f"status={summary.get('status', 'n/a')}, "
            f"final_iteration={_format_scalar(summary.get('final_iteration'))}, "
            f"generation_rows={len(generations)}"
        ),
        "constraint_handling_mode": "g(x) <= 0 / feasibility-first",
        "duplicate_elimination": _display_algorithm(algorithm_raw) in {"NSGA-II", "NSGA-III"},
        "search_budget_summary": (
            f"attempts={attempt_count}, "
            f"generation_rows={len(generations)}, "
            f"population_size={population_size or 'n/a'}"
        ),
    }

    attempt_progress = {
        "attempt_count": attempt_count,
        "best_attempt_index": int(best_attempt.get("attempt", 0) or 0),
        "attempt_rows": [
            {
                "attempt": row.get("attempt"),
                "diagnosis_status": row.get("diagnosis_status"),
                "best_cv": row.get("best_cv"),
                "is_best_attempt": row.get("is_best_attempt"),
                "dominant_violation": row.get("dominant_violation"),
                "operator_actions_brief": row.get("operator_actions_brief"),
                "notes": row.get("notes"),
            }
            for row in attempts
        ],
    }

    generation_progress = {
        "generation_count": int(len(generations)),
        "best_cv_curve": [
            {
                "attempt": row.get("attempt"),
                "generation": row.get("generation"),
                "best_cv": row.get("best_cv"),
            }
            for row in generations
        ],
        "feasible_ratio_curve": [
            {
                "attempt": row.get("attempt"),
                "generation": row.get("generation"),
                "feasible_ratio": row.get("feasible_ratio"),
            }
            for row in generations
        ],
        "best_generation_summary": {
            "attempt": best_generation.get("attempt"),
            "generation": best_generation.get("generation"),
            "best_cv": best_generation.get("best_cv"),
            "feasible_ratio": best_generation.get("feasible_ratio"),
            "feasible_count": best_generation.get("feasible_count"),
            "population_size": best_generation.get("population_size"),
        },
        "generation_rows": _sample_generation_rows(generations),
    }

    feasibility_progress = {
        "first_feasible_eval": _coalesce_value(
            release_audit_row.get("first_feasible_eval"),
            summary.get("first_feasible_eval"),
            manifest_extra.get("first_feasible_eval"),
        ),
        "comsol_calls_to_first_feasible": _coalesce_value(
            release_audit_row.get("comsol_calls_to_first_feasible"),
            summary.get("comsol_calls_to_first_feasible"),
            manifest_extra.get("comsol_calls_to_first_feasible"),
        ),
        "feasible_attempt_count": int(
            sum(
                1
                for row in attempts
                if str(row.get("diagnosis_status", "") or "").strip().lower() in FEASIBLE_STATUSES
            )
        ),
        "final_diagnosis_status": str(diagnosis_status or ""),
        "final_diagnosis_reason": str(diagnosis_reason or ""),
    }

    final_result = {
        "status": str(summary.get("status", "") or "n/a"),
        "final_iteration": summary.get("final_iteration"),
        "best_cv_min": best_cv_min,
        "best_candidate_metrics": final_metrics,
        "final_mph_path": str(
            _coalesce_value(
                summary.get("final_mph_path"),
                release_audit_row.get("final_mph_path"),
            )
            or ""
        ),
        "final_layout_summary": _build_final_layout_summary(
            summary,
            layout_rows,
            visualization_summary_text,
        ),
        "conclusion": _build_conclusion(
            diagnosis_status=diagnosis_status,
            diagnosis_reason=diagnosis_reason,
            final_audit_status=final_audit_status,
        ),
    }

    release_audit_summary = {
        "final_audit_status": str(final_audit_status or ""),
        "simulation_backend": runtime_baseline.get("simulation_backend"),
        "thermal_evaluator_mode": runtime_baseline.get("thermal_evaluator_mode"),
        "source_gate_passed": _coalesce_value(
            release_audit_row.get("source_gate_passed"),
            summary.get("source_gate_passed"),
            manifest_extra.get("source_gate_passed"),
        ),
        "operator_family_gate_passed": _coalesce_value(
            release_audit_row.get("operator_family_gate_passed"),
            summary.get("operator_family_gate_passed"),
            manifest_extra.get("operator_family_gate_passed"),
        ),
        "operator_realization_gate_passed": _coalesce_value(
            release_audit_row.get("operator_realization_gate_passed"),
            summary.get("operator_realization_gate_passed"),
            manifest_extra.get("operator_realization_gate_passed"),
        ),
        "release_grade_verdict": _derive_release_grade_verdict(final_audit_status),
    }

    source_artifacts = [
        {
            "path": _serialize_rel(run_path, summary_path),
            "exists": summary_path.exists(),
            "role": "run_summary",
        },
        {
            "path": _serialize_rel(run_path, manifest_path),
            "exists": manifest_path.exists(),
            "role": "run_manifest",
        },
        {
            "path": _serialize_rel(run_path, attempts_path),
            "exists": attempts_path.exists(),
            "role": "attempt_table",
        },
        {
            "path": _serialize_rel(run_path, generations_path),
            "exists": generations_path.exists(),
            "role": "generation_table",
        },
        {
            "path": _serialize_rel(run_path, release_audit_path),
            "exists": release_audit_path.exists(),
            "role": "release_audit_table",
        },
        {
            "path": _serialize_rel(run_path, candidates_path),
            "exists": candidates_path.exists(),
            "role": "candidate_table",
        },
        {
            "path": _serialize_rel(run_path, layout_timeline_path),
            "exists": layout_timeline_path.exists(),
            "role": "layout_timeline_table",
        },
        {
            "path": _serialize_rel(run_path, report_path),
            "exists": report_path.exists(),
            "role": "markdown_report",
        },
        {
            "path": _serialize_rel(run_path, visualization_summary_path),
            "exists": visualization_summary_path.exists(),
            "role": "visualization_summary",
        },
        {
            "path": _serialize_rel(run_path, artifact_index_path),
            "exists": artifact_index_path.exists(),
            "role": "artifact_index",
        },
    ]

    return {
        "schema_version": DIGEST_SCHEMA_VERSION,
        "run_identity": {
            "run_id": str(_coalesce_value(summary.get("run_id"), manifest.get("run_id")) or ""),
            "run_mode": run_mode or "mass",
            "execution_mode": execution_mode or "mass",
            "timestamp": str(
                _coalesce_value(summary.get("timestamp"), manifest.get("timestamp")) or ""
            ),
            "level": str(
                _coalesce_value(
                    summary.get("level"),
                    manifest.get("level"),
                    summary.get("run_label"),
                    manifest.get("run_label"),
                )
                or ""
            ),
            "algorithm": _display_algorithm(algorithm_raw),
        },
        "problem_definition": problem_definition,
        "runtime_baseline": runtime_baseline,
        "algorithm_setup": algorithm_setup,
        "attempt_progress": attempt_progress,
        "generation_progress": generation_progress,
        "feasibility_progress": feasibility_progress,
        "objective_constraint_progress": {
            "key_metric_rows": key_metric_rows,
            "constraint_progress_rows": constraint_progress_rows,
            "objective_progress_rows": objective_progress_rows,
        },
        "final_result": final_result,
        "release_audit_summary": release_audit_summary,
        "source_artifacts": source_artifacts,
        "artifact_index_snapshot": dict(artifact_index or {}),
    }


def _escape_markdown_cell(value: Any) -> str:
    text = _format_scalar(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _render_table(
    rows: List[Dict[str, Any]],
    columns: List[str],
    labels: Optional[Dict[str, str]] = None,
) -> str:
    headers = [str((labels or {}).get(column, column)) for column in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    if not rows:
        lines.append("| " + " | ".join("n/a" for _ in columns) + " |")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_escape_markdown_cell(row.get(column)) for column in columns)
            + " |"
        )
    return "\n".join(lines)


def _render_field_table(mapping: Dict[str, Any]) -> str:
    rows = [{"字段": key, "值": value} for key, value in mapping.items()]
    return _render_table(rows, ["字段", "值"])


def render_mass_final_summary_markdown(digest: Dict[str, Any]) -> str:
    run_identity = dict(digest.get("run_identity", {}) or {})
    problem_definition = dict(digest.get("problem_definition", {}) or {})
    runtime_baseline = dict(digest.get("runtime_baseline", {}) or {})
    algorithm_setup = dict(digest.get("algorithm_setup", {}) or {})
    attempt_progress = dict(digest.get("attempt_progress", {}) or {})
    generation_progress = dict(digest.get("generation_progress", {}) or {})
    feasibility_progress = dict(digest.get("feasibility_progress", {}) or {})
    objective_constraint_progress = dict(
        digest.get("objective_constraint_progress", {}) or {}
    )
    final_result = dict(digest.get("final_result", {}) or {})
    release_audit_summary = dict(digest.get("release_audit_summary", {}) or {})
    source_artifacts = list(digest.get("source_artifacts", []) or [])

    lines: List[str] = [
        "# MASS 优化结果总结（中文）",
        "",
        "## 运行身份",
        "",
        _render_field_table(
            {
                "run_id": run_identity.get("run_id"),
                "run_mode": run_identity.get("run_mode"),
                "execution_mode": run_identity.get("execution_mode"),
                "timestamp": run_identity.get("timestamp"),
                "level": run_identity.get("level"),
                "algorithm": run_identity.get("algorithm"),
            }
        ),
        "",
        "## 优化问题定义",
        "",
        _render_field_table(
            {
                "requirement_text_brief": problem_definition.get("requirement_text_brief"),
                "requirement_text_full": problem_definition.get("requirement_text_full"),
                "component_count": problem_definition.get("component_count"),
                "decision_variable_summary": problem_definition.get("decision_variable_summary"),
            }
        ),
        "",
        "### 目标函数",
        "",
        _render_table(
            list(problem_definition.get("objective_rows", []) or []),
            ["name", "metric_key", "direction", "weight", "normalized_text"],
            labels={"normalized_text": "normalized_text / notes"},
        ),
        "",
        "### 硬约束",
        "",
        _render_table(
            list(problem_definition.get("hard_constraint_rows", []) or []),
            ["name", "metric_key", "relation", "target_value", "category", "normalized_g_leq_0"],
        ),
        "",
        "## 运行基线与算法配置",
        "",
        "### Runtime Baseline",
        "",
        _render_field_table(runtime_baseline),
        "",
        "### Algorithm Setup",
        "",
        _render_field_table(algorithm_setup),
        "",
        "## Attempts 收敛过程",
        "",
        _render_field_table(
            {
                "attempt_count": attempt_progress.get("attempt_count"),
                "best_attempt_index": attempt_progress.get("best_attempt_index"),
            }
        ),
        "",
        _render_table(
            list(attempt_progress.get("attempt_rows", []) or []),
            [
                "attempt",
                "diagnosis_status",
                "best_cv",
                "is_best_attempt",
                "dominant_violation",
                "operator_actions_brief",
                "notes",
            ],
        ),
        "",
        "## Generations 演化过程",
        "",
        _render_field_table(
            {
                "generation_count": generation_progress.get("generation_count"),
                "best_generation_summary": generation_progress.get("best_generation_summary"),
            }
        ),
        "",
        _render_table(
            list(generation_progress.get("generation_rows", []) or []),
            [
                "attempt",
                "generation",
                "population_size",
                "feasible_count",
                "feasible_ratio",
                "best_cv",
                "mean_cv",
                "best_feasible_sum_f",
            ],
        ),
        "",
        "## 可行性收敛情况",
        "",
        _render_field_table(feasibility_progress),
        "",
        "## 目标与约束变化",
        "",
        "### key_metric_rows",
        "",
        _render_table(
            list(objective_constraint_progress.get("key_metric_rows", []) or []),
            ["metric_key", "initial_value", "best_value", "final_value", "notes"],
        ),
        "",
        "### constraint_progress_rows",
        "",
        _render_table(
            list(objective_constraint_progress.get("constraint_progress_rows", []) or []),
            ["metric_key", "relation", "target_value", "final_value", "final_gap", "status"],
        ),
        "",
        "### objective_progress_rows",
        "",
        _render_table(
            list(objective_constraint_progress.get("objective_progress_rows", []) or []),
            ["metric_key", "direction", "weight", "best_value", "final_value"],
        ),
        "",
        "## 最终结果",
        "",
        _render_field_table(
            {
                "status": final_result.get("status"),
                "final_iteration": final_result.get("final_iteration"),
                "best_cv_min": final_result.get("best_cv_min"),
                "final_mph_path": final_result.get("final_mph_path"),
                "final_layout_summary": final_result.get("final_layout_summary"),
                "conclusion": final_result.get("conclusion"),
            }
        ),
        "",
        "### best_candidate_metrics",
        "",
        _render_table(
            [
                {"metric_key": key, "value": value}
                for key, value in dict(final_result.get("best_candidate_metrics", {}) or {}).items()
            ],
            ["metric_key", "value"],
        ),
        "",
        "## Release Audit 结论",
        "",
        _render_field_table(release_audit_summary),
        "",
        "## 产物索引",
        "",
        _render_table(source_artifacts, ["path", "exists", "role"]),
        "",
    ]
    return "\n".join(lines)


def generate_mass_final_summary_zh(run_dir: str) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    manifest_path = run_path / "events" / "run_manifest.json"
    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path)
    run_mode = str(summary.get("run_mode", "") or manifest.get("run_mode", "") or "").strip()
    if run_mode != "mass":
        return {"generated": False, "reason": f"unsupported_run_mode:{run_mode or 'unknown'}"}

    digest = build_mass_final_summary_digest(str(run_path))
    digest_path = run_path / DIGEST_REL_PATH
    markdown_path = run_path / SUMMARY_MD_FILENAME
    _write_json(digest_path, digest)
    markdown_path.write_text(
        render_mass_final_summary_markdown(digest),
        encoding="utf-8",
    )

    summary_fields = {
        "mass_final_summary_zh_path": _serialize_rel(run_path, markdown_path),
        "mass_final_summary_digest_path": _serialize_rel(run_path, digest_path),
        "mass_final_summary_status": "template_only",
        "mass_final_summary_language": SUMMARY_LANGUAGE,
    }
    updated_summary = _merge_summary_fields(summary_path, summary_fields)
    existing_extra = dict(manifest.get("extra", {}) or {})
    existing_extra.update(summary_fields)
    manifest_payload = dict(summary_fields)
    manifest_payload["extra"] = existing_extra
    updated_manifest = _merge_summary_fields(manifest_path, manifest_payload)

    return {
        "generated": True,
        "digest": digest,
        "markdown_path": _serialize_rel(run_path, markdown_path),
        "digest_path": _serialize_rel(run_path, digest_path),
        "status": "template_only",
        "summary_fields": summary_fields,
        "summary": updated_summary,
        "manifest": updated_manifest,
    }
