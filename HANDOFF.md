# MsGalaxy HANDOFF

**Role**: Single Source of Truth (SSOT)  
**Last Updated**: 2026-03-07 01:20 +08:00 (Asia/Shanghai)  
**State Tag**: `mp-op-maas-v3-transition`  
**Current Focus**: 新版 `L1-L4` 串行 `NSGA-III + real COMSOL` 主线验证。`L1/L2` 已在 strict-real 口径下跑通并通过 gate / `dset` 复核；当前暂停于 `L2`，恢复后继续 `L3 -> L4`。旧 benchmark 资产已清空，后续按新命名规则单独重建。

---

## 1. 当前真实状态（Implemented vs Planned）

### 1.1 已实现（可执行）
- 两条优化模式已接入运行时路由：
  - `optimization.mode=agent_loop`
  - `optimization.mode=mass`
- `mass` 为 A/B/C/D 闭环：
  - A `ModelingIntent` 生成/构建
  - B 硬约束规范化为 `g(x) <= 0`
  - C 编译为 pymoo 问题并执行（`nsga2/nsga3/moead`）
  - D 诊断、反射、可选放松与重试
- Mass 专用 RAG（`CGRAG-Mass`）已切换为当前唯一检索后端：
  - 代码路径：`optimization/knowledge/mass/*`
  - 证据库：`data/knowledge_base/mass_evidence.jsonl`
  - 旧通用 RAG 兼容层已移除，不再维护双路径
- OP-MaaS v3 薄切片（M2/M3）已可执行：
  - 多物理执行链进入主评估：`thermal + structural + power + mission keepout`
  - `structural`：COMSOL 结构支路（`Solid + Stationary + Eigenfrequency`）可执行，并带失败回退
  - `power`：COMSOL 电学支路（`ec + terminal/ground + std_power`）可执行，并保留 DC 网络方程回退
  - 热-结构-电耦合 study 框架 `std_coupled` 已建好执行骨架
  - `Operator Program DSL v3` 动作族已打通 `validator -> intent handler -> codec -> runner`
- simulation 重构与契约收敛已落地：
  - `simulation/comsol_driver.py` 已降为薄门面，核心逻辑拆分到 `simulation/comsol/*`
  - `simulation/contracts.py` 统一约束判定与来源标签
  - `mission_keepout` 已具备 repair-before-block 预检修复机制
  - `source/operator-family/operator-realization` 三类 strict gate 已可阻断非真实结论
- L1-L4 主线已按当前能力重构：
  - MASS BOM：
    - `config/bom/mass/level_L1_foundation_stack.json`
    - `config/bom/mass/level_L2_thermal_power_stack.json`
    - `config/bom/mass/level_L3_structural_mission_stack.json`
    - `config/bom/mass/level_L4_full_stack_operator.json`
  - MASS level profile：
    - `config/system/mass/level_profiles_l1_l4.yaml`
    - `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
  - canonical 物理域：`geometry/thermal/structural/power/mission(keepout)`
  - canonical 算子集：`group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`
- 当前对外保留的主线入口只有：
  - `run/run_scenario.py`
  - `run/mass/run_L1.py` ~ `run/mass/run_L4.py`
  - `run/agent_loop/run_L1.py` ~ `run/agent_loop/run_L4.py`
- 旧批量 benchmark 入口、旧模板、旧测试链已删除；`benchmarks/` 已在 2026-03-07 清空，后续若重建必须遵循 `RULES.md` 的短名规则。
- Blender 可视化侧链 P0 已落地：可从 run 目录生成 `render_bundle.json`、Blender 场景脚本、Codex brief，并可选 direct Blender render。

### 1.2 未实现/仅规划（不可过度声明）
- M4（神经可行性预测、神经算子策略、多保真神经调度）尚未开始实现。
- mission/FOV/EMC 高保真路径仍依赖外部 evaluator；当前仓内默认执行的是 keepout 代理接口。
- L1-L4 新版轻量 benchmark 框架目前不存在；后续需要基于新模板与新命名规则重建。
- 当前还没有新的 `LLM intent vs deterministic` 对照结论；这一阶段尚未开始。

---

## 2. 架构基线（当前项目口径）

### 2.1 主体架构
- LLM 层：需求理解、约束/目标编排、反射建议、策略更新。
- pymoo 层：多目标搜索核心（`NSGA-II` / `NSGA-III` / `MOEA/D`）。
- Physics 层：proxy 快评估 + online COMSOL + 电源网络方程回退。
- 当前恢复工作主线聚焦在 `mass`：保持 MOEA 为数值优化核心，不以 LLM 直接输出最终坐标替代搜索。

### 2.2 约束契约（当前有效）
- 硬约束统一规范为 `g(x) <= 0`。
- 当前主线 mandatory hard constraints 覆盖：`collision/clearance/boundary/thermal/cg_limit`。
- L2-L4 在 level profile 中分级增加 `structural/power/mission_keepout` 约束收口。
- strict-real 复核必须检查：
  - `source_gate_passed`
  - `operator_family_gate_passed`
  - `operator_realization_gate_passed`
  - `run_log.txt` 中 `Dataset "dset*" does not exist` 计数为 `0`

### 2.3 搜索空间与求解器
- 当前可执行变量类型：`continuous` / `integer` / `binary`。
- 当前主线调试策略：
  - 只做串行单次运行
  - 当前只跑 `NSGA-III`
  - 当前只跑真实 COMSOL
  - 逐级推进：`L1 -> L2 -> L3 -> L4`

---

## 3. 近次关键证据（可追溯）

### 3.1 代码与入口
- 当前主线入口：
  - `run/run_scenario.py`
  - `run/mass/run_L1.py`
  - `run/mass/run_L2.py`
  - `run/mass/run_L3.py`
  - `run/mass/run_L4.py`
- 当前主线配置：
  - `config/system/mass/base.yaml`
  - `config/system/mass/level_profiles_l1_l4.yaml`
  - `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
