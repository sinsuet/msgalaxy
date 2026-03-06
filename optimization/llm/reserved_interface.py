"""
Reserved interfaces for next-generation LLM policy program stack.

This module is intentionally minimal and stable so future implementations can
plug in without touching run/config routing contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


@dataclass
class LLMPolicyContext:
    requirement_text: str
    runtime_constraints: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMPolicyResult:
    status: str
    program_id: str = ""
    actions: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReservedLLMPolicyProgram(Protocol):
    """
    Future policy-program interface for the dedicated LLM scheme.
    """

    def generate(self, context: LLMPolicyContext) -> LLMPolicyResult:
        ...

