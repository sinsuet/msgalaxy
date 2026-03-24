# Mode Scoped Experiment Observability Refactor Master Plan

- report_type: master_refactor_plan
- date: 2026-03-08
- owner: msgalaxy-core
- decision_adr: `docs/adr/0008-mode-scoped-experiment-observability-v2.md`
- related_adr: `docs/adr/0007-vop-maas-verified-operator-policy-experimental-mode.md`

## 1. 背景与现状证据

当前问题不是单点 defect，而是实验结果系统长期把三类概念混成了一套逻辑：
- `run_mode`：入口请求模式；
- `observability_mode`：观测层 bucket；
- delegated execution mode：真实执行核心。

已确认的现状证据：
- `core/logger.py` 旧实现只认可 `agent_loop|mass`，`vop_maas` 会掉入 `unknown` 或错误 bucket；
- `core/mode_contract.py` 与 `core/llm_interaction_store.py` 延续“两态观测模型”，使 `vop_maas` 的 LLM 交互进入 `shared/agent_loop` 语义；
- `core/visualization.py` 曾把 `vop_maas` 归一化成 `agent_loop|mass` 二选一，导致 `visualization_summary.txt` 首行写错；
- `core/logger.py` 曾无条件创建 `evolution_trace.csv` 与 `mass_trace.csv`，让 `mass` / `vop_maas` 天生带有 legacy `agent_loop` 痕迹；
- 下游 reader / tool 长期硬编码根目录 `mass_trace.csv`、`evolution_trace.csv`、`snapshots/`、`llm_interactions/`。

结论：当前系统缺的不是“再加一个 mode if-else”，而是要把实验身份、执行身份、raw artifact 布局和主消费面完整拆开。

## 2. Current vs Target

### 2.1 Current

| 维度 | 当前状态 | 问题 |
| --- | --- | --- |
| run identity | 入口 mode 与观测 bucket 混用 | `vop_maas` 易降级 |
| raw artifact | 根目录混放 + 两态路径 | mode 污染、writer 歧义 |
| LLM interactions | shared/role 推断为主 | 无法稳定表达 VOP controller vs delegated mass |
| visualization | 二态 dispatch | `vop_maas` 视图失真 |
| reader/tool | 依赖硬编码路径 | 历史兼容与新布局难并存 |

### 2.2 Target

| 维度 | 目标状态 |
| --- | --- |
| run identity | `run_mode`、`execution_mode`、`lifecycle_state` 显式分离 |
| raw artifact | 全部进入 `artifacts/<mode>/...` 命名空间 |
| LLM interactions | 显式 mode writer，`vop_maas` 顶层与 delegated mass 分域 |
| visualization | 三态 dispatch：`mass` / `vop_maas` / `agent_loop(legacy)` |
| reader/tool | 优先读 `events/artifact_index.json`，无索引才 fallback |

## 3. 目录协议 v2

### 3.1 顶层公共消费面

```text
<run_dir>/
  summary.json
  report.md
  events/
  tables/
  visualizations/
  mph_models/
  run_log.txt
  run_log_debug.txt  # legacy fallback only
```

### 3.2 Mode-scoped raw artifacts

```text
<run_dir>/
  artifacts/
    mass/
      llm_interactions/
      trace/
      snapshots/
      step_files/
      mass_trace.csv
      maas_diagnostics.jsonl
    vop_maas/
      llm_interactions/
      policy/
      rounds/
      delegated_mass/
        llm_interactions/
        trace/
        snapshots/
        step_files/
        mass_trace.csv
        maas_diagnostics.jsonl
    agent_loop/
      llm_interactions/
      evolution_trace.csv
      trace/
      snapshots/
```

### 3.3 索引协议

```text
<run_dir>/events/artifact_index.json
```

约束：
- 所有 raw artifact 以 `artifact_index.json` 为 canonical 索引；
- 顶层公共消费面不再直接承载 root raw file；
- `vop_maas` 的 delegated `mass` 必须进入 `artifacts/vop_maas/delegated_mass/...`。

