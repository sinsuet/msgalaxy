# R47 0012 Integration Handoff 20260311

## 任务范围

本次只落实 `docs/adr/0012-comsol-canonical-satellite-physics-contract.md` 对应的最小实现切片，范围限定为：

- COMSOL physics profile 最小合同
- source claim 最小输出
- field / unit / export registry 最小切片
- 配置与导出合同的重构收口

明确不在本次范围内：

- 不修改 archetype
- 不做 shell / aperture 几何协议扩展
- 不改 DSL v4
- 不改 `optimization/`
- 不改 `workflow/`
- 不改 `geometry/cad_export_occ.py`
- 不改 `HANDOFF.md`
- 不改 `README.md`

## 实际改动

### 1. 4 类 physics profile 的实际落点

4 类 profile 已固定落在 [simulation/comsol/physics_profiles.py](/E:/Code/msgalaxy/simulation/comsol/physics_profiles.py)：

1. `thermal_static_canonical`
2. `thermal_orbital_canonical`
3. `electro_thermo_structural_canonical`
4. `diagnostic_simplified`

当前运行时默认入口仍然是 `diagnostic_simplified`，配置落点在 [config/system/mass/base.yaml](/E:/Code/msgalaxy/config/system/mass/base.yaml)，其中：

- `physics_profile: diagnostic_simplified`
- `orbital_thermal_loads_available: false`

这是有意保持“当前实现真实口径”，不把现有简化热路径冒充为 canonical release-grade。

### 2. canonical 与 diagnostic_simplified 的边界

边界已在 [simulation/comsol/physics_profiles.py](/E:/Code/msgalaxy/simulation/comsol/physics_profiles.py) 与 [simulation/comsol/model_builder.py](/E:/Code/msgalaxy/simulation/comsol/model_builder.py) 显式化：

- canonical request 仅在没有降级条件时维持 canonical profile
- 一旦命中当前简化策略，结果显式降级为 `diagnostic_simplified`

当前被明确归类到 `diagnostic_simplified` 的简化项：

- `P_scale`
- `weak_convection_stabilizer`
- `simplified_boundary_temperature_anchor`

对应 source claim 中会输出：

- `physics_profile`
- `thermal_realness_level`
- `structural_realness_level`
- `power_realness_level`
- `degradation_reason`

### 3. field / unit / export registry 的实际落点

字段注册表落在 [simulation/comsol/field_registry.py](/E:/Code/msgalaxy/simulation/comsol/field_registry.py)。

当前最小覆盖范围：

- 温度：`temperature` -> `T` / `K`
- 位移模：`displacement_magnitude` -> `solid.disp` / `m`
- 位移分量：`displacement_u/v/w` -> `u,v,w` / `m`
- von Mises：`von_mises` -> `solid.mises` / `Pa`

当前别名：

- `displacement` -> `displacement_magnitude`
- `displacement_x/y/z` -> `displacement_u/v/w`
- `stress` -> `von_mises`

metric summary 单位合同落在 [simulation/comsol/metric_contracts.py](/E:/Code/msgalaxy/simulation/comsol/metric_contracts.py)，用于显式说明 driver summary 与 field unit 的换算关系，避免直接修改现有数值语义。

### 4. source claim / contract bundle / 导出链接线

source claim 与 profile audit digest 由 [simulation/comsol/physics_profiles.py](/E:/Code/msgalaxy/simulation/comsol/physics_profiles.py) 统一生成。

本次额外做了 `contract_bundle` 收口，统一包含：

- profile 与 release-grade 字段
- realness 字段
- `degradation_reason`
- `profile_audit_digest`
- 合同版本信息
- 对 `source_claim` / `field_export_registry` / `physics_profile_contract` / `simulation_metric_unit_contract` 的 section 指针

接线落点：

