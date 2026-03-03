# MsGalaxy HANDOFF

**文档级别**: 核心交接文档（Single Source of Truth）  
**最后更新**: 2026-03-04 01:08 (Asia/Shanghai)  
**当前版本**: v2.4.0-observability-layout-timeline-rag-hygiene  
**状态说明**: `pymoo_maas` 基线稳定；OP-MaaS v2 继续推进；已完成 `NSGA-II/NSGA-III/MOEAD` 统一求解入口并可在 L4-L7 基准脚手架中同框对照

---


### 0.1 自治心跳日志

<!-- AUTOPILOT_HEARTBEAT_START -->
- 2026-03-03 02:45 (Asia/Shanghai): 已接管8小时自治执行；开始第1阶段（契约落库 + 指标口径修复 + L1-L7矩阵扩展）。
- 2026-03-03 02:55 (Asia/Shanghai): 用户确认“同意执行8小时计划”；已完成规则与仓库状态同步，进入第2阶段（高预算多seed矩阵验证 + OP-MaaS v2迭代修复）。
- 2026-03-03 03:11 (Asia/Shanghai): 完成 `L4/L7 × (baseline/meta_policy/operator_program/multi_fidelity) × (nsga2/nsga3/moead) × seeds(42,43,44)`（72 runs，simplified+proxy）正式批次；发现 L7 在旧 proxy 下全不可行且主导违规为 `g_temp`。
- 2026-03-03 03:17 (Asia/Shanghai): 已完成“热代理布局敏感化”修复（统一接入 `simulation/physics_engine.py` 到 orchestrator/runtime proxy 与 problem generator fallback），并补充 `summary.json` 关键诊断字段（`search_space/dominant_violation/violation_breakdown/best_candidate_metrics/operator_bias/operator_credit_snapshot`）。
- 2026-03-03 03:33 (Asia/Shanghai): 基准脚本 `baseline` profile 重新定义为“纯算法对照”（禁用 `MCTS/auto_relax/retry_on_stall`），用于对比“传统算法单跑”与 OP-MaaS 框架增强效果。
- 2026-03-03 03:51 (Asia/Shanghai): 完成新口径矩阵 `L4/L7 × 4 profiles × 3 algorithms × seeds(42,43)`（48 runs，tag=`opv2_algo_l4_l7_seed42_43_r5_baselinepure`）；结果显示 baseline 在 L7 全不可行，而 OP-MaaS profile 在 L7 全可行，差异可归因。
- 2026-03-03 12:06 (Asia/Shanghai): 完成 algorithm-aware meta policy 落地（`meta_policy_v3_algo_aware`），将 `pymoo_algorithm/search_space_mode` 注入策略输入并增加 `NSGA-II/NSGA-III/MOEAD` 条件规则；相关回归 `40 passed`。
- 2026-03-03 12:06 (Asia/Shanghai): 完成验证矩阵 `docs/benchmarks/pymoo_maas_benchmark_opv2_algoaware_l4_l7_seed42_43_r6/`（36 runs，三算法 × L4/L7 × meta/operator/multi_fidelity），结果与 r5 主结论一致且策略动作已呈算法差异化。
- 2026-03-03 12:30 (Asia/Shanghai): 完成 `meta_policy + nsga2 + L4` 定向复测（`docs/benchmarks/pymoo_maas_benchmark_opv2_algoaware_nsga2_l4_r7/`，2 seeds）；`feasible_ratio: 0.0 -> 0.5`，`best_cv_min_mean: 0.9675 -> 0.2474`（相对 r6）。
- 2026-03-03 17:38 (Asia/Shanghai): 启动并完成可观测性 Phase-1（事件层双写）：新增 `events/run_manifest.json` 与 `phase/attempt/policy/physics/candidate` 五类 `jsonl`，`core/logger.py` 保持原 `csv/summary` 兼容并同步结构化事件；`workflow/maas_pipeline_service.py` 已接入 phase/policy/physics/run-manifest 钩子；新增回归 `tests/test_event_logger.py` 通过。
- 2026-03-03 18:14 (Asia/Shanghai): 完成可观测性 Phase-2（代际收敛日志）：`runner` 每代输出 `generation_records`，并在 `orchestrator` attempt 评估阶段写入 `events/generation_events.jsonl`；回归通过（`tests/test_event_logger.py` + `tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema`）。
- 2026-03-03 18:20 (Asia/Shanghai): 完成可观测性 Phase-3（事件表物化）：`optimization/observability/materialize.py` 已接入 `pymoo_maas` 收尾流程，自动生成 `tables/*.csv`；`final_state.metadata/run_manifest/summary.json` 均回填 `observability_tables` 计数；回归通过（`tests/test_event_logger.py`、`tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema`、`tests/test_runner_multi_algorithms.py`、`tests/test_runner_operator_bias.py`）。
- 2026-03-03 18:27 (Asia/Shanghai): 完成可观测性 Phase-4（看板落地）：`core/visualization.py` 新增 `pymoo_maas_storyboard`（单次 run 四宫格）与 tables 摘要拼接；新增 `run/render_pymoo_maas_benchmark_dashboard.py` 读取 `matrix_runs.csv + matrix_aggregate_profile_level.csv` 生成三张矩阵看板与 `dashboard_summary.md`；新增回归 `tests/test_visualization_storyboard.py`、`tests/test_benchmark_dashboard.py` 通过。
- 2026-03-03 21:15 (Asia/Shanghai): 完成 matrix 脚本自动看板集成：`run/run_pymoo_maas_benchmark_matrix.py` 现默认渲染 dashboard（可用 `--skip-dashboard` 关闭，`--dashboard-output-dir` 定向输出），`matrix_report.md` 自动追加看板产物索引；新增回归 `tests/test_pymoo_maas_benchmark_matrix.py`（dashboard enabled/skip 分支）通过。
- 2026-03-03 21:19 (Asia/Shanghai): 完成 `best_cv_min` 口径一致性修复：`workflow/maas_pipeline_service.py` 新增 `best_cv_min` 多级回填（`trace_features -> execution_best_cv_curve -> solver_diagnosis -> attempt_payload -> feasible_inferred_zero`），并输出 `best_cv_min_source`；新增回归 `tests/test_maas_pipeline.py::test_pymoo_maas_best_cv_min_fallback_uses_execution_curve` 通过，矩阵聚合中 `best_cv_min=null` 概率显著下降。
- 2026-03-03 21:21 (Asia/Shanghai): 完成矩阵层 CV 覆盖度统计：`run/run_pymoo_maas_benchmark_matrix.py` 新增行级字段 `best_cv_min_source`，聚合新增 `best_cv_valid_count/best_cv_missing_count/best_cv_missing_ratio`，`matrix_report.md` 聚合表新增 `best_cv_missing` 列；新增回归 `tests/test_pymoo_maas_benchmark_matrix.py::test_aggregate_rows_tracks_best_cv_missing_ratio` 通过。
- 2026-03-03 21:38 (Asia/Shanghai): 完成 `L7 + real COMSOL + pymoo_maas` 实测确认：`docs/benchmarks/pymoo_maas_benchmark_l7_real_comsol_feasible_check_20260303/` 中 `operator_program + nsga2 + seed42` 达到 `status=SUCCESS, diagnosis=feasible, feasible_ratio=1.0`（run: `experiments/run_20260303_212443`）；同批 `multi_fidelity` 为 `PARTIAL_SUCCESS`（`audit_no_feasible_candidate`）。
- 2026-03-03 21:56 (Asia/Shanghai): 完成 `L7 + real COMSOL` 三算法补齐中的 `nsga3/moead` 实测：`docs/benchmarks/pymoo_maas_benchmark_l7_real_comsol_nsga3_moead_check_20260303/` 显示 `operator_program + nsga3` 达 `SUCCESS/feasible`，`operator_program + moead` 为 `PARTIAL_SUCCESS/no_feasible`（`audit_no_feasible_candidate`）。
- 2026-03-03 23:33 (Asia/Shanghai): 完成 `L8 + real COMSOL + operator_program + seed42` 三算法独立实测：`nsga2/nsga3/moead` 均为 `PARTIAL_SUCCESS + diagnosis=no_feasible + reason=audit_no_feasible_candidate`；`best_cv_min` 分别为 `2.6562/2.6562/2.1362`，主导违规均为 `g_cg`（对应 run: `run_20260303_220354`, `run_20260303_230348`, `run_20260303_231805`）。
- 2026-03-04 00:08 (Asia/Shanghai): 完成可观测性 Phase-5（布局时间线）：新增 `layout_events/snapshots` 逐帧渲染链路，`core/visualization.py` 现可输出 `visualizations/timeline_frames/*.png + layout_timeline.gif + layout_timeline_summary.txt`，并将 `layout_timeline.csv` 纳入 tables 摘要与故事板漏斗；新增回归 `tests/test_event_logger.py::test_layout_snapshot_writes_snapshot_and_event`、`tests/test_visualization_storyboard.py::test_plot_layout_timeline_from_snapshots` 通过。
- 2026-03-04 00:08 (Asia/Shanghai): 完成知识污染治理与最终 `.mph` 保留：`RAGSystem` 新增 anomaly 过滤（默认剔除 999/9999°C 失效案例与异常 max_temp 改变量样本）；`ComsolDriver` 新增 `force_save_current_model()` 并在 MaaS 收尾强制保存 `final_selected` 对应 `.mph`（路径回填 `summary.json/final_state.metadata/run_manifest` 的 `final_mph_path`）。
- 2026-03-04 01:08 (Asia/Shanghai): 完成 `L7 + real COMSOL + operator_program + nsga3 + seed42` 单组实测（`docs/benchmarks/pymoo_maas_benchmark_l7_nsga3_real_single_20260304/`）；结果 `status=SUCCESS, diagnosis=feasible, feasible_ratio=1.0, best_cv_min=0.0, first_feasible_eval=1, comsol_calls_to_first_feasible=24`，run 目录为 `experiments/run_20260304_005824`，已产出 `layout_timeline.gif/pymoo_maas_storyboard.png` 且 `final_mph_path` 有效落盘。
- 2026-03-04 00:53 (Asia/Shanghai): 完成 `Truth-First EvolViz v2` 落地：`core/visualization.py` 新增 `plot_layout_evolution_from_snapshots()` 并在 `pymoo_maas` 下改为 `events/snapshots` 真值口径（不再依赖 `design_state_iter_*` 首尾）；新增三口径位移输出（`initial->best`, `best->final`, `frame-to-frame`）与 `zero_reason` 解释；`optimization/observability/materialize.py` 新增 `tables/layout_deltas.csv`；`workflow/maas_pipeline_service.py` 新增 `layout_state_hash/duplicate_with_previous_snapshot` 诊断透传到 attempt/summary；回归 `6 passed`（`tests/test_visualization_storyboard.py`, `tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema`, `tests/test_event_logger.py`）。
- 2026-03-04 00:30 (Asia/Shanghai): 完成 `L7 + real COMSOL + operator_program + nsga2 + seed42` 单组实测（`docs/benchmarks/pymoo_maas_benchmark_l7_nsga2_real_single_20260304/`）；结果 `status=SUCCESS, diagnosis=feasible, feasible_ratio=1.0, best_cv_min=0.0, first_feasible_eval=1, comsol_calls_to_first_feasible=24`，run 目录为 `experiments/run_20260304_002234`，已产出 `layout_timeline.gif/pymoo_maas_storyboard.png` 且 `final_mph_path` 有效落盘。
- 2026-03-04 00:06 (Asia/Shanghai): 完成 smoke 复测 `experiments/run_20260304_000454`：`layout_events=5`，可视化已产出 `timeline_frames/` 与 `layout_timeline.gif`，`visualization_summary.txt` 已追加时间线产物索引；日志显示 anomaly 过滤已拦截 `K039/K040`，`summary.json` 已稳定包含 `final_mph_path` 字段。
<!-- AUTOPILOT_HEARTBEAT_END -->

