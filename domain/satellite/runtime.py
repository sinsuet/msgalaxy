from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.protocol import ComponentGeometry, DesignState

from .baseline import load_default_satellite_reference_baseline
from .contracts import (
    AppendageInstance,
    CandidateInteriorZoneAssignment,
    CandidateTaskFace,
    InteriorZoneDefinition,
    LikenessGateCheck,
    SatelliteLayoutCandidate,
    SatelliteLikenessReport,
    SatelliteReferenceBaseline,
    TaskFaceSemantic,
)
from .gate import SatelliteLikenessGate
from .scenario import SatelliteScenarioSpec
from .selector import TaskTypeArchetypeSelector


CANONICAL_FACES = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}


def _normalize_gate_mode(value: Any, *, default: str = "off") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"off", "diagnostic", "strict"}:
        return normalized
    return str(default or "off").strip().lower() or "off"


def _load_bom_payload(bom_file: Optional[str]) -> Dict[str, Any]:
    path = Path(str(bom_file or "")).resolve() if str(bom_file or "").strip() else None
    if path is None or not path.exists():
        return {}
    if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        return {}

    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            payload = json.load(handle)
        else:
            payload = yaml.safe_load(handle)
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _parse_task_face_assignments(raw_faces: Any) -> List[CandidateTaskFace]:
    parsed: List[CandidateTaskFace] = []
    for item in list(raw_faces or []):
        if not isinstance(item, dict):
            continue
        parsed.append(CandidateTaskFace.model_validate(item))
    return parsed


def _parse_appendages(raw_appendages: Any) -> List[AppendageInstance]:
    parsed: List[AppendageInstance] = []
    for item in list(raw_appendages or []):
        if not isinstance(item, dict):
            continue
        parsed.append(AppendageInstance.model_validate(item))
    return parsed


def _parse_interior_zone_assignments(raw_assignments: Any) -> List[CandidateInteriorZoneAssignment]:
    parsed: List[CandidateInteriorZoneAssignment] = []
    for item in list(raw_assignments or []):
        if not isinstance(item, dict):
            continue
        parsed.append(CandidateInteriorZoneAssignment.model_validate(item))
    return parsed


def _build_inference_text(payload: Dict[str, Any]) -> str:
    tokens: List[str] = []
    for key in ("scenario_id", "description", "task_type", "mission_type"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            tokens.append(value)

    satellite_cfg = dict(payload.get("satellite", {}) or {})
    for key in ("task_type", "mission_type", "archetype_hint", "notes"):
        value = str(satellite_cfg.get(key, "") or "").strip()
        if value:
            tokens.append(value)

    for item in list(payload.get("components", []) or []):
        if not isinstance(item, dict):
            continue
        for key in ("id", "name", "category", "notes"):
            value = str(item.get(key, "") or "").strip()
            if value:
                tokens.append(value)

    return " ".join(tokens)


def _normalize_category(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "comm": "communication",
        "communications": "communication",
        "radio": "communication",
        "rf": "communication",
        "eps": "power",
        "electrical_power": "power",
        "battery_pack": "battery",
        "structural": "structure",
        "optical": "optics",
        "experiment": "science",
    }
    return aliases.get(text, text)


def _canonical_face_id(value: Any) -> str:
    face_id = str(value or "").strip().upper()
    return face_id if face_id in CANONICAL_FACES else ""


def _placement_state_by_component(design_state: DesignState) -> Dict[str, Dict[str, Any]]:
    placement_index: Dict[str, Dict[str, Any]] = {}
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    for item in list(metadata.get("placement_state", []) or []):
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("instance_id", "") or "").strip()
        if component_id:
            placement_index[component_id] = dict(item)
    return placement_index


