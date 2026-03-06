# MsGalaxy

**面向微小卫星三维布局的神经符号多物理约束优化系统**

MsGalaxy 聚焦卫星领域组件布局自动设计问题：  
从需求文本与 BOM 约束出发，构建可执行的多目标约束优化问题，并在多物理评估（proxy + online COMSOL + 电源网络方程）下输出可行布局候选、诊断证据与可视化轨迹。

> 真实实现边界与状态以 `HANDOFF.md` 为唯一事实源（SSOT）。

---

## 1. 研究定位

MsGalaxy 解决的是“高维、强约束、昂贵评估预算”条件下的布局优化难题：
- 设计变量维度高，几何/热/结构/电源等约束耦合紧密；
- 高保真仿真代价高，传统单一路径优化在可行域搜索上效率受限；
- 工程落地要求“可追溯、可解释、可复现实证”，而非黑箱坐标输出。

方法上采用 **Neuro-Symbolic** 范式：
- LLM 负责需求理解、建模意图组织与策略反射；
- pymoo MOEA 负责可执行数值搜索（`NSGA-II/III`、`MOEA/D`）；
- 物理层采用 proxy 快评估 + online COMSOL（热/结构/电学）+ DC 电源网络求解。

---

## 2. 当前能力快照（2026-03-07）

### 2.1 已实现（可执行）
- 双模式主链路：
  - `optimization.mode=agent_loop`
  - `optimization.mode=mass`（当前主线）
- 栈级配置已分离并启用 fail-fast 契约：
  - `config/bom/{mass,agent_loop}` + `config/system/{mass,agent_loop}`
  - 统一入口 `run/run_scenario.py --stack --level`
  - 强约束：`stack->mode`、BOM/base-config 路径绑定，跨栈混用直接报错
- `mass` A/B/C/D 闭环可执行：
  - A：建模意图生成
  - B：硬约束规范化为 `g(x) <= 0`
  - C：编译并执行 MOEA 搜索
  - D：诊断、反射与可选重试
- L1-L4 已按当前真实能力重构为统一档位：
  - level profile：`config/system/mass/level_profiles_l1_l4.yaml`
  - strict-real profile：`config/system/mass/level_profiles_l1_l4_real_strict.yaml`
  - BOM：`config/bom/mass/level_L1_foundation_stack.json` 至 `level_L4_full_stack_operator.json`
  - canonical 算子（10）：`group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`
  - canonical 物理域：`geometry/thermal/structural/power/mission(keepout)`
- v3 分阶段落地已到 M3 薄切片：
  - COMSOL 结构支路：`Solid + Stationary + Eigenfrequency`
  - COMSOL 电学支路：`ec + terminal/ground + std_power`
  - 电源网络方程回退：`simulation/power_network_solver.py`
  - 热-结构-电耦合 study 框架：`std_coupled`
  - `Operator Program DSL v3` 动作族已打通 `validator -> handler -> codec -> runner`
- strict gate 体系已成形：
  - `source gate`
  - `operator-family gate`
  - `operator-realization gate`
- Blender 可视化侧链 P0 已具备：可从 run 目录生成 `render_bundle.json`、Blender 场景脚本与 Codex brief。
- 旧 benchmark 入口、旧模板、旧批量测试链已删除；`benchmarks/` 历史目录已清空，后续再按新命名规则重建。

### 2.2 未实现（不可过度声明）
- M4（神经可行性预测、神经算子策略、多保真神经调度）尚未实现；
- mission/FOV/EMC 的仓内高保真联立求解尚未实现；
- 新版轻量 benchmark 框架尚未重建；
- 新版 `LLM intent vs deterministic` 对照尚未启动。

### 2.3 当前阶段推进目标（2026-03-07）
- 当前只做串行、小规模、真实 COMSOL 的 `NSGA-III` 主线验证；
- 逐级推进：`L1 -> L2 -> L3 -> L4`；
- 不做大规模矩阵，不做并行，不做对照，不做最终大消融；
- 先把新版模板与真实物理链逐级跑通，再谈 benchmark 与 A/B。

### 2.4 最新运行状态补充（2026-03-07）
- `L1` 已 strict-real 跑通：`experiments/0307/0141_l1_nsga3`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - strict gates 全通过，`dset` 错误计数为 `0`
- `L2` 已 strict-real 跑通：`experiments/0307/0209_l2_nsga3`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - strict gates 全通过，`dset` 错误计数为 `0`
- 当前暂停点：`L3`。

