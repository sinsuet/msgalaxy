# R53 Single Archetype Real COMSOL Teacher Chain 20260311

## 任务范围

本次只推进 `optical_remote_sensing_microsat` 固定单案例的 Phase A：

- 不扩 archetype 数量；
- 不重构 COMSOL 求解器；
- 不改 DSL v4；
- 只让单案例真实经过现有 COMSOL field-demo 链，并核对其是否满足 `ADR-0012` / `ADR-0014` 的下一步推进条件。

## 本次薄改

### 1. 单案例 helper 默认绑定独立 canonical config

新增：

- `tools/comsol_field_demo/configs/single_archetype_teacher_demo.yaml`

用途：

- 仅服务固定单案例；
- 显式声明 `physics.physics_profile=thermal_static_canonical`；
- 不改变通用 demo dataset 的默认配置口径。

### 2. field-demo driver config 显式透传 physics profile

更新：

- `tools/comsol_field_demo/tool_run_fields.py`

本次只补最小透传字段：

- `physics_profile`
- `orbital_thermal_loads_available`

不改现有求解器实现，不改 study 结构，不改导出链。

### 3. 单案例 helper 默认使用上述独立 config

更新：

- `tools/comsol_field_demo/tool_single_archetype_demo_case.py`

效果：

- 直接运行 helper 时，会默认走 `single_archetype_teacher_demo.yaml`；
- 因而单案例会明确“请求 canonical profile”，而不是静默落回 sidecar 默认 `diagnostic_simplified`。

### 4. 补一个定向回归断言

更新：

- `tests/test_single_archetype_demo_case.py`

新增断言：

- 单案例 helper 的默认 config 路径正确；
- 传入 driver 的 `physics_profile` 已是 `thermal_static_canonical`。

## 额外支撑修复

在执行最小验证时，当前工作区存在一个会阻断本次链路的循环导入：

- `core.logger -> visualization.review_package -> core.visualization -> core.logger`

为恢复单案例 helper / pytest 的最小执行能力，做了一个局部支撑修复：

- `core/visualization.py`

修复方式：

- 去掉其对 `core.logger` 的顶层导入；
- 改为直接使用标准库 `logging.getLogger("visualization")`；
- 不改任何可视化业务逻辑。

这不是本次领域目标的一部分，但它直接阻断单案例验证，因此作为最小支撑修复一并纳入。

## 真实 COMSOL 运行

