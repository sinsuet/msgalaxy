# R38 Runtime Feature Fingerprint + 中文总结表格化升级报告

日期：2026-03-08  
范围：`vop_maas` 新 run 中文总结、运行特征指纹、`report.md` 摘要入口，以及后续 `visualization / bundle / brief / release-audit` 集成  
状态：implemented + integrated

---

## 1. 背景

在 `llm_final_summary_zh.md` 第一版落地后，出现了两个明显问题：

1. 顶层“目标摘要”仍然做了过强截断，无法完整表达一次仿真优化的原始任务说明；
2. 运行基线、功能线、VOP override、delegated `mass` 生效配置与最终结果仍没有被统一呈现，阅读者需要在 `summary.json`、`tables/*`、`report.md`、日志之间来回跳转。

同时，`docs/reports/R25_current_feature_lines_explainer_20260307.md` 虽然解释了项目的主要功能线，但它产生于 `vop_maas` 主链收口之前，存在以下遗漏：

- 未显式区分 `run_mode` 与 `execution_mode`
- 未覆盖 `vop_maas -> mass` delegated execution 语义
- 未覆盖 `VOP controller override` 与 delegated `mass` 生效值之间的关系
- 未覆盖 controller-first observability、single run log、mode-scoped artifact layout v2
- 未提供可直接被中文总结/报告消费的 canonical 结构

因此，本轮升级目标不是再增加一层自由文本，而是引入一份可审计、可复用、可表格渲染的运行事实中间层。

---

## 2. 本轮决策

本轮为 `vop_maas` 新 run 新增 canonical artifact：

- `events/runtime_feature_fingerprint.json`

它用于稳定表达一次运行的：

- requested baseline
- effective runtime
- gate audit
- VOP controller overlay

并作为以下消费面的统一事实源：

- `llm_final_summary_zh.md`
- `report.md` 中的 `## 中文 LLM 决策总结`
- 后续可扩展到 visualization / bundle / review package

---

## 3. 运行特征指纹内容

`runtime_feature_fingerprint.json` 当前固定表达四块：

### 3.1 Requested Baseline

描述“这次运行一开始打算怎么跑”，包括：

- 入口栈 / run identity
- intent 模式
- search space
- genome representation
- simulation backend
- thermal evaluator mode
- `MCTS / meta_policy / physics audit`
- `operator_program / seed_population`
- `source_gate / operator_family_gate / operator_realization_gate`
- `reflective_replan / feedback_aware_fidelity`

### 3.2 Effective Runtime

描述“最终实际怎么跑”，包括：

- effective intent source
- intent API attempted / succeeded / fallback
- effective search space
- effective genome representation
- effective simulation backend
- effective thermal evaluator mode
- effective online COMSOL budget
- effective physics audit top-k
- first feasible efficiency
- LLM effective validation results

### 3.3 Gate Audit

描述三类 gate 的 mode、pass/fail、strict block 与缺失项：

- `source_gate`
- `operator_family_gate`
- `operator_realization_gate`

### 3.4 VOP Controller Overlay

描述 `vop_maas` 控制层对 delegated `mass` 的增量影响：

- VOP round count
- primary round / key
- policy id
- operator program / actions
- search space override
- runtime overrides
- fidelity plan
- reflective replan triggered / reason
- feedback-aware fidelity reason
- delegated effectiveness verdict

### 3.5 覆盖矩阵

| 关注点 | `runtime_feature_fingerprint` 固定字段块 | 中文总结中的主要表格位置 |
| --- | --- | --- |
| `run_mode vs execution_mode` | `run_identity` + `requested_baseline` | `## 运行身份`、`## 运行基线与功能线指纹` |
| deterministic intent / LLM intent | `requested_baseline.intent_mode` + `effective_runtime.intent_*` | `Requested Baseline vs Effective Runtime` |
| 坐标向量 / 算子程序向量 / hybrid | `requested_baseline.requested_search_space_mode` + `effective_runtime.effective_search_space_mode` | `Requested Baseline vs Effective Runtime` |
| proxy / online COMSOL | `requested_baseline.requested_simulation_backend` + `effective_runtime.effective_simulation_backend` | `Requested Baseline vs Effective Runtime` |
| `MCTS / meta_policy / physics_audit` | `requested_baseline` | `Requested Baseline vs Effective Runtime` |
| strict gates | `gate_audit` | `Gate Audit`、`## 严格门禁与审计结果` |
| VOP controller override | `vop_controller_overlay` | `VOP Controller Overlay`、`## LLM 决策流程` |
| delegated `mass` 效果 | `effective_runtime` + summary-derived delegated effect | `## Delegated Mass 执行结果`、`## 最终结果与结论` |

