"""
实验日志系统

提供完整的可追溯性支持，记录每次迭代的输入输出、指标变化和 LLM 交互。
"""

import os
import json
import logging
import math
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from core.artifact_index import (
    ARTIFACT_LAYOUT_VERSION,
    build_artifact_index_payload,
    default_raw_scope_for_run_mode,
    load_artifact_index,
    normalize_artifact_scope,
    scope_relative_root,
    write_artifact_index,
)
from core.event_logger import EventLogger
from core.llm_interaction_store import LLMInteractionStore
from core.mode_contract import (
    normalize_observability_mode,
    normalize_runtime_mode,
    resolve_execution_mode,
    resolve_lifecycle_state,
)
from core.path_policy import serialize_artifact_path, serialize_run_path
from visualization.review_summary_bridge import (
    format_iteration_review_report_block,
    load_iteration_review_summary_for_run,
)
from core.modes.agent_loop.trace_store import (
    append_agent_loop_trace_row,
    init_agent_loop_trace_csv,
    materialize_metrics_payload,
)
from core.modes.mass.trace_store import (
    append_mass_trace_row,
    init_mass_trace_csv,
    materialize_trace_payload,
)

GLOBAL_FILE_LOGGER_NAMES = {
    "api_server",
    "websocket_client",
}

RUN_NAME_SCHEMA_VERSION = 2
RUN_NAME_MODE_TOKENS = {
    "agent_loop": "agent",
    "mass": "mass",
    "vop_maas": "vop",
}


