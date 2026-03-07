"""
Shared simulation/runtime contracts for multiphysics metrics and constraints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

if TYPE_CHECKING:
    from optimization.protocol import GeometryMetrics, PowerMetrics, StructuralMetrics, ThermalMetrics, ViolationItem

# Metric source labels
THERMAL_SOURCE_ONLINE_COMSOL = "online_comsol"
THERMAL_SOURCE_PENALTY = "penalty_temp"
THERMAL_SOURCE_PROXY = "proxy"

STRUCTURAL_SOURCE_ONLINE_COMSOL = "online_comsol_structural"
STRUCTURAL_SOURCE_ONLINE_COMSOL_PARTIAL = "online_comsol_structural_partial"
STRUCTURAL_SOURCE_PROXY = "proxy"

POWER_SOURCE_NETWORK_SOLVER = "network_dc_solver"
POWER_SOURCE_NETWORK_SOLVER_PARTIAL = "network_dc_solver_partial"
POWER_SOURCE_ONLINE_COMSOL = "online_comsol_power"
POWER_SOURCE_ONLINE_COMSOL_PARTIAL = "online_comsol_power_partial"
POWER_SOURCE_PROXY = "proxy"

MISSION_SOURCE_KEEP_OUT_ALIAS = "keepout_alias_proxy"
MISSION_SOURCE_FOV_PROXY = "mission_fov_proxy"
MISSION_SOURCE_FOV_REAL = "mission_fov_real"
MISSION_SOURCE_UNAVAILABLE = "mission_unavailable"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(numeric):
        return float(default)
    return float(numeric)


def to_bool(value: Any, *, default: bool = False) -> bool:
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


def normalize_runtime_constraints(raw_constraints: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = dict(raw_constraints or {})
    return {
        "max_temp_c": _to_float(raw.get("max_temp_c", 60.0), 60.0),
        "min_clearance_mm": _to_float(raw.get("min_clearance_mm", 3.0), 3.0),
        "max_cg_offset_mm": _to_float(raw.get("max_cg_offset_mm", 20.0), 20.0),
        "min_safety_factor": _to_float(raw.get("min_safety_factor", 2.0), 2.0),
        "min_modal_freq_hz": _to_float(raw.get("min_modal_freq_hz", 55.0), 55.0),
        "max_voltage_drop_v": _to_float(raw.get("max_voltage_drop_v", 0.5), 0.5),
        "min_power_margin_pct": _to_float(raw.get("min_power_margin_pct", 10.0), 10.0),
        "max_power_w": _to_float(raw.get("max_power_w", 500.0), 500.0),
        "max_mass_kg": _to_float(raw.get("max_mass_kg", 0.0), 0.0),
        "bus_voltage_v": _to_float(raw.get("bus_voltage_v", 28.0), 28.0),
        "enforce_power_budget": to_bool(raw.get("enforce_power_budget", False), default=False),
    }


def _append_record(
    records: List[Dict[str, Any]],
    *,
    code: str,
    violation_type: str,
    severity: str,
    description: str,
    metric_value: float,
    threshold: float,
) -> None:
    records.append(
        {
            "code": str(code),
            "violation_type": str(violation_type),
            "severity": str(severity),
            "description": str(description),
            "metric_value": float(metric_value),
            "threshold": float(threshold),
            "affected_components": [],
        }
    )


def evaluate_constraint_records(
    *,
    scalar_metrics: Mapping[str, Any],
    runtime_constraints: Optional[Mapping[str, Any]] = None,
    enforce_power_budget: Optional[bool] = None,
    power_budget_metric: str = "peak_power",
    include_runtime_multiphysics_rules: bool = True,
    include_mass_rule: bool = False,
) -> List[Dict[str, Any]]:
    """
    Evaluate scalar metrics against shared constraints and return normalized records.
    """
    metrics = dict(scalar_metrics or {})
    available = set(metrics.keys())
    limits = normalize_runtime_constraints(runtime_constraints)
    if enforce_power_budget is None:
        enforce_budget = bool(limits.get("enforce_power_budget", False))
    else:
        enforce_budget = bool(enforce_power_budget)

    records: List[Dict[str, Any]] = []

    min_clearance = _to_float(metrics.get("min_clearance", np.inf), np.inf)
    min_clearance_limit = _to_float(limits.get("min_clearance_mm", 3.0), 3.0)
    if "min_clearance" in available and min_clearance < min_clearance_limit:
        _append_record(
            records,
            code="clearance",
            violation_type="geometry",
            severity="major",
            description="最小间隙不足",
            metric_value=min_clearance,
            threshold=min_clearance_limit,
        )

    num_collisions = _to_float(metrics.get("num_collisions", 0.0), 0.0)
    if include_runtime_multiphysics_rules and "num_collisions" in available and num_collisions > 0:
        _append_record(
            records,
            code="collision",
            violation_type="geometry",
            severity="critical",
            description="存在组件几何重叠",
            metric_value=num_collisions,
            threshold=0.0,
        )

    cg_offset = _to_float(metrics.get("cg_offset", 0.0), 0.0)
    max_cg_offset = _to_float(limits.get("max_cg_offset_mm", 20.0), 20.0)
    if include_runtime_multiphysics_rules and "cg_offset" in available and cg_offset > max_cg_offset:
        _append_record(
            records,
            code="cg_limit",
            violation_type="geometry",
            severity="major",
            description="质心偏移过大，影响姿态控制",
            metric_value=cg_offset,
            threshold=max_cg_offset,
        )

    max_temp = _to_float(metrics.get("max_temp", 0.0), 0.0)
    max_temp_limit = _to_float(limits.get("max_temp_c", 60.0), 60.0)
    if "max_temp" in available and max_temp > max_temp_limit:
        _append_record(
            records,
            code="thermal",
            violation_type="thermal",
            severity="critical",
            description="温度超标",
            metric_value=max_temp,
            threshold=max_temp_limit,
        )

    if include_runtime_multiphysics_rules:
        min_safety_factor = _to_float(limits.get("min_safety_factor", 2.0), 2.0)
        safety_factor = _to_float(metrics.get("safety_factor", np.inf), np.inf)
        if "safety_factor" in available and safety_factor < min_safety_factor:
            _append_record(
                records,
                code="struct_safety",
                violation_type="structural",
                severity="critical",
                description="安全系数不足",
                metric_value=safety_factor,
                threshold=min_safety_factor,
            )

        min_modal = _to_float(limits.get("min_modal_freq_hz", 55.0), 55.0)
        modal = _to_float(metrics.get("first_modal_freq", np.inf), np.inf)
        if "first_modal_freq" in available and modal < min_modal:
            _append_record(
                records,
                code="struct_modal",
                violation_type="structural",
                severity="major",
                description="一阶模态频率不足",
                metric_value=modal,
                threshold=min_modal,
            )

        max_vdrop = _to_float(limits.get("max_voltage_drop_v", 0.5), 0.5)
        vdrop = _to_float(metrics.get("voltage_drop", 0.0), 0.0)
        if "voltage_drop" in available and vdrop > max_vdrop:
            _append_record(
                records,
                code="power_vdrop",
                violation_type="power",
                severity="major",
                description="供电母线压降超限",
                metric_value=vdrop,
                threshold=max_vdrop,
            )

        min_margin = _to_float(limits.get("min_power_margin_pct", 10.0), 10.0)
        margin = _to_float(metrics.get("power_margin", np.inf), np.inf)
        if "power_margin" in available and margin < min_margin:
            _append_record(
                records,
                code="power_margin",
                violation_type="power",
                severity="major",
                description="功率裕度不足",
                metric_value=margin,
                threshold=min_margin,
            )

        mission_keepout = _to_float(metrics.get("mission_keepout_violation", 0.0), 0.0)
        if "mission_keepout_violation" in available and mission_keepout > 0.0:
            _append_record(
                records,
                code="mission_keepout",
                violation_type="mission",
                severity="critical",
                description="任务视场/禁区约束违反",
                metric_value=mission_keepout,
                threshold=0.0,
            )

    max_power = _to_float(limits.get("max_power_w", 500.0), 500.0)
    budget_metric_name = str(power_budget_metric or "peak_power")
    budget_metric_value = _to_float(metrics.get(budget_metric_name, 0.0), 0.0)
    if (
        enforce_budget
        and budget_metric_name in available
        and budget_metric_value > max_power
    ):
        _append_record(
            records,
            code="power_peak" if budget_metric_name == "peak_power" else "power_budget",
            violation_type="power",
            severity="critical",
            description="峰值功耗超出电源预算"
            if budget_metric_name == "peak_power"
            else "功率预算超限",
            metric_value=budget_metric_value,
            threshold=max_power,
        )

    if include_mass_rule:
        mass_limit = _to_float(limits.get("max_mass_kg", 0.0), 0.0)
        total_mass = _to_float(metrics.get("total_mass", 0.0), 0.0)
        if mass_limit > 0.0 and "total_mass" in available and total_mass > mass_limit:
            _append_record(
                records,
                code="mass_limit",
                violation_type="mass",
                severity="major",
                description="总质量超出限制",
                metric_value=total_mass,
                threshold=mass_limit,
            )

    return records


def build_runtime_violations(
    *,
    geometry_metrics: "GeometryMetrics",
    thermal_metrics: "ThermalMetrics",
    structural_metrics: "StructuralMetrics",
    power_metrics: "PowerMetrics",
    mission_metrics: Optional[Mapping[str, Any]] = None,
    runtime_constraints: Optional[Mapping[str, Any]] = None,
) -> List["ViolationItem"]:
    from optimization.protocol import ViolationItem
    scalar_metrics = {
        "min_clearance": float(geometry_metrics.min_clearance),
        "num_collisions": float(geometry_metrics.num_collisions),
        "cg_offset": float(geometry_metrics.cg_offset_magnitude),
        "max_temp": float(thermal_metrics.max_temp),
        "safety_factor": float(structural_metrics.safety_factor),
        "first_modal_freq": float(structural_metrics.first_modal_freq),
        "voltage_drop": float(power_metrics.voltage_drop),
        "power_margin": float(power_metrics.power_margin),
        "peak_power": float(power_metrics.peak_power),
    }
    mission_payload = dict(mission_metrics or {})
    if "mission_keepout_violation" in mission_payload:
        scalar_metrics["mission_keepout_violation"] = float(
            mission_payload.get("mission_keepout_violation", 0.0) or 0.0
        )
    records = evaluate_constraint_records(
        scalar_metrics=scalar_metrics,
        runtime_constraints=runtime_constraints,
        enforce_power_budget=None,
        power_budget_metric="peak_power",
        include_runtime_multiphysics_rules=True,
        include_mass_rule=False,
    )
    violations: List[ViolationItem] = []
    for idx, item in enumerate(records):
        violations.append(
            ViolationItem(
                violation_id=f"V_{str(item.get('code', 'GEN')).upper()}_{idx}",
                violation_type=str(item.get("violation_type", "geometry")),
                severity=str(item.get("severity", "major")),
                description=str(item.get("description", "")),
                affected_components=list(item.get("affected_components", [])),
                metric_value=float(item.get("metric_value", 0.0)),
                threshold=float(item.get("threshold", 0.0)),
            )
        )
    return violations


def build_simulation_constraint_rows(
    *,
    scalar_metrics: Mapping[str, Any],
    runtime_constraints: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Build legacy SimulationDriver `check_constraints` payload rows.
    """
    constraints = dict(runtime_constraints or {})
    records = evaluate_constraint_records(
        scalar_metrics=scalar_metrics,
        runtime_constraints=constraints,
        enforce_power_budget=("max_power_w" in constraints),
        power_budget_metric="total_power",
        include_runtime_multiphysics_rules=False,
        include_mass_rule=True,
    )

    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(records):
        code = str(item.get("code", "")).strip().lower()
        metric_value = float(item.get("metric_value", 0.0))
        threshold = float(item.get("threshold", 0.0))
        severity = 0.0
        if threshold > 1e-9:
            if code in {"clearance"}:
                severity = min(1.0, max(threshold - metric_value, 0.0) / max(threshold, 1e-9))
            else:
                severity = min(1.0, max(metric_value - threshold, 0.0) / max(threshold, 1e-9))

        if code == "thermal":
            row_type = "THERMAL_OVERHEAT"
            row_id = f"TEMP_VIOLATION_{idx}"
            description = f"Temperature {metric_value:.1f}°C > {threshold:.1f}°C"
        elif code == "clearance":
            row_type = "GEOMETRY_CLASH"
            row_id = f"CLEARANCE_VIOLATION_{idx}"
            description = f"Clearance {metric_value:.1f}mm < {threshold:.1f}mm"
        elif code == "mass_limit":
            row_type = "MASS_LIMIT"
            row_id = f"MASS_VIOLATION_{idx}"
            description = f"Mass {metric_value:.1f}kg > {threshold:.1f}kg"
        else:
            row_type = "POWER_LIMIT"
            row_id = f"POWER_VIOLATION_{idx}"
            description = f"Power {metric_value:.1f}W > {threshold:.1f}W"

        rows.append(
            {
                "id": row_id,
                "type": row_type,
                "description": description,
                "involved_components": ["system"],
                "severity": float(max(0.0, min(severity, 1.0))),
            }
        )
    return rows