def resolve_satellite_bom_context(
    bom_file: Optional[str],
    *,
    baseline: Optional[SatelliteReferenceBaseline] = None,
    default_gate_mode: str = "off",
) -> Dict[str, Any]:
    baseline_obj = baseline or load_default_satellite_reference_baseline()
    selector = TaskTypeArchetypeSelector(baseline=baseline_obj)
    payload = _load_bom_payload(bom_file)
    satellite_cfg = dict(payload.get("satellite", {}) or {})

    explicit_archetype_id = str(
        satellite_cfg.get("archetype_id", "") or payload.get("satellite_archetype", "") or ""
    ).strip()
    explicit_task_type = str(
        satellite_cfg.get("task_type", "") or payload.get("task_type", "") or payload.get("mission_type", "") or ""
    ).strip()
    gate_mode = _normalize_gate_mode(
        satellite_cfg.get("likeness_gate_mode", "") or satellite_cfg.get("gate_mode", ""),
        default=_normalize_gate_mode(payload.get("satellite_likeness_gate_mode", ""), default=default_gate_mode),
    )

    task_face_assignments = _parse_task_face_assignments(satellite_cfg.get("task_face_assignments", []))
    appendages = _parse_appendages(satellite_cfg.get("appendages", []))
    interior_zone_assignments = _parse_interior_zone_assignments(
        satellite_cfg.get("interior_zone_assignments", [])
    )

    archetype = None
    archetype_source = ""
    task_type = explicit_task_type
    task_type_source = "explicit_bom" if explicit_task_type else ""

    if explicit_archetype_id:
        archetype = baseline_obj.get_archetype(explicit_archetype_id)
        archetype_source = "explicit_bom" if archetype is not None else ""

    if archetype is None:
        inference_text = task_type or _build_inference_text(payload)
        if inference_text:
            try:
                archetype = selector.select(inference_text)
                archetype_source = "task_type_selector"
                if not task_type:
                    task_type = inference_text
                    task_type_source = "bom_inference"
            except ValueError:
                archetype = None

    return {
        "enabled": archetype is not None,
        "bom_file": str(bom_file or ""),
        "baseline_id": str(baseline_obj.baseline_id),
        "baseline_version": str(baseline_obj.version),
        "reference_boundary": str(baseline_obj.reference_boundary),
        "baseline_reference_boundary": str(baseline_obj.reference_boundary),
        "task_type": str(task_type),
        "task_type_source": str(task_type_source),
        "archetype_id": str(getattr(archetype, "archetype_id", "") or explicit_archetype_id),
        "archetype_source": str(archetype_source),
        "mission_class": str(getattr(getattr(archetype, "mission_class", None), "value", "") or ""),
        "default_rule_profile": str(getattr(archetype, "default_rule_profile", "") or ""),
        "archetype_reference_boundary": str(
            getattr(archetype, "reference_boundary", "") or ""
        ),
        "public_reference_notes": list(
            getattr(archetype, "public_reference_notes", []) or []
        ),
        "gate_mode": str(gate_mode),
        "task_face_assignments": [item.model_dump() for item in task_face_assignments],
        "appendages": [item.model_dump() for item in appendages],
        "interior_zone_assignments": [
            item.model_dump() for item in interior_zone_assignments
        ],
        "payload_keys": sorted(payload.keys()),
    }


def _envelope_bounds(design_state: DesignState) -> Tuple[List[float], List[float]]:
    envelope = design_state.envelope
    size = [
        float(envelope.outer_size.x),
        float(envelope.outer_size.y),
        float(envelope.outer_size.z),
    ]
    if str(getattr(envelope, "origin", "center")).strip().lower() == "center":
        return ([-0.5 * size[0], -0.5 * size[1], -0.5 * size[2]], [0.5 * size[0], 0.5 * size[1], 0.5 * size[2]])
    return ([0.0, 0.0, 0.0], [size[0], size[1], size[2]])


