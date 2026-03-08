# 0007-vop-maas-verified-operator-policy-experimental-mode

- status: accepted
- date: 2026-03-07
- deciders: msgalaxy-core
- supersedes: `0002-vop-maas-reserved-mode`

## Context

`mass` 已经形成可信的 `LLM -> ModelingIntent -> pymoo -> multiphysics -> diagnosis` 主链路，
并且 `L1-L4` strict-real 主线已经跑通。下一阶段的研究重点不是让 LLM 直接输出最终布局坐标，
而是在不破坏 `mass` 数值优化内核的前提下，把 LLM 升级为 **策略层 / 算子层控制器**。

保留 `vop_maas` 为 reserved scaffold 已不足以支撑后续研究：
- 需要一个真正可运行的 experimental mode 来承载 `VOP-MaaS` 研究切片；
- 需要把 LLM 的输出边界限定为 **verified operator-policy**，而不是自由代码或直接几何解；
- 需要明确归因：策略是否真的改变了搜索轨迹、可行解到达速度与高保真预算消耗。

## Decision

1. 将 `optimization.mode = "vop_maas"` 从 reserved scaffold 升级为 **experimental mode**。
2. `vop_maas` 保持 `mass` 为唯一可执行数值优化内核：
   - 不允许 LLM 直接输出最终布局坐标；
   - 不允许 `vop_maas` 绕过 `ModelingIntent -> compile -> pymoo -> physics` 主链路。
3. 在 `vop_maas` 中引入两个研究接口：
   - `VOPG`：Violation-Operator Provenance Graph，多物理违规-算子溯源图；
   - `VOPPolicyPack`：受 schema 约束的 operator-policy 包。
4. 所有策略包必须经过：
   - schema validation
   - allowlist repair / rejection
   - optional screening
   - fallback-to-`mass`
5. `VOPPolicyPack` 当前只允许影响以下注入点：
   - Phase A intent patch
   - operator seed candidates
   - `mass_search_space`
   - runtime knob priors
   - fidelity hooks
6. `vop_maas` 必须写出 attribution/telemetry：
   - `vop_policy_graph`
   - `vop_policy_generation`
   - `vop_policy_validation`
   - `vop_policy_screening`
   - `vop_policy_priors`
   - `vop_policy_applied`
   - `vop_policy_fallback_reason`

## Research Scope

当前仓库承诺 `M1-M4` 的第一可运行切片，并新增 `M5-min` 单轮 reflective replanning 薄切片：
- `M1`：正式 experimental routing + stack contract + fallback attribution
- `M2`：`VOPG` / `VOPPolicyPack` schema + validator + repair / rejection
- `M3`：单次 pre-search policy generation，并注入 `mass`
- `M4`：counterfactual-style screening（cheap proxy / candidate scoring）
- `M5-min`：基于 `previous_policy_pack + vop_policy_feedback + updated VOPG` 的一次 reflective replanning，并带 feedback-aware fidelity_plan，再次委托 `mass`

当前**不**宣称以下能力已实现：
- multi-round reflective replanning
- policy memory / template evolution
- neural feasibility predictor
- neural operator policy
- multi-fidelity neural scheduler

## Implementation Snapshot (2026-03-07)

当前 experimental 切片已经具备如下实现落点：

1. **策略契约层**
   - 已定义 `VOPGraph` / `VOPPolicyPack` / `VOPOperatorCandidate`
   - 已实现 `validate_vop_policy_pack(...)`
   - 已具备 repair / rejection / allowlist 过滤

2. **策略上下文层**
   - 已实现 `build_vop_graph(...)`
   - 当前输入主要来自 bootstrap metrics、violation family、runtime constraints 与 source tags

3. **策略执行层**
   - 已实现 `build_mock_policy_pack(...)`
   - 已实现 `screen_policy_pack(...)`
   - 已实现 `vop_maas -> mass` 委托执行

4. **`mass` 内核注入层**
   - 已支持 `policy_priors` 注入
   - 当前影响面限定为：
     - intent patch
     - operator candidates
     - search-space prior
     - runtime knobs
     - fidelity-related overrides