### 2.5 命名与收尾（2026-03-07）
- `benchmarks/` 历史产物已清空。
- `RULES.md` 已冻结新的短名规则：
  - benchmark 目录：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
  - experiments 目录：`experiments/<YYYYMMDD>/<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
  - helper 脚本：`bm_<scope>.py` / `tool_<topic>.py` / `audit_<topic>.py`
- 详细修复原因、调参背景、临时说明统一写入 `summary.json` / manifest，不再塞进目录名或脚本名。

---

## 3. 方法契约（Method Contract）

- 不直接让 LLM 输出最终坐标作为优化结果。
- 统一由 `ModelingIntent -> compile -> pymoo problem -> solve -> diagnose` 闭环执行。
- 硬约束必须可表达为 `g(x) <= 0`：
  - `metric <= target` -> `g = metric - target`
  - `metric >= target` -> `g = target - metric`
  - `metric == target` -> `g = |metric - target| - eps`
- strict-real 复核最少检查：
  - `source_gate_passed`
  - `operator_family_gate_passed`
  - `operator_realization_gate_passed`
  - `run_log.txt` 中 `Dataset "dset*" does not exist` 计数为 `0`

---

## 4. 代表性证据（Representative Evidence）

### 4.1 L1 strict-real 可行
- 证据：`experiments/0307/0141_l1_nsga3/summary.json`
- 关键结果：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=38.858394212138705`
  - strict gates 全通过，`dset` 错误计数为 `0`

### 4.2 L2 strict-real 可行
- 调参中间证据：`experiments/0307/0200_l2_nsga3/summary.json`
  - 只剩 `clearance` 主违例
- 收口后证据：`experiments/0307/0209_l2_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `best_cv_min=0.0`
  - `min_clearance=5.0`
  - `cg_offset=28.11641116193352`
  - `power_margin=61.4`
  - strict gates 全通过，`dset` 错误计数为 `0`

### 4.3 最新回归测试
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py tests/test_maas_core.py tests/test_api.py -q`
  - `140 passed`
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_core.py tests/test_api.py -q`
  - `66 passed`

---

## 5. 快速开始与复现

所有 Python/pytest 命令统一前缀：
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...
```

测试收集默认由 `pytest.ini` 限定到 `tests/` 目录，归档目录不会被自动收集。

### 5.1 环境安装
```bash
conda create -n msgalaxy python=3.12 -y
conda activate msgalaxy
pip install -r requirements.txt
```

可选（online COMSOL 路径）：
```bash
pip install mph
```

### 5.2 统一栈入口（推荐）
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_scenario.py --stack mass --level L1 --backend simplified --max-iterations 1 --deterministic-intent
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_scenario.py --stack agent_loop --level L1 --backend simplified --max-iterations 1
```

### 5.3 当前主线：串行 real COMSOL 单次运行
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L1.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L2.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L3.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/run_L4.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
```

### 5.4 strict-real 复核
```powershell
$run = 'experiments/0307/0209_l2_nsga3'
(Get-Content "$run/summary.json" -Raw | ConvertFrom-Json) | Select-Object status, diagnosis_status, best_cv_min, source_gate_passed, operator_family_gate_passed, operator_realization_gate_passed
(Select-String -Path "$run/run_log.txt" -Pattern 'Dataset "dset.*does not exist' -AllMatches | Measure-Object).Count
```

### 5.5 Mass RAG（CGRAG-Mass）
- 当前 `mass` 检索后端已切换为 `optimization/knowledge/mass/*`，不再使用旧通用 RAG 路径。
- 默认结构化证据库路径：`data/knowledge_base/mass_evidence.jsonl`。
- 可从历史运行产物导入 evidence：
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python -m optimization.knowledge.mass.ingest_from_runs --runs-root experiments --kb-path data/knowledge_base
```

---

## 6. 关键入口

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
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
- `simulation/comsol_driver.py`
- `simulation/power_network_solver.py`
- `run/render_blender_scene.py`

---

## 7. 产物结构

单次运行目录推荐短名：`experiments/<YYYYMMDD>/<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
- `summary.json`
- `report.md`
- `run_log.txt` / `run_log_debug.txt`
- `events/*.jsonl`
- `tables/*.csv`
- `trace/*.json`
- `snapshots/*.json`
- `visualizations/*`

说明：实验级日志统一写入对应 `experiments/<run>/` 目录；根路径 `logs/` 仅保留 `api_server` 等长期服务日志，不再复制实验运行日志。

`benchmarks/` 当前刻意保持为空；后续重建时统一使用：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
- 详细实验说明、修复原因、附加标签统一写入 `summary.json` / manifest
- 不再使用冗长目录名堆叠阶段说明

---

## 8. 文档导航

- `HANDOFF.md`：状态与实现边界（SSOT）
- `RULES.md`：执行、命名与证据治理规则
- `AGENTS.md`：代理协作规范
- `docs/reports/R23_blender_mcp_visualization_plan_20260306.md`：Blender MCP 可视化方案
- `docs/reports/R24_blender_mcp_setup_20260306.md`：Blender MCP / Codex / Blender Windows 接入说明
- `docs/adr/0006-blender-mcp-visualization-sidecar.md`：Blender 可视化侧链 ADR
