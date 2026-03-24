"""
Shared experiment discovery and path helpers for API/CLI surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.path_policy import resolve_artifact_or_repo_path, serialize_artifact_path


def resolve_experiments_root(configured: str | Path = "experiments") -> Path:
    return resolve_artifact_or_repo_path("experiments", configured)


def resolve_experiment_dir(experiments_root: str | Path, path_value: str | Path) -> Path:
    return resolve_artifact_or_repo_path(resolve_experiments_root(experiments_root), path_value)


def serialize_experiment_dir(experiments_root: str | Path, path_value: str | Path) -> str:
    return serialize_artifact_path(resolve_experiments_root(experiments_root), path_value)


def is_run_dir(path: Path) -> bool:
    name = str(path.name or "")
    if name.startswith("run_"):
        return True
    if len(name) >= 5 and name[:4].isdigit() and name[4] == "_":
        return True
    if len(name) >= 7 and name[:6].isdigit() and name[6] == "_":
        return True
    return False


def load_json_if_exists(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def iter_experiment_dirs(experiments_root: str | Path) -> List[Path]:
    root = resolve_experiments_root(experiments_root)
    if not root.exists():
        return []

    run_dirs: List[Path] = []
    seen = set()
    for summary_path in root.rglob("summary.json"):
        run_dir = summary_path.parent
        if not is_run_dir(run_dir):
            continue
        key = str(run_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        run_dirs.append(run_dir)
    return run_dirs


def latest_index_path(experiments_root: str | Path) -> Path:
    return resolve_experiments_root(experiments_root) / "_latest.json"


def load_latest_index(experiments_root: str | Path) -> Dict[str, Any]:
    root = resolve_experiments_root(experiments_root)
    payload = load_json_if_exists(root / "_latest.json")
    if not payload:
        return {}

    result = dict(payload)
    if result.get("run_dir"):
        result["run_dir"] = serialize_experiment_dir(root, str(result.get("run_dir", "") or ""))
    for key in ("summary_path", "manifest_path"):
        if result.get(key):
            result[key] = serialize_experiment_dir(root, str(result.get(key, "") or ""))
    return result


def _experiment_sort_key(path: Path) -> tuple[str, str]:
    summary = load_json_if_exists(path / "summary.json")
    return (
        str(summary.get("run_started_at", "") or ""),
        serialize_experiment_dir(path.parent.parent if path.parent.name.isdigit() else path.parent, path),
    )


def find_experiment_dir(experiments_root: str | Path, ref: str | Path) -> Optional[Path]:
    root = resolve_experiments_root(experiments_root)
    raw_ref = str(ref or "").strip()
    if not raw_ref:
        return None

    if raw_ref.lower() == "latest":
        latest = load_latest_index(root)
        if latest.get("run_dir"):
            return resolve_experiment_dir(root, str(latest.get("run_dir", "") or ""))
        return None

    direct = resolve_experiment_dir(root, raw_ref)
    if direct.exists():
        return direct

    candidates = [path for path in iter_experiment_dirs(root) if path.name == raw_ref]
    if not candidates:
        return None

    candidates.sort(key=_experiment_sort_key, reverse=True)
    return candidates[0]
