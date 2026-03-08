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
- 三态运行身份已明确：
  - `optimization.mode=agent_loop`（deprecated / legacy compatibility）
  - `optimization.mode=mass`（stable，当前数值执行主线）
  - `optimization.mode=vop_maas`（experimental，controller 视角顶层 mode，执行委托给 `mass`）
- `vop_maas` experimental 主链路已可执行：
  - `optimization.mode=vop_maas`
  - 先构建 `VOPG`，再生成/验证/筛选 `VOPPolicyPack`
  - 最终仍委托 `mass` 执行 pymoo + multiphysics 搜索
  - LLM 只生成 verified operator-policy，不直接输出最终布局坐标
  - real LLM primary round 现可稳健消费直接 JSON、fenced JSON 与 DashScope list-block content，并对缺省 `operator_candidates / policy_source / candidate_id / program_id` 做 bounded autofill（会显式标记 `llm_api_autofill`）
  - 已新增单轮、受限、可审计的 reflective replanning 薄切片：首轮 policy 明确失败/停滞时，可基于 `previous_policy_pack + vop_policy_feedback + updated VOPG` 再生成一次 policy，并再次委托 `mass`
  - `vop_policy_feedback` 现额外保留 `failure_signature / fidelity_escalation_allowed / fidelity_escalation_reason`，作为 reflective second-pass 的边界输入
  - reflective round 已新增 feedback-aware fidelity_plan：仅在真实 `comsol` backend 且命中特定 failure signature 时，才会有界升级 `thermal_evaluator_mode / online_comsol_eval_budget / physics_audit_top_k`，并把“不升级”原因写出
- `vop_maas -> mass` 委托执行现做 per-run `optimization` 快照/恢复，避免 policy 配置污染后续 round
- `PolicyPack -> mass` 的 intent patch 现补齐 `first_modal_freq / safety_factor / power_margin` 等 metric 级 objective 注入，不再只覆盖 `max_stress / voltage_drop`
- LLM 接入正在按 docs-first 方案重构：正式决策见 `docs/adr/0009-llm-openai-compatible-gateway.md`，研究与迁移蓝图见 `docs/reports/R34_llm_unified_gateway_research_20260308.md`、`docs/reports/R35_llm_gateway_migration_plan_20260308.md`
- 当前内部统一标准锁定为 `OpenAI-compatible chat.completions + embeddings`；默认供应商仍为 `Qwen Max`，默认通过 `DashScope` 的 OpenAI-compatible 层访问，`DashScope native` 仅作为显式受控 fallback
- 当前 `config/system/{mass,agent_loop,vop_maas}/base.yaml` 已内置示例 text profiles：`qwen_max_default`、`openai_gpt_default`、`openai_gpt_relay_default`、`claude_compat_default`、`glm_compat_default`、`minimax_compat_default`；可通过统一入口 `run/run_scenario.py --llm-profile <profile>` 或各 stack runner 的同名参数切换，而无需改业务代码
- 若使用只提供中转地址/API Key 的 GPT 平台，优先使用 `openai_gpt_relay_default`：当前配置按 `OpenAI-compatible responses` 接入，固定 `model=gpt-5.4`、`base_url=https://cdn-gmn.chuangzuoli.com/v1/responses`，并优先读取 `OPENAI_RELAY_API_KEY`
- 统一网关现支持跨 provider 的 reasoning 配置抽象：`reasoning_profile / thinking_mode / completion_budget_tokens / reasoning_budget_tokens`；默认 `qwen_max_default` 已开启高预算高思考模式，`Qwen(DashScope)` 走 `enable_thinking + thinking_budget` 映射，`OpenAI` 走 `reasoning_effort + max_completion_tokens` 映射
  - reflective / feedback-aware attribution 现会稳定写入 `summary.json`，并同步保留到 `events/run_manifest.json`、`tables/policy_tuning.csv`、`tables/phases.csv`、`tables/vop_rounds.csv`
  - `tables/vop_rounds.csv` 是 round-level 审计主表；`summary.json` / manifest / `report.md` / visualization summary 会与该表对齐口径
  - `summary.json` 还会保留轻量 `vop_round_audit_digest` 与 `vop_round_audit_table`，可与三张表通过 `vop_round_key` 做 round 级联查
  - `policy_tuning.csv`、`phases.csv`、`vop_rounds.csv` 的公共 join-key 现固定前置，便于脚本/可视化直接按 round 联查
  - release-grade audit 字段 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 现已统一写入 `summary.json`、`events/run_manifest.json`、`report.md`、visualization summary 与 `tables/release_audit.csv`
  - 结果系统已进入 `Mode Scoped Experiment Observability v2`：`summary.json / events/run_manifest.json` 固定写出 `run_mode / execution_mode / lifecycle_state / artifact_layout_version / artifact_index_path`
  - raw artifacts 不再默认根目录混放，而是迁入 `artifacts/<mode>/...`
  - `vop_maas` 顶层 controller 证据进入 `artifacts/vop_maas/*`，delegated `mass` 证据进入 `artifacts/vop_maas/delegated_mass/*`
  - `events/artifact_index.json` 成为 raw artifact canonical 索引，reader/tool 优先读 index，再 fallback 旧路径
  - `tests/test_vop_maas_mode.py` 现新增 `L1-L4` targeted regression，覆盖 real `MetaReasoner -> VOP service -> mass` 主链与 feedback-aware second-pass
