# R49 0014 Integration Handoff 2026-03-11

> 状态更新（2026-03-11，晚于本报告原始切片）：
> 本文主体记录的是 ADR-0014 第一阶段 integration handoff，当时的范围与结论不等于当前最新状态。
> 后续增量已补齐：
> - `R54_case_contract_adapter_20260311`：旧 case -> review contract 最小适配
> - `R55_teacher_demo_field_case_gate_20260311`：`teacher_demo` strict field-case gate
> - `HANDOFF.md` 与 `README.md` 已在后续同步中更新为最新真实状态

## 任务范围

本次交付严格限定在 `ADR-0014 iteration review package and teacher demo chain` 的最小可执行切片，不包含 `ADR-0010/0011/0012/0013` 的实现；以下正文描述的是该次集成切片当时的范围，不代表后续增量文档同步状态。

本次范围内的目标包括：

- 定义 `IterationReviewPackage` 最小 schema
- 定义并接入 `metric_registry / unit_registry / color_registry`
- 建立 `teacher_demo / research_fast` 两个 review profile 的最小合同
- 在现有 review/visualization 体系上新增 step 级 package builder
- 支持无真实 COMSOL 结果时也能生成 lightweight package manifest
- 低风险补齐最小 `before/after` 轻量图、`step_montage`、`timeline/keyframe montage`，以及在 linked field-case 场景下的 `dataset_overview`
- 将 iteration review 索引摘要接入 Blender sidecar payload / metadata

不在本次范围内的内容：

- 0010 `satellite archetype and reference baseline`
- 0011 `catalog shell/aperture geometry kernel`
- 0012 `COMSOL canonical satellite physics contract`
- 0013 `operator DSL v4 and placement rule engine`
- COMSOL 求解内核重构
- `simulation/` 主链重构
- `geometry/` 主链重构
- shell/aperture 几何建模
- 主 workflow 的重接线

## 实际改动

### 1. IterationReviewPackage schema

`IterationReviewPackage` schema 位于：

- `visualization/review_package/contracts.py`

当前最小 schema 已覆盖：

- `before / after` 状态索引
- `operator / action` 信息
- `physics profile / source claim`
- `metrics`
- `metric_deltas`
- `review_artifacts`

同时保留了对旧 DSL 的兼容字段，并为 v4 预留了 `v4_reserved` 扩展位。

### 2. Registry 与 profile contract

registry 位于：

- `visualization/review_package/registry.py`

当前已接入：

- `METRIC_REGISTRY`
- `UNIT_REGISTRY`
- `COLOR_REGISTRY`
- `REVIEW_PROFILE_REGISTRY`

`teacher_demo` 与 `research_fast` 的区别如下：

- `teacher_demo`
  - `package_level=full`
  - `shell_visual_policy=required`
  - `field_render_mode=prefer_linked`
  - `triptych_policy=prefer_existing`
  - 会生成 `before/after` 轻量图
  - 会生成 `step_montage`
  - 会生成 profile 级 `timeline_montage / keyframe_montage`
  - 在 linked field-case 存在时会生成 `dataset_overview`

- `research_fast`
  - `package_level=lightweight`
  - `checkpoint_only=True`
  - `field_render_mode=manifest_only`
  - `triptych_policy=skip`
  - 保留 manifest/index-first 路径
  - 不生成 `step_montage`
  - 不生成 aggregate montage

### 3. Step package builder

step builder 位于：

- `visualization/review_package/iteration_builder.py`

当前 builder 已落地的能力：

- 写出 root index / profile index / step manifest / metrics payload
- 生成本地 `geometry_before.png / geometry_after.png`
- 复用上游 `triptych.png` 或在有三场 PNG 时最小拼接生成 `triptych.png`
- 为 `teacher_demo` 生成 `step_montage.png`
- 为 `teacher_demo` 生成 `timeline_montage.png / keyframe_montage.png`
- 在 linked field-case 场景下生成 `dataset_overview/case_grid.png`

本次还修复了一个集成根因：

- 空字符串 `field_case_dir=""` 以前会被解析成当前目录 `.`，导致无 field-case 场景被误识别为 `field_case_linked`
- 当前已在 `_load_field_case_assets(...)` 处显式拦截空字符串

### 4. Blender sidecar 最小集成

最小接入位于：

- `visualization/blender_mcp/bundle_builder.py`
- `visualization/blender_mcp/contracts.py`
- `visualization/review_package/builders.py`

当前 sidecar 已具备：

- 将 review builder 的 `iteration_review_root / index_path / teacher_demo index / research_fast index` 写回 bundle/payload/manifest
- 在 `ReviewPayload` 中新增 `iteration_review` 摘要块
- 在 bundle / manifest metadata 中新增 `iteration_review_summary` 摘要

