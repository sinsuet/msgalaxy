# R44 Step 级审阅包与教师展示链规划（2026-03-10）

> 状态说明（2026-03-11）：
> 本文是 ADR-0014 的规划文档，不是最新实现状态。
> 最新落地情况请结合以下文档阅读：
> - `R49_0014_integration_handoff_20260311`
> - `R54_case_contract_adapter_20260311`
> - `R55_teacher_demo_field_case_gate_20260311`
> - `HANDOFF.md`

## 1. 目的

本报告用于把 MsGalaxy 的结果消费面从“run summary + 零散图”升级为“step 级可审阅、可展示的标准产物链”。

对应 ADR：

- `docs/adr/0014-iteration-review-package-and-teacher-demo-chain.md`

## 2. 当前状态审计

当前仓库已经有三类可视化资产：

1. `summary / report / tables` 的审计文本链；
2. Blender sidecar / review package P0；
3. `tools/comsol_field_demo` 的三场 PNG 与 montage 原型。

但这些资产尚未被组织成统一 teacher-facing 产物：

- 图和指标没有稳定绑定；
- 每个 operator step 的前后差异没有形成标准产物；
- 机壳、aperture、卫星原型语义没有稳定进入审阅主链；
- 字段、单位、色标上界下界仍有分散处理风险。

## 3. `IterationReviewPackage` 规划

建议每个接受 step 都固定产生如下结构：

### 3.1 核心元数据

- `run_id`
- `iteration`
- `attempt`
- `step_index`
- `policy_id`
- `operator_action`
- `physics_profile`
- `source_claim`
- `archetype_id`

### 3.2 几何与状态差分

- `before_layout_view`
- `after_layout_view`
- `moved_components`
- `activated_aperture_sites`
- `mount_changes`
- `thermal_contact_changes`

### 3.3 三场结果

- `temperature_field`
- `displacement_field`
- `stress_field`
- `geometry_overlay`

### 3.4 指标卡

最小集合建议固定为：

- `max_temp`
- `temp_margin`
- `max_displacement`
- `max_von_mises`
- `min_clearance`
- `cg_offset`
- `power_margin`
- `fill_ratio`
- `rule_violation_count`

同时保留：

- `metric_deltas`
- `dominant_violation_change`

### 3.5 解释信息

- `operator_rationale`
- `expected_effects`
- `observed_effects`
- `accept_or_reject_reason`

## 4. teacher_demo 与 research_fast

建议显式分成两个消费 profile：

### 4.1 `teacher_demo`

目标：

- 高可读性；
- 每步可讲清；
- 三场与指标可直接展示。

特点：

- 每个接受 step 都可导出完整 review package；
- 图面统一风格；
- 支持 montage 和多案例 overview。

### 4.2 `research_fast`

目标：

- 不拖垮搜索；
- 强调 checkpoint 与关键帧审阅。

特点：

- 只在关键节点做完整三场；
- 保留轻量级指标追踪；
- 和 `teacher_demo` 共享 registry，但不要求每步全量导出。

## 5. metric / unit / color registry

建议新增三类统一注册表：

### 5.1 `metric_registry`

定义：

- 指标名；
- 含义；
- 来源；
- 方向（越大越好/越小越好）；
- 允许展示范围。

### 5.2 `unit_registry`

定义：

- 字段 -> 单位；
- report / PNG / tensor / JSON 统一映射；
- 任何脚本不得自行猜测。

### 5.3 `color_registry`

定义：

- 每类物理场使用的 colormap；
- 统一上下界来源；
- montage 大图 colorbar 规范；
- 小图默认不显示重复 colorbar/title 的规则。

## 6. 产物目录规划

建议未来单个 step 的标准目录如下：

```text
review_packages/
  step_000/
    review_manifest.json
    metrics.json
    geometry_before.png
    geometry_after.png
    geometry_overlay.png
    temperature_field.png
    displacement_field.png
    stress_field.png
    triptych.png
    triptych_with_bars.png
```

单案例全流程：

```text
review_packages/
  montage/
    timeline_montage.png
    keyframe_montage.png
```

多案例展示：

```text
review_packages/
  dataset_overview/
    case_grid.png
    archetype_grid.png
```

## 7. 与 COMSOL 和 DSL v4 的关系

`IterationReviewPackage` 不是单纯渲染问题，它依赖：

- COMSOL canonical field export；
- 机壳/aperture 真几何；
- DSL v4 的领域语义；
- archetype 与 shell source claim。

若没有这些上游事实，review package 仍会退化成“漂亮图片”，而不是“工程审阅包”。

## 8. 并行实施边界

该主题建议拆为三个并行包：

### WP-A：schema 与 registry

- 定义 package schema；
- 定义 metric/unit/color registry；
- 定义 manifest。

### WP-B：render pipeline

- 三场图片标准化；
- before/after 几何图；
- triptych/montage 输出。

### WP-C：run integration

- `vop_maas` 接入 step 级索引；
- 审阅包写入产物目录；
- report/summary 连接 review package。

## 9. 验收标准

- 每个接受 step 至少有一份统一 review package；
- 老师可以直接看三联图、指标和前后变化；
- 机壳和载荷面始终可见；
- 单位、色标、字段名在不同 case 间一致；
- 可以自动汇总成单案例 montage 与多案例 overview。

## 10. 风险

- teacher_demo profile 容易放大 COMSOL 成本；
- 产物数量上升，存储和索引压力会增加；
- 若 registry 不先冻结，后续图片风格会再次分散。

建议先把 schema 与 registry 固定，再逐步接入渲染与 run integration。
