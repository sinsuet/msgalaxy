"""
Runtime and observability mode contract utilities for the rebuilt scenario mainline.
"""

from __future__ import annotations

from typing import Any


ACTIVE_RUNTIME_MODES = {"mass"}
OBSERVABILITY_MODES = {"mass", "legacy"}
RUNTIME_ALIASES = {}

_EXECUTION_MODE_MAP = {
    "mass": "mass",
}

_LIFECYCLE_STATE_MAP = {
    "mass": "stable",
}


def normalize_runtime_mode(mode: Any, default: str = "mass") -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in ACTIVE_RUNTIME_MODES:
        return normalized
    if normalized in RUNTIME_ALIASES:
        return str(RUNTIME_ALIASES[normalized])

    default_normalized = str(default or "").strip().lower()
    if default_normalized in ACTIVE_RUNTIME_MODES:
        return default_normalized
    return "mass"


def normalize_observability_mode(mode: Any, default: str = "legacy") -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in OBSERVABILITY_MODES:
        return normalized
    if normalized in ACTIVE_RUNTIME_MODES:
        return normalized

    default_normalized = str(default or "").strip().lower()
    if default_normalized in OBSERVABILITY_MODES:
        return default_normalized
    if default_normalized in ACTIVE_RUNTIME_MODES:
        return default_normalized
    return "legacy"


def resolve_execution_mode(run_mode: Any) -> str:
    normalized = normalize_runtime_mode(run_mode, default="mass")
    return str(_EXECUTION_MODE_MAP.get(normalized, "mass"))


def resolve_lifecycle_state(run_mode: Any) -> str:
    normalized = normalize_runtime_mode(run_mode, default="mass")
    return str(_LIFECYCLE_STATE_MAP.get(normalized, "stable"))


def is_mass_mode(mode: Any) -> bool:
    normalized = normalize_observability_mode(mode, default="legacy")
    return normalized == "mass"