---

## 1. 当前状态（真实执行基线）

系统当前支持两条可运行主链路：

- `optimization.mode = "agent_loop"`：原有 Multi-Agent + 物理评估优化循环。
- `optimization.mode = "pymoo_maas"`：新增 Neuro-symbolic MaaS 闭环（LLM 建模 + pymoo 求解 + 反射重试）。

关键结论：

- `pymoo_maas` 已从“初步适配”进入“可运行闭环”阶段。
- `P0/P1/P2` 已修复：热源绑定歧义、COMSOL 过载与保存冲突、模式化 trace/可视化。
- `P1` 已继续推进：`online_comsol` 增加几何门控统计，避免明显几何不可行候选浪费高保真预算。
- `P1` 新增不确定性感知多保真调度（`pymoo_maas_online_comsol_schedule_mode=ucb_topk`）：默认保持 `budget_only` 兼容模式，可切换为“proxy 全量筛选 + top-fraction/UCB 触发 COMSOL”。
- `P1` 新增运行期调度自适应：`meta_policy_v3_algo_aware` 已可在回路内动态调参 `online_comsol_schedule_mode/top_fraction/explore_prob/uncertainty_weight`，并按 `nsga2/nsga3/moead + search_space_mode` 做差异化策略更新。
- `P2` 已继续推进：`run_log.txt` 引入噪声过滤并新增 `run_log_debug.txt` 完整日志分流。
- 新增 `maas_trace_features`：自动汇总可行率、CV 趋势、COMSOL 调用效率、Top-K 物理审计通过率。
- `maas_trace_features` 新增基线口径：`first_feasible_eval`、`comsol_calls_to_first_feasible`，并同步写入 `summary.json`。
- `R2` 评估脚手架已补齐：新增 `run/run_pymoo_maas_benchmark_matrix.py`，支持 `profile x level x seed` 批量运行并自动产出 `jsonl/csv/markdown` 对照报告。
- 新增 `meta_policy`：基于 trace 特征生成调参动作，并输出下一轮策略建议；非 MCTS retry 与 MCTS 路径均支持当轮应用。
- 核心回归已通过，最新验证为 `21 passed`。
- 默认模型继续保持 `qwen3-max`，未回退到 OpenAI 旧模型默认值。
- 2026-03-02 热修复：`disable_semantic` 已联动关闭 MaaS 编译期自动语义分区（新增 `optimization.pymoo_maas_enable_semantic_zones`），避免“语义开关未生效”导致搜索域被意外收缩。
- 2026-03-02 热修复：`run/run_L4_extreme.py` 默认 deterministic 边界扩大（`ratio=0.45/min=20mm/max=220mm`），修复 L4 初始偏置下难以达成 CG 约束的问题。
- 最新实测：`experiments/run_20260302_180812`（`pymoo_maas + online_comsol`）达到 `diagnosis=feasible`，`best_cv_min=0.0`，`physics_pass_rate_topk=1.0`。
- 2026-03-02 L8 瓶颈定位：`config/bom_L8_40components.json`（40 组件）在默认 NSGA-II 随机初始化下持续 `no_feasible`，主要违规由 `cg_offset` 主导，热约束非主瓶颈。
- 2026-03-02 求解器增强：`PymooNSGA2Runner` 新增初始种群注入（warm-start），将当前可解码布局向量注入 NSGA-II 首批个体；L8 `best_cv_min` 从 `36.74` 降至 `12.44`，但仍未达可行。
- 2026-03-02 L8 强化（本轮）：新增多种子注入（整体平移 + 重组件定向）与 attempt 级主导违规分解（`dominant_violation` / `constraint_violation_breakdown` / `best_candidate_metrics`），并接入 trace+meta policy。
- 2026-03-02 L8 强化（本轮）：`CentroidPushApartRepair` 新增 CG 回中平移修复，且平移限幅同时受包络边界与变量边界约束，避免 clip 扭曲导致几何退化。
- 最新实测（online_comsol + no-audit）：`experiments/run_20260302_194038`，`best_cv_min=9.716`（较 12.44 继续下降），主导违规仍为 `g_cg`，尚未达到 `cv=0`。
- 2026-03-02 对比结论：L7（32组件）已达 `diagnosis=feasible`，L8（40组件）仍 `diagnosis=no_feasible`，说明当前 “连续坐标搜索 + 规则反射” 在高维强约束场景存在瓶颈。
- 2026-03-02 战略决议（已确认）：下一阶段引入 OP-MaaS，将 LLM 角色从“建模文本生成”升级为“算子程序/策略程序生成与搜索控制”，与 NSGA-II + 多保真物理评估形成三层闭环。
- 核心回归更新：`33 passed`（`tests/test_maas_core.py tests/test_maas_pipeline.py tests/test_maas_mcts.py`）。
- 2026-03-02 OP-MaaS R1 继续落地：MCTS 节点评估 payload 已透传 `dominant_violation / constraint_violation_breakdown / best_candidate_metrics`，避免算子程序分支退化为 default 模板。
- 2026-03-02 OP-MaaS R1 继续落地：新增 `OperatorProgramGenomeCodec` + `OperatorProgramProblemGenerator`，支持 `optimization.pymoo_maas_search_space = coordinate | operator_program | hybrid`。
- 核心回归更新：`52 passed`（`tests/test_operator_program_core.py tests/test_operator_program.py tests/test_runner_operator_bias.py tests/test_maas_mcts.py tests/test_maas_core.py tests/test_maas_pipeline.py`）。
- 2026-03-03 OP-MaaS R1 强化：`operator_program` 搜索空间新增 codec 级种子群注入（`build_seed_population`，operator 模式 `initial_population_injected` 从 1 提升到 5）；`cg_recenter` 语义升级为“刚体平移式重心回中”（保持相对布局，降低碰撞回滚）。
- 2026-03-03 L4 实测（`real_comsol_l4_opcore_2seeds_fix3`）：`operator_program` 从 `feasible_ratio=0.0` 提升到 `1.0`，`best_cv_min_mean=0.0`，`first_feasible_eval_mean=1.0`；`hybrid` 同步提升到 `feasible_ratio=1.0`。
- 2026-03-03 OP-MaaS v2 薄切片落地（R2 pre-alpha）：`OperatorProgramGenomeCodec.encode()` 升级为状态感知编码（不再恒 neutral）；MCTS attempt 评分新增 `first_feasible_bonus / comsol_efficiency_bonus / solver_cost_penalty` 并写入 `score_breakdown`；新增 `operator_credit_table`（按 action 聚合 `count/mean_score/feasible_rate/best_cv`）驱动 `operator_bias` 动态调参。
- 2026-03-03 快速实测（`docs/benchmarks/pymoo_maas_benchmark_opv2_slice1/`，`operator_program x L4 x seed42 x real COMSOL`）：`feasible_ratio=1.0`，`best_cv_min_mean=0.0`，`first_feasible_eval_mean=1.0`，`comsol_calls_to_first_feasible_mean=24.0`。
- 2026-03-03 R2 严格消融（`docs/benchmarks/pymoo_maas_benchmark_opv2_ablation_l4_s2_quick/`，real COMSOL，L4，2 seeds）：`baseline feasible_ratio=0.5 / best_cv_min_mean=3.1436`；`operator_program` 与 `seed_off`、`credit_off` 均为 `feasible_ratio=1.0 / best_cv_min_mean=0.0 / first_feasible_eval_mean=1.0`。
- 2026-03-03 机制诊断：当前 L4 场景下 `operator_program` 分支虽存在，但 `best_action` 长期为 `identity_d1`；`credit_off` 与默认无显著差异的主因是“早可行 + 评分偏置 + MCTS rollout 较短”，而非 credit 机制未接通（`operator_bias.credit_bias_enabled` 已可控）。
- 2026-03-03 多算法求解器落地：`optimization.pymoo_algorithm = nsga2 | nsga3 | moead`，编排层不再硬编码 `PymooNSGA2Runner`，改为统一工厂按配置选择。
- 2026-03-03 `PymooNSGA2Runner` 升级为统一 runner：新增 `PymooNSGA3Runner` / `PymooMOEADRunner` 封装，执行元数据新增 `algorithm_requested/algorithm/algorithm_fallback_reason/algorithm_parameters`。
- 2026-03-03 MOEA/D 约束适配：针对 pymoo 基线中 MOEA/D 不支持约束的问题，新增 `_ConstraintPenaltyProblem`（约束惩罚目标化 + `raw_cv` 诊断回传），保证 `g(x)<=0` 语义下可运行并可监控 CV。
- 2026-03-03 矩阵评测脚本升级：`run/run_pymoo_maas_benchmark_matrix.py` 扩展为 `profile x algorithm x level x seed`，支持 `--algorithms nsga2,nsga3,moead`。
- 2026-03-03 冒烟验证：`docs/benchmarks/pymoo_maas_benchmark_algo_matrix_exec_smoke/` 已生成三算法同场报告；对应 `summary.json` 已记录 `pymoo_algorithm` 字段。
- 2026-03-03 L4/L7 快速矩阵（`docs/benchmarks/pymoo_maas_benchmark_opv2_algo_l4_l7_seed42_quick/`，`baseline vs operator_program`，`nsga2/nsga3/moead`，simplified/proxy，低预算）已完成：当前仅 `operator_program + moead + L4` 达到 `feasible_ratio=1.0`（`first_feasible_eval=1`）；其余组合在该低预算设置下仍 `no_feasible`，需扩大 `pop_size/n_gen/seeds` 做正式结论。
- 核心回归更新：`19 passed`（`tests/test_runner_operator_bias.py tests/test_runner_multi_algorithms.py tests/test_operator_program_core.py tests/test_pymoo_maas_benchmark_matrix.py`）。
- 2026-03-03 热代理升级：`simulation/physics_engine.py` 新增布局敏感 thermal proxy（wall cooling + hotspot compaction + spread），并在 `workflow/orchestrator.py` 与 `optimization/pymoo_integration/problem_generator.py` 统一复用，修复“proxy 对布局不敏感导致 L7 温度常数化”的评测偏差。
- 2026-03-03 可观测性补强：`summary.json` 现已稳定输出 `search_space/dominant_violation/constraint_violation_breakdown/best_candidate_metrics/operator_bias/operator_credit_snapshot`，矩阵结果可直接做失败归因。
- 2026-03-03 可观测性 Phase-1：新增结构化事件层 `events/`（`run_manifest.json` + `phase_events.jsonl` + `attempt_events.jsonl` + `policy_events.jsonl` + `physics_events.jsonl` + `candidate_events.jsonl`）；保持 `evolution_trace.csv/pymoo_maas_trace.csv/summary.json` 原路径不变，实现双写兼容。
- 2026-03-03 可观测性 Phase-2：新增 `events/generation_events.jsonl`，记录每代 `population_size/feasible_ratio/best_cv/mean_cv/best_feasible_sum_f`，用于跨算法收敛行为对比与后续 dashboard 构建。
- 2026-03-03 可观测性 Phase-3：新增 `tables/` 物化层（`attempts/generations/policy_tuning/physics_budget/candidates/phases.csv`），由 `materialize_observability_tables(run_dir)` 从 `events/*.jsonl` 自动转换，并写回 `summary.json` 与 `final_state.metadata["observability_tables"]`。
- 2026-03-03 可观测性 Phase-4：新增运行内故事板 `visualizations/pymoo_maas_storyboard.png` 与矩阵级看板脚本 `run/render_pymoo_maas_benchmark_dashboard.py`，支持“单次运行诊断”+“多配置对比”的统一可视化产物。
- 2026-03-03 可观测性 Phase-4.1：矩阵执行脚本已原生接入 dashboard 后处理，基准批跑结束可直接得到 `matrix_*.csv/md + dashboard_*.png + dashboard_summary.md` 一套可发布产物。
- 2026-03-03 指标口径修复：`summary.json` 新增 `best_cv_min_source`，用于标记 `best_cv_min` 来源，解决跨算法对照时 `best_cv_min` 缺失难以归因的问题。
- 2026-03-03 指标覆盖观测：matrix 聚合新增 `best_cv_missing_ratio`，可直接评估不同 `profile x algorithm x level` 的 CV 指标完备度。
- 2026-03-03 真实 COMSOL 复核：`L7` 在当前代码基线上已可求出可行解（`operator_program + nsga2` 实测成功），并已自动产出 run 内故事板与矩阵 dashboard。
- 2026-03-03 真实 COMSOL 复核（新增）：`L7` 三算法中 `nsga2/nsga3` 可行、`moead` 在该配置下最终未通过审计；`L8` 三算法（`nsga2/nsga3/moead`）在 `operator_program + seed42` 下均未达可行，主导瓶颈为 `g_cg`。
- 2026-03-03 基准口径重定义：`baseline` profile 现在显式禁用 `MCTS/auto_relax/retry_on_stall`，用于“传统算法单跑”对照；`meta_policy/operator_program/multi_fidelity` 保留 OP-MaaS 控制闭环。
- 2026-03-03 正式矩阵（`docs/benchmarks/pymoo_maas_benchmark_opv2_algo_l4_l7_seed42_43_r5_baselinepure/`，48 runs）结论：
  - `L7`: baseline（三算法）`feasible_ratio=0.0`，而 `meta_policy/operator_program/multi_fidelity` 三算法均 `feasible_ratio=1.0`。
  - `L4`: baseline 中 `nsga2/nsga3` 仍不稳定（`feasible_ratio=0.0`），`operator_program/multi_fidelity` 三算法均 `feasible_ratio=1.0`，体现框架增强收益。
