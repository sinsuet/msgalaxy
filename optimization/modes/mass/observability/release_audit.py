"""
Helpers for release-grade audit derivation and run artifact rebuild.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from core.artifact_index import (
    build_artifact_index_payload,
    materialize_legacy_artifacts_into_layout_v2,
    write_artifact_index,
)
from core.logger import discover_active_llm_buckets, write_markdown_report
from core.mode_contract import (
    normalize_runtime_mode,
    resolve_execution_mode,
    resolve_lifecycle_state,
)
from core.path_policy import serialize_repo_path, serialize_run_path
from optimization.modes.mass.observability.materialize import (
    materialize_observability_tables,
)

_RELEASE_AUDIT_FEASIBLE_STATUSES = {"feasible", "feasible_but_stalled"}


def normalize_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return int(parsed)


def normalize_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return float(parsed)


def build_release_audit_payload(
    *,
    simulation_backend: str,
    thermal_evaluator_mode: str,
    enable_physics_audit: bool,
    diagnosis_status: str,
    source_gate_report: Dict[str, Any],
    operator_family_gate_report: Dict[str, Any],
    operator_realization_gate_report: Dict[str, Any],
    final_mph_path: str,
    trace_features: Dict[str, Any],
) -> Dict[str, Any]:
    backend = str(simulation_backend or "").strip().lower()
    thermal_mode = str(thermal_evaluator_mode or "").strip().lower()
    diagnosis = str(diagnosis_status or "").strip().lower()
    mph_path = str(final_mph_path or "").strip()
    first_feasible_eval = normalize_optional_int(
        trace_features.get("first_feasible_eval")
    )
    comsol_calls_to_first_feasible = normalize_optional_int(
        trace_features.get("comsol_calls_to_first_feasible")
    )

    if backend != "comsol":
        final_audit_status = "diagnostic_only_non_comsol_backend"
    elif thermal_mode != "online_comsol":
        final_audit_status = "diagnostic_only_non_online_comsol"
    elif not bool(enable_physics_audit):
        final_audit_status = "diagnostic_only_audit_disabled"
    elif not bool(source_gate_report.get("passed", True)):
        final_audit_status = "diagnostic_only_source_gate_blocked"
    elif not bool(operator_family_gate_report.get("passed", True)):
        final_audit_status = "diagnostic_only_operator_family_gate_blocked"
    elif not bool(operator_realization_gate_report.get("passed", True)):
        final_audit_status = "diagnostic_only_operator_realization_gate_blocked"
    elif diagnosis not in _RELEASE_AUDIT_FEASIBLE_STATUSES:
        final_audit_status = "diagnostic_only_no_feasible_final_state"
    elif not mph_path:
        final_audit_status = "diagnostic_only_missing_final_mph"
    elif first_feasible_eval is None:
        final_audit_status = "diagnostic_only_missing_first_feasible_eval"
    elif comsol_calls_to_first_feasible is None:
        final_audit_status = "diagnostic_only_missing_comsol_calls_to_first_feasible"
    else:
        final_audit_status = "release_grade_real_comsol_validated"

    return {
        "simulation_backend": backend,
        "final_audit_status": str(final_audit_status),
        "first_feasible_eval": first_feasible_eval,
        "comsol_calls_to_first_feasible": comsol_calls_to_first_feasible,
    }


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


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


def _read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return [dict(row or {}) for row in csv.DictReader(f)]
    except Exception:
        return []


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y"}:
        return True
    if raw in {"0", "false", "no", "n"}:
        return False
    return bool(default)


def _infer_level(summary: Dict[str, Any], run_dir: str) -> str:
    for candidate in (
        summary.get("level"),
        summary.get("run_label"),
        summary.get("run_id"),
    ):
        raw = str(candidate or "").strip().lower()
        if not raw:
            continue
        match = re.search(r"\b(l[1-4])\b", raw)
        if match:
            return str(match.group(1)).upper()
        match = re.search(r"[_-](l[1-4])(?:[_-]|$)", raw)
        if match:
            return str(match.group(1)).upper()
    raw_run = str(Path(run_dir).name).strip().lower()
    match = re.search(r"[_-](l[1-4])(?:[_-]|$)", raw_run)
    if match:
        return str(match.group(1)).upper()
    return ""


def _coalesce_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        return value
    return None


def _format_evidence_hint(**payload: Any) -> str:
    parts: List[str] = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                continue
            parts.append(f"{key}={normalized}")
            continue
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def _infer_simulation_backend(
    *,
    summary: Dict[str, Any],
    manifest_extra: Dict[str, Any],
    physics_rows: Iterable[Dict[str, Any]],
) -> str:
    for candidate in (
        summary.get("simulation_backend"),
        manifest_extra.get("simulation_backend"),
    ):
        value = str(candidate or "").strip().lower()
        if value:
            return value
    for row in list(physics_rows or []):
        value = str(row.get("simulation_backend", "") or "").strip().lower()
        if value:
            return value
    if str(summary.get("thermal_evaluator_mode", "") or "").strip().lower() == "online_comsol":
        return "comsol"
    if str(summary.get("final_mph_path", "") or "").strip():
        return "comsol"
    return ""


def _infer_enable_physics_audit(
    *,
    summary: Dict[str, Any],
    manifest_extra: Dict[str, Any],
    physics_rows: Iterable[Dict[str, Any]],
) -> bool:
    if summary.get("physics_pass_rate_topk") is not None:
        return True
    if manifest_extra.get("physics_pass_rate_topk") is not None:
        return True
    return any(
        str(row.get("event_type", "") or "").strip() == "physics_audit"
        for row in list(physics_rows or [])
    )


def _infer_first_feasible_trace_features(
    *,
    summary: Dict[str, Any],
    candidate_rows: List[Dict[str, Any]],
    physics_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    first_feasible_eval = normalize_optional_int(summary.get("first_feasible_eval"))
    comsol_calls = normalize_optional_int(
        summary.get("comsol_calls_to_first_feasible")
    )

    if first_feasible_eval is not None and comsol_calls is not None:
        return {
            "first_feasible_eval": first_feasible_eval,
            "comsol_calls_to_first_feasible": comsol_calls,
        }

    diagnosis_status = str(summary.get("diagnosis_status", "") or "").strip().lower()
    diagnosis_reason = str(summary.get("diagnosis_reason", "") or "").strip().lower()
    if (
        first_feasible_eval is None
        and diagnosis_status in _RELEASE_AUDIT_FEASIBLE_STATUSES
        and diagnosis_reason == "final_state_recheck_feasible"
    ):
        selected_candidates = [
            row
            for row in list(candidate_rows or [])
            if _normalize_bool(row.get("is_selected"), default=False)
        ]
        if selected_candidates:
            attempt_values = [
                normalize_optional_int(item.get("attempt"))
                for item in selected_candidates
            ]
            attempt_values = [item for item in attempt_values if item is not None]
            if attempt_values:
                first_feasible_eval = int(attempt_values[-1])

    if comsol_calls is None and first_feasible_eval is not None:
        matched_snapshots = []
        for row in list(physics_rows or []):
            if str(row.get("event_type", "") or "").strip() != "runtime_thermal_snapshot":
                continue
            attempt = normalize_optional_int(row.get("attempt"))
            if attempt is None:
                continue
            if attempt <= int(first_feasible_eval):
                matched_snapshots.append(row)
        if matched_snapshots:
            snapshot = matched_snapshots[-1]
            stats = dict(snapshot.get("stats", {}) or {})
            comsol_calls = normalize_optional_int(stats.get("executed_online_comsol"))

    return {
        "first_feasible_eval": first_feasible_eval,
        "comsol_calls_to_first_feasible": comsol_calls,
    }


def derive_release_audit_payload_from_run_dir(run_dir: str) -> Dict[str, Any]:
    run_path = Path(run_dir)
    summary = _read_json(run_path / "summary.json")
    if not summary:
        return {}

    manifest = _read_json(run_path / "events" / "run_manifest.json")
    manifest_extra = dict(manifest.get("extra", {}) or {})
    physics_rows = _read_jsonl(run_path / "events" / "physics_events.jsonl")
    candidate_rows = _read_jsonl(run_path / "events" / "candidate_events.jsonl")

    trace_features = _infer_first_feasible_trace_features(
        summary=summary,
        candidate_rows=candidate_rows,
        physics_rows=physics_rows,
    )
    simulation_backend = _infer_simulation_backend(
        summary=summary,
        manifest_extra=manifest_extra,
        physics_rows=physics_rows,
    )
    payload = build_release_audit_payload(
        simulation_backend=simulation_backend,
        thermal_evaluator_mode=str(summary.get("thermal_evaluator_mode", "") or ""),
        enable_physics_audit=_infer_enable_physics_audit(
            summary=summary,
            manifest_extra=manifest_extra,
            physics_rows=physics_rows,
        ),
        diagnosis_status=str(summary.get("diagnosis_status", "") or ""),
        source_gate_report={"passed": _normalize_bool(summary.get("source_gate_passed"), default=True)},
        operator_family_gate_report={
            "passed": _normalize_bool(
                summary.get("operator_family_gate_passed"),
                default=True,
            )
        },
        operator_realization_gate_report={
            "passed": _normalize_bool(
                summary.get("operator_realization_gate_passed"),
                default=True,
            )
        },
        final_mph_path=str(summary.get("final_mph_path", "") or ""),
        trace_features=trace_features,
    )
    return payload


def rebuild_run_release_audit_artifacts(
    run_dir: str,
    *,
    refresh_visualizations: bool = True,
) -> Dict[str, Any]:
    run_path = Path(run_dir)
    summary_path = run_path / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found under {run_dir}")

    summary = _read_json(summary_path)
    audit_payload = derive_release_audit_payload_from_run_dir(run_dir)
    summary.update(audit_payload)
    summary["run_dir"] = serialize_repo_path(run_dir)
    run_mode = normalize_runtime_mode(
        summary.get("run_mode") or summary.get("optimization_mode"),
        default="mass",
    )
    artifact_index = build_artifact_index_payload(
        run_dir=run_dir,
        run_mode=run_mode,
        execution_mode=resolve_execution_mode(run_mode),
        lifecycle_state=resolve_lifecycle_state(run_mode),
    )
    artifact_index = materialize_legacy_artifacts_into_layout_v2(
        run_dir,
        artifact_index,
    )
    write_artifact_index(run_dir, artifact_index)
    summary.setdefault("run_mode", run_mode)
    summary.setdefault("execution_mode", resolve_execution_mode(run_mode))
    summary.setdefault("lifecycle_state", resolve_lifecycle_state(run_mode))
    summary["artifact_layout_version"] = int(artifact_index.get("artifact_layout_version", 2) or 2)
    summary["artifact_index_path"] = str(artifact_index.get("artifact_index_path", "") or "")
    _write_json(summary_path, summary)

    observability_tables = materialize_observability_tables(run_dir)
    summary["observability_tables"] = dict(observability_tables or {})
    _write_json(summary_path, summary)

    manifest_path = run_path / "events" / "run_manifest.json"
    manifest = _read_json(manifest_path)
    manifest_extra = dict(manifest.get("extra", {}) or {})
    manifest_extra.update(audit_payload)
    manifest_extra["observability_tables"] = dict(observability_tables or {})
    manifest["extra"] = manifest_extra
    manifest["run_dir"] = serialize_repo_path(run_dir)
    manifest.setdefault("run_mode", run_mode)
    manifest.setdefault("execution_mode", resolve_execution_mode(run_mode))
    manifest.setdefault("lifecycle_state", resolve_lifecycle_state(run_mode))
    manifest["artifact_layout_version"] = int(artifact_index.get("artifact_layout_version", 2) or 2)
    manifest["artifact_index_path"] = str(artifact_index.get("artifact_index_path", "") or "")
    _write_json(manifest_path, manifest)

    write_markdown_report(
        run_dir=run_dir,
        summary=summary,
        active_buckets=discover_active_llm_buckets(run_dir),
    )

    if refresh_visualizations:
        from core.visualization import generate_visualizations

        generate_visualizations(run_dir)

    return {
        "run_dir": serialize_repo_path(run_dir),
        "final_audit_status": str(summary.get("final_audit_status", "") or ""),
        "first_feasible_eval": summary.get("first_feasible_eval"),
        "comsol_calls_to_first_feasible": summary.get(
            "comsol_calls_to_first_feasible"
        ),
        "release_audit_table": str(
            (observability_tables or {}).get("release_audit_path", "") or ""
        ),
    }


def classify_release_audit_gap(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row or {})
    audit_status = str(payload.get("final_audit_status", "") or "").strip().lower()
    diagnosis_reason = str(payload.get("diagnosis_reason", "") or "").strip().lower()
    backend = str(payload.get("simulation_backend", "") or "").strip().lower()
    thermal_mode = str(payload.get("thermal_evaluator_mode", "") or "").strip().lower()
    dominant_violation = str(payload.get("dominant_violation", "") or "").strip().lower()
    best_cv_min = normalize_optional_float(payload.get("best_cv_min"))
    min_clearance = normalize_optional_float(payload.get("best_candidate_min_clearance"))
    mission_keepout_violation = normalize_optional_float(
        payload.get("best_candidate_mission_keepout_violation")
    )

    if audit_status == "release_grade_real_comsol_validated":
        return {
            "gap_category": "release_grade",
            "primary_failure_signature": "release_grade_real_comsol_validated",
            "minimal_remediation": "none",
            "evidence_hint": "release-grade audit satisfied",
        }

    if audit_status == "diagnostic_only_non_comsol_backend":
        return {
            "gap_category": "backend_contract_gap",
            "primary_failure_signature": "non_comsol_backend",
            "minimal_remediation": "rerun on real COMSOL backend before making release-grade claims",
            "evidence_hint": _format_evidence_hint(
                simulation_backend=backend or "n/a",
                thermal_evaluator_mode=thermal_mode or "n/a",
            ),
        }
    if audit_status == "diagnostic_only_non_online_comsol":
        return {
            "gap_category": "thermal_fidelity_gap",
            "primary_failure_signature": "non_online_comsol",
            "minimal_remediation": "switch to online_comsol thermal evaluation on the COMSOL backend",
            "evidence_hint": _format_evidence_hint(
                simulation_backend=backend or "n/a",
                thermal_evaluator_mode=thermal_mode or "n/a",
            ),
        }
    if audit_status == "diagnostic_only_audit_disabled":
        return {
            "gap_category": "physics_audit_gap",
            "primary_failure_signature": "physics_audit_disabled",
            "minimal_remediation": "re-enable bounded physics audit before release-grade validation",
            "evidence_hint": _format_evidence_hint(
                simulation_backend=backend or "n/a",
                thermal_evaluator_mode=thermal_mode or "n/a",
            ),
        }

    if audit_status == "diagnostic_only_source_gate_blocked" or not _normalize_bool(
        payload.get("source_gate_passed"),
        default=True,
    ):
        return {
            "gap_category": "source_gate_gap",
            "primary_failure_signature": "source_gate_blocked",
            "minimal_remediation": "repair source gate violations before retrying release-grade validation",
            "evidence_hint": _format_evidence_hint(
                source_gate_passed=payload.get("source_gate_passed")
            ),
        }
    if audit_status == "diagnostic_only_operator_family_gate_blocked" or not _normalize_bool(
        payload.get("operator_family_gate_passed"),
        default=True,
    ):
        return {
            "gap_category": "operator_family_gate_gap",
            "primary_failure_signature": "operator_family_gate_blocked",
            "minimal_remediation": "repair operator-family gate violations before retrying release-grade validation",
            "evidence_hint": _format_evidence_hint(
                operator_family_gate_passed=payload.get("operator_family_gate_passed")
            ),
        }
    if audit_status == "diagnostic_only_operator_realization_gate_blocked" or not _normalize_bool(
        payload.get("operator_realization_gate_passed"),
        default=True,
    ):
        return {
            "gap_category": "operator_realization_gate_gap",
            "primary_failure_signature": "operator_realization_gate_blocked",
            "minimal_remediation": "repair operator-realization gate violations before retrying release-grade validation",
            "evidence_hint": _format_evidence_hint(
                operator_realization_gate_passed=payload.get(
                    "operator_realization_gate_passed"
                )
            ),
        }

    if audit_status == "diagnostic_only_missing_final_mph":
        return {
            "gap_category": "artifact_final_mph_gap",
            "primary_failure_signature": "missing_final_mph",
            "minimal_remediation": "ensure the final feasible state exports final_mph_path before release-grade validation",
            "evidence_hint": _format_evidence_hint(
                final_mph_path=payload.get("final_mph_path")
            ),
        }
    if audit_status in {
        "diagnostic_only_missing_first_feasible_eval",
        "diagnostic_only_missing_comsol_calls_to_first_feasible",
    }:
        return {
            "gap_category": "artifact_trace_gap",
            "primary_failure_signature": audit_status.removeprefix("diagnostic_only_"),
            "minimal_remediation": "rebuild release audit artifacts to restore missing first-feasible trace fields",
            "evidence_hint": _format_evidence_hint(
                first_feasible_eval=payload.get("first_feasible_eval"),
                comsol_calls_to_first_feasible=payload.get(
                    "comsol_calls_to_first_feasible"
                ),
            ),
        }

    if audit_status == "diagnostic_only_no_feasible_final_state":
        evidence_hint = _format_evidence_hint(
            dominant_violation=dominant_violation or "n/a",
            best_cv_min=best_cv_min,
            min_clearance=min_clearance,
            mission_keepout_violation=mission_keepout_violation,
            diagnosis_reason=diagnosis_reason or "n/a",
        )
        if dominant_violation == "g_clearance":
            return {
                "gap_category": "feasibility_clearance_gap",
                "primary_failure_signature": "g_clearance",
                "minimal_remediation": "prioritize initial clearance sync / spacing repair before rerun",
                "evidence_hint": evidence_hint,
            }
        if dominant_violation == "g_mission_keepout":
            return {
                "gap_category": "feasibility_mission_keepout_gap",
                "primary_failure_signature": "g_mission_keepout",
                "minimal_remediation": "prioritize initial mission keepout repair / keepout push before rerun",
                "evidence_hint": evidence_hint,
            }
        if dominant_violation == "g_cg":
            return {
                "gap_category": "feasibility_cg_gap",
                "primary_failure_signature": "g_cg",
                "minimal_remediation": "prioritize cg recenter / mass rebalance before rerun",
                "evidence_hint": evidence_hint,
            }
        if dominant_violation in {"g_boundary", "g_collision"}:
            return {
                "gap_category": "feasibility_geometry_gap",
                "primary_failure_signature": dominant_violation or "geometry_violation",
                "minimal_remediation": "prioritize boundary / collision repair before rerun",
                "evidence_hint": evidence_hint,
            }
        if dominant_violation.startswith("g_power") or dominant_violation in {
            "g_voltage_drop",
            "g_peak_power",
        }:
            return {
                "gap_category": "feasibility_power_gap",
                "primary_failure_signature": dominant_violation or "power_proxy_violation",
                "minimal_remediation": "prioritize bounded power-proxy repair before rerun",
                "evidence_hint": evidence_hint,
            }
        if dominant_violation.startswith("g_modal") or dominant_violation in {
            "g_safety_factor",
            "g_stress",
        }:
            return {
                "gap_category": "feasibility_structural_gap",
                "primary_failure_signature": dominant_violation or "structural_proxy_violation",
                "minimal_remediation": "prioritize bounded structural-proxy repair before rerun",
                "evidence_hint": evidence_hint,
            }
        return {
            "gap_category": "feasibility_other_gap",
            "primary_failure_signature": dominant_violation or diagnosis_reason or audit_status,
            "minimal_remediation": "inspect dominant violation and policy feedback before rerun",
            "evidence_hint": evidence_hint,
        }

    return {
        "gap_category": "unclassified_gap",
        "primary_failure_signature": audit_status or diagnosis_reason or "unknown_gap",
        "minimal_remediation": "inspect summary, release_audit table, and policy feedback before rerun",
        "evidence_hint": _format_evidence_hint(
            final_audit_status=audit_status or "n/a",
            diagnosis_reason=diagnosis_reason or "n/a",
        ),
    }


def collect_release_audit_summary_rows(run_dirs: Iterable[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run_dir in list(run_dirs or []):
        run_path = Path(run_dir)
        summary = _read_json(run_path / "summary.json")
        if not summary:
            continue
        release_audit_table_path = run_path / "tables" / "release_audit.csv"
        release_audit_rows = _read_csv_rows(release_audit_table_path)
        release_audit_row = dict(release_audit_rows[-1] or {}) if release_audit_rows else {}
        if release_audit_table_path.exists():
            release_audit_table = serialize_run_path(run_dir, release_audit_table_path)
        else:
            release_audit_table = ""
        vop_round_table_path = run_path / "tables" / "vop_rounds.csv"
        vop_round_rows = _read_csv_rows(vop_round_table_path)
        if vop_round_table_path.exists():
            vop_round_audit_table = serialize_run_path(run_dir, vop_round_table_path)
        else:
            vop_round_audit_table = str(summary.get("vop_round_audit_table", "") or "")
        vop_round_events = (
            [] if vop_round_rows else _read_jsonl(run_path / "events" / "vop_round_events.jsonl")
        )
        vop_round_count = len(vop_round_rows) if vop_round_rows else len(vop_round_events)
        best_candidate_metrics = dict(summary.get("best_candidate_metrics", {}) or {})
        row: Dict[str, Any] = {
            "run_dir": serialize_repo_path(run_dir),
            "run_id": str(
                _coalesce_value(release_audit_row.get("run_id"), summary.get("run_id"), "") or ""
            ),
            "run_mode": str(
                _coalesce_value(summary.get("run_mode"), summary.get("optimization_mode"), "")
                or ""
            ),
            "execution_mode": str(
                _coalesce_value(summary.get("execution_mode"), summary.get("run_mode"), "")
                or ""
            ),
            "level": _infer_level(summary, run_dir),
            "status": str(
                _coalesce_value(release_audit_row.get("status"), summary.get("status"), "") or ""
            ),
            "diagnosis_status": str(
                _coalesce_value(
                    release_audit_row.get("diagnosis_status"),
                    summary.get("diagnosis_status"),
                    "",
                )
                or ""
            ),
            "diagnosis_reason": str(
                _coalesce_value(
                    release_audit_row.get("diagnosis_reason"),
                    summary.get("diagnosis_reason"),
                    "",
                )
                or ""
            ),
            "simulation_backend": str(
                _coalesce_value(
                    release_audit_row.get("simulation_backend"),
                    summary.get("simulation_backend"),
                    "",
                )
                or ""
            ),
            "thermal_evaluator_mode": str(
                _coalesce_value(
                    release_audit_row.get("thermal_evaluator_mode"),
                    summary.get("thermal_evaluator_mode"),
                    "",
                )
                or ""
            ),
            "final_audit_status": str(
                _coalesce_value(
                    release_audit_row.get("final_audit_status"),
                    summary.get("final_audit_status"),
                    "",
                )
                or ""
            ),
            "first_feasible_eval": _coalesce_value(
                release_audit_row.get("first_feasible_eval"),
                summary.get("first_feasible_eval"),
            ),
            "comsol_calls_to_first_feasible": _coalesce_value(
                release_audit_row.get("comsol_calls_to_first_feasible"),
                summary.get("comsol_calls_to_first_feasible"),
            ),
            "final_mph_path": str(
                _coalesce_value(
                    release_audit_row.get("final_mph_path"),
                    summary.get("final_mph_path"),
                    "",
                )
                or ""
            ),
            "source_gate_passed": _coalesce_value(
                release_audit_row.get("source_gate_passed"),
                summary.get("source_gate_passed"),
            ),
            "operator_family_gate_passed": _coalesce_value(
                release_audit_row.get("operator_family_gate_passed"),
                summary.get("operator_family_gate_passed"),
            ),
            "operator_realization_gate_passed": _coalesce_value(
                release_audit_row.get("operator_realization_gate_passed"),
                summary.get("operator_realization_gate_passed"),
            ),
            "dominant_violation": str(summary.get("dominant_violation", "") or ""),
            "best_cv_min": summary.get("best_cv_min"),
            "best_candidate_min_clearance": best_candidate_metrics.get("min_clearance"),
            "best_candidate_mission_keepout_violation": best_candidate_metrics.get(
                "mission_keepout_violation"
            ),
            "vop_round_count": int(summary.get("vop_round_count", vop_round_count) or 0)
            if not vop_round_rows
            else int(vop_round_count),
            "vop_round_audit_table": str(vop_round_audit_table or ""),
            "release_audit_table": str(release_audit_table or ""),
            "runtime_feature_fingerprint_path": str(
                summary.get("runtime_feature_fingerprint_path", "") or ""
            ),
            "llm_final_summary_zh_path": str(
                summary.get("llm_final_summary_zh_path", "") or ""
            ),
            "llm_final_summary_digest_path": str(
                summary.get("llm_final_summary_digest_path", "") or ""
            ),
        }
        row.update(classify_release_audit_gap(row))
        rows.append(row)
    return rows


def build_release_audit_rollup(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    materialized = [dict(item or {}) for item in list(rows or []) if dict(item or {})]
    rollup: Dict[str, Any] = {
        "total_runs": int(len(materialized)),
        "non_release_runs": 0,
        "by_level": {},
        "by_final_audit_status": {},
        "by_gap_category": {},
    }
    for row in materialized:
        level = str(row.get("level", "") or "").strip() or "UNKNOWN"
        audit_status = str(row.get("final_audit_status", "") or "").strip() or "MISSING"
        level_entry = dict(rollup["by_level"].get(level, {}) or {})
        level_entry["total_runs"] = int(level_entry.get("total_runs", 0) or 0) + 1
        level_entry[audit_status] = int(level_entry.get(audit_status, 0) or 0) + 1
        rollup["by_level"][level] = level_entry
        rollup["by_final_audit_status"][audit_status] = int(
            rollup["by_final_audit_status"].get(audit_status, 0) or 0
        ) + 1
        if audit_status != "release_grade_real_comsol_validated":
            rollup["non_release_runs"] = int(rollup.get("non_release_runs", 0) or 0) + 1
            gap_category = str(row.get("gap_category", "") or "").strip() or "unclassified_gap"
            rollup["by_gap_category"][gap_category] = int(
                rollup["by_gap_category"].get(gap_category, 0) or 0
            ) + 1
    return rollup
