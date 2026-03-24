from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.protocol import (
    ComponentGeometry,
    DesignState,
    Envelope,
    SimulationRequest,
    SimulationType,
    Vector3D,
)
from simulation.comsol.field_registry import (
    COMSOL_FIELD_REGISTRY_VERSION,
    build_field_registry_manifest,
    get_field_aliases,
    get_field_spec,
)
from simulation.comsol.metric_contracts import (
    COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
    build_simulation_metric_unit_contract,
)
from simulation.comsol.physics_profiles import (
    COMSOL_CONTRACT_BUNDLE_VERSION,
    COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
    COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
    DEFAULT_COMSOL_PHYSICS_PROFILE,
    PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
    PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
    PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
    REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK,
    REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
    build_contract_bundle,
    build_physics_profile_manifest,
    build_profile_audit_digest,
    build_source_claim,
    materialize_contract_payload,
    resolve_contract_bundle,
)
from simulation.comsol_driver import ComsolDriver


def _build_state() -> DesignState:
    comp = ComponentGeometry(
        id="CompA",
        position=Vector3D(x=8.0, y=0.0, z=0.0),
        dimensions=Vector3D(x=10.0, y=10.0, z=10.0),
        mass=2.0,
        power=12.0,
        category="payload",
    )
    return DesignState(
        iteration=1,
        components=[comp],
        envelope=Envelope(outer_size=Vector3D(x=100.0, y=100.0, z=100.0), origin="center"),
    )


def test_field_registry_minimal_slice_covers_required_fields():
    manifest = build_field_registry_manifest()

    assert manifest["temperature"]["key"] == "temperature"
    assert manifest["temperature"]["aliases"] == []
    assert manifest["temperature"]["expression"] == "T"
    assert manifest["temperature"]["unit"] == "K"
    assert manifest["displacement_magnitude"]["key"] == "displacement_magnitude"
    assert manifest["displacement_magnitude"]["aliases"] == ["displacement"]
    assert manifest["displacement_magnitude"]["expression"] == "solid.disp"
    assert manifest["displacement_magnitude"]["unit"] == "m"
    assert manifest["displacement_u"]["aliases"] == ["displacement_x"]
    assert manifest["displacement_v"]["aliases"] == ["displacement_y"]
    assert manifest["displacement_w"]["aliases"] == ["displacement_z"]
    assert manifest["displacement_u"]["expression_candidates"] == ["u", "solid.u"]
    assert manifest["displacement_v"]["expression_candidates"] == ["v", "solid.v"]
    assert manifest["displacement_w"]["expression_candidates"] == ["w", "solid.w"]
    assert manifest["von_mises"]["aliases"] == ["stress"]
    assert manifest["von_mises"]["expression"] == "solid.mises"
    assert manifest["von_mises"]["unit"] == "Pa"
    assert get_field_spec("stress").key == "von_mises"
    assert get_field_spec("displacement").key == "displacement_magnitude"
    assert get_field_spec("displacement_x").key == "displacement_u"
    assert get_field_spec("displacement_y").key == "displacement_v"
    assert get_field_spec("displacement_z").key == "displacement_w"
    assert get_field_aliases("von_mises") == ("stress",)
    assert COMSOL_FIELD_REGISTRY_VERSION == "1.0"


def test_physics_profile_manifest_covers_canonical_profiles_only():
    manifest = build_physics_profile_manifest()

    assert set(manifest) == {
        PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
        PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
        PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
    }
    assert manifest[PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL]["release_grade"] is True
    assert manifest[PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL]["release_grade"] is True
    assert manifest[PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL]["release_grade"] is True
    assert DEFAULT_COMSOL_PHYSICS_PROFILE == PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    assert COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION == "2.0"


def test_simulation_metric_unit_contract_covers_driver_summary_units():
    contract = build_simulation_metric_unit_contract()

    assert contract["max_temp"]["summary_unit"] == "degC"
    assert contract["max_temp"]["field_registry_key"] == "temperature"
    assert contract["max_stress"]["summary_unit"] == "MPa"
    assert contract["max_stress"]["field_registry_key"] == "von_mises"
    assert contract["max_displacement"]["summary_unit"] == "mm"
    assert contract["max_displacement"]["field_registry_key"] == "displacement_magnitude"
    assert contract["first_modal_freq"]["summary_unit"] == "Hz"
    assert contract["safety_factor"]["summary_unit"] == "1"
    assert COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION == "1.0"