- 2026-03-03 已输出差异摘要：`docs/benchmarks/pymoo_maas_benchmark_opv2_algo_l4_l7_seed42_43_r5_baselinepure/baseline_vs_framework.md`。
- 2026-03-03 策略升级：`optimization/meta_policy.py` 进入 `meta_policy_v3_algo_aware`，新增算法感知规则（`nsga2/nsga3/moead`）与搜索空间感知 prior floor（`operator_program/hybrid`）。
- 2026-03-03 策略上下文注入：`workflow/maas_pipeline_service.py` 将 `pymoo_algorithm/search_space_mode` 注入到每轮 runtime meta policy 与 next-run recommendation 输入，避免“跨算法同一套调参”。
- 2026-03-03 核心回归更新：`40 passed`（`tests/test_maas_core.py tests/test_maas_pipeline.py`，含新增算法感知 meta policy 用例）。
- 2026-03-03 新矩阵（`docs/benchmarks/pymoo_maas_benchmark_opv2_algoaware_l4_l7_seed42_43_r6/`）显示：
  - `meta_policy`: `nsga2@L4` 仍未完全可行（`0/2`），`nsga3/moead` 在 `L4/L7` 均可行；
  - `operator_program/multi_fidelity`: 三算法在 `L4/L7` 均保持 `feasible_ratio=1.0`，说明 OP-MaaS v2 主路径仍稳定。
