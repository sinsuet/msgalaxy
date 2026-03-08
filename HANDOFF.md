# MsGalaxy HANDOFF

**Role**: Single Source of Truth (SSOT)  
**Last Updated**: 2026-03-09 00:45 +08:00 (Asia/Shanghai)
**State Tag**: `mp-op-maas-v3-transition`  
**Current Focus**: 继续沿 `vop_maas` 主链收口 real-LLM primary round、`PolicyPack -> mass` 注入、feedback-aware second-pass 与 `L1-L4` targeted regression；`Mode Scoped Experiment Observability v2` 本轮已进一步补完 `run naming + single run log + VOP controller-first summary/report/visualization + runtime fingerprint -> bundle/brief/release-audit`，下一步转向 `mass` run 的传统优化过程中文总结。

---

## 1. 当前真实状态（Implemented vs Planned）

### 1.1 已实现（可执行）
- 三条优化模式已接入运行时路由：
  - `optimization.mode=agent_loop`
  - `optimization.mode=mass`
  - `optimization.mode=vop_maas`（experimental，verified operator-policy mode，执行委托给 `mass`）
- `mass` 为 A/B/C/D 闭环：
  - A `ModelingIntent` 生成/构建
  - B 硬约束规范化为 `g(x) <= 0`
  - C 编译为 pymoo 问题并执行（`nsga2/nsga3/moead`）
  - D 诊断、反射、可选放松与重试
- `vop_maas` 已从 reserved scaffold 升级为可运行 experimental mode：
  - 先构建 `VOPG`（Violation-Operator Provenance Graph）结构化上下文
  - 再生成/验证/筛选 `VOPPolicyPack`
  - 仅以 `policy_priors` 形式注入 `mass` 内核，不直接输出最终布局坐标
  - 当前已实现 `mock_policy`、schema validator、counterfactual-style screening、stable fallback-to-`mass`
  - `MetaReasoner.generate_policy_program(...)` 现可稳健消费 real-LLM 直接 JSON、fenced JSON 与 DashScope list-block message content，并对缺省 `operator_candidates / policy_source / candidate_id / program_id` 做 bounded autofill；修补后会显式标记 `policy_source=llm_api_autofill`
  - 已新增 **单轮、受限、可审计** 的 reflective replanning 薄切片：仅在首轮 policy 已应用且结果明确失败/停滞时触发一次 `policy_feedback -> regenerated policy -> delegated mass rerun`
  - `vop_policy_feedback` 会稳定汇总 `first_feasible_eval / comsol_calls_to_first_feasible / fallback attribution / effective fidelity`，并新增 `failure_signature / fidelity_escalation_allowed / fidelity_escalation_reason`
  - reflective round 已新增 feedback-aware fidelity_plan：仅在真实 `comsol` backend 且命中特定 failure signature 时，才会有界地升级 `thermal_evaluator_mode / online_comsol_eval_budget / physics_audit_top_k`，并把“不升级”原因稳定写出
  - `vop_maas -> mass` 委托执行现对 `optimization` 配置做 per-run snapshot/restore，避免首轮 policy 污染后续 reflective round
  - `PolicyPack -> mass` intent patch 现补齐 `structural/power` focus 的 metric 级注入覆盖：除 `max_stress / voltage_drop` 外，还会把 `first_modal_freq / safety_factor / power_margin` 按 objective 形式注入
