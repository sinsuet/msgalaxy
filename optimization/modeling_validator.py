"""
Validation utilities for MaaS modeling intent payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .protocol import ModelingConstraint, ModelingIntent


SUPPORTED_UNITS = {
    "",
    "mm",
    "m",
    "count",
    "kg",
    "W",
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
) -> Dict[str, Any]:
    """
    Validate intent structure, unit consistency hints and hard-constraint coverage.
    """
    errors: List[str] = []
    warnings: List[str] = []

    runtime_constraints = runtime_constraints or {}

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

    for cons in [*intent.hard_constraints, *intent.soft_constraints]:
        if cons.unit not in SUPPORTED_UNITS:
            warnings.append(f"约束 {cons.name} 使用了未登记单位: {cons.unit}")
        if cons.relation not in {"<=", ">=", "=="}:
            errors.append(f"约束 {cons.name} relation 非法: {cons.relation}")
        if not cons.metric_key.strip():
            errors.append(f"约束 {cons.name} metric_key 为空")

    errors.extend(_check_constraint_conflicts(intent.hard_constraints))
    warnings.extend(_check_runtime_constraint_coverage(intent, runtime_constraints))
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


def _check_runtime_constraint_coverage(
    intent: ModelingIntent,
    runtime_constraints: Dict[str, Any],
) -> List[str]:
    """
    Warn if runtime hard limits are not represented in hard_constraints.
    """
    warnings: List[str] = []
    hard_metric_keys = {cons.metric_key.lower() for cons in intent.hard_constraints}

    expected_map: List[Tuple[str, Tuple[str, ...]]] = [
        ("max_temp_c", ("max_temp", "temperature", "temp")),
        ("min_clearance_mm", ("min_clearance", "clearance", "distance")),
        ("max_cg_offset_mm", ("cg_offset", "cg_offset_norm", "cg_norm", "centroid", "com_offset")),
    ]

    for runtime_key, aliases in expected_map:
        if runtime_key not in runtime_constraints:
            continue
        if not any(any(alias in metric for alias in aliases) for metric in hard_metric_keys):
            warnings.append(
                f"运行时硬约束 {runtime_key} 尚未映射到 hard_constraints"
            )

    return warnings


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
