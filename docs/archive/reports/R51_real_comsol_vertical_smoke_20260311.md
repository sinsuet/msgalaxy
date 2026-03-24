# R51 Real COMSOL Vertical Smoke (2026-03-11)

## 1. Scope

This report records one real COMSOL minimal end-to-end smoke run for the exact chain:

`STEP -> COMSOL import -> solve -> field export -> IterationReviewPackage upstream inputs`

Strict scope for this run:

- one single case only;
- no batch run;
- no archetype refactor;
- no geometry-kernel rewrite;
- no DSL rewrite;
- no full test sweep.

## 2. Command And Environment

Command executed in the repository workspace:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; conda run -n msgalaxy python tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py --clean
```

Re-run evidence captured in this session:

- run timestamp: `2026-03-11 18:19 ~ 18:20 +08:00`
- reliable invocation path: keep `conda run -n msgalaxy ...`
- local note:
  - direct `D:\MSCode\miniconda3\envs\msgalaxy\python.exe` import of `mph` was unstable in this environment and was not used as the validation path
  - this is a local runtime observation, not a COMSOL product claim

Real environment confirmed before the run:

- COMSOL runtime: `COMSOL Multiphysics 6.3 (开发版本: 290)`
- required modules present:
  - `CAD Import`
  - `Heat Transfer`
  - `Structural Mechanics`

Single-case output root:

- `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311`

## 3. Minimal Smoke Case

Shell/aperture input used for this smoke:

- shell spec: `config/catalog_components/shell_box_panel_aperture_min.json`
- shell outer size: `400 x 300 x 260 mm`
- shell thickness: `8 mm`
- panels carried into geometry: `2`
- apertures carried into geometry: `1`

Minimal internal layout:

- `payload_camera_core`
- `eps_bus`
- `battery_pack`
- `avionics_core`

The runner materializes a single case at:

- `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000`

## 4. Step Results

### 4.1 STEP generation

Status: `SUCCESS`

Evidence:

- STEP path exists:
  - `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000/geometry/demo_layout.step`
- geometry manifest exists:
  - `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000/geometry/demo_layout.geometry_manifest.json`
- geometry proxy manifest exists:
  - `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000/geometry/demo_layout.geometry_proxy_manifest.json`
- geometry manifest validation:
  - `shell_count = 1`
  - `panel_count = 2`
  - `aperture_count = 1`

Interpretation:

- shell/panel/aperture metadata entered STEP generation successfully;
- aperture topology was produced at STEP boolean stage, not only as side metadata.

### 4.2 COMSOL import

Status: `SUCCESS`

Evidence from `real_comsol_vertical_smoke_summary.json` and `field_exports/manifest.json`:

- imported geometry domain count: `5`
- expected component count: `4`
- shell therefore remained a real imported domain
- geometry boundary count: `40`
- audit `passed = true`
- `boxsel_outer_boundary` present
- `shell_outer_selection_count = 6`
- selection tags include:
  - `boxsel_shell_outer_xp`
  - `boxsel_shell_outer_xn`
  - `boxsel_shell_outer_yp`
  - `boxsel_shell_outer_yn`
  - `boxsel_shell_outer_zp`
  - `boxsel_shell_outer_zn`

Interpretation:

- shell metadata survived into COMSOL model builder;
- STEP import still supported downstream boundary selection;
- shell outer boundary selection remained operational after import.

### 4.3 Solve

Status: `SUCCESS`

Evidence:

- thermal stationary ramp solved for `P_scale = 0.01 -> 0.20 -> 1.0`
- structural stationary solved
- modal solve solved
- `.mph` model saved:
  - `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000/mph_models/model_case_0000_20260311_181953_158865.mph`

Selected extracted metrics:

- `max_temp = 866.9290332188239 K`
- `min_temp = 271.15050813738833 K`
- `temp_gradient = 595.7785250814355 K`
- `max_stress = 549070.7209711553 Pa`
- `max_displacement = 5.4378479132261336e-06 m`
- `first_modal_freq = 704.9393737076667 Hz`

### 4.4 Three-field export

Status: `SUCCESS`

Field contracts exported successfully:

- temperature:
  - VTU: `field_exports/vtu/temperature.vtu`
  - grid: `field_exports/grid/temperature_grid.txt`
  - tensor: `tensor/temperature_tensor.npz`
  - render: `renders/temperature_field.png`
- displacement:
  - VTU: `field_exports/vtu/displacement.vtu`
  - grid: `field_exports/grid/displacement_grid.txt`
  - vector grids:
    - `field_exports/grid/displacement_u_grid.txt`
    - `field_exports/grid/displacement_v_grid.txt`
    - `field_exports/grid/displacement_w_grid.txt`
  - tensor: `tensor/displacement_tensor.npz`
  - render: `renders/displacement_field.png`
- stress:
  - VTU: `field_exports/vtu/stress.vtu`
  - grid: `field_exports/grid/stress_grid.txt`
  - tensor: `tensor/stress_tensor.npz`
  - render: `renders/stress_field.png`

Registry/unit contract confirmed in manifests:

- temperature: registry `temperature`, unit `K`
- displacement: registry `displacement_magnitude`, unit `m`
- stress: registry `von_mises`, unit `Pa`

Bridge repair applied for this run:

- `tools/comsol_field_demo/tool_run_fields.py` now prefers temperature grid statistics when direct temperature metrics materially disagree with the exported field, so review-package metric cards align with the actual exported temperature field.

### 4.5 IterationReviewPackage upstream inputs

Status: `SUCCESS`

Upstream inputs confirmed complete:

- field manifest:
  - `field_exports/manifest.json`
- metric audit:
  - `field_exports/metric_audit.json`
- tensor manifest:
  - `tensor/manifest.json`
- render manifest:
  - `renders/manifest.json`
- review input manifest:
  - `review_package_input_manifest.json`
- geometry overlay:
  - `renders/geometry_overlay.png`
- triptych:
  - `renders/three_fields_horizontal.png`

This means the current chain already produces the upstream artifacts required by the planned `IterationReviewPackage` consumption path, even though a full step-level review package builder was not introduced here.

### 4.6 Guarded regression test

Status: `SUCCESS`

Added targeted test:

- `tests/test_real_comsol_vertical_smoke.py`

Executed command:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_real_comsol_vertical_smoke.py -q
```

