"""
Iteration review summary bridge helpers shared by run artifacts and sidecars.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from core.path_policy import resolve_repo_path, serialize_repo_path


_REPORT_BLOCK_START = "<!-- ITERATION_REVIEW_SUMMARY:START -->"
_REPORT_BLOCK_END = "<!-- ITERATION_REVIEW_SUMMARY:END -->"
_VISUALIZATION_BLOCK_START = "=== Iteration Review Field-Case Mapping ==="
_VISUALIZATION_BLOCK_END = "=== End Iteration Review Field-Case Mapping ==="


def report_block_markers() -> tuple[str, str]:
    return _REPORT_BLOCK_START, _REPORT_BLOCK_END


def visualization_block_markers() -> tuple[str, str]:
    return _VISUALIZATION_BLOCK_START, _VISUALIZATION_BLOCK_END


def upsert_text_block(content: str, block: str, *, start_marker: str, end_marker: str) -> str:
    if not block:
        return content
    if start_marker in content and end_marker in content:
        prefix, remainder = content.split(start_marker, 1)
        _, suffix = remainder.split(end_marker, 1)
        return f"{prefix}{block}{suffix.lstrip()}"
    separator = "" if content.endswith("\n") or not content else "\n"
    return f"{content}{separator}\n{block}" if content else f"{block}\n"


def _load_optional_json(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _resolve_repo_json_path(raw_path: Any) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    try:
        return resolve_repo_path(text)
    except Exception:
        return None


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def _normalize_family_counts(payload: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for key, value in dict(payload or {}).items():
        family = str(key or "").strip()
        if not family:
            continue
        try:
            counts[family] = int(value)
        except Exception:
            counts[family] = 0
    return counts


def _normalize_family_labels(payload: Any) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for key, value in dict(payload or {}).items():
        family = str(key or "").strip()
        if not family:
            continue
        labels[family] = str(value or "").strip()
    return labels


def _normalize_field_case_gate(payload: Any, *, profile_name: str) -> Dict[str, Any]:
    gate_payload = dict(payload or {})
    violations = [
        dict(item)
        for item in list(gate_payload.get("violations", []) or [])
        if isinstance(item, dict)
    ]
    mode = str(gate_payload.get("mode", "") or "off").strip() or "off"
    active = bool(gate_payload.get("active", False))
    passed = bool(gate_payload.get("passed", True))
    status = str(gate_payload.get("status", "") or "").strip()
    if not status:
        if mode == "off":
            status = "off"
        elif not active:
            status = "not_applicable"
        else:
            status = "passed" if passed else "blocked"
    enforcement_action = str(gate_payload.get("enforcement_action", "") or "").strip()
    if not enforcement_action:
        enforcement_action = "allow" if passed else "skip_profile_packages"
    return {
        "schema_version": str(
            gate_payload.get("schema_version", "") or "iteration_review_profile_field_case_gate/v1"
        ),
        "profile_name": str(gate_payload.get("profile_name", "") or profile_name),
        "mode": mode,
        "active": active,
        "passed": passed,
        "status": status,
        "enforcement_action": enforcement_action,
        "allowed_resolution_sources": [
            str(item).strip()
            for item in list(gate_payload.get("allowed_resolution_sources", []) or [])
            if str(item).strip()
        ],
        "observed_resolution_sources": [
            str(item).strip()
            for item in list(gate_payload.get("observed_resolution_sources", []) or [])
            if str(item).strip()
        ],
        "violation_count": int(gate_payload.get("violation_count", len(violations)) or 0),
        "violations": violations,
        "reason": str(gate_payload.get("reason", "") or ""),
        "notes": [
            str(item)
            for item in list(gate_payload.get("notes", []) or [])
            if str(item).strip()
        ],
    }


def _summarize_operator_family_audit(profile_index: Mapping[str, Any]) -> Dict[str, Any]:
    packages = [
        dict(item)
        for item in list(dict(profile_index or {}).get("packages", []) or [])
        if isinstance(item, dict)
    ]
    audit_payload = dict(dict(profile_index or {}).get("operator_family_audit", {}) or {})

    if not audit_payload and packages:
        primary_family_counts: Dict[str, int] = {}
        primary_family_labels: Dict[str, str] = {}
        action_family_counts: Dict[str, int] = {}
        action_family_labels: Dict[str, str] = {}
        unmapped_actions: set[str] = set()
        package_steps_with_unmapped_actions: list[int] = []
        family_contract_warning_count = 0

        for package in packages:
            primary_family = str(package.get("primary_action_family", "") or "").strip()
            primary_family_label = str(package.get("primary_action_family_label", "") or "").strip()
            if primary_family:
                primary_family_counts[primary_family] = int(primary_family_counts.get(primary_family, 0) or 0) + 1
                if primary_family_label:
                    primary_family_labels[primary_family] = primary_family_label

            for family in [
                str(item).strip()
                for item in list(package.get("action_family_sequence", []) or [])
                if str(item).strip()
            ]:
                action_family_counts[family] = int(action_family_counts.get(family, 0) or 0) + 1
                action_family_labels.setdefault(family, family)

            entry_unmapped_actions = [
                str(item).strip()
                for item in list(package.get("unmapped_actions", []) or [])
                if str(item).strip()
            ]
            if entry_unmapped_actions:
                package_steps_with_unmapped_actions.append(int(package.get("step_index", 0) or 0))
                unmapped_actions.update(entry_unmapped_actions)

            family_contract_warning_count += len(
                [
                    str(item).strip()
                    for item in list(package.get("family_contract_warnings", []) or [])
                    if str(item).strip()
                ]
            )

        dominant_primary_family = ""
        dominant_primary_family_label = ""
        if primary_family_counts:
            dominant_primary_family = sorted(
                primary_family_counts.items(),
                key=lambda item: (-int(item[1]), item[0]),
            )[0][0]
            dominant_primary_family_label = str(
                primary_family_labels.get(dominant_primary_family, "") or dominant_primary_family
            )

        audit_payload = {
            "dominant_primary_family": dominant_primary_family,
            "dominant_primary_family_label": dominant_primary_family_label,
            "primary_family_counts": primary_family_counts,
            "primary_family_labels": primary_family_labels,
            "action_family_counts": action_family_counts,
            "action_family_labels": action_family_labels,
            "unmapped_actions": sorted(unmapped_actions),
            "unmapped_action_count": int(len(unmapped_actions)),
            "package_steps_with_unmapped_actions": sorted(set(package_steps_with_unmapped_actions)),
            "family_contract_warning_count": int(family_contract_warning_count),
        }

    primary_family_counts = _normalize_family_counts(audit_payload.get("primary_family_counts", {}))
    primary_family_labels = _normalize_family_labels(audit_payload.get("primary_family_labels", {}))
    action_family_counts = _normalize_family_counts(audit_payload.get("action_family_counts", {}))
    action_family_labels = _normalize_family_labels(audit_payload.get("action_family_labels", {}))
    dominant_primary_family = str(audit_payload.get("dominant_primary_family", "") or "").strip()
    dominant_primary_family_label = str(audit_payload.get("dominant_primary_family_label", "") or "").strip()
    if dominant_primary_family and dominant_primary_family not in primary_family_labels:
        primary_family_labels[dominant_primary_family] = dominant_primary_family_label or dominant_primary_family
    if not dominant_primary_family and primary_family_counts:
        dominant_primary_family = sorted(
            primary_family_counts.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )[0][0]
        dominant_primary_family_label = str(
            primary_family_labels.get(dominant_primary_family, "") or dominant_primary_family
        )

    unmapped_actions = [
        str(item).strip()
        for item in list(audit_payload.get("unmapped_actions", []) or [])
        if str(item).strip()
    ]
    package_steps_with_unmapped_actions = [
        int(item)
        for item in list(audit_payload.get("package_steps_with_unmapped_actions", []) or [])
        if str(item).strip()
    ]

    return {
        "dominant_primary_family": dominant_primary_family,
        "dominant_primary_family_label": dominant_primary_family_label,
        "primary_family_counts": primary_family_counts,
        "primary_family_labels": primary_family_labels,
        "action_family_counts": action_family_counts,
        "action_family_labels": action_family_labels,
        "unmapped_actions": unmapped_actions,
        "unmapped_action_count": int(audit_payload.get("unmapped_action_count", len(unmapped_actions)) or 0),
        "package_steps_with_unmapped_actions": package_steps_with_unmapped_actions,
        "family_contract_warning_count": int(audit_payload.get("family_contract_warning_count", 0) or 0),
    }


def _format_operator_family_counts(audit_payload: Mapping[str, Any], *, count_key: str, label_key: str) -> str:
    counts = _normalize_family_counts(dict(audit_payload or {}).get(count_key, {}))
    labels = _normalize_family_labels(dict(audit_payload or {}).get(label_key, {}))
    if not counts:
        return "n/a"
    rendered = [
        f"{str(labels.get(family, '') or family)}:{int(count)}"
        for family, count in sorted(
            counts.items(),
            key=lambda item: (-int(item[1]), str(labels.get(item[0], "") or item[0])),
        )
    ]
    return ", ".join(rendered)


def _format_keyframe_caption_preview(profile_payload: Mapping[str, Any]) -> str:
    aggregate_outputs = dict(dict(profile_payload or {}).get("aggregate_outputs", {}) or {})
    keyframe_payload = dict(aggregate_outputs.get("keyframe_montage", {}) or {})
    items = [
        dict(item)
        for item in list(keyframe_payload.get("items", []) or [])
        if isinstance(item, dict)
    ]
    labels = [str(item.get("label", "") or "").strip() for item in items if str(item.get("label", "") or "").strip()]
    if not labels:
        return "n/a"
    return " | ".join(labels[:3])


def _summarize_iteration_review_profile(profile_name: str, profile_index: Mapping[str, Any]) -> Dict[str, Any]:
    packages = [
        dict(item)
        for item in list(dict(profile_index or {}).get("packages", []) or [])
        if isinstance(item, dict)
    ]
    aggregate_outputs = dict(dict(profile_index or {}).get("aggregate_outputs", {}) or {})
    return {
        "review_profile": profile_name,
        "index_path": str(dict(profile_index or {}).get("index_path", "") or ""),
        "package_count": int(dict(profile_index or {}).get("package_count", len(packages)) or 0),
        "linked_field_asset_count": sum(
            1 for item in packages if str(item.get("package_status", "") or "") == "linked_field_assets"
        ),
        "lightweight_manifest_count": sum(
            1 for item in packages if str(item.get("package_status", "") or "") == "lightweight_manifest"
        ),
        "triptych_count": sum(1 for item in packages if str(item.get("triptych_path", "") or "").strip()),
        "step_montage_count": sum(1 for item in packages if str(item.get("step_montage_path", "") or "").strip()),
        "field_case_linked_count": sum(1 for item in packages if str(item.get("field_case_dir", "") or "").strip()),
        "field_case_gate": _normalize_field_case_gate(
            dict(profile_index or {}).get("field_case_gate", {}),
            profile_name=profile_name,
        ),
        "operator_family_audit": _summarize_operator_family_audit(profile_index),
        "aggregate_outputs": {
            key: {
                "path": str(dict(value or {}).get("path", "") or ""),
                "exists": bool(dict(value or {}).get("exists", False)),
                "items": [
                    dict(item)
                    for item in list(dict(value or {}).get("items", []) or [])
                    if isinstance(item, dict)
                ],
                "notes": [
                    str(note)
                    for note in list(dict(value or {}).get("notes", []) or [])
                    if str(note).strip()
                ],
            }
            for key, value in aggregate_outputs.items()
        },
    }


def build_iteration_review_summary_from_paths(
    *,
    root_index_path_text: str,
    teacher_demo_review_index_path: str = "",
    research_fast_review_index_path: str = "",
) -> Dict[str, Any]:
    root_index_path = _resolve_repo_json_path(root_index_path_text)
    if root_index_path is None or not root_index_path.exists():
        return {
            "schema_version": "iteration_review_payload_digest/v1",
            "status": "unavailable",
            "root_index_path": root_index_path_text,
            "profiles": {},
            "notes": ["Iteration review index is not available for this run."],
        }

    root_index = _load_optional_json(root_index_path)
    profiles_summary: Dict[str, Any] = {}
    explicit_paths = {
        "teacher_demo": str(teacher_demo_review_index_path or ""),
        "research_fast": str(research_fast_review_index_path or ""),
    }

    for profile_name in ("teacher_demo", "research_fast"):
        profile_index_path_text = explicit_paths.get(profile_name, "")
        if not profile_index_path_text:
            profile_index_path_text = str(
                dict(dict(root_index.get("profiles", {}) or {}).get(profile_name, {}) or {}).get("index_path", "")
                or ""
            )
        profile_index_path = _resolve_repo_json_path(profile_index_path_text)
        profile_index = _load_optional_json(profile_index_path)
        if profile_index:
            profile_index["index_path"] = profile_index_path_text
            profiles_summary[profile_name] = _summarize_iteration_review_profile(profile_name, profile_index)
            continue
        profiles_summary[profile_name] = {
            "review_profile": profile_name,
            "index_path": profile_index_path_text,
            "package_count": 0,
            "linked_field_asset_count": 0,
            "lightweight_manifest_count": 0,
            "triptych_count": 0,
            "step_montage_count": 0,
            "field_case_linked_count": 0,
            "field_case_gate": _normalize_field_case_gate({}, profile_name=profile_name),
            "operator_family_audit": {
                "dominant_primary_family": "",
                "dominant_primary_family_label": "",
                "primary_family_counts": {},
                "primary_family_labels": {},
                "action_family_counts": {},
                "action_family_labels": {},
                "unmapped_actions": [],
                "unmapped_action_count": 0,
                "package_steps_with_unmapped_actions": [],
                "family_contract_warning_count": 0,
            },
            "aggregate_outputs": {},
        }

    return {
        "schema_version": "iteration_review_payload_digest/v1",
        "status": "available",
        "root_index_path": root_index_path_text,
        "output_root": str(root_index.get("output_root", "") or ""),
        "run_id": str(root_index.get("run_id", "") or ""),
        "package_dir_pattern": str(root_index.get("package_dir_pattern", "") or ""),
        "field_case_mapping": dict(root_index.get("field_case_mapping", {}) or {}),
        "profiles": profiles_summary,
        "notes": [str(note) for note in list(root_index.get("notes", []) or []) if str(note).strip()],
    }


def load_iteration_review_summary_for_run(
    run_dir: str | Path,
    *,
    summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_payload = dict(summary or {})
    existing = dict(summary_payload.get("iteration_review_summary", {}) or {})

    root_index_path_text = str(summary_payload.get("iteration_review_index_path", "") or "").strip()
    if not root_index_path_text:
        root_index_path_text = str(existing.get("root_index_path", "") or "").strip()
    if not root_index_path_text:
        default_root_index = run_path / "visualizations" / "review_packages" / "index.json"
        if default_root_index.exists():
            root_index_path_text = serialize_repo_path(default_root_index)

    teacher_demo_review_index_path = str(
        summary_payload.get("iteration_review_teacher_demo_index_path", "") or ""
    ).strip()
    if not teacher_demo_review_index_path:
        teacher_demo_review_index_path = str(
            summary_payload.get("teacher_demo_review_index_path", "") or ""
        ).strip()

    research_fast_review_index_path = str(
        summary_payload.get("iteration_review_research_fast_index_path", "") or ""
    ).strip()
    if not research_fast_review_index_path:
        research_fast_review_index_path = str(
            summary_payload.get("research_fast_review_index_path", "") or ""
        ).strip()

    if root_index_path_text:
        return build_iteration_review_summary_from_paths(
            root_index_path_text=root_index_path_text,
            teacher_demo_review_index_path=teacher_demo_review_index_path,
            research_fast_review_index_path=research_fast_review_index_path,
        )

    if existing:
        return existing
    return {
        "schema_version": "iteration_review_payload_digest/v1",
        "status": "unavailable",
        "root_index_path": "",
        "profiles": {},
        "notes": ["Iteration review index is not available for this run."],
    }


def build_iteration_review_audit_digest(iteration_review_summary: Mapping[str, Any] | None) -> Dict[str, Any]:
    summary_payload = dict(iteration_review_summary or {})
    mapping_payload = dict(summary_payload.get("field_case_mapping", {}) or {})
    profiles_payload = dict(summary_payload.get("profiles", {}) or {})

    digest: Dict[str, Any] = {
        "schema_version": "iteration_review_audit_digest/v1",
        "status": str(summary_payload.get("status", "") or ""),
        "root_index_path": str(summary_payload.get("root_index_path", "") or ""),
        "field_case_mapping": {
            "mapping_source": str(mapping_payload.get("mapping_source", "") or ""),
            "dataset_root": str(mapping_payload.get("dataset_root", "") or ""),
            "default_case_dir": str(mapping_payload.get("default_case_dir", "") or ""),
            "mapped_step_count": int(mapping_payload.get("mapped_step_count", 0) or 0),
            "matched_step_count": int(mapping_payload.get("matched_step_count", 0) or 0),
            "defaulted_step_count": int(mapping_payload.get("defaulted_step_count", 0) or 0),
            "unmapped_step_count": int(mapping_payload.get("unmapped_step_count", 0) or 0),
            "expected_step_count": int(mapping_payload.get("expected_step_count", 0) or 0),
            "compatible_case_count": int(mapping_payload.get("compatible_case_count", 0) or 0),
            "incompatible_case_count": int(mapping_payload.get("incompatible_case_count", 0) or 0),
            "ambiguous_binding_count": int(mapping_payload.get("ambiguous_binding_count", 0) or 0),
        },
        "profiles": {},
        "notes": [str(note) for note in list(summary_payload.get("notes", []) or []) if str(note).strip()],
    }

    for profile_name in ("teacher_demo", "research_fast"):
        profile_payload = dict(profiles_payload.get(profile_name, {}) or {})
        aggregate_outputs = dict(profile_payload.get("aggregate_outputs", {}) or {})
        digest["profiles"][profile_name] = {
            "package_count": int(profile_payload.get("package_count", 0) or 0),
            "linked_field_asset_count": int(profile_payload.get("linked_field_asset_count", 0) or 0),
            "lightweight_manifest_count": int(profile_payload.get("lightweight_manifest_count", 0) or 0),
            "field_case_linked_count": int(profile_payload.get("field_case_linked_count", 0) or 0),
            "step_montage_count": int(profile_payload.get("step_montage_count", 0) or 0),
            "triptych_count": int(profile_payload.get("triptych_count", 0) or 0),
            "timeline_montage_exists": bool(
                dict(aggregate_outputs.get("timeline_montage", {}) or {}).get("exists", False)
            ),
            "dataset_overview_exists": bool(
                dict(aggregate_outputs.get("dataset_overview", {}) or {}).get("exists", False)
            ),
            "field_case_gate": _normalize_field_case_gate(
                profile_payload.get("field_case_gate", {}),
                profile_name=profile_name,
            ),
            "operator_family_audit": dict(profile_payload.get("operator_family_audit", {}) or {}),
            "keyframe_caption_preview": _format_keyframe_caption_preview(profile_payload),
        }
    return digest


def format_iteration_review_report_block(iteration_review_summary: Mapping[str, Any] | None) -> str:
    digest = build_iteration_review_audit_digest(iteration_review_summary)
    if digest.get("status") != "available":
        return ""

    mapping_payload = dict(digest.get("field_case_mapping", {}) or {})
    teacher_profile = dict(dict(digest.get("profiles", {}) or {}).get("teacher_demo", {}) or {})
    research_profile = dict(dict(digest.get("profiles", {}) or {}).get("research_fast", {}) or {})
    teacher_gate = dict(teacher_profile.get("field_case_gate", {}) or {})
    research_gate = dict(research_profile.get("field_case_gate", {}) or {})
    teacher_family = dict(teacher_profile.get("operator_family_audit", {}) or {})
    research_family = dict(research_profile.get("operator_family_audit", {}) or {})
    expected = int(mapping_payload.get("expected_step_count", 0) or 0)
    mapped = int(mapping_payload.get("mapped_step_count", 0) or 0)

    lines = [
        _REPORT_BLOCK_START,
        "## Iteration Review Field-Case Mapping",
        "",
        f"- Review index: `{str(digest.get('root_index_path', '') or 'n/a')}`",
        (
            "- Field-case mapping: "
            f"mapped=`{mapped}/{expected or mapped}`, "
            f"matched=`{int(mapping_payload.get('matched_step_count', 0) or 0)}`, "
            f"defaulted=`{int(mapping_payload.get('defaulted_step_count', 0) or 0)}`, "
            f"unmapped=`{int(mapping_payload.get('unmapped_step_count', 0) or 0)}`"
        ),
        (
            "- Binding audit: "
            f"mapping_source=`{str(mapping_payload.get('mapping_source', '') or 'none')}`, "
            f"dataset_root=`{str(mapping_payload.get('dataset_root', '') or 'n/a')}`, "
            f"ambiguous=`{int(mapping_payload.get('ambiguous_binding_count', 0) or 0)}`"
        ),
        (
            "- Case compatibility: "
            f"compatible=`{int(mapping_payload.get('compatible_case_count', 0) or 0)}`, "
            f"incompatible=`{int(mapping_payload.get('incompatible_case_count', 0) or 0)}`"
        ),
        (
            "- Teacher demo: "
            f"packages=`{int(teacher_profile.get('package_count', 0) or 0)}`, "
            f"linked_field_assets=`{int(teacher_profile.get('linked_field_asset_count', 0) or 0)}`, "
            f"step_montage=`{int(teacher_profile.get('step_montage_count', 0) or 0)}`, "
            f"dataset_overview=`{_bool_text(teacher_profile.get('dataset_overview_exists'))}`"
        ),
        (
            "- Teacher field-case gate: "
            f"status=`{str(teacher_gate.get('status', '') or 'n/a')}`, "
            f"active=`{_bool_text(teacher_gate.get('active'))}`, "
            f"passed=`{_bool_text(teacher_gate.get('passed'))}`, "
            f"action=`{str(teacher_gate.get('enforcement_action', '') or 'n/a')}`, "
            f"violations=`{int(teacher_gate.get('violation_count', 0) or 0)}`"
        ),
        (
            "- Teacher operator families: "
            f"dominant_primary=`{str(teacher_family.get('dominant_primary_family_label', '') or 'n/a')}`, "
            f"primary_counts=`{_format_operator_family_counts(teacher_family, count_key='primary_family_counts', label_key='primary_family_labels')}`, "
            f"action_counts=`{_format_operator_family_counts(teacher_family, count_key='action_family_counts', label_key='action_family_labels')}`, "
            f"unmapped=`{int(teacher_family.get('unmapped_action_count', 0) or 0)}`"
        ),
        (
            "- Teacher keyframe captions: "
            f"`{str(teacher_profile.get('keyframe_caption_preview', '') or 'n/a')}`"
        ),
        (
            "- Research fast: "
            f"packages=`{int(research_profile.get('package_count', 0) or 0)}`, "
            f"linked_field_assets=`{int(research_profile.get('linked_field_asset_count', 0) or 0)}`, "
            f"step_montage=`{int(research_profile.get('step_montage_count', 0) or 0)}`, "
            f"dataset_overview=`{_bool_text(research_profile.get('dataset_overview_exists'))}`"
        ),
        (
            "- Research field-case gate: "
            f"status=`{str(research_gate.get('status', '') or 'n/a')}`, "
            f"active=`{_bool_text(research_gate.get('active'))}`, "
            f"passed=`{_bool_text(research_gate.get('passed'))}`, "
            f"action=`{str(research_gate.get('enforcement_action', '') or 'n/a')}`, "
            f"violations=`{int(research_gate.get('violation_count', 0) or 0)}`"
        ),
        (
            "- Research operator families: "
            f"dominant_primary=`{str(research_family.get('dominant_primary_family_label', '') or 'n/a')}`, "
            f"primary_counts=`{_format_operator_family_counts(research_family, count_key='primary_family_counts', label_key='primary_family_labels')}`, "
            f"action_counts=`{_format_operator_family_counts(research_family, count_key='action_family_counts', label_key='action_family_labels')}`, "
            f"unmapped=`{int(research_family.get('unmapped_action_count', 0) or 0)}`"
        ),
        _REPORT_BLOCK_END,
        "",
    ]
    return "\n".join(lines)


def format_iteration_review_visualization_block(iteration_review_summary: Mapping[str, Any] | None) -> str:
    digest = build_iteration_review_audit_digest(iteration_review_summary)
    if digest.get("status") != "available":
        return ""

    mapping_payload = dict(digest.get("field_case_mapping", {}) or {})
    teacher_profile = dict(dict(digest.get("profiles", {}) or {}).get("teacher_demo", {}) or {})
    research_profile = dict(dict(digest.get("profiles", {}) or {}).get("research_fast", {}) or {})
    teacher_gate = dict(teacher_profile.get("field_case_gate", {}) or {})
    research_gate = dict(research_profile.get("field_case_gate", {}) or {})
    teacher_family = dict(teacher_profile.get("operator_family_audit", {}) or {})
    research_family = dict(research_profile.get("operator_family_audit", {}) or {})
    expected = int(mapping_payload.get("expected_step_count", 0) or 0)
    mapped = int(mapping_payload.get("mapped_step_count", 0) or 0)

    lines = [
        _VISUALIZATION_BLOCK_START,
        f"- Review index: {str(digest.get('root_index_path', '') or 'n/a')}",
        (
            "- Step coverage: "
            f"mapped={mapped}/{expected or mapped}, "
            f"matched={int(mapping_payload.get('matched_step_count', 0) or 0)}, "
            f"defaulted={int(mapping_payload.get('defaulted_step_count', 0) or 0)}, "
            f"unmapped={int(mapping_payload.get('unmapped_step_count', 0) or 0)}"
        ),
        (
            "- Binding audit: "
            f"source={str(mapping_payload.get('mapping_source', '') or 'none')}, "
            f"dataset_root={str(mapping_payload.get('dataset_root', '') or 'n/a')}, "
            f"ambiguous={int(mapping_payload.get('ambiguous_binding_count', 0) or 0)}"
        ),
        (
            "- Case compatibility: "
            f"compatible={int(mapping_payload.get('compatible_case_count', 0) or 0)}, "
            f"incompatible={int(mapping_payload.get('incompatible_case_count', 0) or 0)}"
        ),
        (
            "- Teacher demo: "
            f"packages={int(teacher_profile.get('package_count', 0) or 0)}, "
            f"linked_field_assets={int(teacher_profile.get('linked_field_asset_count', 0) or 0)}, "
            f"dataset_overview={_bool_text(teacher_profile.get('dataset_overview_exists'))}"
        ),
        (
            "- Teacher field-case gate: "
            f"status={str(teacher_gate.get('status', '') or 'n/a')}, "
            f"active={_bool_text(teacher_gate.get('active'))}, "
            f"passed={_bool_text(teacher_gate.get('passed'))}, "
            f"action={str(teacher_gate.get('enforcement_action', '') or 'n/a')}, "
            f"violations={int(teacher_gate.get('violation_count', 0) or 0)}"
        ),
        (
            "- Teacher operator families: "
            f"dominant={str(teacher_family.get('dominant_primary_family_label', '') or 'n/a')}, "
            f"primary={_format_operator_family_counts(teacher_family, count_key='primary_family_counts', label_key='primary_family_labels')}, "
            f"action={_format_operator_family_counts(teacher_family, count_key='action_family_counts', label_key='action_family_labels')}, "
            f"unmapped={int(teacher_family.get('unmapped_action_count', 0) or 0)}"
        ),
        (
            "- Teacher keyframe captions: "
            f"{str(teacher_profile.get('keyframe_caption_preview', '') or 'n/a')}"
        ),
        (
            "- Research fast: "
            f"packages={int(research_profile.get('package_count', 0) or 0)}, "
            f"linked_field_assets={int(research_profile.get('linked_field_asset_count', 0) or 0)}, "
            f"dataset_overview={_bool_text(research_profile.get('dataset_overview_exists'))}"
        ),
        (
            "- Research field-case gate: "
            f"status={str(research_gate.get('status', '') or 'n/a')}, "
            f"active={_bool_text(research_gate.get('active'))}, "
            f"passed={_bool_text(research_gate.get('passed'))}, "
            f"action={str(research_gate.get('enforcement_action', '') or 'n/a')}, "
            f"violations={int(research_gate.get('violation_count', 0) or 0)}"
        ),
        (
            "- Research operator families: "
            f"dominant={str(research_family.get('dominant_primary_family_label', '') or 'n/a')}, "
            f"primary={_format_operator_family_counts(research_family, count_key='primary_family_counts', label_key='primary_family_labels')}, "
            f"action={_format_operator_family_counts(research_family, count_key='action_family_counts', label_key='action_family_labels')}, "
            f"unmapped={int(research_family.get('unmapped_action_count', 0) or 0)}"
        ),
        _VISUALIZATION_BLOCK_END,
    ]
    return "\n".join(lines)