### 3.4 命名协议 v2.1

- 新 run 目录命名固定为：`experiments/<YYYYMMDD>/<HHMM>_<mode-token>_<short-tag>`。
- 固定 mode token：
  - `mass -> mass`
  - `vop_maas -> vop`
  - `agent_loop -> agent`
- `short-tag` 保留 `l1_nsga3` 这类紧凑格式，因此推荐样式：
  - `1828_mass_l1_nsga3`
  - `1828_vop_l1_nsga3`
  - `1828_agent_l1_nsga3`
- 若 `short-tag` 已包含相同 mode token，writer 必须去重，避免 `mass_mass` / `vop_vop`。
- 冲突序号 `_02` 规则保持不变。
- `run_id` 跟随 run leaf name 带上 mode token。

### 3.5 单日志策略

- 新 run 默认只保留 `run_log.txt`，不再生成 `run_log_debug.txt`。
- `run_log.txt` 使用 milestone tag 分层：
  - `[RUN]`
  - `[VOP][BOOTSTRAP]`
  - `[VOP][DECISION]`
  - `[VOP][DELEGATE]`
  - `[VOP][EFFECT]`
  - `[VOP][REPLAN]`
  - `[MASS][A]...[MASS][D]`
- 原始 prompt / response dump 继续保留在 mode-scoped `llm_interactions`。
- reader / tool 固定读取顺序：
  1. `run_log.txt`
  2. 若不存在且是 legacy run，再 fallback `run_log_debug.txt`

## 4. 字段协议 v2

### 4.1 Run-level mandatory fields

`summary.json` / `events/run_manifest.json` 必须新增或固定：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `run_mode` | 入口请求模式 | `vop_maas` |
| `execution_mode` | 真实执行核心 | `mass` |
| `lifecycle_state` | 生命周期状态 | `experimental` |
| `artifact_layout_version` | artifact 协议版本 | `2` |
| `artifact_index_path` | raw 索引路径 | `events/artifact_index.json` |
| `delegated_execution_mode` | 委托执行模式 | `mass` |
| `optimization_mode` | 运行态口径字段 | `mass` 或 `vop_maas` |
| `run_name_mode_token` | run 目录 mode token | `mass` / `vop` / `agent` |
| `run_name_schema_version` | run 命名协议版本 | `2` |

### 4.2 Event / table identity columns

新增统一身份列：
- `producer_mode`
- `execution_mode`
- `lifecycle_state`

约束：
- run-level 必须完整；
- 表级至少不能缺失会影响归属判断的字段；
- join-key 前缀保持稳定，identity 列追加在 join-key 之后，避免破坏既有下游读取口径。

### 4.3 VOP controller summary chain

- `events/vop_round_events.jsonl -> tables/vop_rounds.csv -> summary.json / report.md / visualization_summary.txt` 是唯一 canonical VOP round chain。
- `summary.json` / manifest extra 对 `vop_maas` 固定补齐：
  - `vop_round_count`
  - `vop_round_audit_table`
  - `vop_round_audit_digest`
  - `vop_policy_primary_round_index`
  - `vop_policy_primary_round_key`
  - `vop_decision_summary`
  - `vop_delegated_effect_summary`
- `vop_decision_summary` 固定承载：
  - `policy_id`
  - `policy_source`
  - `selected_operator_program_id`
  - `operator_actions`
  - `search_space_override`
  - `intent_changes`
  - `runtime_overrides`
  - `fidelity_plan`
  - `expected_effects`
  - `confidence`
- `vop_delegated_effect_summary` 固定承载：
  - `diagnosis_status`
  - `diagnosis_reason`
  - `search_space_effect`
  - `first_feasible_eval`
  - `comsol_calls_to_first_feasible`
  - `audit_status`
  - `effectiveness_verdict`

### 4.4 Canonical round audit requirements