这使下游消费端不需要再次手工读取 `review_packages/index.json` 才能知道：

- 各 profile 的 package 数量
- linked field asset 数量
- `step_montage` 数量
- `timeline_montage / dataset_overview` 是否已物化

## 共享接口/配置/数据合同

### Schema / contract 位置

- `visualization/review_package/contracts.py`
  - `IterationReviewPackage`
  - `ReviewStateIndex`
  - `OperatorActionInfo`
  - `PhysicsProfileInfo`
  - `ReviewMetricCard`
  - `ReviewMetricDelta`
  - `ReviewArtifactRef`

### Registry 覆盖范围

#### metric_registry

当前覆盖：

- `best_cv`
- `max_temp`
- `temp_margin`
- `max_displacement`
- `max_stress`
- `min_clearance`
- `num_collisions`
- `boundary_violation`
- `cg_offset`
- `power_margin`
- `voltage_drop`
- `safety_factor`
- `first_modal_freq`
- `mission_keepout_violation`
- `packing_efficiency`
- `rule_violation_count`

当前 alias：

- `fill_ratio -> packing_efficiency`
- `max_von_mises -> max_stress`

#### unit_registry

当前覆盖：

- `unitless`
- `temperature_kelvin`
- `length_mm`
- `stress_pa`
- `stress_mpa`
- `frequency_hz`
- `electric_potential_v`

#### color_registry

当前覆盖：

- `temperature_field`
- `displacement_field`
- `stress_field`
- `geometry_overlay`
- `before_layout_view`
- `after_layout_view`

### Builder 依赖的上游产物

当前 package builder 依赖：

- `summary.json`
- layout snapshot records
  - 优先走现有 `load_layout_snapshot_records(...)`
  - 无事件流时回退到 `snapshots/*.json`

可选 field-case 上游：

- `tools/comsol_field_demo/.../renders/manifest.json`
- `tools/comsol_field_demo/.../field_exports/manifest.json`
- `tools/comsol_field_demo/.../field_exports/simulation_result.json`
- `tools/comsol_field_demo/.../tensor/manifest.json`

可选映射输入：

- `field_case_dir`
- `field_case_map`

### 当前缺的上游能力

当前仍缺（以本报告原始切片为准，后续实现见顶部状态更新）：

- step 到 field-case 的自动运行时推断
- 更高保真的 shell / Blender 版本 before-after 图
- 更完整的数据集总览逻辑，当前 `dataset_overview` 只是最小 contact sheet

## 最小验证与结果

本次仅执行最小范围验证，没有跑全量测试。

执行：

```bash
conda run -n msgalaxy pytest tests/test_iteration_review_package.py tests/test_blender_render_bundle.py::test_build_render_bundle_from_run -q
```

结果：

- `5 passed in 1.90s`

覆盖点包括：

- `IterationReviewPackage` schema 最小可用性
- registry / profile contract
- step manifest/index 生成
- `before/after` 轻量图生成
- `teacher_demo` 下的 `step_montage`
- `teacher_demo` 下的 `timeline/keyframe montage`
- linked field-case 场景下的 `dataset_overview`
- Blender sidecar 中的 `iteration_review` payload digest 与 metadata digest

## 未完成项/风险

- `dataset_overview` 目前是低风险 contact sheet，不是完整 dashboard 或数据集分析视图
- `research_fast` 仍然保持 lightweight，不物化 montage；若后续有研究态快速拼图需求，需要单独定义合同
- 当前 `before/after` 图是本地 2D top-view，不是 Blender shell 级图
- review package 摘要虽已在后续增量中接回 `summary/report/visualization` 主消费链，但更完整的 teacher-facing dashboard 和数据集联查仍未实现
- field-case 映射仍主要依赖显式传参或外部 summary hint，未形成自动推断闭环

## 对集成测试的建议

建议后续集成测试只补以下几类，不要直接扩大到全量：

- `review_package`:
  - 无 field-case 的 lightweight 路径
  - 有 field-case map 的 linked-field 路径
  - `teacher_demo / research_fast` profile 差异断言

- `blender sidecar`:
  - `review_payload.iteration_review` digest 是否稳定
  - bundle / manifest metadata 的 `iteration_review_summary` 是否稳定

- `field-case contract`:
  - `renders/manifest.json` 缺字段时的降级行为
  - `field_exports/manifest.json` 与 `simulation_result.json` 不一致时的优先级行为

- `aggregate outputs`:
  - 无 linked field-case 时 `dataset_overview.exists == False`
  - 有 linked field-case 且有 `triptych` 时 `dataset_overview.exists == True`
