"""
Modeling-intent controller abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from optimization.llm.contracts import IntentModelerController

@dataclass
class IntentModeler(IntentModelerController):
    """Adapter that isolates modeling-intent calls from orchestration code."""

    delegate: Any

    def generate_modeling_intent(
        self,
        *,
        context: Any,
        runtime_constraints: Dict[str, Any],
        requirement_text: str = "",
    ):
        generator = getattr(self.delegate, "generate_modeling_intent", None)
        if not callable(generator):
            raise RuntimeError("intent_modeler delegate does not implement generate_modeling_intent")
        return generator(
            context=context,
            runtime_constraints=runtime_constraints,
            requirement_text=requirement_text,
        )

    def get_modeling_intent_diagnostics(self) -> Dict[str, Any]:
        getter = getattr(self.delegate, "get_modeling_intent_diagnostics", None)
        if callable(getter):
            try:
                return dict(getter() or {})
            except Exception:
                return {}
        return {}
