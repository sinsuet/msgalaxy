"""
Validation utilities for MaaS modeling intent payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .metric_registry import (
    MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT,
    detect_covered_groups,
    get_metric_status,
    normalize_metric_key,
    parse_mandatory_groups,
)
from .protocol import ModelingConstraint, ModelingIntent


SUPPORTED_UNITS = {
    "",
    "mm",
    "m",
    "count",
    "kg",
    "W",
    "V",
    "Hz",
    "MPa",
    "deg",
    "rad",
    "C",
    "K",
    "%",
    "dimensionless",
}


def validate_modeling_intent(
    intent: ModelingIntent,
    runtime_constraints: Dict[str, Any] | None = None,
    *,
    mandatory_hard_constraint_groups: List[str] | Tuple[str, ...] | None = None,
    hard_constraint_coverage_mode: str = "off",
    metric_registry_mode: str = "warn",
) -> Dict[str, Any]:
    """
    Validate intent structure, unit consistency hints and hard-constraint coverage.
    """
    errors: List[str] = []
    warnings: List[str] = []

    runtime_constraints = runtime_constraints or {}
    coverage_mode = _normalize_mode(hard_constraint_coverage_mode)
    registry_mode = _normalize_mode(metric_registry_mode)
    mandatory_groups = parse_mandatory_groups(
        mandatory_hard_constraint_groups
        if mandatory_hard_constraint_groups is not None
        else MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT
    )

    if not intent.variables:
        errors.append("variables 不能为空")
    if not intent.objectives:
        errors.append("objectives 不能为空")
    if not intent.hard_constraints:
        errors.append("hard_constraints 不能为空")

    for var in intent.variables:
        if var.unit not in SUPPORTED_UNITS:
            warnings.append(f"变量 {var.name} 使用了未登记单位: {var.unit}")
        if var.variable_type != "binary":
            if var.lower_bound is None or var.upper_bound is None:
                errors.append(f"变量 {var.name} 缺少上下界")

    for obj in intent.objectives:
        if obj.direction not in {"minimize", "maximize"}:
            errors.append(f"目标 {obj.name} 的 direction 非法: {obj.direction}")
        if obj.weight <= 0:
            warnings.append(f"目标 {obj.name} 的权重<=0，可能导致优化器忽略该目标")
        metric_issue = _check_metric_registry(
            metric_key=obj.metric_key,
            mode=registry_mode,
            context=f"目标 {obj.name}",
        )
        if metric_issue:
            if registry_mode == "strict":
                errors.append(metric_issue)
            else:
                warnings.append(metric_issue)

    for cons in [*intent.hard_constraints, *intent.soft_constraints]:
        if cons.unit not in SUPPORTED_UNITS:
            warnings.append(f"约束 {cons.name} 使用了未登记单位: {cons.unit}")
        if cons.relation not in {"<=", ">=", "=="}:
            errors.append(f"约束 {cons.name} relation 非法: {cons.relation}")
        if not cons.metric_key.strip():
            errors.append(f"约束 {cons.name} metric_key 为空")
        metric_issue = _check_metric_registry(
            metric_key=cons.metric_key,
            mode=registry_mode,
            context=f"约束 {cons.name}",
        )
        if metric_issue:
            if registry_mode == "strict":
                errors.append(metric_issue)
            else:
                warnings.append(metric_issue)

    errors.extend(_check_constraint_conflicts(intent.hard_constraints))
    warnings.extend(_check_runtime_constraint_coverage(intent, runtime_constraints))
    missing_groups = _check_mandatory_hard_constraint_coverage(
        intent=intent,
        mandatory_groups=mandatory_groups,
    )
    if coverage_mode in {"warn", "strict"} and missing_groups:
        recommended = "/".join(mandatory_groups) if mandatory_groups else "mandatory groups"
        message = (
            "硬约束覆盖缺失: "
            + ", ".join(missing_groups)
            + f"（建议至少覆盖 {recommended}）"
        )
        if coverage_mode == "strict":
            errors.append(message)
        else:
            warnings.append(message)
    warnings.extend(_check_unit_semantics(intent))

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _check_constraint_conflicts(constraints: List[ModelingConstraint]) -> List[str]:
    """
    Check simple interval conflicts for same metric_key.
    """
    issues: List[str] = []
    bounds: Dict[str, Dict[str, float]] = {}

    for cons in constraints:
        slot = bounds.setdefault(cons.metric_key, {})
        if cons.relation == "<=":
            slot["ub"] = min(slot.get("ub", cons.target_value), cons.target_value)
        elif cons.relation == ">=":
            slot["lb"] = max(slot.get("lb", cons.target_value), cons.target_value)
        else:
            slot["eq"] = cons.target_value

    for metric_key, bnd in bounds.items():
        lb = bnd.get("lb")
        ub = bnd.get("ub")
        eq = bnd.get("eq")

        if lb is not None and ub is not None and lb > ub:
            issues.append(f"硬约束冲突: {metric_key} 下界 {lb} 大于上界 {ub}")

        if eq is not None:
            if lb is not None and eq < lb:
                issues.append(f"硬约束冲突: {metric_key} 等式值 {eq} 小于下界 {lb}")
            if ub is not None and eq > ub:
                issues.append(f"硬约束冲突: {metric_key} 等式值 {eq} 大于上界 {ub}")

    return issues


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in {"off", "warn", "strict"}:
        return normalized
    return "off"


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(default)


def _check_metric_registry(
    *,
    metric_key: str,
    mode: str,
    context: str,
) -> str:
    if mode == "off":
        return ""
    status = get_metric_status(metric_key)
    normalized = str(status.get("normalized", "") or "")
    if not bool(status.get("is_known", False)):
        return (
            f"{context} 使用未注册 metric_key: {metric_key} "
            f"(normalized={normalized})"
        )
    if not bool(status.get("is_implemented", False)):
        return (
            f"{context} 使用未实现 metric_key: {metric_key} "
            f"(normalized={normalized})"
        )
    return ""


def _check_runtime_constraint_coverage(
    intent: ModelingIntent,
    runtime_constraints: Dict[str, Any],
) -> List[str]:
    """
    Warn if runtime hard limits are not represented in hard_constraints.
    """
    warnings: List[str] = []
    hard_metric_keys = {
        normalize_metric_key(cons.metric_key).lower()
        for cons in intent.hard_constraints
        if str(cons.metric_key or "").strip()
    }

    expected_map: List[Tuple[str, Tuple[str, ...]]] = [
        ("max_temp_c", ("max_temp",)),
        ("min_clearance_mm", ("min_clearance",)),
        ("max_cg_offset_mm", ("cg_offset",)),
        ("min_safety_factor", ("safety_factor", "safety_factor_violation")),
        ("min_modal_freq_hz", ("first_modal_freq", "modal_freq_violation")),
        ("max_voltage_drop_v", ("voltage_drop", "voltage_drop_violation")),
        ("min_power_margin_pct", ("power_margin", "power_margin_violation")),
    ]

    for runtime_key, aliases in expected_map:
        if runtime_key not in runtime_constraints:
            continue
        if not any(alias in hard_metric_keys for alias in aliases):
            warnings.append(
                f"运行时硬约束 {runtime_key} 尚未映射到 hard_constraints"
            )

    enforce_power_budget = _as_bool(runtime_constraints.get("enforce_power_budget", False), default=False)
    if enforce_power_budget and ("max_power_w" in runtime_constraints):
        if not any(alias in hard_metric_keys for alias in {"peak_power", "peak_power_violation"}):
            warnings.append(
                "运行时硬约束 max_power_w 尚未映射到 hard_constraints (enforce_power_budget=true)"
            )

    return warnings


def _check_mandatory_hard_constraint_coverage(
    *,
    intent: ModelingIntent,
    mandatory_groups: Tuple[str, ...],
) -> List[str]:
    hard_metric_keys = [
        normalize_metric_key(cons.metric_key)
        for cons in intent.hard_constraints
        if str(cons.metric_key or "").strip()
    ]
    covered = detect_covered_groups(
        metric_keys=hard_metric_keys,
        mandatory_groups=mandatory_groups,
    )
    missing = [
        group
        for group in mandatory_groups
        if group not in covered
    ]
    return missing


def _check_unit_semantics(intent: ModelingIntent) -> List[str]:
    warnings: List[str] = []
    for cons in [*intent.hard_constraints, *intent.soft_constraints]:
        metric_key = cons.metric_key.lower()
        unit = cons.unit
        if "temp" in metric_key and unit not in {"", "C", "K"}:
            warnings.append(f"约束 {cons.name}: temperature 类指标建议单位 C 或 K")
        if ("clearance" in metric_key or "distance" in metric_key) and unit not in {"", "mm", "m"}:
            warnings.append(f"约束 {cons.name}: distance 类指标建议单位 mm 或 m")
        if ("mass" in metric_key or "moi" in metric_key) and unit not in {"", "kg", "dimensionless"}:
            warnings.append(f"约束 {cons.name}: mass/moi 类指标建议单位 kg 或 dimensionless")
    return warnings