5. **observability / attribution**
   - 已写出：
     - `vop_policy_graph`
     - `vop_policy_generation`
     - `vop_policy_validation`
     - `vop_policy_screening`
     - `vop_policy_priors`
     - `vop_policy_applied`
     - `vop_policy_fallback_reason`
   - 已新增：
     - `vop_policy_feedback`
     - `vop_policy_rounds`
     - `vop_reflective_replanning`
   - `vop_policy_feedback` 当前会稳定汇总 `first_feasible_eval / comsol_calls_to_first_feasible / fallback attribution / effective fidelity`

6. **bounded reflective replanning**
   - 已支持单轮 reflective replanning
   - 二次输入限定为 `previous_policy_pack + vop_policy_feedback + updated VOPG`
   - 已支持 feedback-aware fidelity_plan：根据上一轮 `fallback attribution / effective fidelity / first_feasible` 指标，生成有界的 `physics_audit_top_k / online_comsol_eval_budget / thermal_evaluator_mode` 推荐
   - 委托 `mass` 执行时会对 `optimization` 配置做 per-run snapshot/restore，保持策略边界可审计

7. **stack / routing**
   - 已注册 `vop_maas` stack
   - 已新增 `config/system/vop_maas/base.yaml`
   - 已新增 `run/vop_maas/run_L1.py` 至 `run/vop_maas/run_L4.py`

当前详细研究/开发总方案另见：
- `docs/reports/R28_vop_maas_master_plan_20260307.md`

### Observability Addendum (2026-03-07)

- `vop_maas` 的 reflective / feedback-aware attribution 必须稳定落到运行产物，而不只停留在内存 metadata。
- 当前 experimental mode 已将以下字段稳定写入 `summary.json`，并同步进 `events/run_manifest.json` 的 `extra`：
  - `vop_policy_primary_round_index`
  - `vop_policy_primary_round_key`
  - `vop_reflective_replanning`
  - `vop_feedback_aware_fidelity_plan`
  - `vop_feedback_aware_fidelity_reason`
- `summary.json` 额外保留轻量 `vop_round_audit_digest`，用于与 `policy_tuning.csv` / `phases.csv` 基于 `vop_round_key` 做 round-level join。
- round-level attribution 继续复用现有 `policy_events.jsonl -> tables/policy_tuning.csv` 与 `phase_events.jsonl -> tables/phases.csv` 路径，不新增 benchmark / 实验矩阵 schema。
- 为降低下游读取复杂度，`policy_tuning.csv` / `phases.csv` 的公共 join-key 采用固定前置顺序：`run_id,timestamp,iteration,attempt,vop_round_key,round_index,policy_id,previous_policy_id`。

#### 2026-03-07 late update

- round-level attribution 已进一步收口到 `tables/vop_rounds.csv`，并与 `summary.json` / `events/run_manifest.json` / `report.md` / visualization summary 对齐口径。
- `tables/vop_rounds.csv` 的稳定字段至少包含：
  - `round_index`
  - `vop_round_key`
  - `trigger_reason`
  - `feedback_aware_fidelity_plan`
  - `feedback_aware_fidelity_reason`
  - `previous_policy_id`
  - `candidate_policy_id`
  - `final_policy_id`
  - `mass_rerun_executed`
  - `skipped_reason`
