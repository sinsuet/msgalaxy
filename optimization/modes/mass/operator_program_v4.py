"""
Semantic operator-program schema and validator for OP-MaaS DSL v4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


DSL_V4_VERSION = "opmaas-r4"
MAX_ACTIONS_DEFAULT = 10
MAX_TARGETS_PER_ACTION = 12

# Canonical semantic family buckets consumed by review/visualization.
OPERATOR_ACTION_FAMILY_MAP_V4: Dict[str, str] = {
    "place_on_panel": "geometry",
    "align_payload_to_aperture": "aperture",
    "reorient_to_allowed_face": "aperture",
    "mount_to_bracket_site": "structural",
    "move_heat_source_to_radiator_zone": "thermal",
    "separate_hot_pair": "thermal",
    "add_heatstrap": "thermal",
    "add_thermal_pad": "thermal",
    "add_mount_bracket": "structural",
    "rebalance_cg_by_group_shift": "geometry",
    "shorten_power_bus": "power",
    "protect_fov_keepout": "mission",
    "activate_aperture_site": "aperture",
}

SUPPORTED_ACTIONS_V4 = frozenset(OPERATOR_ACTION_FAMILY_MAP_V4.keys())
SUPPORTED_TARGET_OBJECT_TYPES = frozenset(
    {"component", "component_group", "panel", "aperture", "zone", "mount_site"}
)


def normalize_operator_action_name_v4(action: Any) -> str:
    return str(action or "").strip().lower()


def operator_action_family_v4(action: Any) -> str:
    return str(
        OPERATOR_ACTION_FAMILY_MAP_V4.get(normalize_operator_action_name_v4(action), "")
    )


@dataclass(frozen=True)
class V4ActionContract:
    required_target_groups: tuple[frozenset[str], ...]
    default_hard_rules: tuple[str, ...] = ()
    default_soft_preferences: tuple[str, ...] = ()


ACTION_CONTRACTS_V4: Dict[str, V4ActionContract] = {
    "place_on_panel": V4ActionContract(
        required_target_groups=(
            frozenset({"panel"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("mount_site_allowed", "minimum_clearance"),
        default_soft_preferences=("payload_on_mission_face",),
    ),
    "align_payload_to_aperture": V4ActionContract(
        required_target_groups=(
            frozenset({"aperture"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("shell_aperture_match", "allowed_face"),
        default_soft_preferences=("payload_on_mission_face",),
    ),
    "reorient_to_allowed_face": V4ActionContract(
        required_target_groups=(
            frozenset({"panel", "aperture"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("allowed_face",),
        default_soft_preferences=("serviceability",),
    ),
    "mount_to_bracket_site": V4ActionContract(
        required_target_groups=(
            frozenset({"mount_site"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("mount_site_allowed", "catalog_interface"),
        default_soft_preferences=("serviceability",),
    ),
    "move_heat_source_to_radiator_zone": V4ActionContract(
        required_target_groups=(
            frozenset({"zone"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("thermal_boundary",),
        default_soft_preferences=("heat_source_to_radiator",),
    ),
    "separate_hot_pair": V4ActionContract(
        required_target_groups=(frozenset({"component_group", "component"}),),
        default_hard_rules=("minimum_clearance", "thermal_boundary"),
        default_soft_preferences=("heat_source_to_radiator",),
    ),
    "add_heatstrap": V4ActionContract(
        required_target_groups=(frozenset({"component_group", "component"}),),
        default_hard_rules=("thermal_boundary",),
        default_soft_preferences=("heat_source_to_radiator",),
    ),
    "add_thermal_pad": V4ActionContract(
        required_target_groups=(
            frozenset({"panel", "mount_site"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("thermal_boundary", "catalog_interface"),
        default_soft_preferences=("heat_source_to_radiator",),
    ),
    "add_mount_bracket": V4ActionContract(
        required_target_groups=(
            frozenset({"mount_site", "panel"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("mount_site_allowed", "catalog_interface"),
        default_soft_preferences=("serviceability",),
    ),
    "rebalance_cg_by_group_shift": V4ActionContract(
        required_target_groups=(frozenset({"component_group", "component"}),),
        default_hard_rules=("cg_limit",),
        default_soft_preferences=("layout_symmetry", "adcs_near_cg"),
    ),
    "shorten_power_bus": V4ActionContract(
        required_target_groups=(frozenset({"component_group", "component"}),),
        default_hard_rules=("power_boundary",),
        default_soft_preferences=("short_power_bus",),
    ),
    "protect_fov_keepout": V4ActionContract(
        required_target_groups=(
            frozenset({"aperture", "zone"}),
            frozenset({"component_group", "component"}),
        ),
        default_hard_rules=("fov_keepout",),
        default_soft_preferences=("payload_on_mission_face",),
    ),
    "activate_aperture_site": V4ActionContract(
        required_target_groups=(frozenset({"aperture"}),),
        default_hard_rules=("shell_aperture_match",),
        default_soft_preferences=("payload_on_mission_face",),
    ),
}


class RuleBindingV4(BaseModel):
    """One hard-rule or soft-preference reference attached to a v4 action."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str
    weight: float = 1.0
    note: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_rule_id(self) -> "RuleBindingV4":
        self.rule_id = str(self.rule_id or "").strip().lower()
        if not self.rule_id:
            raise ValueError("rule_id 不能为空")
        self.weight = float(self.weight or 0.0)
        return self


