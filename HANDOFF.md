# MsGalaxy HANDOFF

**Role**: Single Source of Truth (SSOT)  
**Last Updated**: 2026-03-24 15:06 +08:00 (Asia/Shanghai)  
**State Tag**: `mass-only-active-bus-real-feasible-v1`

## 1. 当前真实状态

### 1.1 已实现
- 主入口已固定为单一 CLI：
  - `python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus`
- 活跃执行主链已固定为 `mass` 单栈同仓 scenario runtime：
  - 输入合同：`SatelliteScenarioSpec`
  - 活跃场景：`optical_remote_sensing_bus`
  - 种子：`domain/satellite/seed.py` deterministic seed generator
  - 搜索：`pymoo` catalog-first position-only search
  - CAD：`geometry/cad_export_occ.py` 导出 shell + aperture + catalog geometry
  - 物理：canonical-only COMSOL request
  - 交付：`summary.json / report.md / result_index.json`
- `workflow/scenario_runtime.py` 已收敛为阶段化状态机：
  - `seed_built`
  - `proxy_optimized`
  - `proxy_feasible`
  - `step_exported`
  - `comsol_model_built`
  - `comsol_solved`
  - `fields_exported`
- 主线已增加稳定的前置闸门：
  - `proxy_feasible=false` 时禁止进入真实 COMSOL
  - `satellite_likeness_gate_mode=strict` 现为 `mass` 主线默认值，并在 `proxy_feasible` 之后、`STEP/COMSOL` 之前执行 `SatelliteLikenessGate`
  - `satellite_likeness_gate_passed=false` 时主线会以 `comsol_block_reason=satellite_likeness_failed` 诚实阻断
  - 失败路径也必须稳定落盘 `summary.json / report.md / result_index.json`
  - `summary.json` 当前稳定字段至少包括：
    - `execution_success`
    - `execution_stage`
    - `proxy_feasible`
    - `proxy_violation_breakdown`
    - `satellite_likeness_gate_mode`
    - `satellite_likeness_gate_passed`
    - `satellite_layout_candidate`
    - `satellite_likeness_report`
    - `real_feasibility_evaluated`
    - `real_feasible`
    - `real_violation_breakdown`
    - `comsol_attempted`
    - `comsol_block_reason`
    - `field_export_attempted`
    - `field_export_error`
    - `shell_contact_audit`
    - `component_thermal_audit`
    - `dominant_thermal_hotspot`
- `mass` 主线搜索变量已锁定为真实值：
  - `variable_coverage = ["position"]`
  - `mount_face / aperture / orientation` 由 deterministic seed 固定
  - 对显式声明 `shell_contact_required=true` 的场景实例，法向安装轴会在搜索边界中保持 flush-to-shell，避免优化结果把真实挂载件漂成热学孤岛
- `config/system/mass/base.yaml` 现默认：
  - `save_mph_each_eval=true`，成功态主线 run 会稳定保存 `.mph`
  - `satellite_likeness_gate_mode=strict`
- `domain/satellite/runtime.py` 中的 `SatelliteArchetype / SatelliteReferenceBaseline / SatelliteLikenessGate` 现已不再停留在 sidecar/diagnostic 面：
  - `workflow/scenario_runtime.py` 会把 gate 审计结果稳定沉淀到 `summary.json / report.md / result_index.json`
  - 当前 gate 通过后继续进入 STEP/COMSOL，失败则在物理阶段前阻断
  - 对 aperture 对齐的载荷语义，gate 现优先使用 `DesignState.metadata.placement_state` 中的 `aperture_site + mount_face` 真值解析 `payload_face`
  - `optical_remote_sensing_microsat` 的 `optical_avionics_middeck` 当前已与活跃 scenario 对齐，允许 `communication` 类侧装天线面板通过 zone grammar