- reflective round / feedback-aware fidelity attribution 现会稳定写入 `summary.json`，并在 `events/run_manifest.json`、`tables/policy_tuning.csv`、`tables/phases.csv`、`tables/vop_rounds.csv` 保留可审计字段（如 `vop_policy_primary_round_index`、`vop_policy_primary_round_key`、`vop_reflective_replanning.*`、`vop_feedback_aware_fidelity_*`）
- `tables/vop_rounds.csv` 已成为 round-level 审计主落点，稳定字段至少覆盖 `round_index / vop_round_key / trigger_reason / feedback_aware_fidelity_plan / feedback_aware_fidelity_reason / previous_policy_id / candidate_policy_id / final_policy_id / mass_rerun_executed / skipped_reason`
- `summary.json` 现额外保留轻量 `vop_round_audit_digest` 与 `vop_round_audit_table`，用于和 `policy_tuning.csv` / `phases.csv` 基于 `vop_round_key` 做 round-level join
- `policy_tuning.csv` / `phases.csv` / `vop_rounds.csv` 现共享固定前置 join-key（`run_id,timestamp,iteration,attempt,vop_round_key,round_index,policy_id,previous_policy_id`），便于轻量联查与下游读取
- `vop_rounds.csv` 已补 controller-level round 审计字段：`decision_rationale / change_summary / expected_effects / observed_effects / effectiveness_summary`，并可回填 `summary/report/visualization`
- `summary.json` / manifest extra 现固定补齐 `vop_decision_summary` 与 `vop_delegated_effect_summary`，使 `vop_maas` 的主消费面从“mass 结果附带 VOP”升级为 “VOP controller-first + delegated mass summary”
- release-grade audit 字段 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 已统一写入 `summary.json`、`events/run_manifest.json`、`report.md`、visualization summary 与 `tables/release_audit.csv`
- 结果系统现开始切入 `Mode Scoped Experiment Observability v2`：`summary.json / run_manifest.json` 追加 `execution_mode / lifecycle_state / artifact_layout_version / artifact_index_path`；raw artifacts 不再以根目录混放为主，而是迁入 `artifacts/<mode>/...`
- `vop_maas` run 目录现显式保持 `run_mode=vop_maas`，委托 `mass` 的原始执行证据进入 `artifacts/vop_maas/delegated_mass/*`；历史 reader 仍保留 fallback
- 新 run 短名现固定带 mode token：`mass -> <HHMM>_mass_<short-tag>`、`vop_maas -> <HHMM>_vop_<short-tag>`、`agent_loop -> <HHMM>_agent_<short-tag>`；历史 run 不改名
- 新 run 现只保留单日志 `run_log.txt`；`run_log_debug.txt` 仅作为 legacy reader fallback，不再为新 run 生成
- `run_log.txt` 对 `vop_maas` 现输出 `[VOP][BOOTSTRAP] / [VOP][DECISION] / [VOP][DELEGATE] / [VOP][EFFECT] / [VOP][REPLAN]` 里程碑，原始 prompt/response dump 继续留在 mode-scoped `llm_interactions`
- 新 `vop_maas` run 在完成后会额外生成 `llm_final_summary_zh.md`、`events/llm_final_summary_digest.json` 与 `events/runtime_feature_fingerprint.json`：先落可审计模板摘要，再在 LLM 可用时追加中文叙事版；`report.md` 仅内嵌简短入口摘要，不镜像全文
- `runtime_feature_fingerprint.json` 用来把 `R25` 未完整覆盖的 runtime feature lines（`run_mode vs execution_mode`、intent 来源、搜索空间 / genome、proxy vs online COMSOL、`MCTS/meta_policy/physics_audit`、strict gates、VOP controller override）收口成 canonical runtime snapshot；`llm_final_summary_zh.md` 则在此基础上输出完整任务说明 + 表格化中文技术复盘
- `visualization_summary.txt` 的 `vop_maas` 主视图现已追加 `Runtime Feature Fingerprint` 摘要块，不再只显示 round/audit 文本而缺失 baseline/gate/controller overlay
- Blender review sidecar 现已把 `runtime_feature_fingerprint_path`、`llm_final_summary_zh_path`、`llm_final_summary_digest_path` 暴露到 `render_bundle.json`、`review_payload.json`、`render_manifest.json` 与 `render_brief.md`
- `run/mass/audit_release_summary.py` 生成的 Markdown 现新增 `## Observability Links`，可直接联到 `runtime_feature_fingerprint.json`、`llm_final_summary_zh.md`、`tables/vop_rounds.csv` 与 `tables/release_audit.csv`
- `core/final_summary_zh.py` 中早期重复定义的 legacy render/digest 实现已物理删除，当前只保留单一正式入口，避免后续维护时出现“修了新逻辑但旧函数仍残留”的混淆
- LLM 接入治理已进入统一网关迁移阶段：新增 `docs/adr/0009-llm-openai-compatible-gateway.md`、`docs/reports/R34_llm_unified_gateway_research_20260308.md`、`docs/reports/R35_llm_gateway_migration_plan_20260308.md`，并已开始把 `MetaReasoner`、`agent_loop` agents、runner profile 覆盖迁移到 `OpenAI-compatible chat.completions + embeddings` 主层
- 当前默认供应商仍为 `Qwen Max`，默认访问路径改为 `DashScope OpenAI-compatible`；`DashScope native Generation` 仅保留为显式受控 fallback，不可对鉴权/模型名/base_url 错误做静默回退
- 三套 base config 已补示例 text profiles：`qwen_max_default`、`openai_gpt_default`、`claude_compat_default`、`glm_compat_default`、`minimax_compat_default`；runner 与统一入口现支持 `--llm-profile` 覆盖 `openai.default_text_profile`
- 统一网关现新增 **provider-agnostic reasoning knobs**：`reasoning_profile / thinking_mode / completion_budget_tokens / reasoning_budget_tokens`；`qwen_max_default` 默认配置已切到高预算高思考模式，`OpenAICompatibleAdapter` 会对 `Qwen(DashScope)` 映射 `extra_body.enable_thinking / thinking_budget`，对 `OpenAI` 映射 `reasoning_effort / max_completion_tokens`
  - `Mode Scoped Experiment Observability v2` 的正式决策与执行蓝图现以 `docs/adr/0008-mode-scoped-experiment-observability-v2.md` 和 `docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md` 为准；后续阶段只追加 checkpoint report
  - 新增 `L1-L4` targeted regression：以真实 `MetaReasoner -> PolicyProgrammer -> VOPPolicyProgramService` 链路覆盖 `primary round -> policy_priors injection -> feedback-aware second-pass -> delegated mass rerun`
  - 当前实现边界是 `M1-M4` 第一可运行切片 + `M5-min` 单轮 reflective replanning，不包含 policy memory / template evolution / neural guidance
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
- geometry 种子与指标治理已补第一阶段：
  - `geometry/layout_seed_service.py` 已将迁移自 `layout3d` 的布局能力收口为 seed service，并接入 `mass` 坐标搜索初始种群
  - `geometry/metrics.py` 已统一 `min_clearance / num_collisions / boundary_violation / packing_efficiency`
  - `workflow/orchestrator.py` 中 `packing_efficiency` 已改为真实体积分数口径，不再使用固定占位值
  - MaaS `attempt payload / summary.json / attempts.csv` 已写出 `seed_population_report`，可追踪 `layout_seed` 生成数量、保留数量与来源分布
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
  - `run/vop_maas/run_L1.py` ~ `run/vop_maas/run_L4.py`（experimental，默认仍建议先用 `simplified` / `mock_policy` 验线）
