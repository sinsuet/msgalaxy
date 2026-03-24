# R52 Single Archetype Demo Case 20260311

## 任务范围

本次实现只覆盖 1 个固定 archetype 的 teacher/demo 可展示案例，不扩展到多 archetype，不改 COMSOL 求解器，不重写 DSL v4，也不做系统级重构。

固定 archetype：

- `optical_remote_sensing_microsat`

## 交付物

固定案例目录：

- `tools/comsol_field_demo/demo_cases/optical_remote_sensing_microsat_teacher_demo/`

目录内固定输入：

- `case_manifest.json`
- `case_config.json`
- `case_parameters.json`
- `design_state.json`

相关几何模板：

- `config/catalog_components/shell_optical_camera_bus_teacher_demo.json`

辅助加载入口：

- `domain/satellite/demo_case.py`
- `tools/comsol_field_demo/tool_single_archetype_demo_case.py`

定向验证：

- `tests/test_single_archetype_demo_case.py`

## 为什么这个案例符合 archetype

### 1. 外形先验符合 optical remote sensing microsat

- 总线为紧凑 box/monocoque 风格，`bus_span_mm=(420, 320, 300)`，比例落在 `optical_remote_sensing_microsat` 的允许范围内：
  - `x/y = 1.3125`
  - `x/z = 1.4`
  - `y/z = 1.0667`
- `+Z` 面固定为 payload face，并在 shell 上开 `camera_window`
- `+Z` 面叠加 optical hood/baffle，第一眼就能区分为相机载荷面，而不是普通盒体顶板
- `+Y/-Y` 两侧通过 panel variant 固定两块 side wing plate，形成明显的卫星侧翼轮廓
- `-Z` 面显式标为 `radiator_face`

### 2. 任务面/载荷面语义明确

案例在 `case_config.json` 中显式固定：

- `payload_face -> +Z`
- `solar_array_mount -> +Y`
- `radiator_face -> -Z`

同时 appendage 语义也固定：

- `optical_baffle -> +Z`
- `optical_solar_wing -> +Y`
- `optical_solar_wing -> -Y`

### 3. 最小目录件组不是随机堆叠

固定目录件和分区：

- `payload_camera -> optical_payload_tube`
- `battery_pack -> optical_power_base`
- `avionics_stack -> optical_avionics_middeck`
- `reaction_wheel_cluster -> optical_avionics_middeck`
- `thermal_control_unit -> optical_thermal_band`

这组器件已经覆盖 payload / battery / avionics / adcs / thermal 的最小 bus 内容，不再是纯随机盒体堆。

## 与 COMSOL / review package 的输入合同关系

该案例固定了后续链路需要的最小输入面：

- `design_state.json`
  - 固定组件位置、尺寸、质量、功耗、shell 元数据
- `case_config.json`
  - 固定 task type、archetype、task-face semantics、appendages、interior zones
- `case_parameters.json`
  - 固定温度、载荷缩放、power scale 等 field-demo 运行参数
- `shell_optical_camera_bus_teacher_demo.json`
  - 固定 shell/panel/aperture/variant
- `case_manifest.json`
  - 固定下游约定路径：
    - `geometry/demo_layout.step`
    - `field_exports/manifest.json`
    - `field_exports/simulation_result.json`
    - `tensor/manifest.json`
    - `renders/manifest.json`
  - 固定 `review_package_input_mode=field_case_dir`
  - 固定 `review_profile=teacher_demo`

同时已补一个最小封装入口：

- `tools/comsol_field_demo/tool_single_archetype_demo_case.py`

该入口会把固定 authored case stage 到 `tools/comsol_field_demo/output/single_archetype_demo/...`，然后复用现有链路依次生成：

- `geometry/demo_layout.step`
- `field_exports/manifest.json`
- `tensor/manifest.json`
- `renders/manifest.json`

并可直接把该 `field_case_dir` 接给 `teacher_demo` review package。

因此它已经可以作为：

- COMSOL smoke 的固定输入 case
- review package 的直接 `field_case_dir` 输入

## SatelliteLikenessGate 最小校验结果

已使用现有 `SatelliteLikenessGate` 路径对该案例执行最小校验。

执行方式：

- `evaluate_satellite_likeness_for_design_state(...)`
- `default_gate_mode="strict"`

结果：

- `gate_passed = true`
- 通过规则共 5 项：
  - `archetype_match`
  - `bus_aspect_ratio_in_bounds`
  - `task_faces_present`
  - `appendage_templates_in_bounds`
  - `interior_zone_assignments_in_bounds`

## 最小验证

只运行本次范围内的定向验证：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_single_archetype_demo_case.py -q
```

结果：

- `5 passed`

覆盖内容：

- fixed case 的 manifest/config/design_state 可加载
- `SatelliteLikenessGate` strict 路径通过
- shell variant / aperture binding / geometry proxy 合同通过
- STEP export smoke 在当前环境实际通过
- staged field/tensor/render manifests 可生成
- 该 case 可直接接入 `teacher_demo` review package

## 当前距离“老师可展示”还差什么

还差的不是 archetype 固定输入，而是更完整的展示链：

- 还没有把这个案例的固定 render 图、montage、review package 产物作为仓内正式展示样例冻结下来；现在是 helper 可生成，不是仓内已提交展示包
- 还没有把这个 fixed case 跑成一轮真实 COMSOL canonical 结果；当前链路和测试已接通，但证据仍是最小 fake driver，不是 release-grade COMSOL 审计
- 还没有把这个 case 绑定到一个真实优化 run 的 teacher_demo 成品目录；现在已经可作为 `field_case_dir` 输入，但还没有附带固定 run 示例
- 还没有更强的 likeness 审校，例如 FOV/遮挡、外部附体展开学、姿态语义一致性

## 结论

当前交付已经满足“单个 archetype 的最小 fixed teacher/demo 输入案例”：

- 固定为 `optical_remote_sensing_microsat`
- 第一眼具备卫星感，而不是随机盒体堆
- 含 shell、panel/aperture、最小目录件、明确任务面语义
- 通过现有 `SatelliteLikenessGate`
- 具备后续 COMSOL smoke / review package 链路的输入合同

但它还不是完整的老师展示包；下一步应优先补这个固定 case 的真实 COMSOL 证据和固定 review package 成品目录，而不是扩 archetype 数量。