- 栈级配置已分离并启用 fail-fast 契约：
  - `config/bom/{mass,agent_loop}` + `config/system/{mass,agent_loop,vop_maas}`
  - 统一入口 `run/run_scenario.py --stack --level`
  - 强约束：`stack->mode`、BOM/base-config 路径绑定，跨栈混用直接报错
- `mass` A/B/C/D 闭环可执行：
  - A：建模意图生成
  - B：硬约束规范化为 `g(x) <= 0`
  - C：编译并执行 MOEA 搜索
  - D：诊断、反射与可选重试
- 几何初始化与指标治理已进入主线：
  - `geometry/layout_seed_service.py` 将迁移自 `layout3d` 的布局能力收口为 `mass` 坐标搜索的 layout seed service
  - `geometry/metrics.py` 统一 `min_clearance / num_collisions / boundary_violation / packing_efficiency`
  - 初始种群现可注入 layout-derived seeds，`packing_efficiency` 采用真实体积分数计算
  - `summary.json`、`events/attempt_events.jsonl`、`tables/attempts.csv` 现保留 `seed_population_report` / `layout_seed_*` 归因字段
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
- Blender 可视化当前批准的下一阶段目标是 **Blender Review Package**：以 Blender 作为主 3D 工程审阅入口，并配套离线 companion dashboard 进行约束/审计联查；该方向当前仍是 planned target，不属于已实现能力。
- 旧 benchmark 入口、旧模板、旧批量测试链已删除；`benchmarks/` 历史目录已清空，后续再按新命名规则重建。

### 2.2 未实现（不可过度声明）
- M4（神经可行性预测、神经算子策略、多保真神经调度）尚未实现；
- mission/FOV/EMC 的仓内高保真联立求解尚未实现；
- 新版轻量 benchmark 框架尚未重建；
- 新版 `LLM intent vs deterministic` 对照尚未启动。
- `vop_maas` 的多轮 reflective replanning、policy memory、template evolution 仍未实现；当前是 `M1-M4` 第一可运行切片 + `M5-min` 单轮 reflective replanning。
- Blender Review Package 尚未实现；当前仓内仍只有 Blender sidecar P0，而非标准化 review package。