- 2026-03-03 定向提升：`meta_policy + nsga2 + L4` 在 r7 已提升到 `feasible_ratio=0.5`（`1/2`），并将 `best_cv_min_mean` 进一步降至 `0.2474`；对照文件：`docs/benchmarks/pymoo_maas_benchmark_opv2_algoaware_nsga2_l4_r7/r6_to_r7_delta.md`。

---

## 2. 本轮升级总览（v2.1.0）

### 2.1 A/B/C/D 神经符号闭环（已落地）

在 `pymoo_maas` 模式中，完整执行：

- A: Understanding  
  - 生成 `ModelingIntent`（变量、目标、硬/软约束、假设）
  - `validate_modeling_intent()` 校验建模可执行性与冲突
- B: Formulation  
  - `formulate_modeling_intent()` 进行约束标准化（`g(x) <= 0` 语义）
- C: Coding/Execution  
  - `compile_intent_to_problem_spec()` 生成问题规格
  - `PymooProblemGenerator` 构造 `ElementwiseProblem`
  - `PymooNSGA2Runner` 执行多目标搜索，输出 Pareto + AOCC + CV 曲线
- D: Reflection  
  - `diagnose_solver_outcome()` 诊断可行性状态
  - `suggest_constraint_relaxation()` 生成松弛建议并自动重试

