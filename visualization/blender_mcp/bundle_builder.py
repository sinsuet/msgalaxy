"""
Build Blender render bundles from MsGalaxy run artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from core.path_policy import serialize_repo_path, serialize_run_path
from core.protocol import DesignState
from geometry.cad_export_occ import export_design_occ
from visualization.blender_mcp.contracts import (
    RenderBundle,
    RenderComponent,
    RenderEnvelope,
    RenderHeuristics,
    RenderProfile,
    RenderSource,
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_snapshot_files(run_dir: Path) -> Iterable[Path]:
    snapshots_dir = run_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    return sorted(snapshots_dir.glob("*.json"))


def _snapshot_sort_key(path: Path) -> Tuple[int, int, str]:
    name = path.stem
    parts = name.split("_")
    sequence = 0
    iteration = 0
    if len(parts) >= 2 and parts[0] == "seq":
        try:
            sequence = int(parts[1])
        except Exception:
            sequence = 0
    if "iter" in parts:
        try:
            iteration = int(parts[parts.index("iter") + 1])
        except Exception:
            iteration = 0
    return sequence, iteration, name


def _select_snapshot(run_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    candidates = list(_iter_snapshot_files(run_dir))
    if not candidates:
        raise FileNotFoundError(f"No snapshot files found in {run_dir}")

    payloads = [(path, _load_json(path)) for path in candidates]
    final_selected = [
        item
        for item in payloads
        if str(item[1].get("stage", "") or "").strip().lower() == "final_selected"
    ]
    if final_selected:
        return sorted(final_selected, key=lambda item: _snapshot_sort_key(item[0]))[-1]
    return sorted(payloads, key=lambda item: _snapshot_sort_key(item[0]))[-1]


def _to_vector_list(obj: Any) -> list[float]:
    if isinstance(obj, dict):
        return [
            float(obj.get("x", 0.0) or 0.0),
            float(obj.get("y", 0.0) or 0.0),
            float(obj.get("z", 0.0) or 0.0),
        ]
    if hasattr(obj, "x") and hasattr(obj, "y") and hasattr(obj, "z"):
        return [float(obj.x), float(obj.y), float(obj.z)]
    values = list(obj or [0.0, 0.0, 0.0])
    return [float(values[0]), float(values[1]), float(values[2])]


def _role_from_component(component: Any) -> str:
    comp_id = str(getattr(component, "id", "") or "").lower()
    category = str(getattr(component, "category", "") or "").lower()

    if "payload" in category and any(token in comp_id for token in ("optic", "camera", "sensor", "tracker")):
        return "payload_optics"
    if "payload" in category:
        return "payload_box"
    if "battery" in comp_id:
        return "battery_pack"
    if "pdu" in comp_id or "power" in category:
        return "power_unit"
    if "avionics" in category or "obc" in comp_id:
        return "avionics_box"
    if "thermal" in category or "radiator" in comp_id:
        return "radiator_panel"
    return category or "generic_box"


def _material_hint(component: Any, role: str) -> str:
    coating = str(getattr(component, "coating_type", "") or "").lower()
    category = str(getattr(component, "category", "") or "").lower()

    if role == "payload_optics":
        return "black_anodized_aluminum"
    if role == "radiator_panel":
        return "white_thermal_paint" if "white" in coating else "brushed_aluminum"
    if role == "battery_pack":
        return "battery_dark_gray"
    if role == "power_unit":
        return "power_blue_gray"
    if role == "avionics_box":
        return "gunmetal_space"
    if "mli" in coating:
        return "mli_silver"
    if category == "thermal":
        return "white_thermal_paint"
    return "spacecraft_gray"


def _is_external_component(component: Any, role: str) -> bool:
    dims = _to_vector_list(getattr(component, "dimensions", None))
    if role in {"radiator_panel", "payload_optics"}:
        return True
    return min(dims) <= 20.0


def _preferred_payload_face(design_state: DesignState) -> str:
    envelope = _to_vector_list(design_state.envelope.outer_size)
    half = [value / 2.0 for value in envelope]

    best_face = "+Z"
    best_score = float("-inf")
    for component in design_state.components:
        category = str(component.category or "").lower()
        if "payload" not in category:
            continue
        position = _to_vector_list(component.position)
        scores = {
            "+X": position[0] / max(half[0], 1.0),
            "-X": -position[0] / max(half[0], 1.0),
            "+Y": position[1] / max(half[1], 1.0),
            "-Y": -position[1] / max(half[1], 1.0),
            "+Z": position[2] / max(half[2], 1.0),
            "-Z": -position[2] / max(half[2], 1.0),
        }
        face, score = max(scores.items(), key=lambda item: item[1])
        if score > best_score:
            best_face = face
            best_score = float(score)
    return best_face


def _build_heuristics(design_state: DesignState, components: list[RenderComponent]) -> RenderHeuristics:
    total_power = sum(float(getattr(component, "power", 0.0) or 0.0) for component in design_state.components)
    roles = {component.render_role for component in components}
    notes: list[str] = []

    enable_solar = total_power >= 60.0 and "solar_panel" not in roles
    if enable_solar:
        notes.append("Solar wings are visual heuristics inferred from total power and not optimization truth.")
    if "payload_optics" in roles:
        notes.append("Payload lens is inferred from payload category and nearest envelope face.")
    if "radiator_panel" in roles:
        notes.append("Radiator fins are stylized from thermal component geometry.")

    return RenderHeuristics(
        payload_face=_preferred_payload_face(design_state),
        enable_payload_lens="payload_optics" in roles,
        enable_radiator_fins="radiator_panel" in roles,
        enable_solar_wings=enable_solar,
        notes=notes,
    )


def _component_to_render(component: Any) -> RenderComponent:
    role = _role_from_component(component)
    return RenderComponent(
        id=str(component.id),
        category=str(component.category or ""),
        render_role=role,
        display_name=str(component.id),
        position_mm=_to_vector_list(component.position),
        dimensions_mm=_to_vector_list(component.dimensions),
        rotation_deg=_to_vector_list(getattr(component, "rotation", None)),
        envelope_type=str(getattr(component, "envelope_type", "box") or "box"),
        material_hint=_material_hint(component, role),
        coating_type=str(getattr(component, "coating_type", "default") or "default"),
        power_w=float(getattr(component, "power", 0.0) or 0.0),
        mass_kg=float(getattr(component, "mass", 0.0) or 0.0),
        is_external=_is_external_component(component, role),
        attachments={
            "heatsink": getattr(component, "heatsink", None),
            "bracket": getattr(component, "bracket", None),
        },
        metadata={
            "clearance_mm": float(getattr(component, "clearance", 0.0) or 0.0),
            "thermal_contacts": dict(getattr(component, "thermal_contacts", {}) or {}),
        },
    )


def _build_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    metrics["best_cv_min"] = float(summary.get("best_cv_min", 0.0) or 0.0)
    metrics["status"] = str(summary.get("status", "") or "")
    metrics["diagnosis_status"] = str(summary.get("diagnosis_status", "") or "")
    metrics.update(dict(summary.get("best_candidate_metrics", {}) or {}))
    return metrics


def _export_step_if_requested(design_state: DesignState, output_path: Path, enabled: bool) -> tuple[str, str]:
    if not enabled:
        return "", "disabled"
    try:
        export_design_occ(design_state, str(output_path))
    except Exception as exc:
        return "", str(exc)
    return str(output_path.resolve()), ""


def build_render_bundle_from_run(
    run_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    profile_name: str = "showcase",
    export_step: bool = False,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run_path}")

    summary = _load_json(summary_path)
    snapshot_path, snapshot_payload = _select_snapshot(run_path)
    design_state = DesignState(**dict(snapshot_payload.get("design_state", {}) or {}))

    output_root = Path(output_dir).resolve() if output_dir else (run_path / "visualizations" / "blender").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    render_components = [_component_to_render(component) for component in design_state.components]
    heuristics = _build_heuristics(design_state, render_components)

    step_output_path = output_root / "final_layout.step"
    step_path, step_error = _export_step_if_requested(design_state, step_output_path, export_step)
    persisted_run_dir = serialize_repo_path(run_path)
    persisted_snapshot_path = serialize_run_path(run_path, snapshot_path)
    persisted_summary_path = serialize_run_path(run_path, summary_path)
    persisted_final_mph_path = serialize_repo_path(summary.get("final_mph_path", "") or "")
    persisted_step_path = serialize_repo_path(step_path)

    bundle = RenderBundle(
        run_id=str(summary.get("run_id", run_path.name) or run_path.name),
        run_label=str(summary.get("run_label", "") or ""),
        source=RenderSource(
            run_dir=persisted_run_dir,
            snapshot_path=persisted_snapshot_path,
            summary_path=persisted_summary_path,
            final_mph_path=persisted_final_mph_path,
            step_path=persisted_step_path,
        ),
        envelope=RenderEnvelope(
            outer_size_mm=_to_vector_list(design_state.envelope.outer_size),
            origin=str(getattr(design_state.envelope, "origin", "center") or "center"),
            thickness_mm=float(getattr(design_state.envelope, "thickness", 0.0) or 0.0),
        ),
        components=render_components,
        keepouts=[
            {
                "tag": str(zone.tag),
                "min_point_mm": _to_vector_list(zone.min_point),
                "max_point_mm": _to_vector_list(zone.max_point),
            }
            for zone in list(design_state.keepouts or [])
        ],
        metrics=_build_metrics(summary),
        render_profile=RenderProfile(profile_name=profile_name),
        heuristics=heuristics,
        metadata={
            "component_count": len(render_components),
            "snapshot_stage": str(snapshot_payload.get("stage", "") or ""),
            "step_export_error": step_error,
            "layout_state_hash": str(summary.get("layout_state_hash", "") or ""),
        },
    )

    bundle_path = output_root / "render_bundle.json"
    bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    manifest = {
        "status": "success",
        "bundle_path": serialize_repo_path(bundle_path),
        "run_dir": persisted_run_dir,
        "snapshot_path": persisted_snapshot_path,
        "summary_path": persisted_summary_path,
        "step_path": persisted_step_path,
        "step_export_error": step_error,
        "profile_name": profile_name,
        "component_count": len(render_components),
    }
    manifest_path = output_root / "render_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "bundle": bundle.model_dump(mode="json"),
        "bundle_path": str(bundle_path),
        "manifest_path": str(manifest_path),
        "output_dir": str(output_root),
    }