def merge_metric_sources(
    *,
    simulation_values: Mapping[str, Optional[float]],
    proxy_values: Mapping[str, float],
    metric_keys: Sequence[str],
    simulation_source_label: str,
    proxy_source_label: str = "proxy",
) -> Tuple[Dict[str, float], Dict[str, str], str]:
    """
    Merge simulation/proxy metrics and compute per-metric + aggregate source labels.
    """
    merged: Dict[str, float] = {}
    per_metric_sources: Dict[str, str] = {}
    sim_source = str(simulation_source_label or "simulation_result")
    proxy_source = str(proxy_source_label or "proxy")

    for key in list(metric_keys or []):
        raw = simulation_values.get(key, None)
        parsed = None
        if raw is not None:
            try:
                numeric = float(raw)
                if np.isfinite(numeric):
                    parsed = float(numeric)
            except Exception:
                parsed = None

        if parsed is not None:
            merged[str(key)] = float(parsed)
            per_metric_sources[str(key)] = sim_source
        else:
            merged[str(key)] = float(proxy_values.get(key, 0.0))
            per_metric_sources[str(key)] = proxy_source

    source_flags = set(per_metric_sources.values())
    if source_flags == {proxy_source}:
        aggregate = proxy_source
    elif proxy_source in source_flags:
        aggregate = "mixed"
    else:
        aggregate = sim_source
    return merged, per_metric_sources, aggregate
