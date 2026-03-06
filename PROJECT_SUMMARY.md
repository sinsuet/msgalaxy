# MsGalaxy Project Summary

**最后更新**：2026-03-07 01:20 +08:00  
**用途**：对外展示、论文叙事、协作口径统一  
**状态事实源**：`HANDOFF.md`

---

## 1. 执行摘要

MsGalaxy 是一个面向小卫星组件布局自动设计的科研系统。  
系统将自然语言需求与 BOM 约束转化为可执行多目标优化问题，在神经符号框架下联合 LLM、MOEA 与多物理评估（proxy + online COMSOL + 电源网络方程），输出满足硬约束的候选布局，并提供全流程可追溯证据。

项目核心价值不在“给出一个坐标答案”，而在：
- 可行域搜索效率；
- 多约束协同下的工程可执行性；
- 面向科研复现与论文归因的可观测性链路。

### 当前阶段目标（2026-03-07）
- 当前只做新版 `L1-L4` 的串行小规模 `NSGA-III + real COMSOL` 主线调通；
- `L1/L2` 已完成 strict-real 可行验证，下一恢复点是 `L3 -> L4`；
- 旧 benchmark 框架与旧模板已移除，后续按当前能力重建轻量 benchmark；
- `LLM intent` 对照、大矩阵与 M4 神经模块均后置。

---

## 2. 科学问题与方法主线

### 2.1 科学问题
- 高维布局变量与强耦合硬约束并存，导致传统单路径优化易陷入不可行区域；
- 高保真仿真（COMSOL）预算昂贵，必须在精度与代价之间做策略调度；
- 结果需要可解释、可复现、可审计，而不仅是“最优值”。

### 2.2 方法主线（Neuro-Symbolic）
- LLM 层：需求理解、建模意图组织、反射建议与策略更新；
- pymoo 层：多目标数值优化核心（`NSGA-II/NSGA-III/MOEAD`）；
- Physics 层：proxy 快评估 + online COMSOL（热/结构/电学）+ DC 电源网络回退。

---

## 3. 当前实现边界（Implemented vs Planned）

### 3.1 已实现（M2/M3）
- `mass` A/B/C/D 闭环可执行；
- Mass 专用 RAG 内核已切换为 `CGRAG-Mass`：
  - 代码路径：`optimization/knowledge/mass/*`
  - 证据库：`data/knowledge_base/mass_evidence.jsonl`
  - 运行时可自动回灌 evidence，并在 `summary.json` 输出 `rag_ingest_*` 指标
- `optimization/` 与 `workflow/` 已完成双模式分仓：
  - `optimization/modes/agent_loop/*`
  - `optimization/modes/mass/*`
  - `workflow/modes/agent_loop/*`
  - `workflow/modes/mass/*`
- 栈级运行分离与 fail-fast 契约已落地：
  - `config/bom/{mass,agent_loop}`
  - `config/system/{mass,agent_loop}`
  - `run/run_scenario.py --stack --level`
- MASS L1-L4 已重构为当前能力主线：
  - level profile：`config/system/mass/level_profiles_l1_l4.yaml` 与 `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
  - BOM：`config/bom/mass/level_L1_foundation_stack.json` 至 `level_L4_full_stack_operator.json`
  - canonical 物理域：`geometry/thermal/structural/power/mission(keepout)`
  - canonical 算子：`group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`
- 旧批量 benchmark 入口、旧模板、旧测试链已移除；`benchmarks/` 历史内容已清空，后续只按新规则重建。
- 多物理执行链当前真实边界：
  - `structural`：COMSOL `Solid + Stationary + Eigenfrequency`
  - `power`：COMSOL `ec + terminal/ground + std_power` + 网络方程回退
  - `mission`：当前主线为 keepout 语义 + 外部 evaluator 接口，非仓内高保真联立求解
- strict gate 体系已形成发布级判定骨架：
  - `source gate`
  - `operator-family gate`
  - `operator-realization gate`
- Blender 可视化侧链 P0 已落地，可从 run 目录生成 `render_bundle.json` 与 Blender 场景脚本。

### 3.2 未实现（M4 / 后置工作）
- M4（神经可行性预测、神经算子策略、多保真神经调度）尚未实现；
- 新 benchmark 框架尚未重建；
- 新版 `LLM intent vs deterministic` 对照尚未启动；
- mission/FOV/EMC 高保真联立仿真仍不应被过度宣称为已实现。

---

## 4. 代表性证据（2026-03-07）

### 4.1 L1 strict-real 已跑通
- 证据：`experiments/0307/0141_l1_nsga3/summary.json`
- 观测：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=38.858394212138705`
  - strict gates 全通过
  - `Dataset "dset*" does not exist` 计数为 `0`