Result:

- `1 passed`

Coverage:

- launches the same real smoke tool in a subprocess
- allows `skip` only when `comsol_runtime_probe` fails
- validates summary status, stage success, geometry/import audit, saved `.mph`, field manifests, and source-claim degradation contract

## 5. Physics Profile And Source Claim

Status: `SUCCESS`

Source claim propagated stably into:

- `field_exports/manifest.json`
- `tensor/manifest.json`
- `renders/manifest.json`

Observed source claim:

- requested physics profile:
  - `electro_thermo_structural_canonical`
- effective physics profile:
  - `diagnostic_simplified`
- thermal realness:
  - `diagnostic_simplified`
- structural realness:
  - `official_interface_thin_slice`
- power realness:
  - `disabled`

Explicit degradation reason written into artifacts:

- thermal path uses `P_scale`
- thermal path uses simplified boundary temperature anchor
- thermal path uses weak convection stabilizer

Interpretation:

- the run is a real COMSOL solve and real three-field export;
- it is not a release-grade canonical thermal claim;
- the artifacts correctly disclose that degradation instead of pretending success at a higher physics profile.

## 6. Files Changed For This Smoke Work

- `simulation/comsol/feature_domain_audit.py`
  - added explicit shell outer boundary selection audit fields
- `tools/comsol_field_demo/tool_run_fields.py`
  - fixed temperature metric-card canonicalization to align with exported grid field when direct metrics drift
- `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py`
  - added the single-case real COMSOL vertical smoke runner
- `tests/test_real_comsol_vertical_smoke.py`
  - added a guarded subprocess regression for the real vertical smoke
- `core/visualization.py`
  - added a minimal DSL v4 family-map fallback so fresh-process review/visualization smoke does not silently degrade v4 actions to `other`

## 7. Main Artifacts

Run summary:

- `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/real_comsol_vertical_smoke_summary.json`

Single-case review-input manifest:

- `tools/comsol_field_demo/output/real_comsol_vertical_smoke_20260311/cases/case_0000/review_package_input_manifest.json`

## 8. Residual Risks

- During dataset probing, COMSOL/MPh still prints benign `Dataset "dset*"... does not exist.` messages to stdout. The run artifacts and exported fields are valid, but the logging remains noisy.
- The thermal branch remains explicitly degraded to `diagnostic_simplified`; this smoke does not promote the thermal path to release-grade canonical truth.

## 9. Conclusion

This single real COMSOL vertical smoke passed end to end.

Passed steps:

1. STEP generation
2. COMSOL import
3. solve
4. temperature/displacement/stress export
5. IterationReviewPackage upstream inputs complete
6. physics profile/source claim stably entered artifacts

There was no license/module blocker in this environment.

## 10. Official COMSOL References Consulted

- CAD import and selection behavior:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.cad/CADImportModuleUsersGuide.pdf>
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.cad/cad_ug_cad_import_repair_defeaturing.5.05.html>
- result export API and file types:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_results.52.040.html>
  - <https://www.comsol.com/support/learning-center/article/supported-file-formats-76161>
- heat-transfer interface baseline:
  - <https://doc.comsol.com/5.3/doc/com.comsol.help.comsol/comsol_ref_heattransfer.21.10.html>

Notes on evidence use:

- documented facts above come from COMSOL official docs;
- the conclusions in Sections 4-9 are local run inferences from the artifacts generated on 2026-03-11.
