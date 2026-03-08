# Phase 1 Identity Contract Checkpoint

- phase: Phase 1
- date: 2026-03-08
- owner: msgalaxy-core
- based_on: `docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md`

## Scope

- 本阶段目标：完成 `run_mode / execution_mode / lifecycle_state / artifact_layout_version` 的基础身份契约，并让 `summary.json`、`events/run_manifest.json`、`report.md` 与顶层 run 身份对齐。
- 实际覆盖范围：除 Phase 1 基础契约外，还完成了与身份契约强绑定的 raw artifact index、mode-scoped writer 薄切片、visualization dispatch 三态收口，以及历史 `mass` run 的 rebuild 验证样本。

## Completed

- `core/mode_contract.py` 已从“两态 observability”升级为“三态 run identity + explicit execution identity`：
  - `run_mode` 支持 `agent_loop / mass / vop_maas`
  - `resolve_execution_mode(vop_maas) -> mass`
  - `resolve_lifecycle_state(agent_loop|mass|vop_maas) -> deprecated|stable|experimental`
- `core/logger.py` 已写出 run-level identity 字段：
  - `run_mode`
  - `execution_mode`
  - `lifecycle_state`
  - `artifact_layout_version`
  - `artifact_index_path`
  - `delegated_execution_mode`
- `events/artifact_index.json` 已成为新 run raw artifact 的 canonical 索引，并由 logger 在 run 初始化时写出。
- `vop_maas` 顶层 run 目录已保持 `run_mode=vop_maas`，不再降级为 `unknown/shared/agent_loop`。
- `summary.json`、`events/run_manifest.json`、`report.md` 已与新身份契约对齐。
- `optimization/modes/mass/observability/materialize.py` 已补齐 run-level / table-level identity 字段，并保持既有 join-key 前缀兼容。
- 文档已同步：
  - `docs/adr/0008-mode-scoped-experiment-observability-v2.md`
  - `docs/reports/R32_mode_scoped_experiment_observability_refactor_20260308.md`
  - `HANDOFF.md`
  - `README.md`

## Deviations

- post-checkpoint deviations / next gate：
  - 发现新 run 目录名仍可能缺少 mode token，需要补 `Run Naming v2`
  - 发现双日志策略未真正分层，需要收口到单 `run_log.txt`
  - 发现 `tables/vop_rounds.csv` 与顶层 digest 仍可能断链，需要补 canonical round audit 回填
  - 第一版 `llm_final_summary_zh.md` 暴露出“目标摘要过度截断 + runtime feature lines 缺少统一表格呈现”的问题；后续以 `events/runtime_feature_fingerprint.json` 和 `R38_runtime_feature_fingerprint_summary_upgrade_20260308.md` 收口

- 严格按 master plan，Phase 1 只要求 identity contract + summary/manifest/report 对齐；但本次实现为避免字段落地后继续被旧 writer/reader 污染，提前落了以下 thin-slice：
  - mode-scoped raw artifact writer
  - `artifact_index.json` reader 优先读取
  - visualization 三态 dispatch 基础收口
- 这属于“为保证 Phase 1 契约可持续成立”的前置收口，不改变 ADR 的决策边界。

## Risks

- 仓库当前仍存在部分工作区脏改动与其他未合并文档草稿；后续合并时需要按本次重构文件单独审查。
- 旧 run 的 batch migrate 当前采取“复制 legacy raw artifacts 到 v2 namespace，同时保留旧路径”的安全策略，磁盘占用会增加。
- 文档编号存在潜在冲突风险：仓库中已有其他 `0008` / `R32` 草稿命名，需要后续治理。

## Validation

### 代码与单测

- 已通过目标回归：
  - `conda run -n msgalaxy python -m pytest tests/test_llm_interaction_store.py tests/test_visualization_mode_dispatch.py tests/test_experiment_logger_naming.py tests/test_event_logger.py tests/test_release_audit_tools.py tests/test_vop_maas_mode.py tests/test_maas_pipeline.py tests/test_path_hygiene.py -q`
  - 结果：`78 passed`

### 历史 run rebuild 样本

- 已选取 4 条典型 legacy `mass` run：
  - `experiments/0307/0141_l1_nsga3`
  - `experiments/0307/0209_l2_nsga3`
  - `experiments/0307/1646_l3_nsga3`
  - `experiments/0307/1708_l4_nsga3`
- 迁移前共性：
  - 无 `events/artifact_index.json`
  - 根目录存在 `mass_trace.csv`、`evolution_trace.csv`、`llm_interactions/`、`snapshots/`
  - `summary.json` 缺失 `execution_mode` 与 `artifact_layout_version`
- 已执行：
  - `conda run -n msgalaxy python run/mass/tool_rebuild_run_artifacts.py ... --skip-visualizations --json`
- 迁移后确认：
  - `summary.json` / `run_manifest.json` 补齐 `execution_mode=mass`、`lifecycle_state=stable`、`artifact_layout_version=2`
  - `events/artifact_index.json` 已生成
  - legacy raw artifacts 已物化到：
    - `artifacts/mass/mass_trace.csv`
    - `artifacts/mass/snapshots/`
    - `artifacts/mass/step_files/`
    - `artifacts/mass/llm_interactions/`
    - `artifacts/agent_loop/evolution_trace.csv`
  - `run/mass/audit_release_summary.py` 能稳定读取 rebuild 后样本

## Next Gate

- 补完 `0008` / `R32` 的 run naming、single log、VOP controller-first addendum，并以此驱动后续 phase

- 下一阶段入口条件：
  - 以本 checkpoint 为基线，继续推进 Phase 2/3 的 writer layout 与 visualization dispatch 收口
  - 对更多历史 run 分层做 rebuild/migrate 抽样验证
  - 在 release-grade 证据 run 上优先完成 migration checklist