### 4.2 L2 strict-real 已跑通
- 中间调参证据：`experiments/0307/0200_l2_nsga3/summary.json`
  - `diagnosis_status=no_feasible`
  - 主违例只剩 `g_clearance=2.0`
- 收口后证据：`experiments/0307/0209_l2_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=28.11641116193352`
  - `power_margin=61.4`
  - strict gates 全通过
  - `Dataset "dset*" does not exist` 计数为 `0`

### 4.3 最新回归
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py tests/test_maas_core.py tests/test_api.py -q`
- 结果：`140 passed`
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_core.py tests/test_api.py -q`
- 结果：`66 passed`

---

## 5. 当前边界与风险声明

- `L3/L4` 尚未在新版 strict-real 模板上完成串行验证；
- 新 benchmark 框架暂时不存在，这是刻意收口而非遗漏；
- `LLM intent` 当前没有新版收益证据，不能宣称优于 deterministic；
- mission 高保真路径依赖外部 evaluator；在 `real-only` 要求下若 evaluator 不可用，将被 strict gate 阻断；
- 历史 `experiments/` 目录仍可能混有旧命名格式，但 `RULES.md` 已冻结新的短名规则。

---

## 6. 对外叙事建议（与真实实现一致）

1. 问题动机：传统算法在高维强约束布局中可行域探索效率不足，且高保真仿真预算昂贵。  
2. 方法贡献：提出 neuro-symbolic MP-OP-MaaS，LLM 负责建模/反射/策略，MOEA 保持为数值搜索核心。  
3. 工程落地：当前已经把热、结构、电源与 mission keepout 接入统一执行契约，并在 real COMSOL 路径上形成 strict gate 判定链。  
4. 阶段结论：新版 L1/L2 已在 `NSGA-III + real COMSOL` 下跑通；L3/L4 仍在后续推进。  
5. 边界合规：M4、批量 benchmark、LLM 对照收益仍是后续工作，不应提前宣称完成。  

---

## 7. 后续优先方向

1. 恢复后先完成 `L3` 单次串行 `NSGA-III + real COMSOL` strict-real 验证；
2. 再推进 `L4`，仍优先改模板/约束语义，不先扩大战术规模；
3. `L1-L4` 全部跑通后，基于当前模板重建轻量 benchmark 框架；
4. benchmark 重建后，再做 `LLM intent` 对照与关键 online COMSOL 复核；
5. M4 与大规模消融后置到最终论文实验阶段。

---

## 8. 关键入口文件

- `run/run_scenario.py`
- `run/mass/run_L1.py`
- `run/mass/run_L2.py`
- `run/mass/run_L3.py`
- `run/mass/run_L4.py`
- `run/agent_loop/run_L1.py`
- `run/agent_loop/run_L2.py`
- `run/agent_loop/run_L3.py`
- `run/agent_loop/run_L4.py`
- `config/system/mass/base.yaml`
- `config/system/mass/level_profiles_l1_l4.yaml`
- `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
- `config/bom/mass/level_L1_foundation_stack.json`
- `config/bom/mass/level_L2_thermal_power_stack.json`
- `config/bom/mass/level_L3_structural_mission_stack.json`
- `config/bom/mass/level_L4_full_stack_operator.json`
- `workflow/modes/mass/pipeline_service.py`
- `workflow/orchestrator.py`
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
- `simulation/comsol_driver.py`
- `simulation/power_network_solver.py`
- `run/render_blender_scene.py`
