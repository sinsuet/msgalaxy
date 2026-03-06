"""
Operator-to-physics implementation matrix for OP-MaaS runtime diagnostics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple


_OPERATOR_MATRIX: Dict[str, Dict[str, Any]] = {
    "group_move": {
        "family": "geometry",
        "implementation": "real",
        "physics_path": "layout_geometry_update",
        "boundary_note": "几何位姿直接变更，进入真实几何约束判定链路",
        "real_requirements": (),
    },
    "cg_recenter": {
        "family": "geometry",
        "implementation": "real",
        "physics_path": "layout_geometry_update",
        "boundary_note": "质心再平衡通过布局变更直接生效",
        "real_requirements": (),
    },
    "swap": {
        "family": "geometry",
        "implementation": "real",
        "physics_path": "layout_geometry_update",
        "boundary_note": "组件交换直接改变真实布局状态",
        "real_requirements": (),
    },
    "hot_spread": {
        "family": "thermal",
        "implementation": "conditional_real",
        "physics_path": "layout_update + thermal_real_evaluator",
        "boundary_note": "动作通过布局调整生效，需真实热评估器校核",
        "real_requirements": ("thermal_real",),
    },
    "add_heatstrap": {
        "family": "thermal",
        "implementation": "conditional_real",
        "physics_path": "thermal_contacts + online_comsol",
        "boundary_note": "热接触参数可执行，需真实热求解链路",
        "real_requirements": ("thermal_real",),
    },
    "set_thermal_contact": {
        "family": "thermal",
        "implementation": "conditional_real",
        "physics_path": "thermal_contacts + online_comsol",
        "boundary_note": "接触热导参数可执行，需真实热求解链路",
        "real_requirements": ("thermal_real",),
    },
    "add_bracket": {
        "family": "structural",
        "implementation": "conditional_real",
        "physics_path": "step_geometry_bracket + comsol_structural",
        "boundary_note": "支架几何进入 STEP/COMSOL，需结构真实来源闭环",
        "real_requirements": ("structural_real",),
    },
    "stiffener_insert": {
        "family": "structural",
        "implementation": "conditional_real",
        "physics_path": "step_geometry_stiffener + comsol_structural",
        "boundary_note": "加强件几何进入 STEP/COMSOL，需结构真实来源闭环",
        "real_requirements": ("structural_real",),
    },
    "bus_proximity_opt": {
        "family": "power",
        "implementation": "conditional_real",
        "physics_path": "layout_routing + dc_network_solver",
        "boundary_note": "动作改变布线拓扑，需真实电源网络求解来源",
        "real_requirements": ("power_real",),
    },
    "fov_keepout_push": {
        "family": "mission",
        "implementation": "conditional_real",
        "physics_path": "mission_fov_emc_evaluator",
        "boundary_note": "需外部真实 FOV/EMC evaluator，不可回落 keepout 代理",
        "real_requirements": ("mission_real",),
    },
}


DEFAULT_REQUIRED_FAMILIES: Tuple[str, ...] = (
    "geometry",
    "thermal",
    "structural",
    "power",
    "mission",
)


def normalize_action_name(action: Any) -> str:
    return str(action or "").strip().lower()


def action_family(action: Any) -> str:
    action_name = normalize_action_name(action)
    return str(_OPERATOR_MATRIX.get(action_name, {}).get("family", ""))


def operator_matrix_entry(action: Any) -> Dict[str, Any]:
    action_name = normalize_action_name(action)
    base = dict(_OPERATOR_MATRIX.get(action_name, {}))
    if not base:
        return {
            "action": action_name,
            "family": "",
            "implementation": "unknown",
            "physics_path": "unknown",
            "boundary_note": "未登记动作",
            "real_requirements": (),
        }
    base["action"] = action_name
    if "real_requirements" not in base:
        base["real_requirements"] = ()
    return base


def parse_required_families(raw: Any) -> Tuple[str, ...]:
    if isinstance(raw, str):
        tokens = [item.strip().lower() for item in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        tokens = [str(item).strip().lower() for item in list(raw)]
    else:
        tokens = []
    if not tokens:
        return DEFAULT_REQUIRED_FAMILIES
    seen = set()
    deduped: List[str] = []
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def build_operator_implementation_report(actions: Sequence[Any]) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    implementation_breakdown: Dict[str, int] = {}
    family_breakdown: Dict[str, int] = {}
    unknown_actions: List[str] = []

    for raw in list(actions or []):
        entry = operator_matrix_entry(raw)
        action_name = str(entry.get("action", ""))
        impl = str(entry.get("implementation", "unknown"))
        family = str(entry.get("family", ""))
        entries.append(entry)
        implementation_breakdown[impl] = int(implementation_breakdown.get(impl, 0)) + 1
        if family:
            family_breakdown[family] = int(family_breakdown.get(family, 0)) + 1
        else:
            unknown_actions.append(action_name)

    return {
        "entries": entries,
        "implementation_breakdown": implementation_breakdown,
        "family_breakdown": family_breakdown,
        "unknown_actions": unknown_actions,
    }


def evaluate_operator_family_coverage(
    *,
    actions: Sequence[Any],
    required_families: Any = None,
) -> Dict[str, Any]:
    required = parse_required_families(required_families)
    report = build_operator_implementation_report(actions)
    covered = sorted(set(report.get("family_breakdown", {}).keys()))
    missing = [family for family in required if family not in covered]
    return {
        "required_families": list(required),
        "covered_families": list(covered),
        "missing_families": list(missing),
        "passed": len(missing) == 0,
        "family_breakdown": dict(report.get("family_breakdown", {}) or {}),
        "implementation_breakdown": dict(report.get("implementation_breakdown", {}) or {}),
        "unknown_actions": list(report.get("unknown_actions", []) or []),
    }


def _normalize_realization_context(raw: Mapping[str, Any]) -> Dict[str, bool]:
    return {
        str(key).strip().lower(): bool(value)
        for key, value in dict(raw or {}).items()
        if str(key).strip()
    }


def evaluate_operator_realization(
    *,
    actions: Sequence[Any],
    realization_context: Mapping[str, Any],
    required_families: Any = None,
) -> Dict[str, Any]:
    required = parse_required_families(required_families)
    required_set = set(required)
    context = _normalize_realization_context(realization_context)

    action_reports: List[Dict[str, Any]] = []
    realized_family_breakdown: Dict[str, int] = {}
    non_real_family_breakdown: Dict[str, int] = {}
    non_real_actions: List[Dict[str, Any]] = []
    unknown_actions: List[str] = []

    for raw in list(actions or []):
        entry = operator_matrix_entry(raw)
        action_name = str(entry.get("action", ""))
        family = str(entry.get("family", ""))
        impl = str(entry.get("implementation", "unknown")).strip().lower()
        requirements = [
            str(item).strip().lower()
            for item in list(entry.get("real_requirements", ()) or ())
            if str(item).strip()
        ]
        missing_requirements = [item for item in requirements if not bool(context.get(item, False))]
        realized = False
        if impl == "real":
            realized = True
        elif impl in {"conditional_real", "conditional", "hybrid"}:
            realized = len(missing_requirements) == 0
        else:
            realized = False

        report_entry = {
            "action": action_name,
            "family": family,
            "implementation": impl,
            "physics_path": str(entry.get("physics_path", "")),
            "boundary_note": str(entry.get("boundary_note", "")),
            "real_requirements": list(requirements),
            "missing_requirements": list(missing_requirements),
            "realized": bool(realized),
        }
        action_reports.append(report_entry)

        if not family:
            unknown_actions.append(action_name)
        if realized:
            if family:
                realized_family_breakdown[family] = int(realized_family_breakdown.get(family, 0)) + 1
        else:
            if family:
                non_real_family_breakdown[family] = int(non_real_family_breakdown.get(family, 0)) + 1
            non_real_actions.append(report_entry)

    realized_families = sorted(
        family for family in realized_family_breakdown.keys() if family in required_set
    )
    missing_realized_families = [
        family for family in required if family not in set(realized_families)
    ]
    non_real_required_actions = [
        item
        for item in non_real_actions
        if str(item.get("family", "")) in required_set or not str(item.get("family", ""))
    ]
    passed = len(missing_realized_families) == 0 and len(non_real_required_actions) == 0

    non_real_actions_by_family: Dict[str, List[str]] = {}
    for item in non_real_actions:
        family = str(item.get("family", "") or "unknown")
        non_real_actions_by_family.setdefault(family, [])
        non_real_actions_by_family[family].append(str(item.get("action", "")))

    return {
        "required_families": list(required),
        "realized_families": list(realized_families),
        "missing_realized_families": list(missing_realized_families),
        "realized_family_breakdown": dict(realized_family_breakdown),
        "non_real_family_breakdown": dict(non_real_family_breakdown),
        "non_real_actions": list(non_real_actions),
        "non_real_actions_by_family": {
            key: sorted(set(values))
            for key, values in non_real_actions_by_family.items()
        },
        "unknown_actions": sorted(set(unknown_actions)),
        "context": dict(context),
        "action_reports": list(action_reports),
        "passed": bool(passed),
    }