- 旧批量 benchmark 入口、旧模板、旧测试链已删除；`benchmarks/` 已在 2026-03-07 清空，后续若重建必须遵循 `RULES.md` 的短名规则。
- Blender 可视化侧链 P0 已落地：可从 run 目录生成 `render_bundle.json`、Blender 场景脚本、Codex brief，并可选 direct Blender render。
- Blender 可视化下一批准方向已冻结为 **Blender Review Package**（planned target，尚未实现）：保持 Blender 作为主 3D 审阅面，新增离线 review dashboard 作为伴随分析面；默认入口仍为 `run/render_blender_scene.py`，默认 profile 为 `engineering`，默认 state set 为 `initial/best/final`。

### 1.2 未实现/仅规划（不可过度声明）
- M4（神经可行性预测、神经算子策略、多保真神经调度）尚未开始实现。
- mission/FOV/EMC 高保真路径仍依赖外部 evaluator；当前仓内默认执行的是 keepout 代理接口。
- L1-L4 新版轻量 benchmark 框架目前不存在；后续需要基于新模板与新命名规则重建。
- 当前还没有新的 `LLM intent vs deterministic` 对照结论；这一阶段尚未开始。
- `LLMGateway` 迁移仍在推进中：`MetaReasoner`、`agent_loop` agents 与 runner/profile 归一已开始落地，但尚未宣称所有历史调用点全部完成切换
- `vop_maas` 的 **多轮** reflective replanning、policy memory、template evolution 仍未实现；当前只能按 `M5-min` 单轮薄切片对外表述，不能上升为完整 `M5/M6`。
- Blender Review Package 仍处于规划态：当前仓内 **尚未** 具备三态 Blender scene、`review_payload.json`、离线 `review_dashboard.html`、升级版 `render_manifest.json v2`，不得将其描述为已落地能力。

---

## 2. 架构基线（当前项目口径）

