"""
mass mode runner.
"""

from __future__ import annotations

from dataclasses import dataclass

from workflow.runtime.contracts import RuntimeContext


@dataclass(frozen=True)
class MassRunner:
    """Delegate mass execution to MaaS pipeline service."""

    mode_name: str = "mass"

    def run(self, *, host, current_state, context: RuntimeContext):
        return host.maas_pipeline_service.run_pipeline(
            current_state=current_state,
            bom_file=context.bom_file,
            max_iterations=int(context.max_iterations),
            convergence_threshold=float(context.convergence_threshold),
        )
