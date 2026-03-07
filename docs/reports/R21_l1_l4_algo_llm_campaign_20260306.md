# R21 L1-L4 三算法与 LLM 策略推进方案（2026-03-06）
## 1. 目标范围

本方案仅覆盖当前已实现能力（M2/M3），不引入 M4 神经模块，也不做最终论文阶段的大规模消融。
本阶段目标：
- G1：在当前物理场与算子实现下，完成 `L1-L4 × NSGA-II/NSGA-III/MOEAD` 的可复现跑通。
- G2：在同预算、同约束、同种子下，实现 `LLM intent` 相对 deterministic baseline 的可量化改进。
- G3：对关键结果执行 online COMSOL strict-real 复核，确保结论不反转。

## 2. 能力边界（本阶段）

纳入能力：
- 物理场：`geometry + thermal + structural + power + mission`（mission 依赖外部 evaluator）
- 算子：`group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`
- 严格门禁：`source_gate / operator_family_gate / operator_realization_gate`（strict）
- 优化器：`nsga2 / nsga3 / moead`

排除能力：
- M4：`feasibility predictor / neural policy / neural scheduler`
- 最终阶段的全量 ablation

## 3. 核心指标与验收口径

主指标：
- `diagnosis_feasible_ratio`
- `strict_proxy_feasible_ratio`
- `best_cv_min`（均值 / 中位数）
- `first_feasible_eval`（均值 / 中位数）
- `comsol_calls_to_first_feasible`（关键组）

辅助指标：
- dominant violation 分布
- `source/operator-family/operator-realization` strict gate 通过率
- `dset` 错误计数（应保持 0）

LLM 优效判定（阶段性）：
- 在至少 3 个 seeds 下，LLM 组相对 deterministic 组满足以下任一项：
- feasible_ratio 提升 >= 0.10
- best_cv_min 均值下降 >= 15%
- first_feasible_eval 均值下降 >= 15%

## 4. 执行阶段

### Phase A：三算法 deterministic 基线矩阵（simplified）
命令：
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_matrix.py --profiles operator_program --levels L1,L2,L3,L4 --algorithms nsga2,nsga3,moead --seeds 42,43,44 --backend simplified --thermal-evaluator-mode proxy --max-iterations 2 --pymoo-pop-size 24 --pymoo-n-gen 12 --intent-template v3_multiphysics --hard-constraint-coverage-mode strict --metric-registry-mode strict --experiment-tag l1_l4_algo3_det_baseline
```
输出：
- `matrix_runs.csv`
- `matrix_aggregate_profile_level.csv`
- `matrix_report.md`
- `matrix_strict_gate.json`

### Phase B：LLM 对照矩阵（simplified）
命令：
```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_matrix.py --profiles operator_program --levels L1,L2,L3,L4 --algorithms nsga2,nsga3,moead --seeds 42,43,44 --backend simplified --thermal-evaluator-mode proxy --max-iterations 2 --pymoo-pop-size 24 --pymoo-n-gen 12 --intent-template v3_multiphysics --hard-constraint-coverage-mode strict --metric-registry-mode strict --use-llm-intent --experiment-tag l1_l4_algo3_llm_ab
```
输出：
- 与 Phase A 同结构产物，便于逐列 A/B 对比。

### Phase C：关键组 online COMSOL strict-real 复核
策略：
- 从 Phase A/B 中挑选每种算法、每个等级的代表组，优先覆盖 best 和 borderline 可行组。
- 固定 `--backend comsol --thermal-evaluator-mode online_comsol` 执行复核。
- 开启 strict gate，并审计 `final_mph_path` 对应 run log 中的 `dset` 计数。

## 5. LLM 策略优化闭环（仅做与 G2 直接相关的最小改造）

优先优化项：
- variable mapping 稳定性，减少默认 xyz 回退
- metric mapping 与 hard constraint 对齐
- operator_program 触发质量，按 dominant violation 匹配 family
- mission keepout 分支可行化，保持 repair-before-block

每次改造后最小验证：
1. `tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py`
2. `run/mass/run_T2_real2.py --require-strict-pass`
3. 重新跑受影响等级 / 算法的 A/B 子矩阵

## 6. 风险与防回归

- 风险1：`NSGA-III / MOEAD` 在 strict 口径下可行率偏低
  - 处置：按等级独立调 `pop_size / n_gen / mass_max_attempts / mcts_budget`
- 风险2：LLM 引入噪声导致可行率下降
  - 处置：对 LLM 输出做 contract 裁剪，并保留 fallback 审计
- 风险3：online COMSOL 成本过高
  - 处置：仅对关键组做复核，不对全矩阵直接上 COMSOL
- 风险4：`dset` 错误回归
  - 处置：每次 COMSOL 复核后都做 run log 计数审计，目标为 0

## 7. 阶段完成定义（DoD）

- D1：Phase A/B 全部完成并产出可对比报告
- D2：至少 1 轮 LLM 策略优化后，达到阶段性 LLM 优效阈值
- D3：关键组 online COMSOL strict-real 复核通过，且无 `dset` 错误风暴
- D4：`HANDOFF.md`、`RULES.md`、`README.md` 与本方案保持一致
