"""
Geometry Agent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from ..protocol import AgentTask, GeometryMetrics, GeometryProposal
from core.exceptions import LLMError
from core.logger import ExperimentLogger
from core.protocol import DesignState
from optimization.llm.gateway import LLMGateway, build_legacy_gateway
from optimization.llm.runtime_client import extract_json_object_text

for proxy_env in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
    if proxy_env in os.environ:
        del os.environ[proxy_env]


class GeometryAgent:
    """Geometry layout specialist."""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        temperature: float = 0.6,
        logger: Optional[ExperimentLogger] = None,
        base_url: Optional[str] = None,
        llm_gateway: Optional[LLMGateway] = None,
        llm_profile: str = "",
    ):
        self.model = model
        self.temperature = temperature
        self.logger = logger
        self.llm_profile = str(llm_profile or "").strip()
        self.llm_client = llm_gateway or build_legacy_gateway(
            api_key=api_key,
            model=model,
            temperature=temperature,
            base_url=base_url,
        )
        self.system_prompt = self._load_system_prompt()

    def _current_llm_log_metadata(self) -> Dict[str, Any]:
        try:
            profile = self.llm_client.resolve_text_profile(self.llm_profile)
            return {
                "profile": profile.name,
                "provider": profile.provider,
                "model": profile.model,
                "api_style": profile.api_style,
                "fallback_used": False,
                "fallback_reason": "",
                "key_source": profile.api_key_source,
                "key_source_masked": profile.key_source_masked,
            }
        except Exception:
            return {
                "profile": str(self.llm_profile or ""),
                "provider": "",
                "model": str(self.model or ""),
                "api_style": "",
                "fallback_used": False,
                "fallback_reason": "",
                "key_source": "",
                "key_source_masked": "",
            }

    @staticmethod
    def _attach_llm_log_metadata(payload: Any, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(payload, dict):
            result = dict(payload)
        else:
            result = {"payload": payload}
        result["_llm"] = dict(metadata or {})
        return result

    def _load_system_prompt(self) -> str:
        return (
            "You are the Geometry Agent for spacecraft layout optimization.\n"
            "Return a valid GeometryProposal JSON object only.\n"
            "Use only component IDs provided in the prompt.\n"
            "Keep actions physically plausible and geometry-focused."
        )

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: GeometryMetrics,
        iteration: int = 0,
    ) -> GeometryProposal:
        try:
            user_prompt = self._build_prompt(task, current_state, current_metrics)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="geometry_agent",
                    request=self._attach_llm_log_metadata(
                        {"messages": messages},
                        self._current_llm_log_metadata(),
                    ),
                    response=None,
                )

            response = self.llm_client.generate_text(
                messages,
                profile_name=self.llm_profile,
                expects_json=True,
            )
            try:
                response_json = json.loads(response.content)
            except json.JSONDecodeError:
                response_json = json.loads(extract_json_object_text(response.content))

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="geometry_agent",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        response_json,
                        response.as_log_metadata(),
                    ),
                )

            proposal = GeometryProposal(**response_json)
            if not proposal.proposal_id or proposal.proposal_id.startswith("GEOM_PROP"):
                proposal.proposal_id = f"GEOM_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            proposal.task_id = task.task_id
            return proposal
        except Exception as exc:
            raise LLMError(f"Geometry Agent failed: {exc}") from exc

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: GeometryMetrics,
    ) -> str:
        prompt = f"""# Geometry Optimization Task

## Task
- task_id: {task.task_id}
- objective: {task.objective}
- priority: {task.priority}

## Constraints
"""
        for index, constraint in enumerate(task.constraints, 1):
            prompt += f"{index}. {constraint}\n"

        prompt += f"""
## Current Geometry Metrics
- min_clearance_mm: {float(current_metrics.min_clearance):.2f}
- com_offset_mm: [{', '.join(f'{float(x):.2f}' for x in current_metrics.com_offset)}]
- inertia: [{', '.join(f'{float(x):.2f}' for x in current_metrics.moment_of_inertia)}]
- packing_efficiency_pct: {float(current_metrics.packing_efficiency):.1f}
- num_collisions: {int(current_metrics.num_collisions)}

## Layout Snapshot
"""
        for comp in current_state.components[:5]:
            prompt += f"- {comp.id}: position={comp.position}, dimensions={comp.dimensions}\n"
        if len(current_state.components) > 5:
            prompt += f"- ... total_components={len(current_state.components)}\n"

        prompt += "\n## Available Component IDs\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} ({comp.category})\n"

        if task.context:
            prompt += "\n## Extra Context\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\nReturn a valid GeometryProposal JSON object only."
        return prompt

    def validate_proposal(
        self,
        proposal: GeometryProposal,
        current_state: DesignState,
    ) -> Dict[str, Any]:
        issues = []
        warnings = []
        component_ids = {c.id for c in current_state.components}

        for action in proposal.actions:
            component_id = getattr(action, "component_id", "")
            if component_id and component_id not in component_ids:
                issues.append(f"unknown component: {component_id}")

            for comp_id in list(getattr(action, "target_components", []) or []):
                if comp_id not in component_ids:
                    issues.append(f"unknown component: {comp_id}")

            if action.op_type == "SWAP":
                comp_a = action.parameters.get("component_a")
                comp_b = action.parameters.get("component_b")
                if comp_a and comp_a not in component_ids:
                    issues.append(f"unknown component: {comp_a}")
                if comp_b and comp_b not in component_ids:
                    issues.append(f"unknown component: {comp_b}")

            if action.op_type == "MOVE":
                range_param = action.parameters.get("range", [])
                if len(range_param) != 2:
                    issues.append("MOVE.range must contain exactly two values")
                elif range_param[0] > range_param[1]:
                    issues.append(f"invalid MOVE.range order: {range_param}")

        if proposal.predicted_metrics.min_clearance < 0:
            issues.append("predicted min_clearance is negative")
        if proposal.predicted_metrics.packing_efficiency > 100:
            issues.append("predicted packing_efficiency exceeds 100%")
        if proposal.confidence < 0.3:
            warnings.append(f"low confidence proposal ({proposal.confidence:.2f})")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
        }


if __name__ == "__main__":
    print("Geometry Agent module created")
