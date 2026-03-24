# 0010-satellite-archetype-and-reference-baseline

- status: accepted
- date: 2026-03-10
- deciders: msgalaxy-core
- cross-reference:
  - `R40_satellite_reference_baseline_20260310`
  - `R41_catalog_shell_geometry_upgrade_20260310`
  - `R43_operator_dsl_v4_and_rule_governance_20260310`
  - `R45_0010_integration_handoff_20260311`

## Context

MsGalaxy 当前真实基线仍然以通用 `ComponentGeometry` 和场景 BOM 驱动，初始布局主要来自：

- 盒体/圆柱代理；
- layout seed service；
- 几何与约束主导的通用布局搜索。

这条路径在“可执行布局优化”层面是成立的，但在卫星领域存在三个根本问题：

1. 初始建模缺少“卫星原型”这一层，导致视觉与结构语义容易退化为任意积木堆；
2. 组件虽然带有 `payload / power / avionics / comm / adcs` 等类别，但没有和卫星平台类型、任务面、载荷面、附体布局建立稳定映射；
3. 教师/评审关心的不只是“约束是否满足”，还关心“第一眼看起来是否像真实卫星的某个原型变体”。

公开、官方来源已经足以支持“形态语法级”建模，而无需复制任何专有 CAD。典型来源包括：

- NASA CubeSat 设计规范与 SmallSat SoA；
- EnduroSat、GomSpace、SSTL 等公开平台介绍；
- 中国官方对小卫星平台、北斗系统与导航卫星平台特征的公开说明。

因此，需要在现有 `mass` scenario runtime 之上增加一层“卫星原型事实基线”，把“像卫星”从口头要求变成正式合同。

## Problem Statement

需要明确回答以下问题：

- 初始生成应从什么对象开始，而不是从随机盒体开始；
- 如何把公开卫星资料沉淀为可执行的形态语法，而不是仅作为图片灵感；
- 如何定义“卫星感外形闸门”，使 teacher/demo 主链只接收具有明确原型归属的布局；
- 如何在不复制专有 CAD 的前提下，让系统偏向真实卫星领域而非任意三维布局问题。

如果不在本阶段做出明确决策，后续目录件、开窗、规则、可视化仍会漂浮在“无原型约束”的盒体系统之上。

## Decision

### 1. 初始建模对象固定为 `SatelliteArchetype`

新架构中的初始建模不再从“组件 + 包络”直接开始，而是从 `SatelliteArchetype` 开始。

`SatelliteArchetype` 至少包含：

- `archetype_id`
- `mission_class`
- `bus_topology`
- `task_face_semantics`
- `external_appendage_schema`
- `interior_zone_schema`
- `attitude_semantics`
- `allowed_shell_variants`
- `default_rule_profile`

### 2. 建立 `SatelliteReferenceBaseline`

`SatelliteReferenceBaseline` 是一套基于公开官方资料提炼的“形态语法库”，不是 CAD 仓库。

其作用是沉淀如下事实：

- 各类卫星原型的典型总线形态；
- 任务面/载荷面/散热面/太阳翼面/天线面等语义；
- 外部附体的典型存在方式；
- 内部分区与组件族的典型归位关系；
- 哪些特征允许变化，哪些特征不能越界。

### 3. 第一批原型族固定为 5 类

为避免 v1 范围失控，第一批只冻结以下 5 类原型：

1. `navigation_satellite`
2. `optical_remote_sensing_microsat`
3. `radar_or_comm_payload_microsat`
4. `cubesat_modular_bus`
5. `science_experiment_smallsat`

### 4. 形态语法的最小字段固定

每个原型必须至少定义以下形态字段：

- 主承力总线类型：`monocoque | panel_bus | modular_frame | cubesat_rail`
- 主任务面：如 `+Z payload face`、`nadir face`、`antenna face`
- 外部附体模板：如太阳翼、天线、雷达罩、镜筒、遮光罩
- 机壳/面板可用开窗位
- 内部分区：电源区、星务区、载荷区、热敏区、承力区
- 姿态语义：如 `earth-pointing`、`nadir-looking`、`antenna boresight stable`

### 5. 初始生成流程固定

初始生成流程正式固定为：

`任务类型 -> 原型族选择 -> 目录件实例化 -> 规则约束布局 -> 卫星感外形闸门`

其中：

- 原型族负责“像哪一类卫星”；
- 目录件负责“放哪些真实/准真实器件”；
- 规则约束负责“怎样摆才能符合工程语义”；
- 外形闸门负责“是否还能被老师一眼看成卫星变体”。

