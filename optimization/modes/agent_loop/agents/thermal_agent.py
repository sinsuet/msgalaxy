"""
Thermal Agent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from ..protocol import AgentTask, ThermalMetrics, ThermalProposal
from core.exceptions import LLMError
from core.logger import ExperimentLogger
from core.protocol import DesignState
from optimization.llm.gateway import LLMGateway, build_legacy_gateway
from optimization.llm.runtime_client import extract_json_object_text

for proxy_env in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
    if proxy_env in os.environ:
        del os.environ[proxy_env]


class ThermalAgent:
    """Thermal control specialist."""

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
            "You are the Thermal Agent for spacecraft layout optimization.\n"
            "Return a valid ThermalProposal JSON object only.\n"
            "Use only component IDs provided in the prompt.\n"
            "Focus on thermal actions and temperature risk reduction."
        )

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: ThermalMetrics,
        iteration: int = 0,
    ) -> ThermalProposal:
        try:
            user_prompt = self._build_prompt(task, current_state, current_metrics)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="thermal_agent",
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
                    role="thermal_agent",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        response_json,
                        response.as_log_metadata(),
                    ),
                )

            proposal = ThermalProposal(**response_json)
            if not proposal.proposal_id or proposal.proposal_id.startswith("THERM_PROP"):
                proposal.proposal_id = f"THERM_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            proposal.task_id = task.task_id
            return proposal
        except Exception as exc:
            raise LLMError(f"Thermal Agent failed: {exc}") from exc

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: ThermalMetrics,
    ) -> str:
        prompt = f"""# Thermal Optimization Task

## Task
- task_id: {task.task_id}
- objective: {task.objective}
- priority: {task.priority}

## Constraints
"""
        for index, constraint in enumerate(task.constraints, 1):
            prompt += f"{index}. {constraint}\n"

        prompt += f"""
## Current Thermal Metrics
- min_temp_c: {float(current_metrics.min_temp):.1f}
- max_temp_c: {float(current_metrics.max_temp):.1f}
- avg_temp_c: {float(current_metrics.avg_temp):.1f}
- temp_gradient: {float(current_metrics.temp_gradient):.2f}
"""
        if current_metrics.hotspot_components:
            prompt += f"- hotspot_components: {', '.join(current_metrics.hotspot_components)}\n"

        prompt += "\n## High Power Components\n"
        for comp in [c for c in current_state.components if c.power > 5.0][:5]:
            prompt += f"- {comp.id}: power={float(comp.power):.1f}W, position={comp.position}\n"

        prompt += "\n## Available Component IDs\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} ({comp.category})\n"

        if task.context:
            prompt += "\n## Extra Context\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\nReturn a valid ThermalProposal JSON object only."
        return prompt

    def validate_proposal(
        self,
        proposal: ThermalProposal,
        current_state: DesignState,
    ) -> Dict[str, Any]:
        issues = []
        warnings = []
        component_ids = {c.id for c in current_state.components}

        for action in proposal.actions:
            for comp_id in action.target_components:
                if comp_id not in component_ids:
                    issues.append(f"unknown component: {comp_id}")

            if action.op_type in {"ADJUST_LAYOUT", "CHANGE_ORIENTATION"}:
                axis = action.parameters.get("axis")
                if axis not in {"X", "Y", "Z"}:
                    issues.append(f"invalid axis: {axis}")

            if action.op_type == "ADD_HEATSINK":
                face = action.parameters.get("face")
                if face and face not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
                    warnings.append(f"unknown heatsink face: {face}")

            if action.op_type == "MODIFY_COATING":
                emissivity = action.parameters.get("emissivity")
                absorptivity = action.parameters.get("absorptivity")
                if emissivity is not None and not 0 <= emissivity <= 1:
                    warnings.append(f"emissivity out of range [0, 1]: {emissivity}")
                if absorptivity is not None and not 0 <= absorptivity <= 1:
                    warnings.append(f"absorptivity out of range [0, 1]: {absorptivity}")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
        }


if __name__ == "__main__":
    print("Thermal Agent module created")
