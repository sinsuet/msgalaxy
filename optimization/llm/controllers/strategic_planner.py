"""
Strategic-planning controller abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from optimization.llm.contracts import StrategicPlannerController

@dataclass
class StrategicPlanner(StrategicPlannerController):
    """Adapter that isolates strategic-plan calls from orchestration code."""

    delegate: Any

    def generate_strategic_plan(self, context: Any):
        generator = getattr(self.delegate, "generate_strategic_plan", None)
        if not callable(generator):
            raise RuntimeError("strategic_planner delegate does not implement generate_strategic_plan")
        return generator(context)
