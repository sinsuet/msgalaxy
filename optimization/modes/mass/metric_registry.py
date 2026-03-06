"""
Metric registry for MaaS compile/validation consistency.

The registry tracks:
- alias normalization,
- whether a metric key is known and implemented in executable evaluators,
- mandatory hard-constraint coverage groups for feasibility-first runs.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple


_METRIC_ALIASES: Dict[str, str] = {
    "temperature": "max_temp",
    "temp": "max_temp",
    "max_temperature": "max_temp",
    "peak_temp": "max_temp",
    "min_distance": "min_clearance",
    "distance": "min_clearance",
    "clearance": "min_clearance",
    "collision": "num_collisions",
    "collision_count": "num_collisions",
    "num_collision": "num_collisions",
    "centroid": "cg_offset",
    "com_offset": "cg_offset",
    "center_of_mass": "cg_offset",
    "cg_offset_norm": "cg_offset",
    "cg_norm": "cg_offset",
    "cg_offset_magnitude": "cg_offset",
    "boundary": "boundary_violation",
    "boundary_overflow": "boundary_violation",
    "modal_freq": "first_modal_freq",
    "modal_freq_1st": "first_modal_freq",
    "modal_freq_1": "first_modal_freq",
    "first_modal_frequency": "first_modal_freq",
    "sf": "safety_factor",
    "voltage_drop_v": "voltage_drop",
    "power_budget_margin": "power_margin",
    "power_margin_pct": "power_margin",
    "total_power_w": "total_power",
    "mission_keepout": "mission_keepout_violation",
    "mission_keepout_cv": "mission_keepout_violation",
    "fov_keepout": "mission_keepout_violation",
}


_METRIC_IMPL_STATUS: Dict[str, bool] = {
    # Implemented in current problem generator / evaluator paths.
    "cg_offset": True,
    "max_temp": True,
    "moi_imbalance": True,
    "min_clearance": True,
    "num_collisions": True,
    "boundary_violation": True,
    "collision_violation": True,
    "clearance_violation": True,
    "thermal_violation": True,
    "cg_violation": True,
    "total_power": True,
    # M2 executable multiphysics proxy metrics.
    "max_stress": True,
    "max_displacement": True,
    "first_modal_freq": True,
    "safety_factor": True,
    "peak_power": True,
    "power_margin": True,
    "voltage_drop": True,
    "safety_factor_violation": True,
    "modal_freq_violation": True,
    "voltage_drop_violation": True,
    "power_margin_violation": True,
    "peak_power_violation": True,
    # Mission keepout proxy interface (alias path for current runtime).
    "mission_keepout_violation": True,
    # Planned v3+ metrics (not fully implemented in executable evaluator yet).
    "soc_eclipse_end": False,
    "emc_separation": False,
    "fov_occlusion": False,
}


MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT: Tuple[str, ...] = (
    "collision",
    "clearance",
    "boundary",
    "thermal",
    "cg_limit",
)


_MANDATORY_GROUP_KEYS: Dict[str, Tuple[str, ...]] = {
    "collision": ("collision_violation", "num_collisions"),
    "clearance": ("clearance_violation", "min_clearance"),
    "boundary": ("boundary_violation",),
    "thermal": ("thermal_violation", "max_temp"),
    "cg_limit": ("cg_violation", "cg_offset"),
    "struct_safety": ("safety_factor_violation", "safety_factor"),
    "struct_modal": ("modal_freq_violation", "first_modal_freq"),
    "power_vdrop": ("voltage_drop_violation", "voltage_drop"),
    "power_margin": ("power_margin_violation", "power_margin"),
    "power_peak": ("peak_power_violation", "peak_power"),
    "mission_keepout": ("mission_keepout_violation", "boundary_violation"),
}


def normalize_metric_key(metric_key: str) -> str:
    key = str(metric_key or "").strip()
    lowered = key.lower()
    return _METRIC_ALIASES.get(lowered, key)


def get_metric_status(metric_key: str) -> Dict[str, object]:
    normalized = normalize_metric_key(metric_key)
    is_known = normalized in _METRIC_IMPL_STATUS
    is_implemented = bool(_METRIC_IMPL_STATUS.get(normalized, False))
    return {
        "input": str(metric_key or ""),
        "normalized": normalized,
        "is_known": bool(is_known),
        "is_implemented": bool(is_implemented),
    }


def metric_covers_group(metric_key: str, group: str) -> bool:
    normalized = normalize_metric_key(metric_key)
    expected = _MANDATORY_GROUP_KEYS.get(str(group).strip().lower(), ())
    return normalized in expected


def detect_covered_groups(
    metric_keys: Iterable[str],
    mandatory_groups: Iterable[str] | None = None,
) -> set[str]:
    groups = {
        str(item).strip().lower()
        for item in (
            mandatory_groups
            if mandatory_groups is not None
            else MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT
        )
        if str(item).strip()
    }
    covered: set[str] = set()
    for raw_key in metric_keys:
        for group in groups:
            if metric_covers_group(raw_key, group):
                covered.add(group)
    return covered


def parse_mandatory_groups(raw_groups: object) -> Tuple[str, ...]:
    if isinstance(raw_groups, str):
        tokens = [item.strip().lower() for item in raw_groups.split(",")]
    elif isinstance(raw_groups, (list, tuple, set)):
        tokens = [str(item).strip().lower() for item in list(raw_groups)]
    else:
        tokens = []

    if not tokens:
        return MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT

    deduped = []
    seen = set()
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)