执行命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python tools/comsol_field_demo/tool_single_archetype_demo_case.py
```

真实输出目录：

- `tools/comsol_field_demo/output/single_archetype_demo/optical_remote_sensing_microsat_teacher_demo/`

已确认存在的关键产物：

- `geometry/demo_layout.step`
- `field_exports/manifest.json`
- `field_exports/simulation_result.json`
- `tensor/manifest.json`
- `renders/manifest.json`
- `materialization_manifest.json`

同时已生成：

- `mph_models/model_optical_remote_sensing_microsat_teacher_demo_20260311_183045_699808.mph`

## Phase A 结果判定

### 1. 产物链是通的

本次真实 COMSOL 运行完成后：

- STEP 成功导出；
- temperature / stress / displacement 场导出成功；
- tensor 成功导出；
- render manifest 成功导出；
- `materialization_manifest.json.errors = []`。

因此从“固定单案例能否真实穿过现有 COMSOL field-demo sidecar”这个问题看，答案是：

- 能；
- 且已具备后续 review package 所需的 `field_case_dir` 形态。

### 2. 但 canonical claim 没有成立

`field_exports/manifest.json` 中的真实 source claim 为：

- `requested_physics_profile = thermal_static_canonical`
- `physics_profile = diagnostic_simplified`
- `effective_profile_release_grade = false`

降级原因明确写出为：

- `thermal path uses diagnostic_simplified operators: P_scale, simplified_boundary_temperature_anchor, weak_convection_stabilizer`

这说明本次运行已经满足“真实 COMSOL 执行”，但仍不满足 `ADR-0012` 所要求的 canonical release-grade 物理口径。

### 3. 结构支路不是 release-grade

运行日志与 `simulation_result.json` 共同表明：

- 结构静力求解失败并回退；
- `first_modal_freq = null`
- `structural_source = ""`

因此当前单案例虽然导出了位移/应力图，但不应把它描述成“结构 canonical 已通过”。

### 4. 热源绑定仍有单案例几何问题

`simulation_result.json.raw_data.heat_binding_report` 表明：

- `assigned_count = 3`
- 绑定失败组件：
  - `avionics_stack`
  - `thermal_control_unit`

这意味着该单案例虽然具备“第一眼像卫星”的外观与输入合同，但其当前精确几何/域映射仍未完全支撑组件级热源绑定。

## 与 ADR 的对应关系

### 对 ADR-0010 / 0011

本案例继续保持单 archetype 固定输入的事实基线：

- archetype 没变；
- shell/panel/aperture 没变；
- 最小目录件和任务面语义没变；
- 真实 STEP / COMSOL 路径也已接入同一个 authored case。

因此 `ADR-0010 / 0011` 关心的“有 archetype 归属、像卫星、并能进入后续链路”的目标已从 fake driver 进一步推进到 real COMSOL sidecar。

### 对 ADR-0012

本次验证出的真实状态是：

- profile 请求已正确透传；
- 但 runtime 仍因为简化热路径而降级为 `diagnostic_simplified`；
- 这正是下一步必须处理的 canonical physics gap。

因此 `ADR-0012` 的下一步不应是继续扩 archetype，而应是针对这个固定单案例移除 diagnostic simplifications，先拿到真正的 `thermal_static_canonical` source claim。

### 对 ADR-0014

本次 real COMSOL 已经把 `field_case_dir` 需要的核心产物写齐，因此 review package 的输入合同面是可用的。

但由于物理 source claim 仍是 diagnostic，本次不把它上升为正式 teacher-demo 成品包，只将其视为：

- review package 可消费的真实字段案例；
- 但还不是 release-grade teacher evidence。

## 最小验证

### 1. 定向 pytest

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_single_archetype_demo_case.py -q
```

结果：

- `6 passed`

### 2. 真实 COMSOL 单案例 materialization

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python tools/comsol_field_demo/tool_single_archetype_demo_case.py
```

结果：

- 命令返回码 `0`
- 单案例产物链完整写出
- 但 canonical claim 被 runtime 降级到 `diagnostic_simplified`

## 结论

本次 Phase A 的结论是“部分成功”：

- 成功点：固定 `optical_remote_sensing_microsat` 单案例已经真实穿过现有 COMSOL field-demo 链，产出完整 `field_case_dir`；
- 未完成点：它还不是 `ADR-0012` 意义上的 canonical teacher evidence；
- 主要 blocker：热路径仍依赖 `P_scale + simplified boundary anchor + weak convection stabilizer`，且单案例仍有 2 个组件热源绑定失败、结构静力支路失败回退。

## 根据 ADR 的下一步

下一步应继续保持“单案例、单 archetype、薄切片”原则，只做以下最小推进：

1. 针对这个固定案例，移除或受控替换当前热路径中的 `diagnostic_simplified` 算子，目标是让 `requested_physics_profile=thermal_static_canonical` 不再被降级。
2. 修复 `avionics_stack` 和 `thermal_control_unit` 的域选择/热源绑定问题，优先从几何域映射与 Box Selection 容差入手。
3. 在不重构求解器的前提下，确认结构支路为何在该单案例下失败，并将其收敛到“至少 source claim 明确、无静默 fallback”的状态。
4. 只有在上述 1~3 达成后，才继续推进把这个真实 `field_case_dir` 绑定成正式 `teacher_demo` review package 成品目录。
