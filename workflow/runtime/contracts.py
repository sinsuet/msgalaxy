"""
Runtime contracts for mode routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from core.protocol import DesignState
    from workflow.orchestrator import WorkflowOrchestrator


@dataclass(frozen=True)
class RuntimeContext:
    """Immutable runtime inputs shared with mode runners."""

    bom_file: Optional[str]
    max_iterations: int
    convergence_threshold: float


class ModeRunner(Protocol):
    """Execution contract for optimization mode runners."""

    mode_name: str

    def run(
        self,
        *,
        host: "WorkflowOrchestrator",
        current_state: "DesignState",
        context: RuntimeContext,
    ) -> "DesignState":
        ...
