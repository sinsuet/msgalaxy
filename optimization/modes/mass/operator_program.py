"""
Operator program schema and DSL validator for OP-MaaS DSL v3.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError, model_validator


SUPPORTED_ACTIONS = frozenset(
    {
        # Geometry
        "group_move",
        "cg_recenter",
        "hot_spread",
        "swap",
        # Thermal
        "add_heatstrap",
        "set_thermal_contact",
        # Structural
        "add_bracket",
        "stiffener_insert",
        # Power & mission
        "bus_proximity_opt",
        "fov_keepout_push",
    }
)
SUPPORTED_AXES = frozenset({"x", "y", "z"})
THERMAL_CONTACT_UPDATE_MODES = frozenset({"max", "set", "delta", "raise"})
MAX_DELTA_MM = 80.0
MAX_ACTIONS_DEFAULT = 10
MAX_COMPONENTS_PER_ACTION = 32


class OperatorAction(BaseModel):
    """One executable operator action in a program."""

    action: Literal[
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
    ]
    params: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class OperatorProgram(BaseModel):
    """Operator-program payload attached to one MCTS branch."""

    program_id: str
    version: str = "opmaas-r3"
    rationale: str = ""
    actions: List[OperatorAction] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "OperatorProgram":
        if not str(self.program_id or "").strip():
            raise ValueError("program_id 不能为空")
        if not self.actions:
            raise ValueError("actions 不能为空")
        return self


def validate_operator_program(
    program: OperatorProgram | Mapping[str, Any],
    *,
    component_ids: Optional[Iterable[str]] = None,
    max_actions: int = MAX_ACTIONS_DEFAULT,
) -> Dict[str, Any]:
    """
    Validate operator DSL legality, boundary and safety constraints.
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        parsed = (
            program
            if isinstance(program, OperatorProgram)
            else OperatorProgram.model_validate(program)
        )
    except ValidationError as exc:
        return {
            "is_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "program": None,
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {
            "is_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "program": None,
        }

    component_set = {
        str(item).strip()
        for item in (component_ids or [])
        if str(item).strip()
    }
    if len(parsed.actions) > int(max_actions):
        errors.append(f"actions 数量超限: {len(parsed.actions)} > {int(max_actions)}")

    normalized_actions: List[OperatorAction] = []
    seen_swaps: set[tuple[str, str]] = set()
    for idx, action in enumerate(parsed.actions, start=1):
        action_errors: List[str] = []
        action_warnings: List[str] = []
        normalized = _validate_action_params(
            action=action,
            component_set=component_set,
            errors=action_errors,
            warnings=action_warnings,
        )
        errors.extend(f"action[{idx}] {msg}" for msg in action_errors)
        warnings.extend(f"action[{idx}] {msg}" for msg in action_warnings)
        if normalized is None:
            continue

        if action.action == "swap":
            pair = tuple(sorted((str(normalized["component_a"]), str(normalized["component_b"]))))
            if pair in seen_swaps:
                warnings.append(f"action[{idx}] swap 对重复: {pair[0]} <-> {pair[1]}")
            seen_swaps.add(pair)

        normalized_actions.append(
            OperatorAction(
                action=action.action,
                params=normalized,
                note=str(action.note or ""),
            )
        )

    if errors:
        return {
            "is_valid": False,
            "errors": errors,
            "warnings": warnings,
            "program": None,
        }

    normalized_program = parsed.model_copy(deep=True)
    normalized_program.actions = normalized_actions
    return {
        "is_valid": True,
        "errors": [],
        "warnings": warnings,
        "program": normalized_program,
    }


def _validate_action_params(
    *,
    action: OperatorAction,
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    if action.action not in SUPPORTED_ACTIONS:
        errors.append(f"未知 action: {action.action}")
        return None

    params = dict(action.params or {})
    if action.action == "group_move":
        return _validate_group_move(params, component_set, errors, warnings)
    if action.action == "cg_recenter":
        return _validate_cg_recenter(params, component_set, errors, warnings)
    if action.action == "hot_spread":
        return _validate_hot_spread(params, component_set, errors, warnings)
    if action.action == "swap":
        return _validate_swap(params, component_set, errors, warnings)
    if action.action == "add_heatstrap":
        return _validate_add_heatstrap(params, component_set, errors, warnings)
    if action.action == "set_thermal_contact":
        return _validate_set_thermal_contact(params, component_set, errors, warnings)
    if action.action == "add_bracket":
        return _validate_add_bracket(params, component_set, errors, warnings)
    if action.action == "stiffener_insert":
        return _validate_stiffener_insert(params, component_set, errors, warnings)
    if action.action == "bus_proximity_opt":
        return _validate_bus_proximity_opt(params, component_set, errors, warnings)
    if action.action == "fov_keepout_push":
        return _validate_fov_keepout_push(params, component_set, errors, warnings)
    errors.append(f"action 尚未实现: {action.action}")
    return None


def _validate_group_move(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if not component_ids:
        errors.append("group_move.component_ids 不能为空")
        return None
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"group_move.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    axis = _normalize_axis(params.get("axis"), default="x")
    if axis is None:
        errors.append("group_move.axis 非法，必须是 x/y/z")
        return None

    delta_mm = _safe_float(params.get("delta_mm"), default=0.0)
    if delta_mm is None:
        errors.append("group_move.delta_mm 不是有效数字")
        return None
    if abs(delta_mm) > MAX_DELTA_MM:
        errors.append(f"group_move.delta_mm 超限: |{delta_mm}| > {MAX_DELTA_MM}")

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.6)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("group_move.focus_ratio 必须在 (0,1] 区间内")
        return None
    if abs(delta_mm) < 1e-9 and focus_ratio >= 0.95:
        warnings.append("group_move 可能几乎不改变搜索域")

    return {
        "component_ids": component_ids,
        "axis": axis,
        "delta_mm": float(delta_mm),
        "focus_ratio": float(focus_ratio),
    }


def _validate_cg_recenter(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    strength = _safe_float(params.get("strength"), default=0.5)
    if strength is None or not (0.0 <= strength <= 1.0):
        errors.append("cg_recenter.strength 必须在 [0,1] 区间内")
        return None

    axes = _normalize_axes(params.get("axes"), default=("x", "y"))
    if not axes:
        errors.append("cg_recenter.axes 不能为空，且必须是 x/y/z")
        return None

    component_ids = _normalize_component_ids(params.get("component_ids"))
    if component_ids:
        if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
            errors.append(
                f"cg_recenter.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
            )
        _check_unknown_components(
            component_ids=component_ids,
            component_set=component_set,
            errors=errors,
        )
    else:
        warnings.append("cg_recenter 未指定 component_ids，将作用于全部可解码组件")

    focus_ratio = _safe_float(params.get("focus_ratio"), default=max(0.35, 1.0 - 0.4 * strength))
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("cg_recenter.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "axes": axes,
        "strength": float(strength),
        "focus_ratio": float(focus_ratio),
    }


def _validate_hot_spread(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if not component_ids:
        errors.append("hot_spread.component_ids 不能为空")
        return None
    if len(component_ids) < 2:
        warnings.append("hot_spread.component_ids 少于 2 个，扩散效果有限")
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"hot_spread.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    axis = _normalize_axis(params.get("axis"), default="y")
    if axis is None:
        errors.append("hot_spread.axis 非法，必须是 x/y/z")
        return None

    min_pair_distance_mm = _safe_float(params.get("min_pair_distance_mm"), default=10.0)
    if min_pair_distance_mm is None or min_pair_distance_mm < 0.0:
        errors.append("hot_spread.min_pair_distance_mm 必须 >= 0")
        return None
    if min_pair_distance_mm > MAX_DELTA_MM:
        errors.append(
            f"hot_spread.min_pair_distance_mm 超限: {min_pair_distance_mm} > {MAX_DELTA_MM}"
        )

    spread_strength = _safe_float(params.get("spread_strength"), default=0.6)
    if spread_strength is None or not (0.0 <= spread_strength <= 1.0):
        errors.append("hot_spread.spread_strength 必须在 [0,1] 区间内")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=max(0.35, 0.8 - 0.3 * spread_strength))
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("hot_spread.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "axis": axis,
        "min_pair_distance_mm": float(min_pair_distance_mm),
        "spread_strength": float(spread_strength),
        "focus_ratio": float(focus_ratio),
    }


def _validate_swap(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    component_a = str(params.get("component_a") or "").strip()
    component_b = str(params.get("component_b") or "").strip()
    if not component_a or not component_b:
        errors.append("swap.component_a / swap.component_b 必填")
        return None
    if component_a == component_b:
        errors.append("swap.component_a 与 swap.component_b 不能相同")
        return None

    _check_unknown_components(
        component_ids=[component_a, component_b],
        component_set=component_set,
        errors=errors,
    )
    return {
        "component_a": component_a,
        "component_b": component_b,
    }


def _validate_add_heatstrap(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if len(component_ids) < 2:
        errors.append("add_heatstrap.component_ids 至少需要 2 个组件")
        return None
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"add_heatstrap.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    update_mode = str(params.get("update_mode", "max") or "max").strip().lower()
    if update_mode not in THERMAL_CONTACT_UPDATE_MODES:
        errors.append("add_heatstrap.update_mode 必须是 max/set/delta/raise 之一")
        return None

    conductance = _safe_float(params.get("conductance"), default=120.0)
    if conductance is None:
        errors.append("add_heatstrap.conductance 必须是有效数字")
        return None
    if update_mode == "delta":
        if abs(float(conductance)) <= 1e-9:
            errors.append("add_heatstrap.conductance 在 delta 模式下不能为 0")
            return None
    elif conductance <= 0.0:
        errors.append("add_heatstrap.conductance 必须 > 0")
        return None
    if conductance > 1500.0:
        warnings.append("add_heatstrap.conductance 偏高，可能导致代理过拟合")

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.65)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("add_heatstrap.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "conductance": float(conductance),
        "update_mode": str(update_mode),
        "focus_ratio": float(focus_ratio),
    }


def _validate_set_thermal_contact(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    source_component = str(params.get("source_component") or "").strip()
    target_component_ids = _normalize_component_ids(params.get("target_component_ids"))
    if not source_component:
        errors.append("set_thermal_contact.source_component 必填")
        return None
    if not target_component_ids:
        errors.append("set_thermal_contact.target_component_ids 不能为空")
        return None
    if len(target_component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"set_thermal_contact.target_component_ids 超限: {len(target_component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )

    _check_unknown_components(
        component_ids=[source_component, *target_component_ids],
        component_set=component_set,
        errors=errors,
    )
    if source_component in set(target_component_ids):
        errors.append("set_thermal_contact.source_component 不能与 target 重复")
        return None

    update_mode = str(params.get("update_mode", "max") or "max").strip().lower()
    if update_mode not in THERMAL_CONTACT_UPDATE_MODES:
        errors.append("set_thermal_contact.update_mode 必须是 max/set/delta/raise 之一")
        return None

    conductance = _safe_float(params.get("conductance"), default=80.0)
    if conductance is None:
        errors.append("set_thermal_contact.conductance 必须是有效数字")
        return None
    if update_mode == "delta":
        if abs(float(conductance)) <= 1e-9:
            errors.append("set_thermal_contact.conductance 在 delta 模式下不能为 0")
            return None
    elif conductance <= 0.0:
        errors.append("set_thermal_contact.conductance 必须 > 0")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.7)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("set_thermal_contact.focus_ratio 必须在 (0,1] 区间内")
        return None
    bidirectional = _safe_bool(params.get("bidirectional"), default=False)

    return {
        "source_component": source_component,
        "target_component_ids": target_component_ids,
        "conductance": float(conductance),
        "update_mode": str(update_mode),
        "bidirectional": bool(bidirectional),
        "focus_ratio": float(focus_ratio),
    }


def _validate_add_bracket(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if not component_ids:
        errors.append("add_bracket.component_ids 不能为空")
        return None
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"add_bracket.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    axes = _normalize_axes(params.get("axes"), default=("x", "y"))
    if not axes:
        errors.append("add_bracket.axes 不能为空，且必须是 x/y/z")
        return None

    stiffness_gain = _safe_float(params.get("stiffness_gain"), default=0.35)
    if stiffness_gain is None or not (0.0 <= stiffness_gain <= 1.0):
        errors.append("add_bracket.stiffness_gain 必须在 [0,1] 区间内")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.70)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("add_bracket.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "axes": axes,
        "stiffness_gain": float(stiffness_gain),
        "focus_ratio": float(focus_ratio),
    }


def _validate_stiffener_insert(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if not component_ids:
        errors.append("stiffener_insert.component_ids 不能为空")
        return None
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"stiffener_insert.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    axes = _normalize_axes(params.get("axes"), default=("x", "y", "z"))
    if not axes:
        errors.append("stiffener_insert.axes 不能为空，且必须是 x/y/z")
        return None

    stiffness_gain = _safe_float(params.get("stiffness_gain"), default=0.5)
    if stiffness_gain is None or not (0.0 <= stiffness_gain <= 1.0):
        errors.append("stiffener_insert.stiffness_gain 必须在 [0,1] 区间内")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.62)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("stiffener_insert.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "axes": axes,
        "stiffness_gain": float(stiffness_gain),
        "focus_ratio": float(focus_ratio),
    }


def _validate_bus_proximity_opt(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    source_component = str(params.get("source_component") or "").strip()
    target_component_ids = _normalize_component_ids(params.get("target_component_ids"))

    if source_component:
        _check_unknown_components(
            component_ids=[source_component],
            component_set=component_set,
            errors=errors,
        )
    if target_component_ids:
        _check_unknown_components(
            component_ids=target_component_ids,
            component_set=component_set,
            errors=errors,
        )
    if source_component and source_component in set(target_component_ids):
        errors.append("bus_proximity_opt.source_component 不能包含在 target_component_ids 中")
        return None
    if len(target_component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"bus_proximity_opt.target_component_ids 超限: {len(target_component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )

    axes = _normalize_axes(params.get("axes"), default=("x", "y"))
    if not axes:
        errors.append("bus_proximity_opt.axes 不能为空，且必须是 x/y/z")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.65)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("bus_proximity_opt.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "source_component": source_component,
        "target_component_ids": target_component_ids,
        "axes": axes,
        "focus_ratio": float(focus_ratio),
    }


def _validate_fov_keepout_push(
    params: Dict[str, Any],
    component_set: set[str],
    errors: List[str],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    _ = warnings
    component_ids = _normalize_component_ids(params.get("component_ids"))
    if not component_ids:
        errors.append("fov_keepout_push.component_ids 不能为空")
        return None
    if len(component_ids) > MAX_COMPONENTS_PER_ACTION:
        errors.append(
            f"fov_keepout_push.component_ids 超限: {len(component_ids)} > {MAX_COMPONENTS_PER_ACTION}"
        )
    _check_unknown_components(
        component_ids=component_ids,
        component_set=component_set,
        errors=errors,
    )

    axis = _normalize_axis(params.get("axis"), default="z")
    if axis is None:
        errors.append("fov_keepout_push.axis 非法，必须是 x/y/z")
        return None

    keepout_center_mm = _safe_float(params.get("keepout_center_mm"), default=0.0)
    if keepout_center_mm is None:
        errors.append("fov_keepout_push.keepout_center_mm 不是有效数字")
        return None

    min_separation_mm = _safe_float(params.get("min_separation_mm"), default=12.0)
    if min_separation_mm is None or min_separation_mm < 0.0:
        errors.append("fov_keepout_push.min_separation_mm 必须 >= 0")
        return None
    if min_separation_mm > MAX_DELTA_MM * 1.5:
        errors.append(
            f"fov_keepout_push.min_separation_mm 超限: {min_separation_mm} > {MAX_DELTA_MM * 1.5}"
        )

    preferred_side = str(params.get("preferred_side") or "auto").strip().lower()
    if preferred_side not in {"auto", "positive", "negative"}:
        errors.append("fov_keepout_push.preferred_side 必须是 auto/positive/negative")
        return None

    focus_ratio = _safe_float(params.get("focus_ratio"), default=0.68)
    if focus_ratio is None or not (0.0 < focus_ratio <= 1.0):
        errors.append("fov_keepout_push.focus_ratio 必须在 (0,1] 区间内")
        return None

    return {
        "component_ids": component_ids,
        "axis": axis,
        "keepout_center_mm": float(keepout_center_mm),
        "min_separation_mm": float(min_separation_mm),
        "preferred_side": preferred_side,
        "focus_ratio": float(focus_ratio),
    }


def _normalize_component_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, Sequence):
        raw = [str(item).strip() for item in value]
    else:
        return []
    deduped: List[str] = []
    seen: set[str] = set()
    for item in raw:
        if not item or item in seen:
            continue
        deduped.append(item)
        seen.add(item)
    return deduped


def _normalize_axis(value: Any, *, default: str) -> Optional[str]:
    axis = str(value or default).strip().lower()
    if axis not in SUPPORTED_AXES:
        return None
    return axis


def _normalize_axes(value: Any, *, default: Sequence[str]) -> List[str]:
    if value is None:
        raw = list(default)
    elif isinstance(value, str):
        raw = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, Sequence):
        raw = [str(item).strip().lower() for item in value]
    else:
        raw = []
    axes: List[str] = []
    seen: set[str] = set()
    for axis in raw:
        if axis in SUPPORTED_AXES and axis not in seen:
            axes.append(axis)
            seen.add(axis)
    return axes


def _check_unknown_components(
    *,
    component_ids: Sequence[str],
    component_set: set[str],
    errors: List[str],
) -> None:
    if not component_set:
        return
    for comp_id in component_ids:
        if comp_id not in component_set:
            errors.append(f"未知 component_id: {comp_id}")


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return None


def _safe_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(default)