def _pick_component_for_semantic(
    design_state: DesignState,
    *,
    categories: Tuple[str, ...],
    id_tokens: Tuple[str, ...],
) -> Optional[ComponentGeometry]:
    best_component: Optional[ComponentGeometry] = None
    best_score = float("-inf")

    for comp in list(getattr(design_state, "components", []) or []):
        text = (
            str(getattr(comp, "id", "") or "").strip().lower()
            + " "
            + str(getattr(comp, "category", "") or "").strip().lower()
        )
        score = 0.0
        if str(getattr(comp, "category", "") or "").strip().lower() in set(categories):
            score += 10.0
        score += float(sum(1 for token in id_tokens if token in text)) * 2.0
        position = getattr(comp, "position", None)
        if position is not None:
            score += abs(float(position.x)) + abs(float(position.y)) + abs(float(position.z))
        if score > best_score:
            best_score = score
            best_component = comp

    return best_component if best_score > 0.0 else None


def _pick_component_with_aperture_site(
    design_state: DesignState,
    *,
    categories: Tuple[str, ...],
    id_tokens: Tuple[str, ...],
) -> Optional[ComponentGeometry]:
    placement_index = _placement_state_by_component(design_state)
    aperture_components = [
        comp
        for comp in list(getattr(design_state, "components", []) or [])
        if str(dict(placement_index.get(str(getattr(comp, "id", "") or ""), {}) or {}).get("aperture_site", "")).strip()
    ]
    if not aperture_components:
        return None

    best_component: Optional[ComponentGeometry] = None
    best_score = float("-inf")
    for comp in aperture_components:
        text = (
            str(getattr(comp, "id", "") or "").strip().lower()
            + " "
            + str(getattr(comp, "category", "") or "").strip().lower()
        )
        score = 0.0
        if str(getattr(comp, "category", "") or "").strip().lower() in set(categories):
            score += 10.0
        score += float(sum(1 for token in id_tokens if token in text)) * 2.0
        if score > best_score:
            best_score = score
            best_component = comp
    return best_component if best_score > 0.0 else aperture_components[0]


def _nearest_boundary_face(component: ComponentGeometry, design_state: DesignState) -> str:
    env_min, env_max = _envelope_bounds(design_state)
    position = component.position
    dimensions = component.dimensions
    center = [float(position.x), float(position.y), float(position.z)]
    half = [0.5 * float(dimensions.x), 0.5 * float(dimensions.y), 0.5 * float(dimensions.z)]
    distances = {
        "+X": float(env_max[0] - (center[0] + half[0])),
        "-X": float((center[0] - half[0]) - env_min[0]),
        "+Y": float(env_max[1] - (center[1] + half[1])),
        "-Y": float((center[1] - half[1]) - env_min[1]),
        "+Z": float(env_max[2] - (center[2] + half[2])),
        "-Z": float((center[2] - half[2]) - env_min[2]),
    }
    return min(distances.items(), key=lambda item: item[1])[0]


def _infer_task_face(
    design_state: DesignState,
    semantic_def: TaskFaceSemantic,
) -> tuple[Optional[CandidateTaskFace], str]:
    semantic_key = str(semantic_def.semantic or "").strip().lower()
    component: Optional[ComponentGeometry] = None
    placement_index = _placement_state_by_component(design_state)

    if "payload_face" in semantic_key or "experiment_face" in semantic_key:
        component = _pick_component_with_aperture_site(
            design_state,
            categories=("payload", "optics", "science"),
            id_tokens=("payload", "camera", "optic", "science", "experiment"),
        )
        if component is not None:
            mount_face = _canonical_face_id(
                dict(placement_index.get(str(getattr(component, "id", "") or ""), {}) or {}).get("mount_face", "")
            )
            if mount_face:
                return (
                    CandidateTaskFace(semantic=semantic_def.semantic, face_id=mount_face),
                    "placement_mount_face",
                )
        component = _pick_component_for_semantic(
            design_state,
            categories=("payload", "optics", "science"),
            id_tokens=("payload", "camera", "optic", "science", "experiment"),
        )
    elif "antenna_face" in semantic_key:
        component = _pick_component_for_semantic(
            design_state,
            categories=("comm", "communication", "payload"),
            id_tokens=("antenna", "radio", "radar", "tx", "comm"),
        )
    elif "radiator_face" in semantic_key or "thermal_rejection_face" in semantic_key:
        component = _pick_component_for_semantic(
            design_state,
            categories=("thermal",),
            id_tokens=("radiator", "thermal", "panel"),
        )

    if component is not None:
        return (
            CandidateTaskFace(semantic=semantic_def.semantic, face_id=_nearest_boundary_face(component, design_state)),
            "layout_component_boundary",
        )

    if any(
        token in semantic_key
        for token in ("solar_array_mount", "deployable_mount", "rail_stack_axis", "payload_mount_face")
    ):
        return (
            CandidateTaskFace(semantic=semantic_def.semantic, face_id=semantic_def.face_id),
            "archetype_default",
        )

    return None, ""


