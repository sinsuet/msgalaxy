# R54 Single Archetype Canonical Thermal Fix 20260311

## 任务范围

本轮仍然只处理固定单案例：

- archetype 固定为 `optical_remote_sensing_microsat`
- 不扩多 archetype
- 不重构 COMSOL 求解器
- 不重写 DSL v4
- 只修 teacher/demo 单案例在 review package 绑定与 real COMSOL canonical thin-slice 上的阻塞点

## 本轮修复内容

### 1. teacher_demo review package 改为显式单案例映射

更新：

- `tools/comsol_field_demo/tool_single_archetype_demo_case.py`
- `tests/test_single_archetype_demo_case.py`

修复前：

- 单案例 helper 只传 `field_case_dir`
- review package 内部会把每个 step 解析成 `default_case_dir`
- `teacher_demo` gate 因 `defaulted_step_count > 0` 被阻塞

修复后：

- helper 会基于 run snapshot 生成单案例显式 `field_case_map`
- 每个 step 都绑定到同一个固定 `field_case_dir`
- 解析来源改为 `explicit_step_index`

定向结果：

- `field_case_gate.status = passed`
- `defaulted_step_count = 0`
- `matched_by_index_count = 3`

### 2. fixed single-case config 补齐 canonical 开关

更新：

- `tools/comsol_field_demo/configs/single_archetype_teacher_demo.yaml`
- `tools/comsol_field_demo/tool_run_fields.py`
- `tests/test_single_archetype_demo_case.py`

本轮把以下字段固定在单案例 config 内，并透传到 driver：

- `physics_profile = thermal_static_canonical`
- `enable_canonical_thermal_path = true`
- `enable_power_continuation_ramp = true`
- `orbital_thermal_loads_available = false`

这一步只服务固定 teacher/demo case，不改变通用默认口径。

### 3. canonical / diagnostic 的 driver 分支修正

更新：

- `simulation/comsol_driver.py`
- `tests/test_comsol_driver_p0.py`

修复内容：

- canonical profile 在未显式开启 continuation 时，默认不再把 `P_scale` 当成全局默认
- 单元测试明确钉住：
  - canonical requested profile 下，driver 默认 continuation 判定不会被静态默认值误导

### 4. 热源绑定恢复到 5/5

更新：

- `simulation/comsol/thermal_operators.py`

修复点：

- 当 `intersects` 命中多个域、`allvertices` 又收紧到 0 域时，不再直接失败
- 改为回退到 `intersects` 候选集合，再执行域歧义收敛

对当前固定案例的真实效果：

- `payload_camera` 不再丢热源
- `heat_binding_report.assigned_count` 从先前的 `3/5` 或 `4/5` 提升到 `5/5`
- `failed_components = []`

### 5. canonical 辐射边界最小修复

更新：

- `simulation/comsol/model_builder.py`

本轮做了两个薄修：

- 给 shell outer boundary 补一层显式 surface emissivity material
- canonical `SurfaceToAmbientRadiation` 的 `Tamb` 改为直接使用 `ambient_temperature_k`，不再错误取 `min(surface_temperature_k, ambient_temperature_k)`

## 官方 COMSOL 依据

本轮判断只引用官方 COMSOL 文档：

- [Heat Transfer with Surface-to-Surface Radiation](https://doc.comsol.com/6.1/doc/com.comsol.help.heat/heat_ug_multiphysics_interfaces.11.03.html)
- [Surface-to-Ambient Radiation](https://doc.comsol.com/6.2/doc/com.comsol.help.heat/heat_ug_ht_features.09.088.html)
- [Thermal Radiation theory context](https://doc.comsol.com/6.2/doc/com.comsol.help.heat/heat_ug_theory.07.048.html)
- [CAD import selections / import behavior](https://doc.comsol.com/6.1/doc/com.comsol.help.comsol/comsol_ref_geometry.22.074.html)

明确区分：

- 文档事实：
  - `SurfaceToAmbientRadiation` 是 canonical heat-transfer feature
  - 辐射边界需要 surface emissivity 输入
- 本地推断：
  - 当前单案例 imported geometry 下，boundary emissivity 与热源域映射都需要显式兜底，才能让 fixed demo case 进入稳定求解尝试

## 最小验证

### 1. 单案例 review package 回归

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_single_archetype_demo_case.py -q
```

结果：

- `6 passed`

覆盖到：

- fixed case authored inputs
- `SatelliteLikenessGate`
- single-case canonical config contract
- teacher_demo review package 显式 field-case 绑定

### 2. canonical continuation 默认值单元回归

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_comsol_driver_p0.py -k "canonical_profile_skips_power_continuation_ramp_by_default" -q
```

结果：

- `1 passed`

### 3. 真实 COMSOL 单案例 materialization

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python tools/comsol_field_demo/tool_single_archetype_demo_case.py
```

本轮最终状态：

- `requested_physics_profile = thermal_static_canonical`
- `physics_profile = thermal_static_canonical`
- `effective_profile_release_grade = true`
- `degradation_reason = ""`
- `thermal_realness_level = official_interface_thin_slice`

同时已确认：

- `heat_binding_report.assigned_count = 5`
- `heat_binding_report.failed_components = []`
- canonical boundary 日志已落为 `SurfaceToAmbientRadiation 已设置: Tamb=293.15K`

但最终仍失败在：

- canonical stationary solve 非线性收敛
- `structural_runtime.stat_solved = false`
- `structural_runtime.modal_solved = false`

本轮最新失败产物：

- `tools/comsol_field_demo/output/single_archetype_demo/optical_remote_sensing_microsat_teacher_demo/mph_models/model_optical_remote_sensing_microsat_teacher_demo_20260311_192414_987100.mph`

## 当前判定

### 已解决

- 单案例 teacher_demo review package 显式绑定已打通
- 单案例 canonical config 合同已固定
- payload 主体热源丢失问题已解决
- canonical 辐射边界与 `Tamb` 配置错误已修正
- source claim 不再被错误降级到 `diagnostic_simplified`

### 仍未解决

- 真实 canonical 热稳态在 very-low-power continuation 第一步仍不收敛
- 因热稳态未过，结构静力/模态结果仍未形成有效 release-grade 证据

## 根据 ADR 的下一步

下一步仍应保持“单案例、单 archetype、薄切片”原则，只推进这一条：

1. 不扩 archetype，不动 review package 核心设计。
2. 继续锁定 `optical_remote_sensing_microsat_teacher_demo`。
3. 只在 canonical solver setup 上做最小收敛修复，优先排查：
   - canonical radiation feature 的求解器初值与 nonlinear damping
   - continuation step 配置是否需要改为 study-level official continuation，而不是仅靠 `P_scale`
   - imported geometry 下全局薄层导热网络与 radiation 边界的组合是否导致过强非线性
4. 只有在 canonical thermal first solve 收敛后，再复验：
   - `structural_runtime.stat_solved`
   - `structural_runtime.modal_solved`
   - real review package 成品目录

## 结论

这轮修复已经把问题从“案例链路不通、热源不全、canonical claim 不成立”推进到了更窄的单点 blocker：

- 现在固定单案例输入是稳定的；
- review package 合同是通的；
- canonical source claim 也已经回正；
- 剩下的是 COMSOL canonical thin-slice 在该固定案例上的稳态收敛问题。

这符合 ADR 的推进方式：先把单案例基线收窄到一个明确的真实物理阻塞点，再决定下一刀切在 solver setup，而不是继续扩 archetype 或做系统重构。
