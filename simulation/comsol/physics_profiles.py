from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from simulation.comsol.field_registry import (
    COMSOL_FIELD_REGISTRY_VERSION,
    build_field_registry_manifest,
)
from simulation.comsol.metric_contracts import (
    COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
    build_simulation_metric_unit_contract,
)

COMSOL_CONTRACT_BUNDLE_VERSION = "2.0"
COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION = "2.0"
COMSOL_PROFILE_AUDIT_DIGEST_VERSION = "2.0"

PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL = "thermal_static_canonical"
PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL = "thermal_orbital_canonical"
PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL = "electro_thermo_structural_canonical"

DEFAULT_COMSOL_PHYSICS_PROFILE = PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL

REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE = "official_interface_thin_slice"
REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK = "network_solver_fallback"
REALNESS_LEVEL_PROXY_FALLBACK = "proxy_fallback"
REALNESS_LEVEL_DISABLED = "disabled"
REALNESS_LEVEL_SETUP_INCOMPLETE = "setup_incomplete"

CONTRACT_BUNDLE_CLAIM_FIELDS = (
    "requested_physics_profile",
    "physics_profile",
    "requested_profile_release_grade",
    "effective_profile_release_grade",
    "canonical_request_preserved",
    "thermal_realness_level",
    "structural_realness_level",
    "power_realness_level",
    "orbital_thermal_loads_available",
    "degradation_reason",
    "thermal_requested",
    "thermal_setup_ok",
    "thermal_study_entered",
    "thermal_study_solved",
    "structural_requested",
    "structural_enabled",
    "structural_setup_ok",
    "structural_study_entered",
    "structural_study_solved",
    "power_requested",
    "power_comsol_enabled",
    "power_setup_ok",
    "power_study_entered",
    "power_study_solved",
    "power_network_enabled",
    "coupled_requested",
    "coupled_setup_ok",
    "coupled_study_entered",
    "coupled_study_solved",
    "requested_profile_interfaces",
    "effective_profile_interfaces",
)

CONTRACT_BUNDLE_PROMOTED_FIELDS = (
    "requested_physics_profile",
    "physics_profile",
    "requested_profile_release_grade",
    "effective_profile_release_grade",
    "canonical_request_preserved",
    "thermal_realness_level",
    "structural_realness_level",
    "power_realness_level",
    "degradation_reason",
    "thermal_study_solved",
    "structural_study_solved",
    "power_study_solved",
    "coupled_study_solved",
)


@dataclass(frozen=True)
class ComsolPhysicsProfile:
    name: str
    label: str
    official_interfaces: tuple[str, ...]
    release_grade: bool
    description: str


_PHYSICS_PROFILES: Dict[str, ComsolPhysicsProfile] = {
    PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL: ComsolPhysicsProfile(
        name=PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
        label="Thermal Static Canonical",
        official_interfaces=(
            "Heat Transfer in Solids",
            "Heat Transfer with Surface-to-Surface Radiation",
            "Stationary Study",
        ),
        release_grade=True,
        description="Static thermal profile anchored to official COMSOL heat-transfer interfaces.",
    ),
    PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL: ComsolPhysicsProfile(
        name=PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
        label="Thermal Orbital Canonical",
        official_interfaces=(
            "Heat Transfer in Solids",
            "Heat Transfer with Surface-to-Surface Radiation",
            "Orbital Thermal Loads",
            "Stationary Study",
        ),
        release_grade=True,
        description="Orbital thermal profile with explicit orbital thermal loads support.",
    ),
    PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL: ComsolPhysicsProfile(
        name=PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
        label="Electro Thermo Structural Canonical",
        official_interfaces=(
            "Heat Transfer in Solids",
            "Electric Currents",
            "Solid Mechanics",
            "Joule Heating",
            "Thermal Expansion",
            "Stationary Study",
            "Eigenfrequency Study",
        ),
        release_grade=True,
        description="Multiphysics canonical profile spanning thermal, electrical, and structural fields.",
    ),
}


def normalize_physics_profile(value: Any) -> str:
    profile_name = str(value or "").strip().lower()
    if profile_name in _PHYSICS_PROFILES:
        return profile_name
    return DEFAULT_COMSOL_PHYSICS_PROFILE


def get_physics_profile(name: Any) -> ComsolPhysicsProfile:
    normalized = normalize_physics_profile(name)
    return _PHYSICS_PROFILES[normalized]


