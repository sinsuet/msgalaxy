"""
DesignState <-> vector codec for pymoo search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from core.protocol import DesignState

from .specs import AxisName, SemanticZone, VariableSpec


@dataclass(frozen=True)
class ComponentBounds:
    x: Tuple[float, float]
    y: Tuple[float, float]
    z: Tuple[float, float]

    def for_axis(self, axis: AxisName) -> Tuple[float, float]:
        if axis == "x":
            return self.x
        if axis == "y":
            return self.y
        return self.z


class DesignStateVectorCodec:
    """
    Encodes/decodes `DesignState` to decision vectors.

    Default mode exposes (x, y, z) for each component. Bounds are always
    explicit and clipped by envelope and optional semantic zones.
    """

    def __init__(
        self,
        base_state: DesignState,
        variable_specs: Optional[Iterable[VariableSpec]] = None,
        semantic_zones: Optional[Iterable[SemanticZone]] = None,
    ) -> None:
        self.base_state = base_state.model_copy(deep=True)
        self._component_index: Dict[str, int] = {
            comp.id: idx for idx, comp in enumerate(self.base_state.components)
        }
        self._semantic_zone_by_component = self._index_semantic_zones(semantic_zones or [])

        if variable_specs:
            self._variable_specs = list(variable_specs)
        else:
            self._variable_specs = self._build_default_specs()

        self.xl = np.asarray([spec.lower_bound for spec in self._variable_specs], dtype=float)
        self.xu = np.asarray([spec.upper_bound for spec in self._variable_specs], dtype=float)

    @property
    def n_var(self) -> int:
        return len(self._variable_specs)

    @property
    def variable_specs(self) -> List[VariableSpec]:
        return list(self._variable_specs)

    @property
    def envelope_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        env = self.base_state.envelope
        size = np.array(
            [env.outer_size.x, env.outer_size.y, env.outer_size.z],
            dtype=float,
        )
        if env.origin == "center":
            env_min = -size / 2.0
            env_max = size / 2.0
        else:
            env_min = np.zeros(3, dtype=float)
            env_max = size
        return env_min, env_max

    def clip(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return np.clip(x, self.xl, self.xu)

    def encode(self, state: DesignState) -> np.ndarray:
        comp_by_id = {comp.id: comp for comp in state.components}
        values = np.zeros(self.n_var, dtype=float)

        for i, spec in enumerate(self._variable_specs):
            comp = comp_by_id[spec.component_id]
            values[i] = getattr(comp.position, spec.axis)

        return values

    def decode(self, x: np.ndarray) -> DesignState:
        x = np.asarray(x, dtype=float).reshape(-1)
        if x.shape[0] != self.n_var:
            raise ValueError(f"Expected vector length {self.n_var}, got {x.shape[0]}")

        state = self.base_state.model_copy(deep=True)
        for i, spec in enumerate(self._variable_specs):
            comp_idx = self._component_index[spec.component_id]
            value = float(x[i])
            if spec.variable_type == "integer":
                value = float(np.rint(value))
            elif spec.variable_type == "binary":
                threshold = (float(spec.lower_bound) + float(spec.upper_bound)) / 2.0
                value = float(spec.upper_bound if value >= threshold else spec.lower_bound)
            setattr(state.components[comp_idx].position, spec.axis, value)

        return state

    def geometry_arrays_from_state(self, state: DesignState) -> Tuple[np.ndarray, np.ndarray]:
        centers = np.array(
            [[comp.position.x, comp.position.y, comp.position.z] for comp in state.components],
            dtype=float,
        )
        half_sizes = np.array(
            [
                [
                    comp.dimensions.x / 2.0,
                    comp.dimensions.y / 2.0,
                    comp.dimensions.z / 2.0,
                ]
                for comp in state.components
            ],
            dtype=float,
        )
        return centers, half_sizes

    def _index_semantic_zones(self, zones: Iterable[SemanticZone]) -> Dict[str, SemanticZone]:
        zone_index: Dict[str, SemanticZone] = {}
        for zone in zones:
            for comp_id in zone.component_ids:
                # First zone wins by design for deterministic clipping.
                if comp_id not in zone_index:
                    zone_index[comp_id] = zone
        return zone_index

    def _build_default_specs(self) -> List[VariableSpec]:
        specs: List[VariableSpec] = []
        for comp in self.base_state.components:
            bounds = self._get_component_center_bounds(comp.id)
            for axis in ("x", "y", "z"):
                lb, ub = bounds.for_axis(axis)  # type: ignore[arg-type]
                specs.append(
                    VariableSpec(
                        name=f"{comp.id}_{axis}",
                        component_id=comp.id,
                        axis=axis,  # type: ignore[arg-type]
                        variable_type="continuous",
                        lower_bound=float(lb),
                        upper_bound=float(ub),
                    )
                )
        return specs

    def _get_component_center_bounds(self, component_id: str) -> ComponentBounds:
        comp = self.base_state.components[self._component_index[component_id]]
        env_min, env_max = self.envelope_bounds

        half = np.array(
            [comp.dimensions.x / 2.0, comp.dimensions.y / 2.0, comp.dimensions.z / 2.0],
            dtype=float,
        )
        lower = env_min + half
        upper = env_max - half

        zone = self._semantic_zone_by_component.get(component_id)
        if zone is not None:
            zone_min = np.asarray(zone.min_corner, dtype=float)
            zone_max = np.asarray(zone.max_corner, dtype=float)
            lower = np.maximum(lower, zone_min)
            upper = np.minimum(upper, zone_max)

        if np.any(lower > upper):
            raise ValueError(
                f"Invalid bounds for component {component_id}: "
                f"lower={lower.tolist()}, upper={upper.tolist()}"
            )

        return ComponentBounds(
            x=(float(lower[0]), float(upper[0])),
            y=(float(lower[1]), float(upper[1])),
            z=(float(lower[2]), float(upper[2])),
        )
