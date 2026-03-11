"""
Minimal v4 -> v3 realization bridge for semantic operator programs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .operator_program import OperatorAction, OperatorProgram, validate_operator_program
from .operator_program_v4 import (
    DSL_V4_VERSION,
    OperatorActionV4,
    OperatorProgramV4,
    build_operator_program_v4_payload,
    validate_operator_program_v4,
)


@dataclass(frozen=True)
class ResolvedTargetBinding:
    object_type: str
    object_id: str
    role: str
    component_ids: tuple[str, ...]
    attributes: Dict[str, Any]


def realize_operator_program_v4(
    program: OperatorProgramV4 | Mapping[str, Any],
    *,
    binding_catalog: Optional[Mapping[str, Any]] = None,
    allow_stub: bool = True,
) -> Dict[str, Any]:
    """
    Realize DSL v4 actions into the existing DSL v3 execution layer.

    The bridge prioritizes contract continuity:
    - semantic v4 payload remains preserved,
    - execution continues through the v3 kernel,
    - unsupported orientation/site semantics fall back to bounded stubs.
    """
    validation = validate_operator_program_v4(program)
    if not validation.get("is_valid", False):
        return {
            "is_valid": False,
            "errors": list(validation.get("errors", []) or []),
            "warnings": list(validation.get("warnings", []) or []),
            "program": None,
            "summary": {},
        }

    parsed: OperatorProgramV4 = validation["program"]
    v4_payload = build_operator_program_v4_payload(parsed)
    normalized_catalog = _normalize_binding_catalog(binding_catalog)
    component_ids = sorted(_collect_component_ids_from_catalog(normalized_catalog))
    realized_actions: List[OperatorAction] = []
    action_reports: List[Dict[str, Any]] = []
    warnings: List[str] = list(validation.get("warnings", []) or [])

    for index, action in enumerate(list(parsed.actions or []), start=1):
        action_v3, report = _realize_action_v4(
            action=action,
            binding_catalog=normalized_catalog,
            allow_stub=allow_stub,
        )
        realized_actions.extend(action_v3)
        action_reports.append({"action_index": int(index), **report})
        if list(report.get("warnings", []) or []):
            warnings.extend(
                f"action[{index}] {item}" for item in list(report.get("warnings", []) or [])
            )

    if not realized_actions:
        return {
            "is_valid": False,
            "errors": ["v4 realization produced no executable v3 actions"],
            "warnings": warnings,
            "program": None,
            "summary": {
                "source_version": DSL_V4_VERSION,
                "action_reports": action_reports,
                "realization_status": "empty",
            },
        }

    realized_program = OperatorProgram(
        program_id=f"{parsed.program_id}__r3",
        version="opmaas-r3",
        rationale=str(parsed.rationale or ""),
        actions=realized_actions,
        metadata={
            "source_dsl_version": str(parsed.version or DSL_V4_VERSION),
            "source_program_id": str(parsed.program_id or ""),
            "semantic_program_v4": v4_payload,
            "v4_realization_action_reports": action_reports,
        },
    )
    v3_validation = validate_operator_program(
        realized_program,
        component_ids=component_ids or None,
        max_actions=max(len(realized_actions), 1),
    )
    if not v3_validation.get("is_valid", False):
        return {
            "is_valid": False,
            "errors": list(v3_validation.get("errors", []) or []),
            "warnings": warnings + list(v3_validation.get("warnings", []) or []),
            "program": None,
            "summary": {
                "source_version": DSL_V4_VERSION,
                "action_reports": action_reports,
                "realization_status": "invalid_v3",
            },
        }

    stubbed = [
        str(item.get("source_action", "") or "")
        for item in list(action_reports or [])
        if bool(item.get("stubbed", False))
    ]
    return {
        "is_valid": True,
        "errors": [],
        "warnings": warnings + list(v3_validation.get("warnings", []) or []),
        "program": v3_validation["program"],
        "summary": {
            "source_version": DSL_V4_VERSION,
            "source_program_id": str(parsed.program_id or ""),
            "realized_program_id": str(v3_validation["program"].program_id or ""),
            "action_reports": action_reports,
            "stubbed_actions": stubbed,
            "realization_status": "ok",
        },
    }


def _realize_action_v4(
    *,
    action: OperatorActionV4,
    binding_catalog: Dict[str, Dict[str, Dict[str, Any]]],
    allow_stub: bool,
) -> Tuple[List[OperatorAction], Dict[str, Any]]:
    resolved_bindings = _resolve_target_bindings(
        action=action,
        binding_catalog=binding_catalog,
    )
    component_ids = _resolve_component_ids(
        action=action,
        binding_catalog=binding_catalog,
        resolved_bindings=resolved_bindings,
    )
    axis, sign = _resolve_axis_and_sign(
        action,
        resolved_bindings=resolved_bindings,
    )
    default_focus = float(_safe_float(action.params.get("focus_ratio"), 0.62) or 0.62)

    if action.action == "place_on_panel":
        delta = abs(float(_safe_float(action.params.get("delta_mm"), 8.0) or 8.0)) * sign
        return (
            [
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": component_ids,
                        "axis": axis,
                        "delta_mm": delta,
                        "focus_ratio": default_focus,
                    },
                    note="v4:place_on_panel",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["group_move"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "align_payload_to_aperture":
        delta = abs(float(_safe_float(action.params.get("alignment_pull_mm"), 6.0) or 6.0)) * sign
        return (
            [
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": component_ids,
                        "axis": axis,
                        "delta_mm": delta,
                        "focus_ratio": default_focus,
                    },
                    note="v4:align_payload_to_aperture",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["group_move"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "reorient_to_allowed_face":
        if component_ids:
            return (
                [
                    OperatorAction(
                        action="group_move",
                        params={
                            "component_ids": component_ids,
                            "axis": axis,
                            "delta_mm": abs(
                                float(_safe_float(action.params.get("delta_mm"), 4.0) or 4.0)
                            )
                            * sign,
                            "focus_ratio": default_focus,
                        },
                        note="v4:reorient_to_allowed_face",
                    )
                ],
                _report(
                    source_action=action.action,
                    realized_actions=["group_move"],
                    resolved_component_ids=component_ids,
                    warnings=["orientation_degraded_to_face_bias_in_v3_kernel"],
                ),
            )
        return (
            [
                _stub_bias_action(
                    source_action=action.action,
                    axis=axis,
                    component_ids=component_ids,
                    focus_ratio=default_focus,
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["cg_recenter" if not component_ids else "group_move"],
                resolved_component_ids=component_ids,
                stubbed=True,
                warnings=["orientation_not_executable_without_resolved_subjects"],
            ),
        )

    if action.action == "mount_to_bracket_site":
        realized = [
            OperatorAction(
                action="add_bracket",
                params={
                    "component_ids": component_ids,
                    "axes": [axis],
                    "stiffness_gain": float(
                        _safe_float(action.params.get("stiffness_gain"), 0.35) or 0.35
                    ),
                    "focus_ratio": default_focus,
                },
                note="v4:mount_to_bracket_site",
            ),
            OperatorAction(
                action="group_move",
                params={
                    "component_ids": component_ids,
                    "axis": axis,
                    "delta_mm": abs(float(_safe_float(action.params.get("delta_mm"), 5.0) or 5.0))
                    * sign,
                    "focus_ratio": default_focus,
                },
                note="v4:mount_to_bracket_site",
            ),
        ]
        return (
            realized,
            _report(
                source_action=action.action,
                realized_actions=["add_bracket", "group_move"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "move_heat_source_to_radiator_zone":
        realized = [
            OperatorAction(
                action="group_move",
                params={
                    "component_ids": component_ids,
                    "axis": axis,
                    "delta_mm": abs(float(_safe_float(action.params.get("delta_mm"), 8.0) or 8.0))
                    * sign,
                    "focus_ratio": default_focus,
                },
                note="v4:move_heat_source_to_radiator_zone",
            )
        ]
        if len(component_ids) >= 2:
            realized.append(
                OperatorAction(
                    action="add_heatstrap",
                    params={
                        "component_ids": component_ids[:2],
                        "conductance": float(
                            _safe_float(action.params.get("conductance"), 110.0) or 110.0
                        ),
                        "update_mode": "max",
                        "focus_ratio": default_focus,
                    },
                    note="v4:move_heat_source_to_radiator_zone",
                )
            )
        return (
            realized,
            _report(
                source_action=action.action,
                realized_actions=[str(item.action) for item in realized],
                resolved_component_ids=component_ids,
                stubbed=len(component_ids) < 2,
                warnings=(
                    ["insufficient_components_for_heatstrap_bridge"]
                    if len(component_ids) < 2
                    else []
                ),
            ),
        )

    if action.action == "separate_hot_pair":
        spread_ids = component_ids[: max(2, min(len(component_ids), 4))]
        return (
            [
                OperatorAction(
                    action="hot_spread",
                    params={
                        "component_ids": spread_ids,
                        "axis": axis,
                        "min_pair_distance_mm": float(
                            _safe_float(action.params.get("min_pair_distance_mm"), 12.0) or 12.0
                        ),
                        "spread_strength": float(
                            _safe_float(action.params.get("spread_strength"), 0.72) or 0.72
                        ),
                        "focus_ratio": default_focus,
                    },
                    note="v4:separate_hot_pair",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["hot_spread"],
                resolved_component_ids=spread_ids,
            ),
        )

    if action.action == "add_heatstrap":
        if len(component_ids) < 2:
            return (
                [
                    _stub_bias_action(
                        source_action=action.action,
                        axis=axis,
                        component_ids=component_ids,
                        focus_ratio=default_focus,
                    )
                ],
                _report(
                    source_action=action.action,
                    realized_actions=["cg_recenter" if not component_ids else "group_move"],
                    resolved_component_ids=component_ids,
                    stubbed=True,
                    warnings=["insufficient_components_for_add_heatstrap"],
                ),
            )
        return (
            [
                OperatorAction(
                    action="add_heatstrap",
                    params={
                        "component_ids": component_ids,
                        "conductance": float(
                            _safe_float(action.params.get("conductance"), 120.0) or 120.0
                        ),
                        "update_mode": str(action.params.get("update_mode", "max") or "max"),
                        "focus_ratio": default_focus,
                    },
                    note="v4:add_heatstrap",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["add_heatstrap"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "add_thermal_pad":
        if len(component_ids) >= 2:
            return (
                [
                    OperatorAction(
                        action="set_thermal_contact",
                        params={
                            "source_component": component_ids[0],
                            "target_component_ids": component_ids[1:],
                            "conductance": float(
                                _safe_float(action.params.get("conductance"), 60.0) or 60.0
                            ),
                            "update_mode": str(action.params.get("update_mode", "set") or "set"),
                            "focus_ratio": default_focus,
                        },
                        note="v4:add_thermal_pad",
                    )
                ],
                _report(
                    source_action=action.action,
                    realized_actions=["set_thermal_contact"],
                    resolved_component_ids=component_ids,
                ),
            )
        return (
            [
                _stub_bias_action(
                    source_action=action.action,
                    axis=axis,
                    component_ids=component_ids,
                    focus_ratio=default_focus,
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["cg_recenter" if not component_ids else "group_move"],
                resolved_component_ids=component_ids,
                stubbed=True,
                warnings=["insufficient_components_for_add_thermal_pad"],
            ),
        )

    if action.action == "add_mount_bracket":
        return (
            [
                OperatorAction(
                    action="add_bracket",
                    params={
                        "component_ids": component_ids,
                        "axes": [axis],
                        "stiffness_gain": float(
                            _safe_float(action.params.get("stiffness_gain"), 0.40) or 0.40
                        ),
                        "focus_ratio": default_focus,
                    },
                    note="v4:add_mount_bracket",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["add_bracket"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "rebalance_cg_by_group_shift":
        realized = [
            OperatorAction(
                action="cg_recenter",
                params={
                    "component_ids": component_ids,
                    "axes": _normalize_axes(action.params.get("axes"), default=[axis, "y"]),
                    "strength": float(_safe_float(action.params.get("strength"), 0.65) or 0.65),
                    "focus_ratio": default_focus,
                },
                note="v4:rebalance_cg_by_group_shift",
            )
        ]
        if component_ids:
            realized.append(
                OperatorAction(
                    action="group_move",
                    params={
                        "component_ids": component_ids,
                        "axis": axis,
                        "delta_mm": float(_safe_float(action.params.get("delta_mm"), 4.0) or 4.0)
                        * sign,
                        "focus_ratio": default_focus,
                    },
                    note="v4:rebalance_cg_by_group_shift",
                )
            )
        return (
            realized,
            _report(
                source_action=action.action,
                realized_actions=[str(item.action) for item in realized],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "shorten_power_bus":
        source_component = component_ids[0] if component_ids else ""
        target_component_ids = component_ids[1:] if len(component_ids) > 1 else []
        return (
            [
                OperatorAction(
                    action="bus_proximity_opt",
                    params={
                        "source_component": source_component,
                        "target_component_ids": target_component_ids,
                        "axes": _normalize_axes(action.params.get("axes"), default=["x", "y"]),
                        "focus_ratio": default_focus,
                    },
                    note="v4:shorten_power_bus",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["bus_proximity_opt"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "protect_fov_keepout":
        keepout_center = float(
            _safe_float(
                action.params.get("keepout_center_mm"),
                _binding_attr_float(resolved_bindings, "center_mm", 0.0),
            )
            or 0.0
        )
        min_sep = float(
            _safe_float(
                action.params.get("min_separation_mm"),
                _binding_attr_float(resolved_bindings, "min_separation_mm", 18.0),
            )
            or 18.0
        )
        preferred_side = str(
            action.params.get("preferred_side")
            or _binding_attr_text(resolved_bindings, "preferred_side", "auto")
            or "auto"
        ).strip().lower()
        return (
            [
                OperatorAction(
                    action="fov_keepout_push",
                    params={
                        "component_ids": component_ids,
                        "axis": axis,
                        "keepout_center_mm": keepout_center,
                        "min_separation_mm": min_sep,
                        "preferred_side": preferred_side,
                        "focus_ratio": default_focus,
                    },
                    note="v4:protect_fov_keepout",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["fov_keepout_push"],
                resolved_component_ids=component_ids,
            ),
        )

    if action.action == "activate_aperture_site":
        if component_ids:
            return (
                [
                    OperatorAction(
                        action="group_move",
                        params={
                            "component_ids": component_ids,
                            "axis": axis,
                            "delta_mm": abs(
                                float(_safe_float(action.params.get("delta_mm"), 6.0) or 6.0)
                            )
                            * sign,
                            "focus_ratio": float(
                                _safe_float(action.params.get("focus_ratio"), 0.70) or 0.70
                            ),
                        },
                        note="v4:activate_aperture_site",
                    )
                ],
                _report(
                    source_action=action.action,
                    realized_actions=["group_move"],
                    resolved_component_ids=component_ids,
                    warnings=["aperture_site_activation_degraded_to_axis_pull_in_v3_kernel"],
                ),
            )
        return (
            [
                OperatorAction(
                    action="cg_recenter",
                    params={
                        "component_ids": component_ids,
                        "axes": [axis],
                        "strength": float(_safe_float(action.params.get("strength"), 0.20) or 0.20),
                        "focus_ratio": float(
                            _safe_float(action.params.get("focus_ratio"), 0.70) or 0.70
                        ),
                    },
                    note="v4:activate_aperture_site",
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["cg_recenter"],
                resolved_component_ids=component_ids,
                stubbed=True,
                warnings=["aperture_site_activation_is_bias_only_without_resolved_subjects"],
            ),
        )

    if allow_stub:
        return (
            [
                _stub_bias_action(
                    source_action=action.action,
                    axis=axis,
                    component_ids=component_ids,
                    focus_ratio=default_focus,
                )
            ],
            _report(
                source_action=action.action,
                realized_actions=["cg_recenter" if not component_ids else "group_move"],
                resolved_component_ids=component_ids,
                stubbed=True,
                warnings=["unhandled_v4_action_fell_back_to_stub"],
            ),
        )
    return [], _report(
        source_action=action.action,
        realized_actions=[],
        resolved_component_ids=component_ids,
        stubbed=True,
        warnings=["unhandled_v4_action_without_stub"],
    )


def _stub_bias_action(
    *,
    source_action: str,
    axis: str,
    component_ids: Sequence[str],
    focus_ratio: float,
) -> OperatorAction:
    if component_ids:
        return OperatorAction(
            action="group_move",
            params={
                "component_ids": list(component_ids),
                "axis": axis,
                "delta_mm": 0.0,
                "focus_ratio": float(focus_ratio),
            },
            note=f"v4_stub:{source_action}",
        )
    return OperatorAction(
        action="cg_recenter",
        params={
            "component_ids": [],
            "axes": [axis],
            "strength": 0.15,
            "focus_ratio": float(max(min(focus_ratio, 1.0), 0.35)),
        },
        note=f"v4_stub:{source_action}",
    )


def _report(
    *,
    source_action: str,
    realized_actions: Sequence[str],
    resolved_component_ids: Sequence[str],
    stubbed: bool = False,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "source_action": str(source_action or ""),
        "realized_actions": [str(item) for item in list(realized_actions or []) if str(item)],
        "resolved_component_ids": [
            str(item).strip() for item in list(resolved_component_ids or []) if str(item).strip()
        ],
        "stubbed": bool(stubbed),
        "warnings": [str(item) for item in list(warnings or []) if str(item)],
    }


def _normalize_binding_catalog(
    raw: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    catalog: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not raw:
        return catalog
    for object_type, payload in dict(raw).items():
        normalized_type = str(object_type or "").strip().lower()
        if isinstance(payload, Mapping):
            catalog[normalized_type] = {}
            for object_id, binding_payload in dict(payload).items():
                normalized_object_id = str(object_id).strip()
                if not normalized_object_id:
                    continue
                if isinstance(binding_payload, Mapping):
                    attrs = dict(binding_payload)
                    component_ids = _normalize_component_ids(
                        attrs.get("component_ids")
                        or attrs.get("members")
                        or attrs.get("components")
                    )
                    catalog[normalized_type][normalized_object_id] = {
                        "component_ids": component_ids,
                        "attributes": attrs,
                    }
                else:
                    catalog[normalized_type][normalized_object_id] = {
                        "component_ids": _normalize_component_ids(binding_payload),
                        "attributes": {},
                    }
            continue
        normalized_ids = _normalize_component_ids(payload)
        catalog[normalized_type] = {
            str(item): {"component_ids": [str(item)], "attributes": {}}
            for item in list(normalized_ids or [])
            if str(item).strip()
        }
    return catalog


def _collect_component_ids_from_catalog(
    catalog: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> set[str]:
    component_ids: set[str] = set()
    for payload in dict(catalog or {}).values():
        for object_id, binding_payload in dict(payload or {}).items():
            if str(object_id).strip():
                component_ids.add(str(object_id).strip())
            values = list(dict(binding_payload or {}).get("component_ids", []) or [])
            for item in values:
                normalized = str(item).strip()
                if normalized:
                    component_ids.add(normalized)
    return component_ids


def _resolve_component_ids(
    *,
    action: OperatorActionV4,
    binding_catalog: Mapping[str, Mapping[str, Mapping[str, Any]]],
    resolved_bindings: Optional[Sequence[ResolvedTargetBinding]] = None,
) -> List[str]:
    resolved: List[str] = []
    seen: set[str] = set()

    def _append_many(values: Iterable[str]) -> None:
        for item in values:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)

    params = dict(action.params or {})
    _append_many(_normalize_component_ids(params.get("component_ids")))
    _append_many(_normalize_component_ids(params.get("target_component_ids")))
    source_component = str(params.get("source_component") or "").strip()
    if source_component:
        _append_many([source_component])

    for binding in list(resolved_bindings or []):
        if binding.component_ids:
            _append_many(list(binding.component_ids))
            continue
        if str(binding.object_type or "") == "component":
            _append_many([str(binding.object_id or "")])
            continue
        if str(binding.object_id or "") in dict(binding_catalog.get("component", {}) or {}):
            _append_many([str(binding.object_id or "")])

    return resolved


def _resolve_target_bindings(
    *,
    action: OperatorActionV4,
    binding_catalog: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> List[ResolvedTargetBinding]:
    resolved: List[ResolvedTargetBinding] = []
    seen: set[tuple[str, str, str]] = set()
    for binding in list(action.targets or []):
        object_type = str(binding.object_type or "").strip().lower()
        object_id = str(binding.object_id or "").strip()
        role = str(binding.role or "").strip().lower()
        if not object_type or not object_id:
            continue
        binding_key = (object_type, object_id, role)
        if binding_key in seen:
            continue
        seen.add(binding_key)
        catalog_payload = dict(
            dict(binding_catalog.get(object_type, {}) or {}).get(object_id, {}) or {}
        )
        catalog_attrs = dict(catalog_payload.get("attributes", {}) or {})
        inline_attrs = dict(binding.attributes or {})
        merged_attrs = dict(catalog_attrs)
        merged_attrs.update(inline_attrs)
        component_ids = _normalize_component_ids(
            inline_attrs.get("component_ids")
            or inline_attrs.get("members")
            or inline_attrs.get("components")
        )
        if not component_ids:
            component_ids = _normalize_component_ids(catalog_payload.get("component_ids"))
        if object_type == "component" and not component_ids:
            component_ids = [object_id]
        resolved.append(
            ResolvedTargetBinding(
                object_type=object_type,
                object_id=object_id,
                role=role,
                component_ids=tuple(component_ids),
                attributes=merged_attrs,
            )
        )
    return resolved


def _resolve_axis_and_sign(
    action: OperatorActionV4,
    *,
    resolved_bindings: Optional[Sequence[ResolvedTargetBinding]] = None,
) -> Tuple[str, float]:
    params = dict(action.params or {})
    direct_axis = _normalize_axis(params.get("axis"))
    if direct_axis is not None:
        return direct_axis, _normalize_sign(params.get("direction") or params.get("preferred_side"))

    for binding in list(resolved_bindings or []):
        attrs = dict(binding.attributes or {})
        for key in ("axis", "normal_axis", "preferred_axis", "face", "normal_face", "allowed_face"):
            axis, sign = _extract_axis_and_sign(attrs.get(key))
            if axis is not None:
                return axis, sign
    return "z", 1.0


def _extract_axis_and_sign(value: Any) -> Tuple[Optional[str], float]:
    text = str(value or "").strip().lower()
    if not text:
        return None, 1.0
    sign = -1.0 if any(token in text for token in ("-", "negative", "minus")) else 1.0
    normalized = (
        text.replace("+", "")
        .replace("-", "")
        .replace("positive_", "")
        .replace("negative_", "")
        .replace("plus_", "")
        .replace("minus_", "")
    )
    axis = _normalize_axis(normalized)
    return axis, sign


def _normalize_axis(value: Any) -> Optional[str]:
    axis = str(value or "").strip().lower()
    if axis in {"x", "y", "z"}:
        return axis
    if axis in {"xp", "xn", "x+", "x-"}:
        return "x"
    if axis in {"yp", "yn", "y+", "y-"}:
        return "y"
    if axis in {"zp", "zn", "z+", "z-"}:
        return "z"
    return None


def _normalize_sign(value: Any) -> float:
    text = str(value or "").strip().lower()
    if text in {"negative", "minus", "-", "backward"}:
        return -1.0
    return 1.0


def _normalize_axes(value: Any, *, default: Sequence[str]) -> List[str]:
    if value is None:
        raw_items = list(default)
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, Sequence):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = list(default)
    axes: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        axis = _normalize_axis(item)
        if axis is None or axis in seen:
            continue
        seen.add(axis)
        axes.append(axis)
    return axes or list(default)


def _binding_attr_float(
    bindings: Sequence[ResolvedTargetBinding],
    key: str,
    default: float,
) -> float:
    for binding in list(bindings or []):
        attrs = dict(binding.attributes or {})
        numeric = _safe_float(attrs.get(key))
        if numeric is not None:
            return float(numeric)
    return float(default)


def _binding_attr_text(
    bindings: Sequence[ResolvedTargetBinding],
    key: str,
    default: str,
) -> str:
    for binding in list(bindings or []):
        attrs = dict(binding.attributes or {})
        text = str(attrs.get(key) or "").strip()
        if text:
            return text
    return str(default)


def _normalize_component_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, Sequence):
        raw_items = [str(item).strip() for item in value]
    else:
        return []
    deduped: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default
