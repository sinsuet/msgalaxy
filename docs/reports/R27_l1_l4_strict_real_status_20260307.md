# R27 L1-L4 strict-real 串行主线完成状态（2026-03-07）

## 1. 范围

本报告记录 2026-03-07 新版 `L1-L4` 串行 `NSGA-III + real COMSOL` strict-real 主线的完成状态，仅覆盖当前仓内已实现链路，不外推到未落地的 v4 / 神经模块 / 高保真 mission evaluator。

## 2. 结论

- `L1/L2/L3/L4` 已全部在当前 `mass` 主线下完成单次串行 strict-real 运行。
- 四档运行均满足当前 strict-real 最低验收口径：
  - `source_gate_passed == true`
  - `operator_family_gate_passed == true`
  - `operator_realization_gate_passed == true`
  - `run_log.txt` 中 `Dataset "dset*" does not exist` 计数为 `0`
- `L3/L4` 的关键收口依赖以下运行时修复：
  - 初始布局 clearance 与场景约束同步
  - 初始 mission keepout repair
  - `mission_keepout` 纳入 runtime hard-violation 汇总
  - final-state 复核结果回写 `summary.json`

## 3. 证据路径

### 3.1 L1

- 证据：`experiments/0307/0141_l1_nsga3/summary.json`
- 结果：`status=SUCCESS`、`diagnosis_status=feasible`、`best_cv_min=0.0`

### 3.2 L2

- 中间调参：`experiments/0307/0200_l2_nsga3/summary.json`
- 收口证据：`experiments/0307/0209_l2_nsga3/summary.json`
- 结果：`status=SUCCESS`、`diagnosis_status=feasible`、`best_cv_min=0.0`

### 3.3 L3

- 证据：`experiments/0307/1646_l3_nsga3/summary.json`
- 结果：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `final_mph_path` 已写出

### 3.4 L4

- 证据：`experiments/0307/1708_l4_nsga3/summary.json`
- 结果：
  - `status=SUCCESS`
  - `diagnosis_status=feasible`
  - `diagnosis_reason=final_state_recheck_feasible`
  - `best_cv_min=0.0`
  - `min_clearance=6.0`
  - `mission_keepout_violation=0.0`
  - `final_mph_path` 已写出

## 4. 当前边界

- 当前结论仅表示 strict-real 主线已跑通，不等价于 release-grade audited evidence。
- `summary.json` 中 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 仍未稳定写出，后续需单独补齐。
- `mission` 当前默认执行 keepout 代理接口，不应表述为完整高保真 FOV/EMC 已闭环。

## 5. 下一步建议

1. 补齐 release-grade audit 字段写出。
2. 基于当前模板重建轻量 benchmark 框架。
3. 在新 benchmark 上开展 `LLM intent vs deterministic` 与多算法对照。