class TargetBindingV4(BaseModel):
    """Explicit semantic object binding for one v4 action."""

    model_config = ConfigDict(extra="ignore")

    object_type: Literal[
        "component",
        "component_group",
        "panel",
        "aperture",
        "zone",
        "mount_site",
    ]
    object_id: str
    role: str = ""
    attributes: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_object_id(self) -> "TargetBindingV4":
        self.object_id = str(self.object_id or "").strip()
        self.role = str(self.role or "").strip().lower()
        if not self.object_id:
            raise ValueError("object_id 不能为空")
        return self


class OperatorActionV4(BaseModel):
    """One semantic operator action in DSL v4."""

    model_config = ConfigDict(extra="ignore")

    action: Literal[
        "place_on_panel",
        "align_payload_to_aperture",
        "reorient_to_allowed_face",
        "mount_to_bracket_site",
        "move_heat_source_to_radiator_zone",
        "separate_hot_pair",
        "add_heatstrap",
        "add_thermal_pad",
        "add_mount_bracket",
        "rebalance_cg_by_group_shift",
        "shorten_power_bus",
        "protect_fov_keepout",
        "activate_aperture_site",
    ]
    targets: List[TargetBindingV4] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)
    hard_rules: List[RuleBindingV4] = Field(default_factory=list)
    soft_preferences: List[RuleBindingV4] = Field(default_factory=list)
    expected_effects: Dict[str, float] = Field(default_factory=dict)
    note: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OperatorProgramV4(BaseModel):
    """Top-level v4 operator program."""

    model_config = ConfigDict(extra="ignore")

    program_id: str
    version: str = DSL_V4_VERSION
    rationale: str = ""
    actions: List[OperatorActionV4] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "OperatorProgramV4":
        self.program_id = str(self.program_id or "").strip()
        if not self.program_id:
            raise ValueError("program_id 不能为空")
        if not self.actions:
            raise ValueError("actions 不能为空")
        self.version = str(self.version or DSL_V4_VERSION).strip() or DSL_V4_VERSION
        return self


def normalize_operator_program_v4_payload(
    payload: Mapping[str, Any] | OperatorProgramV4,
) -> Dict[str, Any]:
    """Normalize common v4 aliases into the canonical schema payload."""
    if isinstance(payload, OperatorProgramV4):
        return payload.model_dump()

    raw = dict(payload or {})
    actions_payload = raw.get("actions")
    if not isinstance(actions_payload, list):
        for alias in ("semantic_actions", "actions_v4", "operator_actions_v4"):
            candidate = raw.get(alias)
            if isinstance(candidate, list):
                actions_payload = list(candidate)
                break
    if not isinstance(actions_payload, list):
        actions_payload = []

    normalized_actions: List[Dict[str, Any]] = []
    for item in actions_payload:
        normalized = _normalize_action_payload(item)
        if normalized is None:
            continue
        normalized_actions.append(normalized)

    version = str(
        raw.get("version")
        or raw.get("dsl_version")
        or raw.get("semantic_version")
        or DSL_V4_VERSION
    ).strip()
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "program_id": str(raw.get("program_id") or raw.get("id") or "").strip(),
        "version": version or DSL_V4_VERSION,
        "rationale": str(raw.get("rationale") or raw.get("reasoning") or "").strip(),
        "actions": normalized_actions,
        "metadata": dict(metadata),
    }


