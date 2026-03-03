"""
Executable operator action library for OP-MaaS R1.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .operator_program import OperatorAction, OperatorProgram, validate_operator_program
from .protocol import ModelingIntent, ModelingVariable


def build_operator_program_from_context(
    intent: ModelingIntent,
    *,
    depth: int,
    evaluation_payload: Optional[Dict[str, Any]] = None,
    max_components: int = 6,
) -> OperatorProgram:
    """
    Build one lightweight operator program from branch diagnostics.
    """
    component_ids = _collect_component_ids(intent)
    selected_ids = component_ids[: max(1, int(max_components))]
    payload = dict(evaluation_payload or {})
    dominant_violation = str(payload.get("dominant_violation") or "").lower()

    actions: List[OperatorAction] = []
    if _contains_any(dominant_violation, ("cg", "centroid", "com_offset")):
        actions.append(
            OperatorAction(
                action="cg_recenter",
                params={
                    "axes": ["x", "y"],
                    "strength": 0.75,
                    "focus_ratio": 0.55,
                },
                note="Focus search around CG center region",
            )
        )
        if selected_ids:
            actions.append(
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected_ids,
                        "axis": "x",
                        "delta_mm": 0.0,
                        "focus_ratio": 0.6,
                    },
                    note="Keep key components near center while preserving diversity",
                )
            )
    elif _contains_any(dominant_violation, ("temp", "thermal", "hotspot")):
        hot_ids = _select_hot_component_ids(component_ids)
        if len(hot_ids) >= 2:
            actions.append(
                OperatorAction(
                    action="hot_spread",
                    params={
                        "component_ids": hot_ids[: max(2, min(len(hot_ids), int(max_components)))],
                        "axis": "y",
                        "min_pair_distance_mm": 12.0,
                        "spread_strength": 0.7,
                        "focus_ratio": 0.55,
                    },
                    note="Spread thermal-critical components to reduce hotspot coupling",
                )
            )
        elif selected_ids:
            actions.append(
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected_ids,
                        "axis": "y",
                        "delta_mm": 8.0,
                        "focus_ratio": 0.55,
                    },
                    note="Fallback thermal move toward cooling side band",
                )
            )
    elif _contains_any(dominant_violation, ("collision", "clearance", "boundary")):
        if len(selected_ids) >= 2:
            actions.append(
                OperatorAction(
                    action="swap",
                    params={
                        "component_a": selected_ids[0],
                        "component_b": selected_ids[-1],
                    },
                    note="Swap two components to escape collision local minima",
                )
            )
        if selected_ids:
            actions.append(
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": selected_ids,
                        "axis": "x",
                        "delta_mm": 10.0,
                        "focus_ratio": 0.55,
                    },
                    note="Increase separation search span in one axis",
                )
            )
    else:
        if len(selected_ids) >= 2:
            actions.append(
                OperatorAction(
                    action="swap",
                    params={
                        "component_a": selected_ids[0],
                        "component_b": selected_ids[-1],
                    },
                    note="Default diversification branch",
                )
            )
        actions.append(
            OperatorAction(
                action="cg_recenter",
                params={
                    "axes": ["x", "y"],
                    "strength": 0.35,
                    "focus_ratio": 0.7,
                },
                note="Mild centering prior to improve feasibility chance",
            )
        )

    if not actions:
        actions.append(
            OperatorAction(
                action="cg_recenter",
                params={"axes": ["x", "y"], "strength": 0.3, "focus_ratio": 0.75},
                note="Fallback action",
            )
        )

    return OperatorProgram(
        program_id=f"op_prog_d{int(depth)}_{_slug(dominant_violation or 'default')}",
        rationale=f"diagnostic-driven branch: {dominant_violation or 'no_dominant_violation'}",
        actions=actions,
        metadata={
            "depth": int(depth),
            "dominant_violation": dominant_violation,
        },
    )


def apply_operator_program_to_intent(
    intent: ModelingIntent,
    program: OperatorProgram,
) -> tuple[ModelingIntent, Dict[str, Any]]:
    """
    Execute operator actions against ModelingIntent variable bounds/objective priors.
    """
    component_ids = _collect_component_ids(intent)
    validation = validate_operator_program(program, component_ids=component_ids)
    if not validation.get("is_valid", False):
        return intent, {
            "program_id": str(program.program_id),
            "is_valid": False,
            "applied_actions": 0,
            "errors": list(validation.get("errors", []) or []),
            "warnings": list(validation.get("warnings", []) or []),
            "events": [],
        }

    normalized_program = validation["program"]
    next_intent = intent.model_copy(deep=True)
    events: List[Dict[str, Any]] = []
    applied = 0
    for idx, action in enumerate(normalized_program.actions, start=1):
        handler = _ACTION_HANDLERS.get(action.action)
        if handler is None:
            events.append(
                {
                    "index": int(idx),
                    "action": action.action,
                    "applied": False,
                    "reason": "unsupported_action",
                }
            )
            continue

        changed, payload = handler(next_intent, dict(action.params or {}))
        events.append(
            {
                "index": int(idx),
                "action": action.action,
                "applied": bool(changed),
                "detail": payload,
            }
        )
        if changed:
            applied += 1

    if applied > 0:
        next_intent.assumptions.append(
            f"op_program:{normalized_program.program_id}:applied={applied}/{len(normalized_program.actions)}"
        )
        action_names = ",".join(evt["action"] for evt in events if evt.get("applied"))
        next_intent.assumptions.append(f"op_actions:{action_names}")

    return next_intent, {
        "program_id": str(normalized_program.program_id),
        "is_valid": True,
        "applied_actions": int(applied),
        "errors": [],
        "warnings": list(validation.get("warnings", []) or []),
        "events": events,
        "program": normalized_program.model_dump(),
    }


def _apply_group_move(intent: ModelingIntent, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    component_ids = {
        str(item).strip()
        for item in list(params.get("component_ids", []))
        if str(item).strip()
    }
    axis = str(params.get("axis") or "x").strip().lower()
    delta_mm = float(params.get("delta_mm", 0.0))
    focus_ratio = float(params.get("focus_ratio", 0.6))

    changed = 0
    for var in intent.variables:
        if str(var.component_id or "").strip() not in component_ids:
            continue
        if _infer_axis(var) != axis:
            continue
        if _shift_and_focus_bounds(var, delta_mm=delta_mm, focus_ratio=focus_ratio):
            changed += 1
    return (
        changed > 0,
        {"axis": axis, "delta_mm": delta_mm, "touched_variables": int(changed)},
    )


def _apply_cg_recenter(intent: ModelingIntent, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    target_components = {
        str(item).strip()
        for item in list(params.get("component_ids", []))
        if str(item).strip()
    }
    axes = {
        str(axis).strip().lower()
        for axis in list(params.get("axes", []))
        if str(axis).strip()
    }
    if not axes:
        axes = {"x", "y"}
    strength = float(params.get("strength", 0.5))
    focus_ratio = float(params.get("focus_ratio", max(0.35, 1.0 - 0.4 * strength)))

    changed = 0
    for var in intent.variables:
        if target_components and str(var.component_id or "").strip() not in target_components:
            continue
        axis = _infer_axis(var)
        if axis not in axes:
            continue
        if _focus_bounds_toward_zero(var, strength=strength, focus_ratio=focus_ratio):
            changed += 1
    return (
        changed > 0,
        {
            "axes": sorted(axes),
            "strength": float(strength),
            "touched_variables": int(changed),
        },
    )


def _apply_hot_spread(intent: ModelingIntent, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    component_ids = [
        str(item).strip()
        for item in list(params.get("component_ids", []))
        if str(item).strip()
    ]
    axis = str(params.get("axis") or "y").strip().lower()
    min_pair_distance_mm = float(params.get("min_pair_distance_mm", 10.0))
    spread_strength = float(params.get("spread_strength", 0.6))
    focus_ratio = float(params.get("focus_ratio", max(0.35, 0.8 - 0.3 * spread_strength)))

    slot_map: List[Tuple[str, ModelingVariable]] = []
    for comp_id in component_ids:
        var = _find_variable(intent, component_id=comp_id, axis=axis)
        if var is not None:
            slot_map.append((comp_id, var))
    if len(slot_map) < 2:
        return False, {"axis": axis, "reason": "insufficient_components"}

    changed = 0
    step = max(0.5, min_pair_distance_mm) * (0.6 + 0.4 * max(0.0, min(1.0, spread_strength)))
    base = 0.5 * float(len(slot_map) - 1) * step
    for rank, (_, var) in enumerate(slot_map):
        target_center = -base + float(rank) * step
        if _focus_bounds_to_target(var, target_center=target_center, focus_ratio=focus_ratio):
            changed += 1
    return (
        changed > 0,
        {
            "axis": axis,
            "step_mm": float(step),
            "touched_variables": int(changed),
        },
    )


def _apply_swap(intent: ModelingIntent, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    component_a = str(params.get("component_a") or "").strip()
    component_b = str(params.get("component_b") or "").strip()
    if not component_a or not component_b:
        return False, {"reason": "missing_component_pair"}
    if component_a == component_b:
        return False, {"reason": "identical_components"}

    swapped_axes: List[str] = []
    for axis in ("x", "y", "z"):
        var_a = _find_variable(intent, component_id=component_a, axis=axis)
        var_b = _find_variable(intent, component_id=component_b, axis=axis)
        if var_a is None or var_b is None:
            continue
        if not _has_bounds(var_a) or not _has_bounds(var_b):
            continue

        a_lb, a_ub = float(var_a.lower_bound), float(var_a.upper_bound)
        b_lb, b_ub = float(var_b.lower_bound), float(var_b.upper_bound)
        if (
            abs(a_lb - b_lb) < 1e-9 and
            abs(a_ub - b_ub) < 1e-9
        ):
            continue
        var_a.lower_bound, var_a.upper_bound = b_lb, b_ub
        var_b.lower_bound, var_b.upper_bound = a_lb, a_ub
        swapped_axes.append(axis)

    return (
        len(swapped_axes) > 0,
        {
            "component_a": component_a,
            "component_b": component_b,
            "swapped_axes": swapped_axes,
        },
    )


_ACTION_HANDLERS = {
    "group_move": _apply_group_move,
    "cg_recenter": _apply_cg_recenter,
    "hot_spread": _apply_hot_spread,
    "swap": _apply_swap,
}


def _collect_component_ids(intent: ModelingIntent) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for var in intent.variables:
        comp_id = str(var.component_id or "").strip()
        if not comp_id or comp_id in seen:
            continue
        seen.add(comp_id)
        result.append(comp_id)
    return result


def _find_variable(
    intent: ModelingIntent,
    *,
    component_id: str,
    axis: str,
) -> Optional[ModelingVariable]:
    for var in intent.variables:
        if str(var.component_id or "").strip() != component_id:
            continue
        if _infer_axis(var) == axis:
            return var
    return None


def _has_bounds(var: ModelingVariable) -> bool:
    return var.lower_bound is not None and var.upper_bound is not None


def _shift_and_focus_bounds(
    var: ModelingVariable,
    *,
    delta_mm: float,
    focus_ratio: float,
) -> bool:
    if not _has_bounds(var):
        return False
    lb = float(var.lower_bound)
    ub = float(var.upper_bound)
    if ub <= lb:
        return False
    center = 0.5 * (lb + ub) + float(delta_mm)
    return _focus_bounds_to_target(var, target_center=center, focus_ratio=focus_ratio)


def _focus_bounds_toward_zero(
    var: ModelingVariable,
    *,
    strength: float,
    focus_ratio: float,
) -> bool:
    if not _has_bounds(var):
        return False
    lb = float(var.lower_bound)
    ub = float(var.upper_bound)
    if ub <= lb:
        return False
    center = 0.5 * (lb + ub)
    target = center * (1.0 - max(0.0, min(1.0, float(strength))))
    return _focus_bounds_to_target(var, target_center=target, focus_ratio=focus_ratio)


def _focus_bounds_to_target(
    var: ModelingVariable,
    *,
    target_center: float,
    focus_ratio: float,
) -> bool:
    if not _has_bounds(var):
        return False

    lb = float(var.lower_bound)
    ub = float(var.upper_bound)
    span = ub - lb
    if span <= 1e-9:
        return False

    safe_focus = max(0.05, min(1.0, float(focus_ratio)))
    target = min(max(float(target_center), lb), ub)
    half_span = max(1e-6, 0.5 * span * safe_focus)
    new_lb = max(lb, target - half_span)
    new_ub = min(ub, target + half_span)
    if new_ub - new_lb <= 1e-9:
        return False

    if abs(new_lb - lb) <= 1e-9 and abs(new_ub - ub) <= 1e-9:
        return False

    var.lower_bound = float(new_lb)
    var.upper_bound = float(new_ub)
    return True


def _infer_axis(var: ModelingVariable) -> Optional[str]:
    raw = str(var.name or "").strip().lower()
    if raw:
        matched = re.search(r"(?:_|-|\.)(x|y|z)$", raw)
        if matched:
            return matched.group(1)
        if raw[-1:] in {"x", "y", "z"} and len(raw) >= 2:
            return raw[-1]

    desc = str(var.description or "").strip().lower()
    if desc:
        for axis in ("x", "y", "z"):
            if f"{axis}-position" in desc or f"{axis} position" in desc:
                return axis
    return None


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in keywords)


def _select_hot_component_ids(component_ids: Sequence[str]) -> List[str]:
    preferred_keywords = ("battery", "power", "payload", "transceiver", "cpu", "amp")
    preferred: List[str] = []
    fallback: List[str] = []
    for comp_id in component_ids:
        lowered = str(comp_id).lower()
        if any(token in lowered for token in preferred_keywords):
            preferred.append(comp_id)
        else:
            fallback.append(comp_id)
    if preferred:
        return preferred + fallback
    return list(component_ids)


def _slug(text: str) -> str:
    if not text:
        return "default"
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return slug or "default"
