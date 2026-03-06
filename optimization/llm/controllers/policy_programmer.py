"""
Policy-program controller abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from optimization.llm.contracts import PolicyProgrammerController


@dataclass
class PolicyProgrammer(PolicyProgrammerController):
    """Adapter for policy-program generation in v3/vop flows."""

    delegate: Any

    def generate_policy_program(self, *, context: Any, **kwargs: Any) -> Dict[str, Any]:
        for method_name in (
            "generate_policy_program",
            "generate_operator_program",
            "generate_policy",
        ):
            method = getattr(self.delegate, method_name, None)
            if callable(method):
                result = method(context=context, **kwargs)
                if isinstance(result, dict):
                    return result
                return {"status": "ok", "payload": result}
        return {
            "status": "unsupported",
            "reason": "delegate_missing_policy_program_api",
        }
