"""
Helpers for mode-scoped raw artifact layout and artifact index persistence.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict

from core.path_policy import serialize_run_path


ARTIFACT_LAYOUT_VERSION = 2
ARTIFACT_INDEX_REL_PATH = Path("events") / "artifact_index.json"

_KNOWN_SCOPES = {
    "agent_loop",
    "mass",
    "vop_maas",
    "delegated_mass",
    "legacy",
}


def normalize_artifact_scope(scope: Any, default: str = "legacy") -> str:
    normalized = str(scope or "").strip().lower()
    if normalized in _KNOWN_SCOPES:
        return normalized
    return str(default or "legacy").strip().lower() or "legacy"


def default_raw_scope_for_run_mode(run_mode: Any) -> str:
    normalized = str(run_mode or "").strip().lower()
    if normalized == "vop_maas":
        return "delegated_mass"
    if normalized in {"agent_loop", "mass"}:
        return normalized
    return "legacy"


def scope_relative_root(scope: Any) -> str:
    normalized = normalize_artifact_scope(scope)
    mapping = {
        "agent_loop": "artifacts/agent_loop",
        "mass": "artifacts/mass",
        "vop_maas": "artifacts/vop_maas",
        "delegated_mass": "artifacts/vop_maas/delegated_mass",
        "legacy": "artifacts/legacy",
    }
    return str(mapping.get(normalized, mapping["legacy"]))


def build_scope_payload(scope: Any) -> Dict[str, Any]:
    normalized = normalize_artifact_scope(scope)
    root = scope_relative_root(normalized)
    payload: Dict[str, Any] = {
        "root": root,
        "llm_interactions_dir": f"{root}/llm_interactions",
        "trace_dir": f"{root}/trace",
    }
    if normalized == "agent_loop":
        payload["evolution_trace_csv"] = f"{root}/evolution_trace.csv"
        payload["snapshots_dir"] = f"{root}/snapshots"
    if normalized in {"mass", "delegated_mass"}:
        payload["mass_trace_csv"] = f"{root}/mass_trace.csv"
        payload["snapshots_dir"] = f"{root}/snapshots"
        payload["step_files_dir"] = f"{root}/step_files"
        payload["maas_diagnostics_jsonl"] = f"{root}/maas_diagnostics.jsonl"
    return payload


def build_artifact_index_payload(
    *,
    run_dir: str,
    run_mode: str,
    execution_mode: str,
    lifecycle_state: str,
) -> Dict[str, Any]:
    run_path = Path(run_dir)
    raw_scope = default_raw_scope_for_run_mode(run_mode)
    llm_scopes: Dict[str, str] = {}
    if str(run_mode or "").strip().lower() == "vop_maas":
        llm_scopes["vop_maas"] = scope_relative_root("vop_maas") + "/llm_interactions"
        llm_scopes["delegated_mass"] = scope_relative_root("delegated_mass") + "/llm_interactions"
    elif str(run_mode or "").strip().lower() in {"agent_loop", "mass"}:
        llm_scopes[str(run_mode).strip().lower()] = (
            scope_relative_root(run_mode) + "/llm_interactions"
        )

    scopes: Dict[str, Dict[str, Any]] = {}
    active_scopes = {"legacy", raw_scope}
    if str(run_mode or "").strip().lower() == "vop_maas":
        active_scopes.add("vop_maas")
        active_scopes.add("delegated_mass")
    elif str(run_mode or "").strip().lower() == "agent_loop":
        active_scopes.add("agent_loop")
    elif str(run_mode or "").strip().lower() == "mass":
        active_scopes.add("mass")

    for scope in sorted(active_scopes):
        scopes[scope] = build_scope_payload(scope)

    payload = {
        "artifact_layout_version": int(ARTIFACT_LAYOUT_VERSION),
        "run_mode": str(run_mode or ""),
        "execution_mode": str(execution_mode or ""),
        "lifecycle_state": str(lifecycle_state or ""),
        "artifact_index_path": serialize_run_path(run_path, run_path / ARTIFACT_INDEX_REL_PATH),
        "default_raw_scope": raw_scope,
        "top_level": {
            "summary_path": "summary.json",
            "report_path": "report.md",
            "events_dir": "events",
            "tables_dir": "tables",
            "visualizations_dir": "visualizations",
            "mph_models_dir": "mph_models",
            "run_log_path": "run_log.txt",
            "run_log_debug_path": "run_log_debug.txt" if (run_path / "run_log_debug.txt").exists() else "",
        },
        "scopes": scopes,
        "paths": {
            "llm_interactions": dict(llm_scopes),
            "agent_loop_trace_csv": scopes.get("agent_loop", {}).get("evolution_trace_csv", ""),
            "mass_trace_csv": scopes.get(raw_scope, {}).get("mass_trace_csv", ""),
            "trace_dir": scopes.get(raw_scope, {}).get("trace_dir", ""),
            "snapshots_dir": scopes.get(raw_scope, {}).get("snapshots_dir", ""),
            "step_files_dir": scopes.get(raw_scope, {}).get("step_files_dir", ""),
            "maas_diagnostics_jsonl": scopes.get(raw_scope, {}).get("maas_diagnostics_jsonl", ""),
        },
    }
    return payload


def _copy_file_if_exists(source: Path, destination: Path) -> bool:
    if not source.exists() or not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _copy_dir_if_exists(source: Path, destination: Path) -> bool:
    if not source.exists() or not source.is_dir():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return True


def materialize_legacy_artifacts_into_layout_v2(
    run_dir: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    run_path = Path(run_dir)
    normalized_payload: Dict[str, Any] = dict(payload or {})
    scopes: Dict[str, Dict[str, Any]] = {
        str(key): dict(value or {})
        for key, value in dict(normalized_payload.get("scopes", {}) or {}).items()
    }
    paths: Dict[str, Any] = dict(normalized_payload.get("paths", {}) or {})
    llm_map: Dict[str, str] = {
        str(key): str(value or "")
        for key, value in dict(paths.get("llm_interactions", {}) or {}).items()
        if str(key).strip()
    }
    default_raw_scope = normalize_artifact_scope(
        normalized_payload.get("default_raw_scope"),
        default="legacy",
    )

    def _ensure_scope(scope: str) -> Dict[str, Any]:
        existing = dict(scopes.get(scope, {}) or {})
        if existing:
            scopes[scope] = existing
            return existing
        built = build_scope_payload(scope)
        scopes[scope] = built
        return built

    default_scope_payload = _ensure_scope(default_raw_scope)

    if (run_path / "llm_interactions").is_dir():
        if not llm_map:
            llm_target = str(default_scope_payload.get("llm_interactions_dir", "") or "")
            if llm_target:
                llm_map[default_raw_scope] = llm_target
        for scope_name, rel_path in list(llm_map.items()):
            if not rel_path:
                continue
            _copy_dir_if_exists(run_path / "llm_interactions", run_path / rel_path)

    legacy_agent_trace = run_path / "evolution_trace.csv"
    if legacy_agent_trace.exists():
        agent_scope = _ensure_scope("agent_loop")
        evolution_target = str(agent_scope.get("evolution_trace_csv", "") or "")
        if evolution_target:
            _copy_file_if_exists(legacy_agent_trace, run_path / evolution_target)
            paths["agent_loop_trace_csv"] = evolution_target

    legacy_artifact_files = {
        "mass_trace_csv": run_path / "mass_trace.csv",
        "maas_diagnostics_jsonl": run_path / "maas_diagnostics.jsonl",
    }
    for key, source_path in legacy_artifact_files.items():
        rel_path = str(default_scope_payload.get(key, "") or "")
        if rel_path:
            if _copy_file_if_exists(source_path, run_path / rel_path):
                paths[key] = rel_path

    legacy_artifact_dirs = {
        "trace_dir": run_path / "trace",
        "snapshots_dir": run_path / "snapshots",
        "step_files_dir": run_path / "step_files",
    }
    for key, source_path in legacy_artifact_dirs.items():
        rel_path = str(default_scope_payload.get(key, "") or "")
        if rel_path:
            if _copy_dir_if_exists(source_path, run_path / rel_path):
                paths[key] = rel_path

    paths["llm_interactions"] = llm_map
    normalized_payload["scopes"] = scopes
    normalized_payload["paths"] = paths
    return normalized_payload


def artifact_index_path(run_dir: str) -> Path:
    return Path(run_dir) / ARTIFACT_INDEX_REL_PATH


def write_artifact_index(run_dir: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = artifact_index_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dict(payload)


def load_artifact_index(run_dir: str) -> Dict[str, Any]:
    path = artifact_index_path(run_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_index_path(index: Dict[str, Any], *keys: str) -> str:
    current: Any = dict(index or {})
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return str(current or "")
