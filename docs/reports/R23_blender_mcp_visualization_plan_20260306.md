# R23 Blender MCP 真实感可视化接入方案（2026-03-06）

## 1. 目标与边界

### 1.0 当前实施状态（2026-03-06）
- **已实现 P0 薄切片**：
  - `visualization/blender_mcp/contracts.py`
  - `visualization/blender_mcp/bundle_builder.py`
  - `visualization/blender_mcp/codegen.py`
  - `visualization/blender_mcp/brief_builder.py`
  - `run/render_blender_scene.py`
- **已验证样例**：
  - `experiments/run_bm_l1_operator_program_nsga3_s42_0306_nsgaiii_003/visualizations/blender/render_bundle.json`
  - `experiments/run_bm_l1_operator_program_nsga3_s42_0306_nsgaiii_003/visualizations/blender/final_satellite_render.png`
  - `experiments/run_bm_l1_operator_program_nsga3_s42_0306_nsgaiii_003/visualizations/blender/final_satellite_scene.blend`
- **尚未实现**：仓内自动 MCP client、主流程 post-run hook、资产库替换、STEP mesh bridge。

### 1.1 目标
- 把 MsGalaxy 最终产出的 `final_selected` 布局结果接入 Blender MCP，生成更接近真实卫星外形的静帧、环绕动画与可交付场景文件。
- 保留当前优化/物理主链真实性：`LLM -> pymoo -> physics -> diagnosis` 不因渲染侧链而改变语义。
- 让可视化从“分析图”升级为“工程展示资产”，服务于论文插图、汇报展示、方案比对与人工审阅。

### 1.2 非目标
- 不把 Blender 放进求解闭环，不参与约束判定，不替代 COMSOL/CAD 主链。
- 不宣称仓库已经完成 Blender MCP 接入；本文档仅定义落地路线与 DoD。
- 不要求一次性做到全高保真 CAD 复刻；优先做“结构正确 + 风格可信 + 工具可用”的渐进式方案。

## 2. 当前仓库基础与差距

### 2.1 已有基础
- `workflow/modes/mass/pipeline_service.py` 已在 `final_selected` 阶段写出最终快照，并保留 `final_mph_path` 元数据。
- `core/logger.py` 已把布局状态写入 `snapshots/*.json`，其中包含完整 `design_state` 与差分信息。
- `core/protocol.py` 已定义 `DesignState / ComponentGeometry / Envelope / KeepoutZone`，且几何单位是 `mm`。
- `core/visualization.py` 已能生成 `final_layout_3d.png`、`layout_evolution.png`、`thermal_heatmap.png`、timeline GIF 等分析型图像。
- `geometry/cad_export_occ.py` 已可用 OpenCASCADE 导出真实 STEP，支持 box/cylinder、heatsink、bracket 等当前动态几何。

### 2.2 现状差距
- 当前 `visualizations/` 主要是分析图，不是面向展示的真实感渲染。
- 当前几何表达以包络盒、圆柱与附加结构为主，缺少真实材质、舱板、太阳翼、天线、贴图、相机语言与灯光系统。
- Blender 官方导入格式列表中未见 STEP，故应推断“STEP 不能作为 Blender 原生主入口”；如果要利用 STEP，必须加桥接转换层。
- 现有产物已经足够驱动 Blender 场景重建，但缺少标准化“渲染包”与独立渲染执行器。

## 3. 总体设计结论

### 3.1 推荐结论
采用 **“优化主链不动 + Blender MCP 渲染侧链”** 的架构：

```text
MsGalaxy final_selected
  -> snapshot/design_state
  -> render bundle builder
  -> Blender MCP client
  -> scene template + asset mapper
  -> still / turntable / exploded render
  -> visualizations/blender/*
```

### 3.2 关键原则
1. **单向旁路**：渲染侧链只消费结果，不反向改写优化结果。  
2. **数据先标准化**：先定义 `render_bundle.json`，再对接 MCP。  
3. **分层保真**：先 primitives 重建，再做 asset 替换，最后再考虑 STEP 桥接。  
4. **失败不阻断主链**：Blender/MCP 失败只能记日志与标记 `render_status=failed`，不能让优化 run 判失败。  
5. **产物可审计**：渲染输入、输出、配置和版本都落盘，保证可复现。  

## 4. 推荐技术路径

