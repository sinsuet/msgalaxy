"""
Specs for dynamic pymoo problem generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from core.protocol import DesignState


AxisName = Literal["x", "y", "z"]


@dataclass(frozen=True)
class VariableSpec:
    """Decision variable definition for one component axis."""

    name: str
    component_id: str
    axis: AxisName
    lower_bound: float
    upper_bound: float
    variable_type: Literal["continuous", "integer", "binary"] = "continuous"


@dataclass(frozen=True)
class ObjectiveSpec:
    """Objective function definition."""

    name: str
    metric_key: str
    sense: Literal["minimize", "maximize"] = "minimize"
    weight: float = 1.0


@dataclass(frozen=True)
class ConstraintSpec:
    """
    Constraint definition, internally normalized to g(x) <= 0.

    relation semantics:
    - "<=": metric <= target_value        => g = metric - target_value
    - ">=": metric >= target_value        => g = target_value - metric
    - "==": |metric - target_value| <= eq_tolerance
    """

    name: str
    metric_key: str
    relation: Literal["<=", ">=", "=="] = "<="
    target_value: float = 0.0
    eq_tolerance: float = 1e-6


@dataclass(frozen=True)
class SemanticZone:
    """
    Semantic zoning for search-space reduction.

    Coordinates are center-position bounds in millimeters.
    """

    zone_id: str
    min_corner: Tuple[float, float, float]
    max_corner: Tuple[float, float, float]
    component_ids: Tuple[str, ...] = ()


@dataclass
class PymooProblemSpec:
    """
    Full spec used to generate an executable pymoo problem.
    """

    base_state: DesignState
    runtime_constraints: Dict[str, float]
    variable_specs: List[VariableSpec] = field(default_factory=list)
    objective_specs: List[ObjectiveSpec] = field(default_factory=list)
    constraint_specs: List[ConstraintSpec] = field(default_factory=list)
    semantic_zones: List[SemanticZone] = field(default_factory=list)
    thermal_evaluator: Optional[Callable[[DesignState], Dict[str, float]]] = None
    tags: Dict[str, Any] = field(default_factory=dict)


def default_objective_specs() -> List[ObjectiveSpec]:
    """
    Default multi-objective setup:
    - Reduce CG offset.
    - Reduce peak temperature.
    - Reduce inertia imbalance.
    """

    return [
        ObjectiveSpec(name="cg_offset", metric_key="cg_offset", sense="minimize", weight=1.0),
        ObjectiveSpec(name="max_temp", metric_key="max_temp", sense="minimize", weight=1.0),
        ObjectiveSpec(name="moi_imbalance", metric_key="moi_imbalance", sense="minimize", weight=0.2),
    ]


def default_constraint_specs() -> List[ConstraintSpec]:
    """
    Default hard constraints, all expected to satisfy g(x) <= 0.
    """

    return [
        ConstraintSpec(name="collision", metric_key="collision_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="clearance", metric_key="clearance_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="boundary", metric_key="boundary_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="thermal", metric_key="thermal_violation", relation="<=", target_value=0.0),
        ConstraintSpec(name="cg_limit", metric_key="cg_violation", relation="<=", target_value=0.0),
    ]
