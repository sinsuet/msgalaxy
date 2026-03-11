# R40 卫星原型与公开参考基线规划（2026-03-10）

## 1. 目的

本报告为 MsGalaxy 下一阶段的“卫星原型驱动建模”提供统一研究与工程规划。它解决的不是“怎样生成更多盒体案例”，而是：

- 怎样让初始布局看起来像真实卫星的某类原型；
- 怎样把公开、官方资料沉淀为可执行的形态语法；
- 怎样为后续目录件、规则、机壳/aperture 与 teacher 可视化提供同一上游事实源。

该报告对应的架构决策是：

- `docs/adr/0010-satellite-archetype-and-reference-baseline.md`

## 2. 当前问题审计

当前仓库虽然明确定位在卫星领域，但初始建模仍存在明显“领域上游缺位”：

1. `ComponentGeometry` 是通用几何对象，尚未绑定卫星原型；
2. 场景 BOM 接近真实器件列表，但没有“属于哪类卫星平台”的上位语义；
3. layout seed 能生成可行排布，但不保证“像卫星”；
4. teacher/demo 反馈已经证明，“仅有可行约束解”不足以构成有说服力的展示；
5. 不少视觉结果仍容易被理解成“随机堆放组件”，而不是“某类卫星平台的合理变体”。

因此，必须把“卫星原型”从软约束提升为正式输入对象。

## 3. 公开参考源基线

`SatelliteReferenceBaseline` 只使用公开、官方或机构正式公开材料，不复制专有 CAD。第一批基线来源固定为：

| 来源 | 用途 | 提炼对象 |
| --- | --- | --- |
| NASA CubeSat 3D Resources | CubeSat 总线与外形直观参考 | `cubesat_modular_bus` |
| NASA CubeSat Design Specification Rev. 14.1 | 标准尺寸、轨道接口、体积限制 | CubeSat rail / deployer 约束 |
| NASA SmallSat Structures, Materials, and Mechanisms | 小卫星结构、材料与机构语义 | bus / panel / appendage 设计边界 |
| NASA SmallSat Power Subsystems | 电池、功率与部署面经验 | 电源区与太阳翼面语义 |
| EnduroSat 16U Platform | 模块化 16U 平台参考 | `cubesat_modular_bus` 的商业化变体 |
| GomSpace NanoStruct / Reference Platforms | 公用平台、面板式外形、模块化 bus | `science_experiment_smallsat` |
| SSTL 平台与 SSTL-MICRO | 微小卫星平台化、载荷面与星务面 | `optical_remote_sensing_microsat` / 公用平台参考 |
| 北斗系统公开说明 | 导航卫星任务功能与系统特征 | `navigation_satellite` 的任务语义 |
| 北斗三号专用导航卫星平台公开说明 | 面板式结构、共面天线等平台特征 | `navigation_satellite` 外形语法 |
| CAST 小卫星平台说明 | 国内小卫星平台参考 | `science_experiment_smallsat` / `radar_or_comm_payload_microsat` |

这些来源用于抽取：

- 外形语法；
- 分区关系；
- 任务面语义；
- 外部附体类别；
- 不可越界项。

不用于：

- 逆向专有内部结构；
- 复刻某个型号的精确 CAD；
- 声称已完成厂家级数字样机。

## 4. 第一批原型族定义

第一阶段只冻结 5 类原型，避免范围失控。

### 4.1 `navigation_satellite`

特征重点：

- 面板式/平台化总线；
- 高稳定姿态与天线/相位中心语义；
- 外表面具备通信/导航相关附体；
- 质量与重心分布更强调平台稳定性。

### 4.2 `optical_remote_sensing_microsat`

特征重点：

- 明确的 `nadir` 任务面；
- 载荷镜筒/遮光罩/开窗或观测 aperture；
- 载荷、星务和热管理分区清晰；
- 光学路径与 FOV 约束显著。

### 4.3 `radar_or_comm_payload_microsat`

特征重点：

