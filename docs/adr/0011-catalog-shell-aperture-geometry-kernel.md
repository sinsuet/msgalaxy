# 0011-catalog-shell-aperture-geometry-kernel

- status: accepted
- date: 2026-03-10
- deciders: msgalaxy-core
- cross-reference:
  - `R41_catalog_shell_geometry_upgrade_20260310`
  - `R40_satellite_reference_baseline_20260310`
  - `R42_comsol_canonical_satellite_physics_20260310`

## Context

当前几何主线存在以下真实状态：

- `ComponentGeometry` 默认仍是通用盒体合同；
- `geometry/cad_export_occ.py` 已支持 `box`、`cylinder`、厚壁 shell、heatsink、bracket；
- 机壳与开窗/开孔尚未形成正式几何协议；
- 组件几何“身份”和“布局变量”仍然耦合，导致尺寸容易被当作可优化变量；
- 非长方体载荷缺少稳定的目录件建模合同。

这意味着，即使后续增加更多规则和三场可视化，只要几何内核仍是“盒体优先、尺寸自由、开窗临时处理”，系统就不能稳定迈向真实卫星建模。

## Problem Statement

需要明确以下关键问题：

- 目录件的几何真值应该存放在哪里；
- 机壳、面板、开窗/开孔是否属于正式合同；
- 开窗是在 STEP 几何生成阶段完成，还是在 COMSOL/算子阶段临时挖孔；
- 非长方体器件怎样在“精确几何”与“快速碰撞代理”之间切换；
- 如何确保布局优化主要改位置、朝向、挂载与局部附体，而不是继续自由改尺寸。

## Decision

### 1. 组件真值与布局状态分离

目录件与布局状态正式拆分为两层：

- `CatalogComponentSpec`：组件身份、几何真值、材料、接口、允许朝向、默认附体
- `LayoutInstanceState`：位置、朝向、挂载面、支架/导热带/局部开孔位选择等实例状态

目录件尺寸、几何轮廓和接口是规格事实，不是默认布局变量。

### 2. 机壳升级为正式合同

新增 `ShellSpec`，至少包含：

- 外轮廓类型与尺寸；
- 壁厚；
- 面板划分；
- 材料与热/结构属性；
- 安装基面；
- 外表面语义；
- 开窗/开孔位集合；
- 面板可替换变体。

机壳不再只是可选透明外框，而是几何、热、结构、规则、可视化的共同事实源。

### 3. 开窗/开孔必须在 STEP 几何阶段完成

开窗/开孔的正式结论固定为：

- 在 STEP/BREP 几何生成阶段完成真实拓扑；
- 不允许把“后期在 COMSOL 里临时挖孔”作为主方案；
- `vop_maas` 算子只允许激活预定义 `aperture site / panel variant`，不允许任意 Boolean 建模。

原因：

- aperture 会影响真实拓扑、网格、热边界、结构载荷路径和 FOV；
- 若在后期临时挖孔，几何、规则、物理和审计将失去统一事实源。

### 4. 建立双表示几何内核

所有目录件与机壳必须同时具备两套表示：

- `GeometryProfileSpec`：精确几何表示，用于 STEP/COMSOL/高精度渲染；
- `GeometryProxySpec`：快速代理表示，用于碰撞、间隙、包络、初期搜索。

精确表示与代理表示必须共享以下事实：

- 尺寸边界；
- 质量与惯性近似中心；
- 可安装面；
- 主要功能朝向。

### 5. v1 形状族最小集合固定

为控制复杂度，v1 只正式支持以下形状族：

- `box`
- `cylinder`
- `frustum`
- `ellipsoid`
- `extruded_profile`
- `composite_primitive`

其中 `composite_primitive` 用于表达：

- 相机本体 + 镜筒；
- 雷达电子箱 + 天线罩；
- 电池壳体 + 端部接口；
- 平台箱体 + 外挂板件。

### 6. 目录件默认禁止自由改尺寸

对 teacher/release 主链，目录件尺寸、主形体和 aperture 位置属于固定规格。

允许变化的默认布局变量只包括：

- 位置；
- 朝向；
- 安装面；
- 挂载位；
- panel/aperture site 选择；
- 支架与热连接等局部附体；
- 有界的小尺度安装公差。

`placeholder` 组件可以保留尺寸参数化，但必须被显式标为非真实目录件。

### 7. STEP 导出顺序固定

正式 STEP 几何流水线固定为：

1. 构建 shell 主体；
2. 应用 panel variant；
3. 应用 aperture/cutout；
4. 放置目录件精确几何；
5. 叠加 bracket/heatsink/radome 等附体；
6. 导出 STEP；
7. 派生 proxy geometry 和审计元数据。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-10 的真实实现）

- 已有 `box/cylinder/shell/heatsink/bracket` 薄切片；
- STEP 生成已基于 OpenCASCADE；
- shell 可通过 metadata 导出厚壁空腔。

### Accepted Target（本 ADR 接受的目标架构）

- 目录件、机壳、面板、开窗/开孔成为正式合同；
- 开窗在 STEP 阶段完成真实拓扑；
- 几何双表示内核进入主链；
- 目录件默认不允许自由改尺寸。

### Deferred（明确延后）

- 不在 v1 一次性支持任意 NURBS/复杂 CAD 曲面；
- 不在本 ADR 中要求所有目录件都具备厂商级精确细节；
- 不在本 ADR 中完成所有 executor 的即时接入，仅冻结几何合同。

## Consequences

### Positive

- 几何、规则、COMSOL 和可视化将使用同一机壳/aperture 事实源；
- 相机、雷达、电池等器件能摆脱“全是盒体”的表达限制；
- 布局优化的自由度更接近真实工程问题。

### Negative

- OCC 几何、Boolean 和网格复杂度上升；
- 代理几何与精确几何的一致性需要额外测试；
- 历史仅支持盒体的工具链需要逐步迁移。

### Neutral / Tradeoff

- 该决策牺牲了一部分实现简单性，以换取几何真实性与后续物理可信度；
- aperture 预定义位策略牺牲了任意几何自由，但显著提升了可审计性与工程稳定性。

## Follow-up

后续实施至少应覆盖：

1. 定义 `CatalogComponentSpec` / `ShellSpec` / `PanelSpec` / `ApertureSiteSpec`；
2. 为 `geometry/cad_export_occ.py` 建立形状族扩展与 Boolean aperture 管线；
3. 引入 `GeometryProxySpec` 并接入碰撞/间隙检查；
4. 建立 teacher/release 对 `placeholder` 的 gating 策略；
5. 把目录件几何与布局变量的边界写入运行时 profile。
