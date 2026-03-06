"""
Reserved vop_maas mode service.

Current behavior:
- call policy_programmer for proposal diagnostics (preview-only),
- delegate executable optimization to mass pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.protocol import DesignState

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


@dataclass
class VOPPolicyProgramService:
    """Reserved new-LLM mode adapter with safe fallback execution."""

    host: "WorkflowOrchestrator"

    def run_pipeline(
        self,
        *,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        host = self.host
        runtime = getattr(host, "runtime_facade", None)
        if runtime is None:
            raise RuntimeError("runtime_facade is not configured")

        host.logger.logger.info(
            "Entering vop_maas reserved mode: policy_program preview + mass execution fallback"
        )

        policy_payload: Dict[str, Any] = {
            "status": "not_called",
            "reason": "",
        }
        context: Any = {"iteration": 1, "mode": "vop_maas"}
        bootstrap_error = ""

        try:
            bootstrap_metrics, bootstrap_violations = runtime.evaluate_design(current_state, 1)
            context = runtime.build_global_context(
                iteration=1,
                design_state=current_state,
                metrics=bootstrap_metrics,
                violations=bootstrap_violations,
                phase="A",
            )
        except Exception as exc:  # pragma: no cover - defensive path
            bootstrap_error = str(exc)
            host.logger.logger.warning(
                "vop_maas context bootstrap failed, fallback to minimal context: %s",
                exc,
            )

        requirement_text = runtime.build_maas_requirement_text(bom_file)
        policy_programmer = getattr(host, "policy_programmer", None)
        if policy_programmer is None or not hasattr(policy_programmer, "generate_policy_program"):
            policy_payload = {
                "status": "unsupported",
                "reason": "policy_programmer_not_configured",
            }
        else:
            try:
                generated = policy_programmer.generate_policy_program(
                    context=context,
                    runtime_constraints=dict(host.runtime_constraints),
                    requirement_text=requirement_text,
                    mode="vop_maas",
                )
                if isinstance(generated, dict):
                    policy_payload = dict(generated)
                    policy_payload.setdefault("status", "ok")
                else:
                    policy_payload = {"status": "ok", "payload": generated}
            except Exception as exc:  # pragma: no cover - defensive path
                policy_payload = {
                    "status": "error",
                    "reason": f"policy_program_generation_failed: {exc}",
                }

        host.logger.log_llm_interaction(
            iteration=1,
            role="policy_program_preview",
            mode="vop_maas",
            request={
                "mode": "vop_maas",
                "requirement_text": requirement_text,
                "runtime_constraints": dict(host.runtime_constraints),
                "bootstrap_error": bootstrap_error,
            },
            response=dict(policy_payload or {}),
        )

        host.logger.log_maas_phase_event(
            {
                "iteration": 1,
                "phase": "V0",
                "status": "completed",
                "details": {
                    "mode": "vop_maas",
                    "policy_status": str(policy_payload.get("status", "")),
                    "bootstrap_error": bootstrap_error,
                    "delegated_execution_mode": "mass",
                },
            }
        )

        final_state = host.maas_pipeline_service.run_pipeline(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=int(max_iterations),
            convergence_threshold=float(convergence_threshold),
        )

        metadata = dict(getattr(final_state, "metadata", {}) or {})
        metadata["optimization_mode"] = "vop_maas"
        metadata["vop_maas_reserved_mode"] = True
        metadata["vop_execution_backend_mode"] = "mass"
        metadata["vop_policy_program"] = dict(policy_payload)
        final_state.metadata = metadata

        host.logger.save_run_manifest(
            {
                "optimization_mode": "vop_maas",
                "status": "COMPLETED",
                "extra": {
                    "delegated_execution_mode": "mass",
                    "policy_status": str(policy_payload.get("status", "")),
                    "policy_reason": str(policy_payload.get("reason", "")),
                },
            }
        )
        return final_state
