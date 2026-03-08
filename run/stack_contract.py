#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stack contract helpers for runtime fail-fast checks.
"""

from __future__ import annotations

from pathlib import Path


STACK_MODE_BINDINGS = {
    "mass": "mass",
    "agent_loop": "agent_loop",
    "vop_maas": "vop_maas",
}

MODE_STACK_BINDINGS = {value: key for key, value in STACK_MODE_BINDINGS.items()}


def _normalize_path_for_compare(*, project_root: Path, path_value: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    path_obj = Path(raw)
    if not path_obj.is_absolute():
        path_obj = (project_root / path_obj).resolve()
    else:
        path_obj = path_obj.resolve()
    return path_obj.as_posix().lower()


def _expected_bom_prefix(*, project_root: Path, stack: str) -> Path:
    if stack == "agent_loop":
        return (project_root / "config" / "bom" / "agent_loop").resolve()
    if stack == "vop_maas":
        return (project_root / "config" / "bom" / "mass").resolve()
    return (project_root / "config" / "bom" / "mass").resolve()


def _expected_base_prefix(*, project_root: Path, stack: str) -> Path:
    return (project_root / "config" / "system" / stack).resolve()


def enforce_mode_stack_contract(
    *,
    mode: str,
    bom_file: str,
    base_config: str,
    project_root: Path,
    context: str = "",
) -> None:
    resolved_mode = str(mode or "").strip()
    if resolved_mode not in MODE_STACK_BINDINGS:
        raise ValueError(
            f"{context}: unsupported mode '{resolved_mode}', "
            f"expected one of {sorted(MODE_STACK_BINDINGS.keys())}"
        )

    stack = MODE_STACK_BINDINGS[resolved_mode]
    expected_bom_prefix = _expected_bom_prefix(project_root=project_root, stack=stack)
    expected_base_prefix = _expected_base_prefix(project_root=project_root, stack=stack)

    bom_norm = _normalize_path_for_compare(project_root=project_root, path_value=bom_file)
    expected_bom_norm = expected_bom_prefix.as_posix().lower()
    if not bom_norm.startswith(expected_bom_norm):
        raise ValueError(
            f"{context}: mode '{resolved_mode}' expects bom path under "
            f"'{expected_bom_prefix.as_posix()}', got '{bom_file}'"
        )

    base_norm = _normalize_path_for_compare(project_root=project_root, path_value=base_config)
    expected_base_norm = expected_base_prefix.as_posix().lower()
    if not base_norm.startswith(expected_base_norm):
        raise ValueError(
            f"{context}: mode '{resolved_mode}' expects base_config path under "
            f"'{expected_base_prefix.as_posix()}', got '{base_config}'"
        )
