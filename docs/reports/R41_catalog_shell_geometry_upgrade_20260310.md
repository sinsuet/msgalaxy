# R41 目录件、机壳开窗与 STEP 几何内核升级规划（2026-03-10）

## 1. 目的

本报告定义 MsGalaxy 下一阶段的几何升级边界。目标不是继续在盒体上追加例外，而是建立：

- 目录件几何真值；
- 机壳/面板/aperture 正式合同；
- 精确几何与快速代理双表示；
- 可被 STEP/COMSOL/规则/可视化共同消费的统一几何内核。

对应 ADR：

- `docs/adr/0011-catalog-shell-aperture-geometry-kernel.md`

## 2. 当前状态审计

截至 2026-03-10，仓库已有几何能力：

- `geometry/cad_export_occ.py`
  - 支持 `box`
  - 支持 `cylinder`
  - 支持厚壁 shell
  - 支持 heatsink
  - 支持 bracket
- `simulation/comsol/model_builder.py`
  - 已能识别 shell 外表面；
  - 已能在机壳存在时绑定外边界选择。

但仍有关键缺口：

1. 没有正式 `CatalogComponentSpec`；
2. aperture/cutout 仍未成为正式建模对象；
3. 椭球、圆台、挤出剖面、复合体尚未成为正式形状族；
4. 代理几何与精确几何没有统一合同；
5. 目录件尺寸仍可能被布局流程当成默认变量。

## 3. 新几何内核的目标对象

下一阶段需要引入以下对象：

- `CatalogComponentSpec`
- `ShellSpec`
- `PanelSpec`
- `ApertureSiteSpec`
- `GeometryProfileSpec`
- `GeometryProxySpec`
- `MountInterfaceSpec`

### 3.1 `CatalogComponentSpec`

至少包含：

- 型号与器件族；
- 主几何 profile；
- proxy geometry；
- 质量/功耗/材料；
- 可安装面；
- 合法朝向；
- 默认附体；
- FOV/aperture 接口；
- 热/结构/电接口。

### 3.2 `ShellSpec`

至少包含：

- 外形类型；
- 总尺寸与厚度；
- 面板列表；
- 表面语义；
- 结构载荷路径；
- 热边界语义；
- aperture site 列表；
- shell variant。

### 3.3 `ApertureSiteSpec`

至少包含：

- 所属面板/壳体面；
- 形状类型；
- 中心、尺寸与朝向；
- 允许的 payload/antenna 类型；
- 是否必须贯通；
- 热/结构/FOV 影响标签。

## 4. 形状族规划

v1 正式形状族固定为：

| 形状族 | 典型用途 |
| --- | --- |
| `box` | 电子箱、标准设备仓 |
| `cylinder` | 反作用轮、圆柱电池、罐体 |
| `frustum` | 镜筒、锥台罩、接口过渡件 |
| `ellipsoid` | 雷达罩、球罩式附体 |
| `extruded_profile` | 薄板天线、截面拉伸件、异形板件 |
| `composite_primitive` | 相机箱体+镜筒、雷达箱体+罩、复合载荷 |

建议典型映射：

- 电池：`box` 或 `cylinder_cluster`
- 光学载荷：`composite_primitive(box + frustum/cylinder)`
- 雷达载荷：`composite_primitive(box + ellipsoid/extruded panel)`
- 天线：`extruded_profile`
- 太阳翼：独立 panel/appendage，不作为普通组件本体。

## 5. aperture 的正式处理结论

该问题已在 ADR 中冻结：**aperture 在 STEP 几何阶段完成真实拓扑**。

理由如下：

1. aperture 会改变真实拓扑；
2. aperture 会改变机壳热路径与结构路径；
3. aperture 会影响 FOV、载荷挂载与网格划分；
4. 若在 COMSOL 阶段临时挖孔，几何、规则和物理会失去统一事实源。

因此，算子只允许：

- 激活预定义 aperture site；
- 在候选 panel variant 之间切换；
- 将 payload 对准某个合法 aperture。

不允许：

- 任意切新孔；
- 在 COMSOL 里直接把 aperture 当做后处理布尔对象。

## 6. 双表示几何策略

为了同时服务搜索期效率和高保真求解，所有对象都需要两套表示：

### 6.1 精确表示

用途：

- STEP 导出；
- COMSOL 导入；
- 高精度渲染；
- 最终几何审计。

实现方向：

- OCC BREP / primitive / Boolean；
- 组合体与面板/aperture 真正成形。

### 6.2 代理表示

用途：

- 快速碰撞检查；
- 间隙计算；
- 初始布局搜索；
- 搜索期近似 CG 和占空比评估。

实现方向：

- AABB -> OBB -> primitive distance 的渐进式升级；
- shell 和 aperture 提供轻量化 zone proxy。

v1 要求：

- 精确表示和代理表示至少在包络、主朝向、可安装面和近似重心上一致。

## 7. STEP 几何流水线

推荐统一流水线如下：

1. 读取 `SatelliteArchetype` 和 `ShellSpec`
2. 生成 shell 主体
3. 应用 `ShellVariant` / `PanelSpec`
4. 对 aperture site 做 Boolean cut
5. 生成目录件精确形体
6. 加入 bracket/heatsink/radome 等附体
7. 汇总导出 STEP
8. 同步生成 proxy geometry 与 geometry manifest

`geometry manifest` 建议至少记录：

- 零件标识；
- 几何类型；
- 主尺寸；
- 是否为 shell / aperture / appendage；
- 近似体积；
- 代理几何类型；
- 主要功能朝向。

## 8. 并行实施边界

该主题建议拆为三个并行包：

### WP-A：目录件几何合同

- 定义 `CatalogComponentSpec`；
- 建立最小目录件几何库；
- 约束尺寸不可自由化。

### WP-B：shell/panel/aperture 内核

- 定义 `ShellSpec/PanelSpec/ApertureSiteSpec`；
- 建立 STEP 阶段 Boolean aperture 管线；
- 建立 shell variant 管理。

### WP-C：双表示与碰撞检查升级

- 定义 `GeometryProxySpec`；
- 逐步从纯 AABB 升级；
- 接入布局与规则检查。

其中 `WP-B` 是 COMSOL canonical physics 和 teacher review 的前置依赖。

## 9. 验收标准

- `box/cylinder/frustum/ellipsoid/extruded_profile/composite_primitive` 都能生成 STEP；
- 带机壳和 aperture 的案例可被 COMSOL 导入；
- shell、payload、aperture 的关系在可视化中可见；
- teacher/demo 主链中不再出现“只有长方体堆叠”的默认卫星模型；
- 目录件尺寸不再作为默认自由变量。

## 10. 风险

- OCC Boolean 与复合体复杂度会推高网格失败率；
- 代理几何若过粗，会影响规则和碰撞判断；
- 目录件若没有最小库支撑，几何内核虽正确但无法落地。

建议先实现“少量高价值器件 + 少量高价值 shell variant”，再扩充覆盖面。
