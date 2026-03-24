# R42 COMSOL 规范卫星物理链重构规划（2026-03-10）

## 1. 目的

本报告将 COMSOL 路径从“已能跑通的混合物理薄切片”升级为“可审计的卫星领域 canonical physics 合同”。

对应 ADR：

- `docs/adr/0012-comsol-canonical-satellite-physics-contract.md`

## 2. 当前 COMSOL 路径审计

截至 2026-03-10，仓库现有 COMSOL 路径具备以下真实能力：

- STEP 动态导入；
- 热传导主链；
- 结构支路：`Solid + Stationary + Eigenfrequency`
- 电学支路：`Electric Currents` 薄切片；
- 耦合 study 骨架；
- 字段导出：温度、位移、应力。

但当前热链仍有明显简化项，来自以下真实代码路径：

- `simulation/comsol/model_builder.py`
  - 使用 `TemperatureBoundary`
  - 使用弱对流稳定锚 `HeatFluxBoundary`
  - 使用 `P_scale` 全局参数缩放热源
- `simulation/comsol/thermal_operators.py`
  - 仍以每组件热源绑定为主

这些路径在诊断与工程调试上是合理的，但在“真实卫星热环境”口径下不能继续混用为 canonical path。

## 3. 规范 profile 规划

下一阶段的 COMSOL 物理链必须显式区分 4 类 profile：

### 3.1 `thermal_static_canonical`

定位：

- 用于具备真实机壳、真实功耗和真实热边界的稳态热分析；
- 适合作为 teacher/demo 的最低真实热 profile。

包含内容：

- `Heat Transfer in Solids`
- shell + component + appendage 实体
- 正式热边界
- 正式热接触/导热带

### 3.2 `thermal_orbital_canonical`

定位：

- 用于存在轨道热载荷语义的更高等级 profile；
- 只有在 COMSOL 模块许可与接线满足时才能宣称。

包含内容：

- 轨道热载荷接口；
- 太阳、地球红外、反照等外热流边界；
- 姿态/轨道相关热边界。

### 3.3 `electro_thermo_structural_canonical`

定位：

- 用于综合 teacher/demo 审阅与更高等级工程校核；
- 是三场联动和 step review 的正式来源。

包含内容：

- `Electric Currents`
- `Heat Transfer in Solids`
- `Solid Mechanics`
- 官方多物理耦合
- 标准导出字段与单位治理。

### 3.4 `diagnostic_simplified`

定位：

- 用于调试、快速排查、缺许可降级；
- 明确不是 release-grade 真实卫星物理结论。

允许保留：

- `P_scale`
- 数值稳定锚
- 简化温度边界
- 简化时间/功率函数

## 4. 官方接口与资料基线

该重构主题的正式资料基线固定采用官方 COMSOL 文档：

- CAD Import Module / About the CAD Import Module
- Heat Transfer with Surface-to-Surface Radiation
- Orbital Thermal Loads Interface
- COMSOL Heat Transfer Module release note / updates
- Electric Currents / Terminal / Joule Heating / Solid Mechanics 相关接口文档

工程口径上遵循：

- profile 命名与功能命名尽量贴近官方接口；
- 禁止继续扩散内部自创物理名字；
- 对外汇报时优先说“使用了哪类官方接口”和“何种降级路径”。

## 5. 字段、单位与 dataset/export 合同

三场审阅链必须依赖统一 registry。建议固定 v1 合同如下：

| 领域 | 表达式 | 单位 | 审阅名称 |
| --- | --- | --- | --- |
| 温度 | `T` | `K` | `temperature` |
| 位移模 | `solid.disp` | `m` | `displacement` |
| 位移分量 | `u,v,w` | `m` | `displacement_vector` |
| von Mises | `solid.mises` | `Pa` | `stress` |
| 一阶频率 | `eigfreq` / registry 映射 | `Hz` | `first_modal_freq` |

后续 PNG、tensor、VTK/VTU、report、review package 全部引用同一 registry。

## 6. source claim 与审计策略

每次 run 至少要明确：

- `physics_profile`
- `thermal_realness_level`
- `structural_realness_level`
- `power_realness_level`
- `module_availability`
- `degradation_reason`

若降级到 `diagnostic_simplified`：

- 必须在 `summary/report/review package` 中明确写出；
- teacher/demo 默认不把它当最终展示主图，除非用户显式要求调试图。

## 7. 机壳/aperture 对物理链的影响

下一阶段物理链必须认识到：

- 机壳不是装饰层，而是热流与应力分布主体之一；
- aperture 改变壳体边界、局部热交换与结构刚度；
- panel variant 会改变辐射面、附体挂载与载荷路径。

因此 teacher/release 主链下：

- 机壳必须入模；
- aperture 必须来自 STEP 真拓扑；
- 三场结果必须能看到 shell 响应。

## 8. 并行实施边界

该主题建议拆为三个并行包：

### WP-A：profile schema 与 source claim 治理

- 定义 4 类 profile；
- 定义 module availability 检查；
- 定义降级与审计字段。

### WP-B：canonical thermal/electrical/structural 接线

- 重构热场；
- 重构电热结构联动；
- 清理非规范命名路径。

### WP-C：字段导出与 registry

- 定义字段表达式与单位；
- 统一 dataset/export；
- 服务 review package 和 tensor 链路。

`WP-C` 可与 review package 并行，但依赖 `WP-A` 先冻结 source claim。

## 9. 验收标准

- canonical profile 名称、用途和降级逻辑清晰；
- 机壳/aperture 进入三场求解；
- 出图单位与 COMSOL 导出单位一致；
- review package 可明确区分 canonical 与 diagnostic；
- 不再以非规范内部物理命名作为 teacher/release 口径。

## 10. 风险

- 模块许可不足会限制 `thermal_orbital_canonical`；
- canonical path 上线后，部分历史案例可能因为建模更真实而更难收敛；
- 需要花额外成本治理 COMSOL 表达式、dataset 和 export 稳定性。

建议先完成 `thermal_static_canonical + electro_thermo_structural_canonical`，再推进 `thermal_orbital_canonical`。
