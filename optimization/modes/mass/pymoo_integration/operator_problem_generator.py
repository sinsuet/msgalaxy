"""
Operator-program search-space problem generator.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
        forced_slot_actions: Optional[List[str]] = None,
        forced_slot_action_params: Optional[List[Optional[Dict[str, Any]]]] = None,
        codec: Optional[OperatorProgramGenomeCodec] = None,
    ) -> None:
        self.search_space = "operator_program"
        self.n_action_slots = max(1, int(n_action_slots))
        constraints = dict(spec.runtime_constraints or {})
        tags = dict(spec.tags or {})
        min_clearance_mm = float(constraints.get("min_clearance_mm", 5.0))
        max_cg_offset_mm = float(constraints.get("max_cg_offset_mm", 50.0))
        mission_keepout_axis = str(
            tags.get("mission_keepout_axis", constraints.get("mission_keepout_axis", "z"))
        ).strip().lower() or "z"
        mission_keepout_center_mm = float(
            tags.get("mission_keepout_center_mm", constraints.get("mission_keepout_center_mm", 0.0))
        )
        mission_min_separation_mm = float(
            tags.get("mission_min_separation_mm", constraints.get("mission_min_separation_mm", 0.0))
        )
        program_codec = codec or OperatorProgramGenomeCodec(
            base_state=spec.base_state,
            n_action_slots=self.n_action_slots,
            max_group_delta_mm=max_group_delta_mm,
            max_hot_distance_mm=max_hot_distance_mm,
            min_clearance_mm=min_clearance_mm,
            max_cg_offset_mm=max_cg_offset_mm,
            action_safety_tolerance=action_safety_tolerance,
            forced_slot_actions=list(forced_slot_actions or []),
            forced_slot_action_params=list(forced_slot_action_params or []),
            mission_keepout_axis=mission_keepout_axis,
            mission_keepout_center_mm=mission_keepout_center_mm,
            mission_min_separation_mm=mission_min_separation_mm,
        )
        super().__init__(spec=spec, codec=program_codec)