- 只要产生过 VOP policy，`tables/vop_rounds.csv` 就必须非空。
- `vop_rounds.csv` 升级为 round-level canonical 审计表，补齐：
  - `decision_rationale`
  - `change_summary`
  - `expected_effects`
  - `observed_effects`
  - `effectiveness_summary`
- `policy_tuning.csv` 保留 policy-level ledger；
- `phases.csv` 继续保留 delegated `mass` A/B/C/D，但 `vop_maas` consumer 优先读取 `V0/V1/...` controller rows；
- 当 `summary` 中 VOP digest 缺失时，report / visualization 不允许静默写 `-1` / `0`，必须回读 `tables/vop_rounds.csv` 重新派生。

### 4.5 Runtime feature fingerprint + 中文总结

- 为补齐 `R25` 未完整覆盖的 runtime feature lines，`vop_maas` 新 run 额外生成：
  - `events/runtime_feature_fingerprint.json`
  - `llm_final_summary_zh.md`
  - `events/llm_final_summary_digest.json`
- `runtime_feature_fingerprint.json` 固定表达四块：
  - requested baseline
  - effective runtime
  - gate audit
  - VOP controller overlay
- `llm_final_summary_zh.md` 固定升级为“完整任务说明 + 表格化技术复盘”：
  - 展示 `requirement_text_full`
  - 表格化呈现 runtime feature lines、hard constraints、objectives、per-round decision/change/effect、gate audit、delegated mass result、final metrics
  - 不粘贴大段 raw prompt / response
- `summary.json` / `events/run_manifest.json` 对 `vop_maas` 追加：
  - `runtime_feature_fingerprint_path`
  - `llm_final_summary_zh_path`
  - `llm_final_summary_digest_path`
  - `llm_final_summary_status`
  - `llm_final_summary_language`
  - `llm_final_summary_model`
- 该升级的补充设计与实施说明单独沉淀在：
  - `docs/reports/R38_runtime_feature_fingerprint_summary_upgrade_20260308.md`

## 5. 可视化分派协议

### 5.1 `mass`

- 只输出 `mass` 视图；
- 只消费 `artifacts/mass/*` 或 delegated mass index；
- 不再产出 `agent_loop` 风格 `evolution_trace.png`；
- 仍可物化 `tables/*` 供下游统一消费。

### 5.2 `vop_maas`

- 顶层视图由 VOP controller 视角主导；
- 必须包含：
  - round 数
  - primary round
  - reflective replan 触发情况
  - fidelity strategy
- 必须额外展示：
  - search-space override
  - runtime / fidelity override
  - expected effect vs observed effect
  - delegated mass final result
- `report.md` 固定新增章节：
  - `## VOP Controller Decision Flow`
  - `## Decision Changes`
  - `## Observed Effects`
  - `## Delegated Mass Execution Summary`
- `visualization_summary.txt` 首行必须是 `Optimization mode: vop_maas`，且不得再回退成 `agent_loop`
  - delegated mass 执行结果摘要
- 不出现 `evolution_trace.png`；
- `visualization_summary.txt` 第一行必须是 `Optimization mode: vop_maas`。

### 5.3 `agent_loop`

- 仅 legacy 分支读取 `evolution_trace`；
- 不再作为默认 fallback；
- 仅用于历史 run 兼容视图。

## 6. 历史 run 迁移策略

### 6.1 读兼容策略

reader 顺序固定为：
1. 读取 `events/artifact_index.json`
2. 若不存在，则尝试 legacy root-path fallback：
   - `mass_trace.csv`
   - `evolution_trace.csv`
   - `snapshots/`
   - `llm_interactions/`
3. 若仍缺失，则进入显式 `legacy` reader 分支，而不是把 run 伪装成 `agent_loop`

### 6.2 rebuild / migrate 目标

历史 run 的 rebuild / migrate 负责补齐：
- `artifact_layout_version=2`
- `events/artifact_index.json`
- `summary.json` / `run_manifest.json` 的身份字段
- 可定位的 raw artifact 索引记录