- 搜索代理几何与真实导出几何已开始统一到同一条 truth chain：
  - `geometry/catalog_geometry.py` 引入 `ResolvedGeometryTruth`
  - `CatalogComponentSpec.resolved_proxy()` 默认从真实 `geometry_profile` 推导搜索包络
  - `domain/satellite/seed.py` 会把 `resolved_geometry_truth` 与 `resolved_shell_truth` 回写到 `DesignState.metadata`
  - `geometry/cad_export_occ.py` 已按旋转后的有效包络和真实中心偏移导出 STEP
  - `geometry/geometry_proxy.py` 与 STEP manifest 已包含 rotation / effective bbox / position semantics
- 真实几何合同的当前已知行为：
  - `payload_camera` 搜索/种子有效包络已改为 `140 x 120 x 170 mm`
  - `payload_camera` 目录件现补齐 `4` 个安装柱 + `4` 个 shell contact pad，在不遮挡 `camera_window` 中央开孔的前提下提供真实导热挂载界面
  - `antenna_panel` 在 `+Y` 安装朝向下有效包络为 `120 x 8 x 60 mm`
  - `ComponentGeometry.position` 在 CAD 导出链中按 `effective_bbox_center` 解释
  - `optical_remote_sensing_bus` 中声明 `shell_contact_required=true` 的挂载件会把 `shell_contact_required / mount_axis_locked` 回写到 `placement_state.metadata`
- COMSOL canonical-only 主线已补齐“诚实声明”与阶段审计：
  - `simulation/comsol/feature_domain_audit.py` / `result_extractor.py` 不再只依赖 legacy shell metadata
  - `simulation/comsol/thermal_operators.py` 现只把默认 `Thermal Contact` 绑定到 Union 几何上的真实共享内边界，不再把 shell/component 两侧 box-selection 直接并集后喂给 COMSOL
  - `summary.json / report.md / result_index.json / comsol_raw_data` 现会稳定沉淀 `shell_contact_audit`
  - `comsol_feature_domain_audit` 现会显式报告 `required_shell_contacts_effective`
  - `simulation/comsol/dataset_eval.py` / `result_extractor.py` 现已支持按组件域做 `max/min/mean` 温度审计
  - `simulation/comsol/thermal_operators.py` 已把热源绑定与组件域解析统一到同一套 box-selection + domain-resolution truth chain
  - `simulation/comsol/physics_profiles.py` / `simulation/comsol_driver.py` 已区分：
    - `requested_profile`
    - `effective_profile`
    - `canonical_request_preserved`
    - `thermal/structural/power/coupled requested`
    - `*_setup_ok`
    - `*_study_entered`
    - `*_study_solved`
  - `simulation/comsol/solver_scheduler.py` / `model_builder.py` 已保留：
    - `model_build_succeeded`
    - `solve_attempted`
    - `solve_succeeded`
    - `comsol_execution_stage`
- 字段导出链已改为只在真实求解成功后执行：
  - 不再允许 COMSOL 失败后继续 field export 覆盖根因
- `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py` 已重接为受控 smoke harness：
  - 复用当前 STEP export + `ComsolDriver` + field export registry
  - 不再依赖已删除的 `tool_run_fields.py` / `tool_render_fields.py` / `tool_export_tensors.py`
- API / CLI / logger / release-audit / interaction-store 测试面已收束到 mass-only 真相：
  - 不再保留旧 `bom/task-runtime/agent_loop/vop` 合同断言
- 已完成的历史面清理继续有效：
  - `agent_loop` / `vop_maas` 活跃 runtime、入口、config、tests 已退出主线
  - 旧 review / Blender / teacher-demo / legacy reports / ADR 已迁入 `docs/archive/`

### 1.2 最新实证
- 2026-03-24 本轮卫星 gate 定向修复回归：
  - `python -m pytest tests/test_satellite_runtime.py tests/test_scenario_runtime_contract.py tests/test_comsol_physics_profiles.py tests/test_satellite_seed.py tests/test_run_scenario_cli.py -q`
  - 结果：`22 passed`
