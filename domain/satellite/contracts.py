from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


CANONICAL_FACES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
CANONICAL_AXES = ("x", "y", "z")


def _validate_face_id(value: str) -> str:
    face_id = str(value or "").strip().upper()
    if face_id not in CANONICAL_FACES:
        raise ValueError(f"unsupported_face_id:{value}")
    return face_id


def _validate_span_triplet(value: Tuple[float, float, float] | List[float]) -> Tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError("span_triplet_must_have_three_values")
    triplet = tuple(float(v) for v in value)
    if any(v <= 0.0 for v in triplet):
        raise ValueError("span_triplet_values_must_be_positive")
    return triplet


class MissionClass(str, Enum):
    NAVIGATION = "navigation"
    EARTH_OBSERVATION = "earth_observation"
    COMMUNICATIONS = "communications"
    TECHNOLOGY_DEMONSTRATION = "technology_demonstration"
    SCIENCE = "science"


class BusTopology(str, Enum):
    MONOCOQUE = "monocoque"
    PANEL_BUS = "panel_bus"
    MODULAR_FRAME = "modular_frame"
    CUBESAT_RAIL = "cubesat_rail"


class TaskFaceSemantic(BaseModel):
    semantic: str
    face_id: str
    required: bool = False
    notes: str = ""

    @field_validator("face_id")
    @classmethod
    def _canonicalize_face_id(cls, value: str) -> str:
        return _validate_face_id(value)


class AppendageTemplate(BaseModel):
    template_id: str
    kind: str
    allowed_faces: List[str] = Field(default_factory=list)
    max_instances: int = Field(default=1, ge=1)
    max_span_mm: Tuple[float, float, float]
    max_offset_mm: float = Field(default=0.0, ge=0.0)
    notes: str = ""

    @field_validator("allowed_faces")
    @classmethod
    def _canonicalize_allowed_faces(cls, values: List[str]) -> List[str]:
        canonical = [_validate_face_id(value) for value in list(values or [])]
        if not canonical:
            raise ValueError("appendage_template_requires_allowed_faces")
        return canonical

    @field_validator("max_span_mm")
    @classmethod
    def _validate_max_span(cls, value: Tuple[float, float, float] | List[float]) -> Tuple[float, float, float]:
        return _validate_span_triplet(value)


class InteriorZoneDefinition(BaseModel):
    zone_id: str
    semantic: str
    allowed_categories: List[str] = Field(default_factory=list)
    notes: str = ""


class AxisRatioBound(BaseModel):
    numerator_axis: str
    denominator_axis: str
    min_ratio: float = Field(gt=0.0)
    max_ratio: float = Field(gt=0.0)
    notes: str = ""

    @field_validator("numerator_axis", "denominator_axis")
    @classmethod
    def _canonicalize_axis(cls, value: str) -> str:
        axis = str(value or "").strip().lower()
        if axis not in CANONICAL_AXES:
            raise ValueError(f"unsupported_axis:{value}")
        return axis

    @field_validator("max_ratio")
    @classmethod
    def _validate_ratio_order(cls, value: float, info) -> float:
        min_ratio = float(info.data.get("min_ratio", 0.0) or 0.0)
        max_ratio = float(value or 0.0)
        if max_ratio < min_ratio:
            raise ValueError("max_ratio_must_be_greater_than_or_equal_to_min_ratio")
        return max_ratio


class MorphologyGrammar(BaseModel):
    bus_topology: BusTopology
    bus_aspect_ratio_bounds: List[AxisRatioBound] = Field(default_factory=list)
    task_face_semantics: List[TaskFaceSemantic] = Field(default_factory=list)
    external_appendage_schema: List[AppendageTemplate] = Field(default_factory=list)
    interior_zone_schema: List[InteriorZoneDefinition] = Field(default_factory=list)
    attitude_semantics: List[str] = Field(default_factory=list)
    allowed_shell_variants: List[str] = Field(default_factory=list)

    def required_task_faces(self) -> List[TaskFaceSemantic]:
        return [item for item in list(self.task_face_semantics or []) if item.required]

    def appendage_template_by_id(self) -> Dict[str, AppendageTemplate]:
        return {
            str(template.template_id): template
            for template in list(self.external_appendage_schema or [])
        }


class SatelliteArchetype(BaseModel):
    archetype_id: str
    mission_class: MissionClass
    morphology: MorphologyGrammar
    default_rule_profile: str
    public_reference_notes: List[str] = Field(default_factory=list)
    reference_boundary: str = ""


class SatelliteReferenceBaseline(BaseModel):
    baseline_id: str
    version: str
    reference_boundary: str
    archetypes: List[SatelliteArchetype] = Field(default_factory=list)

    def get_archetype(self, archetype_id: str) -> Optional[SatelliteArchetype]:
        target = str(archetype_id or "").strip()
        for archetype in list(self.archetypes or []):
            if str(archetype.archetype_id) == target:
                return archetype
        return None

    def archetype_ids(self) -> List[str]:
        return [str(archetype.archetype_id) for archetype in list(self.archetypes or [])]


class CandidateTaskFace(BaseModel):
    semantic: str
    face_id: str

    @field_validator("face_id")
    @classmethod
    def _canonicalize_face_id(cls, value: str) -> str:
        return _validate_face_id(value)


class AppendageInstance(BaseModel):
    template_id: str
    host_face: str
    span_mm: Tuple[float, float, float]
    offset_mm: float = Field(default=0.0, ge=0.0)

    @field_validator("host_face")
    @classmethod
    def _canonicalize_host_face(cls, value: str) -> str:
        return _validate_face_id(value)

    @field_validator("span_mm")
    @classmethod
    def _validate_span(cls, value: Tuple[float, float, float] | List[float]) -> Tuple[float, float, float]:
        return _validate_span_triplet(value)


class CandidateInteriorZoneAssignment(BaseModel):
    zone_id: str
    component_id: str
    component_category: str
    source: str = ""


class SatelliteLayoutCandidate(BaseModel):
    archetype_id: str
    bus_span_mm: Optional[Tuple[float, float, float]] = None
    task_face_assignments: List[CandidateTaskFace] = Field(default_factory=list)
    appendages: List[AppendageInstance] = Field(default_factory=list)
    interior_zone_assignments: List[CandidateInteriorZoneAssignment] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("bus_span_mm")
    @classmethod
    def _validate_bus_span(cls, value: Optional[Tuple[float, float, float] | List[float]]) -> Optional[Tuple[float, float, float]]:
        if value is None:
            return None
        return _validate_span_triplet(value)


class LikenessGateCheck(BaseModel):
    rule_id: str
    passed: bool
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class SatelliteLikenessReport(BaseModel):
    passed: bool
    candidate_archetype_id: str
    expected_archetype_id: Optional[str] = None
    checks: List[LikenessGateCheck] = Field(default_factory=list)

    def failed_rule_ids(self) -> List[str]:
        return [check.rule_id for check in list(self.checks or []) if not check.passed]
