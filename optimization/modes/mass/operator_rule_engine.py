"""
Minimal hard-rule / soft-preference rule engine for semantic operator DSL v4.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .operator_program_v4 import (
    ACTION_CONTRACTS_V4,
    OperatorProgramV4,
    SUPPORTED_TARGET_OBJECT_TYPES,
    build_operator_program_v4_payload,
    validate_operator_program_v4,
)


KNOWN_HARD_RULES = frozenset(
    {
        "shell_aperture_match",
        "mount_site_allowed",
        "allowed_face",
        "collision_free",
        "minimum_clearance",
        "fov_keepout",
        "cg_limit",
        "thermal_boundary",
        "structural_boundary",
        "power_boundary",
        "catalog_interface",
    }
)
KNOWN_SOFT_PREFERENCES = frozenset(
    {
        "battery_near_structure",
        "payload_on_mission_face",
        "heat_source_to_radiator",
        "adcs_near_cg",
        "short_power_bus",
        "layout_symmetry",
        "serviceability",
    }
)
RULE_TARGET_HINTS = {
    "shell_aperture_match": frozenset({"aperture", "panel"}),
    "mount_site_allowed": frozenset({"mount_site", "panel"}),
    "allowed_face": frozenset({"panel", "aperture"}),
    "collision_free": frozenset({"component", "component_group"}),
    "minimum_clearance": frozenset({"component", "component_group"}),
    "fov_keepout": frozenset({"aperture", "zone"}),
    "cg_limit": frozenset({"component", "component_group"}),
    "thermal_boundary": frozenset({"zone", "panel", "component", "component_group"}),
    "structural_boundary": frozenset({"mount_site", "panel", "component", "component_group"}),
    "power_boundary": frozenset({"component", "component_group", "zone"}),
    "catalog_interface": frozenset({"mount_site", "panel", "component_group"}),
}
PREFERENCE_TARGET_HINTS = {
    "battery_near_structure": frozenset({"zone", "panel", "mount_site"}),
    "payload_on_mission_face": frozenset({"panel", "aperture"}),
    "heat_source_to_radiator": frozenset({"zone", "panel"}),
    "adcs_near_cg": frozenset({"component", "component_group"}),
    "short_power_bus": frozenset({"component", "component_group", "zone"}),
    "layout_symmetry": frozenset({"panel", "component_group"}),
    "serviceability": frozenset({"panel", "mount_site"}),
}


def evaluate_operator_rules_v4(
    program: OperatorProgramV4 | Mapping[str, Any],
    *,
    object_catalog: Optional[Mapping[str, Iterable[str]]] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Evaluate hard rules and soft preferences for a v4 operator program.

    This engine checks only contract-level governance:
    - hard_rules / soft_preferences separation,
    - target-object binding completeness,
    - known rule identifiers,
    - optional object catalog membership.

    It does not check shell geometry, aperture geometry, collisions, or physics.
    """
    validation = validate_operator_program_v4(
        program,
        available_object_ids=object_catalog,
    )
    if not validation.get("is_valid", False):
        return {
            "is_valid": False,
            "errors": list(validation.get("errors", []) or []),
            "warnings": list(validation.get("warnings", []) or []),
            "hard_rules": [],
            "soft_preferences": [],
            "action_reports": [],
            "normalized_payload": validation.get("normalized_payload"),
            "program": None,
        }

    parsed: OperatorProgramV4 = validation["program"]
    normalized_payload = build_operator_program_v4_payload(parsed)
    errors: List[str] = []
    warnings: List[str] = list(validation.get("warnings", []) or [])
    hard_rules_checked: List[str] = []
    soft_preferences_checked: List[str] = []
    action_reports: List[Dict[str, Any]] = []
    normalized_catalog = _normalize_object_catalog(object_catalog)

    for index, action in enumerate(list(parsed.actions or []), start=1):
        action_errors: List[str] = []
        action_warnings: List[str] = []
        bound_types = {
            str(binding.object_type or "").strip().lower()
            for binding in list(action.targets or [])
            if str(binding.object_type or "").strip()
        }
        contract = ACTION_CONTRACTS_V4.get(str(action.action or ""))
        if contract is None:
            action_errors.append(f"missing_contract:{action.action}")
        else:
            for target_group in list(contract.required_target_groups or []):
                if not bound_types.intersection(set(target_group)):
                    action_errors.append(
                        "missing_required_target_group:" + "/".join(sorted(target_group))
                    )

        for binding in list(action.targets or []):
            object_type = str(binding.object_type or "").strip().lower()
            object_id = str(binding.object_id or "").strip()
            if object_type not in SUPPORTED_TARGET_OBJECT_TYPES:
                action_errors.append(f"unsupported_target_type:{object_type}")
                continue
            if normalized_catalog.get(object_type) and object_id not in normalized_catalog[object_type]:
                action_errors.append(f"unknown_target:{object_type}:{object_id}")

        for hard_rule in list(action.hard_rules or []):
            rule_id = str(hard_rule.rule_id or "").strip().lower()
            if not rule_id:
                continue
            hard_rules_checked.append(rule_id)
            if rule_id not in KNOWN_HARD_RULES:
                action_errors.append(f"unknown_hard_rule:{rule_id}")
                continue
            hinted_targets = RULE_TARGET_HINTS.get(rule_id, frozenset())
            if hinted_targets and not bound_types.intersection(set(hinted_targets)):
                action_errors.append(
                    "hard_rule_target_mismatch:"
                    + rule_id
                    + ":"
                    + "/".join(sorted(hinted_targets))
                )

        preference_score = 0.0
        for preference in list(action.soft_preferences or []):
            rule_id = str(preference.rule_id or "").strip().lower()
            if not rule_id:
                continue
            soft_preferences_checked.append(rule_id)
            preference_score += max(float(preference.weight or 0.0), 0.0)
            if rule_id not in KNOWN_SOFT_PREFERENCES:
                action_warnings.append(f"unknown_soft_preference:{rule_id}")
                continue
            hinted_targets = PREFERENCE_TARGET_HINTS.get(rule_id, frozenset())
            if hinted_targets and not bound_types.intersection(set(hinted_targets)):
                action_warnings.append(
                    "soft_preference_target_mismatch:"
                    + rule_id
                    + ":"
                    + "/".join(sorted(hinted_targets))
                )

        if strict and not list(action.hard_rules or []):
            action_errors.append("missing_hard_rules")
        if strict and not list(action.soft_preferences or []):
            action_warnings.append("missing_soft_preferences")

        errors.extend(f"action[{index}] {item}" for item in action_errors)
        warnings.extend(f"action[{index}] {item}" for item in action_warnings)
        action_reports.append(
            {
                "action_index": int(index),
                "action": str(action.action or ""),
                "target_types": sorted(bound_types),
                "hard_rules_checked": [
                    str(item.rule_id or "").strip().lower()
                    for item in list(action.hard_rules or [])
                    if str(item.rule_id or "").strip()
                ],
                "soft_preferences_checked": [
                    str(item.rule_id or "").strip().lower()
                    for item in list(action.soft_preferences or [])
                    if str(item.rule_id or "").strip()
                ],
                "preference_score": float(preference_score),
                "errors": action_errors,
                "warnings": action_warnings,
            }
        )

    return {
        "is_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "hard_rules": sorted(set(hard_rules_checked)),
        "soft_preferences": sorted(set(soft_preferences_checked)),
        "action_reports": action_reports,
        "normalized_payload": normalized_payload,
        "program": parsed,
    }


def _normalize_object_catalog(
    raw: Optional[Mapping[str, Iterable[str]]],
) -> Dict[str, set[str]]:
    catalog: Dict[str, set[str]] = {}
    if not raw:
        return catalog
    for object_type, payload in dict(raw).items():
        normalized_type = str(object_type or "").strip().lower()
        if normalized_type not in SUPPORTED_TARGET_OBJECT_TYPES:
            continue
        if isinstance(payload, Mapping):
            values = [str(item).strip() for item in dict(payload).keys()]
        else:
            values = [str(item).strip() for item in list(payload or [])]
        catalog[normalized_type] = {item for item in values if item}
    return catalog