def _infer_component_zone_assignment(
    component: ComponentGeometry,
    interior_zones: List[InteriorZoneDefinition],
) -> tuple[Optional[CandidateInteriorZoneAssignment], str]:
    component_category = _normalize_category(getattr(component, "category", "") or "")
    if not component_category:
        return None, ""

    zone_matches: List[InteriorZoneDefinition] = []
    for zone in list(interior_zones or []):
        allowed = {
            _normalize_category(item)
            for item in list(getattr(zone, "allowed_categories", []) or [])
            if str(item).strip()
        }
        if component_category in allowed:
            zone_matches.append(zone)

    if not zone_matches:
        return None, ""

    selected_zone = zone_matches[0]
    return (
        CandidateInteriorZoneAssignment(
            zone_id=str(selected_zone.zone_id),
            component_id=str(getattr(component, "id", "") or ""),
            component_category=component_category,
            source="category_match",
        ),
        "category_match",
    )


def build_satellite_layout_candidate(
    design_state: DesignState,
    *,
    context: Dict[str, Any],
    baseline: Optional[SatelliteReferenceBaseline] = None,
) -> tuple[Optional[SatelliteLayoutCandidate], List[Dict[str, Any]]]:
    baseline_obj = baseline or load_default_satellite_reference_baseline()
    archetype_id = str(context.get("archetype_id", "") or "").strip()
    archetype = baseline_obj.get_archetype(archetype_id)
    if archetype is None:
        return None, []

    face_map: Dict[str, CandidateTaskFace] = {}
    resolution: List[Dict[str, Any]] = []
    interior_zone_assignments: List[CandidateInteriorZoneAssignment] = []
    interior_zone_resolution: List[Dict[str, Any]] = []
    interior_zone_by_component: Dict[str, CandidateInteriorZoneAssignment] = {}
    explicit_zone_assignments = _parse_interior_zone_assignments(
        context.get("interior_zone_assignments", [])
    )

    for item in _parse_task_face_assignments(context.get("task_face_assignments", [])):
        face_map[str(item.semantic)] = item
        resolution.append(
            {
                "semantic": str(item.semantic),
                "face_id": str(item.face_id),
                "source": "explicit_bom",
            }
        )

    for semantic_def in list(archetype.morphology.required_task_faces() or []):
        semantic_name = str(semantic_def.semantic)
        if semantic_name in face_map:
            continue
        inferred_face, source = _infer_task_face(design_state, semantic_def)
        if inferred_face is None:
            continue
        face_map[semantic_name] = inferred_face
        resolution.append(
            {
                "semantic": str(inferred_face.semantic),
                "face_id": str(inferred_face.face_id),
                "source": str(source),
            }
        )

    for assignment in explicit_zone_assignments:
        component_id = str(assignment.component_id or "").strip()
        if not component_id or component_id in interior_zone_by_component:
            continue
        interior_zone_by_component[component_id] = assignment
        interior_zone_assignments.append(assignment)
        interior_zone_resolution.append(
            {
                "component_id": component_id,
                "zone_id": str(assignment.zone_id),
                "component_category": str(assignment.component_category),
                "source": str(assignment.source or "explicit_bom"),
            }
        )

    unassigned_components: List[Dict[str, str]] = []
    interior_zones = list(archetype.morphology.interior_zone_schema or [])
    for component in list(getattr(design_state, "components", []) or []):
        component_id = str(getattr(component, "id", "") or "").strip()
        if not component_id or component_id in interior_zone_by_component:
            continue
        assignment, source = _infer_component_zone_assignment(component, interior_zones)
        if assignment is None:
            unassigned_components.append(
                {
                    "component_id": component_id,
                    "component_category": _normalize_category(
                        getattr(component, "category", "") or ""
                    ),
                }
            )
            continue
        interior_zone_by_component[component_id] = assignment
        interior_zone_assignments.append(assignment)
        interior_zone_resolution.append(
            {
                "component_id": component_id,
                "zone_id": str(assignment.zone_id),
                "component_category": str(assignment.component_category),
                "source": str(source),
            }
        )

    candidate = SatelliteLayoutCandidate(
        archetype_id=archetype.archetype_id,
        bus_span_mm=(
            float(design_state.envelope.outer_size.x),
            float(design_state.envelope.outer_size.y),
            float(design_state.envelope.outer_size.z),
        ),
        task_face_assignments=list(face_map.values()),
        appendages=_parse_appendages(context.get("appendages", [])),
        interior_zone_assignments=list(interior_zone_assignments),
        metadata={
            "task_type": str(context.get("task_type", "") or ""),
            "task_face_resolution": list(resolution),
            "bus_span_source": "envelope_outer_size",
            "interior_zone_resolution": list(interior_zone_resolution),
            "interior_zone_unassigned_components": list(unassigned_components),
        },
    )
    return candidate, resolution


