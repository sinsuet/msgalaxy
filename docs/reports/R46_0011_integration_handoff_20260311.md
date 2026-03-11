# R46 0011 Integration Handoff 20260311

## 任务范围

本次交付只覆盖 `docs/adr/0011-catalog-shell-aperture-geometry-kernel.md` 的最小可执行切片，不包含其他 ADR 的实现。

范围内内容：

- 目录件最小几何合同：
  - `CatalogComponentSpec`
  - `GeometryProfileSpec`
  - `GeometryProxySpec`
- shell 最小合同：
  - `ShellSpec`
  - `PanelSpec`
  - `ApertureSiteSpec`
- STEP 导出最小扩展：
  - `frustum`
  - `ellipsoid`
  - `extruded_profile`
  - `composite_primitive`
- shell/panel/aperture 统一输入合同
- aperture 在 STEP 生成阶段的真实拓扑切除
- 面向布局/keepout/seed/metrics 的最小 proxy 接线

明确不在本次范围内的内容：

- ADR 0010 `satellite archetype + reference baseline`
- ADR 0012 `COMSOL canonical satellite physics contract`
- archetype selector
- COMSOL canonical physics
- DSL v4
- `optimization/` 和 `workflow/` 路径下实现
- COMSOL 驱动文件变更

## 实际改动

### 1. 新几何合同的实际落点

`geometry/catalog_geometry.py`

- `GeometryProfileSpec`
  - 作为精确几何合同，供 STEP 真实几何构造使用
  - 负责 `box / cylinder / frustum / ellipsoid / extruded_profile / composite_primitive` 的最小参数表达
- `CatalogComponentSpec`
  - 作为目录件真值合同
  - 保存目录件族别、几何真值、proxy、质量、功耗等最小信息
- 对旧 `ComponentGeometry` 的兼容适配
  - `GeometryProfileSpec.from_component_geometry(...)`
  - `CatalogComponentSpec.from_component_geometry(...)`
  - `resolve_catalog_component_spec(...)`

`geometry/shell_spec.py`

- `ShellSpec`
  - 作为机壳真值合同
  - 统一外形、厚度、面板、开孔位点
- `PanelSpec`
  - 作为面板合同
  - 统一 panel face、span、active variant
- `ApertureSiteSpec`
  - 作为 aperture 位点合同
  - 统一 aperture shape、center、size、profile points、depth、proxy depth
- 统一规划函数：
  - `plan_box_panel_variant(...)`
  - `plan_box_panel_aperture(...)`
  - `aperture_proxy_plans(...)`

`geometry/geometry_proxy.py`

- `GeometryProxySpec`
  - 作为布局/碰撞/边界/keepout 使用的轻量 proxy 合同
- `build_geometry_proxy_manifest(...)`
  - 统一输出 `shell_proxy / shell_interior_proxy / panel_variant_proxy / aperture_proxy / component_proxy`
- `shell_interior_proxy_entries_from_shell_spec(...)`
  - 为 `cylinder / frustum` 生成最小曲面腔体近似 keepout

### 2. STEP 导出中的实际落点

`geometry/cad_export_occ.py`

- shell 统一入口：
  - `_create_enclosure_shell_shape(...)`
- panel variant 布尔融合：
  - `_apply_panel_variants(...)`
- aperture 真拓扑切除：
  - `_apply_aperture_cutouts(...)`
- aperture 切除规划：
  - `_plan_box_panel_aperture_cutout(...)`
- aperture 切除实体生成：
  - `_build_aperture_cutout_shape(...)`
- 目录件形状族构造：
  - `_build_shape_from_profile(...)`

其中 aperture 的真实实现位置在 STEP 阶段，而不是布局阶段：

- `geometry/shell_spec.py` 负责出规划合同
- `geometry/cad_export_occ.py` 负责 OCC Boolean cut

### 3. 旧 `ComponentGeometry` 的兼容关系

旧 `ComponentGeometry` 没有被移除，但它现在是兼容输入，不再承担完整几何真值角色。

关系如下：

- 如果 `DesignState.metadata` 或 layout config 中存在 catalog/shell 合同：
  - 优先走 `CatalogComponentSpec` / `ShellSpec`
- 如果没有新合同：
  - 仍可由旧 `ComponentGeometry` 自动退化适配

兼容层入口：

- `geometry/catalog_geometry.py`
  - `GeometryProfileSpec.from_component_geometry(...)`
  - `CatalogComponentSpec.from_component_geometry(...)`
  - `resolve_catalog_component_spec(...)`
- `geometry/shell_spec.py`
  - `shell_spec_from_legacy_design_state(...)`
  - `resolve_shell_spec(...)`

兼容策略是“适配层”，不是重写旧协议。

### 4. 下游最小接线

`geometry/layout_engine.py`

- 支持从 `catalog_component_file` 或内联 catalog 合同推导组件 proxy 尺寸、质量、功耗、类别

`geometry/layout_seed_service.py`

- 在 seed state 中保留：
  - `catalog_components`
  - `shell_spec`
  - `shell_spec_file`
  - `shell_spec_path`

`geometry/metrics.py`

- 清空间距、边界、体积统计优先读取 catalog proxy 尺寸

`geometry/keepout.py`

- `create_keepout_aabbs(...)` 现在会合并：
  - 显式 `keep_out`
  - `shell_interior_proxy`
  - `aperture_proxy`

### 5. 配置与样例数据

`config/catalog_components/`

目录件样例：

- `payload_camera_composite.json`
- `panel_antenna_extruded.json`
- `radome_ellipsoid.json`

