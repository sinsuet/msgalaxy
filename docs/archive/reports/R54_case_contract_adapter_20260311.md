# R54 Case Contract Adapter 最小适配报告（2026-03-11）

## 1. 范围

本次交付严格限定为：

- 只做旧 `case` 目录合同到新 `IterationReviewPackage` 输入的最小适配；
- 不重写 review package 主体；
- 不改 COMSOL 求解器；
- 不改 archetype / geometry / DSL 本体；
- 不做“兼容所有历史垃圾数据”的泛化修复。

实际落点是：在 `visualization/review_package/iteration_builder.py` 前面新增一层旧 case 识别与 normalize 逻辑，再把归一化结果继续喂给现有 review package builder。

## 2. 旧 case 最小识别规则

适配器现在只认三类高价值旧输入信号，至少满足其一：

1. `design_state.json`
   - 典型形态：`<case>/design_state.json`
   - 作用：把旧 case 识别为有效 case root，即使没有 field render，也允许生成 lightweight review manifest。

2. `field_exports`
   - 典型形态：
     - `<case>/field_exports/manifest.json`
     - `<case>/field_exports/simulation_result.json`
     - 或 dataset 级 `field_run_summary.json` 中存在该 case 条目
   - 作用：为新 review contract 提供 physics metrics / source claim / physics profile 的最小来源。

3. render / summary metadata
   - 典型形态：
     - `<case>/renders/manifest.json`
     - `<case>/renders/*.png`
     - 或 dataset 级 `render_summary.json` 中存在该 case 条目
   - 作用：为 `geometry_overlay / temperature_field / displacement_field / stress_field / triptych` 提供最小链接来源。

额外的入口 normalize 规则：

- 若用户传入的是 `<case>/field_exports/`、`<case>/renders/`、`<case>/tensor/`，会先归一为 `<case>/`。
- 若用户传入的是：
  - `<case>/design_state.json`
  - `<case>/field_exports/manifest.json`
  - `<case>/field_exports/simulation_result.json`
  - `<case>/renders/manifest.json`
  也会先归一为 `<case>/`。
- 若用户传入的是 dataset 级 `field_run_summary.json` / `render_summary.json`，会先归一到 dataset root，然后按 `cases/` 目录枚举兼容 case。

## 3. 新 review contract 需要的最小输入

现有 review package 主体真正消费的最小输入，本次被收敛成统一的 `field_assets` 归一化结果：

- `case_dir`
- `metrics`
- `physics_profile`
- `source_claim`
- `contract_bundle(_version)`（可空）
- `field_export_registry(_version)`（可空）
- `simulation_metric_unit_contract(_version)`（可空）
- `profile_audit_digest(_version)`（可空）
- `render_manifest_path / field_manifest_path / tensor_manifest_path / simulation_result_path`
- `artifacts`
  - `geometry_overlay`
  - `temperature_field`
  - `displacement_field`
  - `stress_field`
  - `triptych`

这次没有重写 `IterationReviewPackage` 主体；只是给 `PhysicsProfileInfo` 新增了一个轻量 `case_contract` 元数据块，用来说明：

- 旧 case 通过哪种最小合同被识别；
- 哪些字段是适配器默认补齐的；
- 是否使用了 dataset summary fallback。

## 4. 旧 case -> 新 review contract 适配规则

### 4.1 适配顺序

1. 先把用户传入路径归一为 case root。
2. 检测 case root 是否命中最小旧合同信号：
   - `design_state.json`
   - `field_exports`
   - `render_metadata`
3. 若 case 在 `<dataset>/cases/<case_id>` 下，再补读：
   - `<dataset>/field_run_summary.json`
   - `<dataset>/render_summary.json`
4. 按优先级把旧数据折叠成新 review contract 最小输入：
   - metrics：`simulation_result.json` -> `field_exports/manifest.json` -> `field_run_summary.json`
   - render assets：`renders/manifest.json` -> `render_summary.json` -> `renders/*.png`
   - physics profile：`contract_bundle.physics_profile` -> `source_claim.physics_profile` -> `raw_data.physics_profile` -> `driver_config.thermal_evaluator_mode` -> `metric_sources.thermal_source` -> 默认值

