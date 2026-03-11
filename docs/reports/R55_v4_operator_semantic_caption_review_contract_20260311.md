# R55 v4 operator semantic caption review contract

Date: 2026-03-11

## 1. Scope

This change extends the existing DSL v4 action-family mapping work from `R53_v4_operator_family_mapping_20260311` with a narrow review-facing semantic caption layer.

In scope:

- stable `primary_action_label` / `semantic_caption` display fields
- target / rule / effect summaries for review consumers
- package index, aggregate montage metadata, payload state summary, Blender brief, and report/visualization summary propagation
- minimal targeted tests

Out of scope:

- DSL v4 schema redesign
- operator realization / rule engine rewrite
- COMSOL / geometry / archetype changes

ADR alignment:

- `docs/adr/0013-operator-dsl-v4-and-placement-rule-engine.md`
- `docs/adr/0014-iteration-review-package-and-teacher-demo-chain.md`

## 2. Added display contract

`visualization/review_package/contracts.py::OperatorActionInfo` now carries these review-facing fields:

- `primary_action_label`
- `semantic_caption_short`
- `semantic_caption`
- `target_summary`
- `rule_summary`
- `expected_effect_summary`
- `observed_effect_summary`

The contract is display-only. It does not change DSL v4 storage or execution semantics.
It also does not change the family semantics established in `R53`; the caption layer is strictly additive for review consumers.

## 3. Normalization source

Review consumers now use a shared helper:

- `visualization/review_package/operator_semantics.py`

Normalization priority:

1. `selected_semantic_action_payloads`
2. `semantic_action_payloads`
3. `selected_operator_action_payloads`
4. `operator_program_patch.actions`
5. `operator_program.actions`

For DSL v4 payloads, target/rule/effect normalization reuses:

- `optimization/modes/mass/operator_program_v4.py::normalize_operator_program_v4_payload(...)`

This keeps review display aligned with the canonical v4 alias normalization without rewriting the schema.

## 4. Consumer propagation

### 4.1 Step package

`iteration_builder.py::_build_operator_info(...)` now emits:

- family fields from R53
- action label
- semantic caption
- target/rule/effect summaries

### 4.2 Package index and aggregate outputs

`package_index.json` step entries now include:

- `primary_action_label`
- `semantic_caption_short`
- `semantic_caption`
- `target_summary`
- `rule_summary`
- `expected_effect_summary`
- `observed_effect_summary`

Aggregate montage items now prefer semantic labels such as:

- `step_0001 | payload-to-aperture alignment @ subject group:payload_cluster[1]; target aperture:mission_aperture`

instead of only family labels.

### 4.3 Review payload / Blender sidecar

State summaries in `review_payload.json` and render-bundle key-state metadata now include the same semantic caption fields, so Blender brief generation can read them directly.

### 4.4 Report / visualization summary

`report.md` and `visualization_summary.txt` now expose a lightweight keyframe caption preview in addition to family audit statistics.

## 5. Compatibility

- v3 actions remain compatible and fall back to legacy labels such as `group move`, `cg recentering`, `heatstrap addition`, etc.
- If semantic payloads are missing, consumers degrade gracefully to family/action-only display.
- `teacher_demo` and `research_fast` keep the existing unknown-v4-family policy; the new caption layer does not weaken that gate.

## 6. Validation

Executed:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; conda run -n msgalaxy pytest tests/test_operator_family_v4_mapping.py tests/test_iteration_review_package.py tests/test_blender_render_bundle.py -q
```

Result:

- `20 passed`

Validated points:

1. `OperatorActionInfo` exposes action/target/rule/effect semantic summaries for DSL v4 payloads
2. `package_index.json` and aggregate montage metadata expose semantic captions
3. `summary.json` digest, `report.md`, and `visualization_summary.txt` carry keyframe caption previews
4. `review_payload.json` state summaries and Blender bundle/brief consume the same semantic caption contract

## 7. Remaining gaps

The new caption layer is still intentionally compact and review-facing only.

Still not fully expressed:

- multi-action causal chains across one step are summarized from the primary action only
- family audit remains family-level, not sub-action-level
- `activate_aperture_site`, `mount_to_bracket_site`, `rebalance_cg_by_group_shift`, `move_heat_source_to_radiator_zone`, and `separate_hot_pair` still do not have richer domain-specific caption templates beyond normalized target/rule summaries
- if a historical run lacks `selected_semantic_action_payloads`, captions fall back to action/family text rather than reconstructing full domain semantics