### 6.3 迁移优先级

1. 最近关键 run
2. release-grade evidence run
3. 下游工具正在消费的 run
4. 其他历史 run 按需 rebuild

## 7. 分阶段重构执行计划

### Phase 1 — Identity contract + artifact layout + summary/manifest/report 对齐

- 目标：建立 `run_mode / execution_mode / lifecycle_state / artifact_layout_version` 基础契约
- 变更范围：
  - `core/mode_contract.py`
  - `core/logger.py`
  - run-level summary / manifest / report
- 不变项：
  - `mass` 仍是数值执行核心
  - `vop_maas` 仍委托 `mass`
- 依赖关系：无前置依赖
- 验收条件：
  - 新 `vop_maas` run 写出 `run_mode=vop_maas`
  - `execution_mode=mass`
  - `lifecycle_state=experimental`
- 回滚策略：
  - 保留新字段，但允许 reader 暂时忽略
  - 不回退为把 `vop_maas` 降级成 `unknown`

### Phase 2 — Raw artifact writer 改造

- 目标：writer 真正按 mode-scoped 布局写 raw artifacts
- 变更范围：
  - `core/logger.py`
  - `core/llm_interaction_store.py`
  - `workflow/modes/mass/pipeline_service.py`
  - `workflow/modes/vop_maas/policy_program_service.py`
- 不变项：
  - 顶层 `tables/*` 保持统一消费
  - 不改动 solver 语义
- 依赖关系：依赖 Phase 1 身份字段
- 验收条件：
  - `mass` 新 run 不生成根目录 `evolution_trace.csv`
  - `vop_maas` delegated mass 证据进入 `artifacts/vop_maas/delegated_mass/*`
- 回滚策略：
  - 保留 legacy reader fallback
  - 不回退 writer 到根目录混放

### Phase 3 — Visualization dispatch 三态拆分

- 目标：可视化真正按三态 dispatch
- 变更范围：
  - `core/visualization.py`
  - `core/modes/mass/visualization_dispatch.py`
  - `core/modes/vop_maas/visualization_dispatch.py`
- 不变项：
  - 顶层 `visualizations/` 仍是统一出口
- 依赖关系：依赖 Phase 1/2 的身份与 artifact index
- 验收条件：
  - `mass` 不再输出 `evolution_trace`
  - `vop_maas` 输出 VOP 主视图 + delegated mass 摘要
  - `visualization_summary.txt` 首行与真实 `run_mode` 一致
- 回滚策略：
  - 允许 legacy reader 回退读取老 run
  - 不允许继续把 `vop_maas` 归一化成 `agent_loop`

### Phase 4 — Reader fallback + migration/rebuild tool

- 目标：reader/tool 先读 index，再 fallback；历史 run 可补齐 layout v2
- 变更范围：
  - `run/mass/audit_release_summary.py`
  - `run/mass/tool_rebuild_run_artifacts.py`
  - `visualization/blender_mcp/bundle_builder.py`
  - CLI / API / helper readers
- 不变项：
  - 历史 run 不做强制破坏性切换
- 依赖关系：依赖 Phase 2/3 产出的稳定索引
- 验收条件：
  - 未迁移旧 run 仍可被读取
  - rebuild 后补齐 identity 与 artifact index
- 回滚策略：
  - 保留 legacy path fallback

### Phase 5 — Tests + docs sync + release gate

- 目标：测试、文档、发布口径收口
- 变更范围：
  - tests
  - `HANDOFF.md`
  - `README.md`
  - checkpoint reports
- 不变项：
  - 不扩展到新的 benchmark schema
- 依赖关系：依赖前四阶段闭环
- 验收条件：
  - 目标测试通过
  - 文档可独立指导后续推进
- 回滚策略：
  - 暂停 release gate，但不回退已接受 ADR

## 8. 风险矩阵

