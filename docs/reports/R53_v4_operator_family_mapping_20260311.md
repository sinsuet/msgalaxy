# R53 v4 operator family mapping

Date: 2026-03-11

## 1. Scope

This change only closes the DSL v4 semantic-action to review/visualization family mapping gap.

Later follow-up:

- `R55_v4_operator_semantic_caption_review_contract_20260311` builds a display-only semantic caption layer on top of this stable family mapping without changing the family semantics.

In scope:

- canonical `v4 action -> family` mapping
- consumer-side normalization for visualization/review package
- minimal targeted tests

Out of scope:

- DSL v4 schema redesign
- review package main contract rewrite
- COMSOL / geometry / archetype changes

## 2. Canonical v4 mapping table

The canonical mapping now lives in `optimization/modes/mass/operator_program_v4.py` as `OPERATOR_ACTION_FAMILY_MAP_V4`.

| v4 action | family | rationale |
| --- | --- | --- |
| `place_on_panel` | `geometry` | panel placement / layout placement |
| `align_payload_to_aperture` | `aperture` | payload-to-aperture alignment |
| `reorient_to_allowed_face` | `aperture` | face/orientation alignment for mission-facing objects |
| `mount_to_bracket_site` | `structural` | support/mount-site realization intent |
| `move_heat_source_to_radiator_zone` | `thermal` | thermal relocation / radiator bias |
| `separate_hot_pair` | `thermal` | thermal spacing management |
| `add_heatstrap` | `thermal` | thermal hardware |
| `add_thermal_pad` | `thermal` | thermal contact hardware |
| `add_mount_bracket` | `structural` | structural support hardware |
| `rebalance_cg_by_group_shift` | `geometry` | geometry-side CG recentering move |
| `shorten_power_bus` | `power` | power routing / bus shortening |
| `protect_fov_keepout` | `mission` | mission/FOV protection |
| `activate_aperture_site` | `aperture` | aperture-site activation / aperture-facing preparation |

Stable family landing after this change:

- `geometry`: geometry/panel placement
- `aperture`: aperture/payload alignment
- `thermal`: thermal management
- `structural`: structural support
- `power`: power routing
- `mission`: mission/FOV protection

## 3. Compatibility with v3

`core.visualization._operator_action_family(...)` now merges:

- legacy v3 family mapping
- canonical DSL v4 family mapping

Legacy v3 actions are unchanged:

- `group_move`, `cg_recenter`, `swap` -> `geometry`
- `hot_spread`, `add_heatstrap`, `set_thermal_contact` -> `thermal`
- `add_bracket`, `stiffener_insert` -> `structural`
- `bus_proximity_opt` -> `power`
- `fov_keepout_push` -> `mission`

This means:

- v3 review/visualization consumers remain backward compatible
- v4 semantic actions stop collapsing into `other`
- mixed payloads (`semantic_operator_actions` + runtime `operator_actions` + `selected_candidate_stubbed_actions`) can be merged in one place

## 4. Consumer-side changes

### 4.1 `core.visualization`

Added normalization helpers:

- `_normalize_operator_action_name(...)`
- `_coerce_operator_action_values(...)`
- `_merge_operator_actions(...)`
- `_resolve_record_operator_actions(...)`

Behavioral effect:

- list/string/JSON/dict-shaped action payloads now normalize into stable action names
- visualization summaries and layout timeline frames now understand v4 semantic actions
- family display order now includes `aperture`

### 4.2 `visualization/review_package`

Updated consumers:

- `builders.py::_build_operator_coverage(...)`
- `iteration_builder.py::_build_operator_info(...)`

Behavioral effect:

- review payload coverage now merges semantic/runtime/stubbed actions
- policy adjustment rows can parse dict-shaped action payloads
- step-level `action_family_counts` now uses the same canonical mapping path as visualization

### 4.3 Registry-facing family labels

`visualization.review_package.build_registry_snapshot(...)` now freezes the review-facing labels for the stable v4 landing:

- `geometry/panel placement`
- `aperture/payload alignment`
- `thermal management`
- `structural support`
- `power routing`
- `mission/fov protection`

For `teacher_demo`, `unknown_v4_family_policy=error` keeps unmapped future semantic actions from silently degrading into review output.

## 5. Proof that stats no longer fall into `other`

Targeted tests added in `tests/test_operator_family_v4_mapping.py`.

Validated points:

1. all 13 DSL v4 actions are covered by the canonical mapping table
2. legacy v3 actions still map to their previous families
3. `core.visualization._build_mass_visualization_summary(...)` reports:
   - `geometry=1, aperture=1, thermal=1, structural=1, power=1, mission=1`
   - no `other=...`
4. `visualization.review_package.builders._build_operator_coverage(...)` counts:
   - `geometry`, `aperture`, `thermal`, `structural`, `power`, `mission`
   - `other == 0`
   - semantic metadata actions and stubbed actions are both consumed
5. `build_registry_snapshot("teacher_demo")` freezes the six public family labels and keeps `unknown_v4_family_policy=error`
6. `IterationReviewPackage` step payloads expose stable `primary_action_family` / `primary_action_family_label` for DSL v4 semantic actions

Executed command:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; conda run -n msgalaxy pytest tests/test_operator_family_v4_mapping.py -q
```

Result:

- `7 passed`

## 6. Remaining v4 actions that are still only coarsely expressed

The following actions are now stably classified, but their family granularity is still intentionally coarse on the review/visualization side:

- `reorient_to_allowed_face`
  - currently folded into `aperture`; orientation-specific semantics are not shown as a separate family
- `activate_aperture_site`
  - currently folded into `aperture`; site activation vs payload alignment is not distinguished
- `mount_to_bracket_site`
  - currently folded into `structural`; mount-site selection vs bracket insertion is not separated
- `rebalance_cg_by_group_shift`
  - currently folded into `geometry`; CG-balancing is not exposed as its own family
- `move_heat_source_to_radiator_zone`
  - currently folded into `thermal`; thermal relocation vs thermal hardware augmentation is not separated
- `separate_hot_pair`
  - currently folded into `thermal`; thermal spacing is not exposed as a separate family

These are presentation/analytics granularity gaps only. They are no longer unmapped, and they no longer default to `other`.
