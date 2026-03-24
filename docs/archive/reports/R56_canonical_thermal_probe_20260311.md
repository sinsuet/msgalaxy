# R56 Canonical Thermal Probe (2026-03-11)

## 1. Scope

This report records a minimal, real COMSOL follow-up to ADR-0012 after `R51`.

Strict scope:

- do not expand the architecture;
- keep the previously validated real vertical smoke intact;
- add only the smallest bridge needed to probe whether the thermal branch can move from `diagnostic_simplified` toward an official canonical slice;
- if the canonical branch still fails, keep that failure explicit instead of masking it.

## 2. Official COMSOL References Consulted

Documented facts were checked against official COMSOL documentation before code changes:

- Surface-to-Ambient Radiation boundary condition:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_ht_features.09.098.html>
- Temperature boundary condition:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_ht_features.09.091.html>
- Heat Transfer with Surface-to-Surface Radiation interface overview:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_interfaces.08.36.html>
- COMSOL study/solver guidance for nonlinear heat-transfer continuation and parametric progression:
  - <https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_solver.35.046.html>

Evidence use boundary:

- statements about `Temperature` vs `Surface-to-Ambient Radiation`, user-defined emissivity, and continuation are documented facts from COMSOL docs;
- statements about convergence or failure in this report are local inferences from the real runs executed on 2026-03-11.

## 3. Minimal Bridge Changes

The following changes were introduced as a narrow bridge layer only:

- `simulation/comsol_driver.py`
  - canonical thermal path now requires explicit opt-in via `enable_canonical_thermal_path`;
  - power continuation remains enabled by default, even for canonical probe mode.
- `simulation/comsol/model_builder.py`
  - added the canonical probe branch based on `SurfaceToAmbientRadiation`;
  - set `epsilon_rad_mat=userdef` and `epsilon_rad=0.8`;
  - added boundary-level shell emissivity material preparation;
  - kept fallback to the existing diagnostic boundary path when shell outer selection is unavailable.
- `simulation/comsol/solver_scheduler.py`
  - canonical probe continuation schedule uses finer first steps (`1e-4 -> ... -> 1.0`) instead of the old diagnostic-only ramp semantics.
- `simulation/comsol/feature_domain_audit.py`
  - audit now recognizes canonical thermal boundary tag `rad_amb1` in addition to the diagnostic `temp1/conv_stabilizer` path.
- `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py`
  - added `--enable-canonical-thermal-path` so canonical thermal probing is explicit and reproducible instead of silently becoming the default.
- `tests/test_comsol_physics_profiles.py`
  - added a contract-level regression for the new opt-in gate.

## 4. Stable Default Path

The real vertical smoke default path remains the validated diagnostic chain from `R51`.

Validated regression command:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_real_comsol_vertical_smoke.py -q
```

Result:

- `1 passed`

Interpretation:

- the repository still keeps one real `STEP -> COMSOL -> field export -> review inputs` path that is stable and reproducible;
- the default real smoke does not pretend that canonical thermal is already solved.

## 5. Real Canonical Probe

Explicit canonical probe command executed in this session:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py --output-root C:/Users/hymn/AppData/Local/Temp/codex_real_comsol_canonical_probe_final --clean --enable-canonical-thermal-path
```

Observed result:

- runtime probe: `SUCCESS`
- STEP generation: `SUCCESS`
- COMSOL import/model assembly: `SUCCESS`
- thermal solve: `FAILED`
- failure stage recorded by the smoke summary: `comsol_import`

Key local evidence from stdout and the generated summary:

- canonical branch used `SurfaceToAmbientRadiation`;
- user-defined emissivity bridge was applied;
- canonical continuation started at `P_scale = 1e-4`;
- the very first stationary step still failed with COMSOL Newton non-convergence;
- the saved `.mph` artifact was written, but no valid thermal/structural field package was produced.

Canonical probe output root:

- `C:/Users/hymn/AppData/Local/Temp/codex_real_comsol_canonical_probe_final`

Main blocker signature:

- `Failed to find a solution`
- `Maximum number of Newton iterations reached`
- `Returned solution is not converged`
- `No parameter steps were returned`

## 6. Integration Test Results

Targeted regression command:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_comsol_physics_profiles.py tests/test_real_comsol_vertical_smoke.py tests/test_architecture_integration_smoke.py -q
```

Result:

- `20 passed`

Coverage of this validation slice:

- contract-level gate for explicit canonical thermal opt-in;
- real diagnostic vertical smoke regression;
- architecture/review-package smoke after the stricter `teacher_demo` field-case gate alignment.

## 7. Conclusion

Current true state after this probe:

- the repository now has a clean split between:
  - stable default real smoke (`diagnostic_simplified`);
  - explicit canonical thermal probe mode;
- the minimal COMSOL interface bridge for canonical thermal is in place;
- real single-case convergence is still blocked.

Therefore:

- canonical thermal must **not** be reported as validated;
- the remaining issue is no longer a missing interface name or missing emissivity binding;
- the remaining issue is a real solver/profile stability blocker.

## 8. Next ADR-Aligned Step

Recommended next step under ADR-0012:

1. keep the current explicit probe mode and stable diagnostic default as-is;
2. investigate canonical thermal convergence with a dedicated, isolated solver/profile task:
   - boundary selection purity for shell outer faces;
   - whether the current imported shell surfaces need directional filtering or cleaner outer-only selections;
   - whether the canonical static slice needs a different official heat-transfer boundary mix or stricter solver controls;
3. do not promote canonical thermal until one real single-case smoke passes end to end with the explicit probe mode.