def evaluate_satellite_likeness_for_design_state(
    design_state: DesignState,
    *,
    bom_file: Optional[str],
    baseline: Optional[SatelliteReferenceBaseline] = None,
    default_gate_mode: str = "off",
) -> Dict[str, Any]:
    baseline_obj = baseline or load_default_satellite_reference_baseline()
    gate_mode = _normalize_gate_mode(default_gate_mode, default="off")
    context = resolve_satellite_bom_context(
        bom_file,
        baseline=baseline_obj,
        default_gate_mode=gate_mode,
    )
    gate_mode = _normalize_gate_mode(context.get("gate_mode", gate_mode), default=gate_mode)

    result = dict(context)
    result["gate_mode"] = gate_mode
    result["candidate"] = {}
    result["task_face_resolution"] = []
    result["interior_zone_resolution"] = []
    result["gate_report"] = {}
    result["gate_passed"] = None

    candidate, resolution = build_satellite_layout_candidate(
        design_state,
        context=context,
        baseline=baseline_obj,
    )
    if candidate is not None:
        result["candidate"] = candidate.model_dump()
        result["task_face_resolution"] = list(resolution)
        result["interior_zone_resolution"] = list(
            dict(candidate.metadata or {}).get("interior_zone_resolution", []) or []
        )

    if gate_mode == "off":
        return result

    if candidate is None:
        report = SatelliteLikenessReport(
            passed=False,
            candidate_archetype_id=str(context.get("archetype_id", "") or ""),
            expected_archetype_id=str(context.get("archetype_id", "") or "") or None,
            checks=[
                LikenessGateCheck(
                    rule_id="archetype_resolution",
                    passed=False,
                    message="no satellite archetype could be resolved from BOM metadata or inferred task type",
                    details={
                        "task_type": str(context.get("task_type", "") or ""),
                        "task_type_source": str(context.get("task_type_source", "") or ""),
                    },
                )
            ],
        )
        result["gate_report"] = report.model_dump()
        result["gate_passed"] = False
        return result

    gate = SatelliteLikenessGate(baseline=baseline_obj)
    report = gate.evaluate(
        candidate,
        expected_archetype_id=str(context.get("archetype_id", "") or ""),
    )
    result["gate_report"] = report.model_dump()
    result["gate_passed"] = bool(report.passed)
    return result


