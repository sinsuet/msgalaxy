# 0013-operator-dsl-v4-and-placement-rule-engine

- status: accepted
- date: 2026-03-10
- deciders: msgalaxy-core
- cross-reference:
  - `R43_operator_dsl_v4_and_rule_governance_20260310`
  - `R48_0013_integration_handoff_20260311`
  - `R53_v4_operator_family_mapping_20260311`
  - `R55_v4_operator_semantic_caption_review_contract_20260311`
  - `R40_satellite_reference_baseline_20260310`
  - `R44_iteration_review_package_20260310`

## Context

当前 `Operator Program DSL v3` 已经是可执行薄切片，但其真实形态仍偏向通用布局操作：

- `group_move`
- `cg_recenter`
- `hot_spread`
- `swap`
- `add_heatstrap`
- `set_thermal_contact`
- `add_bracket`
- `stiffener_insert`
- `bus_proximity_opt`
- `fov_keepout_push`

这组动作对执行链是有价值的，但对卫星领域表达存在明显不足：

- 算子名称不直接对应卫星工程语义；
- 开窗/载荷对窗/挂载面/承力区/任务面等概念无法成为一等公民；
- 规则大多靠 metric/constraint 间接体现，缺乏“为何这样摆”的领域解释。

## Problem Statement

需要明确：

- 新一代算子是否继续围绕通用位移/交换展开；
- 卫星布局规则如何拆成硬规则与软偏好；
- 算子如何直接消费 `SatelliteArchetype`、目录件、机壳/aperture、zone 等对象；
- `vop_maas` 输出应该描述什么层级的操作；
- 旧 DSL v3 与新 DSL v4 的过渡边界是什么。

## Decision

### 1. 新公开算子切换到领域语义

下一阶段对外公开的主算子固定为领域语义，而不是通用几何语义。最小动作集包括：

- `place_on_panel`
- `align_payload_to_aperture`
- `reorient_to_allowed_face`
- `mount_to_bracket_site`
- `move_heat_source_to_radiator_zone`
- `separate_hot_pair`
- `add_heatstrap`
- `add_thermal_pad`
- `add_mount_bracket`
- `rebalance_cg_by_group_shift`
- `shorten_power_bus`
- `protect_fov_keepout`
- `activate_aperture_site`

### 2. 旧 DSL v3 降级为底层 mutation kernel

`group_move / swap / hot_spread / cg_recenter` 等动作不会立即删除，但其角色降级为：

- 内部实现内核；
- 兼容旧路径；
- v4 高层动作的 realization backend。

teacher/demo、论文与新审阅链优先使用 v4 语义，不再把 v3 作为主叙事层。

### 3. 规则层正式拆为硬规则与软偏好

硬规则至少包括：

- 壳体与 aperture 匹配；
- 安装面合法性；
- 朝向合法性；
- 碰撞/间隙；
- FOV/EMC keepout；
- CG；
- 热/结构边界；
- 目录件接口约束。

软偏好至少包括：

- 电池靠承力区；
- 载荷贴任务面；
- 热源靠散热面；
- 飞轮/ADCS 靠质心邻域；
- 线缆/母线更短；
- 左右/前后布置更对称；
- 可维护性更高。

### 4. 规则必须显式绑定领域对象

新规则和算子必须能显式引用：

- `SatelliteArchetype`
- `CatalogComponentSpec`
- `ShellSpec`
- `ApertureSiteSpec`
- `MountInterfaceSpec`
- `ZoneSpec`

不能继续只靠“组件类别 + 坐标区间”表达全部卫星规则。

### 5. `vop_maas` 输出切换为“领域操作 + 预期效果”

`vop_maas` 的下一阶段输出不再只是一组通用 operator candidate，而是包含：

- 领域动作；
- 目标对象；
- 受影响规则；
- 预期指标变化；
- 预期物理证据；
- 失败时的 fallback 解释。

### 6. v4 不允许把尺寸自由化当作默认逃逸手段

为了防止系统继续靠“改尺寸逃约束”，v4 明确规定：

- teacher/release 主链中，目录件主形体尺寸不属于默认动作；
- 如需引入尺寸变化，只能针对 `placeholder` 或明确声明为 parametric prototype 的对象；
- 不允许把“拉伸/缩小器件”当作通用可行化手段。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-11 的真实实现）

- `optimization/modes/mass/operator_program_v4.py` 已冻结公开 v4 动作集合，并提供 canonical `action -> family` 映射与 payload normalization helper；
- `core.visualization._operator_action_family(...)`、review package builder、Blender review sidecar 已稳定消费 v4 family map，`geometry/aperture/thermal/structural/power/mission` 六大家族不再在 review/visualization 统计中大面积落到 `other`；
- review-facing DSL v4 语义说明薄切片已落地：`primary_action_label / semantic_caption / target_summary / rule_summary / expected_effect_summary / observed_effect_summary` 已可写入 `IterationReviewPackage`、`package_index.json`、`review_payload.json`、`render_brief.md`、`report.md` 与 `visualization_summary.txt`；
- DSL v3 executor thin-slice 仍是当前真实执行基线；规则仍大量依赖既有 metrics / constraints，尚未在本 ADR 内完成全面 v4 realization backend 与 rule-engine rewrite。

### Accepted Target（本 ADR 接受的目标架构）

- 对外语义切换为 DSL v4；
- 规则层正式拆分为硬规则与软偏好；
- v3 退居 realization backend；
- `vop_maas` 输出升级为领域操作与证据导向结构。

### Deferred（明确延后）

- 不在本 ADR 中一次性重写所有 executor；
- 不在本 ADR 中删除 v3；
- 不在本 ADR 中实现完整神经规则学习，仅冻结 DSL 与规则治理边界。

## Consequences

### Positive

- 算子会更贴近卫星工程语言，teacher 更容易理解；
- `vop_maas` 的策略输出更容易与规则、几何和物理证据对齐；
- 后续论文叙事将从“移动盒体”升级为“执行卫星工程动作”。

### Negative

- 需要重写一层 DSL、validator、realization mapping；
- 旧的 operator bias、credit 和 logs 需要迁移；
- v3 与 v4 并行一段时间会增加维护成本。

### Neutral / Tradeoff

- 高层语义更强，意味着实现复杂度更高；
- 但这能换来更强的可解释性和更清晰的教师演示效果。

## Follow-up

后续实施至少应覆盖：

1. 继续补强 v4 schema / validation 覆盖与 `vop_maas` 输出合同；
2. 扩展 v4 -> v3 / low-level realization mapping，避免仅在消费侧落语义；
3. 建立可执行的硬规则与软偏好 rule registry，并和 runtime gate 对齐；
4. 将 round-level observability 从 family/caption 扩展到更细粒度的领域动作与效果归因；
5. 保持 v3 兼容路径可审计，直到 v4 executor 覆盖足够完整后再讨论主线切换。