### 2.2 约束、物理与审计增强（已落地）

- 运行时热评估支持：
  - `proxy`（默认）
  - `online_comsol`（通过 `_evaluate_design` 包装 + 缓存）
- Top-K 物理审计：
  - 对 Pareto 候选进行二次物理筛选（当前非 COMSOL backend 自动跳过）
- 语义分区增强：
  - 可解析 `assumptions` 中的 `zone:*` 字符串和 JSON 配置
  - 缺失时按热风险自动推断分区
- 混合变量支持：
  - `continuous / integer / binary`
  - 编码器解码时自动离散化（round / threshold）

### 2.3 MCTS 路径搜索增强（已落地）

`MaaSMCTSPlanner` 已支持：

- UCT 选择
- 分支剪枝（`prune_margin`）
- 停滞早停（`stagnation_rounds`）
- 分支级统计（`branch_stats`）
- 剪枝计数（`pruning_events`）
- 动作先验策略（优先级3实现）
  - 基于历史动作得分与 CV 的先验打分
  - 影响节点选择与扩展排序
  - 统计输出 `action_stats`

---

## 3. 优先级 2/3 结果（本对话新增）

### 3.1 优先级 2：流程服务化拆分（已完成）

- 新增 `workflow/maas_pipeline_service.py`
  - 将 `_run_pymoo_maas_pipeline` 大体量主流程从 `WorkflowOrchestrator` 抽离
  - `WorkflowOrchestrator` 只保留委托入口