### 2.3 当前阶段推进目标（2026-03-07）
- 当前串行、小规模、真实 COMSOL 的 `NSGA-III` strict-real 主线已完成 `L1 -> L4`；
- 当前优先做 release-grade audit 与下游消费收口：统一 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible`，并优先让脚本/可视化读取 `tables/vop_rounds.csv` 与 `tables/release_audit.csv`；
- 当前 `M0` 研究包已冻结为仅保留已跑通的 `L1-L4 + NSGA-III` 执行口径；
- 仍不做大规模矩阵，不做并行，不做最终大消融；
- 在 benchmark 重建完成前，不启动新版 `LLM intent vs deterministic` 对照。

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
- `L3` 已 strict-real 跑通：`experiments/0307/1646_l3_nsga3`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `mission_keepout_violation=0.0`
  - strict gates 全通过，`dset` 错误计数为 `0`
- `L4` 已 strict-real 跑通：`experiments/0307/1708_l4_nsga3`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `mission_keepout_violation=0.0`
  - strict gates 全通过，`dset` 错误计数为 `0`
- 当前 `L1-L4` strict-real 串行主线已闭环；`final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 已稳定写出到 `summary.json / run_manifest / report / visualization / tables/release_audit.csv`。但只有在 `final_audit_status=release_grade_real_comsol_validated` 且 `final_mph_path`、strict gates 同时满足时，才可对外表述为 release-grade real COMSOL evidence。
- 已生成整批 rollup：`experiments/0307/release_audit_summary.csv` 与 `experiments/0307/release_audit_summary.md`
  - `release_grade_real_comsol_validated=7`
  - `diagnostic_only_no_feasible_final_state=6`
  - `diagnostic_only_non_comsol_backend=1`

### 2.4 Mode Scoped Experiment Observability v2
- 顶层 run identity 字段固定为：
  - `run_mode`
  - `execution_mode`
  - `lifecycle_state`
  - `artifact_layout_version=2`
- 顶层统一消费面保持不变：
  - `summary.json`
  - `report.md`
  - `events/`
  - `tables/`
  - `visualizations/`
  - `mph_models/`
- raw artifacts 统一进入 mode-scoped 命名空间：
  - `artifacts/mass/...`
  - `artifacts/vop_maas/...`
  - `artifacts/vop_maas/delegated_mass/...`
  - `artifacts/agent_loop/...`
- 新 run 命名协议已升级到 v2.1：
  - `mass -> experiments/<YYYYMMDD>/<HHMM>_mass_<short-tag>`
  - `vop_maas -> experiments/<YYYYMMDD>/<HHMM>_vop_<short-tag>`
  - `agent_loop -> experiments/<YYYYMMDD>/<HHMM>_agent_<short-tag>`
  - 历史 run 不做目录改名，reader 同时兼容旧 `<HHMM>_<level>_<algo>` 与新 `<HHMM>_<mode-token>_<level>_<algo>`
- 新 run 默认只保留 `run_log.txt`；`run_log_debug.txt` 仅作为 legacy run 的 reader fallback
- `vop_maas` 的主消费面固定为 `summary.json + report.md + tables/vop_rounds.csv`，并由 `vop_decision_summary` / `vop_delegated_effect_summary` 驱动 controller-first 叙事
- 新 `vop_maas` run 会在收尾阶段追加：
  - `llm_final_summary_zh.md`
  - `events/llm_final_summary_digest.json`
  - `events/runtime_feature_fingerprint.json`
- 其中 `runtime_feature_fingerprint.json` 用来把 `R25` 未覆盖完整的 runtime feature lines（如 `run_mode vs execution_mode`、intent 来源、搜索空间、physics path、`MCTS/meta_policy/physics_audit`、strict gates、VOP controller override）收口成可复用事实层；`llm_final_summary_zh.md` 则在此基础上输出完整任务说明 + 表格化中文复盘
- 以上三者现已继续接入：
  - `visualizations/visualization_summary.txt` 的 `vop_maas` controller-first 摘要
  - Blender `render_bundle.json / review_payload.json / render_manifest.json / render_brief.md`
  - `run/mass/audit_release_summary.py` 生成的 Markdown `## Observability Links`
- 模板版中文总结始终保底，二次 LLM 中文叙事失败也不影响主流程，`report.md` 只内嵌摘要入口
- `agent_loop` 仅保留 deprecated / legacy 兼容层，不再作为默认 observability fallback。
- 正式决策与执行蓝图见：
  - `docs/adr/0008-mode-scoped-experiment-observability-v2.md`
  - `docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md`
  - `docs/reports/R38_runtime_feature_fingerprint_summary_upgrade_20260308.md`

