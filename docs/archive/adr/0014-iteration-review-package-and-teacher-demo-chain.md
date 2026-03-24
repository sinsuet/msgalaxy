# 0014-iteration-review-package-and-teacher-demo-chain

- status: superseded
- date: 2026-03-10
- deciders: msgalaxy-core
- superseded_by: `0015-same-repo-single-core-scenario-runtime`
- cross-reference:
  - `R44_iteration_review_package_20260310`
  - `R49_0014_integration_handoff_20260311`
  - `R54_case_contract_adapter_20260311`
  - `R55_teacher_demo_field_case_gate_20260311`
  - `R55_v4_operator_semantic_caption_review_contract_20260311`
  - `R42_comsol_canonical_satellite_physics_20260310`
  - `R43_operator_dsl_v4_and_rule_governance_20260310`

> 2026-03-11 状态更新：
> 对应的 Blender/review/teacher-demo/field-demo tracked 代码表面已从当前主仓主线移除。
> 本 ADR 仅保留为历史参考，不再描述当前实现状态；当前主线以 `HANDOFF.md` 与 `0015-same-repo-single-core-scenario-runtime` 为准。

## Context

当前仓内已具备：

- run summary / report / tables / visualization summary；
- Blender sidecar 与 review package P0；
- `tools/comsol_field_demo` 的三场导出与 montage 原型。

但教师反馈表明，现有可视化与运行证据仍不够直观，主要问题是：

- 没有把每个 operator step 的前后变化讲清楚；
- 三场与指标没有被稳定绑定成一个标准审阅包；
- 单位、色标、标题、布局样式容易分散在多个脚本中各自处理；
- 机壳与真实卫星语义没有稳定进入 teacher-facing 主消费面。

## Problem Statement

需要明确：

- teacher/demo 主链究竟应该消费什么产物，而不是只看 run summary；
- 每个 step 需要固定输出哪些字段；
- 三场图片、指标卡、operator 解释、违规变化怎样组合；
- 是否要区分 teacher/demo 与 research/fast 两类审阅链；
- registry 应怎样约束字段名、单位与色标范围。

## Decision

### 1. 正式定义 `IterationReviewPackage`

`vop_maas` 每个被接受的 step 都必须产出一个标准 `IterationReviewPackage`。

其最小内容固定为：

- `before` 机壳+组件视图；
- `after` 机壳+组件视图；
- `temperature_field`；
- `displacement_field`；
- `stress_field`；
- 指标卡；
- 指标 delta；
- operator 解释；
- 违规变化摘要；
- source claim 与 profile 信息。

### 2. teacher/demo 与 research/fast 双 profile 分流

为避免后续算力与展示目标冲突，审阅链正式分为：

- `teacher_demo`：以高可读性、每步三场和最终 montage 为目标；
- `research_fast`：以搜索效率和 checkpoint 审阅为目标。

`teacher_demo` 不自动等价于默认搜索期 profile。

### 3. 指标、单位、色标由 registry 统一治理

新增三类 registry：

- `metric_registry`
- `unit_registry`
- `color_registry`

teacher/demo 主链中：

- 小图不重复写 colorbar/title；
- 大图统一对齐视角、量纲和色标上下界；
- 每张大图保留明确的 source claim；
- 不允许不同脚本对同一字段各自定义单位和色标。

### 4. 机壳成为审阅包默认视觉基座

teacher/demo 主链下：

- 所有三场默认包含机壳实体；
- 机壳既是几何参考，也承载热/结构分布；
- 不再允许“只有组件 + 透明外框”的默认 teacher 图。

### 5. montage 与数据集总览成为正式产物

除了 step 级 `IterationReviewPackage` 外，teacher/demo 主链还应固定支持：

- 单案例全流程 montage；
- 多案例网格拼图；
- 数据集总览大图。

这些产物用于展示：

- 单个方案如何迭代变好；
- 多个样本是否都保持卫星感；
- 数据集是否具备一致的三场审阅标准。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-11 的真实实现）

- 已有标准化 `IterationReviewPackage` 最小 schema、`metric/unit/color` registry 与 `teacher_demo / research_fast` 双 profile；
- 已有 step/root/profile index、本地 `before/after` 轻量图、`triptych / step_montage / timeline_montage / keyframe_montage` 最小产物；
- 已将 iteration review 摘要接回 `summary.json / report.md / visualizations/visualization_summary.txt` 与 Blender sidecar digest，并稳定暴露 `operator_family_audit`；
- 已支持旧 case 最小 adapter：识别 `design_state.json`、已有 `field_exports`、已有 render/summary metadata，并保留 clear fail-fast；
- `teacher_demo` 已具备 `field_case_gate`，仅在 linked field-case 绑定严格时放行；`research_fast` 仍保持轻量检查链；
- DSL v4 review 语义说明已进入 step/package/index/bundle/brief/report/summary 消费面：`primary_action_label / semantic_caption / target_summary / rule_summary / expected_effect_summary / observed_effect_summary` 已作为 review-facing 合同字段写出；
- 已有 Blender sidecar 与 `tools/comsol_field_demo` 原型级三场输出；当前 sidecar 已能消费 `review_payload.json`、`render_manifest.json v2`、三态 scene script 与 family/caption 摘要；
- 已有 `summary/report/tables` 审计基线。

### Accepted Target（本 ADR 接受的目标架构）

- 正式引入 `IterationReviewPackage`；
- teacher/demo 与 research/fast 双 profile 分流；
- metric/unit/color registry 统一治理；
- 机壳成为默认 teacher 审阅基座。

### Deferred（明确延后）

- 不在本 ADR 中直接承诺完整交互式 dashboard；
- 不在本 ADR 中把所有搜索迭代都强制跑高保真三场；
- 不在本 ADR 中承诺完整 teacher-demo 离线 dashboard、全自动高保真渲染 farm 或更丰富的数据集级 narrative UI；当前只落实离线 review package + Blender consumer thin-slice。
- 不在本 ADR 中自动修复所有历史 field-case 脏数据；只接受最小高价值旧 case adapter。

## Consequences

### Positive

- 教师可见的产物形态会稳定、统一、可复述；
- 每次 operator 的效果可以通过 before/after + 三场 + 指标直接呈现；
- 多案例数据集总览将有正式产物位置。

### Negative

- 审阅包产物数量与存储量显著上升；
- teacher_demo profile 对 COMSOL 预算更敏感；
- registry 治理需要额外维护。

### Neutral / Tradeoff

- 该决策优先保证“可审阅、可展示”，而不是最小产物体积；
- 通过 teacher/demo 与 research/fast 分流，平衡展示价值与算力成本。

## Follow-up

后续实施至少应覆盖：

1. 把高价值旧 dataset 从 `dataset_case_order` / `default_case_dir` 提升为显式 `field_case_map`，减少 `teacher_demo` gate 阻断；
2. 继续完善 dataset overview 与 Blender 主审阅面的联动，但不要把 dashboard 规划误报为已实现；
3. 把 semantic caption 从 primary-action 摘要扩展到更丰富的多动作因果叙事；
4. 在保持 `research_fast` 轻量链路的前提下，补充更多 teacher-facing 验证样例与定向回归。
