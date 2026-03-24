# 0008-mode-scoped-experiment-observability-v2

- status: accepted
- date: 2026-03-08
- deciders: msgalaxy-core
- supersedes: `0005-core-two-mode-observability-split`
- cross-reference: `0007-vop-maas-verified-operator-policy-experimental-mode`

## Context

`0005` 在当时把核心观测逻辑从单体实现拆成 `agent_loop` / `mass` 两态，这是一个合理的过渡方案：`mass` 作为当前主线，`agent_loop` 作为 legacy 主链，足以支撑 2026-03-05 的运行事实。

但到 2026-03-08，仓库的真实运行态已经发生变化：
- `agent_loop` 只保留兼容价值，生命周期已进入 deprecated；
- `mass` 是稳定的数值执行主链；
- `vop_maas` 已经成为真实可运行的 experimental mode，其顶层承担 VOP controller 语义，但真实数值执行仍委托给 `mass`。

现有实验结果系统仍在沿用“两态观测模型”，导致 run 目录、LLM 交互、trace、可视化和 summary/report 之间出现跨 mode 污染。这个问题已经不再是 UI 层命名不准，而是实验身份、执行身份与 raw artifact 协议没有被显式分离。

## Problem Statement

已确认的混淆点如下：

1. `core/logger.py` 旧逻辑只把 `agent_loop|mass` 当作合法 `run_mode`，`vop_maas` 会降级成 `unknown` 或进入错误 bucket。
2. `core/mode_contract.py` 与 `core/llm_interaction_store.py` 延续“两态观测模型”，使 `vop_maas` 的 LLM 交互进入 `shared/agent_loop` 语义。
3. `core/visualization.py` 把 `vop_maas` 归一化为 `agent_loop|mass` 二选一，导致 `visualization_summary.txt` 会把真实 `vop_maas` run 写成 `Optimization mode: agent_loop`。
4. `core/logger.py` 曾无条件初始化 `evolution_trace.csv` 与 `mass_trace.csv`，让 `mass` / `vop_maas` run 天生带有 legacy `agent_loop` 痕迹。
5. `run_mode`、`observability_mode`、delegated execution mode 混成一套隐式规则，下游工具大量依赖目录与文件名猜 mode，而不是读取显式身份字段。
6. `agent_loop` 被默认为 fallback 观测桶，导致“历史兼容”与“当前主视图”无法分离。

## Decision

### 1. 运行身份契约升级为“三态 run identity + explicit execution identity”

- `run_mode` 表示入口请求模式，仅允许：
  - `agent_loop`
  - `mass`
  - `vop_maas`
- `execution_mode` 表示真实执行核心：
  - `agent_loop -> agent_loop`
  - `mass -> mass`
  - `vop_maas -> mass`
- `lifecycle_state` 为显式生命周期字段：
  - `agent_loop = deprecated`
  - `mass = stable`
  - `vop_maas = experimental`

### 2. 新增 `artifact_layout_version = 2`

所有新 run 采用 `Mode Scoped Experiment Observability v2` 协议，并在 run 启动时写出：
- `events/artifact_index.json`
- `summary.json` 中的 `artifact_layout_version`
- `events/run_manifest.json` 中的 `artifact_layout_version`

### 3. 顶层 run 身份与 delegated execution 必须分离表达

- `vop_maas` 顶层 run 目录归属于 `vop_maas`，不得再降级成 `unknown`、`shared` 或借道 `agent_loop`。
- `vop_maas` 的 controller / policy / round-level 证据保留在 `artifacts/vop_maas/...`。
- `vop_maas` 委托 `mass` 的原始执行证据进入 `artifacts/vop_maas/delegated_mass/...`。
- `mass` 只表达 `mass` 当前真实执行链，不再默认伴随 legacy `agent_loop` 产物。

### 4. 顶层统一消费面保留，raw artifact 改为 mode namespace

顶层 run 目录继续保留统一消费面：
- `summary.json`
- `report.md`
- `events/`
- `tables/`
- `visualizations/`
- `mph_models/`
- `run_log.txt`
- `run_log_debug.txt`（legacy read fallback only；新 run 默认不再生成）

但 raw artifact 必须进入 mode-scoped 命名空间：
- `artifacts/mass/...`
- `artifacts/vop_maas/...`
- `artifacts/agent_loop/...`
- `artifacts/vop_maas/delegated_mass/...`

### 5. `artifact_index.json` 成为 raw artifact 的 canonical 索引

- 所有 raw artifact 的 canonical 索引为 `events/artifact_index.json`。
- reader / tool 优先读取 `artifact_index.json`，缺失时才回退到 legacy 路径。
- `tables/release_audit.csv` 与 `tables/vop_rounds.csv` 继续分别作为 run-level / round-level 主消费表。

### 6. `agent_loop` 进入 deprecated / legacy compatibility layer