- 目的：
  - 降低 Orchestrator 复杂度
  - 提高 MaaS 子系统可维护性与可测试性

### 3.2 优先级 3：分支级策略打分（已完成）

在 `optimization/maas_mcts.py` 新增：

- `action_prior_weight`
- `cv_penalty_weight`
- 动作归一化与历史反馈统计
- `selected_action_prior` 逐 rollout 记录
- `action_stats` 结构化导出

在 `config/system.yaml` 新增配置：

- `pymoo_maas_mcts_action_prior_weight`
- `pymoo_maas_mcts_cv_penalty_weight`

### 3.3 Trace Features 抽取（本轮新增）

- 新增 `optimization/trace_features.py`
  - `extract_maas_trace_features(...)` 从 `maas_attempts` + 运行时热评估统计 + 物理审计结果提取结构化特征。
  - 关键特征：`feasible_rate`、`feasible_rate_recent`、`best_cv_min`、`best_cv_slope`、`cv_decay_rate`、`comsol_calls_per_feasible_attempt`、`physics_pass_rate_topk`。
  - 新增 `alerts`（例如 `geometry_infeasible_ratio_high`、`feasible_rate_low`）作为下一步 `meta_policy` 的直接输入。
- `workflow/maas_pipeline_service.py`
  - 每轮结束自动计算 trace features，写入：
    - `final_state.metadata["maas_trace_features"]`
    - `trace/iter_xx_plan.json`（战略计划快照）
    - `summary.json`（关键压缩字段）
  - `run_log.txt` 输出一行压缩指标摘要，便于快速诊断。
- `config/system.yaml`
  - 新增 `optimization.pymoo_maas_trace_feature_window`（默认 `5`，用于 recent window 统计）。

### 3.4 Rule-based Meta Policy（本轮新增）

- 新增 `optimization/meta_policy.py`
  - `propose_meta_policy_actions(...)`：输入 `maas_trace_features` 与当前 knobs，输出动作集合与 next knobs。
  - 当前覆盖动作：
    - `relax_constraint_bounded`
    - `tighten_constraint_back`
    - `retune_mcts_action_prior`
    - `retune_mcts_cv_penalty`
    - `reallocate_comsol_budget`
- `workflow/maas_pipeline_service.py`
  - 非 MCTS retry 回路：当 `attempt >= pymoo_maas_meta_policy_min_attempts` 时，按特征做当轮调参（`maas_relax_ratio`，以及 online COMSOL budget）。
  - 全模式：输出 `meta_policy_report`（runtime 事件 + next_run_recommendation）。
  - `summary.json` 新增：
    - `meta_policy_runtime_events`
    - `meta_policy_next_run_actions`
- `workflow/orchestrator.py`
  - `online_comsol` 运行时热评估器新增动态预算接口：
    - `set_eval_budget(...)`
    - `get_eval_budget(...)`
  - 允许 meta policy 在运行期调整剩余预算策略。
- `config/system.yaml` 新增：
  - `pymoo_maas_enable_meta_policy`
  - `pymoo_maas_meta_policy_apply_runtime`
  - `pymoo_maas_meta_policy_min_attempts`

### 3.5 MCTS 轮内动态调权（本轮新增）

- `optimization/maas_mcts.py`
  - 新增运行期更新接口：
    - `update_policy_weights(...)`
    - `get_policy_weights(...)`
  - rollout 记录新增权重快照：
    - `action_prior_weight`
    - `cv_penalty_weight`
- `workflow/maas_pipeline_service.py`
  - 在 MCTS `evaluate_node` 回调中引入 runtime meta policy：
    - 每次满足阈值后根据 interim trace 特征生成动作
    - 当轮更新 planner 权重与 `maas_relax_ratio`
    - 记录事件（`trigger_rollout`, `planner_policy_weights`）
- `summary.json` 新增字段：
  - `meta_policy_runtime_applied_events`

### 3.6 多保真调度运行期自适应（本轮新增）

- `workflow/orchestrator.py`
  - `online_comsol` evaluator 新增：
    - `set_scheduler_params(...)`
    - `get_scheduler_params()`
  - 调度参数（mode/top_fraction/min_observations/warmup_calls/explore_prob/uncertainty_weight/uncertainty_scale_mm）从“初始化固定值”升级为“运行期可更新控制面”。
- `workflow/maas_pipeline_service.py`
  - `_current_runtime_knobs()` 扩展输出 scheduler knobs。
  - `_apply_runtime_knobs()` 扩展下发 scheduler knobs 到 runtime evaluator。
  - attempt payload 增加运行时热评估快照（`online_comsol_calls_so_far`）。
- `optimization/meta_policy.py`
  - 升级为 `meta_policy_v2`，新增 scheduler-aware 规则：
    - 高跳过率 + 低可行：放宽 top_fraction 并提高 explore；
    - 高执行率 + 低物理通过：收紧 top_fraction，降低 uncertainty_weight；
    - budget 压力 + 低可行：可从 `budget_only` 切换到 `ucb_topk`。
- `optimization/trace_features.py`
  - 新增 `first_feasible_eval`、`comsol_calls_to_first_feasible` 特征。

### 3.7 R2 对照实验脚手架（本轮新增）

