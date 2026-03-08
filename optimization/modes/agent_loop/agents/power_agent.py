"""
Power Agent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from ..protocol import AgentTask, PowerMetrics, PowerProposal
from core.exceptions import LLMError
from core.logger import ExperimentLogger
from core.protocol import DesignState
from optimization.llm.gateway import LLMGateway, build_legacy_gateway
from optimization.llm.runtime_client import extract_json_object_text

for proxy_env in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
    if proxy_env in os.environ:
        del os.environ[proxy_env]


class PowerAgent:
    """Power system specialist."""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        temperature: float = 0.6,
        base_url: Optional[str] = None,
        logger: Optional[ExperimentLogger] = None,
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
            "You are the Power Agent for spacecraft layout optimization.\n"
            "Return a valid PowerProposal JSON object only.\n"
            "Use only component IDs provided in the prompt.\n"
            "Focus on power routing, margin, and voltage drop."
        )

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: PowerMetrics,
        iteration: int = 0,
    ) -> PowerProposal:
        try:
            user_prompt = self._build_prompt(task, current_state, current_metrics)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="power_agent",
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
                    role="power_agent",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        response_json,
                        response.as_log_metadata(),
                    ),
                )

            proposal = PowerProposal(**response_json)
            if not proposal.proposal_id or proposal.proposal_id.startswith("POWER_PROP"):
                proposal.proposal_id = f"POWER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            proposal.task_id = task.task_id
            return proposal
        except Exception as exc:
            raise LLMError(f"Power Agent failed: {exc}") from exc

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: PowerMetrics,
    ) -> str:
        prompt = f"""# Power Optimization Task

## Task
- task_id: {task.task_id}
- objective: {task.objective}
- priority: {task.priority}

## Constraints
"""
        for index, constraint in enumerate(task.constraints, 1):
            prompt += f"{index}. {constraint}\n"

        prompt += f"""
## Current Power Metrics
- total_power_w: {current_metrics.total_power:.1f}
- peak_power_w: {current_metrics.peak_power:.1f}
- power_margin_pct: {current_metrics.power_margin:.1f}
- voltage_drop_v: {current_metrics.voltage_drop:.2f}

## Powered Components
"""
        for comp in current_state.components[:5]:
            if comp.power > 0:
                prompt += f"- {comp.id}: power={comp.power:.1f}W\n"

        prompt += "\n## Available Component IDs\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} ({comp.category})\n"

        if task.context:
            prompt += "\n## Extra Context\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\nReturn a valid PowerProposal JSON object only."
        return prompt

    def validate_proposal(
        self,
        proposal: PowerProposal,
        current_state: DesignState,
    ) -> Dict[str, Any]:
        issues = []
        warnings = []
        component_ids = {c.id for c in current_state.components}

        for action in proposal.actions:
            for comp_id in action.target_components:
                if comp_id not in component_ids:
                    issues.append(f"unknown component: {comp_id}")

        if proposal.predicted_metrics.power_margin < 15:
            warnings.append(
                f"low predicted power_margin ({proposal.predicted_metrics.power_margin:.1f}%)"
            )
        if proposal.predicted_metrics.voltage_drop > 1.0:
            warnings.append(
                f"high predicted voltage_drop ({proposal.predicted_metrics.voltage_drop:.2f}V)"
            )

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
        }


if __name__ == "__main__":
    print("Power Agent module created")
