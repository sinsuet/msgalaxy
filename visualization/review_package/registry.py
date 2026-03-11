"""
Registry definitions for iteration review packages.
"""

from __future__ import annotations

from typing import Any, Dict

from .contracts import (
    ColorSpec,
    MetricSpec,
    OperatorFamilySpec,
    ReviewFieldCaseGateContract,
    ReviewProfileContract,
    ReviewRegistryVersions,
    UnitSpec,
)


REGISTRY_VERSIONS = ReviewRegistryVersions()


METRIC_REGISTRY: Dict[str, MetricSpec] = {
    "best_cv": MetricSpec(
        key="best_cv",
        label="Best CV",
        description="Constraint violation score reported by the optimizer.",
        direction="minimize",
        source="layout_snapshot",
        unit_key="unitless",
        category="optimization",
    ),
    "max_temp": MetricSpec(
        key="max_temp",
        label="Max Temperature",
        description="Maximum temperature across the current state.",
        direction="minimize",
        source="thermal",
        unit_key="temperature_celsius",
        category="thermal",
    ),
    "temp_margin": MetricSpec(
        key="temp_margin",
        label="Temperature Margin",
        description="Remaining temperature margin to the target or limit.",
        direction="maximize",
        source="thermal",
        unit_key="temperature_kelvin",
        category="thermal",
    ),
    "max_displacement": MetricSpec(
        key="max_displacement",
        label="Max Displacement",
        description="Maximum structural displacement.",
        direction="minimize",
        source="structural",
        unit_key="length_mm",
        category="structural",
    ),
    "max_stress": MetricSpec(
        key="max_stress",
        label="Max Von Mises Stress",
        description="Maximum structural stress.",
        direction="minimize",
        source="structural",
        unit_key="stress_mpa",
        category="structural",
        aliases=["max_von_mises"],
    ),
    "min_clearance": MetricSpec(
        key="min_clearance",
        label="Min Clearance",
        description="Minimum component clearance.",
        direction="maximize",
        source="geometry",
        unit_key="length_mm",
        category="geometry",
    ),
    "num_collisions": MetricSpec(
        key="num_collisions",
        label="Collision Count",
        description="Number of AABB collision violations.",
        direction="minimize",
        source="geometry",
        unit_key="unitless",
        category="geometry",
    ),
    "boundary_violation": MetricSpec(
        key="boundary_violation",
        label="Boundary Violation",
        description="Envelope boundary violation amount.",
        direction="minimize",
        source="geometry",
        unit_key="length_mm",
        category="geometry",
    ),
    "cg_offset": MetricSpec(
        key="cg_offset",
        label="CG Offset",
        description="Center-of-gravity offset from target.",
        direction="minimize",
        source="mass_property",
        unit_key="length_mm",
        category="mass_property",
    ),
    "power_margin": MetricSpec(
        key="power_margin",
        label="Power Margin",
        description="Available power margin after loads.",
        direction="maximize",
        source="power",
        unit_key="percent",
        category="power",
    ),
    "voltage_drop": MetricSpec(
        key="voltage_drop",
        label="Voltage Drop",
        description="Estimated voltage drop along the power path.",
        direction="minimize",
        source="power",
        unit_key="electric_potential_v",
        category="power",
    ),
    "safety_factor": MetricSpec(
        key="safety_factor",
        label="Safety Factor",
        description="Structural safety factor.",
        direction="maximize",
        source="structural",
        unit_key="unitless",
        category="structural",
    ),
    "first_modal_freq": MetricSpec(
        key="first_modal_freq",
        label="First Modal Frequency",
        description="First structural modal frequency.",
        direction="maximize",
        source="structural",
        unit_key="frequency_hz",
        category="structural",
    ),
    "mission_keepout_violation": MetricSpec(
        key="mission_keepout_violation",
        label="Mission Keepout Violation",
        description="Mission keepout violation count or magnitude.",
        direction="minimize",
        source="mission",
        unit_key="unitless",
        category="mission",
    ),
    "packing_efficiency": MetricSpec(
        key="packing_efficiency",
        label="Fill Ratio",
        description="Occupied volume ratio inside the envelope.",
        direction="maximize",
        source="geometry",
        unit_key="unitless",
        category="geometry",
        aliases=["fill_ratio"],
    ),
    "rule_violation_count": MetricSpec(
        key="rule_violation_count",
        label="Rule Violation Count",
        description="Count of violated placement or review rules.",
        direction="minimize",
        source="review",
        unit_key="unitless",
        category="review",
    ),
}