def _should_persist_global_log(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("experiment_"):
        return False
    return normalized in GLOBAL_FILE_LOGGER_NAMES


def resolve_run_name_mode_token(run_mode: Any) -> str:
    normalized = normalize_runtime_mode(run_mode, default="mass")
    return str(RUN_NAME_MODE_TOKENS.get(normalized, normalized or "run"))

def _sanitize_json_value(value: Any) -> Any:
    """Convert non-JSON-safe numeric values (NaN/Inf) into JSON-safe nulls."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json_value(v) for v in value]

    # Handle numpy arrays or similar containers with .tolist()
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _sanitize_json_value(tolist_fn())
        except Exception:
            pass

    # Handle numpy scalars or similar objects with .item()
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _sanitize_json_value(item_fn())
        except Exception:
            return value

    return value


def _json_dump_safe(payload: Any, fp) -> None:
    json.dump(
        _sanitize_json_value(payload),
        fp,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_dumps_safe(payload: Any) -> str:
    return json.dumps(
        _sanitize_json_value(payload),
        ensure_ascii=False,
        allow_nan=False,
    )


def discover_active_llm_buckets(run_dir: str) -> List[str]:
    index = load_artifact_index(run_dir)
    llm_map = dict(index.get("paths", {}).get("llm_interactions", {}) or {})
    if llm_map:
        buckets = []
        for key, value in llm_map.items():
            raw = str(value or "").strip()
            if not raw:
                continue
            candidate = Path(run_dir) / raw
            if candidate.is_dir():
                buckets.append(str(key))
        if buckets:
            return sorted(buckets)

    llm_dir = Path(run_dir) / "llm_interactions"
    if not llm_dir.exists() or not llm_dir.is_dir():
        return []
    buckets: List[str] = []
    for item in sorted(llm_dir.iterdir(), key=lambda path: path.name):
        if item.is_dir():
            buckets.append(item.name)
    return buckets


def _load_run_summary_payload(run_dir: str) -> Dict[str, Any]:
    summary_path = Path(run_dir) / "summary.json"
    if not summary_path.exists():
        return {}
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _load_llm_final_summary_digest(run_dir: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    rel_path = str(summary.get("llm_final_summary_digest_path", "") or "").strip()
    if not rel_path:
        return {}
    path = Path(run_dir) / rel_path
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _load_mass_final_summary_digest(run_dir: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    rel_path = str(summary.get("mass_final_summary_digest_path", "") or "").strip()
    if not rel_path:
        return {}
    path = Path(run_dir) / rel_path
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _build_llm_final_summary_conclusion(
    digest: Dict[str, Any],
    summary: Dict[str, Any],
) -> str:
    delegated = dict(digest.get("delegated_mass_result", {}) or {})
    final_result = dict(digest.get("final_result_summary", {}) or {})
    diagnosis = str(
        delegated.get("diagnosis_status", "") or summary.get("diagnosis_status", "") or ""
    )
    audit_status = str(
        delegated.get("final_audit_status", "")
        or summary.get("final_audit_status", "")
        or ""
    )
    verdict = str(
        final_result.get("effectiveness_verdict", "")
        or delegated.get("effectiveness_verdict", "")
        or ""
    )
    if diagnosis == "feasible":
        if audit_status and audit_status not in {"passed", "ok", "success"}:
            return f"已得到可行解，但审计状态仍为 {audit_status}。"
        return "已得到可行解，VOP 控制层与 delegated mass 结果基本闭环。"
    if verdict:
        return f"当前效果判断为 {verdict}，建议继续做下一轮策略收敛。"
    return "已生成中文总结，但最终效果仍需结合详细文档复核。"


def build_llm_final_summary_report_block(
    run_dir: str,
    summary: Dict[str, Any],
) -> str:
    run_mode = str(summary.get("run_mode", "") or "").strip()
    status = str(summary.get("llm_final_summary_status", "") or "").strip()
    summary_path = str(summary.get("llm_final_summary_zh_path", "") or "").strip()
    if run_mode != "vop_maas" or (not summary_path and not status):
        return ""

    digest = _load_llm_final_summary_digest(run_dir, summary)
    goal_summary = dict(digest.get("goal_summary", {}) or {})
    decision_flow = list(digest.get("decision_flow", []) or [])
    operator_summary = dict(digest.get("operator_summary", {}) or {})
    optimization_scheme = dict(digest.get("optimization_scheme", {}) or {})
    delegated = dict(digest.get("delegated_mass_result", {}) or {})

    first_decision = dict(decision_flow[0] or {}) if decision_flow else {}
    last_decision = dict(decision_flow[-1] or {}) if decision_flow else {}
    block_lines = [
        "<!-- LLM_FINAL_SUMMARY_ZH:START -->",
        "## 中文 LLM 决策总结",
        "",
        f"- 生成状态：`{status or 'n/a'}`",
        f"- 文档路径：`{summary_path or 'n/a'}`",
    ]
    runtime_feature_path = str(
        summary.get("runtime_feature_fingerprint_path", "") or ""
    ).strip()
    if runtime_feature_path:
        block_lines.append(f"- 运行指纹：`{runtime_feature_path}`")

    requirement_brief = str(goal_summary.get("requirement_text_brief", "") or "").strip()
    if requirement_brief:
        block_lines.append(f"- 目标摘要：`{requirement_brief}`")
    block_lines.append(f"- 决策轮数：`{len(decision_flow) or int(summary.get('vop_round_count', 0) or 0)}`")

    program_id = str(
        operator_summary.get("selected_operator_program_id", "")
        or summary.get("vop_selected_operator_program_id", "")
        or ""
    ).strip()
    operator_actions = [
        str(item).strip()
        for item in list(operator_summary.get("operator_actions", []) or [])
        if str(item).strip()
    ]
    if program_id or operator_actions:
        block_lines.append(
            f"- 主算子：program=`{program_id or 'n/a'}`，actions=`{', '.join(operator_actions) or 'n/a'}`"
        )

    search_space_override = str(
        optimization_scheme.get("search_space_override", "")
        or summary.get("vop_search_space_override", "")
        or ""
    ).strip()
    if search_space_override:
        block_lines.append(
            f"- 关键变更：search_space=`{search_space_override}`，replan=`{str(last_decision.get('replan_reason', '') or 'n/a')}`"
        )

    diagnosis = str(
        delegated.get("diagnosis_status", "")
        or summary.get("diagnosis_status", "")
        or ""
    ).strip()
    audit_status = str(
        delegated.get("final_audit_status", "")
        or summary.get("final_audit_status", "")
        or ""
    ).strip()
    block_lines.append(
        f"- 观察结果：diagnosis=`{diagnosis or 'n/a'}`，audit=`{audit_status or 'n/a'}`"
    )
    block_lines.append(
        f"- 一句话结论：{_build_llm_final_summary_conclusion(digest, summary)}"
    )
    block_lines.extend(["<!-- LLM_FINAL_SUMMARY_ZH:END -->", ""])
    return "\n".join(block_lines)


def _build_mass_final_summary_conclusion(
    digest: Dict[str, Any],
    summary: Dict[str, Any],
) -> str:
    final_result = dict(digest.get("final_result", {}) or {})
    release_audit = dict(digest.get("release_audit_summary", {}) or {})
    conclusion = str(final_result.get("conclusion", "") or "").strip()
    if conclusion:
        return conclusion
    diagnosis = str(summary.get("diagnosis_status", "") or "").strip()
    audit_status = str(
        release_audit.get("final_audit_status", "") or summary.get("final_audit_status", "") or ""
    ).strip()
    if diagnosis == "feasible":
        if audit_status == "release_grade_real_comsol_validated":
            return "已得到可行解，且 release audit 已达 release-grade。"
        return f"已得到可行解，但最终 audit 状态为 {audit_status or 'n/a'}。"
    if audit_status:
        return f"尚未形成 release-grade 结果，当前 audit 状态为 {audit_status}。"
    return "已生成 MASS 中文总结，建议结合文档复核收敛与审计结论。"


def build_mass_final_summary_report_block(
    run_dir: str,
    summary: Dict[str, Any],
) -> str:
    run_mode = str(summary.get("run_mode", "") or "").strip()
    status = str(summary.get("mass_final_summary_status", "") or "").strip()
    summary_path = str(summary.get("mass_final_summary_zh_path", "") or "").strip()
    digest_path = str(summary.get("mass_final_summary_digest_path", "") or "").strip()
    if run_mode != "mass" or (not status and not summary_path):
        return ""

    digest = _load_mass_final_summary_digest(run_dir, summary)
    run_identity = dict(digest.get("run_identity", {}) or {})
    attempt_progress = dict(digest.get("attempt_progress", {}) or {})
    generation_progress = dict(digest.get("generation_progress", {}) or {})
    feasibility_progress = dict(digest.get("feasibility_progress", {}) or {})
    release_audit = dict(digest.get("release_audit_summary", {}) or {})

    block_lines = [
        "<!-- MASS_FINAL_SUMMARY_ZH:START -->",
        "## 中文优化过程总结",
        "",
        f"- 生成状态：`{status or 'n/a'}`",
        f"- 文档路径：`{summary_path or 'n/a'}`",
        f"- Digest 路径：`{digest_path or 'n/a'}`",
        f"- 算法主线：`{str(run_identity.get('algorithm', '') or summary.get('pymoo_algorithm', '') or 'n/a')}`",
        f"- 收敛概览：attempt_count=`{attempt_progress.get('attempt_count', 'n/a')}`，generation_count=`{generation_progress.get('generation_count', 'n/a')}`",
        f"- 可行性：first_feasible_eval=`{feasibility_progress.get('first_feasible_eval', 'n/a')}`，final_audit_status=`{release_audit.get('final_audit_status', summary.get('final_audit_status', 'n/a'))}`",
        f"- 一句话结论：{_build_mass_final_summary_conclusion(digest, summary)}",
        "<!-- MASS_FINAL_SUMMARY_ZH:END -->",
        "",
    ]
    return "\n".join(block_lines)


def _upsert_markdown_block(content: str, block: str, start_marker: str, end_marker: str) -> str:
    if not block:
        return content
    if start_marker in content and end_marker in content:
        prefix, remainder = content.split(start_marker, 1)
        _, suffix = remainder.split(end_marker, 1)
        return f"{prefix}{block}{suffix.lstrip()}"
    separator = "" if content.endswith("\n") else "\n"
    return f"{content}{separator}\n{block}"


def _format_markdown_bool(value: Any) -> str:
    if value is None:
        return "n/a"
    return "true" if bool(value) else "false"


def write_markdown_report(
    run_dir: str,
    summary: Dict[str, Any],
    *,
    active_buckets: Optional[List[str]] = None,
) -> None:
    report_path = os.path.join(run_dir, "report.md")
    observability_tables = dict(summary.get("observability_tables", {}) or {})
    artifact_index = load_artifact_index(run_dir)
    top_level = dict(artifact_index.get("top_level", {}) or {})
    indexed_paths = dict(artifact_index.get("paths", {}) or {})
    execution_mode = str(summary.get("execution_mode", "") or "")
    lifecycle_state = str(summary.get("lifecycle_state", "") or "")

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# Satellite Design Optimization Report\n\n")
        f.write(f"**Status**: {summary['status']}\n\n")
        f.write(f"**Final Iteration**: {summary['final_iteration']}\n\n")
        f.write(f"**Timestamp**: {summary['timestamp']}\n\n")
        run_mode = str(summary.get("run_mode", "") or "")
        if run_mode or execution_mode or lifecycle_state:
            f.write("## Run Identity\n\n")
            if run_mode:
                f.write(f"- Run mode: `{run_mode}`\n")
            if execution_mode:
                f.write(f"- Execution mode: `{execution_mode}`\n")
            if lifecycle_state:
                f.write(f"- Lifecycle state: `{lifecycle_state}`\n")
            artifact_layout_version = summary.get("artifact_layout_version")
            if artifact_layout_version is not None:
                f.write(f"- Artifact layout version: `{artifact_layout_version}`\n")
            artifact_index_path = str(summary.get("artifact_index_path", "") or "")
            if artifact_index_path:
                f.write(f"- Artifact index: `{artifact_index_path}`\n")
            f.write("\n")

        if summary.get('notes'):
            f.write(f"## Notes\n\n{summary['notes']}\n\n")

        audit_status = str(summary.get("final_audit_status", "") or "").strip()
        simulation_backend = str(summary.get("simulation_backend", "") or "").strip()
        thermal_mode = str(summary.get("thermal_evaluator_mode", "") or "").strip()
        final_mph_path = str(summary.get("final_mph_path", "") or "").strip()
        if (
            audit_status
            or simulation_backend
            or thermal_mode
            or "first_feasible_eval" in summary
            or "comsol_calls_to_first_feasible" in summary
        ):
            f.write("## Release Audit\n\n")
            if audit_status:
                f.write(f"- Final audit status: `{audit_status}`\n")
            if simulation_backend:
                f.write(f"- Simulation backend: `{simulation_backend}`\n")
            if thermal_mode:
                f.write(f"- Thermal evaluator mode: `{thermal_mode}`\n")
            first_feasible_eval = summary.get("first_feasible_eval")
            if first_feasible_eval is None:
                f.write("- First feasible eval: `n/a`\n")
            else:
                f.write(f"- First feasible eval: `{first_feasible_eval}`\n")
            comsol_calls = summary.get("comsol_calls_to_first_feasible")
            if comsol_calls is None:
                f.write("- COMSOL calls to first feasible: `n/a`\n")
            else:
                f.write(
                    f"- COMSOL calls to first feasible: `{comsol_calls}`\n"
                )
            if final_mph_path:
                f.write(f"- Final MPH path: `{final_mph_path}`\n")
            release_audit_path = str(
                observability_tables.get("release_audit_path", "") or ""
            ).strip()
            if release_audit_path:
                f.write(f"- Release audit table: `{release_audit_path}`\n")
            vop_round_audit_table = str(
                summary.get("vop_round_audit_table", "") or ""
            ).strip()
            if vop_round_audit_table:
                f.write(f"- VOP round audit table: `{vop_round_audit_table}`\n")
            f.write("\n")

        iteration_review_summary = load_iteration_review_summary_for_run(run_dir, summary=summary)
        iteration_review_block = format_iteration_review_report_block(iteration_review_summary)
        if iteration_review_block:
            f.write(iteration_review_block)

        satellite_archetype_id = str(summary.get("satellite_archetype_id", "") or "").strip()
        satellite_mission_class = str(summary.get("satellite_mission_class", "") or "").strip()
        satellite_task_type = str(summary.get("satellite_task_type", "") or "").strip()
        satellite_default_rule_profile = str(
            summary.get("satellite_default_rule_profile", "") or ""
        ).strip()
        satellite_bus_span_mm = list(summary.get("satellite_bus_span_mm", []) or [])
        satellite_bus_aspect_ratio_evaluated = list(
            summary.get("satellite_bus_aspect_ratio_evaluated", []) or []
        )
        satellite_bus_aspect_ratio_violations = list(
            summary.get("satellite_bus_aspect_ratio_violations", []) or []
        )
        satellite_task_face_missing_requirements = [
            str(item).strip()
            for item in list(summary.get("satellite_task_face_missing_requirements", []) or [])
            if str(item).strip()
        ]
        satellite_task_face_resolution = [
            dict(item)
            for item in list(summary.get("satellite_task_face_resolution", []) or [])
            if isinstance(item, dict)
        ]
        satellite_task_face_resolution_source_counts = dict(
            summary.get("satellite_task_face_resolution_source_counts", {}) or {}
        )
        satellite_appendage_template_violations = [
            str(item).strip()
            for item in list(summary.get("satellite_appendage_template_violations", []) or [])
            if str(item).strip()
        ]
        satellite_interior_zone_violations = [
            str(item).strip()
            for item in list(summary.get("satellite_interior_zone_violations", []) or [])
            if str(item).strip()
        ]
        satellite_interior_zone_resolution = [
            dict(item)
            for item in list(summary.get("satellite_interior_zone_resolution", []) or [])
            if isinstance(item, dict)
        ]
        satellite_interior_zone_resolution_source_counts = dict(
            summary.get("satellite_interior_zone_resolution_source_counts", {}) or {}
        )
        satellite_interior_zone_unassigned_components = [
            dict(item)
            for item in list(summary.get("satellite_interior_zone_unassigned_components", []) or [])
            if isinstance(item, dict)
        ]
        satellite_reference_baseline_id = str(
            summary.get("satellite_reference_baseline_id", "") or ""
        ).strip()
        satellite_reference_baseline_version = str(
            summary.get("satellite_reference_baseline_version", "") or ""
        ).strip()
        satellite_baseline_reference_boundary = str(
            summary.get("satellite_baseline_reference_boundary", "") or ""
        ).strip()
        satellite_archetype_reference_boundary = str(
            summary.get("satellite_archetype_reference_boundary", "") or ""
        ).strip()
        satellite_public_reference_notes = [
            str(item).strip()
            for item in list(summary.get("satellite_public_reference_notes", []) or [])
            if str(item).strip()
        ]
        satellite_archetype_source = str(summary.get("satellite_archetype_source", "") or "").strip()
        satellite_gate_mode = str(summary.get("satellite_likeness_gate_mode", "") or "").strip()
        satellite_gate_evaluation_stage = str(
            summary.get("satellite_gate_evaluation_stage", "") or ""
        ).strip()
        satellite_final_warning = bool(
            summary.get("satellite_likeness_gate_final_warning", False)
        )
        satellite_final_warning_failed_rules = [
            str(item).strip()
            for item in list(
                summary.get("satellite_likeness_gate_final_warning_failed_rules", []) or []
            )
            if str(item).strip()
        ]
        satellite_gate_total_rule_count = int(
            summary.get("satellite_gate_total_rule_count", 0) or 0
        )
        satellite_gate_passed_rule_count = int(
            summary.get("satellite_gate_passed_rule_count", 0) or 0
        )
        satellite_gate_failed_rule_count = int(
            summary.get("satellite_gate_failed_rule_count", 0) or 0
        )
        satellite_gate_rule_results = [
            dict(item)
            for item in list(summary.get("satellite_likeness_gate_rule_results", []) or [])
            if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
        ]
        satellite_gate_failed_rule_details = [
            dict(item)
            for item in list(summary.get("satellite_gate_failed_rule_details", []) or [])
            if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
        ]
        satellite_failed_rules = [
            str(item).strip()
            for item in list(summary.get("satellite_likeness_gate_failed_rules", []) or [])
            if str(item).strip()
        ]
        satellite_gate_present = (
            satellite_archetype_id
            or satellite_mission_class
            or satellite_task_type
            or satellite_default_rule_profile
            or satellite_bus_span_mm
            or satellite_bus_aspect_ratio_evaluated
            or satellite_bus_aspect_ratio_violations
            or satellite_reference_baseline_id
            or satellite_reference_baseline_version
            or satellite_baseline_reference_boundary
            or satellite_archetype_reference_boundary
            or satellite_public_reference_notes
            or satellite_archetype_source
            or satellite_gate_mode
            or satellite_gate_evaluation_stage
            or satellite_final_warning
            or satellite_final_warning_failed_rules
            or satellite_gate_rule_results
            or "satellite_likeness_gate_passed" in summary
        )
        if satellite_gate_present:
            f.write("## Satellite Context\n\n")
            if satellite_archetype_id:
                f.write(f"- Archetype: `{satellite_archetype_id}`\n")
            if satellite_mission_class:
                f.write(f"- Mission class: `{satellite_mission_class}`\n")
            if satellite_task_type:
                f.write(f"- Task type: `{satellite_task_type}`\n")
            if satellite_default_rule_profile:
                f.write(f"- Default rule profile: `{satellite_default_rule_profile}`\n")
            if satellite_bus_span_mm:
                f.write(f"- Bus span mm: `{satellite_bus_span_mm}`\n")
            if satellite_reference_baseline_id or satellite_reference_baseline_version:
                f.write(
                    "- Reference baseline: "
                    f"`{satellite_reference_baseline_id or 'n/a'}@{satellite_reference_baseline_version or 'n/a'}`\n"
                )
            if satellite_archetype_source:
                f.write(f"- Archetype source: `{satellite_archetype_source}`\n")
            if satellite_baseline_reference_boundary:
                f.write(
                    "- Baseline boundary: "
                    f"`{satellite_baseline_reference_boundary}`\n"
                )
            if satellite_archetype_reference_boundary:
                f.write(
                    "- Archetype boundary: "
                    f"`{satellite_archetype_reference_boundary}`\n"
                )
            if satellite_public_reference_notes:
                f.write(
                    "- Public reference notes: "
                    f"`{' | '.join(satellite_public_reference_notes)}`\n"
                )
            if satellite_gate_mode:
                f.write(f"- Likeness gate mode: `{satellite_gate_mode}`\n")
            if satellite_gate_evaluation_stage:
                f.write(
                    "- Likeness evaluation stage: "
                    f"`{satellite_gate_evaluation_stage}`\n"
                )
            f.write(
                "- Likeness gate passed: "
                f"`{_format_markdown_bool(summary.get('satellite_likeness_gate_passed', None))}`\n"
            )
            f.write(
                "- Final-state gate warning: "
                f"`{_format_markdown_bool(satellite_final_warning)}`\n"
            )
            if satellite_final_warning_failed_rules:
                f.write(
                    "- Final-state gate warning failed rules: "
                    f"`{', '.join(satellite_final_warning_failed_rules)}`\n"
                )
            if satellite_gate_rule_results:
                rule_text = ", ".join(
                    [
                        f"{str(item.get('rule_id', '') or '')}={_format_markdown_bool(item.get('passed', None))}"
                        for item in satellite_gate_rule_results
                        if str(item.get("rule_id", "") or "").strip()
                    ]
                )
                f.write(f"- Gate rule results: `{rule_text or 'n/a'}`\n")
            if satellite_gate_total_rule_count:
                f.write(
                    "- Gate rule counts: "
                    f"`total={satellite_gate_total_rule_count}, passed={satellite_gate_passed_rule_count}, failed={satellite_gate_failed_rule_count}`\n"
                )
            if satellite_gate_failed_rule_details:
                failed_detail_text = " | ".join(
                    [
                        f"{str(item.get('rule_id', '') or '')}: {str(item.get('summary', '') or '').strip()}"
                        for item in satellite_gate_failed_rule_details
                        if str(item.get("rule_id", "") or "").strip()
                    ]
                )
                f.write(
                    "- Failed rule details: "
                    f"`{failed_detail_text or 'n/a'}`\n"
                )
            if satellite_bus_aspect_ratio_evaluated:
                ratio_text = ", ".join(
                    [
                        f"{str(item.get('ratio_id', '') or '')}={item.get('ratio')}"
                        for item in satellite_bus_aspect_ratio_evaluated
                        if str(item.get("ratio_id", "") or "").strip()
                    ]
                )
                f.write(f"- Bus aspect ratios: `{ratio_text or 'n/a'}`\n")
            if satellite_bus_aspect_ratio_violations:
                f.write(
                    "- Bus aspect-ratio violations: "
                    f"`{', '.join(str(item) for item in satellite_bus_aspect_ratio_violations)}`\n"
                )
            f.write(
                "- Task-face missing requirements: "
                f"`{', '.join(satellite_task_face_missing_requirements) if satellite_task_face_missing_requirements else 'none'}`\n"
            )
            if satellite_task_face_resolution:
                task_face_resolution_text = ", ".join(
                    [
                        f"{str(item.get('semantic', '') or '')}:{str(item.get('face_id', '') or '')}({str(item.get('source', '') or 'n/a')})"
                        for item in satellite_task_face_resolution
                        if str(item.get("semantic", "") or "").strip()
                    ]
                )
                f.write(
                    "- Task-face resolution: "
                    f"`{task_face_resolution_text or 'n/a'}`\n"
                )
            if satellite_task_face_resolution_source_counts:
                f.write(
                    "- Task-face resolution source counts: "
                    f"`{', '.join([f'{key}={value}' for key, value in satellite_task_face_resolution_source_counts.items()])}`\n"
                )
            f.write(
                "- Appendage template violations: "
                f"`{', '.join(satellite_appendage_template_violations) if satellite_appendage_template_violations else 'none'}`\n"
            )
            f.write(
                "- Interior-zone violations: "
                f"`{', '.join(satellite_interior_zone_violations) if satellite_interior_zone_violations else 'none'}`\n"
            )
            if satellite_interior_zone_resolution:
                interior_zone_resolution_text = ", ".join(
                    [
                        f"{str(item.get('component_id', '') or '')}->{str(item.get('zone_id', '') or '')}({str(item.get('component_category', '') or '')};{str(item.get('source', '') or 'n/a')})"
                        for item in satellite_interior_zone_resolution
                        if str(item.get("component_id", "") or "").strip()
                    ]
                )
                f.write(
                    "- Interior-zone resolution: "
                    f"`{interior_zone_resolution_text or 'n/a'}`\n"
                )
            if satellite_interior_zone_resolution_source_counts:
                f.write(
                    "- Interior-zone resolution source counts: "
                    f"`{', '.join([f'{key}={value}' for key, value in satellite_interior_zone_resolution_source_counts.items()])}`\n"
                )
            if satellite_interior_zone_unassigned_components:
                unassigned_text = ", ".join(
                    [
                        f"{str(item.get('component_id', '') or '')}:{str(item.get('component_category', '') or '')}"
                        for item in satellite_interior_zone_unassigned_components
                        if str(item.get("component_id", "") or "").strip()
                    ]
                )
                f.write(
                    "- Interior-zone unassigned details: "
                    f"`{unassigned_text or 'n/a'}`\n"
                )
            f.write(
                "- Failed rules: "
                f"`{', '.join(satellite_failed_rules) if satellite_failed_rules else 'none'}`\n"
            )
            if "satellite_candidate_task_face_count" in summary:
                f.write(
                    "- Candidate task faces: "
                    f"`{summary.get('satellite_candidate_task_face_count')}`\n"
                )
            if "satellite_candidate_appendage_count" in summary:
                f.write(
                    "- Candidate appendages: "
                    f"`{summary.get('satellite_candidate_appendage_count')}`\n"
                )
            if "satellite_candidate_interior_zone_assignment_count" in summary:
                f.write(
                    "- Candidate interior-zone assignments: "
                    f"`{summary.get('satellite_candidate_interior_zone_assignment_count')}`\n"
                )
            if "satellite_candidate_interior_zone_unassigned_count" in summary:
                f.write(
                    "- Candidate interior-zone unassigned: "
                    f"`{summary.get('satellite_candidate_interior_zone_unassigned_count')}`\n"
                )
            f.write("\n")

        mass_final_summary_block = build_mass_final_summary_report_block(run_dir, summary)
        if mass_final_summary_block:
            f.write(mass_final_summary_block)

        f.write(f"## Files\n\n")
        evolution_trace = str(indexed_paths.get("agent_loop_trace_csv", "") or "")
        mass_trace = str(indexed_paths.get("mass_trace_csv", "") or "")
        if evolution_trace:
            f.write(f"- Evolution trace: `{evolution_trace}`\n")
        if mass_trace:
            f.write(f"- mass trace: `{mass_trace}`\n")
        f.write(f"- Events: `{top_level.get('events_dir', 'events')}`\n")
        f.write(f"  - generation events: `events/generation_events.jsonl`\n")
        f.write(f"- Materialized tables: `{top_level.get('tables_dir', 'tables')}`\n")
        if observability_tables.get("release_audit_path"):
            f.write(
                f"  - release audit: `{observability_tables.get('release_audit_path')}`\n"
            )
        if summary.get("vop_round_audit_table"):
            f.write(
                f"  - VOP rounds: `{summary.get('vop_round_audit_table')}`\n"
            )
        if active_buckets:
            f.write(
                "- LLM scopes: "
                + ", ".join(
                    [
                        f"`{str((indexed_paths.get('llm_interactions', {}) or {}).get(name, name))}`"
                        for name in active_buckets
                    ]
                )
                + "\n"
            )
        else:
            f.write("- LLM scopes: (none)\n")
        f.write(f"- Visualizations: `{top_level.get('visualizations_dir', 'visualizations')}`\n")


def _sanitize_run_label(raw_label: Any) -> str:
    """Normalize run label for directory/file-system safety."""
    text = str(raw_label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "_")
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    if not text:
        return ""
    return text[:64]


def _normalize_run_algorithm(raw_algorithm: Any) -> str:
    """Normalize algorithm tag for run naming."""
    text = str(raw_algorithm or "").strip().lower()
    if not text:
        return ""

    compact = re.sub(r"[^a-z0-9]+", "", text)
    aliases = {
        "nsga2": "nsga2",
        "nsgaii": "nsga2",
        "nsga3": "nsga3",
        "nsgaiii": "nsga3",
        "moead": "moead",
    }
    mapped = aliases.get(compact, "")
    if mapped:
        return mapped

    token = _sanitize_run_label(text)
    return token[:16]


def _contains_algorithm_token(label: str, algorithm: str) -> bool:
    normalized_label = _sanitize_run_label(label)
    normalized_algorithm = _normalize_run_algorithm(algorithm)
    if not normalized_label or not normalized_algorithm:
        return False
    tokens = [item for item in normalized_label.split("_") if item]
    for token in tokens:
        if _normalize_run_algorithm(token) == normalized_algorithm:
            return True
    return False


def _build_compact_run_label(raw_label: Any, *, run_mode: str, run_algorithm: str) -> str:
    label = _sanitize_run_label(raw_label)
    algorithm = _normalize_run_algorithm(run_algorithm)
    normalized_mode = normalize_runtime_mode(run_mode, default="mass")
    mode_tag = resolve_run_name_mode_token(normalized_mode)
    normalized_mode_token = _sanitize_run_label(normalized_mode)

    if not label:
        label = mode_tag or "run"

    replacements = (
        ("operator_program", "op"),
        ("meta_policy", "mp"),
        ("baseline", "base"),
        ("deterministic", "det"),
        ("strict_replay", "replay"),
        ("agent_loop", "agent"),
        ("online_comsol", "ocomsol"),
        ("real_only", "real"),
        ("intermediate", "mid"),
        ("complex", "cx"),
        ("extreme", "x"),
    )
    for source, target in replacements:
        label = label.replace(source, target)

    raw_tokens = [item for item in label.split("_") if item]
    compacted_tokens: List[str] = []
    seen = set()
    for token in raw_tokens:
        if token in {"bm", "run"}:
            continue
        if re.fullmatch(r"\d{4}", token) or re.fullmatch(r"\d{6}", token):
            continue
        if token == "simple" and compacted_tokens and re.fullmatch(r"l\d+", compacted_tokens[-1]):
            continue
        if token in {mode_tag, normalized_mode_token}:
            continue
        if token in seen:
            continue
        seen.add(token)
        compacted_tokens.append(token)

    if not compacted_tokens:
        compacted_tokens = [mode_tag or "run"]

    compacted = "_".join(compacted_tokens)
    if algorithm and algorithm != "na" and not _contains_algorithm_token(compacted, algorithm):
        compacted = f"{compacted}_{algorithm}"
    compacted = _sanitize_run_label(compacted)

    if len(compacted) <= 40:
        return compacted

    preferred: List[str] = []
    for token in compacted.split("_"):
        if token.startswith("l") and token[1:].isdigit():
            preferred.append(token)
        elif token in {"op", "mp", "base", "agent", "mass", "vop"}:
            preferred.append(token)
        elif token.startswith("s") and token[1:].isdigit():
            preferred.append(token)
        elif _normalize_run_algorithm(token) in {"nsga2", "nsga3", "moead"}:
            preferred.append(_normalize_run_algorithm(token))
        elif token.startswith("t") and any(ch.isdigit() for ch in token):
            preferred.append(token)
        elif len(preferred) < 4:
            preferred.append(token)
    compacted = _sanitize_run_label("_".join(preferred) or compacted)
    return compacted[:40]


def _next_run_sequence(parent_dir: str, name_prefix: str) -> int:
    """Resolve next collision index for a run leaf name."""
    root = Path(str(parent_dir)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(name_prefix)}(?:_(\d{{2}}))?$")
    max_seq = 0
    try:
        for item in root.iterdir():
            if not item.is_dir():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            try:
                suffix = match.group(1)
                seq = int(suffix) if suffix is not None else 1
                max_seq = max(max_seq, seq)
            except Exception:
                continue
    except Exception:
        return 1
    return max_seq + 1


class ExperimentLogger:
    """Experiment-scoped artifact and observability logger."""
    def __init__(
        self,
        base_dir: str = "experiments",
        run_mode: Optional[str] = None,
        run_label: Optional[str] = None,
        run_algorithm: Optional[str] = None,
        run_naming_strategy: Optional[str] = None,
    ):
        """
        初始化日志管理器

        Args:
            base_dir: 实验输出根目录
            run_mode: 运行模式标签（agent_loop/mass）
            run_label: 运行标签（通常来自 BOM 或测试名）
            run_algorithm: 算法标签（如 NSGA-II/MOEAD）
            run_naming_strategy: 命名策略（compact/verbose）
        """
        self.base_dir = base_dir
        self.base_dir_path = Path(self.base_dir).resolve()
        self.run_mode = normalize_runtime_mode(run_mode, default="mass")
        self.execution_mode = resolve_execution_mode(self.run_mode)
        self.lifecycle_state = resolve_lifecycle_state(self.run_mode)
        self.run_mode_bucket = normalize_observability_mode(
            self.run_mode,
            default=self.run_mode,
        )
        self.artifact_layout_version = int(ARTIFACT_LAYOUT_VERSION)
        self.default_raw_scope = default_raw_scope_for_run_mode(self.run_mode)
        self.run_name_mode_token = resolve_run_name_mode_token(self.run_mode)
        self.run_name_schema_version = int(RUN_NAME_SCHEMA_VERSION)
        self.run_label = _sanitize_run_label(run_label)
        self.run_algorithm = _normalize_run_algorithm(run_algorithm) or "na"
        strategy = str(run_naming_strategy or "compact").strip().lower()
        if strategy not in {"compact", "verbose"}:
            strategy = "compact"
        self.run_naming_strategy = strategy
        mode_tag = self.run_name_mode_token

        started_at = datetime.now()
        self.run_started_at = started_at.isoformat()
        self.run_date = started_at.strftime("%m%d")
        self.run_time = started_at.strftime("%H%M")
        self.run_time_precise = started_at.strftime("%H%M%S")
        self.run_timestamp = f"{self.run_date}_{self.run_time_precise}"
        date_root = self.base_dir_path / self.run_date
        if self.run_naming_strategy == "verbose":
            run_prefix = f"{self.run_time_precise}_{mode_tag}"
            if self.run_label:
                run_prefix = f"{run_prefix}_{self.run_label}"
            if self.run_algorithm != "na" and not _contains_algorithm_token(run_prefix, self.run_algorithm):
                run_prefix = f"{run_prefix}_{self.run_algorithm}"
        else:
            short_tag = _build_compact_run_label(
                self.run_label,
                run_mode=self.run_mode,
                run_algorithm=self.run_algorithm,
            )
            if not short_tag:
                run_prefix = f"{self.run_time}_{mode_tag}"
            elif short_tag == mode_tag or str(short_tag).startswith(f"{mode_tag}_"):
                run_prefix = f"{self.run_time}_{short_tag}"
            else:
                run_prefix = f"{self.run_time}_{mode_tag}_{short_tag}"

        self.run_sequence = int(max(1, _next_run_sequence(parent_dir=str(date_root), name_prefix=run_prefix)))
        run_stem = run_prefix if self.run_sequence == 1 else f"{run_prefix}_{self.run_sequence:02d}"
        run_dir = date_root / run_stem
        while run_dir.exists():
            self.run_sequence += 1
            run_stem = f"{run_prefix}_{self.run_sequence:02d}"
            run_dir = date_root / run_stem
        self.run_dir = str(run_dir)
        self.exp_dir = self.run_dir  # 添加 exp_dir 别名
        self.run_id = f"run_{self.run_date}_{run_stem}"
        self.latest_index_path = str(self.base_dir_path / "_latest.json")
        os.makedirs(self.run_dir, exist_ok=True)
        self.event_logger = EventLogger(
            self.run_dir,
            persisted_run_dir=serialize_artifact_path(self.base_dir_path, self.run_dir),
        )
        self.event_logger.run_id = self.run_id
        self.artifacts_root = Path(self.run_dir) / "artifacts"
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.artifact_index = build_artifact_index_payload(
            run_dir=self.run_dir,
            run_mode=self.run_mode,
            execution_mode=self.execution_mode,
            lifecycle_state=self.lifecycle_state,
        )
        write_artifact_index(self.run_dir, self.artifact_index)
        self.save_run_manifest(
            {
                "run_mode": self.run_mode,
                "run_mode_bucket": self.run_mode_bucket,
                "execution_mode": self.execution_mode,
                "lifecycle_state": self.lifecycle_state,
                "artifact_layout_version": int(self.artifact_layout_version),
                "artifact_index_path": str(self.artifact_index.get("artifact_index_path", "") or ""),
                "run_name_mode_token": self.run_name_mode_token,
                "run_name_schema_version": int(self.run_name_schema_version),
                "run_label": self.run_label,
                "run_algorithm": self.run_algorithm,
                "run_naming_strategy": self.run_naming_strategy,
                "run_date": self.run_date,
                "run_time": self.run_time,
                "run_timestamp": self.run_timestamp,
                "run_started_at": self.run_started_at,
                "run_sequence": int(self.run_sequence),
            }
        )

        # 创建子文件夹
        self.llm_store = LLMInteractionStore(self.run_dir, run_mode=self.run_mode)
        self.llm_log_dir = self._relative_path_for_scope(self.default_raw_scope, "llm_interactions")

        self.viz_dir = os.path.join(self.run_dir, "visualizations")
        os.makedirs(self.viz_dir, exist_ok=True)

        # 初始化 CSV 统计文件
        self.csv_path = self.get_agent_loop_trace_path() if self.run_mode == "agent_loop" else ""
        self.mass_csv_path = self.get_mass_trace_path()
        if self.run_mode == "agent_loop":
            self._init_csv()
        if self.default_raw_scope in {"mass", "delegated_mass"}:
            self._init_mass_csv()

        # 历史记录
        self.history: List[str] = []

        # 创建 Python logger
        self.logger = get_logger(f"experiment_{self.run_id}", persist_global=False)

        # 添加文件处理器，将日志输出到实验目录的 run_log.txt
        self._add_run_log_handler(self.run_timestamp)

        print(f"Experiment logs: {self.run_dir}")

    def serialize_artifact_path(self, path_value: Any) -> str:
        return serialize_artifact_path(self.base_dir_path, path_value)

    def serialize_run_path(self, path_value: Any) -> str:
        return serialize_run_path(self.run_dir, path_value)

    def _scope_dir(self, scope: str) -> Path:
        normalized = normalize_artifact_scope(scope, default=self.default_raw_scope)
        path = Path(self.run_dir) / scope_relative_root(normalized)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _path_for_scope(self, scope: str, *parts: str) -> str:
        scope_dir = self._scope_dir(scope)
        if not parts:
            return str(scope_dir)
        return str(scope_dir.joinpath(*parts))

    def _relative_path_for_scope(self, scope: str, *parts: str) -> str:
        normalized = normalize_artifact_scope(scope, default=self.default_raw_scope)
        scope_dir = Path(self.run_dir) / scope_relative_root(normalized)
        if not parts:
            return str(scope_dir)
        return str(scope_dir.joinpath(*parts))

    def _default_raw_scope_path(self, *parts: str) -> str:
        return self._path_for_scope(self.default_raw_scope, *parts)

    def _default_llm_mode(self) -> str:
        if self.run_mode == "vop_maas":
            return "delegated_mass"
        return self.run_mode

    def get_agent_loop_trace_path(self) -> str:
        return self._relative_path_for_scope("agent_loop", "evolution_trace.csv")

    def get_mass_trace_path(self) -> str:
        return self._default_raw_scope_path("mass_trace.csv")

    def get_trace_dir(self) -> str:
        return self._default_raw_scope_path("trace")

    def get_snapshots_dir(self) -> str:
        return self._default_raw_scope_path("snapshots")

    def get_step_files_dir(self) -> str:
        return self._default_raw_scope_path("step_files")

    def get_maas_diagnostics_path(self) -> str:
        return self._default_raw_scope_path("maas_diagnostics.jsonl")

    def refresh_artifact_index(self) -> Dict[str, Any]:
        self.artifact_index = build_artifact_index_payload(
            run_dir=self.run_dir,
            run_mode=self.run_mode,
            execution_mode=self.execution_mode,
            lifecycle_state=self.lifecycle_state,
        )
        return write_artifact_index(self.run_dir, self.artifact_index)

    def _enrich_identity_payload(
        self,
        payload: Optional[Dict[str, Any]],
        *,
        producer_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        enriched = dict(payload or {})
        enriched.setdefault("run_mode", self.run_mode)
        enriched.setdefault("execution_mode", self.execution_mode)
        enriched.setdefault("lifecycle_state", self.lifecycle_state)
        enriched.setdefault(
            "producer_mode",
            str(producer_mode or self.execution_mode or self.run_mode),
        )
        enriched.setdefault("artifact_layout_version", int(self.artifact_layout_version))
        return enriched

    def _add_run_log_handler(self, timestamp: str):
        """
        添加文件处理器，将日志输出到实验目录的 run_log.txt

        Args:
            timestamp: 时间戳字符串
        """
        # 创建 run_log.txt 文件路径
        run_log_path = os.path.join(self.run_dir, "run_log.txt")
        class _RunLogCompactFilter(logging.Filter):
            """
            精简 run_log.txt 中高重复、低信息密度的日志。

            设计原则：
            - WARNING/ERROR 一律保留；
            - 高频重复的结构指标明细直接过滤；
            - 关键流程锚点（COMSOL 调用、预算耗尽、审计结论）保留。
            """

            _structural_prefixes = (
                "质心:",
                "几何中心:",
                "质心偏移量:",
                "转动惯量:",
            )

            def filter(self, record: logging.LogRecord) -> bool:
                if record.levelno >= logging.WARNING:
                    return True

                if record.name == "simulation.structural_physics":
                    message = str(record.getMessage() or "")
                    if message.startswith(self._structural_prefixes):
                        return False

                return True

        # Configure the per-run log formatter.
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        root_logger = logging.getLogger()

        # 避免同进程多次初始化时重复挂载本系统 handler 导致日志倍增
        stale_handlers = [
            h for h in list(root_logger.handlers)
            if bool(getattr(h, "_msgalaxy_run_handler", False))
        ]
        for handler in stale_handlers:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        # run_log.txt keeps a compact view for quick diagnosis.
        compact_handler = logging.FileHandler(run_log_path, encoding='utf-8')
        compact_handler.setLevel(logging.INFO)
        compact_handler.setFormatter(formatter)
        compact_handler.addFilter(_RunLogCompactFilter())
        compact_handler._msgalaxy_run_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(compact_handler)

        # 确保根 logger 的级别不会过滤掉 INFO 级别日志
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)

        self.logger.info(
            "[RUN] Run log initialized: %s",
            run_log_path,
        )

    def _init_csv(self):
        """Initialize the agent-loop trace CSV."""
        init_agent_loop_trace_csv(self.csv_path)

    def _init_mass_csv(self):
        """Initialize the mass trace CSV."""
        init_mass_trace_csv(self.mass_csv_path)

    def log_llm_interaction(self, iteration: int, role: str = None, request: Dict[str, Any] = None,
                           response: Dict[str, Any] = None, context_dict: Dict[str, Any] = None,
                           response_dict: Dict[str, Any] = None, mode: Optional[str] = None):
        """
        记录 LLM 交互

        支持两种调用方式：
        1. 新方式: log_llm_interaction(iteration, role, request, response)
        2. 旧方式: log_llm_interaction(iteration, context_dict, response_dict)

        Args:
            iteration: 迭代次数
            role: 角色名称（meta_reasoner, thermal_agent 等）
            request: 请求数据
            response: 响应数据
            context_dict: 输入上下文（旧方式）
            response_dict: LLM 响应（旧方式）
            mode: 可选模式标签（agent_loop/mass/vop_maas/delegated_mass）
        """
        # 兼容旧方式
        if context_dict is not None:
            request = context_dict
        if response_dict is not None:
            response = response_dict

        # 如果没有数据，跳过
        if request is None and response is None:
            return

        prefix = self.llm_store.write(
            iteration=int(iteration),
            role=role,
            request=request,
            response=response,
            mode=mode if mode is not None else self._default_llm_mode(),
        )

        if request is not None or response is not None:
            print(f"  已保存 LLM 交互: {prefix}")

    def log_metrics(self, data: Dict[str, Any]):
        """
        记录迭代指标

        Args:
            data: 指标数据字典
        """
        if self.run_mode != "agent_loop" or not self.csv_path:
            return
        row = materialize_metrics_payload(data)
        append_agent_loop_trace_row(self.csv_path, row)

    def add_history(self, message: str):
        """
        添加历史记录

        Args:
            message: 历史消息
        """
        self.history.append(message)

    def get_recent_history(self, n: int = 3) -> List[str]:
        """
        获取最近的历史记录

        Args:
            n: 返回最近 n 条记录

        Returns:
            历史记录列表
        """
        return self.history[-n:] if len(self.history) >= n else self.history

    def save_design_state(self, iteration: int, design_state: Dict[str, Any]):
        """
        保存设计状态

        Args:
            iteration: 迭代次数
            design_state: 设计状态字典
        """
        state_path = os.path.join(self.run_dir, f"design_state_iter_{iteration:02d}.json")
        with open(state_path, 'w', encoding='utf-8') as f:
            _json_dump_safe(design_state, f)

    @staticmethod
    def _component_diff_lists(
        previous_state: Optional[Dict[str, Any]],
        current_state: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """Summarize component-level deltas between two layout states."""
        prev_components = {
            str(item.get("id", "")): item
            for item in list((previous_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        curr_components = {
            str(item.get("id", "")): item
            for item in list((current_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }

        moved_components: List[str] = []
        added_heatsinks: List[str] = []
        added_brackets: List[str] = []
        changed_contacts: List[str] = []
        changed_coatings: List[str] = []

        for comp_id, curr in curr_components.items():
            prev = prev_components.get(comp_id, {})
            curr_pos = dict(curr.get("position", {}) or {})
            prev_pos = dict(prev.get("position", {}) or {})
            try:
                dx = float(curr_pos.get("x", 0.0)) - float(prev_pos.get("x", 0.0))
                dy = float(curr_pos.get("y", 0.0)) - float(prev_pos.get("y", 0.0))
                dz = float(curr_pos.get("z", 0.0)) - float(prev_pos.get("z", 0.0))
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist > 1e-6:
                    moved_components.append(comp_id)
            except Exception:
                pass

            prev_heatsink = prev.get("heatsink")
            curr_heatsink = curr.get("heatsink")
            if curr_heatsink and curr_heatsink != prev_heatsink:
                added_heatsinks.append(comp_id)

            prev_bracket = prev.get("bracket")
            curr_bracket = curr.get("bracket")
            if curr_bracket and curr_bracket != prev_bracket:
                added_brackets.append(comp_id)

            prev_contacts = dict(prev.get("thermal_contacts", {}) or {})
            curr_contacts = dict(curr.get("thermal_contacts", {}) or {})
            if curr_contacts != prev_contacts:
                changed_contacts.append(comp_id)

            prev_coating = (
                prev.get("coating_type", "default"),
                float(prev.get("emissivity", 0.8) or 0.8),
                float(prev.get("absorptivity", 0.3) or 0.3),
            )
            curr_coating = (
                curr.get("coating_type", "default"),
                float(curr.get("emissivity", 0.8) or 0.8),
                float(curr.get("absorptivity", 0.3) or 0.3),
            )
            if curr_coating != prev_coating:
                changed_coatings.append(comp_id)

        return {
            "moved_components": sorted(moved_components),
            "added_heatsinks": sorted(added_heatsinks),
            "added_brackets": sorted(added_brackets),
            "changed_contacts": sorted(changed_contacts),
            "changed_coatings": sorted(changed_coatings),
        }

    def save_layout_snapshot(
        self,
        *,
        iteration: int,
        attempt: int,
        sequence: int,
        stage: str,
        design_state: Any,
        thermal_source: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        branch_action: str = "",
        branch_source: str = "",
        diagnosis_status: str = "",
        diagnosis_reason: str = "",
        operator_program_id: str = "",
        operator_actions: Optional[List[str]] = None,
        previous_design_state: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a layout snapshot and emit the paired layout event."""
        snapshots_dir = self._default_raw_scope_path("snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)

        stage_token = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_"
            for ch in str(stage or "snapshot")
        ).strip("_") or "snapshot"
        snapshot_name = (
            f"seq_{int(sequence):04d}_iter_{int(iteration):02d}_"
            f"attempt_{int(attempt):02d}_{stage_token}.json"
        )
        snapshot_path = os.path.join(snapshots_dir, snapshot_name)

        if hasattr(design_state, "model_dump"):
            state_payload = design_state.model_dump()
        elif isinstance(design_state, dict):
            state_payload = dict(design_state)
        else:
            state_payload = {}

        prev_payload: Optional[Dict[str, Any]] = None
        if previous_design_state is not None:
            if hasattr(previous_design_state, "model_dump"):
                prev_payload = previous_design_state.model_dump()
            elif isinstance(previous_design_state, dict):
                prev_payload = dict(previous_design_state)
            else:
                prev_payload = None

        delta = self._component_diff_lists(prev_payload, state_payload)
        payload = {
            "run_id": self.run_id,
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "timestamp": datetime.now().isoformat(),
            "run_mode": self.run_mode,
            "execution_mode": self.execution_mode,
            "lifecycle_state": self.lifecycle_state,
            "sequence": int(sequence),
            "iteration": int(iteration),
            "attempt": int(attempt),
            "stage": str(stage or ""),
            "thermal_source": str(thermal_source or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "metrics": dict(metrics or {}),
            "delta": dict(delta),
            "metadata": dict(metadata or {}),
            "design_state": state_payload,
        }

        with open(snapshot_path, "w", encoding="utf-8") as f:
            _json_dump_safe(payload, f)

        event_payload = self._enrich_identity_payload(
            {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "snapshot_path": self.serialize_run_path(snapshot_path),
            "thermal_source": str(thermal_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "moved_components": list(delta.get("moved_components", [])),
            "added_heatsinks": list(delta.get("added_heatsinks", [])),
            "added_brackets": list(delta.get("added_brackets", [])),
            "changed_contacts": list(delta.get("changed_contacts", [])),
            "changed_coatings": list(delta.get("changed_coatings", [])),
            "metrics": dict(metrics or {}),
            "metadata": dict(metadata or {}),
            },
            producer_mode=self.execution_mode,
        )
        self.event_logger.append_layout_event(event_payload)
        return {"snapshot_path": snapshot_path, "event": event_payload, "delta": delta}

    def save_visualization(self, iteration: int, fig_name: str, fig):
        """Persist a generated visualization figure."""
        viz_path = os.path.join(self.viz_dir, f"iter_{iteration:02d}_{fig_name}.png")
        fig.savefig(viz_path, dpi=150, bbox_inches='tight')
        print(f"  已保存可视化: {fig_name}")

    def save_summary(
        self,
        status: str,
        final_iteration: int,
        notes: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ):
        """Persist the run summary and refresh the run manifest."""
        summary = {
            "status": status,
            "final_iteration": final_iteration,
            "timestamp": datetime.now().isoformat(),
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "execution_mode": self.execution_mode,
            "lifecycle_state": self.lifecycle_state,
            "artifact_layout_version": int(self.artifact_layout_version),
            "artifact_index_path": str(self.artifact_index.get("artifact_index_path", "") or ""),
            "run_name_mode_token": self.run_name_mode_token,
            "run_name_schema_version": int(self.run_name_schema_version),
            "delegated_execution_mode": (
                self.execution_mode if self.run_mode != self.execution_mode else ""
            ),
            "run_label": self.run_label,
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "notes": notes
        }
        if extra:
            summary.update(extra)

        summary_path = os.path.join(self.run_dir, "summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            _json_dump_safe(summary, f)

        # 生成 Markdown 报告
        self._generate_markdown_report(summary)

        # 同步更新 run manifest
        self.save_run_manifest(
            {
                "status": str(status),
                "final_iteration": int(final_iteration),
                "extra": dict(extra or {}),
            }
        )

    def _write_latest_index(self, manifest_payload: Dict[str, Any]) -> None:
        latest_path = Path(self.latest_index_path)
        latest_path.parent.mkdir(parents=True, exist_ok=True)

        existing: Dict[str, Any] = {}
        if latest_path.exists():
            try:
                existing = json.loads(latest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}

        existing_started_at = str(existing.get("run_started_at", "") or "").strip()
        existing_run_id = str(existing.get("run_id", "") or "").strip()
        if (
            existing_started_at
            and existing_run_id
            and existing_run_id != self.run_id
            and existing_started_at > self.run_started_at
        ):
            return

        extra = dict(manifest_payload.get("extra", {}) or {})

        def _latest_artifact_path(raw_value: Any) -> str:
            text = str(raw_value or "").strip()
            if not text:
                return ""
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = Path(self.run_dir) / candidate
            return self.serialize_artifact_path(candidate)

        latest_payload = {
            "run_id": self.run_id,
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_leaf_dir": Path(self.run_dir).name,
            "run_date_dir": Path(self.run_dir).parent.name,
            "run_label": self.run_label,
            "run_mode": self.run_mode,
            "execution_mode": self.execution_mode,
            "lifecycle_state": self.lifecycle_state,
            "artifact_layout_version": int(self.artifact_layout_version),
            "artifact_index_path": str(self.artifact_index.get("artifact_index_path", "") or ""),
            "run_name_mode_token": self.run_name_mode_token,
            "run_name_schema_version": int(self.run_name_schema_version),
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "optimization_mode": str(manifest_payload.get("optimization_mode", "") or ""),
            "pymoo_algorithm": str(manifest_payload.get("pymoo_algorithm", "") or ""),
            "thermal_evaluator_mode": str(manifest_payload.get("thermal_evaluator_mode", "") or ""),
            "search_space_mode": str(manifest_payload.get("search_space_mode", "") or ""),
            "profile": str(manifest_payload.get("profile", "") or ""),
            "level": str(manifest_payload.get("level", "") or ""),
            "seed": manifest_payload.get("seed"),
            "status": str(manifest_payload.get("status", "") or ""),
            "diagnosis_status": str(extra.get("diagnosis_status", "") or ""),
            "diagnosis_reason": str(extra.get("diagnosis_reason", "") or ""),
            "summary_path": self.serialize_artifact_path(Path(self.run_dir) / "summary.json"),
            "manifest_path": self.serialize_artifact_path(
                Path(self.run_dir) / "events" / "run_manifest.json"
            ),
            "mass_final_summary_zh_path": _latest_artifact_path(
                extra.get("mass_final_summary_zh_path")
                or manifest_payload.get("mass_final_summary_zh_path")
            ),
            "mass_final_summary_digest_path": _latest_artifact_path(
                extra.get("mass_final_summary_digest_path")
                or manifest_payload.get("mass_final_summary_digest_path")
            ),
            "llm_final_summary_zh_path": _latest_artifact_path(
                extra.get("llm_final_summary_zh_path")
                or manifest_payload.get("llm_final_summary_zh_path")
            ),
            "llm_final_summary_digest_path": _latest_artifact_path(
                extra.get("llm_final_summary_digest_path")
                or manifest_payload.get("llm_final_summary_digest_path")
            ),
            "updated_at": datetime.now().isoformat(),
        }
        with latest_path.open("w", encoding="utf-8") as f:
            _json_dump_safe(latest_payload, f)

    def save_run_manifest(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Update the per-run manifest event payload."""
        data = dict(payload or {})
        data.setdefault("run_id", self.run_id)
        data["run_dir"] = self.serialize_artifact_path(data.get("run_dir", self.run_dir))
        data.setdefault("run_mode", self.run_mode)
        data.setdefault("run_mode_bucket", self.run_mode_bucket)
        data.setdefault("execution_mode", self.execution_mode)
        data.setdefault("lifecycle_state", self.lifecycle_state)
        data.setdefault("artifact_layout_version", int(self.artifact_layout_version))
        data.setdefault("artifact_index_path", str(self.artifact_index.get("artifact_index_path", "") or ""))
        data.setdefault("run_name_mode_token", self.run_name_mode_token)
        data.setdefault("run_name_schema_version", int(self.run_name_schema_version))
        data.setdefault(
            "delegated_execution_mode",
            self.execution_mode if self.run_mode != self.execution_mode else "",
        )
        data.setdefault("run_label", self.run_label)
        data.setdefault("run_algorithm", self.run_algorithm)
        data.setdefault("run_naming_strategy", self.run_naming_strategy)
        data.setdefault("run_date", self.run_date)
        data.setdefault("run_time", self.run_time)
        data.setdefault("run_timestamp", self.run_timestamp)
        data.setdefault("run_started_at", self.run_started_at)
        data.setdefault("run_sequence", int(self.run_sequence))
        manifest = self.event_logger.write_run_manifest(data)
        self._write_latest_index(manifest)
        return manifest

    @staticmethod
    def _stringify_log_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            try:
                rendered = json.dumps(
                    _sanitize_json_value(value),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            except Exception:
                rendered = str(value).strip()
            if len(rendered) > 180:
                return rendered[:177] + "..."
            return rendered
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return ",".join(items[:6])
        return str(value).strip()

    def _emit_run_log_milestone(self, tag: str, message: str, **fields: Any) -> None:
        details = []
        for key, raw_value in fields.items():
            value = self._stringify_log_value(raw_value)
            if not value:
                continue
            details.append(f"{key}={value}")
        suffix = f" | {', '.join(details)}" if details else ""
        self.logger.info("%s %s%s", str(tag or "").strip(), str(message or "").strip(), suffix)

    def log_maas_phase_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS phase event."""
        try:
            payload = self._enrich_identity_payload(
                dict(data or {}),
                producer_mode=str(dict(data or {}).get("producer_mode", "") or self.execution_mode),
            )
            self.event_logger.append_phase_event(payload)
            phase = str(payload.get("phase", "") or "").strip()
            status = str(payload.get("status", "") or "").strip()
            producer_mode = str(payload.get("producer_mode", "") or "").strip()
            phase_family = str(payload.get("phase_family", "") or "").strip()
            details = dict(payload.get("details", {}) or {})
            if phase in {"A", "B", "C", "D"}:
                self._emit_run_log_milestone(
                    f"[MASS][{phase}]",
                    status or "event",
                    step=details.get("step"),
                    iteration=payload.get("iteration"),
                    attempt=payload.get("attempt"),
                    stage=payload.get("stage"),
                )
            elif producer_mode == "vop_maas" or phase_family == "vop_maas" or phase.startswith("V"):
                stage = str(payload.get("stage", "") or "").strip().lower()
                tag = "[VOP][DECISION]"
                if stage == "bootstrap":
                    tag = "[VOP][BOOTSTRAP]"
                elif "reflective" in stage:
                    tag = "[VOP][REPLAN]"
                self._emit_run_log_milestone(
                    tag,
                    status or "event",
                    round_index=payload.get("round_index"),
                    stage=payload.get("stage"),
                    policy_id=payload.get("policy_id"),
                    previous_policy_id=payload.get("previous_policy_id"),
                )
        except Exception as exc:
            self.logger.debug("maas phase event write failed: %s", exc)

    def log_maas_policy_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS policy event."""
        try:
            payload = self._enrich_identity_payload(
                dict(data or {}),
                producer_mode=str(dict(data or {}).get("producer_mode", "") or self.execution_mode),
            )
            self.event_logger.append_policy_event(payload)
            if str(payload.get("producer_mode", "") or "").strip() == "vop_maas":
                self._emit_run_log_milestone(
                    "[VOP][DECISION]",
                    "policy recorded",
                    round_index=payload.get("round_index"),
                    stage=payload.get("stage"),
                    policy_id=payload.get("policy_id"),
                    search_space=(
                        dict(payload.get("metadata", {}) or {}).get("search_space_prior")
                        or payload.get("search_space_override")
                    ),
                    program=payload.get("selected_operator_program_id"),
                    actions=payload.get("actions"),
                    rationale=payload.get("decision_rationale"),
                    changes=payload.get("change_summary"),
                    expected=payload.get("expected_effects"),
                    confidence=payload.get("confidence"),
                )
        except Exception as exc:
            self.logger.debug("maas policy event write failed: %s", exc)

    def log_maas_generation_events(self, data: Dict[str, Any]) -> None:
        """Append MaaS generation-level events."""
        payload = dict(data or {})
        records = list(payload.get("records", []) or [])
        if not records:
            return

        iteration = int(payload.get("iteration", 0) or 0)
        attempt = int(payload.get("attempt", 0) or 0)
        branch_action = str(payload.get("branch_action", ""))
        branch_source = str(payload.get("branch_source", ""))
        search_space_mode = str(payload.get("search_space_mode", ""))
        pymoo_algorithm = str(payload.get("pymoo_algorithm", ""))

        for item in records:
            try:
                record = dict(item or {})
                self.event_logger.append_generation_event(
                    self._enrich_identity_payload(
                        {
                            "iteration": iteration,
                            "attempt": attempt,
                            "generation": int(record.get("generation", 0) or 0),
                            "pymoo_algorithm": pymoo_algorithm,
                            "branch_action": branch_action,
                            "branch_source": branch_source,
                            "search_space_mode": search_space_mode,
                            "population_size": int(record.get("population_size", 0) or 0),
                            "feasible_count": int(record.get("feasible_count", 0) or 0),
                            "feasible_ratio": record.get("feasible_ratio"),
                            "best_cv": record.get("best_cv"),
                            "mean_cv": record.get("mean_cv"),
                            "best_feasible_sum_f": record.get("best_feasible_sum_f"),
                        },
                        producer_mode=str(record.get("producer_mode", "") or self.execution_mode),
                    )
                )
            except Exception as exc:
                self.logger.debug("maas generation event write failed: %s", exc)

    def log_maas_physics_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS physics event."""
        try:
            payload = self._enrich_identity_payload(
                dict(data or {}),
                producer_mode=str(dict(data or {}).get("producer_mode", "") or self.execution_mode),
            )
            self.event_logger.append_physics_event(payload)
        except Exception as exc:
            self.logger.debug("maas physics event write failed: %s", exc)

    def log_mass_trace(self, data: Dict[str, Any]):
        """Record a mass attempt-level trace row."""
        materialized = materialize_trace_payload(dict(data or {}))
        append_mass_trace_row(
            self.mass_csv_path,
            list(materialized.get("row", []) or []),
        )

        attempt_event_payload = self._enrich_identity_payload(
            dict(materialized.get("attempt_event_payload", {}) or {}),
            producer_mode="mass",
        )
        candidate_event_payload = materialized.get("candidate_event_payload", None)
        if isinstance(candidate_event_payload, dict):
            candidate_event_payload = self._enrich_identity_payload(
                dict(candidate_event_payload),
                producer_mode="mass",
            )
        is_best_attempt = bool(materialized.get("is_best_attempt", False))
        try:
            if not is_best_attempt:
                self.event_logger.append_attempt_event(attempt_event_payload)
            elif isinstance(candidate_event_payload, dict):
                self.event_logger.append_candidate_event(dict(candidate_event_payload))
        except Exception as exc:
            self.logger.debug("maas attempt/candidate event write failed: %s", exc)

    def _generate_markdown_report(self, summary: Dict[str, Any]):
        """Generate the markdown summary report."""
        write_markdown_report(
            run_dir=self.run_dir,
            summary=summary,
            active_buckets=self.llm_store.get_active_buckets(),
        )
        print("  已生成报告: report.md")

    def append_llm_final_summary_report_section(
        self,
        summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        report_path = Path(self.run_dir) / "report.md"
        if not report_path.exists():
            return
        summary_payload = dict(summary or _load_run_summary_payload(self.run_dir) or {})
        if not summary_payload:
            return
        block = build_llm_final_summary_report_block(self.run_dir, summary_payload)
        if not block:
            return
        try:
            content = report_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.logger.warning("llm final summary report enrichment failed: %s", exc)
            return
        updated = _upsert_markdown_block(
            content,
            block,
            "<!-- LLM_FINAL_SUMMARY_ZH:START -->",
            "<!-- LLM_FINAL_SUMMARY_ZH:END -->",
        )
        report_path.write_text(updated, encoding="utf-8")

    # ============ Phase 4: Trace 审计日志 ============

    def save_trace_data(
        self,
        iteration: int,
        context_pack: Optional[Dict[str, Any]] = None,
        strategic_plan: Optional[Dict[str, Any]] = None,
        eval_result: Optional[Dict[str, Any]] = None
    ):
        """
        保存完整的 Trace 审计数据（Phase 4）

        Args:
            iteration: 迭代次数
            context_pack: 输入给 LLM 的上下文包
            strategic_plan: LLM 的战略计划输出
            eval_result: 物理仿真的评估结果
        """
        # 创建 trace 子目录
        trace_dir = self._default_raw_scope_path("trace")
        os.makedirs(trace_dir, exist_ok=True)

        prefix = f"iter_{iteration:02d}"

        # 保存 ContextPack
        if context_pack is not None:
            context_path = os.path.join(trace_dir, f"{prefix}_context.json")
            with open(context_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(context_pack, f)

        # 保存 StrategicPlan
        if strategic_plan is not None:
            plan_path = os.path.join(trace_dir, f"{prefix}_plan.json")
            with open(plan_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(strategic_plan, f)

        # 保存 EvalResult
        if eval_result is not None:
            eval_path = os.path.join(trace_dir, f"{prefix}_eval.json")
            with open(eval_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(eval_result, f)

        self.logger.info(f"  已保存 Trace 数据: {prefix}")

    def save_maas_diagnostic_event(
        self,
        iteration: int,
        attempt: int,
        payload: Dict[str, Any],
    ) -> None:
        """
        记录 MaaS 闭环每次求解尝试的诊断事件（JSONL）。

        Args:
            iteration: 外层优化迭代编号
            attempt: MaaS 内部第几次建模/求解尝试（从 1 开始）
            payload: 任意可序列化诊断信息
        """
        log_path = self._default_raw_scope_path("maas_diagnostics.jsonl")
        event = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "timestamp": datetime.now().isoformat(),
            "payload": payload,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json_dumps_safe(event) + "\n")
        self.logger.info(f"  已保存 MaaS 诊断: iter={iteration}, attempt={attempt}")

    def save_rollback_event(
        self,
        iteration: int,
        rollback_reason: str,
        from_state_id: str,
        to_state_id: str,
        penalty_before: float,
        penalty_after: float
    ):
        """
        记录回退事件（Phase 4）

        Args:
            iteration: 触发回退的迭代次数
            rollback_reason: 回退原因
            from_state_id: 回退前的状态 ID
            to_state_id: 回退后的状态 ID
            penalty_before: 回退前的惩罚值
            penalty_after: 回退后的惩罚值
        """
        rollback_log_path = os.path.join(self.run_dir, "rollback_events.jsonl")

        event = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "reason": rollback_reason,
            "from_state": from_state_id,
            "to_state": to_state_id,
            "penalty_before": penalty_before,
            "penalty_after": penalty_after
        }

        # 追加到 JSONL 文件
        with open(rollback_log_path, 'a', encoding='utf-8') as f:
            f.write(_json_dumps_safe(event) + '\n')

        self.logger.warning(f"  已记录回退事件: {from_state_id} -> {to_state_id}")


def get_logger(name: str, *, persist_global: Optional[bool] = None) -> Any:
    """
    Get a configured Python logger.

    Args:
        name: logger name
        persist_global: whether to also write `logs/<name>.log`

    Returns:
        logging.Logger object
    """
    import logging

    if persist_global is None:
        persist_global = _should_persist_global_log(name)

    logger = logging.getLogger(name)
    console_handlers = [
        handler for handler in logger.handlers
        if bool(getattr(handler, "_msgalaxy_console_handler", False))
    ]
    global_file_handlers = [
        handler for handler in logger.handlers
        if bool(getattr(handler, "_msgalaxy_global_file_handler", False))
    ]

    if not console_handlers:
        import sys

        console_handler = logging.StreamHandler(sys.stdout)
        if hasattr(console_handler.stream, "reconfigure"):
            console_handler.stream.reconfigure(encoding="utf-8")
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        console_handler._msgalaxy_console_handler = True  # type: ignore[attr-defined]
        logger.addHandler(console_handler)

    if persist_global and not global_file_handlers:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        file_handler._msgalaxy_global_file_handler = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)
    elif not persist_global and global_file_handlers:
        for handler in global_file_handlers:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    logger.setLevel(logging.DEBUG)
    return logger

def log_exception(logger, exception: Exception, context: str = ""):
    """
    记录异常详情

    Args:
        logger: 日志记录器
        exception: 异常对象
        context: 上下文信息
    """
    import traceback

    error_msg = f"Exception in {context}: {type(exception).__name__}: {str(exception)}"
    logger.error(error_msg)
    logger.debug(traceback.format_exc())