def validate_operator_program_v4(
    program: OperatorProgramV4 | Mapping[str, Any],
    *,
    available_object_ids: Optional[Mapping[str, Iterable[str]]] = None,
    max_actions: int = MAX_ACTIONS_DEFAULT,
) -> Dict[str, Any]:
    """
    Validate and normalize DSL v4 payloads.

    The validator checks semantic contract completeness, explicit object
    bindings, and normalized rule containers. It does not perform geometry,
    archetype, or physics reasoning.
    """
    errors: List[str] = []
    warnings: List[str] = []
    normalized_input = normalize_operator_program_v4_payload(program)

    try:
        parsed = OperatorProgramV4.model_validate(normalized_input)
    except ValidationError as exc:
        return {
            "is_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "program": None,
            "normalized_payload": None,
        }
    except Exception as exc:
        return {
            "is_valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "program": None,
            "normalized_payload": None,
        }

    object_catalog = _normalize_object_catalog(available_object_ids)
    if len(parsed.actions) > int(max_actions):
        errors.append(f"actions 数量超限: {len(parsed.actions)} > {int(max_actions)}")

    normalized_actions: List[OperatorActionV4] = []
    for index, action in enumerate(parsed.actions, start=1):
        action_errors: List[str] = []
        action_warnings: List[str] = []
        normalized_action = _validate_action_v4(
            action=action,
            object_catalog=object_catalog,
            errors=action_errors,
            warnings=action_warnings,
        )
        errors.extend(f"action[{index}] {item}" for item in action_errors)
        warnings.extend(f"action[{index}] {item}" for item in action_warnings)
        if normalized_action is not None:
            normalized_actions.append(normalized_action)

    if errors:
        return {
            "is_valid": False,
            "errors": errors,
            "warnings": warnings,
            "program": None,
            "normalized_payload": None,
        }

    normalized_program = parsed.model_copy(deep=True)
    normalized_program.actions = normalized_actions
    normalized_payload = build_operator_program_v4_payload(normalized_program)
    return {
        "is_valid": True,
        "errors": [],
        "warnings": warnings,
        "program": normalized_program,
        "normalized_payload": normalized_payload,
    }


def build_operator_program_v4_payload(program: OperatorProgramV4) -> Dict[str, Any]:
    """Return a stable normalized payload for downstream storage and transport."""
    return {
        "program_id": str(program.program_id or ""),
        "version": str(program.version or DSL_V4_VERSION),
        "rationale": str(program.rationale or ""),
        "actions": [
            {
                "action": str(action.action or ""),
                "targets": [
                    {
                        "object_type": str(binding.object_type or ""),
                        "object_id": str(binding.object_id or ""),
                        "role": str(binding.role or ""),
                        "attributes": dict(binding.attributes or {}),
                    }
                    for binding in list(action.targets or [])
                ],
                "params": dict(action.params or {}),
                "hard_rules": [
                    {
                        "rule_id": str(rule.rule_id or ""),
                        "weight": float(rule.weight or 0.0),
                        "note": str(rule.note or ""),
                        "metadata": dict(rule.metadata or {}),
                    }
                    for rule in list(action.hard_rules or [])
                ],
                "soft_preferences": [
                    {
                        "rule_id": str(rule.rule_id or ""),
                        "weight": float(rule.weight or 0.0),
                        "note": str(rule.note or ""),
                        "metadata": dict(rule.metadata or {}),
                    }
                    for rule in list(action.soft_preferences or [])
                ],
                "expected_effects": {
                    str(key): float(value)
                    for key, value in dict(action.expected_effects or {}).items()
                    if _safe_float(value) is not None
                },
                "note": str(action.note or ""),
                "metadata": dict(action.metadata or {}),
            }
            for action in list(program.actions or [])
        ],
        "metadata": dict(program.metadata or {}),
    }