def test_profile_audit_digest_marks_orbital_fallback_as_degraded():
    claim = build_source_claim(
        requested_profile=PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
        orbital_thermal_loads_available=False,
        structural_enabled=True,
        structural_setup_ok=True,
    )
    digest = build_profile_audit_digest(claim)

    assert claim["requested_physics_profile"] == PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL
    assert claim["physics_profile"] == PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL
    assert claim["requested_profile_release_grade"] is True
    assert claim["effective_profile_release_grade"] is True
    assert claim["canonical_request_preserved"] is False
    assert "fallback to thermal_static_canonical" in claim["degradation_reason"]
    assert digest["canonical_request_degraded"] is True
    assert digest["release_grade_blocked"] is False
    assert digest["has_degradation"] is True
    assert COMSOL_PROFILE_AUDIT_DIGEST_VERSION == "2.0"


def test_contract_bundle_summarizes_versions_and_payload_refs():
    claim = build_source_claim(
        requested_profile=PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
        structural_enabled=True,
        structural_setup_ok=True,
        power_comsol_enabled=True,
        power_setup_ok=False,
        power_network_enabled=True,
    )

    bundle = build_contract_bundle(claim)

    assert bundle["bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert bundle["physics_profile"] == PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    assert bundle["requested_profile_release_grade"] is True
    assert bundle["effective_profile_release_grade"] is True
    assert bundle["canonical_request_preserved"] is True
    assert bundle["power_realness_level"] == REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK
    assert bundle["profile_audit_digest"]["canonical_request_degraded"] is False
    assert bundle["contract_versions"]["field_export_registry"] == COMSOL_FIELD_REGISTRY_VERSION
    assert (
        bundle["contract_versions"]["simulation_metric_unit_contract"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert bundle["contract_sections"]["source_claim"] == "source_claim"
    assert bundle["contract_sections"]["physics_profile_contract"] == "physics_profile_contract"


def test_resolve_contract_bundle_backfills_partial_source_claim_from_payload_top_level():
    bundle = resolve_contract_bundle(
        {
            "source_claim": {
                "requested_physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
                "physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
                "requested_profile_release_grade": True,
                "effective_profile_release_grade": True,
                "canonical_request_preserved": True,
                "thermal_realness_level": REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
            },
            "structural_realness_level": REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
            "power_realness_level": REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK,
            "degradation_reason": "fixture degradation",
        }
    )

    assert bundle["bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert bundle["structural_realness_level"] == REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    assert bundle["power_realness_level"] == REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK
    assert bundle["degradation_reason"] == "fixture degradation"


def test_materialize_contract_payload_backfills_default_contract_sections():
    payload = materialize_contract_payload(
        {
            "source_claim": {
                "requested_physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
                "physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
                "requested_profile_release_grade": True,
                "effective_profile_release_grade": True,
                "canonical_request_preserved": True,
                "thermal_realness_level": REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
            }
        }
    )

    assert payload["contract_bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert payload["field_export_registry_version"] == COMSOL_FIELD_REGISTRY_VERSION
    assert payload["physics_profile_contract_version"] == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    assert payload["profile_audit_digest_version"] == COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    assert (
        payload["simulation_metric_unit_contract_version"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert payload["field_export_registry"]["temperature"]["unit"] == "K"
    assert payload["physics_profile_contract"][
        PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    ]["release_grade"] is True
    assert payload["simulation_metric_unit_contract"]["max_stress"]["summary_unit"] == "MPa"
    assert payload["contract_bundle"]["physics_profile"] == PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL


def test_materialize_contract_payload_prefers_source_payload_for_promoted_profile_fields():
    payload = materialize_contract_payload(
        {
            "physics_profile": "",
            "structural_realness_level": "",
        },
        claim={
            "requested_physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
            "physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
            "requested_profile_release_grade": True,
            "effective_profile_release_grade": True,
            "canonical_request_preserved": True,
            "thermal_realness_level": REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
        },
        source_payload={
            "physics_profile": PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
            "structural_realness_level": REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE,
            "power_realness_level": REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK,
            "degradation_reason": "source payload truth",
        },
    )

    assert payload["physics_profile"] == PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL
    assert payload["structural_realness_level"] == REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    assert payload["power_realness_level"] == REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK
    assert payload["contract_bundle"]["degradation_reason"] == "source payload truth"


def test_canonical_thermal_path_is_enabled_by_default():
    driver = ComsolDriver(
        config={
            "physics_profile": PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
        }
    )

    assert driver._uses_canonical_thermal_path() is True
    assert driver._heat_source_power_density_expression(12.5) == "12.5[W/m^3]"


def test_run_dynamic_simulation_heat_binding_failure_includes_canonical_source_claim():
    driver = ComsolDriver(
        config={
            "physics_profile": PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
            "save_mph_on_failure": False,
            "enable_structural_real": True,
            "enable_power_comsol_real": True,
            "enable_power_network_real": True,
        }
    )
    state = _build_state()
    request = SimulationRequest(
        sim_type=SimulationType.COMSOL,
        design_state=state,
        parameters={},
    )

    driver._get_or_generate_step_file = lambda req: Path("dummy.step")

    def _patched_create_dynamic_model(step_file, design_state):
        _ = step_file, design_state
        driver._last_heat_binding_report = {
            "active_components": 1,
            "assigned_count": 0,
            "ambiguous_components": [],
            "disambiguated_components": [],
            "failed_components": ["CompA"],
        }
        driver._structural_setup_ok = True
        driver._power_setup_ok = False
        return None

    driver._create_dynamic_model = _patched_create_dynamic_model

    result = driver._run_dynamic_simulation(request)

    assert result.success is False
    raw_data = dict(result.raw_data or {})
    source_claim = dict(raw_data.get("source_claim", {}) or {})
    contract_bundle = dict(raw_data.get("contract_bundle", {}) or {})
    field_export_registry = dict(raw_data.get("field_export_registry", {}) or {})
    physics_profile_contract = dict(raw_data.get("physics_profile_contract", {}) or {})
    profile_audit_digest = dict(raw_data.get("profile_audit_digest", {}) or {})

    assert raw_data["contract_bundle_version"] == COMSOL_CONTRACT_BUNDLE_VERSION
    assert raw_data["physics_profile"] == PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    assert raw_data["field_export_registry_version"] == COMSOL_FIELD_REGISTRY_VERSION
    assert raw_data["physics_profile_contract_version"] == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    assert raw_data["profile_audit_digest_version"] == COMSOL_PROFILE_AUDIT_DIGEST_VERSION
    assert (
        raw_data["simulation_metric_unit_contract_version"]
        == COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
    )
    assert raw_data["requested_profile_release_grade"] is True
    assert raw_data["effective_profile_release_grade"] is True
    assert raw_data["canonical_request_preserved"] is True
    assert raw_data["thermal_realness_level"] == REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    assert raw_data["structural_realness_level"] == REALNESS_LEVEL_OFFICIAL_INTERFACE_THIN_SLICE
    assert raw_data["power_realness_level"] == REALNESS_LEVEL_NETWORK_SOLVER_FALLBACK
    assert raw_data["thermal_study_solved"] is False
    assert raw_data["structural_study_solved"] is False
    assert raw_data["power_study_solved"] is False
    assert source_claim["requested_physics_profile"] == PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    assert source_claim["requested_profile_release_grade"] is True
    assert source_claim["effective_profile_release_grade"] is True
    assert source_claim["canonical_request_preserved"] is True
    assert source_claim["structural_enabled"] is True
    assert source_claim["structural_setup_ok"] is True
    assert source_claim["structural_study_solved"] is False
    assert source_claim["power_comsol_enabled"] is True
    assert source_claim["power_setup_ok"] is False
    assert source_claim["power_study_solved"] is False
    assert source_claim["power_network_enabled"] is True
    assert contract_bundle["physics_profile"] == PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL
    assert (
        contract_bundle["contract_versions"]["physics_profile_contract"]
        == COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION
    )
    assert contract_bundle["profile_audit_digest"]["canonical_request_degraded"] is False
    assert PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL in physics_profile_contract
    assert profile_audit_digest["canonical_request_degraded"] is False
    assert profile_audit_digest["release_grade_blocked"] is False
    assert "temperature" in field_export_registry
    assert "displacement_magnitude" in field_export_registry
    assert "displacement_u" in field_export_registry
    assert "von_mises" in field_export_registry