def evaluate_satellite_likeness_for_scenario(
    design_state: DesignState,
    *,
    scenario: SatelliteScenarioSpec,
    baseline: Optional[SatelliteReferenceBaseline] = None,
    default_gate_mode: str = "off",
) -> Dict[str, Any]:
    baseline_obj = baseline or load_default_satellite_reference_baseline()
    gate_mode = _normalize_gate_mode(default_gate_mode, default="off")
    archetype = baseline_obj.get_archetype(str(scenario.archetype_id or "").strip())
    catalog_specs = scenario.catalog_specs_by_instance()

    task_type = str(dict(scenario.metadata or {}).get("task_type", "") or "").strip()
    task_type_source = "scenario_metadata" if task_type else ""
    if not task_type:
        task_type = str(scenario.description or scenario.scenario_id or "").strip()
        task_type_source = "scenario_description" if task_type else ""

    result = {
        "enabled": archetype is not None,
        "bom_file": "",
        "baseline_id": str(baseline_obj.baseline_id),
        "baseline_version": str(baseline_obj.version),
        "reference_boundary": str(baseline_obj.reference_boundary),
        "baseline_reference_boundary": str(baseline_obj.reference_boundary),
        "task_type": str(task_type),
        "task_type_source": str(task_type_source),
        "archetype_id": str(getattr(archetype, "archetype_id", "") or scenario.archetype_id),
        "archetype_source": "scenario_spec" if archetype is not None else "",
        "mission_class": str(getattr(getattr(archetype, "mission_class", None), "value", "") or ""),
        "default_rule_profile": str(scenario.rule_profile or getattr(archetype, "default_rule_profile", "") or ""),
        "archetype_reference_boundary": str(getattr(archetype, "reference_boundary", "") or ""),
        "public_reference_notes": list(getattr(archetype, "public_reference_notes", []) or []),
        "gate_mode": str(gate_mode),
        "task_face_assignments": list(dict(scenario.metadata or {}).get("task_face_assignments", []) or []),
        "appendages": list(dict(scenario.metadata or {}).get("appendages", []) or []),
        "interior_zone_assignments": [
            {
                "zone_id": str(instance.zone_id),
                "component_id": str(instance.instance_id),
                "component_category": str(getattr(catalog_specs.get(str(instance.instance_id)), "family", "") or ""),
                "source": "scenario_contract",
            }
            for instance in list(scenario.catalog_component_instances or [])
            if str(instance.zone_id or "").strip()
        ],
        "payload_keys": sorted(list(scenario.model_dump().keys())),
        "candidate": {},
        "task_face_resolution": [],
        "interior_zone_resolution": [],
        "gate_report": {},
        "gate_passed": None,
    }

    candidate, resolution = build_satellite_layout_candidate(
        design_state,
        context=result,
        baseline=baseline_obj,
    )
    if candidate is not None:
        result["candidate"] = candidate.model_dump()
        result["task_face_resolution"] = list(resolution)
        result["interior_zone_resolution"] = list(
            dict(candidate.metadata or {}).get("interior_zone_resolution", []) or []
        )

    if gate_mode == "off":
        return result

    if candidate is None:
        report = SatelliteLikenessReport(
            passed=False,
            candidate_archetype_id=str(result.get("archetype_id", "") or ""),
            expected_archetype_id=str(result.get("archetype_id", "") or "") or None,
            checks=[
                LikenessGateCheck(
                    rule_id="archetype_resolution",
                    passed=False,
                    message="no satellite archetype could be resolved from scenario metadata",
                    details={
                        "scenario_id": str(scenario.scenario_id),
                        "task_type": str(result.get("task_type", "") or ""),
                        "task_type_source": str(result.get("task_type_source", "") or ""),
                    },
                )
            ],
        )
        result["gate_report"] = report.model_dump()
        result["gate_passed"] = False
        return result

    gate = SatelliteLikenessGate(baseline=baseline_obj)
    report = gate.evaluate(
        candidate,
        expected_archetype_id=str(result.get("archetype_id", "") or ""),
    )
    result["gate_report"] = report.model_dump()
    result["gate_passed"] = bool(report.passed)
    return result