- 2026-03-24 本轮 active bus 三次独立复跑（same command, same machine）：
  - `python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --run-label rerun_gate_fix_1`
  - `python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --run-label rerun_gate_fix_2`
  - `python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --run-label rerun_gate_fix_3`
  - 三次结果一致：
    - `status=SUCCESS`
    - `execution_stage=fields_exported`
    - `satellite_likeness_gate_mode=strict`
    - `satellite_likeness_gate_passed=true`
    - `payload_face=+Z`
    - `solar_array_mount=+Y`
    - `real_feasible=true`
    - `final_metrics.max_temp=50.10 degC`
    - `dominant_thermal_hotspot.component_id=payload_camera`
  - 运行目录：
    - `experiments/20260324/150039_mass_rerun_gate_fix_1`
    - `experiments/20260324/150038_mass_rerun_gate_fix_2`
    - `experiments/20260324/150038_mass_rerun_gate_fix_3`
- 2026-03-12 本轮全量测试：
  - `python -m pytest tests -q`
  - 结果：`190 passed, 3 skipped`
- 2026-03-13 本轮定向回归：
  - `python -m pytest tests/test_scenario_runtime_contract.py tests/test_comsol_driver_p0.py tests/test_comsol_physics_profiles.py -q`
  - 结果：`39 passed`
- 2026-03-13 本轮几何/STEP/COMSOL 定向回归：
  - `python -m pytest tests/test_catalog_shell_geometry.py tests/test_geometry_services.py tests/test_scenario_runtime_contract.py tests/test_catalog_shell_step_smoke.py tests/test_comsol_driver_p0.py tests/test_comsol_physics_profiles.py -q`
  - 结果：`102 passed`
- 本轮已明确通过的主线契约覆盖面包括：
  - scenario runtime contract
  - satellite likeness gate block/pass audit
  - run_scenario CLI dry-run
  - canonical COMSOL physics profile truth
  - catalog shell geometry contract
  - geometry services / seed behavior
  - scenario-driven API / CLI
  - mass-only release-audit helpers
