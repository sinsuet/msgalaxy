from __future__ import annotations

from collections import Counter
from typing import Optional

from .baseline import load_default_satellite_reference_baseline
from .contracts import (
    AppendageTemplate,
    AxisRatioBound,
    CandidateInteriorZoneAssignment,
    InteriorZoneDefinition,
    LikenessGateCheck,
    SatelliteLayoutCandidate,
    SatelliteLikenessReport,
    SatelliteReferenceBaseline,
)
from .selector import TaskTypeArchetypeSelector


class SatelliteLikenessGate:
    """
    Minimal rule-based skeleton for the ADR-0010 satellite likeness gate.
    """

    def __init__(
        self,
        baseline: Optional[SatelliteReferenceBaseline] = None,
    ) -> None:
        self.baseline = baseline or load_default_satellite_reference_baseline()
        self.selector = TaskTypeArchetypeSelector(baseline=self.baseline)

    def evaluate(
        self,
        candidate: SatelliteLayoutCandidate,
        *,
        expected_archetype_id: Optional[str] = None,
    ) -> SatelliteLikenessReport:
        checks: list[LikenessGateCheck] = []
        archetype = self.baseline.get_archetype(candidate.archetype_id)

        checks.append(
            self._check_archetype_match(
                candidate=candidate,
                archetype_found=archetype is not None,
                expected_archetype_id=expected_archetype_id,
            )
        )

        if archetype is None:
            return SatelliteLikenessReport(
                passed=False,
                candidate_archetype_id=str(candidate.archetype_id),
                expected_archetype_id=expected_archetype_id,
                checks=checks,
            )

        checks.append(
            self._check_bus_aspect_ratio_bounds(
                candidate=candidate,
                ratio_bounds=list(archetype.morphology.bus_aspect_ratio_bounds or []),
            )
        )
        checks.append(self._check_required_task_faces(candidate, archetype.morphology.required_task_faces()))
        checks.append(
            self._check_appendages_within_template_bounds(
                candidate=candidate,
                templates=archetype.morphology.appendage_template_by_id(),
            )
        )
        checks.append(
            self._check_interior_zone_assignments(
                candidate=candidate,
                interior_zones=list(archetype.morphology.interior_zone_schema or []),
            )
        )

        return SatelliteLikenessReport(
            passed=all(check.passed for check in checks),
            candidate_archetype_id=str(candidate.archetype_id),
            expected_archetype_id=expected_archetype_id,
            checks=checks,
        )

    def evaluate_for_task_type(
        self,
        candidate: SatelliteLayoutCandidate,
        *,
        task_type: str,
    ) -> SatelliteLikenessReport:
        archetype = self.selector.select(task_type)
        return self.evaluate(candidate, expected_archetype_id=archetype.archetype_id)

    @staticmethod
    def _normalize_zone_category(value: str) -> str:
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

    @staticmethod
    def _axis_index(axis: str) -> int:
        return {"x": 0, "y": 1, "z": 2}[str(axis).strip().lower()]

    @staticmethod
    def _check_archetype_match(
        *,
        candidate: SatelliteLayoutCandidate,
        archetype_found: bool,
        expected_archetype_id: Optional[str],
    ) -> LikenessGateCheck:
        if not archetype_found:
            return LikenessGateCheck(
                rule_id="archetype_match",
                passed=False,
                message="candidate archetype is not registered in the public reference baseline",
                details={"candidate_archetype_id": str(candidate.archetype_id)},
            )

        if expected_archetype_id and str(candidate.archetype_id) != str(expected_archetype_id):
            return LikenessGateCheck(
                rule_id="archetype_match",
                passed=False,
                message="candidate archetype does not match the task-selected archetype",
                details={
                    "candidate_archetype_id": str(candidate.archetype_id),
                    "expected_archetype_id": str(expected_archetype_id),
                },
            )

        return LikenessGateCheck(
            rule_id="archetype_match",
            passed=True,
            message="candidate archetype is present and consistent with the selected archetype",
            details={
                "candidate_archetype_id": str(candidate.archetype_id),
                "expected_archetype_id": str(expected_archetype_id or candidate.archetype_id),
            },
        )

    @staticmethod
    def _check_required_task_faces(
        candidate: SatelliteLayoutCandidate,
        required_faces,
    ) -> LikenessGateCheck:
        required_pairs = {
            (str(task_face.semantic), str(task_face.face_id))
            for task_face in list(required_faces or [])
        }
        actual_pairs = {
            (str(task_face.semantic), str(task_face.face_id))
            for task_face in list(candidate.task_face_assignments or [])
        }
        missing_pairs = sorted(required_pairs - actual_pairs)

        if missing_pairs:
            return LikenessGateCheck(
                rule_id="task_faces_present",
                passed=False,
                message="candidate is missing required task-face semantics from the archetype grammar",
                details={"missing_task_faces": missing_pairs},
            )

        return LikenessGateCheck(
            rule_id="task_faces_present",
            passed=True,
            message="candidate carries all required task-face semantics for the archetype",
            details={"required_task_faces": sorted(required_pairs)},
        )

    @staticmethod
    def _check_bus_aspect_ratio_bounds(
        *,
        candidate: SatelliteLayoutCandidate,
        ratio_bounds: list[AxisRatioBound],
    ) -> LikenessGateCheck:
        if not ratio_bounds:
            return LikenessGateCheck(
                rule_id="bus_aspect_ratio_in_bounds",
                passed=True,
                message="archetype does not define bus aspect-ratio bounds for this minimal baseline",
                details={"checked_ratio_count": 0},
            )

        if candidate.bus_span_mm is None:
            return LikenessGateCheck(
                rule_id="bus_aspect_ratio_in_bounds",
                passed=True,
                message="candidate does not provide bus span for aspect-ratio evaluation in this minimal diagnostic mode",
                details={"checked_ratio_count": 0, "evaluation_mode": "empty_candidate"},
            )

        span = tuple(float(value) for value in candidate.bus_span_mm)
        violations: list[str] = []
        evaluated: list[dict[str, float | str]] = []
        for bound in list(ratio_bounds or []):
            numerator = span[SatelliteLikenessGate._axis_index(bound.numerator_axis)]
            denominator = span[SatelliteLikenessGate._axis_index(bound.denominator_axis)]
            ratio = float(numerator / denominator) if denominator > 0.0 else float("inf")
            evaluated.append(
                {
                    "ratio_id": f"{bound.numerator_axis}/{bound.denominator_axis}",
                    "ratio": float(ratio),
                    "min_ratio": float(bound.min_ratio),
                    "max_ratio": float(bound.max_ratio),
                }
            )
            if ratio < float(bound.min_ratio) or ratio > float(bound.max_ratio):
                violations.append(
                    f"{bound.numerator_axis}/{bound.denominator_axis}:{ratio:.4f}"
                    f" not_in [{float(bound.min_ratio):.4f},{float(bound.max_ratio):.4f}]"
                )

        if violations:
            return LikenessGateCheck(
                rule_id="bus_aspect_ratio_in_bounds",
                passed=False,
                message="candidate bus aspect ratios exceed the archetype morphology bounds",
                details={
                    "bus_span_mm": span,
                    "violations": violations,
                    "evaluated_ratios": evaluated,
                },
            )

        return LikenessGateCheck(
            rule_id="bus_aspect_ratio_in_bounds",
            passed=True,
            message="candidate bus aspect ratios stay within the archetype morphology bounds",
            details={"bus_span_mm": span, "evaluated_ratios": evaluated},
        )

    @staticmethod
    def _check_appendages_within_template_bounds(
        *,
        candidate: SatelliteLayoutCandidate,
        templates: dict[str, AppendageTemplate],
    ) -> LikenessGateCheck:
        violations: list[str] = []
        counts = Counter(str(appendage.template_id) for appendage in list(candidate.appendages or []))

        for template_id, count in counts.items():
            template = templates.get(template_id)
            if template is None:
                violations.append(f"unknown_template:{template_id}")
                continue
            if int(count) > int(template.max_instances):
                violations.append(
                    f"instance_limit_exceeded:{template_id}:{count}>{template.max_instances}"
                )

        for appendage in list(candidate.appendages or []):
            template = templates.get(str(appendage.template_id))
            if template is None:
                continue
            if str(appendage.host_face) not in set(template.allowed_faces):
                violations.append(
                    f"host_face_out_of_bounds:{appendage.template_id}:{appendage.host_face}"
                )
            if any(
                float(actual) > float(allowed)
                for actual, allowed in zip(appendage.span_mm, template.max_span_mm)
            ):
                violations.append(
                    f"span_out_of_bounds:{appendage.template_id}:{tuple(appendage.span_mm)}"
                )
            if float(appendage.offset_mm) > float(template.max_offset_mm):
                violations.append(
                    f"offset_out_of_bounds:{appendage.template_id}:{appendage.offset_mm}>{template.max_offset_mm}"
                )

        if violations:
            return LikenessGateCheck(
                rule_id="appendage_templates_in_bounds",
                passed=False,
                message="candidate appendages exceed the active archetype template bounds",
                details={"violations": violations},
            )

        return LikenessGateCheck(
            rule_id="appendage_templates_in_bounds",
            passed=True,
            message="candidate appendages stay within template face/count/span/offset bounds",
            details={"appendage_count": len(list(candidate.appendages or []))},
        )

    @staticmethod
    def _check_interior_zone_assignments(
        *,
        candidate: SatelliteLayoutCandidate,
        interior_zones: list[InteriorZoneDefinition],
    ) -> LikenessGateCheck:
        if not interior_zones:
            return LikenessGateCheck(
                rule_id="interior_zone_assignments_in_bounds",
                passed=True,
                message="archetype does not define interior zones for this minimal baseline",
                details={"assignment_count": 0},
            )

        zone_map = {
            str(zone.zone_id): zone
            for zone in list(interior_zones or [])
            if str(zone.zone_id).strip()
        }
        assignments = list(candidate.interior_zone_assignments or [])
        if not assignments and not list(
            dict(candidate.metadata or {}).get("interior_zone_unassigned_components", []) or []
        ):
            return LikenessGateCheck(
                rule_id="interior_zone_assignments_in_bounds",
                passed=True,
                message="candidate does not provide interior zone assignments in this minimal diagnostic mode",
                details={"assignment_count": 0, "evaluation_mode": "empty_candidate"},
            )

        violations: list[str] = []
        for assignment in assignments:
            violation = SatelliteLikenessGate._validate_interior_zone_assignment(
                assignment=assignment,
                zone_map=zone_map,
            )
            if violation:
                violations.append(violation)

        unassigned_components = [
            item
            for item in list(
                dict(candidate.metadata or {}).get("interior_zone_unassigned_components", []) or []
            )
            if str(item.get("component_category", "") or "").strip()
        ]
        if unassigned_components:
            for item in unassigned_components:
                violations.append(
                    "unassigned_component:"
                    + str(item.get("component_id", "") or "")
                    + ":"
                    + str(item.get("component_category", "") or "")
                )

        if violations:
            return LikenessGateCheck(
                rule_id="interior_zone_assignments_in_bounds",
                passed=False,
                message="candidate interior-zone assignments violate the archetype zone-category grammar",
                details={
                    "violations": violations,
                    "assignment_count": len(assignments),
                    "unassigned_components": list(unassigned_components),
                },
            )

        return LikenessGateCheck(
            rule_id="interior_zone_assignments_in_bounds",
            passed=True,
            message="candidate interior-zone assignments stay within the archetype zone-category grammar",
            details={"assignment_count": len(assignments)},
        )

    @staticmethod
    def _validate_interior_zone_assignment(
        *,
        assignment: CandidateInteriorZoneAssignment,
        zone_map: dict[str, InteriorZoneDefinition],
    ) -> str:
        zone_id = str(assignment.zone_id or "").strip()
        component_category = SatelliteLikenessGate._normalize_zone_category(
            str(assignment.component_category or "")
        )
        component_id = str(assignment.component_id or "").strip()
        zone = zone_map.get(zone_id)
        if zone is None:
            return f"unknown_zone:{zone_id}:{component_id}"

        allowed_categories = {
            SatelliteLikenessGate._normalize_zone_category(str(item or ""))
            for item in list(zone.allowed_categories or [])
            if str(item or "").strip()
        }
        if component_category not in allowed_categories:
            return f"category_out_of_zone:{zone_id}:{component_id}:{component_category}"
        return ""
