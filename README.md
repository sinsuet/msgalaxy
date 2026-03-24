# MsGalaxy

MsGalaxy 当前主线已收敛为同仓 `mass` 单栈 scenario runtime。活跃执行链只保留：

- `shell + aperture + catalog components`
- deterministic seed
- `pymoo` position-only search
- STEP 导出
- canonical-only COMSOL
- 稳定审计产物 `summary.json / report.md / result_index.json`

## 当前入口
```powershell
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus
```

查看解析结果：

```powershell
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --dry-run
```

## 当前主链真相
- 唯一活跃 scenario 是 `optical_remote_sensing_bus`
- 搜索变量当前只开放 `position(x, y, z)`
- `mount_face / aperture / orientation` 由 deterministic seed 固定
- 显式 `shell_contact_required=true` 的场景实例会在搜索期保持法向挂载，不允许优化结果把真实安装件漂离 shell
- `ScenarioRuntime` 已改为阶段化状态机：
  - `seed_built -> proxy_optimized -> proxy_feasible -> step_exported -> comsol_model_built -> comsol_solved -> fields_exported`
- `proxy_feasible` 是进入 COMSOL 的硬闸门
- `SatelliteLikenessGate` 现已接入主线，并在 `proxy_feasible` 之后、`STEP/COMSOL` 之前执行
- 默认 `satellite_likeness_gate_mode = strict`
- aperture 对齐载荷的 `payload_face` 现优先使用 `placement_state.mount_face` 真值解析，避免被位置分数误判到侧面组件
- 成功态真实 COMSOL run 默认会保存 `.mph`
- `status / execution_success` 表示主线执行链是否跑通，`real_feasible` 单独表达真实物理是否过约束
- `summary.json / report.md / result_index.json` 现会额外沉淀：
  - `satellite_likeness_gate_mode / satellite_likeness_gate_passed`
  - `satellite_layout_candidate / satellite_likeness_report`
  - `shell_contact_audit / component_thermal_audit / dominant_thermal_hotspot`
- 失败路径也会稳定写出：
  - `summary.json`
  - `report.md`
  - `result_index.json`

## 几何与物理合同
- 搜索代理几何、种子几何、STEP 导出几何已统一到同一条 geometry truth chain
- `payload_camera` 当前有效搜索包络为 `140 x 120 x 170 mm`
- `payload_camera` 目录件现补齐 `4` 个安装柱 + `4` 个 shell contact pad，在不遮挡 `camera_window` 的前提下提供真实挂载接触界面
- `antenna_panel` 在 `+Y` 安装朝向下有效包络为 `120 x 8 x 60 mm`
- `optical_remote_sensing_microsat.optical_avionics_middeck` 当前允许 `communication` 类侧装天线面板通过 zone grammar
- COMSOL 主线只保留 canonical profile
- 默认 `shell mount contact` 现在只会绑定 Union 几何上的真实共享内边界，不再把 shell/component 两侧 box-selection 直接并集后喂给 COMSOL
- `requested_profile == effective_profile` 只代表请求未降级，不等于“所有物理场已真正求解完成”
- source claim 现已区分：
  - `canonical_request_preserved`
  - `*_setup_ok`
  - `*_study_entered`
  - `*_study_solved`

## 当前状态说明
- 活跃主线只保留 `mass`
- `agent_loop` / `vop_maas` / 旧 L1-L4 入口 / review-package / Blender sidecar / teacher-demo 已退出活跃代码面并归档
- `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py` 已重接为受控 smoke harness，复用当前主线公共模块
- 当前可以宣称：
  - canonical-only request 链已通
  - STEP 几何链已通
  - COMSOL model-build / solve / field-export 链已通
  - 主线已能把真实热热点定位到具体组件
  - 主线已能把 shell/contact 是否存在真实共享界面写入审计产物
- 当前可以额外诚实宣称：
  - `optical_remote_sensing_bus` 已在 2026-03-24 三次独立复跑中稳定得到 `satellite_likeness_gate_passed=true` 与 `real_feasible=true`
  - 上述三次复跑都稳定产出 `STEP + mph + fields`
- 当前还不能宣称：
  - 跨环境/跨种子意义上的 release-grade 稳定性
  - 场景矩阵级通过能力
- 最新真实主线证据（2026-03-13）显示：
  - `execution_success=true`
  - `real_feasible=true`
  - `dominant_thermal_hotspot = payload_camera`
  - `payload_camera.max_temp_c ≈ 50.10 degC`
  - `shell_contact_audit.payload_camera.selection_status = shared_interface_applied`
  - `shell_contact_audit.applied_count = 5`
  - `shell_contact_audit.unresolved_count = 0`
  - 该次 run 稳定产出：
    - `STEP`
    - `.mph`
    - `temperature/stress/displacement fields`
  - 这说明当前热点主因确实是几何上缺少真实导热挂载界面；补齐真实安装接触几何后，主线热可行性已经回到约束内
- 本轮 COMSOL 语义修正参考官方文档：
  - [Thermal Contact](https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_ht_features.09.092.html)
  - [Identity Pair](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_definitions.21.110.html)
  - [Pairs in Physics Interfaces](https://doc.comsol.com/6.2/doc/com.comsol.help.comsol/comsol_ref_definitions.19.029.html)

## 关键文件
- `run/run_scenario.py`
- `workflow/scenario_runtime.py`
- `domain/satellite/scenario.py`
- `domain/satellite/seed.py`
- `geometry/catalog_geometry.py`
- `geometry/cad_export_occ.py`
- `simulation/comsol_driver.py`
- `simulation/comsol/physics_profiles.py`
- `api/cli.py`
- `api/server.py`

## 验证
```powershell
python -m pytest tests -q
```

当前仓库状态下，2026-03-12 本轮结果为：

- `190 passed`
- `3 skipped`

2026-03-24 本轮与卫星 gate 修复直接相关的定向验证为：

- `python -m pytest tests/test_satellite_runtime.py tests/test_scenario_runtime_contract.py tests/test_comsol_physics_profiles.py tests/test_satellite_seed.py tests/test_run_scenario_cli.py -q`
- `22 passed`

## 仍需继续
- 继续围绕 `optical_remote_sensing_bus` 做独立复跑与 artifact 审计，确认这次 `real_feasible=true` 结果可重复
- 在该场景通过“重复可行 + stable artifacts”前，不扩场景矩阵