- 天线面或高频设备面明确；
- 可能存在 radome、展开天线或板状外部构件；
- 高频设备与热管理耦合更强；
- aperture/面板选择对任务意义更强。

### 4.4 `cubesat_modular_bus`

特征重点：

- 标准化外形；
- rail、模块化堆叠、板卡/电池仓语义明确；
- 更强的体积边界与接口规范；
- 更适合作为紧凑型、标准化演示原型。

### 4.5 `science_experiment_smallsat`

特征重点：

- 通用平台、适配多任务；
- 可有外部实验件或小型附体；
- 平台面和载荷面关系相对灵活；
- 强调可拓展性与模块化。

## 5. `MorphologyGrammar` 最小合同

每个 archetype 必须至少定义以下字段：

- `bus_topology`
- `bus_outer_ratio_range`
- `task_face_semantics`
- `appendage_templates`
- `interior_zones`
- `allowed_shell_variants`
- `attitude_semantics`
- `default_keepout_semantics`
- `archetype_forbidden_patterns`

建议 v1 的 `interior_zones` 固定包含：

- `payload_zone`
- `avionics_zone`
- `power_zone`
- `thermal_sink_zone`
- `structural_support_zone`

## 6. `SatelliteLikenessGate` 规划

`SatelliteLikenessGate` 是 teacher/demo 主链的强制闸门，不是简单打分器。建议拆为“硬失败 + 软评分”两层：

### 6.1 硬失败项

- 未匹配任何 archetype；
- 任务载荷未落在合法任务面/aperture 关系中；
- bus 比例严重越界；
- 外部附体方向与姿态语义冲突；
- aperture 位与载荷类型完全不匹配；
- 组件自由堆叠导致明显随机积木感。

### 6.2 软评分项

- 分区占用是否合理；
- 外部附体是否均衡；
- 热源与散热面关系是否合理；
- 重件是否靠承力区/低质心区；
- 整体外形是否符合 archetype 风格。

teacher/demo 默认要求：

- 无硬失败；
- 软评分高于设定阈值。

## 7. 并行实施边界

该主题可拆成三个并行工作包：

### WP-A：公开资料抽取与 archetype baseline

- 收集来源；
- 抽取形态语法；
- 形成 5 类 archetype 定义文件。

### WP-B：原型驱动的初始实例化

- `任务 -> archetype -> shell variant -> 目录件模板`；
- 初始布局骨架生成；
- teacher/demo 案例模板生成。

### WP-C：外形闸门与可视化验收

- 定义 `SatelliteLikenessGate`；
- 定义多视角检查；
- 把“像卫星”纳入审阅链。

三者可以并行，但先后依赖是：`WP-A` 先冻结字段，再由 `WP-B/WP-C` 实现。

## 8. 验收口径

该主题的验收不以“求解是否最优”为主，而以“原型是否成立”为主：

- 任意初始 teacher/demo 案例必须显式归属于 5 类 archetype 之一；
- 多视角下能明显看出总线、任务面、附体与主要分区；
- 不能再被老师评价为“任意形状”或“随机组件堆”；
- 任何 archetype 都能为后续 aperture、目录件、规则提供稳定上游。

## 9. 风险

- 资料抽取过粗，会导致 archetype 仍然偏泛化；
- archetype 过细，会让 v1 范围失控；
- 外形闸门过松会失去意义，过严会影响可行解覆盖。

建议 v1 先保守：少量 archetype、高解释性字段、强 teacher/demo 约束。

## 10. 参考资料

- NASA CubeSat 3D Resources  
- NASA CubeSat Design Specification Rev. 14.1  
- NASA SmallSat Structures, Materials, and Mechanisms  
- NASA SmallSat Power Subsystems  
- EnduroSat 16U Platform  
- GomSpace NanoStruct 8U / Reference Platforms  
- SSTL Satellite Platforms / SSTL-MICRO  
- 北斗系统公开说明  
- 北斗三号专用导航卫星平台公开说明  
- CAST 小卫星平台公开说明