def _validate_action_v4(
    *,
    action: OperatorActionV4,
    object_catalog: Dict[str, set[str]],
    errors: List[str],
    warnings: List[str],
) -> Optional[OperatorActionV4]:
    if action.action not in SUPPORTED_ACTIONS_V4:
        errors.append(f"未知 v4 action: {action.action}")
        return None

    if len(action.targets) > MAX_TARGETS_PER_ACTION:
        errors.append(
            f"targets 数量超限: {len(action.targets)} > {MAX_TARGETS_PER_ACTION}"
        )

    deduped_targets: List[TargetBindingV4] = []
    seen_targets: set[tuple[str, str, str]] = set()
    observed_types: set[str] = set()
    for binding in list(action.targets or []):
        object_type = str(binding.object_type or "").strip().lower()
        object_id = str(binding.object_id or "").strip()
        role = str(binding.role or "").strip().lower()
        if object_type not in SUPPORTED_TARGET_OBJECT_TYPES:
            errors.append(f"不支持的 target.object_type: {object_type}")
            continue
        target_key = (object_type, object_id, role)
        if target_key in seen_targets:
            warnings.append(f"重复 target 已折叠: {object_type}:{object_id}:{role}")
            continue
        if object_catalog.get(object_type) and object_id not in object_catalog[object_type]:
            errors.append(f"未知 {object_type}: {object_id}")
            continue
        seen_targets.add(target_key)
        observed_types.add(object_type)
        deduped_targets.append(
            binding.model_copy(
                update={
                    "object_type": object_type,
                    "object_id": object_id,
                    "role": role,
                    "attributes": dict(binding.attributes or {}),
                },
                deep=True,
            )
        )

    contract = ACTION_CONTRACTS_V4.get(str(action.action or ""))
    if contract is None:
        errors.append(f"缺少 v4 action 合同: {action.action}")
        return None
    for target_group in list(contract.required_target_groups or []):
        if not observed_types.intersection(set(target_group)):
            errors.append(
                "缺少必需 target 绑定: "
                + "/".join(sorted(str(item) for item in list(target_group)))
            )

    hard_rules = _normalize_rule_bindings(
        list(action.hard_rules or []),
        fallback=list(contract.default_hard_rules or []),
    )
    soft_preferences = _normalize_rule_bindings(
        list(action.soft_preferences or []),
        fallback=list(contract.default_soft_preferences or []),
    )

    normalized_effects: Dict[str, float] = {}
    for key, value in dict(action.expected_effects or {}).items():
        numeric = _safe_float(value)
        if numeric is None:
            warnings.append(f"expected_effects.{key} 非法，已忽略")
            continue
        normalized_effects[str(key).strip()] = float(numeric)

    return action.model_copy(
        update={
            "targets": deduped_targets,
            "hard_rules": hard_rules,
            "soft_preferences": soft_preferences,
            "expected_effects": normalized_effects,
            "note": str(action.note or ""),
            "metadata": dict(action.metadata or {}),
            "params": dict(action.params or {}),
        },
        deep=True,
    )


def _normalize_action_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    action_name = str(
        payload.get("action")
        or payload.get("operator")
        or payload.get("name")
        or ""
    ).strip().lower()
    if not action_name:
        return None

    params = payload.get("params", {})
    if not isinstance(params, dict):
        params = {}
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    expected_effects = payload.get("expected_effects", {})
    if not isinstance(expected_effects, dict):
        expected_effects = {}

    return {
        "action": action_name,
        "targets": _normalize_targets_payload(payload),
        "params": dict(params),
        "hard_rules": _normalize_rule_payload(
            payload.get("hard_rules"),
            fallback=payload.get("mandatory_rules"),
        ),
        "soft_preferences": _normalize_rule_payload(
            payload.get("soft_preferences"),
            fallback=payload.get("preferences"),
        ),
        "expected_effects": dict(expected_effects),
        "note": str(payload.get("note", "") or "").strip(),
        "metadata": dict(metadata),
    }


