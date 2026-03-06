"""
Agent-loop mode runner.
"""

from __future__ import annotations

from dataclasses import dataclass

from workflow.runtime.contracts import RuntimeContext


@dataclass(frozen=True)
class AgentLoopRunner:
    """Execute legacy agent-loop optimization flow."""

    mode_name: str = "agent_loop"

    def run(self, *, host, current_state, context: RuntimeContext):
        return host.agent_loop_service.run(
            current_state=current_state,
            max_iterations=int(context.max_iterations),
            convergence_threshold=float(context.convergence_threshold),
        )