### 4.1 主路径：`DesignState -> Blender Scene`
这是最推荐的第一阶段实现路径。

优点：
- 不依赖 STEP 导入支持；
- 直接使用仓内已稳定的数据协议；
- 容易做单元测试、mock MCP 与差分审计；
- 可直接保留组件 ID、类别、热/结构标记，方便叠加工程语义。

实现方式：
- 把 `DesignState` 转换为 `render_bundle.json`；
- Blender MCP 读取 bundle 后直接调用 `bpy`/场景工具：
  - 创建舱体；
  - 按 `position/dimensions/rotation` 摆放组件；
  - 根据 `category`、`coating_type`、`power`、`heatsink`、`bracket` 赋予材质和附加件；
  - 生成灯光、地台、背景、相机；
  - 输出静帧和 turntable。

### 4.2 增强路径：`DesignState -> Asset Mapping -> Blender`
当 primitives 版本稳定后，引入组件资产映射。

做法：
- 为 `category` 与 BOM 标签建立 `render_role`：
  - `payload`
  - `avionics_box`
  - `battery_pack`
  - `reaction_wheel`
  - `antenna`
  - `radiator`
  - `solar_panel`
  - `structure_panel`
- 在 `assets/blender/components/` 存放可复用 `glb/blend` 资产；
- 若命中资产，则替换包络盒；若未命中，回退为参数化 box/cylinder。

### 4.3 可选路径：`STEP -> Bridge -> glTF -> Blender`
这是第三阶段可选增强，不建议作为首版主链。

原因：
- STEP 更适合 COMSOL/CAD，不适合直接进 Blender；
- 需要额外桥接器把 BREP 三角化为 `glb/gltf/obj`；
- 三角化与法线、材质、层级映射都会引入额外不确定性。

建议用途：
- 用于舱体外壳、真实面板、已有高质量 CAD 的关键部件；
- 不作为全量组件唯一来源；
- 优先与 `DesignState` 路径并存，而不是替代。

## 5. 新增数据契约

### 5.1 产物：`render_bundle.json`
建议位置：`experiments/run_<ts>/visualizations/blender/render_bundle.json`

建议字段：

```json
{
  "schema_version": "blender_render_bundle/v1",
  "run_id": "run_20260306_xxx",
  "source": {
    "snapshot_path": "snapshots/seq_....json",
    "summary_path": "summary.json",
    "final_mph_path": "....mph",
    "step_path": "....step"
  },
  "units": "mm",
  "coordinate_system": {
    "source": "msgalaxy_rhs_mm",
    "target": "blender_rhs_m"
  },
  "envelope": {
    "outer_size_mm": [100.0, 100.0, 300.0],
    "origin": "center"
  },
  "components": [
    {
      "id": "payload_cam_01",
      "category": "payload",
      "render_role": "payload",
      "position_mm": [0.0, 20.0, 30.0],
      "dimensions_mm": [40.0, 30.0, 50.0],
      "rotation_deg": [0.0, 0.0, 90.0],
      "envelope_type": "box",
      "material_hint": "black_anodized_al",
      "coating_type": "default",
      "power_w": 12.0,
      "mass_kg": 1.2,
      "attachments": {
        "heatsink": null,
        "bracket": null
      }
    }
  ],
  "keepouts": [],
  "metrics": {
    "best_cv_min": 0.0,
    "cg_offset_mm": 2.3,
    "max_temperature_k": 318.4
  },
  "render_profile": {
    "template": "satellite_cleanroom_v1",
    "shots": ["iso", "top", "front", "exploded"],
    "animation": ["turntable"]
  }
}
```

### 5.2 为什么要先做 bundle
- 便于测试：无需 Blender 就可校验 bundle 内容。
- 便于演进：后续可切换 MCP server 或渲染引擎，而不动优化主链。
- 便于审计：论文图、汇报图、宣传图都能回溯到同一 bundle。

## 6. 建议代码结构

建议新增如下模块（规划态）：

```text
visualization/
  blender_mcp/
    __init__.py
    contracts.py
    bundle_builder.py
    asset_mapper.py
    client.py
    scene_builder.py
    render_service.py
    manifest.py
run/
  render_blender_scene.py
assets/
  blender/
    templates/
      satellite_cleanroom_v1.blend
    components/
      payload_camera.glb
      battery_pack.glb
      antenna_patch.glb
```

