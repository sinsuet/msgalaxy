"""
Contracts for LLM controller adapters.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol


class StrategicPlannerController(Protocol):
    """Contract for strategic planning controller."""

    def generate_strategic_plan(self, context: Any) -> Any:
        ...


class IntentModelerController(Protocol):
    """Contract for modeling-intent controller."""

    def generate_modeling_intent(
        self,
        *,
        context: Any,
        runtime_constraints: Dict[str, Any],
        requirement_text: str = "",
    ) -> Any:
        ...

    def get_modeling_intent_diagnostics(self) -> Dict[str, Any]:
        ...


class PolicyProgrammerController(Protocol):
    """Contract for policy-program controller."""

    def generate_policy_program(self, *, context: Any, **kwargs: Any) -> Dict[str, Any]:
        ...
