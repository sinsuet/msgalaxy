# 0012-comsol-canonical-satellite-physics-contract

- status: accepted
- date: 2026-03-10
- updated: 2026-03-13
- deciders: msgalaxy-core
- cross-reference:
  - `0015-same-repo-single-core-scenario-runtime`
  - `R41_catalog_shell_geometry_upgrade_20260310`

## Context

MsGalaxy 的 COMSOL 主链已经进入 `mass` 单栈 scenario runtime，但历史上曾长期混用两类语义：

- 一类是面向正式 source claim 的 canonical COMSOL 接口；
- 另一类是调试期遗留的简化热路，包括 `P_scale`、`TemperatureBoundary`、弱对流稳定锚等。

这种混用会造成两个直接问题：

- 配置表面看似请求 canonical profile，但运行时仍可能落入旧简化热路；
- 下游 summary / report / field manifest 难以稳定表达“这次 run 到底用了什么物理合同”。

在 `mass` 主线收敛到 shell/aperture/catalog-first 后，COMSOL 也必须同步收敛到 canonical-only 可审计合同。

## Problem Statement

需要明确：

- 哪些 profile 是 active mainline；
- 哪些历史简化热路不再允许作为主链兜底；
- 轨道热模块缺失时如何做显式、可审计的 canonical 内部降级；
- 如何让 source claim、profile manifest、field/export contract 在活跃代码中保持一致。

## Decision

### 1. Active COMSOL profile 固定为 3 个 canonical profile

主链只保留以下 profile：

1. `thermal_static_canonical`
2. `thermal_orbital_canonical`
3. `electro_thermo_structural_canonical`

不再把 `diagnostic_simplified` 作为 active profile、默认 profile 或可执行主链合同的一部分。

### 2. Canonical profile 必须优先使用官方 COMSOL 接口

canonical path 的接口优先级固定为：

- 热场：`Heat Transfer in Solids`
- 辐射：`Heat Transfer with Surface-to-Surface Radiation`
- 轨道热载荷：`Orbital Thermal Loads`
- 电学：`Electric Currents`
- 结构：`Solid Mechanics`
- 耦合：官方 multiphysics 接口

### 3. 历史简化热路从主线代码面退出

以下旧路径不再作为主链可执行兜底：

- `P_scale` 功率 continuation
- `TemperatureBoundary` 边界温度锚
- 仅为数值稳定存在的弱对流边界锚
- 任何以“diagnostic profile”名义暴露给 active runtime 的旧热路开关

### 4. Canonical request 缺少 shell outer boundary 时必须 fail-fast

若 canonical 辐射边界缺少机壳外表面选择：

- 直接阻断本次 canonical thermal build；
- 不再回退到全边界温度锚或弱对流稳定锚；
- 审计信息必须保留缺失原因。

### 5. 轨道热模块缺失只允许 canonical 内部显式降级

当 `thermal_orbital_canonical` 缺少 `Orbital Thermal Loads` 能力时：

- 允许显式回落到 `thermal_static_canonical`
- 不允许静默冒充 `thermal_orbital_canonical`
- 不允许再借由旧 `diagnostic_simplified` profile 逃逸

### 6. 字段、单位和导出合同保持统一

字段与单位合同继续固定：

- 温度：`T` / `K`
- 位移模：`solid.disp` / `m`
- 位移分量：`u,v,w` / `m`
- von Mises：`solid.mises` / `Pa`
- 模态频率：`Hz`

同一套 field registry / unit contract 必须服务 summary、field export、tensor 与下游消费者。

### 7. Source claim 必须区分 requested 与 effective

source claim 至少要稳定给出：

- `requested_physics_profile`
- `physics_profile`
- `canonical_request_preserved`
- `requested_profile_release_grade`
- `effective_profile_release_grade`
- `thermal_realness_level`
- `structural_realness_level`
- `power_realness_level`
- `thermal_setup_ok / thermal_study_entered / thermal_study_solved`
- `structural_setup_ok / structural_study_entered / structural_study_solved`
- `power_setup_ok / power_study_entered / power_study_solved`
- `coupled_setup_ok / coupled_study_entered / coupled_study_solved`
- `degradation_reason`

## Implemented / Deferred

### Implemented（截至 2026-03-13）

- active profile manifest 已收敛为 3 个 canonical profile；
- 默认 COMSOL profile 已切到 `electro_thermo_structural_canonical`；
- `thermal_orbital_canonical` 缺模块时显式回落到 `thermal_static_canonical`；
- 主链已移除 `diagnostic_simplified` profile 与旧热路兜底代码；
- canonical thermal path 缺少 shell outer boundary 时会 strict block。
- shell 外表面与 feature/domain audit 已优先消费 shell contract truth，而不再只依赖 legacy metadata；
- solver scheduler / result extractor 已显式记录 model-build 与 solve 的阶段差异；
- source claim 已能区分 canonical request preserved 与实际 study entered / solved 状态；
- shell mount `Thermal Contact` 已按官方 COMSOL boundary/pair 语义重新收敛：
  - Union 几何下只绑定真实共享内边界；
  - assembly pair 路径当前不在主线内静默兜底；
  - `summary / report / result_index / comsol_raw_data` 已稳定沉淀 `shell_contact_audit`；
- `payload_camera` 的目录件几何合同已补齐真实安装接触特征：
  - 保持 `140 x 120 x 170 mm` 有效包络不变；
  - 在不遮挡 `camera_window` 的前提下新增 `4` 个安装柱和 `4` 个 shell contact pad；
- 受控 real COMSOL smoke 在当前代码上已能稳定证明：
  - runtime probe 成功
  - STEP import 成功
  - canonical model build 成功
  - shell outer boundary selection 非空
  - 能诚实区分“contact feature 创建成功”与“真实共享界面存在”
  - `optical_remote_sensing_bus` 最新主线 run（`experiments/20260313/231429_mass_optical_remote_sensing_bus`）已出现：
    - `payload_camera.selection_status = shared_interface_applied`
    - `shell_contact_audit.applied_count = 5`
    - `real_feasible = true`
    - `final_metrics.max_temp = 50.10 degC`
  - 该证据说明 geometry-first contact fix 可以在不放松 canonical contract 的前提下恢复热可行性。

### Deferred

- 不在本 ADR 中声称已完成轨道热高保真全链实现；
- 不在本 ADR 中声称真实 COMSOL canonical 已完成多次独立复跑后的 release-grade 验证；
- 不在本 ADR 中一次性补齐 mission/EMC 等更高保真物理域。

## Consequences

### Positive

- active runtime、配置和 source claim 的口径终于一致；
- 下游不再把旧简化热路误读为 canonical 主链；
- 下游不再把“box selection 命中附近边界”误读为“存在真实传热界面”；
- geometry-first 的真实安装接触修复已在 active bus 主线证明有效；
- profile manifest 更接近真实工程可审计合同。

### Negative

- 某些历史案例会因为失去旧兜底热路而更容易 fail-fast；
- 某些挂载语义会因缺少真实共享界面而暴露为 `required_shell_contacts_effective=false`；
- 真实 COMSOL 垂直 smoke 需要重新积累 canonical-only 证据；
- 历史测试和 sidecar 文档需要继续清仓。

### Tradeoff

- 该决策优先保证 source claim 的真实性，而不是保留“更容易跑通”的旧调试热路。