| 风险 | 影响 | 概率 | 控制措施 |
| --- | --- | --- | --- |
| 下游脚本硬编码 `mass_trace.csv` / `evolution_trace.csv` / `llm_interactions/` | 高 | 高 | 先引入 `artifact_index.json` 与 reader fallback，再切 writer |
| `vop_maas` 顶层与 delegated mass 字段重复、冲突 | 中 | 中 | 顶层只保留摘要字段，raw 细节全部索引到子命名空间 |
| 历史 run 数量大，迁移成本不均 | 中 | 高 | 优先迁移最近关键 run 与 release-grade run，其余按需 rebuild |
| 测试未强约束 `run_mode` / `execution_mode` | 高 | 中 | 先补测试，再做 writer 重构 |
| visualization 仍残留两态假设 | 中 | 中 | 三态 dispatch + storyboard 回归测试 |

## 9. 验收矩阵

| 类别 | 验收项 | 通过标准 |
| --- | --- | --- |
| 文档 | ADR 与主报告无二义性 | 可独立指导实现，明确 supersede / cross-reference |
| 协议 | `vop_maas` run 身份正确 | `run_mode=vop_maas`、`execution_mode=mass` |
| 目录 | raw artifacts 正确归域 | 不再出现默认 root raw file 混写 |
| 可视化 | dispatch 正确 | `mass` 无 `evolution_trace`，`vop_maas` 有 VOP 主视图 |
| 兼容 | 历史 run 仍可读 | reader fallback 生效 |
| 测试 | 新旧口径都受控 | identity / layout / dispatch / fallback 测试通过 |

## 10. 执行看板模板

后续阶段 checkpoint report 统一采用以下模板，并只记录阶段完成项、偏差、遗留风险、下一阶段入口。

### 命名约定

- `R33_phase1_identity_contract_checkpoint_<YYYYMMDD>.md`
- `R34_phase2_writer_layout_checkpoint_<YYYYMMDD>.md`
- `R35_phase3_visualization_dispatch_checkpoint_<YYYYMMDD>.md`
- `R36_phase4_migration_checkpoint_<YYYYMMDD>.md`

### 模板

```text
# <Report Title>

- phase:
- date:
- owner:
- based_on: docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md

## Scope
- 本阶段目标
- 实际覆盖范围

## Completed
- 已完成项

## Deviations
- 与 master plan 的偏差

## Risks
- 当前残留风险

## Validation
- 跑过的测试 / 手工验证

## Next Gate
- 下阶段入口条件
```

## 11. 实施备注

本 master report 固定以下实现边界：
- `core/mode_contract.py` 负责三态 run identity 与 execution identity；
- `core/logger.py` 负责 run-level identity、artifact index、writer 分派；
- `core/llm_interaction_store.py` 去掉 shared 默认语义，writer 要求显式 mode；
- `workflow/modes/mass/pipeline_service.py` 的 LLM / trace / snapshot / step_files / mass_trace 统一归入 `artifacts/mass/*`；
- `workflow/modes/vop_maas/policy_program_service.py` 顶层身份固定为 `run_mode=vop_maas`，delegated mass 证据归入 `artifacts/vop_maas/delegated_mass/*`；
- `optimization/modes/mass/observability/materialize.py` 保持顶层 `tables/*` 统一消费，但需要补齐 identity 字段；
- `run/mass/audit_release_summary.py`、`run/mass/tool_rebuild_run_artifacts.py`、Blender bundle reader 必须优先读 `artifact_index.json`。

## 12. Definition of Done

本重构被视为完成，需同时满足：
- 新 run 不再把 `vop_maas` 写成 `unknown`、`shared` 或 `agent_loop` fallback；
- `mass` / `vop_maas` 的 raw artifacts 已彻底按 mode-scoped 协议落盘；
- `visualization_summary.txt` 首行稳定输出真实 `run_mode`；
- 历史 run 通过 fallback 或 rebuild 后仍可被下游工具读取；
- `tables/vop_rounds.csv` 与 `tables/release_audit.csv` 的主消费口径未被破坏。