### 6. 引入 `SatelliteLikenessGate`

`SatelliteLikenessGate` 是 teacher/demo 主链的强制闸门。

它至少检查：

- 是否匹配某个 archetype；
- 总线外形比例是否落在 archetype 允许范围；
- 任务面和 aperture/载荷关系是否正确；
- 外部附体数量、位置与朝向是否越界；
- 内部分区是否被明显破坏；
- 是否存在“随机堆叠感”过强的自由造型。

该闸门失败的布局可以继续作为 diagnostic 结果存在，但不能进入 teacher/demo 主链。

### 7. 禁止复制专有 CAD

本 ADR 接受的事实基线只允许提炼：

- 形态规律；
- 分区逻辑；
- 公开尺寸级别；
- 对外公开的结构/接口信息。

明确禁止：

- 复刻厂商内部 CAD；
- 声称对某一型号进行了精确实体还原；
- 用公开图片逆向出未经授权的内部结构。

### 8. 旧通用盒体路径降级为 legacy diagnostic

通用盒体布局路径不会立刻删除，但其角色降级为：

- legacy benchmark；
- diagnostic fallback；
- 非 teacher/release 主链的快速实验路径。

它不再代表下一阶段的默认卫星建模口径。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-24 的真实实现）

- `domain/satellite/contracts.py` 中已存在正式的 `SatelliteArchetype / MorphologyGrammar / SatelliteReferenceBaseline` 最小合同；
- `config/satellite_archetypes/public_reference_baseline.json` 已提供公开资料驱动的 archetype baseline；
- `domain/satellite/gate.py` 中已存在规则化 `SatelliteLikenessGate` skeleton；
- `domain/satellite/seed.py` 已把 `satellite_archetype_id / satellite_default_rule_profile` 回写到 `DesignState.metadata`；
- `domain/satellite/runtime.py` 现会对 aperture 对齐的载荷优先使用 `placement_state.aperture_site + mount_face` 解析 `payload_face`，避免仅按位置分数误选侧面组件；
- `optical_remote_sensing_microsat` 的 `optical_avionics_middeck` grammar 当前已与活跃 `optical_remote_sensing_bus` scenario 对齐，允许 `communication` 类侧装天线面板；
- `workflow/scenario_runtime.py` 主线现已在 `proxy_feasible` 之后、`STEP/COMSOL` 之前执行卫星 likeness gate：
  - 默认 `satellite_likeness_gate_mode = strict`
  - gate 失败会以 `comsol_block_reason=satellite_likeness_failed` 阻断真实物理链
  - `summary / report / result_index` 会稳定沉淀 candidate / gate report / resolution audit
- 2026-03-24 同日 3 次独立主线复跑已表明：修复后的 strict gate 不再误阻断活跃 optical bus，三次均进入 `fields_exported` 且 `real_feasible=true`
- 当前仍未把该 gate 扩展成 teacher/demo 专用的高保真几何审校器，也未完成多场景 release-grade 证明。

### Accepted Target（本 ADR 接受的目标架构）

- 初始建模切换为 `SatelliteArchetype` 驱动；
- 建立 `SatelliteReferenceBaseline`；
- 建立 teacher/demo 强制 `SatelliteLikenessGate`；
- 原型族成为目录件、规则、几何与审阅链的统一上游。

### Deferred（明确延后）

- 不在本 ADR 中引入高保真厂商 CAD；
- 不在本 ADR 中一次性覆盖所有卫星家族；
- 不在本 ADR 中直接重写现有求解器，只冻结原型与形态语法合同。

## Consequences

### Positive

- 初始模型将从“任意布局”转向“卫星原型变体”；
- 后续机壳、开窗、载荷对窗、规则和可视化都有稳定上游；
- teacher/demo 场景能统一回答“为什么这个布局长这样”。

### Negative

- 新增一层原型治理，增加资料抽取和维护成本；
- 一部分历史案例会因为“不像卫星”而被降级出主链；
- 原型选择错误会直接污染后续几何与规则决策。

### Neutral / Tradeoff

- 该决策优先保证“领域真实性”，而不是最大化几何自由度；
- 视觉上更像卫星，意味着搜索空间会被显著收紧。

## Follow-up

后续实施至少应覆盖：

1. 定义 `SatelliteArchetype` / `MorphologyGrammar` / `SatelliteLikenessGate` 合同；
2. 基于公开资料建立 5 类 archetype baseline；
3. 将 archetype 选择接入初始案例生成；
4. 将外形闸门接入 teacher/demo profile；
5. 在 report 中记录每个 archetype 的来源边界与不可越界项。
