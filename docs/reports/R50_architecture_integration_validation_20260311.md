# R50 Architecture Integration Validation 2026-03-11

## 集成范围

本次仅覆盖 ADR-0010 ~ ADR-0014 的共享接口对齐、最小桥接修复、最小集成测试，不重做 5 个子任务本身，不扩架构，不跑全量 `pytest`。

本轮实际检查的链路为：

1. `task / mission -> SatelliteArchetype`
2. `SatelliteArchetype -> catalog / shell / aperture`
3. `catalog / shell / aperture -> geometry / STEP contracts`
4. `geometry / source info -> COMSOL physics profile / field registry`
5. `operator DSL v4 / rule engine -> IterationReviewPackage`
6. `metric / unit / color registry -> review package output`

## 已对齐的共享接口

1. `task / mission -> SatelliteArchetype`
   - 通过 `resolve_satellite_bom_context(...)` 选择 `optical_remote_sensing_microsat`。
   - 新增共享适配层把该选择结果与后续 shell/catalog/geometry proxy 合并为单个归一化报告。

2. `SatelliteArchetype -> catalog / shell / aperture`
   - `allowed_shell_variants` 与 `design_state.metadata.shell_variant` 已可在共享适配层中对齐验证。
   - catalog `interfaces.aperture_alignment` 与 shell `aperture_id` 的绑定关系已可统一检查。

3. `catalog / shell / aperture -> geometry / STEP contracts`
   - 当前环境下 `pythonocc` 可用，最小 STEP smoke 已证明 aperture/component/shell 信息能落入 geometry manifest。

4. `geometry / source info -> COMSOL physics profile / field registry`
   - `ComsolModelBuilderMixin._resolve_shell_geometry(...)` 已可直接读取 `metadata["shell_spec"]`。
   - review package 侧已能透传 `contract_bundle / field_export_registry / simulation_metric_unit_contract / profile_audit_digest`。

5. `operator DSL v4 / rule engine -> IterationReviewPackage`
   - review package 已兼容 `selected_candidate_dsl_version / selected_semantic_program_id / semantic_operator_actions / selected_candidate_stubbed_actions / rule_engine_report / realization`。

6. `metric / unit / color registry -> review package output`
   - `max_temp / max_stress / power_margin` 已与 ADR-0012 的 summary unit 合同对齐，review output 中分别使用 `degC / MPa / %`。

## 做过的桥接修复

### 1. `shell_spec -> COMSOL` 最小桥接

- 文件：`simulation/comsol/model_builder.py`
- 修复点：
  - `_resolve_shell_geometry(...)` 优先读取 `geometry.shell_spec.resolve_shell_spec(...)`
  - 新式 `shell_spec` 可直接输出 `outer_x / outer_y / outer_z / thickness`
  - 保留 legacy `metadata["shell"]` fallback，不破坏旧路径

### 2. `COMSOL 合同 -> Review Package` 透传

- 文件：`visualization/review_package/contracts.py`
- 修复点：
  - `PhysicsProfileInfo` 增加可选合同字段：
    - `contract_bundle(_version)`
    - `field_export_registry(_version)`
    - `simulation_metric_unit_contract(_version)`
    - `profile_audit_digest(_version)`

- 文件：`visualization/review_package/iteration_builder.py`
- 修复点：
  - `_load_field_case_assets(...)` 透传上述合同字段
  - `physics_profile` 优先从 `contract_bundle.physics_profile` 读取
  - `_build_physics_info(...)` 将合同字段写入 `IterationReviewPackage.physics`

### 3. `VOP / DSL v4 -> Review Package` 字段兼容

- 文件：`visualization/review_package/iteration_builder.py`
- 修复点：
  - `_build_operator_info(...)` 兼容：
    - `selected_candidate_dsl_version`
    - `selected_semantic_program_id`
    - `selected_operator_program_id`
    - `semantic_operator_actions`
    - `selected_candidate_stubbed_actions`
    - `selected_candidate_realization_status`
    - `selected_candidate_has_stub_realization`
    - `rule_engine_report`
    - `realization`

### 4. `summary_unit -> review unit registry` 对齐

- 文件：`visualization/review_package/registry.py`
- 修复点：
  - 新增 `temperature_celsius`、`percent`
  - `max_temp.unit_key -> temperature_celsius`
  - `max_stress.unit_key -> stress_mpa`
  - `power_margin.unit_key -> percent`

- 文件：`visualization/review_package/iteration_builder.py`
- 修复点：
  - `_build_metric_cards(...)` 支持读取 `simulation_metric_unit_contract`
  - 依据 `summary_unit` 覆盖 review metric card / delta 的单位显示

### 5. `0010 -> 0011` 共享适配层

- 文件：`domain/satellite/geometry_bridge.py`
- 修复点：
  - 新增最小共享适配层，统一输出：
    - `archetype_id`
    - `allowed_shell_variants`
    - `shell_kind / shell_variant / shell_variant_allowed`
    - `panel_faces / aperture_count`
    - `aperture_bindings / matched_catalog_components`
    - `geometry_proxy_manifest_version / component_proxy_count / aperture_proxy_count`

### 6. 第二轮校正（仅补验证，不新增主实现）

- 文件：`tests/test_architecture_integration_smoke.py`
- 校正点：
  - 补充 `core.visualization._operator_action_family(...)` 对 DSL v4 语义动作族映射的最小验证
  - 补充 review package field-case 缺失目录与不兼容 dataset root 的 fail-fast 验证
  - 本轮未新增共享主实现修复；只校正 `R50` 对当前状态的结论

