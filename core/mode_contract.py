"""
Runtime mode contract utilities (active modes only).

Current executable focus:
- agent_loop
- mass

Only active runtime modes are accepted.
"""

from __future__ import annotations

from typing import Any


ACTIVE_RUNTIME_MODES = {"agent_loop", "mass"}
RUNTIME_ALIASES = {}


def normalize_runtime_mode(mode: Any, default: str = "agent_loop") -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in ACTIVE_RUNTIME_MODES:
        return normalized
    if normalized in RUNTIME_ALIASES:
        return str(RUNTIME_ALIASES[normalized])
    return str(default)


def normalize_observability_mode(mode: Any, default: str = "agent_loop") -> str:
    normalized = str(mode or "").strip().lower()
    default_normalized = str(default or "").strip().lower()

    if normalized == "shared":
        return "shared"

    if not normalized:
        if default_normalized == "shared":
            return "shared"
        if default_normalized == "mass":
            return "mass"

    normalized_runtime = normalize_runtime_mode(normalized, default="agent_loop")
    if normalized_runtime == "mass":
        return "mass"
    return "agent_loop"


def is_mass_mode(mode: Any) -> bool:
    return normalize_observability_mode(mode) == "mass"