- `agent_loop` 不再是默认 observability fallback。
- fallback 只能是 `legacy` 或 reader 端显式回退规则，不能再借道 `agent_loop` 伪装当前主视图。

### 7. 本轮明确不做的事项

- 不借本次重构恢复 `agent_loop` 主链功能。
- 不把 `vop_maas` 拆成独立数值优化器。
- 不改变 `mass` 作为数值执行核心的架构事实。
- 不在本轮引入新的 benchmark schema。

### 8. Run Naming v2

- 新 run 目录命名升级为：`experiments/<YYYYMMDD>/<HHMM>_<mode-token>_<short-tag>`。
- 固定 mode token：
  - `mass -> mass`
  - `vop_maas -> vop`
  - `agent_loop -> agent`
- `run_id` 必须跟随 run leaf name 同步带上 mode token。
- `summary.json` / `events/run_manifest.json` 固定补齐：
  - `run_name_mode_token`
  - `run_name_schema_version = 2`
- 历史 run 不做目录改名；reader 必须同时兼容：
  - 旧：`<HHMM>_<level>_<algo>`
  - 新：`<HHMM>_<mode-token>_<level>_<algo>`

### 9. Single Run Log Policy

- 新 run 默认只保留一个日志文件：`run_log.txt`。
- `run_log_debug.txt` 仅作为 legacy reader fallback，不再为新 run 生成。
- `run_log.txt` 使用分层 milestone tag，而不是双文件分流：
  - `[RUN]`
  - `[VOP][BOOTSTRAP]`
  - `[VOP][DECISION]`
  - `[VOP][DELEGATE]`
  - `[VOP][EFFECT]`
  - `[VOP][REPLAN]`
  - `[MASS][A]...[MASS][D]`
- 原始 prompt / response dump 继续保留在 mode-scoped `llm_interactions`，不写入主日志。

### 10. VOP Controller-First Observability + LLM Decision Contract

- `vop_maas` 顶层主消费面改为 `VOP controller-first + delegated mass summary`，不再让 `mass` 成为顶层叙事主体。
- `events/vop_round_events.jsonl -> tables/vop_rounds.csv -> summary.json / report.md / visualization_summary.txt` 成为唯一 canonical VOP round chain。
- `summary.json` / manifest extra 固定补齐：
  - `vop_round_count`
  - `vop_round_audit_table`
  - `vop_round_audit_digest`
  - `vop_policy_primary_round_index`
  - `vop_policy_primary_round_key`
  - `vop_decision_summary`
  - `vop_delegated_effect_summary`
- policy generation / reflective replan 固定采用“三段式控制契约”：
  - `decision_rationale`
  - `change_set`
  - `expected_effects`
- `vop_rounds.csv` 必须补齐 round-level canonical 审计列：
  - `decision_rationale`
  - `change_summary`
  - `expected_effects`
  - `observed_effects`
  - `effectiveness_summary`

### 10.1 Runtime Feature Fingerprint + Chinese Final Summary

- `vop_maas` 新 run 允许在顶层统一消费面中新增：
  - `events/runtime_feature_fingerprint.json`
  - `llm_final_summary_zh.md`
- `runtime_feature_fingerprint.json` 作为 controller-first observability 的导出事实层，用于补齐 `R25` 未覆盖完整的 runtime feature lines，包括：
  - `run_mode vs execution_mode`
  - delegated `mass` 执行关系
  - intent 来源
  - search space / genome
  - proxy vs online COMSOL
  - `MCTS / meta_policy / physics_audit`
  - strict gates
  - VOP controller override
- `llm_final_summary_zh.md` 必须基于结构化 digest / fingerprint 生成，默认以完整任务说明 + 表格化复盘呈现，不得整篇转录原始 prompt / response。
- `report.md` 仅允许内嵌中文总结入口摘要与路径，不镜像全文。

## Artifact Model v2

### Directory layout