### 2.1 主体架构
- LLM 层：需求理解、约束/目标编排、反射建议、策略更新。
- pymoo 层：多目标搜索核心（`NSGA-II` / `NSGA-III` / `MOEA/D`）。
- Physics 层：proxy 快评估 + online COMSOL + 电源网络方程回退。
- 当前恢复工作主线聚焦在 `mass`：保持 MOEA 为数值优化核心，不以 LLM 直接输出最终坐标替代搜索。
- `vop_maas` 的当前定位是 `mass` 之上的 verified operator-policy controller：LLM 只控制策略层/算子层，不替代数值优化器。

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
- `experiments/0307/1646_l3_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `cg_offset=27.026735756212528`
  - `power_margin=46.6`
  - `final_mph_path` 已写出
  - `source/operator-family/operator-realization` 三类 gate 全通过
  - `run_log.txt` 中 `dset` 错误计数为 `0`
- `experiments/0307/1708_l4_nsga3/summary.json`
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `cg_offset=24.424141828548695`
  - `power_margin=36.4`
  - `final_mph_path` 已写出
  - `source/operator-family/operator-realization` 三类 gate 全通过
  - `run_log.txt` 中 `dset` 错误计数为 `0`
- 结论：新版 `L1-L4` 模板与 `real-strict` profile 已完成单次串行主线验证；release-grade audit 字段已统一写出，下一阶段优先转向历史产物补重建与 targeted strict-real 回归，benchmark 继续后置。
- `experiments/0307/release_audit_summary.csv` 与 `experiments/0307/release_audit_summary.md` 已生成：
  - `release_grade_real_comsol_validated=7`
  - `diagnostic_only_no_feasible_final_state=6`
  - `diagnostic_only_non_comsol_backend=1`
  - `audit_release_summary.py` 现会优先读取 `tables/release_audit.csv` / `tables/vop_rounds.csv`，并对非 release-grade run 输出 `gap_category / primary_failure_signature / minimal_remediation / evidence_hint`

### 3.3 回归测试
- 已通过：
  - `pytest tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py tests/test_maas_core.py tests/test_api.py -q`
  - 结果：`140 passed`
- 已通过：
  - `pytest tests/test_maas_core.py tests/test_api.py -q`
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

## 4. v3 分阶段状态（M0-M5-min）

- M0：已进一步冻结为单算法可执行研究包；当前只保留已跑通的 `L1-L4 + NSGA-III` 主线，详见 `docs/reports/R29_vop_maas_m0_execution_package_20260307.md`。
- M1：hard-constraint coverage + metric registry 闸门已落地。
- M2：结构/电源 proxy 与真实路径已进入可执行链。
- M3：`Operator Program DSL v3` 已形成可执行薄切片，并已在 strict-real 路径中通过 gate 约束验证。
- M4：未实现，保持规划态。
- M5-min：`vop_maas` 已补单轮 reflective replanning 薄切片；当前支持基于 `previous_policy_pack + vop_policy_feedback + updated VOPG` 的一次再规划，并带 feedback-aware fidelity_plan 推荐/并入，再次委托 `mass` 执行，不包含 memory / template evolution。

---

## 5. 当前已知问题与风险

- release-grade audit 字段虽已统一写出，但历史 `experiments/` 产物若早于本次收口，仍需重新生成 `report.md` / visualization summary / `tables/release_audit.csv` 后，才能按同一口径复核。
- 新 benchmark 框架尚未重建；当前仓库不提供可直接复用的批量对照入口。
- `LLM intent` 相对 deterministic 的统计收益尚无新版证据。
- mission 高保真路径仍依赖外部 evaluator；若要求 real-only 且 evaluator 不可用，会被 strict gate 阻断。
- 运行目录命名已支持 `compact`，但历史 `experiments/` 目录仍混有旧格式产物；后续可再统一收口。

---

## 6. 运行建议（当前推荐）

### 6.1 VS Code 工作区终端（强制）
```bash
python ...
pytest ...
```
工作区 `.vscode/settings.json` 已约束默认终端进入 `msgalaxy` conda 环境，并注入 `PYTHONIOENCODING=utf-8` 与 `PYTHONUTF8=1`。
只有在 Codex / CI / 外部 shell 中，才使用显式 fallback：
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...
```

### 6.2 当前推荐：串行 real COMSOL 单次运行
```bash
python run/mass/run_L1.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L2.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L3.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L4.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
```

### 6.3 `vop_maas` experimental 验线命令
```bash
python run/run_scenario.py --stack vop_maas --level L1 --backend simplified --max-iterations 1
python run/vop_maas/run_L1.py --backend simplified --mock-policy --deterministic-intent --max-iterations 1
pytest tests/test_vop_maas_mode.py -q
```
- 当前推荐先用 `simplified + mock_policy` 验证路由、schema、screening、fallback 与 metadata。
- 若需要 real-LLM primary round，`run/vop_maas/common.py` 现会优先读取 `DASHSCOPE_API_KEY`，其次回退到 `OPENAI_API_KEY`。
- `vop_maas` 真实执行仍委托给 `mass`；无可用 policy 时应明确回退到纯 `mass`。