- `summary.json` 现保留 `vop_round_count`、`vop_round_audit_table` 与轻量 `vop_round_audit_digest`；`run_manifest.extra` 同步保留 `vop_round_audit_table` 与主 round 标识，避免 schema 过大。
- `policy_tuning.csv` / `phases.csv` / `vop_rounds.csv` 共享固定前置 join-key：`run_id,timestamp,iteration,attempt,vop_round_key,round_index,policy_id,previous_policy_id`。
- release-grade audit 字段 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 已统一写入 `summary.json`、`events/run_manifest.json`、`report.md`、visualization summary，并物化到 `tables/release_audit.csv`。
- 下游读取优先口径调整为：`tables/vop_rounds.csv` 负责 round-level 审计，`tables/release_audit.csv` 负责 run-level release audit；`policy_tuning.csv` / `phases.csv` 继续作为补充联查表，而非唯一消费入口。
- 历史产物补重建与轻量汇总入口已补到 helper 脚本：`run/mass/tool_rebuild_run_artifacts.py` 与 `run/mass/audit_release_summary.py`。
- `audit_release_summary.py` 现对非 release-grade run 额外给出 `gap_category / primary_failure_signature / minimal_remediation / evidence_hint`，用于下游直接做 failure breakdown，而不必手工 join 多张表。
- feedback-aware fidelity escalation 仍保持 bounded：仅在真实 `comsol` backend 且命中特定 `failure_signature` 时，才允许升级 `thermal_evaluator_mode / online_comsol_eval_budget / physics_audit_top_k`；若不升级，必须写出明确的 `fidelity_escalation_reason / feedback_aware_fidelity_reason`。

## ADR Update Rule

本 ADR 在 `vop_maas` 生命周期内应持续更新，但需遵守以下边界：

- 若只是补充当前 experimental 切片的实现细节、指标口径或 observability 字段，可在本 ADR 上增补；
- 若 `vop_maas` 从 `experimental` 升级到 `stable`，或引入 `M5/M6` 级别的新控制语义，应新增后续 ADR；
- 当前 ADR 已覆盖 “single-shot policy + screening + bounded single-round reflective replanning”；若未来升级为 multi-round reflective replanning、policy memory 或 template evolution，应新增后续 ADR 记录控制闭环变化，而不是仅在文档中模糊补充。

## Evaluation Contract

`vop_maas` 的主评估不只看最终 Pareto/frontier，而是优先看：
- `feasibility_rate`
- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`
- `best_cv`
- `best_feasible_objective_vector`
- `real_source_coverage`
- `operator_family_coverage`
- `policy_validity_rate`
- `policy_fallback_rate`

核心对照线冻结为：
- `mass_baseline`
- `mass_llm_intent_only`
- `vop_static`
- `vop_screened`

核心 ablation 优先级：
- 去掉 `VOPG`
- 去掉 screening
- 只保留 operator prior
- 只保留 runtime prior
- 去掉 fidelity plan

### M0 Execution Freeze

为避免 `vop_maas` 在早期研究阶段同时扩展“策略层”和“算法层”，当前 `M0` 执行冻结如下：

- 只保留 `L1-L4`
- 只保留 `NSGA-III`
- 只保留当前已跑通的 strict-real level profile 口径
- `NSGA-II`、`MOEA/D` 与其他 solver 扩展全部后置

这意味着当前阶段的研究主命题必须聚焦于：

- verified operator-policy 是否有效；
- 其是否改变了搜索轨迹；
- 其是否改善 `first_feasible` 效率与高保真预算消耗；

而不是同时讨论多算法泛化。

## Consequences

### Positive
- 给 `VOP-MaaS` 论文叙事提供了清晰、可信、可复现的 runtime host。
- 保持 `mass` baseline 的可信度，不把 LLM 误用为直接布局生成器。
- 让策略收益可以落到明确的 attribution 字段与轨迹分叉证据上。
- 允许在无 API 环境下通过 `mock_policy` 做 deterministic smoke。

### Negative
- `vop_maas` 与 `mass` 暂时共享执行内核，文档与日志必须持续强调边界，避免外部误读。
- 额外引入 schema、screening、fallback 与 telemetry 的维护成本。
- 如果后续实验不能证明轨迹分叉与 first-feasible 效率收益，论文贡献会显著减弱。

### Constraints
- `vop_maas` 当前生命周期状态仅为 `experimental`，不得作为默认 stable 主线对外表述。
- `vop_maas` 默认继续复用 `config/bom/mass/*`，但必须使用独立 `config/system/vop_maas/base.yaml`。
- unknown fields / unsupported operators / unimplemented metric keys 不得 silent pass。
- 任何真实实验结论仍需遵守现有 `source/operator-family/operator-realization` gate 口径。