### 4.2 默认补齐项

允许默认补齐的字段：

- `physics_profile`
  - 默认值：`field_case_linked`
- `source_claim.field_data_source`
  - 默认值：`tools/comsol_field_demo`
- render artifact 路径
  - 当 manifest 缺失但约定文件名存在时，自动回退到：
    - `renders/geometry_overlay.png`
    - `renders/temperature_field.png`
    - `renders/displacement_field.png`
    - `renders/stress_field.png`
    - `renders/three_fields_horizontal.png` / `renders/triptych.png`
- dataset root
  - 当 case 位于 `<dataset>/cases/<case_id>` 时自动推断 dataset root
- metrics
  - 当 case 内 per-case field manifest 缺失，但 dataset `field_run_summary.json` 有该 case 条目时，自动从 summary 回填

所有默认补齐都会写入：

- `package.physics.case_contract.defaulted_fields`
- `package.notes`

### 4.3 必须 fail-fast 的情况

以下情况不会再静默跳过，而是明确报错：

1. `field_case_map` 文件路径不存在
2. `field_case_dir` 路径不存在
3. `field_case_map.steps[*].field_case_dir` 指向的路径无法被归一为兼容 case
4. case 根目录既没有：
   - `design_state.json`
   - 也没有可识别的 `field_exports`
   - 也没有可识别的 render / summary metadata
5. 旧 case 虽命中了 summary 信号，但最终既无法解析出任何 render artifact，也拿不到任何 metrics，且也没有 `design_state.json`

## 5. 成功样例

本次新增的最小成功样例在测试里覆盖了如下路径：

- 输入：
  - `field_case_map.steps[0].field_case_dir = <dataset>/cases/case_0000/field_exports`
  - case 内没有 `design_state.json`
  - case 内没有 per-case `field_exports/manifest.json`
  - dataset 级存在：
    - `field_run_summary.json`
    - `render_summary.json`
- 适配后：
  - 入口被先归一为 `<dataset>/cases/case_0000`
  - `metrics` 从 `field_run_summary.json` 回填
  - `temperature_field / displacement_field / stress_field / triptych` 从 `render_summary.json` 解析
  - 最终 `IterationReviewPackage.package_status = linked_field_assets`

对应测试：

- `tests/test_review_package_case_adapter.py::test_case_adapter_normalizes_field_exports_dir_and_uses_dataset_summary_metadata`

## 6. 仍然不兼容的旧 case

本次明确不兼容以下旧 case：

1. 只有 `field_exports/grid/*.txt` 或 `field_exports/vtu/*.vtu`，但没有：
   - `manifest.json`
   - `simulation_result.json`
   - `field_run_summary.json` case 条目

2. 只有零散 PNG，但没有：
   - `renders/manifest.json`
   - `render_summary.json` case 条目
   - 也不符合约定文件名

3. 路径只指向 dataset root，但 `cases/` 下面没有任何兼容 case

4. 破损 summary：summary 里有 case 条目，但路径都失效，最终既无法得到 metrics，也无法得到 render artifact

这些场景都按“最小高价值适配之外”处理，保留 fail-fast，不做自动修复。

## 7. 改动文件

- `visualization/review_package/contracts.py`
  - 新增 `CaseContractAdapterInfo`
  - `PhysicsProfileInfo` 增加 `case_contract`
- `visualization/review_package/iteration_builder.py`
  - 新增旧 case 识别、路径归一化、dataset summary fallback、清晰 fail-fast
- `tests/test_review_package_case_adapter.py`
  - 新增 design-state-only / field-exports+summary / fail-fast 三类最小测试

## 8. 最小验证

执行命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_review_package_case_adapter.py tests/test_iteration_review_package.py -q
```

结果：

- `7 passed`