### 2.5 命名与收尾（2026-03-07）
- `benchmarks/` 历史产物已清空。
- `RULES.md` 已冻结新的短名规则：
  - benchmark 目录：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
  - experiments 目录：`experiments/<YYYYMMDD>/<HHMM>_<mode-token>_<short-tag>`
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

### 4.3 L3 strict-real 可行
- 证据：`experiments/0307/1646_l3_nsga3/summary.json`
- 关键结果：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `cg_offset=27.026735756212528`
  - `power_margin=46.6`
  - strict gates 全通过，`dset` 错误计数为 `0`

### 4.4 L4 strict-real 可行
- 证据：`experiments/0307/1708_l4_nsga3/summary.json`
- 关键结果：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `cg_offset=24.424141828548695`
  - `power_margin=36.4`
  - strict gates 全通过，`dset` 错误计数为 `0`

### 4.5 最新回归测试
- `pytest tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py tests/test_maas_core.py tests/test_api.py -q`
  - `140 passed`
- `pytest tests/test_maas_core.py tests/test_api.py -q`
  - `66 passed`
- `pytest tests/test_vop_maas_mode.py tests/test_maas_core.py -q`
  - `59 passed`

---

## 5. 快速开始与复现

所有 Python/pytest 命令默认在本仓库的 VS Code 集成终端中执行：
```bash
python ...
pytest ...
```
工作区 `.vscode/settings.json` 已自动约束：
- 默认终端进入 `msgalaxy` conda 环境
- `PYTHONIOENCODING=utf-8`
- `PYTHONUTF8=1`

