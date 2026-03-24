# S1-S4 几何/热/结构测试集方案（阶段性验证 LLM+NSGA-III）

日期：2026-03-06  
适用范围：`mass` 栈，阶段性目标验证（非发布结论）

## 1. 目标

在统一预算与统一约束下，验证 `LLM + NSGA-III` 是否优于纯传统 `NSGA-III`。  
本轮仅覆盖已实现且可执行的三类物理场：

- 几何（geometry）
- 热（thermal）
- 结构（structural）

不纳入本轮结论的域：

- 电源（power）
- mission/FOV/EMC

## 2. S1-S4 测试集定义

S1-S4 采用“难度逐级上升”的同构设计，所有阶段均保持 `g(x) <= 0` 约束语义。

| Stage | BOM 基础映射 | 强制硬约束组（仅三域） | 算子集合（仅相关族） | 后端/评估模式 | 目标用途 |
|---|---|---|---|---|---|
| S1 | L1 | `collision, clearance, boundary, cg_limit` | `group_move, cg_recenter, swap` | `simplified + proxy` | 几何可行域启动能力 |
| S2 | L2 | `collision, clearance, boundary, thermal, cg_limit` | `S1 + hot_spread, add_heatstrap, set_thermal_contact` | `simplified + proxy` | 热约束耦合下可行性 |
| S3 | L3 | `collision, clearance, boundary, thermal, cg_limit, struct_safety, struct_modal` | `S2 + add_bracket, stiffener_insert` | `simplified + proxy` | 三域联动（代理） |
| S4 | L4 | 与 S3 相同（阈值更严格） | 与 S3 相同 | `comsol + online_comsol`（预算受控） | 三域联动（在线真值抽检） |

约束与算子边界：

- 禁止纳入 `power_*`、`mission_keepout` 约束组进入本轮结论。
- 禁止纳入 `bus_proximity_opt`、`fov_keepout_push` 进入本轮算子集。

## 3. 对照实验设计（公平比较）

每个 Stage 做两臂对照，预算严格一致：

- A 组（传统基线）：`NSGA-III`，`use_llm_intent=false`，`profile=baseline`
- B 组（目标方法）：`NSGA-III`，`use_llm_intent=true`，`profile=operator_program`

统一预算项（A/B 完全一致）：

- `max_iterations`
- `pymoo_pop_size`
- `pymoo_n_gen`
- `mass_max_attempts / mcts_budget / online_comsol_eval_budget`（若启用）
- 种子集合

统计最小要求：

- `seed >= 5`（建议：`42,43,44,45,46`）
- 每个 Stage 独立统计并给出总体汇总

## 4. 指标与通过门槛

主指标：

- `strict_proxy_feasible_ratio`（S1-S3）
- `strict_real_feasible_ratio`（S4，online COMSOL 有效样本）
- `best_cv_min_mean`
- `first_feasible_eval_mean`

辅助指标：

- `dominant_violation_top1`
- `comsol_calls_to_first_feasible_mean`（S4）
- 动作族命中占比：geometry/thermal/structural

阶段性通过判据（建议）：

- S1-S3：B 组相对 A 组 `strict_proxy_feasible_ratio` 绝对提升 >= `10%`
- S4：B 组相对 A 组 `strict_real_feasible_ratio` 绝对提升 >= `8%`
- 至少 3 个 Stage 同时满足提升门槛，且无 Stage 出现明显退化（退化 > `5%`）

## 5. 落地改造清单（最小实现集）

1. 新增 GTS 专用等级配置：`config/system/mass/s1_s4_gts_profiles.yaml`  
2. 在 benchmark 入口支持 GTS intent 模板（仅注入几何/热/结构硬约束与目标）。  
3. 新增执行入口：`run/mass/benchmark_s1_s4_gts_nsga3.py`，默认输出 A/B 对照矩阵。  
4. 新增结果汇总脚本：输出 `stage_compare.csv`、`stage_compare.md`、`gate_result.json`。  
5. 新增测试：`tests/test_mass_benchmark_s1_s4_gts.py`，覆盖参数构建、组装矩阵、门槛判定。

## 6. 执行命令模板

先做 dry-run 验证：

```powershell
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_s1_s4_gts_nsga3.py --dry-run
```

再做代理冒烟（S1-S3）：

```powershell
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_s1_s4_gts_nsga3.py --stages S1,S2,S3 --backend simplified --thermal-evaluator-mode proxy --seeds 42,43,44,45,46 --experiment-tag s1_s3_gts_proxy_ab
```

最后做 S4 在线真值抽检：

```powershell
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_s1_s4_gts_nsga3.py --stages S4 --backend comsol --thermal-evaluator-mode online_comsol --online-comsol-eval-budget 24 --seeds 42,43,44,45,46 --experiment-tag s4_gts_online_ab
```

## 7. 风险与约束

- online COMSOL 预算耗尽会导致真值样本不足，需单独报告 `eval_available`。
- 若 strict 指标仍绑定 power/mission 口径，会污染本轮结论，需先切换为“三域口径”。
- LLM 变量映射失败会退回 deterministic 意图，必须在结果中显式披露占比。

## 8. 预期产物

- `benchmarks/mass_benchmark_s1_s4_gts_*`
- `matrix_runs.csv / matrix_aggregate_profile_level.csv / matrix_report.md`
- `stage_compare.csv / stage_compare.md / gate_result.json`
- 可视化 dashboard（按 Stage 展示 A/B 差值）

