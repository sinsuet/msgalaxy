# R43 卫星领域算子 DSL v4 与规则治理规划（2026-03-10）

## 1. 目的

本报告用于把 MsGalaxy 的算子和规则体系从“通用布局变换”升级为“卫星领域动作与规则合同”。

对应 ADR：

- `docs/adr/0013-operator-dsl-v4-and-placement-rule-engine.md`

## 2. 当前状态审计

当前 DSL v3 的 10 个 canonical 动作已经形成稳定执行薄切片，这是宝贵的现有资产。但它们主要围绕：

- 位移；
- 交换；
- 热接触调整；
- 支架/刚化件添加；
- keepout 推离；
- 母线邻近优化。

问题不是这些动作错，而是它们处在“实现层语义”，而不是“卫星工程语义”。

这导致三类问题：

1. `vop_maas` 难以清晰说明“这步到底在做什么卫星工程动作”；
2. teacher/demo 很难从 operator 名称直接理解意图；
3. shell、aperture、task face、mount site、承力区等领域对象无法成为一等 action target。

## 3. v4 动作分层

建议把动作明确分为三层：

### 3.1 领域动作层（对外叙事层）

这是 teacher/demo、报告、论文的主语义层。推荐最小动作集：

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

### 3.2 领域实现层（规则与对象绑定层）

这一层负责把高层动作落到具体对象：

- 哪个 panel；
- 哪个 aperture；
- 哪个 zone；
- 哪组 component；
- 哪个 mount site；
- 预期影响哪些 metric/rule。

### 3.3 mutation kernel 层（兼容实现层）

继续复用或逐步替换现有：

- `group_move`
- `swap`
- `hot_spread`
- `cg_recenter`
- 其他结构/热/母线实现原语。

该层不再作为 teacher-facing 语义。

## 4. 规则治理规划

### 4.1 硬规则

至少包含：

- 壳体包络；
- aperture/payload 匹配；
- 安装面合法性；
- 朝向合法性；
- 碰撞/间隙；
- FOV/EMC keepout；
- CG 限制；
- 热边界；
- 结构边界；
- 目录件接口约束。

### 4.2 软偏好

至少包含：

- 电池靠承力区/低质心区；
- 载荷贴任务面；
- 热源靠散热面；
- 飞轮/ADCS 靠质心邻域；
- 母线更短；
- 对称性更好；
- 可维护性更高。

### 4.3 registry 化

建议建立：

- `rule_registry`
- `operator_registry`
- `operator_to_rule_impact_registry`

每个动作都要明确：

- 修改对象；
- 允许前置条件；
- 影响的硬规则/软偏好；
- 预期指标；
- 失败 fallback。

## 5. `vop_maas` 输出升级

下一阶段 `vop_maas` 输出建议至少包含：

- `action`
- `target_objects`
- `preconditions`
- `expected_rule_effects`
- `expected_metric_deltas`
- `expected_visual_evidence`
- `fallback_action`

这使 `vop_maas` 不再只是给出“下一步试试 move/swap”，而是明确：

- 为什么做；
- 想改善什么；
- 期望从哪类三场或指标上看到效果。

## 6. 与卫星原型、机壳和 COMSOL 的关系

v4 不是孤立升级，必须绑定上游和下游：

- 上游绑定 `SatelliteArchetype` 和目录件对象；
- 中游绑定 shell/panel/aperture/mount site；
- 下游绑定 review package 和 COMSOL source claim。

举例：

- `align_payload_to_aperture`
  - 上游：payload 类型、任务 archetype
  - 中游：aperture site / task face
  - 下游：FOV/keepout、温度场、结构载荷路径、审阅图

## 7. 并行实施边界

该主题建议拆成三个并行包：

### WP-A：DSL schema 与 validator

- 新 v4 schema；
- 参数规范；
- target object 绑定规则。

### WP-B：rule registry 与对象模型

- 硬规则/软偏好 registry；
- zone/panel/aperture/mount site object binding；
- metric linkage。

### WP-C：realization mapping 与 observability

- v4 -> v3/low-level kernel mapping；
- 日志、report、review package 改为 v4 语义；
- operator credit/attribution 迁移。

## 8. 验收标准

- operator 名称能直接被老师理解；
- 规则能显式绑定到 shell、panel、aperture、zone；
- 不允许通过改尺寸规避主规则；
- review package 能显示“这步做了什么卫星工程动作、带来了什么效果”。

## 9. 风险

- DSL v4 与旧执行链之间会有一段双轨期；
- 若对象模型没先冻结，v4 动作会漂浮在空中；
- 若 rule registry 设计不清，后续会重复回到 metric-only 推理。

建议先实现“少量高价值动作 + 少量高价值规则”的垂直切片。