### 6.1 接入点选择
- **首选**：在 `workflow/modes/mass/pipeline_service.py` 完成 `final_selected` 快照后，增加一个“可选 post-run 渲染钩子”。
- **并行**：提供手动 CLI `run/render_blender_scene.py --run-dir ...`，先从已有 run 目录回放构建，避免影响主流程。
- **API 层后续可选**：等 CLI 稳定后，再考虑把结果暴露到 `api/server.py` 的 visualizations 路由。

## 7. Blender MCP 适配要求

### 7.1 MCP 侧必须具备的最小能力
无论最终选用哪套 Blender MCP server，至少需要以下工具能力：
- 健康检查：`ping / health`
- 场景生命周期：`reset_scene / open_template / save_scene`
- 对象创建：`create_box / create_cylinder / duplicate_asset / import_glb`
- 变换：`set_transform / set_scale / set_rotation`
- 材质：`assign_material / set_texture / set_label`
- 灯光与相机：`add_light / set_camera / set_background`
- 渲染：`render_still / render_animation`
- 文件输出：返回输出文件路径与状态

### 7.2 适配策略
- 不把上层逻辑绑死到某个 MCP server 的私有工具名；
- 在 `client.py` 里做一次“仓内标准接口 -> 实际 MCP 工具”的映射；
- 若后续替换 MCP server，只改适配层，不改 bundle 和 scene builder。

## 8. 场景与美术策略

### 8.1 画面目标
输出至少四类可用资产：
1. **主宣传图**：45° 斜俯视真实感静帧；
2. **工程审阅图**：正交 top/front/side；
3. **结构解释图**：exploded view；
4. **动态展示图**：turntable 或布局演化动画。

### 8.2 材质与风格
- 舱体结构：拉丝铝或喷砂铝；
- 热控面：黑色/银色 MLI；
- 散热板：高导热金属色；
- 电池/电子盒：阳极氧化深灰；
- 太阳翼：蓝黑色电池片 + 金属框；
- 禁区/FOV：半透明颜色体，可作为工程调试开关，不作为最终宣传图默认开启。

### 8.3 资产回退机制
- 命中资产：使用高保真资产；
- 未命中资产：box/cylinder + 材质；
- 缺少材质：用类别默认材质；
- 整体异常：退化为“分析风格渲染”，但仍输出结果，不阻断 run。

## 9. 坐标、单位与几何语义

### 9.1 单位转换
- 仓内 `DesignState` 单位是 `mm`；
- Blender 场景建议统一转为 `m`；
- 需在 bundle 中显式记录 `mm -> m = 0.001` 转换，禁止隐式缩放。

### 9.2 坐标约定
- 在 bundle 中固定源坐标系和目标坐标系；
- 若 Blender 模板使用不同前向轴，统一在 `scene_builder.py` 做轴变换；
- 不允许在资产文件和代码两侧同时做旋转补偿，避免双重旋转。

### 9.3 几何语义增强
建议把下列语义从 `metadata/BOM` 中补进 bundle：
- `display_name`
- `render_role`
- `material_hint`
- `deployable=true|false`
- `mission_side=+X/-X/+Y/-Y/+Z/-Z`
- `is_external=true|false`
- `heat_level=low|mid|high`

这些字段不进入求解约束，只服务视觉表达。

## 10. 阶段实施方案

### 阶段 P0：离线回放渲染打通
目标：
- 不改主流程，只从已有 `experiments/run_*` 目录构建 bundle 和 render。

交付：
- `run/render_blender_scene.py`
- `render_bundle.json`
- `visualizations/blender/final_iso.png`
- `visualizations/blender/render_manifest.json`

DoD：
- 组件数量与 snapshot 完全一致；
- 位姿误差控制在 `<= 1 mm / 1 deg`；
- 缺失资产可自动回退。

当前状态：
- **已实现并验证**；命令入口为 `run/render_blender_scene.py`。

### 阶段 P1：post-run 自动渲染
目标：
- 在 `mass` 最终快照生成后，按开关触发 Blender MCP 渲染。

建议配置：
- `visualization.blender_mcp.enabled: false|true`
- `visualization.blender_mcp.trigger: off|manual|post_run`
- `visualization.blender_mcp.profile: engineering|realistic`
- `visualization.blender_mcp.fail_mode: warn`