### 7. fresh-process 下的 v4 family map fallback

- 文件：`core/visualization.py`
- 修复点：
  - 新增 `_OPERATOR_ACTION_FAMILY_MAP_V4_FALLBACK`
  - 当 `optimization.modes.mass.operator_program_v4` 在初始化链中因包导入顺序被吞异常时，visualization 不再把 DSL v4 语义动作族整体回退为 `other`
  - 该修复只作用于 review/visualization 兼容层，不修改 DSL v4 本体与 rule engine 本体

## 集成测试项与结果

### 静态/导入/合同级检查

1. `python -m py_compile` 目标文件静态编译检查
   - 结论：通过

2. `test_task_to_archetype_to_shell_catalog_geometry_proxy_contract`
   - 类型：合同级集成测试
   - 结论：通过
   - 覆盖链路：
     - `task -> archetype`
     - `archetype -> shell variant`
     - `catalog -> aperture binding`
     - `shell/catalog -> geometry proxy`

3. `test_shell_spec_metadata_bridges_into_comsol_shell_geometry`
   - 类型：合同级集成测试
   - 结论：通过
   - 覆盖链路：
     - `shell_spec -> COMSOL shell geometry`

### 最小 smoke 集成测试

4. `test_v4_review_package_smoke_propagates_contracts_and_units`
   - 类型：最小 smoke
   - 结论：通过
   - 覆盖链路：
     - `geometry/source info -> physics profile / field registry`
     - `operator DSL v4 / rule engine -> IterationReviewPackage`
     - `metric/unit/color registry -> review package output`

5. `test_step_contract_smoke_for_catalog_shell_chain`
   - 类型：最小 smoke
   - 结论：通过
   - 覆盖链路：
     - `catalog / shell / aperture -> geometry / STEP contracts`

6. `test_v4_operator_action_family_mapping_is_visible_to_visualization`
   - 类型：最小 smoke
   - 结论：通过
   - 覆盖链路：
     - `operator DSL v4 -> visualization action family aggregation`

7. `test_review_package_field_case_map_missing_case_dir_fails_fast`
   - 类型：合同级 fail-fast 测试
   - 结论：通过
   - 覆盖链路：
     - `field_case_map.steps[*].field_case_dir -> review preflight`

8. `test_review_package_field_dataset_root_without_compatible_cases_fails_fast`
   - 类型：合同级 fail-fast 测试
   - 结论：通过
   - 覆盖链路：
     - `field_case_dir dataset root -> review preflight`

9. `test_real_comsol_vertical_smoke_subprocess_contract`
   - 类型：真实 COMSOL subprocess smoke
   - 结论：通过
   - 覆盖链路：
     - `STEP -> COMSOL import -> solve -> field export -> review input manifests`
   - 补充说明：
     - 该测试通过子进程运行 `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py`
     - 测试会在 `comsol_runtime_probe` 失败时 `skip`
     - 当前环境本次实际结果为 `PASS`

### 最小测试命令

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m py_compile tests/test_architecture_integration_smoke.py
conda run -n msgalaxy python -m pytest tests/test_architecture_integration_smoke.py -q
conda run -n msgalaxy python -m py_compile tests/test_real_comsol_vertical_smoke.py
conda run -n msgalaxy python -m pytest tests/test_real_comsol_vertical_smoke.py -q
conda run -n msgalaxy python -m pytest tests/test_architecture_integration_smoke.py tests/test_real_comsol_vertical_smoke.py -q
```

本次结果：

- `tests/test_architecture_integration_smoke.py -> 7 passed`
- `tests/test_real_comsol_vertical_smoke.py -> 1 passed`
- 合并执行 -> `8 passed`
- `0 failed`
- `0 skipped`

## 跳过项及原因

1. 无
   - 当前环境下真实 COMSOL 许可与所需模块可用，`R51` 所述单案例垂直 smoke 已实际执行通过

## 当前剩余阻塞

1. 真实 COMSOL 垂直 smoke 已验证到 `review_package_input_manifest.json`，但当前真实 physics profile 仍如实降级为 `diagnostic_simplified`，不是 release-grade canonical thermal。

2. 真实验证目前覆盖的是 `STEP -> import -> solve -> field export -> review inputs`。
   - 仍未单独验证“真实优化 run 目录 + 真实 COMSOL case”直接产出 `teacher_demo IterationReviewPackage` 的完整教师演示成品目录。

3. 当前未发现新的共享接口阻塞。
   - `core.visualization._operator_action_family(...)` 已通过 fallback 稳定消费 DSL v4 family map，并由最小测试覆盖。
   - review package field-case preflight 已存在且已由最小 fail-fast 测试覆盖；它仍然要求 case 目录满足既有合同，但这是当前边界，不是未实现缺口。

## 下一步建议

1. 若要继续按 ADR 推进，优先把 `R51` 的真实 case 接到一个最小 `teacher_demo` 成品目录产物，而不是先扩更多 archetype。

2. 若要把 thermal claim 提升到 canonical/release-grade，下一步应针对 `diagnostic_simplified -> canonical thermal` 的退化原因单独治理，不要把当前 smoke 误报为 release-grade。

3. 保持 `domain/satellite/geometry_bridge.py`、`core.visualization.py` fallback 和 subprocess smoke test 作为共享兼容层，后续继续优先 adapter/test layer，不直接改 5 个子系统内部接口。