- 当前受控 real COMSOL smoke 的已知本机证据：
  - runtime probe 成功
  - STEP 生成成功
  - canonical model build 成功
  - `shell_outer_selection_count > 0`，最近一次本地证据为 `6`
  - `canonical_request_preserved = true`
  - 当前主线已可到达 `fields_exported`，并能稳定产出 `STEP + mph + temperature/stress/displacement fields`
  - 最新真实主线 run：`experiments/20260313/231429_mass_optical_remote_sensing_bus`
  - 该 run 中 `summary.json / report.md` 已稳定落盘组件级热审计：
    - `dominant_thermal_hotspot.component_id = payload_camera`
    - `dominant_thermal_hotspot.max_temp_c = 50.10 degC`
    - 其余目录件温度约 `38-41 degC`
  - 该 run 中 `shell_contact_audit` 已明确：
    - `payload_camera.mount_face=+Z -> selection_status=shared_interface_applied`
    - `payload_camera.effective_boundary_ids = [30,35,39,43,64,70,74,79]`
    - `required_count = 5`、`applied_count = 5`、`unresolved_count = 0`
  - 该 run 中真实可行性合同已通过：
    - `execution_success=true`
    - `real_feasible=true`
    - `thermal_violation = -14.90 degC`
    - `final_metrics.max_temp = 50.10 degC`
  - 当前更强的本地结论是：
    - documented fact: 根据 COMSOL 官方文档，[Thermal Contact](https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_ht_features.09.092.html) 的 boundary 版本属于 Heat Transfer in Solids 的 boundary feature；pair 版本位于 Pairs 菜单下并依赖 pair selection / identity pair
    - documented fact: [Identity Pair](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_definitions.21.110.html) / [Pairs in Physics Interfaces](https://doc.comsol.com/6.2/doc/com.comsol.help.comsol/comsol_ref_definitions.19.029.html) 说明 pair 语义主要服务 assembly / pair-based coupling
    - local fact: 当前导入后的几何 `isAssembly=false`
    - local inference: `payload_camera` 的主因确实是“现几何下没有真实 shell 传热界面”；在补齐真实安装接触几何后，热热点从 `202.50 degC` 回落到 `50.10 degC`
- 结论：
  - 当前可以诚实宣称“canonical-only request 链、STEP 几何链、model-build 链、solve/field-export 链已打通”
  - 当前也可以诚实宣称“主线已具备组件级热点归因能力”
  - 当前也可以诚实宣称“主线已具备 shell/contact 共享界面审计能力”
  - 当前可以诚实宣称“`optical_remote_sensing_bus` 已在 2026-03-24 同日 3 次独立复跑中稳定通过 strict gate，并稳定产出 `STEP + mph + fields`”
  - 还不能宣称“主线已达到跨环境/跨种子意义上的 release-grade 稳定性，或已具备场景矩阵级通过能力”

### 1.3 当前边界
- 当前稳定化目标只有 `mass`
- 当前唯一稳定化场景只有 `optical_remote_sensing_bus`
- 场景矩阵尚未重新开放；在该场景稳定前，不新增第二个真实场景
- 主线仍不接回：
  - `vop_maas`
  - operator stack 扩面
  - 旧 L1-L4 入口
- canonical COMSOL 仍保持 strict block：
  - 不回退到 `diagnostic_simplified`
  - 不回退到 `P_scale` continuation
  - 不回退到 `TemperatureBoundary` 或弱对流稳定锚
- 当前核心目标不是“侥幸跑过一次”，而是：
  - 对失败诚实
  - 对 search/CAD/COMSOL 使用同一几何真值
  - 为后续扩场景留出可审计主线

## 2. 当前推荐入口

### 2.1 统一 dry-run
```powershell
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --dry-run
```

### 2.2 实际运行
```powershell
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus
```

### 2.3 受控 real COMSOL smoke
```powershell
python -m pytest tests/test_real_comsol_vertical_smoke.py -q
```

## 3. 当前关键文件
- `run/run_scenario.py`
- `workflow/scenario_runtime.py`
- `workflow/modes/mass/pipeline_service.py`
- `domain/satellite/scenario.py`
- `domain/satellite/seed.py`
- `geometry/catalog_geometry.py`
- `geometry/geometry_proxy.py`
- `geometry/cad_export_occ.py`
- `simulation/comsol/feature_domain_audit.py`
- `simulation/comsol/dataset_eval.py`
- `simulation/comsol/physics_profiles.py`
- `simulation/comsol/result_extractor.py`
- `simulation/comsol/solver_scheduler.py`
- `simulation/comsol/thermal_operators.py`
- `simulation/comsol/model_builder.py`
- `simulation/comsol_driver.py`
- `simulation/thermal_proxy.py`
- `tools/comsol_field_demo/tool_real_comsol_vertical_smoke.py`
- `api/cli.py`
- `api/server.py`
- `core/logger.py`
- `core/llm_interaction_store.py`
- `optimization/modes/mass/observability/release_audit.py`

## 4. 已验证内容
- `python -m pytest tests -q`
  - 结果：`190 passed, 3 skipped`
- 重点契约测试已覆盖：
  - `tests/test_scenario_runtime_contract.py`
  - `tests/test_run_scenario_cli.py`
  - `tests/test_comsol_physics_profiles.py`
  - `tests/test_real_comsol_vertical_smoke.py`
  - `tests/test_catalog_shell_geometry.py`
  - `tests/test_geometry_services.py`
  - `tests/test_api.py`
  - `tests/test_cli.py`
  - `tests/test_release_audit_tools.py`
- 当前 repo 已清除旧主线残留测试语义：
  - 旧 `agent_loop / vop / bom-runtime / review-package` 断言不再作为活跃测试真相

## 5. 下一步
1. 在真实 COMSOL 目标环境上继续做 canonical-only 主线稳定化，目标顺序固定为：
   - `proxy_feasible`
   - `comsol_model_built`
   - `comsol_solved`
   - `fields_exported`
   - stable artifacts
2. 现阶段已拿到一次 `real_feasible=true` 的主线证据，下一步优先做独立复跑与 artifact 审计，确认该结果可重复，而不是重新回到盲调求解器。
3. 继续只围绕 `optical_remote_sensing_bus` 稳定主线；在它完成重复验证前，不新增场景矩阵。
4. 等该场景达到“重复可行 + artifacts 稳定”后，再按 `controlled smoke -> active bus -> second bus` 顺序扩场景。
