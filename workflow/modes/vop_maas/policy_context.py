"""
Structured VOPG builders for vop_maas.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from optimization.protocol import GlobalContextPack, ViolationItem

from .contracts import VOPGEdge, VOPGNode, VOPGraph


_SEVERITY_WEIGHT = {
    "critical": 3.0,
    "major": 2.0,
    "minor": 1.0,
}


def _dominant_violation_family(violations: Iterable[ViolationItem]) -> str:
    weighted: Dict[str, float] = {}
    for item in list(violations or []):
        family = str(getattr(item, "violation_type", "") or "").strip().lower()
        severity = str(getattr(item, "severity", "minor") or "minor").strip().lower()
        weight = float(_SEVERITY_WEIGHT.get(severity, 1.0))
        if family:
            weighted[family] = float(weighted.get(family, 0.0)) + weight
        desc = str(getattr(item, "description", "") or "").strip().lower()
        if "cg" in desc or "centroid" in desc or "center of mass" in desc:
            weighted["cg"] = float(weighted.get("cg", 0.0)) + weight * 1.25
    if not weighted:
        return ""
    return max(weighted.items(), key=lambda item: item[1])[0]


def _dominant_metric(violations: Iterable[ViolationItem]) -> str:
    scores: Dict[str, float] = {}
    for item in list(violations or []):
        desc = str(getattr(item, "description", "") or "").strip().lower()
        severity = str(getattr(item, "severity", "minor") or "minor").strip().lower()
        weight = float(_SEVERITY_WEIGHT.get(severity, 1.0))
        if "cg" in desc:
            scores["cg_offset"] = float(scores.get("cg_offset", 0.0)) + weight
        if "clearance" in desc or "collision" in desc:
            scores["min_clearance"] = float(scores.get("min_clearance", 0.0)) + weight
        if "temp" in desc:
            scores["max_temp"] = float(scores.get("max_temp", 0.0)) + weight
        if "stress" in desc:
            scores["max_stress"] = float(scores.get("max_stress", 0.0)) + weight
        if "voltage" in desc:
            scores["voltage_drop"] = float(scores.get("voltage_drop", 0.0)) + weight
        if "mission" in desc or "keepout" in desc or "fov" in desc:
            scores["mission_keepout_violation"] = float(
                scores.get("mission_keepout_violation", 0.0)
            ) + weight
    if not scores:
        return ""
    return max(scores.items(), key=lambda item: item[1])[0]


def _normalize_axis_and_sign(value: Any) -> Tuple[Optional[str], float]:
    text = str(value or "").strip().lower()
    if not text:
        return None, 1.0
    axis = next((token for token in ("x", "y", "z") if token in text), None)
    if axis is None:
        return None, 1.0
    sign = -1.0 if any(token in text for token in ("-", "negative", "minus")) else 1.0
    return axis, sign


def _face_from_axis(axis: str, sign: float) -> str:
    return f"{'-' if float(sign) < 0.0 else '+'}{str(axis).strip().lower()}"


def _collect_affected_components(violations: Iterable[ViolationItem]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for violation in list(violations or []):
        for component_id in list(getattr(violation, "affected_components", []) or []):
            normalized = str(component_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _collect_hot_components(
    *,
    context: GlobalContextPack,
    violations: Iterable[ViolationItem],
) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for component_id in list(getattr(context.thermal_metrics, "hotspot_components", []) or []):
        normalized = str(component_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    for violation in list(violations or []):
        if str(getattr(violation, "violation_type", "") or "").strip().lower() != "thermal":
            continue
        for component_id in list(getattr(violation, "affected_components", []) or []):
            normalized = str(component_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _build_binding_catalog_hint(
    *,
    context: GlobalContextPack,
    runtime_constraints: Dict[str, Any],
    component_ids: Iterable[str],
    dominant_family: str,
    dominant_metric: str,
) -> Dict[str, Any]:
    hint: Dict[str, Any] = {}
    unique_components = sorted({str(item).strip() for item in component_ids if str(item).strip()})
    if unique_components:
        hint["component"] = list(unique_components)

    violations = list(getattr(context, "violations", []) or [])
    affected_components = _collect_affected_components(violations)
    hot_components = _collect_hot_components(context=context, violations=violations)
    if affected_components or hot_components:
        component_groups: Dict[str, Any] = {}
        if affected_components:
            component_groups["affected_cluster"] = {
                "component_ids": list(affected_components),
            }
        if hot_components:
            component_groups["hot_cluster"] = {
                "component_ids": list(hot_components),
            }
        if component_groups:
            hint["component_group"] = component_groups

    keepout_axis, keepout_sign = _normalize_axis_and_sign(
        runtime_constraints.get("mission_keepout_axis")
    )
    mission_face = _face_from_axis(keepout_axis, keepout_sign) if keepout_axis else ""
    keepout_center = float(runtime_constraints.get("mission_keepout_center_mm", 0.0) or 0.0)
    keepout_separation = float(
        runtime_constraints.get("mission_min_separation_mm", 0.0) or 0.0
    )
    mission_related = bool(
        keepout_axis
        or str(dominant_family or "").strip().lower() == "mission"
        or str(dominant_metric or "").strip().lower() == "mission_keepout_violation"
    )
    if mission_related:
        mission_axis = keepout_axis or "z"
        mission_sign = keepout_sign if keepout_axis else 1.0
        mission_face = mission_face or _face_from_axis(mission_axis, mission_sign)
        hint["aperture"] = {
            "mission_aperture": {
                "axis": mission_axis,
                "face": mission_face,
            }
        }
        hint["panel"] = {
            "mission_panel": {
                "axis": mission_axis,
                "face": mission_face,
            }
        }
        hint.setdefault("zone", {})
        hint["zone"]["mission_keepout_zone"] = {
            "axis": mission_axis,
            "face": mission_face,
            "center_mm": float(keepout_center),
            "min_separation_mm": float(max(keepout_separation, 0.0)),
            "preferred_side": "auto",
        }

    thermal_related = bool(
        hot_components
        or str(dominant_family or "").strip().lower() == "thermal"
        or str(dominant_metric or "").strip().lower() == "max_temp"
    )
    if thermal_related:
        radiator_axis = keepout_axis or "z"
        radiator_sign = -keepout_sign if keepout_axis else -1.0
        hint.setdefault("zone", {})
        hint["zone"]["radiator_zone_primary"] = {
            "axis": radiator_axis,
            "face": _face_from_axis(radiator_axis, radiator_sign),
            "preferred_side": "negative" if radiator_sign < 0.0 else "positive",
            "component_ids": list(hot_components),
        }

    structural_related = bool(
        str(dominant_family or "").strip().lower() == "structural"
        or str(dominant_metric or "").strip().lower()
        in {"max_stress", "safety_factor", "first_modal_freq"}
    )
    if structural_related:
        structure_axis = keepout_axis or "z"
        structure_face = _face_from_axis(structure_axis, 1.0)
        hint.setdefault("mount_site", {})
        hint["mount_site"]["structural_mount_site"] = {
            "axis": structure_axis,
            "face": structure_face,
        }
        hint.setdefault("panel", {})
        hint["panel"]["structural_panel"] = {
            "axis": structure_axis,
            "face": structure_face,
        }

    return hint


def build_vop_graph(
    *,
    context: GlobalContextPack,
    metrics: Dict[str, Any],
    runtime_constraints: Dict[str, Any],
    component_ids: Iterable[str],
    simulation_backend: str = "",
    retrieval_items: int = 0,
) -> VOPGraph:
    """Build a compact violation-operator provenance graph."""
    violations = list(getattr(context, "violations", []) or [])
    dominant_family = _dominant_violation_family(violations)
    dominant_metric = _dominant_metric(violations)
    nodes: List[VOPGNode] = []
    edges: List[VOPGEdge] = []

    metric_snapshot = {
        "max_temp": float(getattr(context.thermal_metrics, "max_temp", 0.0) or 0.0),
        "min_clearance": float(getattr(context.geometry_metrics, "min_clearance", 0.0) or 0.0),
        "cg_offset": float(getattr(context.geometry_metrics, "cg_offset_magnitude", 0.0) or 0.0),
        "safety_factor": float(getattr(context.structural_metrics, "safety_factor", 0.0) or 0.0),
        "first_modal_freq": float(getattr(context.structural_metrics, "first_modal_freq", 0.0) or 0.0),
        "voltage_drop": float(getattr(context.power_metrics, "voltage_drop", 0.0) or 0.0),
        "power_margin": float(getattr(context.power_metrics, "power_margin", 0.0) or 0.0),
    }
    for metric_key, metric_value in metric_snapshot.items():
        nodes.append(
            VOPGNode(
                node_id=f"metric:{metric_key}",
                node_type="metric",
                label=metric_key,
                attributes={
                    "value": float(metric_value),
                    "runtime_constraint": runtime_constraints.get(metric_key),
                },
            )
        )

    unique_components = {str(item).strip() for item in component_ids if str(item).strip()}
    for comp_id in sorted(unique_components):
        nodes.append(
            VOPGNode(
                node_id=f"component:{comp_id}",
                node_type="component",
                label=comp_id,
                attributes={},
            )
        )

    for family in ("geometry", "thermal", "structural", "power", "mission"):
        nodes.append(
            VOPGNode(
                node_id=f"operator_family:{family}",
                node_type="operator_family",
                label=family,
                attributes={"is_dominant_hint": bool(family == dominant_family)},
            )
        )

    source_labels = sorted(
        {
            str(item).strip().lower()
            for item in (
                simulation_backend,
                str((metrics.get("diagnostics", {}) or {}).get("thermal_source", "")),
                str((metrics.get("diagnostics", {}) or {}).get("structural_source", "")),
                str((metrics.get("diagnostics", {}) or {}).get("power_source", "")),
            )
            if str(item).strip()
        }
    )
    for source in source_labels:
        nodes.append(
            VOPGNode(
                node_id=f"source:{source}",
                node_type="evidence_source",
                label=source,
                attributes={},
            )
        )

    for violation in violations:
        violation_id = str(getattr(violation, "violation_id", "") or "")
        family = str(getattr(violation, "violation_type", "") or "").strip().lower()
        severity = str(getattr(violation, "severity", "") or "").strip().lower()
        nodes.append(
            VOPGNode(
                node_id=f"constraint:{violation_id}",
                node_type="constraint",
                label=violation_id or family or "constraint",
                attributes={
                    "family": family,
                    "severity": severity,
                    "description": str(getattr(violation, "description", "") or ""),
                    "metric_value": float(getattr(violation, "metric_value", 0.0) or 0.0),
                    "threshold": float(getattr(violation, "threshold", 0.0) or 0.0),
                },
            )
        )
        edges.append(
            VOPGEdge(
                source=f"constraint:{violation_id}",
                target=f"operator_family:{family or dominant_family or 'geometry'}",
                relation="repair_family",
                weight=float(_SEVERITY_WEIGHT.get(severity, 1.0)),
            )
        )
        for comp_id in list(getattr(violation, "affected_components", []) or []):
            comp_text = str(comp_id).strip()
            if not comp_text:
                continue
            edges.append(
                VOPGEdge(
                    source=f"constraint:{violation_id}",
                    target=f"component:{comp_text}",
                    relation="affects",
                    weight=float(_SEVERITY_WEIGHT.get(severity, 1.0)),
                )
            )

    if dominant_metric:
        edges.append(
            VOPGEdge(
                source=f"metric:{dominant_metric}",
                target=f"operator_family:{dominant_family or 'geometry'}",
                relation="dominant_metric_guides",
                weight=1.0,
            )
        )

    summary = (
        f"dominant_family={dominant_family or 'none'}, "
        f"dominant_metric={dominant_metric or 'none'}, "
        f"violations={len(violations)}, "
        f"components={len(unique_components)}, "
        f"retrieved_knowledge={int(retrieval_items)}"
    )
    binding_catalog_hint = _build_binding_catalog_hint(
        context=context,
        runtime_constraints=dict(runtime_constraints or {}),
        component_ids=sorted(unique_components),
        dominant_family=dominant_family,
        dominant_metric=dominant_metric,
    )
    return VOPGraph(
        graph_id=f"vopg_iter_{int(context.iteration):02d}",
        iteration=int(context.iteration),
        dominant_violation_family=str(dominant_family or ""),
        dominant_metric=str(dominant_metric or ""),
        nodes=nodes,
        edges=edges,
        metadata={
            "simulation_backend": str(simulation_backend or ""),
            "retrieval_items": int(retrieval_items),
            "history_summary": str(getattr(context, "history_summary", "") or ""),
            "design_state_summary": str(getattr(context, "design_state_summary", "") or ""),
            "binding_catalog_hint": binding_catalog_hint,
        },
        summary=summary,
    )