```text
<run_dir>/
  summary.json
  report.md
  run_log.txt
  run_log_debug.txt  # legacy fallback only
  mph_models/
  events/
    run_manifest.json
    artifact_index.json
    *.jsonl
  tables/
    release_audit.csv
    vop_rounds.csv
    *.csv
  visualizations/
    *.png
    visualization_summary.txt
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

### Field contract

`summary.json` 与 `events/run_manifest.json` 必须新增或固定以下字段：
- `run_mode`
- `execution_mode`
- `lifecycle_state`
- `artifact_layout_version`
- `artifact_index_path`
- `delegated_execution_mode`
- `optimization_mode`
- `run_name_mode_token`
- `run_name_schema_version`

事件 / 表级统一身份列：
- `producer_mode`
- `execution_mode`
- `lifecycle_state`

说明：
- run-level 必须完整写出 `run_mode / execution_mode / lifecycle_state`；
- 表级可以按需裁剪，但不得依赖目录推断 mode 归属；
- `visualization_summary.txt` 第一行必须精确输出真实 `run_mode`。

## Compatibility / Migration

### Read compatibility

- 历史 run 允许继续保留 layout v1 或根目录 raw file 布局。
- reader 端优先读取 `events/artifact_index.json`。
- 若索引不存在，则按 legacy fallback 顺序查找：
  - 根目录 `mass_trace.csv`
  - 根目录 `evolution_trace.csv`
  - 根目录 `snapshots/`
  - 根目录 `llm_interactions/`

### Batch migrate / rebuild

- 为历史 run 提供 rebuild / migrate 工具，补齐：
  - `artifact_layout_version`
  - `artifact_index.json`
  - `run_mode / execution_mode / lifecycle_state`
- migration 目标是“读兼容 + 增量补齐”，不是强制破坏性切换。
- 在 release gate 通过前，不要求一次性重写全部历史 run。

## Alternatives Considered

### A. 继续保留两态观测模型，只在 `vop_maas` 上增加例外分支

否决原因：
- 会继续把 `run_mode` 与 `execution_mode` 混成隐式规则；
- 共享模块里的 mode 分支会持续膨胀；
- `vop_maas` 的 controller 视角无法成为稳定顶层主视图。

### B. 让 `vop_maas` 直接成为独立数值优化器

否决原因：
- 与当前仓库真实架构不符；
- 会淡化 `mass` 作为数值执行核心的事实；
- 会把 observability 重构扩大成优化架构重构，超出本轮范围。

### C. 破坏性迁移所有历史 run，不保留 legacy reader fallback

否决原因：
- 历史 run 数量多、价值分层不均；
- 会抬高一次性迁移成本并增加审计回归风险；
- 不利于先收敛当前主链实验解释口径。

## Consequences

### Positive

- `mass` / `vop_maas` / `agent_loop(legacy)` 的实验结果边界明确。
- `vop_maas` 可稳定表达 controller 视角，同时保留 delegated `mass` 的原始执行证据。
- 下游工具可从显式字段与 `artifact_index.json` 判断身份，不再依赖目录猜测。
- summary / report / visualization / audit tables 的 mode 语义更一致。

### Negative

- writer / reader / visualization dispatch 需要同步升级，改动面较广。
- 旧脚本里硬编码根目录 raw file 的逻辑需要逐步迁移。
- 历史 run 会在一段时间内并存 v1 / v2 两种布局，需要 fallback 与 migration 双维护。

### Constraints

- 不得把 `vop_maas` 的 delegated `mass` 证据误写回顶层 `mass` 语义。
- 不得以“兼容”为由保留 `agent_loop` 作为默认观测 fallback。
- 不得声称 `artifact_layout_version=2` 之外的能力已经全部迁移完成，除非对应 writer / reader / tests 已落地。

## Rollout / Phase Gates

### Phase 1 — Identity contract + artifact layout + summary/manifest/report 对齐
- 目标：引入 `run_mode / execution_mode / lifecycle_state / artifact_layout_version`
- gate：`summary.json`、`run_manifest.json`、`report.md` 输出与顶层 run 身份一致

### Phase 2 — Raw artifact writer 改造
- 目标：`logger / llm store / trace layout` 改为 mode-scoped writer
- gate：`mass` 不再默认创建 legacy `evolution_trace.csv`；`vop_maas` 的 delegated evidence 落入明确子域

### Phase 3 — Visualization dispatch 三态拆分
- 目标：`mass`、`vop_maas`、`agent_loop(legacy)` 各自走独立 dispatch
- gate：`visualization_summary.txt` 首行、图表类型与 storyboard 内容和真实 `run_mode` 一致

### Phase 4 — Reader fallback + migration/rebuild tool
- 目标：reader 优先读 index，历史 run 仍可读；提供 rebuild / migrate 工具
- gate：`audit_release_summary`、Blender bundle、CLI/API 都能读 v2 与 legacy run

### Phase 5 — Tests + docs sync + release gate
- 目标：补齐回归测试、README/HANDOFF/报告同步
- gate：目标测试通过，文档可独立指导后续推进

## Acceptance Criteria

- 新 `vop_maas` run 的 `summary.json` / `run_manifest.json` 中：
  - `run_mode = vop_maas`
  - `execution_mode = mass`
  - `lifecycle_state = experimental`
- 新 `mass` run 不再生成 legacy 根目录 `evolution_trace.csv`。
- 新 `vop_maas` run 不再生成 `llm_interactions/agent_loop` 作为默认写入目标。
- `events/artifact_index.json` 能完整索引 raw artifacts。
- `visualization_summary.txt` 第一行精确输出真实 `run_mode`。
- `mass` 只展示 `mass` 当前真实执行链；`vop_maas` 顶层展示 controller 视角并附带 delegated mass 摘要。
- 历史 run 在未迁移前仍可通过 fallback reader 被 `audit_release_summary` 和 Blender bundle 读取。
- rebuild / migrate 后，历史 run 可补齐 layout v2 必需字段与索引。