- 新增 `run/run_pymoo_maas_benchmark_matrix.py`
  - 支持 profile：`baseline` / `meta_policy` / `operator_program` / `multi_fidelity`
  - 支持 algorithm：`nsga2` / `nsga3` / `moead`
  - 支持 level：默认 `L3/L4/L7/L8`
  - 支持多 seed 批量执行
  - 自动写出：
    - `matrix_runs.jsonl`
    - `matrix_runs.csv`
    - `matrix_aggregate_profile_level.csv`
    - `matrix_report.md`
- 新增单测 `tests/test_pymoo_maas_benchmark_matrix.py`（profile 配置映射 + 聚合统计）。
- 冒烟验证：
  - `baseline x L3 x seed=42`（simplified backend）已跑通并生成报告目录：
    - `docs/benchmarks/pymoo_maas_benchmark_smoke_matrix_20260302/`

### 2.4 P0/P1/P2 运行稳定性修复（v2.1.1，已落地）

- `P0`（热源绑定有效性）
  - `simulation/comsol_driver.py`：多域歧义不再直接跳过，新增确定性域收敛（优先几何中心最近，失败回退域号接近度）。
  - `simulation/comsol_driver.py`：若 `active_components>0` 且 `assigned_count==0`，立即返回失败惩罚（`NO_HEAT_SOURCE_BOUND`），不再把近零温升当有效结果。
- `P1`（COMSOL 负载与文件冲突）
  - `simulation/comsol_driver.py`：`.mph` 保存改为唯一路径优先，减少固定文件锁冲突。
  - `simulation/comsol_driver.py`：新增 `save_mph_each_eval` / `save_mph_on_failure` 配置，默认仅失败保存。
  - `workflow/orchestrator.py`：`online_comsol` 运行时热评估新增预算节流 `pymoo_maas_online_comsol_eval_budget`，超限自动回退 proxy。
  - `workflow/orchestrator.py`：新增 `pymoo_maas_online_comsol_geometry_gate`（默认开启），几何明显不可行候选直接回退 proxy，减少无效 COMSOL 调用。
  - `workflow/orchestrator.py`：新增 `pymoo_maas_online_comsol_cache_quantize_mm`（默认 0.0），可按位置量化步长生成缓存键以提升近邻候选复用率。
- `P2`（模式化观测）
  - `core/logger.py`：新增 `pymoo_maas_trace.csv` + `log_pymoo_maas_trace()`。
  - `workflow/maas_pipeline_service.py`：记录 attempt 级轨迹（诊断、CV、AOCC、cost、best 标记、physics audit 选择原因）。
  - `core/visualization.py`：按模式分流生成图表；`agent_loop` 继续使用 `evolution_trace`，`pymoo_maas` 使用专用轨迹图与摘要。
  - `run/run_L1_simple.py`：中断时补写 summary，并强制释放仿真连接。
  - `core/logger.py`：`run_log.txt` 改为精简审计视图（过滤结构高频重复行），新增 `run_log_debug.txt` 保留完整信息。
  - `workflow/orchestrator.py`：在线热评估新增低频进度汇总 `pymoo_maas_online_comsol_stats_log_interval`（默认 5000）。

---

## 4. 关键文件（本轮关注）

- 入口编排：`workflow/orchestrator.py`
- MaaS 流程服务：`workflow/maas_pipeline_service.py`（新增）
- COMSOL 驱动：`simulation/comsol_driver.py`
- 日志与可视化：`core/logger.py`, `core/visualization.py`
- 基准看板渲染：`run/render_pymoo_maas_benchmark_dashboard.py`（新增）
- 矩阵主脚本（已接入自动看板）：`run/run_pymoo_maas_benchmark_matrix.py`
- MCTS 策略：`optimization/maas_mcts.py`
- Meta 策略：`optimization/meta_policy.py`（新增）
- MCTS 动态调权：`optimization/maas_mcts.py`（新增接口）
- Trace 特征：`optimization/trace_features.py`（新增）
- MaaS 编译/审计/反射：
  - `optimization/maas_compiler.py`
  - `optimization/maas_audit.py`
  - `optimization/maas_reflection.py`
  - `optimization/modeling_validator.py`
- pymoo 集成层：
  - `optimization/pymoo_integration/`
- 多算法矩阵脚手架：
  - `run/run_pymoo_maas_benchmark_matrix.py`
- 烟测入口：`run/run_pymoo_maas_smoke.py`
- 配置：`config/system.yaml`

---

## 5. 运行与验证（已执行）

### 5.1 必须使用的命令前缀

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...
```

### 5.2 已通过的验证

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_mcts.py tests/test_maas_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py -q
# 结果: 21 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_core.py tests/test_maas_pipeline.py -q
# 结果: 25 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_pymoo_maas_smoke.py
# 结果: SMOKE_DONE, diagnosis=feasible, mcts_enabled=True
# 产物: summary.json 新增 feasible_rate / best_cv_min / comsol_calls_per_feasible_attempt / physics_pass_rate_topk
# 产物: metadata/trace/summary 新增 meta_policy_report 与 meta policy 计数字段
# 产物: summary.json 新增 meta_policy_runtime_applied_events

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_event_logger.py tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema -q
# 结果: 2 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_runner_multi_algorithms.py tests/test_runner_operator_bias.py -q
# 结果: 4 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_visualization_storyboard.py tests/test_benchmark_dashboard.py tests/test_event_logger.py tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema -q
# 结果: 4 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/render_pymoo_maas_benchmark_dashboard.py --benchmark-dir docs/benchmarks/pymoo_maas_benchmark_opv2_algoaware_nsga2_l4_r7
# 结果: 生成 dashboard_feasible_ratio.png/dashboard_best_cv.png/dashboard_first_feasible_eval.png/dashboard_summary.md

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_pymoo_maas_benchmark_matrix.py tests/test_benchmark_dashboard.py -q
# 结果: 9 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_pymoo_maas_benchmark_matrix.py --profiles baseline --algorithms nsga2 --levels L1 --seeds 42 --max-iterations 1 --pymoo-pop-size 8 --pymoo-n-gen 2 --backend simplified --thermal-evaluator-mode proxy --experiment-tag dashboard_auto_smoke_enabled --output-dir docs/benchmarks
# 结果: 自动产出 matrix 报告 + dashboard 三图 + dashboard_summary，并写入 matrix_report.md 的 Dashboard Artifacts 段

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_maas_pipeline.py::test_pymoo_maas_best_cv_min_fallback_uses_execution_curve tests/test_maas_pipeline.py::test_pymoo_maas_pipeline_mcts_report_schema tests/test_pymoo_maas_benchmark_matrix.py tests/test_benchmark_dashboard.py -q
# 结果: 11 passed

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy pytest tests/test_pymoo_maas_benchmark_matrix.py tests/test_maas_pipeline.py::test_pymoo_maas_best_cv_min_fallback_uses_execution_curve tests/test_benchmark_dashboard.py -q
# 结果: 11 passed（含新增 best_cv_missing_ratio 覆盖用例）

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_pymoo_maas_benchmark_matrix.py --profiles "operator_program,multi_fidelity" --algorithms "nsga2" --levels "L7" --seeds "42" --backend comsol --thermal-evaluator-mode online_comsol --max-iterations 4 --pymoo-pop-size 24 --pymoo-n-gen 12 --online-comsol-eval-budget 36 --enable-physics-audit --experiment-tag l7_real_comsol_feasible_check_20260303 --output-dir docs/benchmarks
# 结果: `operator_program` 可行（SUCCESS/feasible），`multi_fidelity` 本次 no_feasible（audit gate）
```