只有在离开 VS Code 工作区终端、转到 CI / Codex / 外部 shell 时，才回退为显式：
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
python run/run_scenario.py --stack mass --level L1 --backend simplified --max-iterations 1 --deterministic-intent
python run/run_scenario.py --stack agent_loop --level L1 --backend simplified --max-iterations 1
python run/run_scenario.py --stack vop_maas --level L1 --backend simplified --max-iterations 1
```

### 5.3 `vop_maas` experimental 验线
```bash
python run/vop_maas/run_L1.py --backend simplified --mock-policy --deterministic-intent --max-iterations 1
pytest tests/test_vop_maas_mode.py -q
```
- 当前建议先验证 `mock_policy`、schema validator、screening、fallback 与 metadata。
- 若需要 real-LLM primary round，`qwen_max_default` 会优先读取 `DASHSCOPE_API_KEY`，其次回退到 `OPENAI_API_KEY`；`openai_gpt_relay_default` 则优先读取 `OPENAI_RELAY_API_KEY`，其次回退到 `OPENAI_API_KEY`。
- `vop_maas` 仍复用 `config/bom/mass/*`，但 base config 独立走 `config/system/vop_maas/base.yaml`。

### 5.4 当前主线：串行 real COMSOL 单次运行
```bash
python run/mass/run_L1.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L2.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L3.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L4.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
```

### 5.5 strict-real 复核
```powershell
$run = 'experiments/0307/1708_l4_nsga3'
(Get-Content "$run/summary.json" -Raw | ConvertFrom-Json) | Select-Object status, diagnosis_status, diagnosis_reason, best_cv_min, source_gate_passed, operator_family_gate_passed, operator_realization_gate_passed
(Select-String -Path "$run/run_log.txt" -Pattern 'Dataset "dset.*does not exist' -AllMatches | Measure-Object).Count
```

### 5.6 历史产物补重建与审计摘要
```bash
python run/mass/tool_rebuild_run_artifacts.py experiments/0307/0141_l1_nsga3 experiments/0307/0209_l2_nsga3 experiments/0307/1646_l3_nsga3 experiments/0307/1708_l4_nsga3
python run/mass/tool_rebuild_run_artifacts.py --runs-root experiments/0307 --glob '*_l*_nsga3'
python run/mass/audit_release_summary.py --runs-root experiments/0307 --glob '*_l*_nsga3' --output-csv experiments/0307/release_audit_summary.csv --output-md experiments/0307/release_audit_summary.md
```
- `tool_rebuild_run_artifacts.py` 用于把历史 run 补齐到新的 release-grade audit 口径。
- `audit_release_summary.py` 优先读取 `tables/release_audit.csv` 与 `tables/vop_rounds.csv`，快速汇总 run 级与 round 级审计字段，并输出 level/gap rollup。
- 非 release-grade run 会额外给出 `gap_category / primary_failure_signature / minimal_remediation / evidence_hint`，并支持 `--only-non-release` 聚焦排查。

### 5.7 Mass RAG（CGRAG-Mass）
- 当前 `mass` 检索后端已切换为 `optimization/knowledge/mass/*`，不再使用旧通用 RAG 路径。
- 默认结构化证据库路径：`data/knowledge_base/mass_evidence.jsonl`。
- 可从历史运行产物导入 evidence：
```bash
python -m optimization.knowledge.mass.ingest_from_runs --runs-root experiments --kb-path data/knowledge_base
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
- `run/vop_maas/run_L1.py`
- `run/vop_maas/run_L2.py`
- `run/vop_maas/run_L3.py`
- `run/vop_maas/run_L4.py`
- `config/system/mass/base.yaml`
- `config/system/mass/level_profiles_l1_l4.yaml`
- `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
- `config/system/vop_maas/base.yaml`
- `config/bom/mass/level_L1_foundation_stack.json`
- `config/bom/mass/level_L2_thermal_power_stack.json`
- `config/bom/mass/level_L3_structural_mission_stack.json`
- `config/bom/mass/level_L4_full_stack_operator.json`
- `workflow/modes/mass/pipeline_service.py`
- `workflow/modes/vop_maas/policy_program_service.py`
- `workflow/modes/vop_maas/contracts.py`
- `workflow/modes/vop_maas/policy_context.py`
- `workflow/modes/vop_maas/policy_compiler.py`
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
- `simulation/comsol_driver.py`
- `simulation/power_network_solver.py`
- `run/render_blender_scene.py`

---

## 7. 产物结构

单次运行目录推荐短名：`experiments/<YYYYMMDD>/<HHMM>_<mode-token>_<short-tag>`
- `summary.json`
- `report.md`
- `run_log.txt`（legacy run 读取时允许 fallback `run_log_debug.txt`）
- `events/*.jsonl`
- `tables/*.csv`
- `trace/*.json`
- `snapshots/*.json`
- `visualizations/*`

mode-scoped 中文总结补充：
- `mass`：`mass_final_summary_zh.md` + `events/mass_final_summary_digest.json`
- `vop_maas`：`llm_final_summary_zh.md` + `events/llm_final_summary_digest.json`
- `report.md` 只保留中文总结入口块，不镜像全文

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
- `docs/reports/R28_vop_maas_master_plan_20260307.md`：`VOP-MaaS` 研究与开发总方案、分阶段路线与当前实现映射
- `docs/reports/R29_vop_maas_m0_execution_package_20260307.md`：`M0` 单算法可执行研究包冻结说明（当前只保留 `L1-L4 + NSGA-III`）
- `docs/reports/R32_blender_review_package_plan_20260308.md`：Blender Review Package 工程可视化升级总方案
- `docs/reports/R38_runtime_feature_fingerprint_summary_upgrade_20260308.md`：`vop_maas` 中文总结升级与 runtime feature fingerprint 补充说明，用于补足 `R25` 对新 runtime feature lines 的遗漏
- `docs/adr/0006-blender-mcp-visualization-sidecar.md`：Blender 可视化侧链 ADR
- `docs/adr/0007-vop-maas-verified-operator-policy-experimental-mode.md`：`vop_maas` experimental mode 与研究口径 ADR
- `docs/adr/0008-blender-review-package-engineering-visualization.md`：Blender Review Package 工程审阅可视化 ADR
