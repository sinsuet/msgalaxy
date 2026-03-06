"""
Mode router for runtime execution.

Initial non-breaking migration:
- `agent_loop` / `mass` / `vop_maas` are delegated to dedicated mode runners.
"""

from __future__ import annotations

from typing import Optional

from workflow.modes.agent_loop.runner import AgentLoopRunner
from workflow.modes.mass.runner import MassRunner
from workflow.modes.vop_maas.runner import VOPMaaSRunner

from .contracts import ModeRunner


def resolve_mode_runner(mode: str) -> Optional[ModeRunner]:
    """
    Resolve mode runner.

    Returns None for legacy in-orchestrator modes that are not migrated yet.
    """
    normalized = str(mode or "").strip().lower()
    if normalized == "mass":
        return MassRunner()
    if normalized == "agent_loop":
        return AgentLoopRunner()
    if normalized == "vop_maas":
        return VOPMaaSRunner()
    return None
