"""
Operator-program search-space problem generator.
"""

from __future__ import annotations

from typing import Optional

from .operator_program_codec import OperatorProgramGenomeCodec
from .problem_generator import PymooProblemGenerator
from .specs import PymooProblemSpec


class OperatorProgramProblemGenerator(PymooProblemGenerator):
    """
    Pymoo problem generator with operator-program genome as decision variables.
    """

    def __init__(
        self,
        *,
        spec: PymooProblemSpec,
        n_action_slots: int = 3,
        max_group_delta_mm: float = 10.0,
        max_hot_distance_mm: float = 12.0,
        action_safety_tolerance: float = 0.5,
        codec: Optional[OperatorProgramGenomeCodec] = None,
    ) -> None:
        self.search_space = "operator_program"
        self.n_action_slots = max(1, int(n_action_slots))
        constraints = dict(spec.runtime_constraints or {})
        min_clearance_mm = float(constraints.get("min_clearance_mm", 5.0))
        max_cg_offset_mm = float(constraints.get("max_cg_offset_mm", 50.0))
        program_codec = codec or OperatorProgramGenomeCodec(
            base_state=spec.base_state,
            n_action_slots=self.n_action_slots,
            max_group_delta_mm=max_group_delta_mm,
            max_hot_distance_mm=max_hot_distance_mm,
            min_clearance_mm=min_clearance_mm,
            max_cg_offset_mm=max_cg_offset_mm,
            action_safety_tolerance=action_safety_tolerance,
        )
        super().__init__(spec=spec, codec=program_codec)