### 6.4 strict-real 复核命令
```powershell
$run = 'experiments/0307/1708_l4_nsga3'
(Get-Content "$run/summary.json" -Raw | ConvertFrom-Json) | Select-Object status, diagnosis_status, diagnosis_reason, best_cv_min, source_gate_passed, operator_family_gate_passed, operator_realization_gate_passed
(Select-String -Path "$run/run_log.txt" -Pattern 'Dataset "dset.*does not exist' -AllMatches | Measure-Object).Count
```

### 6.5 历史产物补重建与审计摘要
```bash
python run/mass/tool_rebuild_run_artifacts.py experiments/0307/0141_l1_nsga3 experiments/0307/0209_l2_nsga3 experiments/0307/1646_l3_nsga3 experiments/0307/1708_l4_nsga3
python run/mass/audit_release_summary.py experiments/0307/0141_l1_nsga3 experiments/0307/0209_l2_nsga3 experiments/0307/1646_l3_nsga3 experiments/0307/1708_l4_nsga3 --output-csv experiments/0307/release_audit_summary.csv
```
- `tool_rebuild_run_artifacts.py` 会补齐 `summary.json / run_manifest / report.md / visualization_summary.txt / tables/release_audit.csv` 口径。
- `audit_release_summary.py` 优先读取 `tables/release_audit.csv + tables/vop_rounds.csv`（缺失时才回落到 `summary.json` / `events/vop_round_events.jsonl`），用于快速汇总，不要求手工 join。
- 非 release-grade run 会额外产出 `gap_category / primary_failure_signature / minimal_remediation / evidence_hint`，可直接做 gap rollup 与最小修复建议。

### 6.6 命名规则（执行时遵循）
- benchmark 目录短名：`bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
- experiments 目录短名：`<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
- helper 脚本短名：`bm_<scope>.py` / `tool_<topic>.py` / `audit_<topic>.py`
- 详细说明、修复原因、临时标签写入 `summary.json` / manifest，不再塞进目录或脚本名。

---

## 7. 下一步（优先级）

1. 对历史 strict-real 运行做一次最小必要的 artifact rebuild，使 `summary.json / run_manifest / report.md / visualization_summary.txt / tables/release_audit.csv` 完全同口径。
2. 继续补 targeted `L3/L4` strict-real 回归与下游消费脚本，优先围绕 `tables/vop_rounds.csv` 与 `tables/release_audit.csv`，不扩 benchmark。
3. benchmark 重建、`LLM intent vs deterministic` 对照与关键 online COMSOL 复核继续后置，等待当前口径稳定后再推进。
4. M4 与大规模消融继续后置，不提前插入当前主线。

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
- `run/vop_maas/run_L1.py`
- `run/vop_maas/run_L2.py`
- `run/vop_maas/run_L3.py`
- `run/vop_maas/run_L4.py`
- `config/system/mass/base.yaml`
- `config/system/mass/level_profiles_l1_l4.yaml`
- `config/system/mass/level_profiles_l1_l4_real_strict.yaml`
- `config/system/vop_maas/base.yaml`
- `docs/adr/`
- `docs/reports/`
- `config/bom/mass/level_L1_foundation_stack.json`
- `config/bom/mass/level_L2_thermal_power_stack.json`
- `config/bom/mass/level_L3_structural_mission_stack.json`
- `config/bom/mass/level_L4_full_stack_operator.json`
- `workflow/modes/mass/pipeline_service.py`
- `workflow/modes/vop_maas/policy_program_service.py`
- `workflow/modes/vop_maas/contracts.py`
- `workflow/modes/vop_maas/policy_context.py`
- `workflow/modes/vop_maas/policy_compiler.py`
- `docs/reports/R28_vop_maas_master_plan_20260307.md`
- `docs/reports/R29_vop_maas_m0_execution_package_20260307.md`
- `docs/adr/0007-vop-maas-verified-operator-policy-experimental-mode.md`
- `docs/adr/0008-mode-scoped-experiment-observability-v2.md`
- `docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md`
- `docs/reports/R33_phase1_identity_contract_checkpoint_20260308.md`
- `docs/reports/R38_runtime_feature_fingerprint_summary_upgrade_20260308.md`
- `workflow/orchestrator.py`
- `optimization/modes/mass/maas_mcts.py`
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
- `simulation/engineering_proxy.py`
- `simulation/comsol_driver.py`
- `simulation/power_network_solver.py`
- `run/render_blender_scene.py`