shell/panel/aperture 样例：

- `shell_box_panel_aperture_min.json`
- `shell_box_panel_circular_aperture_min.json`
- `shell_box_panel_profile_aperture_min.json`
- `shell_box_panel_frustum_variant_min.json`
- `shell_cylinder_endcap_circular_aperture_min.json`
- `shell_cylinder_endcap_frustum_variant_profile_aperture_min.json`
- `shell_cylinder_side_profile_aperture_min.json`
- `shell_frustum_endcap_frustum_variant_profile_aperture_min.json`
- `shell_frustum_side_profile_aperture_min.json`
- `shell_frustum_side_frustum_variant_profile_aperture_min.json`

## 共享接口/配置/数据合同

### 1. geometry profile 合同

文件：

- `geometry/catalog_geometry.py`

关键模型：

- `GeometryProfileSpec`

用途：

- STEP 几何真值
- panel variant profile
- aperture profile cutout

最小支持形状：

- `box`
- `cylinder`
- `frustum`
- `ellipsoid`
- `extruded_profile`
- `composite_primitive`

### 2. geometry proxy 合同

文件：

- `geometry/geometry_proxy.py`

关键模型：

- `GeometryProxySpec`

用途：

- 布局 AABB 近似
- clearance / collision / boundary
- keepout proxy manifest

最小支持 proxy 条目：

- `component_proxy`
- `shell_proxy`
- `shell_interior_proxy`
- `panel_variant_proxy`
- `aperture_proxy`

### 3. shell / panel / aperture 合同

文件：

- `geometry/shell_spec.py`

关键模型：

- `ShellSpec`
- `PanelSpec`
- `ApertureSiteSpec`

用途：

- shell 真值
- panel 归属与变体激活
- aperture 位点、cutout 规划、proxy 规划

### 4. STEP 阶段 aperture 处理边界

布局阶段：

- 只消费 `aperture_proxy`
- 不构造真实拓扑

STEP 导出阶段：

- 使用 `ApertureSiteSpec`
- 通过 OCC Boolean cut 生成真实 aperture 拓扑

### 5. 已最小实现 vs 占位

已最小实现：

- shell 真空腔：
  - `box`
  - `cylinder`
  - `frustum`
- aperture cutout：
  - `rectangular_cutout`
  - `circular_cutout`
  - `profile_cutout`
- panel variant：
  - `box_pad`
  - `frustum_pad`
- 组件形状：
  - `box`
  - `cylinder`
  - `frustum`
  - `ellipsoid`
  - `extruded_profile`
  - `composite_primitive`

仍为占位或近似：

- `cylinder / frustum` 内腔 keepout 仍是 AABB 角区近似，不是精确曲面约束
- 非 `box / cylinder / frustum` shell 还没有真实 shell cavity 布尔实现
- 其他 aperture 形状族未实现
- 其他 panel variant kind 未实现
- `ellipsoid` 依赖 OCC 各向异性缩放；失败时会退化为 box 近似

## 最小验证与结果

本次没有跑全量测试，只执行范围内最小验证。

执行命令：

```powershell
conda run -n msgalaxy python -m pytest tests/test_catalog_shell_geometry.py tests/test_catalog_shell_step_smoke.py tests/test_geometry_services.py -q
```

结果：

- `59 passed, 228 warnings`

覆盖内容：

- 新合同与旧 `ComponentGeometry` 的兼容适配
- catalog component 样例几何合同
- box/cylinder/frustum 的 shell/panel/aperture 规划
- profile/circular aperture 的 proxy 规划
- geometry proxy manifest 输出
- keepout AABB 合并逻辑
- OCC/STEP 最小 smoke

说明：

- warnings 主要来自 `pythonocc` 的 SWIG deprecation，不影响通过结果

## 未完成项/风险

### 1. 未完成项

- 未进入 ADR 0010 的 archetype/reference baseline 集成
- 未进入 ADR 0012 的 COMSOL canonical physics 集成
- 未扩展更多 aperture 形状
- 未扩展更多 shell 真拓扑族
- 未把 curved-shell keepout 提升为更精确的几何代理

### 2. 集成风险

- 现有 `shell_interior_proxy` 是保守近似，可能影响布局可行域大小
- 老路径如果只依赖 `ComponentGeometry.dimensions`，与新 catalog proxy 尺寸可能出现度量差异
- `profile_cutout` 的几何质量依赖 profile 点序和 OCC 挤出稳定性
- `ellipsoid` 依赖 OCC 缩放能力，跨环境稳定性需要单独关注
- 新增 shell/panel/aperture 数据合同后，未来如果上层配置生成器输出不完整，需要显式校验而不是静默回退

## 对集成测试的建议

建议后续集成测试只补以下薄层，不需要立刻扩 scope：

1. 增加一组 `layout_seed -> STEP export -> geometry manifest/proxy manifest` 的端到端回归，覆盖 box/cylinder/frustum 三类 shell。
2. 增加一组 `catalog_component_file + shell_spec_file` 的配置装配测试，确保上层配置生成器不会漏传 metadata。
3. 对 `cylinder / frustum` 增加固定基准用例，验证 `shell_interior_proxy` 的数量、slice 分布和 keepout 体积变化。
4. 在具备稳定 OCC 环境的机器上，补一组 profile aperture 的导出稳定性 smoke，重点观察布尔切除失败率。
5. 若后续进入多模块集成，优先验证新合同与旧 `ComponentGeometry` 共存时的行为，而不是先做更大范围重构。
