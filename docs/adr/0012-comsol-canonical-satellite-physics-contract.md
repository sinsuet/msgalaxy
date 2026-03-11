# 0012-comsol-canonical-satellite-physics-contract

- status: accepted
- date: 2026-03-10
- deciders: msgalaxy-core
- cross-reference:
  - `R42_comsol_canonical_satellite_physics_20260310`
  - `R41_catalog_shell_geometry_upgrade_20260310`
  - `R44_iteration_review_package_20260310`

## Context

MsGalaxy 当前 COMSOL 主链已经能完成热、结构、电学的可执行薄切片，但真实状态是“canonical 与 simplified 路径混杂共存”：

- 当前热场中存在 `TemperatureBoundary`、弱对流稳定锚、`P_scale` 功率缩放等简化数值策略；
- 当前结构、电学与耦合支路已具备骨架，但并未形成面向卫星领域的正式 profile 治理；
- 当前没有把“真实卫星热环境”和“诊断级简化环境”明确区分为不同合同；
- 历史调试中存在非规范命名和局部自定义表达，容易让外部误解为“真实物理配置”。

随着机壳、开窗、卫星原型进入主链，COMSOL 物理路径必须升级为“官方接口优先、profile 显式分层、source claim 可审计”的正式合同。

## Problem Statement

需要明确：

- 什么配置可以被称为 canonical satellite physics；
- 什么配置只能作为 diagnostic simplified path；
- 轨道热、表面对表面辐射、电热耦合、热-结构耦合如何在 profile 层明确；
- 如何防止旧的调试性物理设置继续被误用为 release-grade 结论；
- 如何让字段名、单位、dataset/export 形成稳定合同，服务后续三场审阅包。

## Decision

### 1. 正式引入 4 类 COMSOL 物理 profile

下一阶段正式 profile 固定为：

1. `thermal_static_canonical`
2. `thermal_orbital_canonical`
3. `electro_thermo_structural_canonical`
4. `diagnostic_simplified`

### 2. canonical profile 必须优先使用官方 COMSOL 接口

canonical path 的建模优先级固定为：

- 热场：`Heat Transfer in Solids`
- 需要辐射时：`Heat Transfer with Surface-to-Surface Radiation`
- 需要轨道热载荷时：`Orbital Thermal Loads`
- 电学：`Electric Currents`
- 结构：`Solid Mechanics`
- 耦合：使用官方多物理耦合接口而非自造命名路径

### 3. `diagnostic_simplified` 与 canonical 路径显式隔离

以下能力只允许保留在 `diagnostic_simplified`：

- `P_scale` 之类的调试缩放；
- 仅为数值稳定存在的弱对流锚；
- 简化边界温度锚点；
- 任何未经过 profile 命名和来源标签治理的临时自定义物理开关。

`diagnostic_simplified` 结果必须显式标注为：

- 非 release-grade；
- 非轨道真实热环境；
- 非最终 teacher 展示证据，除非用户明确接受 diagnostic 演示。

### 4. 禁止继续扩散非规范物理命名

今后不再把下列风格的名字写成主链合同：

- 自定义“辐射场模式”命名；
- 自定义“功率阶跃模式”命名；
- 任何未映射到 COMSOL 官方物理接口/study 类型的内部术语。

若需要时变功率或切换工况，必须归入以下正式机制之一：

- `Time Dependent Study`
- `Parametric Sweep`
- 正式函数/载荷曲线

### 5. 机壳实体必须进入三场求解

teacher/release profile 下：

- 机壳必须作为真实实体进入热场与结构场；
- aperture 与 panel variant 必须参与热边界、结构载荷路径和网格生成；
- 不允许再以“透明壳渲染”替代物理实体机壳。

### 6. 字段名、单位和导出合同固定

字段与单位合同至少固定：

- 温度：`T` / `K`
- 位移模：`solid.disp` / `m`
- 位移分量：`u,v,w` / `m`
- von Mises：`solid.mises` / `Pa`
- 模态频率：`Hz`

任何出图、tensor、审阅包都必须引用同一 registry，而不能在不同脚本中各自猜测单位或量纲。

### 7. 模块许可与降级必须显式审计

如果 `Orbital Thermal Loads` 或相关模块不可用：

- 允许显式降级到 `thermal_static_canonical`；
- 不允许静默冒充 `thermal_orbital_canonical`；
- 审计产物必须写出降级原因与模块缺失说明。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-10 的真实实现）

- COMSOL 热/结构/电学薄切片可执行；
- 结构、电学与耦合 study 有执行骨架；
- 现有导出脚本已能导出 `T / displacement / stress`。

### Accepted Target（本 ADR 接受的目标架构）

- 建立 4 类正式 profile；
- canonical 与 simplified path 显式分层；
- 机壳/aperture 进入三场真实求解；
- 字段名、单位与 dataset/export 合同统一治理。

### Deferred（明确延后）

- 不在本 ADR 中声称已完成轨道热高保真全链实现；
- 不在本 ADR 中立刻删除所有 simplified 逻辑；
- 不在本 ADR 中一次性补齐所有 mission/EMC 高保真物理域。

## Consequences

### Positive

- 后续三场审阅包与 teacher 演示将有稳定、可信的物理来源标签；
- “真实卫星热/结构/电学”与“诊断级近似”不再混淆；
- COMSOL 物理链的命名和验收将显著规范化。

### Negative

- canonical profile 的接线与模块依赖更复杂；
- 部分历史实验会因为 source claim 不满足而降级；
- profile 治理和导出 registry 需要额外维护成本。

### Neutral / Tradeoff

- 该决策优先保证 source claim 的严谨性，而不是追求所有案例都能无门槛跑通；
- simplified path 仍保留，但其地位从“默认主链”降为“受控诊断工具”。

## Follow-up

后续实施至少应覆盖：

1. 定义 canonical/simplified profile schema；
2. 为机壳/aperture 引入 profile-aware 边界与选择逻辑；
3. 建立字段/单位/dataset/export registry；
4. 在 summary/report/review package 中写出 source claim；
5. 将模块许可检查和降级原因纳入审计产物。
