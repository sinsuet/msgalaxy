"""
Runtime and observability mode contract utilities.
"""

from __future__ import annotations

from typing import Any


ACTIVE_RUNTIME_MODES = {"agent_loop", "mass", "vop_maas"}
OBSERVABILITY_MODES = {"agent_loop", "mass", "vop_maas", "legacy"}
RUNTIME_ALIASES = {}

_EXECUTION_MODE_MAP = {
    "agent_loop": "agent_loop",
    "mass": "mass",
    "vop_maas": "mass",
}

_LIFECYCLE_STATE_MAP = {
    "agent_loop": "deprecated",
    "mass": "stable",
    "vop_maas": "experimental",
}


def normalize_runtime_mode(mode: Any, default: str = "agent_loop") -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in ACTIVE_RUNTIME_MODES:
        return normalized
    if normalized in RUNTIME_ALIASES:
        return str(RUNTIME_ALIASES[normalized])

    default_normalized = str(default or "").strip().lower()
    if default_normalized in ACTIVE_RUNTIME_MODES:
        return default_normalized
    return "agent_loop"


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
    return str(_EXECUTION_MODE_MAP.get(normalized, normalized))


def resolve_lifecycle_state(run_mode: Any) -> str:
    normalized = normalize_runtime_mode(run_mode, default="mass")
    return str(_LIFECYCLE_STATE_MAP.get(normalized, "stable"))


def is_mass_mode(mode: Any) -> bool:
    normalized = normalize_observability_mode(mode, default="legacy")
    return normalized in {"mass", "vop_maas"}