- [simulation/comsol_driver.py](/E:/Code/msgalaxy/simulation/comsol_driver.py)
- [tools/comsol_field_demo/tool_run_fields.py](/E:/Code/msgalaxy/tools/comsol_field_demo/tool_run_fields.py)
- [tools/comsol_field_demo/tool_export_tensors.py](/E:/Code/msgalaxy/tools/comsol_field_demo/tool_export_tensors.py)
- [tools/comsol_field_demo/tool_render_fields.py](/E:/Code/msgalaxy/tools/comsol_field_demo/tool_render_fields.py)

driver 的 `raw_data`、field manifest、tensor manifest、render manifest 现在都通过同一合同物化 helper 收口，减少重复拼装。

## 共享接口/配置/数据合同

### 共享 Python 接口

位于 [simulation/comsol/physics_profiles.py](/E:/Code/msgalaxy/simulation/comsol/physics_profiles.py)：

- `build_source_claim(...)`
- `build_profile_audit_digest(...)`
- `build_contract_bundle(...)`
- `resolve_contract_bundle(...)`
- `attach_contract_bundle(...)`
- `build_contract_defaults(...)`
- `materialize_contract_payload(...)`

### 配置合同

当前最小配置接入位于 [config/system/mass/base.yaml](/E:/Code/msgalaxy/config/system/mass/base.yaml)：

- `physics_profile`
- `orbital_thermal_loads_available`

### 数据合同

运行与导出链当前统一暴露：

- `source_claim`
- `contract_bundle`
- `contract_bundle_version`
- `field_export_registry`
- `field_export_registry_version`
- `physics_profile_contract`
- `physics_profile_contract_version`
- `profile_audit_digest`
- `profile_audit_digest_version`
- `simulation_metric_unit_contract`
- `simulation_metric_unit_contract_version`

### COMSOL / license / 模块不可用时的口径

本次实现不会因为 COMSOL license、`Orbital Thermal Loads` 模块、结构/电学设置不可用而阻塞代码接线。

当前行为是显式降级并审计，不静默冒充 canonical：

- `thermal_orbital_canonical` 在 `Orbital Thermal Loads` 不可用时降级
- 结构/电学真实支路不可用时，通过 `structural_realness_level` / `power_realness_level` 明示
- 降级原因进入 `degradation_reason`

这属于合同治理，不等于新增高保真能力。

## 最小验证与结果

本次只跑了范围内最小验证，没有跑全量测试。

执行命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_comsol_physics_profiles.py tests/test_comsol_driver_p0.py tests/test_comsol_field_demo.py -q
```

结果：

- `47 passed in 10.72s`

覆盖点：

- physics profile / source claim / contract bundle builder
- driver `raw_data` 中的合同元数据
- field export -> tensor -> render 三段 manifest 的合同传播

## 未完成项/风险

- 当前默认仍是 `diagnostic_simplified`，这是对现状的真实表述，不是 canonical 已 fully ready 的声明。
- `thermal_orbital_canonical` 目前只有 profile 合同与降级治理，并不代表轨道热高保真链条已完整实现。
- `electro_thermo_structural_canonical` 当前是最小合同接线，不应被表述为完整耦合 release-grade 证据。
- `simulation_metric_unit_contract` 目前用于显式说明 summary 单位换算，没有反向改写既有 driver 指标语义。
- 更高层 summary / report / review package 尚未接入 `contract_bundle` 读取；本次按范围要求没有继续上推。

## 对集成测试的建议

建议后续集成测试只做增量，不要在本轮基础上继续扩 scope：

1. 验证 `run_scenario` 或上层实验产物是否稳定透传 `source_claim` 与 `contract_bundle`
2. 验证非 COMSOL / 模块缺失场景下，`degradation_reason` 与 realness 字段是否保持一致
3. 验证 field demo 之外的下游读取端，是否统一从 registry/contract 取字段与单位，而不是硬编码 `T` / `solid.disp` / `solid.mises`
4. 若后续接入 review package，优先验证 manifest 契约一致性，不先改求解逻辑