DoD：
- 主流程成功时可自动产出静帧；
- 渲染失败只写告警，不影响 `summary.json` 主结论。

### 阶段 P2：资产库与真实感提升
目标：
- 引入 `assets/blender/` 组件库，提升“像卫星”的程度。

DoD：
- 至少覆盖 `payload/avionics/power/antenna/radiator/solar_panel/structure` 七类；
- 未覆盖组件仍可回退；
- 输出 4 视角 still + 1 个 turntable。

### 阶段 P3：演化动画与讲故事能力
目标：
- 利用 `snapshots/*.json` 做布局演化动画或 exploded storytelling。

DoD：
- 支持 `layout_replay.mp4`；
- 支持 `exploded_view.png`；
- 可把关键 operator action 标到字幕或 overlay。

### 阶段 P4：STEP 桥接增强
目标：
- 对已存在真实 CAD 的部件，引入 STEP -> mesh 桥接。

DoD：
- 明确选定桥接器；
- 三角化后的比例、法线、层级与材质归属稳定；
- 桥接失败时自动回退到 primitives/asset 路径。

## 11. 验证与验收

### 11.1 自动验证
建议新增测试：
- `tests/test_blender_render_bundle.py`
- `tests/test_blender_asset_mapper.py`
- `tests/test_blender_render_manifest.py`
- `tests/test_blender_postrun_hook.py`

覆盖点：
- bundle schema、单位转换、组件数量、附件字段；
- 类别到资产/材质映射；
- 渲染产物落盘与失败状态记录；
- mock MCP 下 post-run 钩子不阻断主链。

### 11.2 手工验收
- 随机抽取 `L1/L2/L4` 各 1 个成功 run；
- 对比 `final_selected` 快照与渲染图的组件数量、相对位置、外部结构；
- 检查太阳翼/天线/散热板/支架是否按规则出现；
- 检查 `render_manifest.json` 是否记录 profile、模板、MCP server 版本、耗时、输出文件。

### 11.3 建议指标
- 几何一致性：组件数量 100% 对齐；
- 位姿一致性：平移误差 `<= 1 mm`，旋转误差 `<= 1 deg`；
- 资产覆盖率：P2 目标 `>= 80%`；
- 渲染稳定性：批量成功率 `>= 95%`；
- 性能预算：单张 still `<= 60s`，turntable `<= 5 min`（本地 Eevee 口径）。

## 12. 风险与缓解

### 12.1 STEP 不能直进 Blender
- 风险：桥接链复杂且不稳定；
- 缓解：P0/P1 先走 `DesignState -> primitives/assets`，STEP 仅作可选增强。

### 12.2 资产库建设成本高
- 风险：早期组件类型太多，难一次补全；
- 缓解：先做类别级资产，未命中自动回退参数化几何。

### 12.3 MCP server 能力不一
- 风险：不同 Blender MCP 项目接口差异大；
- 缓解：仓内定义统一 `client.py` 适配层，不把业务逻辑写死在具体工具名上。

### 12.4 真实感与工程真实性冲突
- 风险：渲染图“好看但不忠实”；
- 缓解：保留 `engineering` 与 `realistic` 双 profile；论文/工程审阅默认工程 profile，宣传页再用 realistic profile。

### 12.5 渲染耗时影响主流程
- 风险：自动渲染拖慢 benchmark；
- 缓解：默认关闭，仅在单 run 或代表组上开启；批量 benchmark 中建议离线回放渲染。

## 13. 推荐实施顺序

1. 先做 `render_bundle.json` 生成器；  
2. 再做 mockable Blender MCP client；  
3. 然后打通离线 CLI 回放；  
4. 再补资产映射与模板场景；  
5. 最后才把 post-run 钩子接到 `mass` 流程。  

## 14. 结论

对于 MsGalaxy，最稳妥的路线不是“把 Blender 塞进优化器”，而是：

- 继续以 `pymoo + physics + strict gate` 作为主求解真相；
- 以 `final_selected` 快照、`DesignState`、可选 STEP 为输入；
- 用 Blender MCP 构建一个可审计、可回放、可失败降级的真实感渲染侧链；
- 先做参数化场景重建，再做资产化与 STEP 桥接增强。

这条路径兼顾了工程可落地性、论文展示效果和仓库当前真实边界，是当前最合适的接入方案。
