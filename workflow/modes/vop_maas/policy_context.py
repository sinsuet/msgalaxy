"""
Structured VOPG builders for vop_maas.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

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
        },
        summary=summary,
    )
