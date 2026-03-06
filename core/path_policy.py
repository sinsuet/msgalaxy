"""
Helpers for serializing persisted paths into portable relative forms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalize_raw_path(path_value: Any) -> str:
    return str(path_value or "").strip()


def _normalize_roots(roots: Iterable[Any]) -> List[Path]:
    normalized: List[Path] = []
    seen = set()
    for root in roots:
        raw = _normalize_raw_path(root)
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)
        key = candidate.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
    return normalized


def serialize_rooted_path(path_value: Any, *roots: Any) -> str:
    raw = _normalize_raw_path(path_value)
    if not raw:
        return ""

    path = Path(raw)
    if not path.is_absolute():
        return path.as_posix()

    resolved = path.resolve(strict=False)
    for root in _normalize_roots(roots):
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        return relative.as_posix() or "."

    return resolved.as_posix()


def serialize_repo_path(path_value: Any) -> str:
    return serialize_rooted_path(path_value, PROJECT_ROOT)


def serialize_artifact_path(base_dir: Any, path_value: Any) -> str:
    raw_base = _normalize_raw_path(base_dir)
    if not raw_base:
        return serialize_repo_path(path_value)
    base_path = Path(raw_base)
    if not base_path.is_absolute():
        base_path = (PROJECT_ROOT / base_path).resolve(strict=False)
    else:
        base_path = base_path.resolve(strict=False)
    return serialize_rooted_path(path_value, PROJECT_ROOT, base_path.parent)


def serialize_run_path(run_dir: Any, path_value: Any) -> str:
    raw_run = _normalize_raw_path(run_dir)
    if not raw_run:
        return serialize_repo_path(path_value)
    run_path = Path(raw_run)
    if not run_path.is_absolute():
        run_path = (PROJECT_ROOT / run_path).resolve(strict=False)
    else:
        run_path = run_path.resolve(strict=False)
    return serialize_rooted_path(path_value, run_path, PROJECT_ROOT)


def resolve_rooted_path(path_value: Any, *roots: Any) -> Path:
    raw = _normalize_raw_path(path_value)
    if not raw:
        return Path()

    path = Path(raw)
    if path.is_absolute():
        return path.resolve(strict=False)

    normalized_roots = _normalize_roots(roots)
    for root in normalized_roots:
        candidate = (root / path).resolve(strict=False)
        if candidate.exists():
            return candidate

    if normalized_roots:
        return (normalized_roots[0] / path).resolve(strict=False)
    return (PROJECT_ROOT / path).resolve(strict=False)


def resolve_repo_path(path_value: Any) -> Path:
    return resolve_rooted_path(path_value, PROJECT_ROOT)


def resolve_artifact_or_repo_path(base_dir: Any, path_value: Any) -> Path:
    raw_base = _normalize_raw_path(base_dir)
    if not raw_base:
        return resolve_repo_path(path_value)
    base_path = Path(raw_base)
    if not base_path.is_absolute():
        base_path = (PROJECT_ROOT / base_path).resolve(strict=False)
    else:
        base_path = base_path.resolve(strict=False)
    return resolve_rooted_path(path_value, PROJECT_ROOT, base_path.parent)


def resolve_run_or_repo_path(run_dir: Any, path_value: Any) -> Path:
    raw_run = _normalize_raw_path(run_dir)
    if not raw_run:
        return resolve_repo_path(path_value)
    run_path = Path(raw_run)
    if not run_path.is_absolute():
        run_path = (PROJECT_ROOT / run_path).resolve(strict=False)
    else:
        run_path = run_path.resolve(strict=False)
    return resolve_rooted_path(path_value, run_path, PROJECT_ROOT)
