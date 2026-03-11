"""
Build Blender render bundles from MsGalaxy run artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence

from core.path_policy import serialize_repo_path, serialize_run_path
from core.protocol import DesignState
from core.visualization import _operator_action_family, _resolve_record_operator_actions
from geometry.cad_export_occ import export_design_occ
from visualization.blender_mcp.contracts import (
    RenderArtifactLinks,
    RenderBundle,
    RenderComponent,
    RenderEnvelope,
    RenderHeuristics,
    RenderManifest,
    RenderProfile,
    RenderSource,
    RenderState,
    ReviewPayload,
)
from visualization.review_package import (
    DEFAULT_KEY_STATES,
    build_iteration_review_packages_from_run,
    build_review_package_artifact_links,
    build_review_payload,
    build_state_selection,
    get_operator_family_spec,
    planned_review_package_paths,
)
from visualization.review_package.operator_semantics import build_operator_semantic_display
from visualization.review_summary_bridge import build_iteration_review_audit_digest


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    notes: list[str] = [
        "Visualization-only heuristics must not be interpreted as solver or physics truth.",
    ]

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
    metrics["final_audit_status"] = str(summary.get("final_audit_status", "") or "")
    metrics["dominant_violation"] = str(summary.get("dominant_violation", "") or "")
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


def _normalize_key_states(key_states: str | Sequence[str] | None) -> list[str]:
    if key_states is None:
        return list(DEFAULT_KEY_STATES)
    if isinstance(key_states, str):
        values = [item.strip().lower() for item in key_states.split(",")]
    else:
        values = [str(item).strip().lower() for item in list(key_states)]
    normalized = [item for item in values if item]
    return list(dict.fromkeys(normalized or list(DEFAULT_KEY_STATES)))


def _operator_family_label(family: str) -> str:
    spec = get_operator_family_spec(family)
    if spec is None:
        return str(family or "")
    return str(spec.label or family)


def _record_to_render_state(state_name: str, record: Dict[str, Any]) -> RenderState:
    event = dict(record.get("event", {}) or {})
    snapshot = dict(record.get("snapshot", {}) or {})
    design_state = DesignState(**dict(snapshot.get("design_state", {}) or {}))
    components = [_component_to_render(component) for component in design_state.components]
    metadata = dict(snapshot.get("metadata", {}) or {})
    event_metadata = dict(event.get("metadata", {}) or {})
    merged_metadata = dict(metadata)
    merged_metadata.update(event_metadata)
    operator_actions = _resolve_record_operator_actions(event, snapshot)
    primary_action = "" if not operator_actions else str(operator_actions[0])
    primary_action_family = _operator_action_family(primary_action) if primary_action else ""
    semantic_display = build_operator_semantic_display(
        primary_action=primary_action,
        dsl_version=(
            merged_metadata.get("selected_candidate_dsl_version", "")
            or merged_metadata.get("operator_dsl_version", "")
            or ""
        ),
        metadata=merged_metadata,
        expected_effects=merged_metadata.get("expected_effects", []),
        observed_effects=[],
        rule_engine_report=(
            merged_metadata.get("rule_engine_report")
            or merged_metadata.get("rule_engine")
            or {}
        ),
    )
    metadata.update(
        {
            "moved_components": list(event.get("moved_components", []) or []),
            "added_heatsinks": list(event.get("added_heatsinks", []) or []),
            "added_brackets": list(event.get("added_brackets", []) or []),
            "changed_contacts": list(event.get("changed_contacts", []) or []),
            "changed_coatings": list(event.get("changed_coatings", []) or []),
            "component_count": len(components),
            "primary_action": primary_action,
            "primary_action_family": primary_action_family,
            "primary_action_family_label": _operator_family_label(primary_action_family),
            "primary_action_label": str(semantic_display.get("primary_action_label", "") or ""),
            "semantic_caption_short": str(semantic_display.get("semantic_caption_short", "") or ""),
            "semantic_caption": str(semantic_display.get("semantic_caption", "") or ""),
            "target_summary": str(semantic_display.get("target_summary", "") or ""),
            "rule_summary": str(semantic_display.get("rule_summary", "") or ""),
            "expected_effect_summary": str(semantic_display.get("expected_effect_summary", "") or ""),
            "observed_effect_summary": str(semantic_display.get("observed_effect_summary", "") or ""),
            "unmapped_actions": [
                action
                for action in operator_actions
                if _operator_action_family(action) == "other"
            ],
        }
    )
    return RenderState(
        name=state_name,
        snapshot_path=str(record.get("persisted_snapshot_path", "") or ""),
        stage=str(event.get("stage", snapshot.get("stage", "")) or ""),
        thermal_source=str(event.get("thermal_source", snapshot.get("thermal_source", "")) or ""),
        diagnosis_status=str(event.get("diagnosis_status", snapshot.get("diagnosis_status", "")) or ""),
        diagnosis_reason=str(event.get("diagnosis_reason", snapshot.get("diagnosis_reason", "")) or ""),
        metrics=dict(snapshot.get("metrics", event.get("metrics", {})) or {}),
        operator_actions=operator_actions,
        components=components,
        metadata=metadata,
    )


def _select_keepouts(states: Dict[str, RenderState]) -> list[Dict[str, Any]]:
    for state_name in ("final", "best", "initial"):
        state = states.get(state_name)
        if state is None:
            continue
        for component in state.components:
            _ = component
        break

    keepouts: list[Dict[str, Any]] = []
    for state_name in ("final", "best", "initial"):
        state = states.get(state_name)
        if state is None:
            continue
        design_state = state.metadata.get("design_state_keepouts")
        if isinstance(design_state, list) and design_state:
            return list(design_state)
    return keepouts


def build_render_bundle_from_run(
    run_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    profile_name: str = "engineering",
    export_step: bool = False,
    key_states: str | Sequence[str] | None = None,
    review_field_case_dir: str | Path | None = None,
    review_field_case_map: str | Path | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run_path}")

    summary = _load_json(summary_path)
    output_root = Path(output_dir).resolve() if output_dir else (run_path / "visualizations" / "blender").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    selected_states = build_state_selection(run_path, _normalize_key_states(key_states))
    all_records = list(selected_states["records"])
    selected_records = dict(selected_states["selected"])
    render_states = {name: _record_to_render_state(name, record) for name, record in selected_records.items()}

    final_state = render_states.get("final") or next(iter(render_states.values()))
    final_record = selected_records.get("final") or next(iter(selected_records.values()))
    final_design_state = DesignState(**dict(dict(final_record.get("snapshot", {}) or {}).get("design_state", {}) or {}))

    step_output_path = output_root / "final_layout.step"
    step_path, step_error = _export_step_if_requested(final_design_state, step_output_path, export_step)

    planned_paths = planned_review_package_paths(output_root)
    artifact_links = build_review_package_artifact_links(
        run_dir=run_path,
        output_root=output_root,
        step_path=serialize_repo_path(step_path),
    )
    review_package_metadata: Dict[str, Any] = {
        "iteration_review_build_status": "not_started",
    }
    try:
        inferred_review_field_case_dir = review_field_case_dir
        inferred_review_field_case_map = review_field_case_map
        if inferred_review_field_case_dir is None:
            inferred_review_field_case_dir = str(summary.get("iteration_review_field_case_dir", "") or "").strip() or None
        if inferred_review_field_case_map is None:
            inferred_review_field_case_map = str(summary.get("iteration_review_field_case_map_path", "") or "").strip() or None
        review_result = build_iteration_review_packages_from_run(
            run_path,
            field_case_dir=inferred_review_field_case_dir,
            field_case_map=inferred_review_field_case_map,
        )
        artifact_links.update(
            {
                "iteration_review_root": str(review_result.get("output_root", "") or ""),
                "iteration_review_index_path": str(review_result.get("index_path", "") or ""),
                "teacher_demo_review_index_path": str(
                    dict(dict(review_result.get("profiles", {}) or {}).get("teacher_demo", {}) or {}).get("index_path", "")
                    or ""
                ),
                "research_fast_review_index_path": str(
                    dict(dict(review_result.get("profiles", {}) or {}).get("research_fast", {}) or {}).get("index_path", "")
                    or ""
                ),
            }
        )
        review_package_metadata = {
            "iteration_review_build_status": "success",
            "iteration_review_index_path": str(review_result.get("index_path", "") or ""),
            "iteration_review_teacher_demo_index_path": str(artifact_links.get("teacher_demo_review_index_path", "") or ""),
            "iteration_review_research_fast_index_path": str(artifact_links.get("research_fast_review_index_path", "") or ""),
        }
    except Exception as exc:
        review_package_metadata = {
            "iteration_review_build_status": "error",
            "iteration_review_error": str(exc),
        }

    source = RenderSource(
        run_dir=serialize_repo_path(run_path),
        snapshot_path=str(final_state.snapshot_path or ""),
        summary_path=serialize_run_path(run_path, summary_path),
        report_path=str(artifact_links.get("report_path", "") or ""),
        layout_events_path=str(artifact_links.get("layout_events_path", "") or ""),
        release_audit_path=str(artifact_links.get("release_audit_path", "") or ""),
        final_mph_path=serialize_repo_path(summary.get("final_mph_path", "") or ""),
        step_path=serialize_repo_path(step_path),
    )

    keepouts: list[Dict[str, Any]] = []
    for state_name in ("final", "best", "initial"):
        record = selected_records.get(state_name)
        if record is None:
            continue
        design_state = DesignState(**dict(dict(record.get("snapshot", {}) or {}).get("design_state", {}) or {}))
        keepouts = [
            {
                "tag": str(zone.tag),
                "min_point_mm": _to_vector_list(zone.min_point),
                "max_point_mm": _to_vector_list(zone.max_point),
            }
            for zone in list(design_state.keepouts or [])
        ]
        if keepouts:
            break

    bundle = RenderBundle(
        run_id=str(summary.get("run_id", run_path.name) or run_path.name),
        run_label=str(summary.get("run_label", "") or ""),
        source=source,
        envelope=RenderEnvelope(
            outer_size_mm=_to_vector_list(final_design_state.envelope.outer_size),
            origin=str(getattr(final_design_state.envelope, "origin", "center") or "center"),
            thickness_mm=float(getattr(final_design_state.envelope, "thickness", 0.0) or 0.0),
        ),
        keepouts=keepouts,
        key_states=render_states,
        components=list(final_state.components),
        metrics=_build_metrics(summary),
        render_profile=RenderProfile(profile_name=profile_name),
        heuristics=_build_heuristics(final_design_state, list(final_state.components)),
        artifact_links=RenderArtifactLinks(**artifact_links),
        metadata={
            "component_count": len(final_state.components),
            "snapshot_stage": str(final_state.stage or ""),
            "step_export_error": step_error,
            "layout_state_hash": str(summary.get("layout_state_hash", "") or ""),
            "run_mode": str(summary.get("run_mode", summary.get("optimization_mode", "")) or ""),
            "execution_mode": str(summary.get("execution_mode", "") or ""),
            "runtime_feature_fingerprint_path": str(
                artifact_links.get("runtime_feature_fingerprint_path", "") or ""
            ),
            "mass_final_summary_zh_path": str(
                artifact_links.get("mass_final_summary_zh_path", "") or ""
            ),
            "mass_final_summary_digest_path": str(
                artifact_links.get("mass_final_summary_digest_path", "") or ""
            ),
            "llm_final_summary_zh_path": str(
                artifact_links.get("llm_final_summary_zh_path", "") or ""
            ),
            "key_state_order": list(render_states.keys()),
            "scene_contract_status": "phase1_final_state_render_only",
            "visualization_only": True,
            "legacy_components_alias_state": "final",
            **review_package_metadata,
        },
    )

    payload = ReviewPayload(
        **build_review_payload(
            run_dir=run_path,
            output_root=output_root,
            bundle_payload=bundle.model_dump(mode="json"),
            state_records=selected_records,
            all_records=all_records,
            summary=summary,
            artifact_links_override=artifact_links,
            notes=list(bundle.heuristics.notes),
        )
    )
    payload_metadata = dict(payload.metadata or {})
    operator_family_registry = dict(payload_metadata.get("operator_family_registry", {}) or {})
    operator_family_audit = dict(payload_metadata.get("operator_family_audit", {}) or {})
    payload.artifacts.update(
        {
            "iteration_review_root": str(artifact_links.get("iteration_review_root", "") or ""),
            "iteration_review_index_path": str(artifact_links.get("iteration_review_index_path", "") or ""),
            "teacher_demo_review_index_path": str(artifact_links.get("teacher_demo_review_index_path", "") or ""),
            "research_fast_review_index_path": str(artifact_links.get("research_fast_review_index_path", "") or ""),
        }
    )
    iteration_review_digest = build_iteration_review_audit_digest(payload.iteration_review)
    bundle.metadata["iteration_review_summary"] = iteration_review_digest
    bundle.metadata["operator_family_registry"] = operator_family_registry
    bundle.metadata["operator_family_audit"] = operator_family_audit
    bundle.metadata["final_primary_action_family_label"] = str(
        dict(final_state.metadata or {}).get("primary_action_family_label", "") or ""
    )

    manifest = RenderManifest(
        run_dir=serialize_repo_path(run_path),
        bundle_path=serialize_repo_path(planned_paths["bundle_path"]),
        scene_script_path=serialize_repo_path(planned_paths["scene_script_path"]),
        brief_path=serialize_repo_path(planned_paths["brief_path"]),
        review_payload_path=serialize_repo_path(planned_paths["review_payload_path"]),
        review_dashboard_path="",
        source_snapshot_paths={name: state.snapshot_path for name, state in render_states.items()},
        output_image_path=serialize_repo_path(planned_paths["output_image_path"]),
        output_image_paths=[serialize_repo_path(planned_paths["output_image_path"])],
        output_blend_path=serialize_repo_path(planned_paths["output_blend_path"]),
        profile_name=profile_name,
        key_states={
            name: {
                "snapshot_path": state.snapshot_path,
                "stage": state.stage,
                "diagnosis_status": state.diagnosis_status,
                "thermal_source": state.thermal_source,
            }
            for name, state in render_states.items()
        },
        direct_render_status="skipped",
        metadata={
            "schema_phase": "phase1",
            "visualization_only": True,
            "scene_contract_status": "phase1_final_state_render_only",
            "run_mode": str(summary.get("run_mode", summary.get("optimization_mode", "")) or ""),
            "execution_mode": str(summary.get("execution_mode", "") or ""),
            "runtime_feature_fingerprint_path": str(
                artifact_links.get("runtime_feature_fingerprint_path", "") or ""
            ),
            "mass_final_summary_zh_path": str(
                artifact_links.get("mass_final_summary_zh_path", "") or ""
            ),
            "mass_final_summary_digest_path": str(
                artifact_links.get("mass_final_summary_digest_path", "") or ""
            ),
            "llm_final_summary_zh_path": str(
                artifact_links.get("llm_final_summary_zh_path", "") or ""
            ),
            "output_exists": {
                "bundle": True,
                "review_payload": True,
                "scene_script": False,
                "brief": False,
                "output_image": False,
                "output_blend": False,
            },
            **review_package_metadata,
        },
        summary_path=source.summary_path,
        snapshot_path=source.snapshot_path,
        step_path=source.step_path,
        step_export_error=step_error,
        component_count=len(final_state.components),
    )
    manifest.metadata["iteration_review_summary"] = iteration_review_digest
    manifest.metadata["operator_family_registry"] = operator_family_registry
    manifest.metadata["operator_family_audit"] = operator_family_audit
    manifest.metadata["final_primary_action_family_label"] = str(
        dict(final_state.metadata or {}).get("primary_action_family_label", "") or ""
    )

    bundle_path = planned_paths["bundle_path"]
    review_payload_path = planned_paths["review_payload_path"]
    manifest_path = planned_paths["manifest_path"]
    bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    review_payload_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    return {
        "bundle": bundle.model_dump(mode="json"),
        "review_payload": payload.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "bundle_path": str(bundle_path),
        "review_payload_path": str(review_payload_path),
        "manifest_path": str(manifest_path),
        "output_dir": str(output_root),
        "key_states": list(render_states.keys()),
        "iteration_review_index_path": str(artifact_links.get("iteration_review_index_path", "") or ""),
    }