UNIT_REGISTRY: Dict[str, UnitSpec] = {
    "unitless": UnitSpec(
        key="unitless",
        symbol="",
        quantity="dimensionless",
        description="Dimensionless scalar.",
    ),
    "temperature_kelvin": UnitSpec(
        key="temperature_kelvin",
        symbol="K",
        quantity="temperature",
        description="Kelvin.",
    ),
    "temperature_celsius": UnitSpec(
        key="temperature_celsius",
        symbol="degC",
        quantity="temperature",
        description="Degree Celsius.",
    ),
    "length_mm": UnitSpec(
        key="length_mm",
        symbol="mm",
        quantity="length",
        description="Millimeter.",
    ),
    "stress_pa": UnitSpec(
        key="stress_pa",
        symbol="Pa",
        quantity="stress",
        description="Pascal.",
    ),
    "stress_mpa": UnitSpec(
        key="stress_mpa",
        symbol="MPa",
        quantity="stress",
        description="Megapascal.",
    ),
    "frequency_hz": UnitSpec(
        key="frequency_hz",
        symbol="Hz",
        quantity="frequency",
        description="Hertz.",
    ),
    "electric_potential_v": UnitSpec(
        key="electric_potential_v",
        symbol="V",
        quantity="electric_potential",
        description="Volt.",
    ),
    "percent": UnitSpec(
        key="percent",
        symbol="%",
        quantity="ratio",
        description="Percent.",
    ),
}


COLOR_REGISTRY: Dict[str, ColorSpec] = {
    "temperature_field": ColorSpec(
        key="temperature_field",
        label="Temperature Field",
        colormap="inferno",
        unit_key="temperature_kelvin",
        default_min=260.0,
        default_max=360.0,
        range_source="registry_or_render_manifest",
        show_colorbar=True,
        show_title=False,
        source_claim_required=True,
    ),
    "displacement_field": ColorSpec(
        key="displacement_field",
        label="Displacement Field",
        colormap="viridis",
        unit_key="length_mm",
        default_min=0.0,
        default_max=5.0,
        range_source="registry_or_render_manifest",
        show_colorbar=True,
        show_title=False,
        source_claim_required=True,
    ),
    "stress_field": ColorSpec(
        key="stress_field",
        label="Stress Field",
        colormap="magma",
        unit_key="stress_mpa",
        default_min=0.0,
        default_max=250.0,
        range_source="registry_or_render_manifest",
        show_colorbar=True,
        show_title=False,
        source_claim_required=True,
    ),
    "geometry_overlay": ColorSpec(
        key="geometry_overlay",
        label="Geometry Overlay",
        colormap="shell_neutral",
        unit_key="unitless",
        range_source="style_contract",
        show_colorbar=False,
        show_title=False,
        source_claim_required=False,
    ),
    "before_layout_view": ColorSpec(
        key="before_layout_view",
        label="Before Layout View",
        colormap="shell_neutral",
        unit_key="unitless",
        range_source="style_contract",
        show_colorbar=False,
        show_title=False,
        source_claim_required=False,
    ),
    "after_layout_view": ColorSpec(
        key="after_layout_view",
        label="After Layout View",
        colormap="shell_neutral",
        unit_key="unitless",
        range_source="style_contract",
        show_colorbar=False,
        show_title=False,
        source_claim_required=False,
    ),
}


OPERATOR_FAMILY_REGISTRY: Dict[str, OperatorFamilySpec] = {
    "geometry": OperatorFamilySpec(
        key="geometry",
        label="geometry/panel placement",
        description="Geometry placement and panel-side layout moves.",
        display_order=10,
    ),
    "aperture": OperatorFamilySpec(
        key="aperture",
        label="aperture/payload alignment",
        description="Payload-facing aperture alignment and activation actions.",
        display_order=20,
    ),
    "thermal": OperatorFamilySpec(
        key="thermal",
        label="thermal management",
        description="Thermal relocation and thermal hardware actions.",
        display_order=30,
    ),
    "structural": OperatorFamilySpec(
        key="structural",
        label="structural support",
        description="Bracket, mount-site, and structural support actions.",
        display_order=40,
    ),
    "power": OperatorFamilySpec(
        key="power",
        label="power routing",
        description="Power-bus and routing optimization actions.",
        display_order=50,
    ),
    "mission": OperatorFamilySpec(
        key="mission",
        label="mission/fov protection",
        description="Mission keepout and FOV protection actions.",
        display_order=60,
    ),
    "other": OperatorFamilySpec(
        key="other",
        label="other/unmapped",
        description="Reserved fallback for unmapped actions.",
        display_order=999,
    ),
}