---

## 4. 中文总结升级

`llm_final_summary_zh.md` 从“摘要式项目符号”升级为“结构化表格 + 完整原始任务说明”的技术复盘文档。

### 4.1 新增/强化章节

- `## 运行基线与功能线指纹`
- `## 原始任务说明（完整）`
- `## 严格门禁与审计结果`

### 4.2 表达方式变化

以下内容统一改为 Markdown table 渲染，减少 JSON blob：

- run identity
- requested baseline vs effective runtime
- gate audit
- VOP controller overlay
- hard constraints
- objectives
- initial metrics
- per-round decision/change/effect
- delegated mass result
- final metrics
- evidence quotes
- artifact index

### 4.3 目标说明策略

- `report.md` 保留 brief summary
- `llm_final_summary_zh.md` 展示 `requirement_text_full`
- 顶层中文文档不再用单行截断替代完整任务说明

---

## 5. 与 R25 的关系

`R25` 仍保留为“功能线解释文档”，但不再承担可执行 summary contract 的角色。  
本轮对 `R25` 的补足方式不是改写原文，而是新增 `runtime_feature_fingerprint.json` 作为 canonical runtime snapshot。

### 5.1 R25 缺口补齐表

| 功能线 / 问题 | `R25` 状态 | `R38` / 新实现如何补齐 |
| --- | --- | --- |
| `run_mode` 与 `execution_mode` 分离 | 未覆盖 `vop_maas -> mass` 双身份 | 在 `summary/manifest/fingerprint` 中固定双身份字段，并进入中文总结运行身份表 |
| delegated `mass` 作为子执行域 | 未覆盖 | 在 `artifacts/vop_maas/delegated_mass/...`、`vop_delegated_effect_summary`、中文总结 delegated 结果表中明确表达 |
| `VOP controller override -> mass` 生效链 | 未覆盖 | 用 `vop_rounds.csv` + `vop_decision_summary` + `runtime_feature_fingerprint.vop_controller_overlay` 串联 |
| single run log / mode-scoped artifacts | 未覆盖 | `0008/R32` 已固定；`R38` 把其纳入运行指纹和中文总结消费链 |
| metrics / 配置表达过于分散 | 仅概念说明，缺少单 run 事实层 | 通过 `runtime_feature_fingerprint.json` + 表格化 `llm_final_summary_zh.md` 统一收口 |
| 完整原始任务说明 | 第一版中文总结仍被截断 | 中文总结固定展示 `requirement_text_full`，不再用单行摘要替代 |

一句话概括：

- `R25` 回答“项目有哪些功能线”
- `R38` 回答“本次 run 具体走了哪几条线、怎么生效、结果如何”

---

## 6. 落地文件

- `core/runtime_feature_fingerprint.py`
- `core/final_summary_zh.py`
- `workflow/modes/vop_maas/policy_program_service.py`
- `core/logger.py`
- `tests/test_final_summary_zh.py`
- `tests/test_vop_maas_mode.py`

---

## 7. 当前结果

当前 `vop_maas` 新 run 在完成后会稳定新增：

- `llm_final_summary_zh.md`
- `events/llm_final_summary_digest.json`
- `events/runtime_feature_fingerprint.json`

并在：

- `summary.json`
- `events/run_manifest.json`
- `report.md`

中暴露相应路径与状态字段。

同时，`runtime_feature_fingerprint.json` 与 `llm_final_summary_zh.md` 已继续接入：

- `visualizations/visualization_summary.txt`
- Blender `render_bundle.json`
- Blender `review_payload.json`
- Blender `render_manifest.json`
- Blender `render_brief.md`
- `run/mass/audit_release_summary.py` 输出的 Markdown `## Observability Links`

这意味着同一份 controller-first runtime snapshot 已能同时服务：

- 中文复盘
- 可视化摘要
- Blender 工程审阅包
- release audit 汇总

---

## 8. 后续建议

上一轮建议中，以下三项已完成：

1. visualization summary
2. Blender bundle / review package / brief
3. release-grade audit summary markdown

因此下一步建议转向下一条中文总结主线：

1. 为 `mass` 新 run 设计并实现“传统优化过程中文总结”
2. 主叙事从 `LLM decision flow` 转为 `NSGA-II / NSGA-III / MOEA/D + attempts/generations/feasibility/audit`
3. 继续复用 digest/render 框架，但保持 mode-scoped，不把 `mass` 误写成 `vop_maas` controller 语义

完成后，“同一份运行事实”将能同时支撑：

- 中文复盘
- 工程可视化
- release 审阅
- 历史 run 对比
- `mass` / `vop_maas` 双主线中文总结
