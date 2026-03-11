"""
Operator semantic display helpers for review/visualization consumers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Dict, List

from optimization.modes.mass.operator_program_v4 import (
    SUPPORTED_ACTIONS_V4,
    normalize_operator_action_name_v4,
    normalize_operator_program_v4_payload,
)


_ACTION_LABELS: Dict[str, str] = {
    "place_on_panel": "panel placement",
    "align_payload_to_aperture": "payload-to-aperture alignment",
    "reorient_to_allowed_face": "allowed-face reorientation",
    "mount_to_bracket_site": "mount-site alignment",
    "move_heat_source_to_radiator_zone": "radiator-zone heat relocation",
    "separate_hot_pair": "hot-pair separation",
    "add_heatstrap": "heatstrap addition",
    "add_thermal_pad": "thermal-pad addition",
    "add_mount_bracket": "mount-bracket support",
    "rebalance_cg_by_group_shift": "cg rebalancing shift",
    "shorten_power_bus": "power-bus shortening",
    "protect_fov_keepout": "fov keepout protection",
    "activate_aperture_site": "aperture-site activation",
    "group_move": "group move",
    "cg_recenter": "cg recentering",
    "hot_spread": "hot-spot spreading",
    "swap": "component swap",
    "set_thermal_contact": "thermal-contact update",
    "add_bracket": "bracket support",
    "stiffener_insert": "stiffener insertion",
    "bus_proximity_opt": "power-bus proximity optimization",
    "fov_keepout_push": "fov keepout push",
}

_OBJECT_TYPE_LABELS: Dict[str, str] = {
    "component": "component",
    "component_group": "group",
    "panel": "panel",
    "aperture": "aperture",
    "zone": "zone",
    "mount_site": "mount-site",
}

_ROLE_ORDER = {
    "subject": 0,
    "target": 1,
    "context": 2,
}


def operator_action_label(action: Any) -> str:
    normalized = normalize_operator_action_name_v4(action)
    if not normalized:
        normalized = str(action or "").strip().lower()
    if not normalized:
        return ""
    label = _ACTION_LABELS.get(normalized)
    if label:
        return label
    return normalized.replace("_", " ")


def build_operator_semantic_display(
    *,
    primary_action: Any,
    dsl_version: Any = "",
    metadata: Mapping[str, Any] | None = None,
    expected_effects: Any = None,
    observed_effects: Sequence[str] | None = None,
    rule_engine_report: Mapping[str, Any] | None = None,
) -> Dict[str, str]:
    normalized_action = normalize_operator_action_name_v4(primary_action)
    if not normalized_action:
        normalized_action = str(primary_action or "").strip().lower()

    action_label = operator_action_label(normalized_action)
    primary_payload = _select_primary_action_payload(
        primary_action=normalized_action,
        dsl_version=dsl_version,
        metadata=metadata,
    )
    target_summary = _summarize_targets(primary_payload)
    rule_summary = _summarize_rules(primary_payload, rule_engine_report)
    expected_effect_summary = _summarize_expected_effects(
        primary_payload.get("expected_effects") if primary_payload else expected_effects
    )
    observed_effect_summary = _summarize_effect_list(observed_effects)

    semantic_caption_short = action_label
    if target_summary:
        semantic_caption_short = f"{action_label} @ {target_summary}"

    semantic_parts = [part for part in [action_label] if part]
    if target_summary:
        semantic_parts.append(f"targets={target_summary}")
    if rule_summary:
        semantic_parts.append(f"rules={rule_summary}")
    if expected_effect_summary:
        semantic_parts.append(f"expected={expected_effect_summary}")
    if observed_effect_summary:
        semantic_parts.append(f"observed={observed_effect_summary}")

    return {
        "primary_action_label": action_label,
        "semantic_caption_short": semantic_caption_short,
        "semantic_caption": "; ".join(semantic_parts),
        "target_summary": target_summary,
        "rule_summary": rule_summary,
        "expected_effect_summary": expected_effect_summary,
        "observed_effect_summary": observed_effect_summary,
    }


def _select_primary_action_payload(
    *,
    primary_action: str,
    dsl_version: Any,
    metadata: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    normalized_dsl_version = str(dsl_version or "").strip().lower()
    payloads = _extract_semantic_action_payloads(metadata)
    if not payloads and normalized_dsl_version not in {"v4", "dsl_v4", "opmaas-r4", "opmaas_r4"}:
        return {}
    if not payloads:
        return {}

    for payload in payloads:
        if normalize_operator_action_name_v4(payload.get("action", "")) == primary_action:
            return payload
    return payloads[0]


def _extract_semantic_action_payloads(metadata: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    payload = dict(metadata or {})
    candidates: List[Dict[str, Any]] = []
    for key in (
        "selected_semantic_action_payloads",
        "semantic_action_payloads",
        "selected_operator_action_payloads",
        "selected_candidate_semantic_action_payloads",
    ):
        candidates.extend(_coerce_normalized_action_payloads(payload.get(key)))

    operator_program_patch = payload.get("operator_program_patch")
    if isinstance(operator_program_patch, Mapping):
        candidates.extend(_coerce_normalized_action_payloads(operator_program_patch.get("actions")))

    operator_program = payload.get("operator_program")
    if isinstance(operator_program, Mapping):
        candidates.extend(_coerce_normalized_action_payloads(operator_program.get("actions")))
        if isinstance(operator_program.get("program_patch"), Mapping):
            candidates.extend(
                _coerce_normalized_action_payloads(
                    dict(operator_program.get("program_patch", {}) or {}).get("actions")
                )
            )

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def _coerce_normalized_action_payloads(value: Any) -> List[Dict[str, Any]]:
    raw_items = _coerce_payload_sequence(value)
    normalized_items: List[Dict[str, Any]] = []
    for raw in raw_items:
        normalized = _normalize_action_payload_like(raw)
        if normalized:
            normalized_items.append(normalized)
    return normalized_items


def _coerce_payload_sequence(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return _coerce_payload_sequence(parsed)
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return list(value)
    return []


def _normalize_action_payload_like(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    raw = dict(value)
    action_name = str(
        raw.get("action")
        or raw.get("type")
        or raw.get("operator")
        or raw.get("name")
        or ""
    ).strip()
    if not action_name:
        return {}

    normalized_action_name = normalize_operator_action_name_v4(action_name)
    if normalized_action_name in SUPPORTED_ACTIONS_V4:
        normalized_program = normalize_operator_program_v4_payload(
            {
                "program_id": "review_display",
                "actions": [raw],
            }
        )
        actions = list(normalized_program.get("actions", []) or [])
        if actions and isinstance(actions[0], Mapping):
            return dict(actions[0])

    return {
        "action": normalized_action_name or action_name.strip().lower(),
        "targets": _coerce_payload_sequence(raw.get("targets")),
        "hard_rules": _coerce_payload_sequence(raw.get("hard_rules")),
        "soft_preferences": _coerce_payload_sequence(raw.get("soft_preferences")),
        "expected_effects": dict(raw.get("expected_effects", {}) or {})
        if isinstance(raw.get("expected_effects"), Mapping)
        else raw.get("expected_effects"),
    }


def _summarize_targets(primary_payload: Mapping[str, Any] | None) -> str:
    payload = dict(primary_payload or {})
    targets = [
        dict(item)
        for item in list(payload.get("targets", []) or [])
        if isinstance(item, Mapping)
    ]
    if not targets:
        return ""

    def _sort_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
        role = str(item.get("role", "") or "").strip().lower()
        object_type = str(item.get("object_type", "") or "").strip().lower()
        object_id = str(item.get("object_id", "") or "").strip()
        return (_ROLE_ORDER.get(role, 99), object_type, object_id)

    parts: List[str] = []
    for target in sorted(targets, key=_sort_key):
        object_type = str(target.get("object_type", "") or "").strip().lower()
        object_id = str(target.get("object_id", "") or "").strip()
        if not object_type or not object_id:
            continue
        role = str(target.get("role", "") or "").strip().lower()
        role_prefix = f"{role} " if role else ""
        type_label = _OBJECT_TYPE_LABELS.get(object_type, object_type)
        extra = ""
        attributes = dict(target.get("attributes", {}) or {})
        if object_type == "component_group":
            component_ids = [
                str(item).strip()
                for item in list(attributes.get("component_ids", []) or [])
                if str(item).strip()
            ]
            if component_ids:
                extra = f"[{len(component_ids)}]"
        parts.append(f"{role_prefix}{type_label}:{object_id}{extra}")
    return "; ".join(parts)


def _extract_rule_ids(value: Any) -> List[str]:
    items = _coerce_payload_sequence(value)
    rule_ids: List[str] = []
    for item in items:
        if isinstance(item, str):
            rule_id = str(item).strip()
        elif isinstance(item, Mapping):
            rule_id = str(dict(item).get("rule_id", "") or dict(item).get("id", "") or "").strip()
        else:
            rule_id = ""
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)
    return rule_ids


def _summarize_rules(
    primary_payload: Mapping[str, Any] | None,
    rule_engine_report: Mapping[str, Any] | None,
) -> str:
    payload = dict(primary_payload or {})
    report = dict(rule_engine_report or {})

    hard_rules = _extract_rule_ids(payload.get("hard_rules"))
    if not hard_rules:
        hard_rules = _extract_rule_ids(report.get("hard_rules"))

    soft_preferences = _extract_rule_ids(payload.get("soft_preferences"))
    if not soft_preferences:
        soft_preferences = _extract_rule_ids(report.get("soft_preferences"))

    parts: List[str] = []
    if hard_rules:
        parts.append(f"hard={','.join(hard_rules)}")
    if soft_preferences:
        parts.append(f"soft={','.join(soft_preferences)}")
    return "; ".join(parts)


def _summarize_expected_effects(value: Any) -> str:
    if isinstance(value, Mapping):
        rendered = []
        for key, raw in dict(value).items():
            metric_key = str(key or "").strip()
            if not metric_key:
                continue
            if isinstance(raw, float):
                raw_text = f"{raw:.3g}"
            else:
                raw_text = str(raw)
            rendered.append(f"{metric_key}={raw_text}")
        return ", ".join(rendered)
    return _summarize_effect_list(value)


def _summarize_effect_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        values = [str(item).strip() for item in list(value) if str(item).strip()]
    else:
        values = [str(value).strip()] if str(value).strip() else []
    return ", ".join(values)