def iter_physics_profiles() -> tuple[ComsolPhysicsProfile, ...]:
    return tuple(_PHYSICS_PROFILES.values())


def build_physics_profile_manifest() -> Dict[str, Dict[str, Any]]:
    return {
        name: {
            "label": profile.label,
            "official_interfaces": list(profile.official_interfaces),
            "release_grade": bool(profile.release_grade),
            "description": profile.description,
        }
        for name, profile in _PHYSICS_PROFILES.items()
    }


def _resolve_branch_realness(
    *,
    enabled: bool,
    setup_ok: Optional[bool],
    fallback_level: str,
) -> str:
    if not bool(enabled):
        return REALNESS_LEVEL_DISABLED
    if setup_ok is None:
        return REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    if bool(setup_ok):
        return REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    return str(fallback_level)


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def build_source_claim(
    *,
    requested_profile: Any,
    orbital_thermal_loads_available: bool = False,
    structural_enabled: bool = False,
    structural_setup_ok: Optional[bool] = None,
    power_comsol_enabled: bool = False,
    power_setup_ok: Optional[bool] = None,
    power_network_enabled: bool = False,
    thermal_runtime: Mapping[str, Any] | None = None,
    structural_runtime: Mapping[str, Any] | None = None,
    power_runtime: Mapping[str, Any] | None = None,
    coupled_runtime: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    requested_name = normalize_physics_profile(requested_profile)
    requested = get_physics_profile(requested_name)
    thermal_rt = dict(thermal_runtime or {})
    struct_rt = dict(structural_runtime or {})
    power_rt = dict(power_runtime or {})
    coupled_rt = dict(coupled_runtime or {})

    degradation_reasons: list[str] = []
    thermal_realness_level = REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    effective_profile_name = requested.name
    effective_profile_release_grade = bool(requested.release_grade)

    if requested.name == PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL and not bool(
        orbital_thermal_loads_available
    ):
        degradation_reasons.append(
            "Orbital Thermal Loads unavailable; fallback to thermal_static_canonical"
        )
        effective_profile_name = PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL
        effective_profile_release_grade = bool(
            get_physics_profile(effective_profile_name).release_grade
        )

    structural_realness_level = _resolve_branch_realness(
        enabled=bool(structural_enabled),
        setup_ok=struct_rt.get("setup_ok", structural_setup_ok),
        fallback_level=REALNESS_LEVEL_PROXY_FALLBACK,
    )
    power_realness_level = _resolve_branch_realness(
        enabled=bool(power_comsol_enabled),
        setup_ok=power_rt.get("setup_ok", power_setup_ok),
        fallback_level=(
            REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK
            if bool(power_network_enabled)
            else REALNESS_LEVEL_SETUP_INCOMPLETE
        ),
    )

    effective = get_physics_profile(effective_profile_name)
    return {
        "requested_physics_profile": requested.name,
        "physics_profile": effective.name,
        "requested_profile_release_grade": bool(requested.release_grade),
        "effective_profile_release_grade": bool(effective_profile_release_grade),
        "canonical_request_preserved": bool(requested.name == effective.name),
        "thermal_realness_level": str(thermal_realness_level),
        "structural_realness_level": str(structural_realness_level),
        "power_realness_level": str(power_realness_level),
        "orbital_thermal_loads_available": bool(orbital_thermal_loads_available),
        "thermal_requested": True,
        "thermal_setup_ok": _bool_or_none(thermal_rt.get("setup_ok", True)),
        "thermal_study_entered": bool(thermal_rt.get("study_entered", False)),
        "thermal_study_solved": bool(thermal_rt.get("study_solved", False)),
        "structural_requested": bool(structural_enabled),
        "structural_enabled": bool(structural_enabled),
        "structural_setup_ok": _bool_or_none(struct_rt.get("setup_ok", structural_setup_ok)),
        "structural_study_entered": bool(struct_rt.get("study_entered", False)),
        "structural_study_solved": bool(struct_rt.get("study_solved", False)),
        "power_requested": bool(power_comsol_enabled),
        "power_comsol_enabled": bool(power_comsol_enabled),
        "power_setup_ok": _bool_or_none(power_rt.get("setup_ok", power_setup_ok)),
        "power_study_entered": bool(power_rt.get("study_entered", False)),
        "power_study_solved": bool(power_rt.get("study_solved", False)),
        "power_network_enabled": bool(power_network_enabled),
        "coupled_requested": bool(coupled_rt.get("enabled", False)),
        "coupled_setup_ok": _bool_or_none(coupled_rt.get("setup_ok", None)),
        "coupled_study_entered": bool(coupled_rt.get("study_entered", False)),
        "coupled_study_solved": bool(coupled_rt.get("study_solved", False)),
        "degradation_reason": "; ".join(str(item) for item in degradation_reasons if str(item).strip()),
        "requested_profile_interfaces": list(requested.official_interfaces),
        "effective_profile_interfaces": list(effective.official_interfaces),
    }


def build_profile_audit_digest(claim: Mapping[str, Any] | None) -> Dict[str, Any]:
    payload = dict(claim or {})
    requested_profile = str(payload.get("requested_physics_profile", "") or "")
    effective_profile = str(payload.get("physics_profile", "") or "")
    degradation_reason = str(payload.get("degradation_reason", "") or "")
    requested_release_grade = bool(payload.get("requested_profile_release_grade", False))
    effective_release_grade = bool(payload.get("effective_profile_release_grade", False))
    canonical_request_preserved = bool(
        payload.get(
            "canonical_request_preserved",
            bool(
                requested_profile
                and effective_profile
                and requested_profile == effective_profile
                and requested_release_grade
                and effective_release_grade
            ),
        )
    )
    return {
        "requested_physics_profile": requested_profile,
        "physics_profile": effective_profile,
        "requested_profile_release_grade": requested_release_grade,
        "effective_profile_release_grade": effective_release_grade,
        "canonical_request_preserved": canonical_request_preserved,
        "canonical_request_degraded": bool(
            requested_release_grade
            and (
                (requested_profile and effective_profile and requested_profile != effective_profile)
                or not effective_release_grade
            )
        ),
        "release_grade_blocked": bool(requested_release_grade and not effective_release_grade),
        "thermal_realness_level": str(payload.get("thermal_realness_level", "") or ""),
        "structural_realness_level": str(payload.get("structural_realness_level", "") or ""),
        "power_realness_level": str(payload.get("power_realness_level", "") or ""),
        "orbital_thermal_loads_available": bool(payload.get("orbital_thermal_loads_available", False)),
        "has_degradation": bool(degradation_reason.strip()),
        "degradation_reason": degradation_reason,
        "requested_profile_interfaces": list(payload.get("requested_profile_interfaces", []) or []),
        "effective_profile_interfaces": list(payload.get("effective_profile_interfaces", []) or []),
    }


def build_contract_bundle(
    claim: Mapping[str, Any] | None,
    *,
    field_registry_version: str = COMSOL_FIELD_REGISTRY_VERSION,
    physics_profile_contract_version: str = COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
    profile_audit_digest_version: str = COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
    simulation_metric_unit_contract_version: str = COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
) -> Dict[str, Any]:
    payload = dict(claim or {})
    canonical_request_preserved = bool(
        payload.get(
            "canonical_request_preserved",
            bool(
                str(payload.get("requested_physics_profile", "") or "")
                and str(payload.get("requested_physics_profile", "") or "")
                == str(payload.get("physics_profile", "") or "")
                and bool(payload.get("requested_profile_release_grade", False))
                and bool(payload.get("effective_profile_release_grade", False))
            ),
        )
    )
    return {
        "bundle_version": COMSOL_CONTRACT_BUNDLE_VERSION,
        "requested_physics_profile": str(payload.get("requested_physics_profile", "") or ""),
        "physics_profile": str(payload.get("physics_profile", "") or ""),
        "requested_profile_release_grade": bool(payload.get("requested_profile_release_grade", False)),
        "effective_profile_release_grade": bool(payload.get("effective_profile_release_grade", False)),
        "canonical_request_preserved": canonical_request_preserved,
        "thermal_realness_level": str(payload.get("thermal_realness_level", "") or ""),
        "structural_realness_level": str(payload.get("structural_realness_level", "") or ""),
        "power_realness_level": str(payload.get("power_realness_level", "") or ""),
        "orbital_thermal_loads_available": bool(payload.get("orbital_thermal_loads_available", False)),
        "thermal_requested": bool(payload.get("thermal_requested", False)),
        "thermal_setup_ok": _bool_or_none(payload.get("thermal_setup_ok")),
        "thermal_study_entered": bool(payload.get("thermal_study_entered", False)),
        "thermal_study_solved": bool(payload.get("thermal_study_solved", False)),
        "structural_requested": bool(payload.get("structural_requested", False)),
        "structural_enabled": bool(payload.get("structural_enabled", False)),
        "structural_setup_ok": _bool_or_none(payload.get("structural_setup_ok")),
        "structural_study_entered": bool(payload.get("structural_study_entered", False)),
        "structural_study_solved": bool(payload.get("structural_study_solved", False)),
        "power_requested": bool(payload.get("power_requested", False)),
        "power_comsol_enabled": bool(payload.get("power_comsol_enabled", False)),
        "power_setup_ok": _bool_or_none(payload.get("power_setup_ok")),
        "power_study_entered": bool(payload.get("power_study_entered", False)),
        "power_study_solved": bool(payload.get("power_study_solved", False)),
        "power_network_enabled": bool(payload.get("power_network_enabled", False)),
        "coupled_requested": bool(payload.get("coupled_requested", False)),
        "coupled_setup_ok": _bool_or_none(payload.get("coupled_setup_ok")),
        "coupled_study_entered": bool(payload.get("coupled_study_entered", False)),
        "coupled_study_solved": bool(payload.get("coupled_study_solved", False)),
        "degradation_reason": str(payload.get("degradation_reason", "") or ""),
        "requested_profile_interfaces": list(payload.get("requested_profile_interfaces", []) or []),
        "effective_profile_interfaces": list(payload.get("effective_profile_interfaces", []) or []),
        "profile_audit_digest": build_profile_audit_digest(payload),
        "contract_versions": {
            "field_export_registry": str(field_registry_version or COMSOL_FIELD_REGISTRY_VERSION),
            "physics_profile_contract": str(
                physics_profile_contract_version or COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
            ),
            "profile_audit_digest": str(
                profile_audit_digest_version or COMSOL_PROFILE_AUDIT_DIGEST_VERSION
            ),
            "simulation_metric_unit_contract": str(
                simulation_metric_unit_contract_version
                or COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
            ),
        },
        "contract_sections": {
            "source_claim": "source_claim",
            "field_export_registry": "field_export_registry",
            "physics_profile_contract": "physics_profile_contract",
            "profile_audit_digest": "profile_audit_digest",
            "simulation_metric_unit_contract": "simulation_metric_unit_contract",
        },
    }


def resolve_contract_bundle(
    payload: Mapping[str, Any] | None,
    *,
    claim: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    raw = dict(payload or {})
    merged_claim = dict(claim or raw.get("source_claim", {}) or {})
    for key in CONTRACT_BUNDLE_CLAIM_FIELDS:
        if key not in merged_claim and key in raw:
            merged_claim[key] = raw.get(key)

    bundle = build_contract_bundle(
        merged_claim,
        field_registry_version=str(
            raw.get("field_export_registry_version", COMSOL_FIELD_REGISTRY_VERSION)
            or COMSOL_FIELD_REGISTRY_VERSION
        ),
        physics_profile_contract_version=str(
            raw.get(
                "physics_profile_contract_version",
                COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
            )
            or COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
        ),
        profile_audit_digest_version=str(
            raw.get("profile_audit_digest_version", COMSOL_PROFILE_AUDIT_DIGEST_VERSION)
            or COMSOL_PROFILE_AUDIT_DIGEST_VERSION
        ),
        simulation_metric_unit_contract_version=str(
            raw.get(
                "simulation_metric_unit_contract_version",
                COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
            )
            or COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
        ),
    )

    existing_bundle = dict(raw.get("contract_bundle", {}) or {})
    if not existing_bundle:
        return bundle

    merged_bundle = dict(bundle)
    merged_bundle.update(existing_bundle)
    merged_bundle["bundle_version"] = str(
        existing_bundle.get("bundle_version", COMSOL_CONTRACT_BUNDLE_VERSION)
        or COMSOL_CONTRACT_BUNDLE_VERSION
    )
    merged_bundle["profile_audit_digest"] = dict(
        existing_bundle.get("profile_audit_digest", bundle.get("profile_audit_digest", {})) or {}
    )
    merged_bundle["contract_versions"] = {
        **dict(bundle.get("contract_versions", {}) or {}),
        **dict(existing_bundle.get("contract_versions", {}) or {}),
    }
    merged_bundle["contract_sections"] = {
        **dict(bundle.get("contract_sections", {}) or {}),
        **dict(existing_bundle.get("contract_sections", {}) or {}),
    }
    return merged_bundle


def attach_contract_bundle(
    payload: Mapping[str, Any] | None,
    contract_bundle: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    output = dict(payload or {})
    bundle = dict(contract_bundle or {})
    if not bundle:
        bundle = build_contract_bundle({})

    versions = dict(bundle.get("contract_versions", {}) or {})
    output["contract_bundle_version"] = str(
        bundle.get("bundle_version", COMSOL_CONTRACT_BUNDLE_VERSION)
        or COMSOL_CONTRACT_BUNDLE_VERSION
    )
    output["contract_bundle"] = dict(bundle)
    output["field_export_registry_version"] = str(
        versions.get("field_export_registry", COMSOL_FIELD_REGISTRY_VERSION)
        or COMSOL_FIELD_REGISTRY_VERSION
    )
    output["physics_profile_contract_version"] = str(
        versions.get("physics_profile_contract", COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION)
        or COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    )
    output["profile_audit_digest_version"] = str(
        versions.get("profile_audit_digest", COMSOL_PROFILE_AUDIT_DIGEST_VERSION)
        or COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    )
    output["simulation_metric_unit_contract_version"] = str(
        versions.get(
            "simulation_metric_unit_contract",
            COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
        )
        or COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    output["profile_audit_digest"] = dict(bundle.get("profile_audit_digest", {}) or {})

    for key in CONTRACT_BUNDLE_PROMOTED_FIELDS:
        output[key] = bundle.get(key)
    return output


def build_contract_defaults(
    *,
    field_export_registry: Mapping[str, Any] | None = None,
    physics_profile_contract: Mapping[str, Any] | None = None,
    simulation_metric_unit_contract: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "field_export_registry_version": COMSOL_FIELD_REGISTRY_VERSION,
        "field_export_registry": dict(field_export_registry or build_field_registry_manifest()),
        "physics_profile_contract_version": COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
        "physics_profile_contract": dict(
            physics_profile_contract or build_physics_profile_manifest()
        ),
        "profile_audit_digest_version": COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
        "profile_audit_digest": {},
        "simulation_metric_unit_contract_version": (
            COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
        ),
        "simulation_metric_unit_contract": dict(
            simulation_metric_unit_contract or build_simulation_metric_unit_contract()
        ),
    }


def materialize_contract_payload(
    payload: Mapping[str, Any] | None,
    *,
    claim: Mapping[str, Any] | None = None,
    contract_bundle: Mapping[str, Any] | None = None,
    source_payload: Mapping[str, Any] | None = None,
    field_export_registry: Mapping[str, Any] | None = None,
    physics_profile_contract: Mapping[str, Any] | None = None,
    simulation_metric_unit_contract: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    defaults = build_contract_defaults(
        field_export_registry=field_export_registry,
        physics_profile_contract=physics_profile_contract,
        simulation_metric_unit_contract=simulation_metric_unit_contract,
    )
    output = dict(payload or {})
    source = dict(source_payload or {})

    for key in CONTRACT_BUNDLE_CLAIM_FIELDS:
        if key in source:
            output[key] = source.get(key)

    for key in (
        "field_export_registry_version",
        "field_export_registry",
        "physics_profile_contract_version",
        "physics_profile_contract",
        "profile_audit_digest_version",
        "profile_audit_digest",
        "simulation_metric_unit_contract_version",
        "simulation_metric_unit_contract",
    ):
        if key in source:
            output[key] = source.get(key)

    for key in (
        "field_export_registry_version",
        "physics_profile_contract_version",
        "profile_audit_digest_version",
        "simulation_metric_unit_contract_version",
    ):
        if not str(output.get(key, "") or "").strip():
            output[key] = defaults[key]

    for key in (
        "field_export_registry",
        "physics_profile_contract",
        "simulation_metric_unit_contract",
    ):
        if not dict(output.get(key, {}) or {}):
            output[key] = dict(defaults[key])

    explicit_bundle = dict(contract_bundle or {})
    if explicit_bundle:
        bundle_payload = dict(output)
        bundle_payload["contract_bundle"] = dict(explicit_bundle)
        bundle = resolve_contract_bundle(bundle_payload, claim=claim)
    elif source_payload is not None:
        bundle_payload = dict(output)
        bundle_payload.pop("contract_bundle", None)
        bundle = resolve_contract_bundle(bundle_payload, claim=claim)
    else:
        bundle = resolve_contract_bundle(output, claim=claim)
    return attach_contract_bundle(output, bundle)
