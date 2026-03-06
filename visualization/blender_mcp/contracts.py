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


class RenderSource(BaseModel):
    run_dir: str
    snapshot_path: str
    summary_path: str
    final_mph_path: str = ""
    step_path: str = ""


class RenderEnvelope(BaseModel):
    outer_size_mm: List[float]
    origin: str = "center"
    thickness_mm: float = 0.0


class RenderProfile(BaseModel):
    profile_name: str = "showcase"
    template: str = "satellite_cleanroom_v1"
    shots: List[str] = Field(default_factory=lambda: ["iso", "front", "top"])
    animation: List[str] = Field(default_factory=lambda: ["turntable"])
    render_engine: str = "BLENDER_EEVEE_NEXT"


class RenderHeuristics(BaseModel):
    payload_face: str = "+Z"
    enable_payload_lens: bool = False
    enable_radiator_fins: bool = False
    enable_solar_wings: bool = False
    shell_style: str = "semi_transparent_panels"
    notes: List[str] = Field(default_factory=list)


class RenderBundle(BaseModel):
    schema_version: str = "blender_render_bundle/v1"
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
    components: List[RenderComponent] = Field(default_factory=list)
    keepouts: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    render_profile: RenderProfile = Field(default_factory=RenderProfile)
    heuristics: RenderHeuristics = Field(default_factory=RenderHeuristics)
    metadata: Dict[str, Any] = Field(default_factory=dict)
