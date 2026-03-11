"""
Contracts for step-level iteration review packages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class MetricSpec(BaseModel):
    key: str
    label: str
    description: str = ""
    direction: Literal["minimize", "maximize", "neutral"] = "neutral"
    source: str = ""
    unit_key: str = "unitless"
    category: str = "review"
    aliases: List[str] = Field(default_factory=list)


class UnitSpec(BaseModel):
    key: str
    symbol: str
    quantity: str = ""
    description: str = ""


class ColorSpec(BaseModel):
    key: str
    label: str
    colormap: str = ""
    unit_key: str = "unitless"
    default_min: Optional[float] = None
    default_max: Optional[float] = None
    range_source: str = "registry"
    show_colorbar: bool = True
    show_title: bool = False
    source_claim_required: bool = False


class OperatorFamilySpec(BaseModel):
    key: str
    label: str
    description: str = ""
    display_order: int = 0


class ReviewFieldCaseGateContract(BaseModel):
    mode: Literal["off", "strict_when_linked"] = "off"
    allowed_resolution_sources: List[str] = Field(default_factory=list)
    require_zero_defaulted: bool = True
    require_zero_unmapped: bool = True
    require_zero_incompatible_cases: bool = True
    require_zero_ambiguous_bindings: bool = True


class ReviewProfileContract(BaseModel):
    name: str
    description: str = ""
    package_level: Literal["full", "lightweight"] = "lightweight"
    shell_visual_policy: Literal["required", "preferred", "none"] = "preferred"
    field_render_mode: Literal["prefer_linked", "manifest_only"] = "manifest_only"
    triptych_policy: Literal["prefer_existing", "skip"] = "skip"
    checkpoint_only: bool = False
    include_metric_deltas: bool = True
    allow_missing_artifacts: bool = True
    source_claim_required: bool = True
    unknown_v4_family_policy: Literal["allow", "warn", "error"] = "allow"
    field_case_gate: ReviewFieldCaseGateContract = Field(default_factory=ReviewFieldCaseGateContract)
    required_artifacts: List[str] = Field(default_factory=list)
    optional_artifacts: List[str] = Field(default_factory=list)


class ReviewRegistryVersions(BaseModel):
    metric_registry: str = "metric_registry/v1"
    unit_registry: str = "unit_registry/v1"
    color_registry: str = "color_registry/v1"
    operator_family_registry: str = "operator_family_registry/v1"
    review_profile_registry: str = "review_profile_registry/v1"


class ReviewRegistryRefs(BaseModel):
    versions: ReviewRegistryVersions = Field(default_factory=ReviewRegistryVersions)


class IterationReviewFieldCaseMapEntry(BaseModel):
    step_index: int = 0
    sequence: int = 0
    stage: str = ""
    field_case_dir: str = ""
    physics_profile: str = ""
    notes: List[str] = Field(default_factory=list)


class IterationReviewFieldCaseMap(BaseModel):
    schema_version: str = "iteration_review_field_case_map/v1"
    run_id: str = ""
    mapping_source: str = ""
    dataset_root: str = ""
    default_case_dir: str = ""
    steps: List[IterationReviewFieldCaseMapEntry] = Field(default_factory=list)


class ReviewStateIndex(BaseModel):
    snapshot_path: str = ""
    stage: str = ""
    sequence: int = 0
    iteration: int = 0
    attempt: int = 0
    thermal_source: str = ""
    diagnosis_status: str = ""
    diagnosis_reason: str = ""
    component_count: int = 0
    layout_state_hash: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)


class OperatorActionInfo(BaseModel):
    dsl_version: str = "legacy_compatible"
    primary_action: str = ""
    primary_action_family: str = ""
    primary_action_family_label: str = ""
    primary_action_label: str = ""
    semantic_caption_short: str = ""
    semantic_caption: str = ""
    target_summary: str = ""
    rule_summary: str = ""
    expected_effect_summary: str = ""
    observed_effect_summary: str = ""
    action_types: List[str] = Field(default_factory=list)
    action_family_sequence: List[str] = Field(default_factory=list)
    action_family_counts: Dict[str, int] = Field(default_factory=dict)
    unmapped_actions: List[str] = Field(default_factory=list)
    family_contract_warnings: List[str] = Field(default_factory=list)
    policy_id: str = ""
    candidate_id: str = ""
    program_id: str = ""
    rationale: str = ""
    expected_effects: List[str] = Field(default_factory=list)
    observed_effects: List[str] = Field(default_factory=list)
    raw_operator_payload: Dict[str, Any] = Field(default_factory=dict)
    v4_reserved: Dict[str, Any] = Field(default_factory=dict)


class CaseContractAdapterInfo(BaseModel):
    adapter_version: str = "legacy_case_review_adapter/v1"
    contract_mode: str = ""
    recognized_inputs: List[str] = Field(default_factory=list)
    defaulted_fields: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class PhysicsSourceClaim(BaseModel):
    thermal_source: str = ""
    structural_source: str = ""
    power_source: str = ""
    field_data_source: str = ""
    source_gate_passed: Optional[bool] = None
    operator_family_gate_passed: Optional[bool] = None
    operator_realization_gate_passed: Optional[bool] = None
    final_audit_status: str = ""


class PhysicsProfileInfo(BaseModel):
    physics_profile: str = ""
    backend: str = ""
    evaluator_mode: str = ""
    source_claim: PhysicsSourceClaim = Field(default_factory=PhysicsSourceClaim)
    case_contract: CaseContractAdapterInfo = Field(default_factory=CaseContractAdapterInfo)
    contract_bundle_version: str = ""
    contract_bundle: Dict[str, Any] = Field(default_factory=dict)
    field_export_registry_version: str = ""
    field_export_registry: Dict[str, Any] = Field(default_factory=dict)
    simulation_metric_unit_contract_version: str = ""
    simulation_metric_unit_contract: Dict[str, Any] = Field(default_factory=dict)
    profile_audit_digest_version: str = ""
    profile_audit_digest: Dict[str, Any] = Field(default_factory=dict)
    final_mph_path: str = ""
    field_case_dir: str = ""
    render_manifest_path: str = ""
    field_manifest_path: str = ""
    tensor_manifest_path: str = ""
    simulation_result_path: str = ""
    review_ready: bool = False


class ReviewMetricCard(BaseModel):
    key: str
    label: str
    value: Any = None
    unit_key: str = "unitless"
    unit_symbol: str = ""
    direction: Literal["minimize", "maximize", "neutral"] = "neutral"
    source: str = ""
    raw_key: str = ""


class ReviewMetricDelta(BaseModel):
    key: str
    label: str
    before: Optional[float] = None
    after: Optional[float] = None
    delta: Optional[float] = None
    improved: Optional[bool] = None
    direction: Literal["minimize", "maximize", "neutral"] = "neutral"
    unit_key: str = "unitless"
    unit_symbol: str = ""


class ReviewArtifactRef(BaseModel):
    key: str
    path: str = ""
    planned_path: str = ""
    exists: bool = False
    artifact_type: str = ""
    source_claim: str = ""
    notes: List[str] = Field(default_factory=list)


class IterationReviewPackage(BaseModel):
    schema_version: str = "iteration_review_package/v1"
    package_status: Literal["lightweight_manifest", "linked_field_assets"] = "lightweight_manifest"
    review_profile: str
    run_id: str
    run_dir: str
    package_dir: str
    manifest_path: str
    step_index: int
    sequence: int
    iteration: int = 0
    attempt: int = 0
    stage: str = ""
    before: ReviewStateIndex
    after: ReviewStateIndex
    operator: OperatorActionInfo = Field(default_factory=OperatorActionInfo)
    physics: PhysicsProfileInfo = Field(default_factory=PhysicsProfileInfo)
    metrics: Dict[str, ReviewMetricCard] = Field(default_factory=dict)
    metric_deltas: Dict[str, ReviewMetricDelta] = Field(default_factory=dict)
    raw_metrics_unregistered: Dict[str, Any] = Field(default_factory=dict)
    review_artifacts: Dict[str, ReviewArtifactRef] = Field(default_factory=dict)
    registry_refs: ReviewRegistryRefs = Field(default_factory=ReviewRegistryRefs)
    notes: List[str] = Field(default_factory=list)


class IterationReviewMetricsPayload(BaseModel):
    schema_version: str = "iteration_review_metrics/v1"
    review_profile: str
    step_index: int
    sequence: int
    metrics: Dict[str, ReviewMetricCard] = Field(default_factory=dict)
    metric_deltas: Dict[str, ReviewMetricDelta] = Field(default_factory=dict)
    raw_metrics_unregistered: Dict[str, Any] = Field(default_factory=dict)