def _normalize_targets_payload(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_targets = payload.get("targets")
    if isinstance(raw_targets, dict):
        raw_targets = [raw_targets]
    elif not isinstance(raw_targets, list):
        raw_targets = []

    normalized_targets: List[Dict[str, Any]] = []
    for item in list(raw_targets or []):
        normalized = _normalize_target_payload(item)
        if normalized is not None:
            normalized_targets.append(normalized)

    inline_component_ids = _normalize_string_list(
        payload.get("component_ids") or payload.get("target_component_ids")
    )
    if inline_component_ids:
        inline_group_id = str(
            payload.get("component_group_id")
            or payload.get("group_id")
            or payload.get("subject_group_id")
            or f"{str(payload.get('action', '') or 'action').strip().lower()}_group"
        ).strip()
        normalized_targets.append(
            {
                "object_type": "component_group",
                "object_id": inline_group_id,
                "role": "subject",
                "attributes": {"component_ids": inline_component_ids},
            }
        )

    inline_component = str(payload.get("component_id") or "").strip()
    if inline_component:
        normalized_targets.append(
            {
                "object_type": "component",
                "object_id": inline_component,
                "role": "subject",
                "attributes": {},
            }
        )

    inline_aliases = {
        "panel_id": "panel",
        "panel": "panel",
        "aperture_id": "aperture",
        "aperture": "aperture",
        "zone_id": "zone",
        "zone": "zone",
        "mount_site_id": "mount_site",
        "mount_site": "mount_site",
    }
    for key, object_type in inline_aliases.items():
        value = payload.get(key)
        if value is None:
            continue
        object_id = str(value or "").strip()
        if not object_id:
            continue
        normalized_targets.append(
            {
                "object_type": object_type,
                "object_id": object_id,
                "role": "context",
                "attributes": {},
            }
        )

    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in normalized_targets:
        target_key = (
            str(item.get("object_type", "")).strip().lower(),
            str(item.get("object_id", "")).strip(),
            str(item.get("role", "")).strip().lower(),
        )
        if not target_key[0] or not target_key[1] or target_key in seen:
            continue
        seen.add(target_key)
        deduped.append(item)
    return deduped


def _normalize_target_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    object_type = str(
        payload.get("object_type")
        or payload.get("type")
        or payload.get("kind")
        or ""
    ).strip().lower()
    object_id = str(
        payload.get("object_id")
        or payload.get("id")
        or payload.get("object")
        or ""
    ).strip()
    if not object_type or not object_id:
        return None

    attributes = payload.get("attributes", {})
    if not isinstance(attributes, dict):
        attributes = {}
    extra_attrs = {
        str(key): value
        for key, value in payload.items()
        if key
        not in {"object_type", "type", "kind", "object_id", "id", "object", "role", "attributes"}
    }
    merged_attrs = dict(attributes)
    merged_attrs.update(extra_attrs)

    return {
        "object_type": object_type,
        "object_id": object_id,
        "role": str(payload.get("role", "") or payload.get("binding_role", "") or "").strip(),
        "attributes": merged_attrs,
    }


def _normalize_rule_payload(value: Any, *, fallback: Any = None) -> List[Dict[str, Any]]:
    raw = value if value not in (None, "") else fallback
    if raw is None:
        return []
    if isinstance(raw, str):
        raw_items: List[Any] = [item for item in raw.split(",") if str(item).strip()]
    elif isinstance(raw, dict):
        raw_items = [raw]
    elif isinstance(raw, Sequence):
        raw_items = list(raw)
    else:
        return []

    normalized: List[Dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, str):
            rule_id = str(item or "").strip().lower()
            if not rule_id:
                continue
            normalized.append({"rule_id": rule_id})
            continue
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or item.get("id") or "").strip().lower()
        if not rule_id:
            continue
        normalized.append(
            {
                "rule_id": rule_id,
                "weight": float(item.get("weight", 1.0) or 1.0),
                "note": str(item.get("note", "") or "").strip(),
                "metadata": dict(item.get("metadata", {}) or {}),
            }
        )
    return normalized


def _normalize_rule_bindings(
    bindings: Sequence[RuleBindingV4],
    *,
    fallback: Sequence[str],
) -> List[RuleBindingV4]:
    deduped: List[RuleBindingV4] = []
    seen: set[str] = set()
    candidates = list(bindings or [])
    if not candidates and fallback:
        candidates = [RuleBindingV4(rule_id=str(item)) for item in list(fallback or [])]
    for binding in candidates:
        rule_id = str(binding.rule_id or "").strip().lower()
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        deduped.append(
            binding.model_copy(
                update={
                    "rule_id": rule_id,
                    "weight": float(binding.weight or 0.0),
                    "note": str(binding.note or ""),
                    "metadata": dict(binding.metadata or {}),
                },
                deep=True,
            )
        )
    return deduped


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
            keys = [str(item).strip() for item in dict(payload).keys()]
        else:
            keys = [str(item).strip() for item in list(payload or [])]
        catalog[normalized_type] = {item for item in keys if item}
    return catalog


def _normalize_string_list(value: Any) -> List[str]:
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


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None