REVIEW_PROFILE_REGISTRY: Dict[str, ReviewProfileContract] = {
    "teacher_demo": ReviewProfileContract(
        name="teacher_demo",
        description="Teacher-facing full review package with stable shell-first visual contract.",
        package_level="full",
        shell_visual_policy="required",
        field_render_mode="prefer_linked",
        triptych_policy="prefer_existing",
        checkpoint_only=False,
        include_metric_deltas=True,
        allow_missing_artifacts=True,
        source_claim_required=True,
        unknown_v4_family_policy="error",
        field_case_gate=ReviewFieldCaseGateContract(
            mode="strict_when_linked",
            allowed_resolution_sources=[
                "explicit_step_index",
                "explicit_sequence",
                "dataset_summary_case_order",
            ],
            require_zero_defaulted=True,
            require_zero_unmapped=True,
            require_zero_incompatible_cases=True,
            require_zero_ambiguous_bindings=True,
        ),
        required_artifacts=[
            "review_manifest",
            "metrics_card",
            "before_layout_view",
            "after_layout_view",
            "temperature_field",
            "displacement_field",
            "stress_field",
            "geometry_overlay",
        ],
        optional_artifacts=["step_montage", "triptych", "timeline_montage", "dataset_overview"],
    ),
    "research_fast": ReviewProfileContract(
        name="research_fast",
        description="Checkpoint-oriented lightweight review package for search-time inspection.",
        package_level="lightweight",
        shell_visual_policy="preferred",
        field_render_mode="manifest_only",
        triptych_policy="skip",
        checkpoint_only=True,
        include_metric_deltas=True,
        allow_missing_artifacts=True,
        source_claim_required=True,
        unknown_v4_family_policy="warn",
        field_case_gate=ReviewFieldCaseGateContract(
            mode="off",
        ),
        required_artifacts=["review_manifest", "metrics_card"],
        optional_artifacts=[
            "before_layout_view",
            "after_layout_view",
            "temperature_field",
            "displacement_field",
            "stress_field",
            "geometry_overlay",
        ],
    ),
}


_METRIC_ALIAS_MAP: Dict[str, str] = {}
for _metric_key, _metric_spec in METRIC_REGISTRY.items():
    _METRIC_ALIAS_MAP[_metric_key] = _metric_key
    for _alias in _metric_spec.aliases:
        _METRIC_ALIAS_MAP[str(_alias)] = _metric_key


def get_metric_spec(metric_key: str) -> MetricSpec | None:
    canonical = _METRIC_ALIAS_MAP.get(str(metric_key or "").strip())
    if not canonical:
        return None
    return METRIC_REGISTRY.get(canonical)


def get_unit_spec(unit_key: str) -> UnitSpec | None:
    return UNIT_REGISTRY.get(str(unit_key or "").strip())


def get_color_spec(color_key: str) -> ColorSpec | None:
    return COLOR_REGISTRY.get(str(color_key or "").strip())


def get_operator_family_spec(family_key: str) -> OperatorFamilySpec | None:
    return OPERATOR_FAMILY_REGISTRY.get(str(family_key or "").strip())


def get_review_profile_contract(profile_name: str) -> ReviewProfileContract:
    normalized = str(profile_name or "").strip()
    if normalized not in REVIEW_PROFILE_REGISTRY:
        raise ValueError(f"Unsupported review profile: {profile_name}")
    return REVIEW_PROFILE_REGISTRY[normalized]


def build_registry_snapshot(profile_name: str) -> Dict[str, Any]:
    profile = get_review_profile_contract(profile_name)
    return {
        "schema_version": "iteration_review_registry_snapshot/v1",
        "versions": REGISTRY_VERSIONS.model_dump(mode="json"),
        "review_profile": profile.model_dump(mode="json"),
        "metric_registry": {
            key: spec.model_dump(mode="json")
            for key, spec in METRIC_REGISTRY.items()
        },
        "unit_registry": {
            key: spec.model_dump(mode="json")
            for key, spec in UNIT_REGISTRY.items()
        },
        "color_registry": {
            key: spec.model_dump(mode="json")
            for key, spec in COLOR_REGISTRY.items()
        },
        "operator_family_registry": {
            key: spec.model_dump(mode="json")
            for key, spec in OPERATOR_FAMILY_REGISTRY.items()
        },
    }
