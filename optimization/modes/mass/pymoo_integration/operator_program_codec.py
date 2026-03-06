"""
Operator-program genome codec for OP-MaaS search space.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.protocol import DesignState
from optimization.modes.mass.operator_program import (
    OperatorAction,
    OperatorProgram,
    validate_operator_program,
)
from simulation.structural_physics import calculate_cg_offset

from .constraints import compute_boundary_violation, compute_geometry_violation_metrics


_SUPPORTED_ACTIONS: Tuple[str, ...] = (
    "group_move",
    "cg_recenter",
    "hot_spread",
    "swap",
    "add_heatstrap",
    "set_thermal_contact",
    "add_bracket",
    "stiffener_insert",
    "bus_proximity_opt",
    "fov_keepout_push",
)
_SUPPORTED_AXES: Tuple[str, ...] = ("x", "y", "z")


class OperatorProgramGenomeCodec:
    """
    Compact numeric genome <-> executable design-state transform.

    Per action slot uses 6 numeric genes:
    - action selector
    - component_a selector
    - component_b selector
    - axis selector
    - magnitude (-1..1)
    - focus ratio selector (0..1)
    """

    def __init__(
        self,
        *,
        base_state: DesignState,
        n_action_slots: int = 3,
        max_group_delta_mm: float = 10.0,
        max_hot_distance_mm: float = 12.0,
        min_clearance_mm: float = 5.0,
        max_cg_offset_mm: float = 50.0,
        action_safety_tolerance: float = 0.5,
        forced_slot_actions: Optional[List[str]] = None,
        forced_slot_action_params: Optional[List[Optional[Dict[str, Any]]]] = None,
        mission_keepout_axis: str = "z",
        mission_keepout_center_mm: float = 0.0,
        mission_min_separation_mm: float = 0.0,
    ) -> None:
        self.base_state = base_state.model_copy(deep=True)
        self.component_ids = [str(comp.id) for comp in list(self.base_state.components)]
        self._component_index = {
            str(comp.id): idx for idx, comp in enumerate(self.base_state.components)
        }

        self.n_action_slots = max(1, min(int(n_action_slots), 10))
        self._slot_width = 6
        self.max_group_delta_mm = max(1.0, float(max_group_delta_mm))
        self.max_hot_distance_mm = max(1.0, float(max_hot_distance_mm))
        self.min_clearance_mm = max(0.0, float(min_clearance_mm))
        self.max_cg_offset_mm = max(1.0, float(max_cg_offset_mm))
        self.action_safety_tolerance = max(0.0, float(action_safety_tolerance))
        normalized_axis = str(mission_keepout_axis or "z").strip().lower()
        if normalized_axis not in {"x", "y", "z"}:
            normalized_axis = "z"
        self.mission_keepout_axis = normalized_axis
        self.mission_keepout_center_mm = float(mission_keepout_center_mm)
        self.mission_min_separation_mm = max(0.0, float(mission_min_separation_mm))
        normalized_forced_actions: List[str] = []
        for action in list(forced_slot_actions or []):
            name = str(action or "").strip().lower()
            if not name or name not in _SUPPORTED_ACTIONS:
                continue
            normalized_forced_actions.append(name)
            if len(normalized_forced_actions) >= self.n_action_slots:
                break
        self._forced_slot_actions = normalized_forced_actions
        raw_forced_slot_params = list(forced_slot_action_params or [])
        normalized_forced_slot_params: List[Dict[str, Any]] = []
        for slot in range(len(self._forced_slot_actions)):
            if slot >= len(raw_forced_slot_params):
                normalized_forced_slot_params.append({})
                continue
            payload = raw_forced_slot_params[slot]
            if isinstance(payload, dict):
                normalized_forced_slot_params.append(dict(payload))
            else:
                normalized_forced_slot_params.append({})
        self._forced_slot_action_params = normalized_forced_slot_params
        self._hot_component_ids = self._build_hot_component_candidates()

        self.xl, self.xu = self._build_bounds()
        self._neutral_genome = self._build_neutral_genome()

    @property
    def n_var(self) -> int:
        return int(self.xl.shape[0])

    @property
    def envelope_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        env = self.base_state.envelope
        size = np.asarray(
            [env.outer_size.x, env.outer_size.y, env.outer_size.z],
            dtype=float,
        )
        if str(env.origin).strip().lower() == "center":
            return (-0.5 * size, 0.5 * size)
        return (np.zeros(3, dtype=float), size)

    def clip(self, x: np.ndarray) -> np.ndarray:
        arr = np.asarray(x, dtype=float).reshape(-1)
        if arr.shape[0] != self.n_var:
            if arr.shape[0] < self.n_var:
                padded = np.zeros(self.n_var, dtype=float)
                padded[: arr.shape[0]] = arr
                arr = padded
            else:
                arr = arr[: self.n_var]
        return np.clip(arr, self.xl, self.xu)

    def encode(self, state: DesignState) -> np.ndarray:
        vector = np.asarray(self._neutral_genome, dtype=float).copy()
        if not self.component_ids:
            return vector

        state_map = {str(comp.id): comp for comp in list(state.components)}
        aligned_ids = [comp_id for comp_id in self.component_ids if comp_id in self._component_index]
        if not aligned_ids:
            return vector

        centers_state: List[np.ndarray] = []
        centers_base: List[np.ndarray] = []
        masses: List[float] = []
        for comp_id in aligned_ids:
            base_idx = self._component_index.get(comp_id)
            if base_idx is None or base_idx >= len(self.base_state.components):
                continue
            base_comp = self.base_state.components[base_idx]
            state_comp = state_map.get(comp_id, base_comp)

            centers_state.append(
                np.asarray(
                    [
                        float(state_comp.position.x),
                        float(state_comp.position.y),
                        float(state_comp.position.z),
                    ],
                    dtype=float,
                )
            )
            centers_base.append(
                np.asarray(
                    [
                        float(base_comp.position.x),
                        float(base_comp.position.y),
                        float(base_comp.position.z),
                    ],
                    dtype=float,
                )
            )
            masses.append(max(float(getattr(state_comp, "mass", 0.0) or 0.0), 1e-6))

        if not centers_state:
            return vector

        centers_state_arr = np.asarray(centers_state, dtype=float)
        centers_base_arr = np.asarray(centers_base, dtype=float)
        masses_arr = np.asarray(masses, dtype=float)
        displacement = centers_state_arr - centers_base_arr
        displacement_norm = np.linalg.norm(displacement, axis=1)

        total_mass = float(np.sum(masses_arr))
        if total_mass > 1e-9:
            target = self._center_target(state)
            com = np.sum(centers_state_arr * masses_arr.reshape(-1, 1), axis=0) / total_mass
            com_offset = com - target
        else:
            com_offset = np.zeros(3, dtype=float)

        axis_order = np.argsort(-np.abs(com_offset))
        primary_axis = int(axis_order[0]) if axis_order.size >= 1 else 0
        secondary_axis = int(axis_order[1]) if axis_order.size >= 2 else primary_axis

        # Slot-1 keeps cg_recenter to preserve feasibility-first behavior.
        slot = 0
        offset = slot * self._slot_width
        vector[offset + 0] = self._action_selector("cg_recenter")
        vector[offset + 3] = float(primary_axis)
        com_axis = float(com_offset[primary_axis])
        if abs(com_axis) > 1e-9:
            signed = (-1.0 if com_axis > 0.0 else 1.0) * min(
                abs(com_axis) / max(float(self.max_group_delta_mm), 1e-9),
                1.0,
            )
            vector[offset + 4] = float(np.clip(signed, -1.0, 1.0))
        vector[offset + 5] = float(
            np.clip(
                float(np.linalg.norm(com_offset)) / max(float(self.max_cg_offset_mm), 1e-9),
                0.0,
                1.0,
            )
        )

        ranked = np.argsort(-displacement_norm)
        if ranked.size >= 1 and self.n_action_slots >= 2:
            lead = int(ranked[0])
            alt = int(ranked[1]) if ranked.size >= 2 else lead
            slot = 1
            offset = slot * self._slot_width
            vector[offset + 0] = self._action_selector("group_move")
            vector[offset + 1] = float(lead)
            vector[offset + 2] = float(alt)
            vector[offset + 3] = float(primary_axis)
            axis_shift = float(displacement[lead, primary_axis])
            vector[offset + 4] = float(
                np.clip(
                    axis_shift / max(float(self.max_group_delta_mm), 1e-9),
                    -1.0,
                    1.0,
                )
            )
            vector[offset + 5] = float(
                np.clip(
                    float(displacement_norm[lead]) / max(float(self.max_group_delta_mm), 1e-9),
                    0.0,
                    1.0,
                )
            )

        if ranked.size >= 2 and self.n_action_slots >= 3:
            first = int(ranked[0])
            second = int(ranked[1])
            slot = 2
            offset = slot * self._slot_width
            vector[offset + 0] = self._action_selector("hot_spread")
            vector[offset + 1] = float(first)
            vector[offset + 2] = float(second)
            vector[offset + 3] = float(secondary_axis)
            state_gap = abs(float(centers_state_arr[first, secondary_axis] - centers_state_arr[second, secondary_axis]))
            base_gap = abs(float(centers_base_arr[first, secondary_axis] - centers_base_arr[second, secondary_axis]))
            gap_delta = state_gap - base_gap
            vector[offset + 4] = float(
                np.clip(
                    gap_delta / max(float(self.max_hot_distance_mm), 1e-9),
                    -1.0,
                    1.0,
                )
            )
            vector[offset + 5] = float(
                np.clip(
                    max(float(displacement_norm[first]), float(displacement_norm[second])) /
                    max(float(self.max_hot_distance_mm), 1e-9),
                    0.0,
                    1.0,
                )
            )

        return self.clip(vector)

    def decode_program(self, x: np.ndarray) -> OperatorProgram:
        vector = self.clip(x)
        if not self.component_ids:
            return self._fallback_program(vector, reason="empty_components")

        actions: List[OperatorAction] = []
        for slot in range(self.n_action_slots):
            offset = slot * self._slot_width
            action_name = self._select_action_for_slot(
                slot=slot,
                selector=float(vector[offset + 0]),
            )
            forced_params: Dict[str, Any] = {}
            if slot < len(self._forced_slot_action_params):
                candidate = self._forced_slot_action_params[slot]
                if isinstance(candidate, dict):
                    forced_params = dict(candidate)
            axis = _SUPPORTED_AXES[self._round_index(vector[offset + 3], upper=2)]
            comp_a = self._component_from_selector(vector[offset + 1])
            comp_b = self._component_from_selector(vector[offset + 2], fallback=comp_a)
            magnitude = float(np.clip(vector[offset + 4], -1.0, 1.0))
            focus_ratio = 0.35 + 0.60 * float(np.clip(vector[offset + 5], 0.0, 1.0))

            params = self._build_action_params(
                action_name=action_name,
                comp_a=comp_a,
                comp_b=comp_b,
                axis=axis,
                magnitude=magnitude,
                focus_ratio=focus_ratio,
            )
            if forced_params:
                merged_params = dict(params)
                for key, value in forced_params.items():
                    merged_params[str(key)] = value
                params = merged_params
            actions.append(
                OperatorAction(
                    action=action_name,  # type: ignore[arg-type]
                    params=params,
                    note=f"slot_{slot + 1}",
                )
            )

        program = OperatorProgram(
            program_id=self._build_program_id(vector),
            rationale="operator_program_genome_decode",
            actions=actions,
            metadata={
                "search_space": "operator_program",
                "n_action_slots": int(self.n_action_slots),
                "component_count": int(len(self.component_ids)),
            },
        )
        validated = validate_operator_program(
            program,
            component_ids=self.component_ids,
            max_actions=self.n_action_slots,
        )
        if validated.get("is_valid", False):
            return validated["program"]
        return self._fallback_program(vector, reason="validator_failed")

    def decode(self, x: np.ndarray) -> DesignState:
        program = self.decode_program(x)
        state = self.base_state.model_copy(deep=True)
        self.apply_program_to_state(state, program)
        return state

    def geometry_arrays_from_state(self, state: DesignState) -> Tuple[np.ndarray, np.ndarray]:
        centers = np.asarray(
            [[comp.position.x, comp.position.y, comp.position.z] for comp in state.components],
            dtype=float,
        )
        half_sizes = np.asarray(
            [
                [
                    float(comp.dimensions.x) / 2.0,
                    float(comp.dimensions.y) / 2.0,
                    float(comp.dimensions.z) / 2.0,
                ]
                for comp in state.components
            ],
            dtype=float,
        )
        return centers, half_sizes

    def build_seed_population(
        self,
        *,
        reference_state: Optional[DesignState] = None,
        max_count: int = 8,
    ) -> np.ndarray:
        """
        Build deterministic operator-genome seeds for NSGA-II warm-start.
        """
        upper = max(1, int(max_count))
        state = (
            reference_state.model_copy(deep=True)
            if reference_state is not None
            else self.base_state.model_copy(deep=True)
        )
        seeds: List[np.ndarray] = [
            self.clip(self.encode(state)),
            np.asarray(self._neutral_genome, dtype=float).copy(),
        ]
        if not self.component_ids:
            return np.asarray(seeds, dtype=float)

        centers, _ = self.geometry_arrays_from_state(state)
        if centers.size <= 0:
            return np.asarray(seeds, dtype=float)

        masses = np.asarray(
            [max(float(comp.mass), 1e-6) for comp in state.components],
            dtype=float,
        )
        target = self._center_target(state)
        total_mass = float(np.sum(masses))
        if total_mass <= 1e-9:
            return np.asarray(seeds, dtype=float)
        com = np.sum(centers * masses.reshape(-1, 1), axis=0) / total_mass
        offset = com - target
        axis_order = np.argsort(-np.abs(offset))
        primary_axis = int(axis_order[0])
        secondary_axis = int(axis_order[1]) if axis_order.size > 1 else primary_axis

        heavy_idx = np.argsort(-masses)
        comp_a = int(heavy_idx[0])
        comp_b = int(heavy_idx[1]) if heavy_idx.size > 1 else int(heavy_idx[0])

        def _base_seed() -> np.ndarray:
            return np.asarray(self._neutral_genome, dtype=float).copy()

        def _set_slot(
            vec: np.ndarray,
            *,
            slot: int,
            action_selector: float,
            comp_i: int,
            comp_j: int,
            axis_i: int,
            magnitude: float,
            focus: float,
        ) -> None:
            offset_idx = slot * self._slot_width
            vec[offset_idx + 0] = float(action_selector)
            vec[offset_idx + 1] = float(comp_i)
            vec[offset_idx + 2] = float(comp_j)
            vec[offset_idx + 3] = float(axis_i)
            vec[offset_idx + 4] = float(np.clip(magnitude, -1.0, 1.0))
            vec[offset_idx + 5] = float(np.clip(focus, 0.0, 1.0))

        dir_primary = -1.0 if float(offset[primary_axis]) >= 0.0 else 1.0
        dir_secondary = -1.0 if float(offset[secondary_axis]) >= 0.0 else 1.0

        seed = _base_seed()
        _set_slot(
            seed,
            slot=0,
            action_selector=self._action_selector("cg_recenter"),
            comp_i=comp_a,
            comp_j=comp_b,
            axis_i=primary_axis,
            magnitude=0.45 * dir_primary,
            focus=0.42,
        )
        seeds.append(seed)

        if self.n_action_slots >= 2:
            seed = _base_seed()
            _set_slot(
                seed,
                slot=0,
                action_selector=self._action_selector("cg_recenter"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=primary_axis,
                magnitude=0.95 * dir_primary,
                focus=1.0,
            )
            _set_slot(
                seed,
                slot=1,
                action_selector=self._action_selector("cg_recenter"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=secondary_axis,
                magnitude=0.85 * dir_secondary,
                focus=1.0,
            )
            seeds.append(seed)

        if self.n_action_slots >= 2:
            seed = _base_seed()
            _set_slot(
                seed,
                slot=0,
                action_selector=self._action_selector("cg_recenter"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=primary_axis,
                magnitude=0.65 * dir_primary,
                focus=0.50,
            )
            _set_slot(
                seed,
                slot=1,
                action_selector=self._action_selector("group_move"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=primary_axis,
                magnitude=0.30 * dir_primary,
                focus=0.45,
            )
            seeds.append(seed)

        if self.n_action_slots >= 3:
            seed = _base_seed()
            _set_slot(
                seed,
                slot=0,
                action_selector=self._action_selector("cg_recenter"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=primary_axis,
                magnitude=0.55 * dir_primary,
                focus=0.50,
            )
            _set_slot(
                seed,
                slot=1,
                action_selector=self._action_selector("cg_recenter"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=secondary_axis,
                magnitude=0.35 * dir_secondary,
                focus=0.40,
            )
            _set_slot(
                seed,
                slot=2,
                action_selector=self._action_selector("group_move"),
                comp_i=comp_a,
                comp_j=comp_b,
                axis_i=secondary_axis,
                magnitude=0.20 * dir_secondary,
                focus=0.45,
            )
            seeds.append(seed)

        unique: List[np.ndarray] = []
        seen: set[Tuple[float, ...]] = set()
        for vec in seeds:
            clipped = self.clip(vec)
            key = tuple(np.round(clipped, 6).tolist())
            if key in seen:
                continue
            seen.add(key)
            unique.append(clipped)
            if len(unique) >= upper:
                break
        return np.asarray(unique, dtype=float)

    def apply_program_to_state(self, state: DesignState, program: OperatorProgram) -> None:
        component_ids = [str(comp.id) for comp in state.components]
        validation = validate_operator_program(program, component_ids=component_ids)
        if not bool(validation.get("is_valid", False)):
            return

        parsed = validation["program"]
        prev_score = self._layout_risk_score(state)
        for action in parsed.actions:
            backup = state.model_copy(deep=True)
            params = dict(action.params or {})
            if action.action == "group_move":
                self._apply_group_move(state, params)
            elif action.action == "cg_recenter":
                self._apply_cg_recenter(state, params)
            elif action.action == "hot_spread":
                self._apply_hot_spread(state, params)
            elif action.action == "swap":
                self._apply_swap(state, params)
            elif action.action == "add_heatstrap":
                self._apply_add_heatstrap(state, params)
            elif action.action == "set_thermal_contact":
                self._apply_set_thermal_contact(state, params)
            elif action.action == "add_bracket":
                self._apply_add_bracket(state, params)
            elif action.action == "stiffener_insert":
                self._apply_stiffener_insert(state, params)
            elif action.action == "bus_proximity_opt":
                self._apply_bus_proximity_opt(state, params)
            elif action.action == "fov_keepout_push":
                self._apply_fov_keepout_push(state, params)

            next_score = self._layout_risk_score(state)
            if next_score > (prev_score + self.action_safety_tolerance):
                self._restore_state_positions(state=state, backup=backup)
                continue
            prev_score = next_score

    def _build_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        xl: List[float] = []
        xu: List[float] = []
        component_upper = max(len(self.component_ids) - 1, 0)
        for _ in range(self.n_action_slots):
            xl.extend([0.0, 0.0, 0.0, 0.0, -1.0, 0.0])
            xu.extend(
                [
                    float(len(_SUPPORTED_ACTIONS) - 1),
                    float(component_upper),
                    float(component_upper),
                    float(len(_SUPPORTED_AXES) - 1),
                    1.0,
                    1.0,
                ]
            )
        return np.asarray(xl, dtype=float), np.asarray(xu, dtype=float)

    def _build_neutral_genome(self) -> np.ndarray:
        neutral = np.zeros(self.n_var, dtype=float)
        for slot in range(self.n_action_slots):
            offset = slot * self._slot_width
            if slot < len(self._forced_slot_actions):
                action_name = self._forced_slot_actions[slot]
            else:
                action_name = "cg_recenter"
            neutral[offset + 0] = self._action_selector(action_name)
            neutral[offset + 1] = float(slot % max(len(self.component_ids), 1))
            neutral[offset + 2] = float((slot + 1) % max(len(self.component_ids), 1))
            neutral[offset + 3] = float(slot % len(_SUPPORTED_AXES))
            neutral[offset + 4] = 0.0
            neutral[offset + 5] = 0.5
        return neutral

    def _build_program_id(self, vector: np.ndarray) -> str:
        digest = hashlib.sha1(np.round(vector, 3).tobytes()).hexdigest()[:10]
        return f"op_genome_{digest}"

    def _select_action_for_slot(self, *, slot: int, selector: float) -> str:
        if slot < len(self._forced_slot_actions):
            return str(self._forced_slot_actions[slot])
        if slot <= 0:
            return "cg_recenter"
        idx = self._round_index(selector, upper=len(_SUPPORTED_ACTIONS) - 1)
        return str(_SUPPORTED_ACTIONS[idx])

    @staticmethod
    def _action_selector(action_name: str) -> float:
        try:
            idx = int(_SUPPORTED_ACTIONS.index(str(action_name)))
        except Exception:
            idx = int(_SUPPORTED_ACTIONS.index("cg_recenter"))
        return float(idx)

    def _fallback_program(self, vector: np.ndarray, reason: str) -> OperatorProgram:
        _ = vector
        return OperatorProgram(
            program_id=f"op_genome_fallback_{reason}",
            rationale=f"fallback:{reason}",
            actions=[
                OperatorAction(
                    action="cg_recenter",
                    params={
                        "axes": ["x", "y"],
                        "strength": 0.35,
                        "focus_ratio": 0.65,
                    },
                    note="fallback_action",
                )
            ],
            metadata={"decode_fallback": True, "reason": str(reason)},
        )

    @staticmethod
    def _round_index(value: float, *, upper: int) -> int:
        rounded = int(np.rint(float(value)))
        return int(min(max(rounded, 0), int(upper)))

    def _component_from_selector(self, selector: float, fallback: Optional[str] = None) -> str:
        if not self.component_ids:
            return ""
        idx = self._round_index(selector, upper=len(self.component_ids) - 1)
        comp_id = str(self.component_ids[idx])
        if comp_id:
            return comp_id
        return str(fallback or self.component_ids[0])

    def _safe_pair(self, comp_a: str, comp_b: str) -> List[str]:
        if not self.component_ids:
            return []
        first = str(comp_a or self.component_ids[0])
        second = str(comp_b or first)
        if second == first:
            alt = next((cid for cid in self.component_ids if cid != first), first)
            second = str(alt)
        if first == second:
            return [first]
        return [first, second]

    def _build_action_params(
        self,
        *,
        action_name: str,
        comp_a: str,
        comp_b: str,
        axis: str,
        magnitude: float,
        focus_ratio: float,
    ) -> Dict[str, Any]:
        pair = self._safe_pair(comp_a, comp_b)
        if action_name == "group_move":
            return {
                "component_ids": [pair[0]] if pair else [comp_a],
                "axis": axis,
                "delta_mm": float(magnitude * self.max_group_delta_mm),
                "focus_ratio": float(max(focus_ratio, 0.55)),
            }
        if action_name == "cg_recenter":
            axes = [axis]
            if axis in {"x", "y"}:
                axes = ["x", "y"]
            return {
                "axes": axes,
                "strength": float(min(1.0, 0.25 + 0.55 * abs(magnitude))),
                "focus_ratio": float(max(focus_ratio, 0.60)),
            }
        if action_name == "hot_spread":
            hot_pair = self._select_hot_pair(default_pair=pair)
            return {
                "component_ids": hot_pair,
                "axis": axis,
                "min_pair_distance_mm": float(2.0 + abs(magnitude) * self.max_hot_distance_mm),
                "spread_strength": float(min(1.0, 0.2 + abs(magnitude) * 0.8)),
                "focus_ratio": float(max(focus_ratio, 0.55)),
            }
        if action_name == "swap":
            return {
                "component_a": pair[0] if pair else comp_a,
                "component_b": pair[1] if len(pair) >= 2 else pair[0] if pair else comp_a,
            }
        if action_name == "add_heatstrap":
            hot_pair = self._select_hot_pair(default_pair=pair)
            update_mode = "set"
            return {
                "component_ids": hot_pair,
                "conductance": float(180.0 + abs(magnitude) * 260.0),
                "update_mode": update_mode,
                "focus_ratio": float(max(focus_ratio, 0.62)),
            }
        if action_name == "set_thermal_contact":
            update_mode = "set"
            return {
                "source_component": pair[0] if pair else comp_a,
                "target_component_ids": [pair[1]] if len(pair) >= 2 else [],
                "conductance": float(160.0 + abs(magnitude) * 240.0),
                "update_mode": update_mode,
                "focus_ratio": float(max(focus_ratio, 0.68)),
            }
        if action_name == "add_bracket":
            return {
                "component_ids": [pair[0]] if pair else [comp_a],
                "axes": ["x", "y"] if axis in {"x", "y"} else [axis],
                "stiffness_gain": float(min(1.0, 0.2 + abs(magnitude) * 0.6)),
                "focus_ratio": float(max(focus_ratio, 0.65)),
            }
        if action_name == "stiffener_insert":
            axes = [axis]
            if axis in {"x", "y"}:
                axes = ["x", "y", "z"]
            return {
                "component_ids": pair if pair else [comp_a],
                "axes": axes,
                "stiffness_gain": float(min(1.0, 0.3 + abs(magnitude) * 0.6)),
                "focus_ratio": float(max(focus_ratio, 0.60)),
            }
        if action_name == "bus_proximity_opt":
            return {
                "source_component": pair[0] if pair else comp_a,
                "target_component_ids": [pair[1]] if len(pair) >= 2 else [],
                "axes": ["x", "y"] if axis in {"x", "y"} else [axis],
                "focus_ratio": float(max(focus_ratio, 0.65)),
            }
        if action_name == "fov_keepout_push":
            preferred_side = "auto"
            if magnitude > 0.2:
                preferred_side = "positive"
            elif magnitude < -0.2:
                preferred_side = "negative"
            base_sep = float(max(self.mission_min_separation_mm, 0.0))
            if base_sep > 0.0:
                min_sep = float(base_sep * (1.0 + 0.25 * abs(magnitude)))
            else:
                min_sep = float(8.0 + abs(magnitude) * 24.0)
            return {
                "component_ids": pair if pair else [comp_a],
                "axis": str(self.mission_keepout_axis),
                "keepout_center_mm": float(self.mission_keepout_center_mm),
                "min_separation_mm": float(min_sep),
                "preferred_side": preferred_side,
                "focus_ratio": float(max(focus_ratio, 0.66)),
            }
        return {
            "component_a": pair[0] if pair else comp_a,
            "component_b": pair[1] if len(pair) >= 2 else pair[0] if pair else comp_a,
        }

    def _apply_group_move(self, state: DesignState, params: Dict[str, Any]) -> None:
        comp_ids = [str(v) for v in list(params.get("component_ids", [])) if str(v)]
        axis = str(params.get("axis", "x")).strip().lower()
        if axis not in {"x", "y", "z"}:
            return
        focus_ratio = float(np.clip(params.get("focus_ratio", 1.0), 0.1, 1.0))
        if not comp_ids:
            comp_ids = [str(comp.id) for comp in state.components]
        comp_ids = self._select_component_subset(
            state=state,
            candidate_ids=comp_ids,
            focus_ratio=focus_ratio,
            axes=[axis],
        )
        delta = float(params.get("delta_mm", 0.0))
        for comp_id in comp_ids:
            idx = self._component_index.get(comp_id)
            if idx is None or idx >= len(state.components):
                continue
            comp = state.components[idx]
            setattr(comp.position, axis, float(getattr(comp.position, axis) + delta))
            self._clip_component_to_envelope(state, comp)

    def _apply_cg_recenter(self, state: DesignState, params: Dict[str, Any]) -> None:
        strength = max(0.0, min(float(params.get("strength", 0.5)), 1.0))
        axes = [
            str(axis).strip().lower()
            for axis in list(params.get("axes", []))
            if str(axis).strip().lower() in {"x", "y", "z"}
        ]
        if not axes:
            axes = ["x", "y"]

        target_ids = [
            str(v) for v in list(params.get("component_ids", [])) if str(v)
        ]
        target = self._center_target(state)
        focus_ratio = float(np.clip(params.get("focus_ratio", 0.6), 0.1, 1.0))
        if not target_ids:
            target_ids = [str(comp.id) for comp in state.components]
        target_ids = self._select_component_subset(
            state=state,
            candidate_ids=target_ids,
            focus_ratio=focus_ratio,
            axes=axes,
            target=target,
        )
        target_set = set(target_ids)
        if not target_set:
            return

        selected_idx: List[int] = []
        masses: List[float] = []
        centers: List[np.ndarray] = []
        lower_shift: List[np.ndarray] = []
        upper_shift: List[np.ndarray] = []
        env_min, env_max = self._envelope_bounds_for_state(state)
        axis_idx = {"x": 0, "y": 1, "z": 2}
        axis_set = set(axes)

        for idx, comp in enumerate(state.components):
            if str(comp.id) not in target_set:
                continue
            pos = np.asarray(
                [float(comp.position.x), float(comp.position.y), float(comp.position.z)],
                dtype=float,
            )
            half = np.asarray(
                [
                    float(comp.dimensions.x) / 2.0,
                    float(comp.dimensions.y) / 2.0,
                    float(comp.dimensions.z) / 2.0,
                ],
                dtype=float,
            )
            selected_idx.append(idx)
            masses.append(max(float(comp.mass), 1e-6))
            centers.append(pos)
            lower_shift.append((env_min + half) - pos)
            upper_shift.append((env_max - half) - pos)

        if not selected_idx:
            return

        centers_arr = np.asarray(centers, dtype=float)
        masses_arr = np.asarray(masses, dtype=float)
        total_mass = float(np.sum(masses_arr))
        if total_mass <= 1e-9:
            return
        group_com = np.sum(centers_arr * masses_arr.reshape(-1, 1), axis=0) / total_mass
        desired = (target - group_com) * float(strength)
        for axis in ("x", "y", "z"):
            if axis not in axis_set:
                desired[axis_idx[axis]] = 0.0

        lower_arr = np.asarray(lower_shift, dtype=float)
        upper_arr = np.asarray(upper_shift, dtype=float)
        feasible_low = np.max(lower_arr, axis=0)
        feasible_high = np.min(upper_arr, axis=0)
        shift = np.minimum(np.maximum(desired, feasible_low), feasible_high)

        if float(np.linalg.norm(shift)) <= 1e-9:
            return

        for idx in selected_idx:
            comp = state.components[idx]
            comp.position.x = float(comp.position.x + shift[0])
            comp.position.y = float(comp.position.y + shift[1])
            comp.position.z = float(comp.position.z + shift[2])

    def _apply_hot_spread(self, state: DesignState, params: Dict[str, Any]) -> None:
        axis = str(params.get("axis", "y")).strip().lower()
        if axis not in {"x", "y", "z"}:
            return

        comp_ids = [str(v) for v in list(params.get("component_ids", [])) if str(v)]
        unique_ids: List[str] = []
        seen: set[str] = set()
        for comp_id in comp_ids:
            if comp_id in seen or comp_id not in self._component_index:
                continue
            seen.add(comp_id)
            unique_ids.append(comp_id)
        if len(unique_ids) < 2:
            return

        spread_strength = max(0.0, min(float(params.get("spread_strength", 0.6)), 1.0))
        min_pair_distance = max(1.0, float(params.get("min_pair_distance_mm", 8.0)))
        step = min_pair_distance * (0.5 + 0.5 * spread_strength)
        axis_idx = {"x": 0, "y": 1, "z": 2}[axis]

        current_values = []
        for comp_id in unique_ids:
            idx = self._component_index[comp_id]
            current_values.append(float(getattr(state.components[idx].position, axis)))
        center = float(np.mean(np.asarray(current_values, dtype=float)))
        start = center - 0.5 * float(len(unique_ids) - 1) * step

        ordered = sorted(
            unique_ids,
            key=lambda comp_id: float(
                getattr(state.components[self._component_index[comp_id]].position, axis)
            ),
        )
        for rank, comp_id in enumerate(ordered):
            idx = self._component_index[comp_id]
            comp = state.components[idx]
            target = start + float(rank) * step
            setattr(comp.position, axis, float(target))
            self._clip_component_to_envelope(state, comp)

    def _apply_swap(self, state: DesignState, params: Dict[str, Any]) -> None:
        comp_a = str(params.get("component_a", "")).strip()
        comp_b = str(params.get("component_b", "")).strip()
        if not comp_a or not comp_b or comp_a == comp_b:
            return
        idx_a = self._component_index.get(comp_a)
        idx_b = self._component_index.get(comp_b)
        if idx_a is None or idx_b is None:
            return
        if idx_a >= len(state.components) or idx_b >= len(state.components):
            return

        pos_a = state.components[idx_a].position.model_copy(deep=True)
        pos_b = state.components[idx_b].position.model_copy(deep=True)
        state.components[idx_a].position = pos_b
        state.components[idx_b].position = pos_a
        self._clip_component_to_envelope(state, state.components[idx_a])
        self._clip_component_to_envelope(state, state.components[idx_b])

    @staticmethod
    def _merge_thermal_contact_value(
        *,
        existing: float,
        requested: float,
        update_mode: str,
    ) -> float:
        mode = str(update_mode or "max").strip().lower()
        base = max(float(existing), 0.0)
        value = float(requested)
        if mode == "set":
            merged = value
        elif mode == "delta":
            merged = base + value
        elif mode == "raise":
            merged = base + abs(value)
        else:
            merged = max(base, value)
        return max(float(merged), 0.0)

    def _apply_add_heatstrap(self, state: DesignState, params: Dict[str, Any]) -> None:
        component_ids = [
            str(v).strip()
            for v in list(params.get("component_ids", []))
            if str(v).strip() in self._component_index
        ]
        if len(component_ids) < 2:
            return
        conductance = float(params.get("conductance", 120.0))
        update_mode = str(params.get("update_mode", "max")).strip().lower()
        for idx_a in range(len(component_ids)):
            comp_a_id = component_ids[idx_a]
            comp_a = state.components[self._component_index[comp_a_id]]
            contacts = dict(getattr(comp_a, "thermal_contacts", {}) or {})
            for idx_b in range(idx_a + 1, len(component_ids)):
                comp_b_id = component_ids[idx_b]
                contacts[comp_b_id] = self._merge_thermal_contact_value(
                    existing=float(contacts.get(comp_b_id, 0.0) or 0.0),
                    requested=conductance,
                    update_mode=update_mode,
                )
                comp_b = state.components[self._component_index[comp_b_id]]
                contacts_b = dict(getattr(comp_b, "thermal_contacts", {}) or {})
                contacts_b[comp_a_id] = self._merge_thermal_contact_value(
                    existing=float(contacts_b.get(comp_a_id, 0.0) or 0.0),
                    requested=conductance,
                    update_mode=update_mode,
                )
                comp_b.thermal_contacts = contacts_b
            comp_a.thermal_contacts = contacts

    def _apply_set_thermal_contact(self, state: DesignState, params: Dict[str, Any]) -> None:
        source_component = str(params.get("source_component", "")).strip()
        target_component_ids = [
            str(v).strip()
            for v in list(params.get("target_component_ids", []))
            if str(v).strip() in self._component_index
        ]
        if source_component not in self._component_index or not target_component_ids:
            return
        conductance = float(params.get("conductance", 80.0))
        update_mode = str(params.get("update_mode", "max")).strip().lower()
        bidirectional = bool(params.get("bidirectional", False))
        source = state.components[self._component_index[source_component]]
        contacts = dict(getattr(source, "thermal_contacts", {}) or {})
        for target in target_component_ids:
            if target == source_component:
                continue
            contacts[target] = self._merge_thermal_contact_value(
                existing=float(contacts.get(target, 0.0) or 0.0),
                requested=conductance,
                update_mode=update_mode,
            )
            if bidirectional:
                target_comp = state.components[self._component_index[target]]
                target_contacts = dict(getattr(target_comp, "thermal_contacts", {}) or {})
                target_contacts[source_component] = self._merge_thermal_contact_value(
                    existing=float(target_contacts.get(source_component, 0.0) or 0.0),
                    requested=conductance,
                    update_mode=update_mode,
                )
                target_comp.thermal_contacts = target_contacts
        source.thermal_contacts = contacts

    def _apply_add_bracket(self, state: DesignState, params: Dict[str, Any]) -> None:
        component_ids = [
            str(v).strip()
            for v in list(params.get("component_ids", []))
            if str(v).strip() in self._component_index
        ]
        if not component_ids:
            return
        stiffness_gain = float(np.clip(params.get("stiffness_gain", 0.35), 0.0, 1.0))
        for comp_id in component_ids:
            comp = state.components[self._component_index[comp_id]]
            bracket = dict(getattr(comp, "bracket", {}) or {})
            bracket["type"] = "op_bracket"
            bracket["stiffness_gain"] = max(float(bracket.get("stiffness_gain", 0.0)), stiffness_gain)
            bracket.setdefault("source", "operator_program")
            comp.bracket = bracket

        self._apply_cg_recenter(
            state,
            {
                "component_ids": component_ids,
                "axes": list(params.get("axes", ["x", "y"])),
                "strength": min(0.8, 0.25 + stiffness_gain * 0.45),
                "focus_ratio": float(np.clip(params.get("focus_ratio", 0.70), 0.1, 1.0)),
            },
        )

    def _apply_stiffener_insert(self, state: DesignState, params: Dict[str, Any]) -> None:
        component_ids = [
            str(v).strip()
            for v in list(params.get("component_ids", []))
            if str(v).strip() in self._component_index
        ]
        if not component_ids:
            return
        stiffness_gain = float(np.clip(params.get("stiffness_gain", 0.5), 0.0, 1.0))
        for comp_id in component_ids:
            comp = state.components[self._component_index[comp_id]]
            bracket = dict(getattr(comp, "bracket", {}) or {})
            bracket["type"] = "op_stiffener"
            bracket["stiffness_gain"] = max(float(bracket.get("stiffness_gain", 0.0)), stiffness_gain)
            bracket.setdefault("source", "operator_program")
            comp.bracket = bracket

        self._apply_cg_recenter(
            state,
            {
                "component_ids": component_ids,
                "axes": list(params.get("axes", ["x", "y", "z"])),
                "strength": min(0.95, 0.35 + stiffness_gain * 0.50),
                "focus_ratio": float(np.clip(params.get("focus_ratio", 0.62), 0.1, 1.0)),
            },
        )

    def _apply_bus_proximity_opt(self, state: DesignState, params: Dict[str, Any]) -> None:
        source_component = str(params.get("source_component", "")).strip()
        if source_component not in self._component_index:
            source_component = self._select_bus_source_component(state)
        if not source_component:
            return

        target_component_ids = [
            str(v).strip()
            for v in list(params.get("target_component_ids", []))
            if str(v).strip() in self._component_index and str(v).strip() != source_component
        ]
        if not target_component_ids:
            target_component_ids = [
                str(comp.id)
                for comp in list(state.components)
                if str(comp.id) != source_component
            ]
        if not target_component_ids:
            return

        axes = [
            str(axis).strip().lower()
            for axis in list(params.get("axes", ["x", "y"]))
            if str(axis).strip().lower() in {"x", "y", "z"}
        ]
        if not axes:
            axes = ["x", "y"]
        focus_ratio = float(np.clip(params.get("focus_ratio", 0.65), 0.1, 1.0))
        shift_ratio = max(0.05, min(0.6, 0.2 + 0.4 * focus_ratio))

        src = state.components[self._component_index[source_component]]
        src_pos = np.asarray([float(src.position.x), float(src.position.y), float(src.position.z)], dtype=float)
        axis_idx = {"x": 0, "y": 1, "z": 2}

        for target_id in target_component_ids:
            idx = self._component_index.get(target_id)
            if idx is None or idx >= len(state.components):
                continue
            comp = state.components[idx]
            pos = np.asarray([float(comp.position.x), float(comp.position.y), float(comp.position.z)], dtype=float)
            for axis in axes:
                aid = axis_idx[axis]
                pos[aid] = pos[aid] + (src_pos[aid] - pos[aid]) * shift_ratio
            comp.position.x = float(pos[0])
            comp.position.y = float(pos[1])
            comp.position.z = float(pos[2])
            self._clip_component_to_envelope(state, comp)

    def _apply_fov_keepout_push(self, state: DesignState, params: Dict[str, Any]) -> None:
        component_ids = [
            str(v).strip()
            for v in list(params.get("component_ids", []))
            if str(v).strip() in self._component_index
        ]
        if not component_ids:
            return
        axis = str(params.get("axis", "z")).strip().lower()
        if axis not in {"x", "y", "z"}:
            return

        keepout_center = float(params.get("keepout_center_mm", 0.0))
        min_sep = max(0.0, float(params.get("min_separation_mm", 12.0)))
        preferred_side = str(params.get("preferred_side", "auto")).strip().lower()
        axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
        env_min, env_max = self._envelope_bounds_for_state(state)

        for comp_id in component_ids:
            idx = self._component_index.get(comp_id)
            if idx is None or idx >= len(state.components):
                continue
            comp = state.components[idx]
            pos = np.asarray([float(comp.position.x), float(comp.position.y), float(comp.position.z)], dtype=float)
            current = float(pos[axis_idx])
            half_axis = 0.5 * float(
                [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z][axis_idx]
            )
            lower = float(env_min[axis_idx] + half_axis)
            upper = float(env_max[axis_idx] - half_axis)
            if lower > upper:
                continue

            required_center_offset = float(min_sep + max(half_axis, 0.0))
            positive_target = float(keepout_center + required_center_offset)
            negative_target = float(keepout_center - required_center_offset)
            positive_feasible = bool(positive_target <= upper + 1e-9)
            negative_feasible = bool(negative_target >= lower - 1e-9)

            if preferred_side == "positive" and positive_feasible:
                target = positive_target
            elif preferred_side == "negative" and negative_feasible:
                target = negative_target
            elif positive_feasible and negative_feasible:
                target = positive_target if current >= keepout_center else negative_target
            elif positive_feasible:
                target = positive_target
            elif negative_feasible:
                target = negative_target
            else:
                upper_sep = abs(float(upper) - float(keepout_center)) - float(half_axis)
                lower_sep = abs(float(lower) - float(keepout_center)) - float(half_axis)
                if preferred_side == "positive":
                    target = upper
                elif preferred_side == "negative":
                    target = lower
                else:
                    target = upper if upper_sep >= lower_sep else lower

            pos[axis_idx] = float(np.clip(target, lower, upper))
            comp.position.x = float(pos[0])
            comp.position.y = float(pos[1])
            comp.position.z = float(pos[2])
            self._clip_component_to_envelope(state, comp)

    def _layout_risk_score(self, state: DesignState) -> float:
        centers, half_sizes = self.geometry_arrays_from_state(state)
        env_min, env_max = self._envelope_bounds_for_state(state)
        geom_metrics = compute_geometry_violation_metrics(
            centers=centers,
            half_sizes=half_sizes,
            min_clearance_mm=float(self.min_clearance_mm),
        )
        boundary = compute_boundary_violation(
            centers=centers,
            half_sizes=half_sizes,
            envelope_min=env_min,
            envelope_max=env_max,
        )
        cg_offset = float(calculate_cg_offset(state))
        cg_violation = max(cg_offset - float(self.max_cg_offset_mm), 0.0)

        return float(
            220.0 * float(geom_metrics.get("collision_violation", 0.0)) +
            18.0 * float(geom_metrics.get("clearance_violation", 0.0)) +
            120.0 * float(boundary) +
            8.0 * float(cg_violation)
        )

    def _restore_state_positions(self, *, state: DesignState, backup: DesignState) -> None:
        count = min(len(state.components), len(backup.components))
        for idx in range(count):
            state.components[idx].position = backup.components[idx].position.model_copy(deep=True)

    def _build_hot_component_candidates(self) -> List[str]:
        scored: List[tuple[float, str]] = []
        for comp in list(self.base_state.components):
            category = str(getattr(comp, "category", "") or "").lower()
            power = float(getattr(comp, "power", 0.0) or 0.0)
            bonus = 0.0
            if "power" in category or "battery" in category:
                bonus += 10.0
            if "payload" in category or "tx" in category or "trans" in category:
                bonus += 4.0
            score = power + bonus
            scored.append((score, str(comp.id)))
        scored = sorted(scored, key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored]

    def _select_bus_source_component(self, state: DesignState) -> str:
        preferred_tokens = ("battery", "power", "eps", "pdu", "bus")
        for comp in list(state.components):
            comp_id = str(getattr(comp, "id", "") or "")
            category = str(getattr(comp, "category", "") or "").lower()
            lowered = comp_id.lower()
            if any(token in lowered for token in preferred_tokens):
                return comp_id
            if any(token in category for token in preferred_tokens):
                return comp_id
        if state.components:
            return str(state.components[0].id)
        return ""

    def _select_hot_pair(self, *, default_pair: List[str]) -> List[str]:
        hot = [comp_id for comp_id in self._hot_component_ids if comp_id in self._component_index]
        if len(hot) >= 2:
            return [hot[0], hot[1]]
        if len(default_pair) >= 2:
            return [default_pair[0], default_pair[1]]
        if len(default_pair) == 1:
            alt = next((cid for cid in self.component_ids if cid != default_pair[0]), default_pair[0])
            return [default_pair[0], alt]
        if len(self.component_ids) >= 2:
            return [self.component_ids[0], self.component_ids[1]]
        if self.component_ids:
            return [self.component_ids[0], self.component_ids[0]]
        return []

    def _select_component_subset(
        self,
        *,
        state: DesignState,
        candidate_ids: List[str],
        focus_ratio: float,
        axes: Optional[List[str]] = None,
        target: Optional[np.ndarray] = None,
    ) -> List[str]:
        uniq_ids: List[str] = []
        seen: set[str] = set()
        for comp_id in candidate_ids:
            normalized = str(comp_id).strip()
            if not normalized or normalized in seen:
                continue
            idx = self._component_index.get(normalized)
            if idx is None or idx >= len(state.components):
                continue
            seen.add(normalized)
            uniq_ids.append(normalized)
        if not uniq_ids:
            return []

        safe_focus = float(np.clip(focus_ratio, 0.1, 1.0))
        keep = max(1, int(np.ceil(len(uniq_ids) * safe_focus)))
        if keep >= len(uniq_ids):
            return uniq_ids

        normalized_axes = [
            str(axis).strip().lower()
            for axis in list(axes or ["x", "y"])
            if str(axis).strip().lower() in {"x", "y", "z"}
        ]
        if not normalized_axes:
            normalized_axes = ["x", "y"]
        axis_idx = {"x": 0, "y": 1, "z": 2}
        center_target = np.asarray(
            target if target is not None else self._center_target(state),
            dtype=float,
        )

        scored: List[Tuple[float, str]] = []
        for comp_id in uniq_ids:
            idx = self._component_index.get(comp_id)
            if idx is None or idx >= len(state.components):
                continue
            comp = state.components[idx]
            mass = max(float(comp.mass), 1e-6)
            pos = np.asarray(
                [float(comp.position.x), float(comp.position.y), float(comp.position.z)],
                dtype=float,
            )
            distance = 0.0
            for axis in normalized_axes:
                aidx = axis_idx[axis]
                distance += abs(float(pos[aidx] - center_target[aidx]))
            scored.append((mass * max(distance, 1e-9), comp_id))

        if not scored:
            return uniq_ids[:keep]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [comp_id for _, comp_id in scored[:keep]]

    def _center_target(self, state: DesignState) -> np.ndarray:
        env = state.envelope
        if str(env.origin).strip().lower() == "center":
            return np.zeros(3, dtype=float)
        return np.asarray(
            [
                float(env.outer_size.x) * 0.5,
                float(env.outer_size.y) * 0.5,
                float(env.outer_size.z) * 0.5,
            ],
            dtype=float,
        )

    def _clip_component_to_envelope(self, state: DesignState, comp: Any) -> None:
        env_min, env_max = self._envelope_bounds_for_state(state)
        half = np.asarray(
            [
                float(comp.dimensions.x) / 2.0,
                float(comp.dimensions.y) / 2.0,
                float(comp.dimensions.z) / 2.0,
            ],
            dtype=float,
        )
        lower = env_min + half
        upper = env_max - half
        if np.any(lower > upper):
            return

        pos = np.asarray(
            [float(comp.position.x), float(comp.position.y), float(comp.position.z)],
            dtype=float,
        )
        clipped = np.minimum(np.maximum(pos, lower), upper)
        comp.position.x = float(clipped[0])
        comp.position.y = float(clipped[1])
        comp.position.z = float(clipped[2])

    @staticmethod
    def _envelope_bounds_for_state(state: DesignState) -> Tuple[np.ndarray, np.ndarray]:
        env = state.envelope
        size = np.asarray(
            [env.outer_size.x, env.outer_size.y, env.outer_size.z],
            dtype=float,
        )
        if str(env.origin).strip().lower() == "center":
            return (-0.5 * size, 0.5 * size)
        return (np.zeros(3, dtype=float), size)