---

## 6. 当前已知约束与注意事项

- `tests/` 当前被 `.gitignore` 忽略：
  - 本地可执行，但默认不会进入 Git 跟踪。
- `pymoo` 与 `pydantic` 存在 deprecation warnings：
  - 不影响当前功能正确性，但建议后续治理。
- `experiments/run_*` 调试产物会累积：
  - 需在允许的执行策略下定期清理。

---

## 7. 下一步执行计划（已批准）

### 7.1 叙事主线（论文/项目统一口径）

从“传统数值优化在高维强约束+昂贵仿真下的可行性瓶颈”出发，主张 MsGalaxy 的创新点不是替代 NSGA-II，而是：

- 外层：LLM 生成并搜索算子程序（Operator Program / Strategy Program）；
- 内层：pymoo(NSGA-II) 做受策略偏置的数值优化；
- 物理层：proxy + online COMSOL 多保真验证与预算调度。

### 7.2 OP-MaaS 路线图（R0/R1/R2）

- `R0`（基线冻结与证据固化，立即执行）
  - 固化 L7/L8 对照实验统计口径：`feasible_rate`、`best_cv_min`、`first_feasible_eval`、`COMSOL_calls_to_first_feasible`。
  - 输出统一对照表，作为后续增量改进基线。
- `R1`（最小可落地内核改造）
  - 新增 `OperatorProgram` 结构与 DSL 校验器（动作合法性、边界、安全约束）。
  - 将 MCTS action 从 “identity/uniform_relax/objective_focus” 扩展为“可执行算子程序”分支。
  - 在 `PymooNSGA2Runner` 接入策略偏置接口（sampling/mutation/repair 的程序化配置）。
- `R2`（多保真与可发表评估）
  - 引入 uncertainty-aware COMSOL 触发策略（非逐候选全量高保真）。
  - 进行消融实验：`Baseline` vs `+MetaPolicy` vs `+OperatorProgram` vs `+MultiFidelity`。
  - 目标：在 L7/L8 提升 first-feasible 速度与预算效率，并保持/提升 Pareto 质量。

### 7.3 下一会话启动清单（直接按此执行）

> 2026-03-02 执行状态：`R1` 第 `1-5` 项已落地；并完成 D1-D3 验证（payload 透传 + operator-program 核心搜索空间 + runtime switch），针对性回归 `52 passed`。

1. [x] 新建 `optimization/operator_program.py`（数据结构 + schema + validator）。  
2. [x] 新建 `optimization/operator_actions.py`（基础算子库：group move / cg recenter / hot spread / swap）。  
3. [x] 扩展 `workflow/orchestrator.py::_propose_maas_mcts_variants`，挂接 `OperatorProgram` 分支。  
4. [x] 扩展 `optimization/pymoo_integration/runner.py`，支持策略偏置注入并记录实验元数据。  
5. [x] 增加单测：算子合法性、边界保持、分支可执行性、运行日志字段完整性（已补 `tests/test_maas_pipeline.py::test_mcts_eval_payload_includes_candidate_diagnostics_for_operator_context` 与 `tests/test_pymoo_maas_pipeline_operator_program_search_space_runs`）。  

### 7.4 下一阶段执行清单（新增：Multi-MOEA + OP-MaaS v2）

1. [ ] 运行正式对照矩阵（建议 real COMSOL + `L4/L7` + `seed>=3` + `pop>=24` + `n_gen>=12`），固化 `profile x algorithm x level` 统计显著性。  
2. [ ] 对 `nsga2/nsga3/moead` 增加统一可行性曲线口径（当前 `moead` 已有 raw CV，`nsga2/nsga3` 的 `best_cv_min=null` 场景需补齐 least-infeasible 回传一致性）。  
3. [ ] 在 MCTS/MetaPolicy 中引入 `algorithm-aware` 策略（不同算法的 exploration / repair / budget knobs 分开建模）。  
4. [ ] 在 OP-MaaS v2 评估中追加“传统算法单跑 vs 框架增强”叙事表：`Baseline(algo only)` / `+OperatorProgram` / `+MetaPolicy` / `+MultiFidelity`。  

---

## 8. 文档同步说明

本次已同步更新：

- `HANDOFF.md`（本文件）
- `AGENTS.md`（新增 OP-MaaS 执行规范与边界）

后续规则：

- 每次架构升级先更新 `HANDOFF.md`，再更新 `PROJECT_SUMMARY.md` 与专项技术文档。  
