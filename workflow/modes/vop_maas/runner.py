"""
vop_maas mode runner.
"""

from __future__ import annotations

from dataclasses import dataclass

from workflow.runtime.contracts import RuntimeContext


@dataclass(frozen=True)
class VOPMaaSRunner:
    """Delegate vop_maas execution to reserved policy-program service."""

    mode_name: str = "vop_maas"

    def run(self, *, host, current_state, context: RuntimeContext):
        return host.vop_policy_program_service.run_pipeline(
            current_state=current_state,
            bom_file=context.bom_file,
            max_iterations=int(context.max_iterations),
            convergence_threshold=float(context.convergence_threshold),
        )