- 当前主线 BOM：
  - `config/bom/mass/level_L1_foundation_stack.json`
  - `config/bom/mass/level_L2_thermal_power_stack.json`
  - `config/bom/mass/level_L3_structural_mission_stack.json`
  - `config/bom/mass/level_L4_full_stack_operator.json`

### 3.2 串行 strict-real 实跑结果（2026-03-07）
- `experiments/0307/0141_l1_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=38.858394212138705`
  - `source/operator-family/operator-realization` 三类 gate 全通过
  - `run_log.txt` 中 `dset` 错误计数为 `0`
- `experiments/0307/0200_l2_nsga3/summary.json`
  - 中间调参证据：`status=PARTIAL_SUCCESS`
  - `diagnosis_status=no_feasible`
  - 主违例只剩 `g_clearance=2.0`
  - 其余 gate 与 `dset` 检查已正常
- `experiments/0307/0209_l2_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=28.11641116193352`
  - `power_margin=61.4`
  - `source/operator-family/operator-realization` 三类 gate 全通过
  - `run_log.txt` 中 `dset` 错误计数为 `0`
- 结论：新版 `L1/L2` 模板与 real-strict profile 已验证可用；当前恢复点是 `L3`。

### 3.3 回归测试
- 已通过：
  - `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py tests/test_maas_core.py tests/test_api.py -q`
  - 结果：`140 passed`
- 已通过：
  - `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_core.py tests/test_api.py -q`
  - 结果：`66 passed`
- 注：`conda run` 在本环境偶发打印误导性 `ERROR conda.cli.main_run...` 文本，但上述两轮返回码均为 `0`，以退出码与 pytest 汇总为准。

### 3.4 收尾治理（2026-03-07）
- `benchmarks/` 历史产物已全部清空。
- `RULES.md` 已新增统一命名规则：
  - benchmark 目录：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
  - 运行目录：`experiments/<YYYYMMDD>/<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
  - helper 脚本：`bm_<scope>.py` / `tool_<topic>.py` / `audit_<topic>.py`
- 主文档已开始同步收口，不再保留旧批量 benchmark 命令作为当前推荐路径。
- 全局日志策略已收口：实验日志仅保留在 `experiments/<run>/run_log*.txt`；根路径 `logs/` 仅保留长期服务日志（如 `api_server`）。

---

## 4. v3 分阶段状态（M0-M4）

- M0：基线指标与 trace schema 已落地。
- M1：hard-constraint coverage + metric registry 闸门已落地。
- M2：结构/电源 proxy 与真实路径已进入可执行链。
- M3：`Operator Program DSL v3` 已形成可执行薄切片，并已在 strict-real 路径中通过 gate 约束验证。
- M4：未实现，保持规划态。

---

## 5. 当前已知问题与风险

- `L3/L4` 还没有在新版 real-strict 模板上完成单次串行验证。
- 新 benchmark 框架尚未重建；当前仓库不提供可直接复用的批量对照入口。
- `LLM intent` 相对 deterministic 的统计收益尚无新版证据。
- mission 高保真路径仍依赖外部 evaluator；若要求 real-only 且 evaluator 不可用，会被 strict gate 阻断。
- 运行目录命名已支持 `compact`，但历史 `experiments/` 目录仍混有旧格式产物；后续可再统一收口。

---

## 6. 运行建议（当前推荐）

### 6.1 命令前缀（强制）
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...
```

### 6.2 当前推荐：串行 real COMSOL 单次运行
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L1.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L2.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L3.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L4.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
```

### 6.3 strict-real 复核命令
```powershell
$run = 'experiments/0307/0209_l2_nsga3'
(Get-Content "$run/summary.json" -Raw | ConvertFrom-Json) | Select-Object status, diagnosis_status, best_cv_min, source_gate_passed, operator_family_gate_passed, operator_realization_gate_passed
(Select-String -Path "$run/run_log.txt" -Pattern 'Dataset "dset.*does not exist' -AllMatches | Measure-Object).Count
```

### 6.4 命名规则（执行时遵循）
- benchmark 目录短名：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
- experiments 目录短名：`<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
- helper 脚本短名：`bm_<scope>.py` / `tool_<topic>.py` / `audit_<topic>.py`
- 详细说明、修复原因、临时标签写入 `summary.json` / manifest，不再塞进目录或脚本名。

---

## 7. 下一步（优先级）

1. 以当前 `real_strict` profile 为基线，恢复后先跑 `L3` 单次串行 `NSGA-III + real COMSOL`。
2. 若 `L3` 跑通，再推进 `L4`；仍遵循“出现问题就改模板/约束语义，不做大盘矩阵”。
3. `L1-L4` 全部单次跑通后，再重建轻量 benchmark 框架；新框架只服务当前能力，不兼容旧模板。
4. benchmark 重建完成后，再做 `LLM intent vs deterministic` 对照与关键 online COMSOL 复核。
5. M4 与大规模消融继续后置，不提前插入当前主线。

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
- `docs/adr/`
- `docs/reports/`
- `config/bom/mass/level_L1_foundation_stack.json`
- `config/bom/mass/level_L2_thermal_power_stack.json`
- `config/bom/mass/level_L3_structural_mission_stack.json`
- `config/bom/mass/level_L4_full_stack_operator.json`
- `workflow/modes/mass/pipeline_service.py`
- `workflow/orchestrator.py`
- `optimization/modes/mass/maas_mcts.py`
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
- `simulation/engineering_proxy.py`
- `simulation/comsol_driver.py`
- `simulation/power_network_solver.py`
- `run/render_blender_scene.py`
