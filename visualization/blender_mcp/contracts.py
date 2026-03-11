"""
Contracts for Blender MCP render sidecar artifacts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RenderAttachment(BaseModel):
    heatsink: Optional[Dict[str, Any]] = None
    bracket: Optional[Dict[str, Any]] = None


class RenderComponent(BaseModel):
    id: str
    category: str
    render_role: str
    display_name: str
    position_mm: List[float]
    dimensions_mm: List[float]
    rotation_deg: List[float]
    envelope_type: str = "box"
    material_hint: str = "spacecraft_gray"
    coating_type: str = "default"
    power_w: float = 0.0
    mass_kg: float = 0.0
    is_external: bool = False
    attachments: RenderAttachment = Field(default_factory=RenderAttachment)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RenderState(BaseModel):
    name: str
    snapshot_path: str
    stage: str = ""
    thermal_source: str = ""
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)
    operator_actions: List[str] = Field(default_factory=list)
    components: List[RenderComponent] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RenderSource(BaseModel):
    run_dir: str
    snapshot_path: str = ""
    summary_path: str = ""
    report_path: str = ""
    layout_events_path: str = ""
    release_audit_path: str = ""
    final_mph_path: str = ""
    step_path: str = ""


class RenderEnvelope(BaseModel):
    outer_size_mm: List[float]
    origin: str = "center"
    thickness_mm: float = 0.0


class RenderProfile(BaseModel):
    profile_name: str = "engineering"
    template: str = "satellite_engineering_review_v1"
    shots: List[str] = Field(default_factory=lambda: ["iso", "front", "top"])
    animation: List[str] = Field(default_factory=list)
    render_engine: str = "BLENDER_EEVEE_NEXT"


class RenderHeuristics(BaseModel):
    payload_face: str = "+Z"
    enable_payload_lens: bool = False
    enable_radiator_fins: bool = False
    enable_solar_wings: bool = False
    shell_style: str = "semi_transparent_panels"
    notes: List[str] = Field(default_factory=list)


class RenderArtifactLinks(BaseModel):
    summary_path: str = ""
    report_path: str = ""
    release_audit_path: str = ""
    layout_events_path: str = ""
    runtime_feature_fingerprint_path: str = ""
    mass_final_summary_zh_path: str = ""
    mass_final_summary_digest_path: str = ""
    llm_final_summary_zh_path: str = ""
    llm_final_summary_digest_path: str = ""
    attempts_table_path: str = ""
    generations_table_path: str = ""
    policy_tuning_path: str = ""
    layout_timeline_path: str = ""
    visualization_paths: List[str] = Field(default_factory=list)
    bundle_path: str = ""
    review_payload_path: str = ""
    render_manifest_path: str = ""
    render_brief_path: str = ""
    scene_script_path: str = ""
    review_dashboard_path: str = ""
    scene_audit_path: str = ""
    scene_readonly_checklist_path: str = ""
    output_image_path: str = ""
    output_image_paths: List[str] = Field(default_factory=list)
    output_blend_path: str = ""
    step_path: str = ""
    iteration_review_root: str = ""
    iteration_review_index_path: str = ""
    teacher_demo_review_index_path: str = ""
    research_fast_review_index_path: str = ""


class RenderBundle(BaseModel):
    schema_version: str = "blender_render_bundle/v2"
    run_id: str
    run_label: str = ""
    source: RenderSource
    units: str = "mm"
    coordinate_system: Dict[str, str] = Field(
        default_factory=lambda: {
            "source": "msgalaxy_rhs_mm",
            "target": "blender_rhs_m",
        }
    )
    envelope: RenderEnvelope
    keepouts: List[Dict[str, Any]] = Field(default_factory=list)
    key_states: Dict[str, RenderState] = Field(default_factory=dict)
    components: List[RenderComponent] = Field(default_factory=list)
    constraint_overlays: List[Dict[str, Any]] = Field(default_factory=list)
    component_annotations: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    render_profile: RenderProfile = Field(default_factory=RenderProfile)
    heuristics: RenderHeuristics = Field(default_factory=RenderHeuristics)
    artifact_links: RenderArtifactLinks = Field(default_factory=RenderArtifactLinks)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReviewPayload(BaseModel):
    schema_version: str = "blender_review_payload/v1"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    run: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    release_audit: Dict[str, Any] = Field(default_factory=dict)
    states: Dict[str, Any] = Field(default_factory=dict)
    attempt_trends: Dict[str, Any] = Field(default_factory=dict)
    generation_trends: Dict[str, Any] = Field(default_factory=dict)
    operator_coverage: Dict[str, Any] = Field(default_factory=dict)
    layout_displacement: Dict[str, Any] = Field(default_factory=dict)
    timeline: Dict[str, Any] = Field(default_factory=dict)
    iteration_review: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class RenderManifest(BaseModel):
    schema_version: str = "blender_render_manifest/v2"
    status: str = "success"
    run_dir: str
    bundle_path: str
    scene_script_path: str = ""
    brief_path: str = ""
    review_payload_path: str = ""
    review_dashboard_path: str = ""
    scene_audit_path: str = ""
    scene_readonly_checklist_path: str = ""
    source_snapshot_paths: Dict[str, str] = Field(default_factory=dict)
    output_image_path: str = ""
    output_image_paths: List[str] = Field(default_factory=list)
    output_blend_path: str = ""
    profile_name: str = "engineering"
    key_states: Dict[str, Any] = Field(default_factory=dict)
    direct_render_status: str = "skipped"
    direct_render_stdout: str = ""
    direct_render_stderr: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    summary_path: str = ""
    snapshot_path: str = ""
    step_path: str = ""
    step_export_error: str = ""
    component_count: int = 0
