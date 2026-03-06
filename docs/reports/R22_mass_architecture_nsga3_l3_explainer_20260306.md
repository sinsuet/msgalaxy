# MsGalaxy 系统架构与 L3 NSGA-III 实跑讲解（面向非本领域老师）

版本日期：2026-03-06  
目标受众：非卫星布局/多目标优化领域的老师  
对应案例：`run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002`（2026-03-06）

---

## 1. 先说结论：我们到底在做什么

MsGalaxy 不是“给一个固定坐标答案”的脚本，而是一个“把工程需求转成可执行优化问题，再在可行域里系统搜索”的研究系统。  

它要解决的问题是：给定小卫星（或类 CubeSat）组件清单（BOM）和工程约束（热、结构、电源、几何、任务隔离等），自动产生物理上可落地的布局方案，并且每一步都留痕，可追溯“为什么这样放”。

从方法上看，它是一个神经符号系统（Neuro-Symbolic）：

1. LLM 层：理解需求、组织建模意图（变量/目标/约束）、提供反射建议。  
2. MOEA 层（pymoo）：做真正的数值优化搜索（NSGA-II/NSGA-III/MOEAD）。  
3. Physics 层：做几何+热+结构+电源+任务隔离评估（代理模型与在线 COMSOL 结合）。  

关键原则是：LLM 只“提案”，最终可行性必须经过可执行优化链路和物理评估链路验证。

---

## 2. 为何这个问题难（给非本领域视角）

卫星布局优化本质上是一个“高维+强约束+多目标”的问题：

1. 变量很多：每个组件至少有 3 个位置维度，若还有姿态、布线、接触参数，维度会更高。  
2. 约束很硬：比如碰撞必须为 0、最小间隙不能低于阈值、热峰值不能超标、质心不能跑偏。  
3. 目标冲突：减温、减重心偏移、提结构裕度、提电源裕度经常互相拉扯。  
4. 评估贵：高保真 COMSOL 调一次就要时间和算力预算，不可能无限试。  

所以不能靠“拍脑袋坐标”，必须做“可行域内的系统搜索”。

---

## 3. 我们系统的总体架构（当前真实实现）

当前主线运行模式是 `mass`（A/B/C/D 闭环）：

1. A 理解（Understanding）  
作用：从当前设计状态和约束上下文，构建/确认 `ModelingIntent`。  

2. B 形式化（Formulation）  
作用：把约束统一改写成 `g(x) <= 0`，把目标统一成可计算目标向量。  

3. C 编译与求解（Coding/Execution）  
作用：把意图编译为 pymoo 的 `ElementwiseProblem`，交给 NSGA-II/III/MOEAD 做演化搜索。  

4. D 反射与审计（Reflection/Audit）  
作用：诊断是否可行、是否卡住；必要时做受控放松与重试；并执行 top-k 物理审计。

这个流程在代码中是明确事件化的，不是口头概念。`phase_events` 会写出 A/B/C/D 的 started/completed 记录，最终汇总到 `summary.json` 和 `tables/phases.csv`。

---

## 4. NSGA-III 从基础讲起

### 4.1 多目标优化为什么不是“一个最优解”

当目标彼此冲突时（例如“最高热安全裕度”与“最低质心偏移”），通常不存在“所有目标都最优”的单点。  
这时我们追求的是 **Pareto 前沿**：任何一个候选点，都不能在“至少一个目标更好且其余不差”的意义上被完全压制。

### 4.2 NSGA-III 的核心思想

NSGA-III 是 NSGA-II 的扩展，重点在“多目标数量增多时，如何保持解集分布均匀”。  

它除了非支配排序，还引入 **参考方向（reference directions）**：

1. 把目标空间划分为很多“方向槽位”；  
2. 每轮选择时，不只看谁好，还看哪些方向还“空”，通过 niching 机制补齐稀疏方向；  
3. 结果是 Pareto 解集覆盖更均匀，适合 3 个及以上目标场景。

### 4.3 NSGA-III 标准流程（概念版）

1. 初始化种群。  
2. 计算每个体的目标向量 `F` 和约束向量 `G`。  
3. 按非支配关系分层（Front 1, Front 2, ...）。  
4. 用参考方向把候选解映射到目标空间方向上。  
5. 依次填充下一代；到“最后一个塞不满的前沿”时，用 niching 按方向稀疏度挑选。  
6. 做交叉与变异，生成子代。  
7. 重复直到达到终止代数。

---

## 5. NSGA-III 在 MsGalaxy 里的具体落地

### 5.1 约束统一为 `g(x) <= 0`

在 MaaS 编译里，硬约束统一被标准化：

1. `metric <= target` 转成 `g(x)=metric-target<=0`  
2. `metric >= target` 转成 `g(x)=target-metric<=0`  
3. `metric == target` 转成 `g(x)=|metric-target|-eps<=0`

这保证优化器只需要处理统一不等式语义，减少歧义。

### 5.2 问题对象是可执行 `ElementwiseProblem`

编译后的问题有明确：

1. 决策变量上下界 `xl/xu`；  
2. 目标向量 `out["F"]`；  
3. 约束向量 `out["G"]`。  

这意味着每个个体都能被真正计算，而不是文本描述层面的“伪优化”。

### 5.3 NSGA-III runner 的关键实现点

在 runner 里：

1. 算法由 `optimization.pymoo_algorithm` 解析，支持 `nsga2/nsga3/moead`。  
2. `nsga3` 分支会构造 reference directions（优先 das-dennis）。  
3. `pop_size` 会和 reference directions 数量对齐。  
4. 默认启用 `eliminate_duplicates=True`。  
5. 通过 callback 记录每代 `feasible_count / feasible_ratio / best_cv / mean_cv`，用于可解释分析。

### 5.4 这次案例不是“直接搜坐标”，而是 `operator_program` 搜索空间

`L3` 这个案例用的是 `mass_search_space=operator_program`。  
这意味着优化变量是“动作程序基因”，而不是直接 `(x,y,z)`：

1. 每个 action slot 用 6 个基因编码（动作类型、组件选择、轴向、幅值、focus 等）。  
2. 本次配置 action slots = 10，因此基因维度约为 60。  
3. 解码后得到动作序列，再作用到设计状态，最后再做物理评估。  

这与“LLM 直接吐最终坐标”完全不同，仍然是 pymoo 在连续向量空间中做系统搜索。

---

## 6. 具体案例：2026-03-06 的 L3 + NSGA-III 运行

### 6.1 案例身份与基本参数

案例 run_id：`run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002`  
聚合报告目录：`benchmarks/mass_benchmark_nsga3_l3_real_single_tune1/`  

关键参数（来自产物）：

1. level：L3  
2. profile：operator_program  
3. algorithm：nsga3  
4. seed：42  
5. backend：comsol  
6. thermal evaluator：online_comsol  
7. `pymoo_pop_size=12`，`pymoo_n_gen=4`  
8. `max_iterations=1`，`mass_max_attempts=1`  
9. `mass_online_comsol_eval_budget=8`

### 6.2 建模意图规模（A 阶段产物）

从 `iter_01_plan.json` 可直接读到：

1. 变量数：24  
2. 目标数：6  
3. 硬约束数：10  

目标包含：

1. 最小化 `cg_offset`  
2. 最小化 `max_temp`  
3. 最大化 `power_margin`  
4. 最小化 `voltage_drop`  
5. 最大化 `safety_factor`  
6. 最大化 `first_modal_freq`

硬约束经标准化后示例：

1. `g_temp: g(x)=max_temp-55.0<=0`  
2. `g_clearance: g(x)=5.0-min_clearance<=0`  
3. `g_struct_modal: g(x)=58.0-first_modal_freq<=0`

### 6.3 A/B/C/D 时间分布（按 phase 表）

本次单轮迭代的阶段耗时（秒）：

1. Phase A（understanding）：约 72.549s  
2. Phase B+C（formulation+solver）：约 309.251s  
3. Phase D（reflection+audit）：约 122.370s  
4. 总计：约 504.170s

这说明求解与审计仍是主要成本中心，符合“高保真评估昂贵”的预期。

### 6.4 NSGA-III 代际行为（4 代）

generation 统计：

1. Gen1：可行 3/12，feasible_ratio=0.25，best_cv=0  
2. Gen2：可行 9/12，feasible_ratio=0.75，best_cv=0  
3. Gen3：可行 9/12，feasible_ratio=0.75，best_cv=0  
4. Gen4：可行 9/12，feasible_ratio=0.75，best_cv=0

可解释点：

1. 第 1 代就出现可行个体（`first_feasible_eval=1`）。  
2. 可行率在第 2 代显著提升并稳定。  
3. 这是“快速进入可行域 + 保持可行解群”的典型表现。

### 6.5 NSGA-III 参数落地（本次实值）

solver metadata 给出的 NSGA-III 关键值：

1. `algorithm=NSGA3`，`algorithm_requested=NSGA3`（无回退）  
2. `constraint_strategy=feasibility_first`  
3. `ref_dirs_method=das-dennis`  
4. `ref_dirs_count=12`  
5. `ref_dirs_partitions=2`  
6. `initial_population_injected=6`

说明：

1. 参考方向不是抽象概念，在本次是“12 个方向”。  
2. 种群规模与方向数量一致，体现 NSGA-III 的方向驱动选择。  
3. 初始注入种群有 6 个，帮助更快靠近可行域。

### 6.6 物理预算与审计

在线 COMSOL 热预算相关：

1. 预算上限：8 次  
2. 实际执行 online_comsol：8 次  
3. 热评估请求总数：49（其余由门禁/回退逻辑处理）

物理审计（top-k）：

1. 请求 top_k=2  
2. 两个候选都成功且 `num_violations=0`  
3. 最终按 `best_feasible_penalty` 选 rank1

### 6.7 约束门禁结果（strict）

本次 strict 门禁全部通过：

1. source gate：`strict` 且 `passed=true`（thermal/mission/structural/power real 都满足）  
2. operator family gate：`strict` 且 `passed=true`（geometry/thermal/structural/power/mission 全覆盖）  
3. operator realization gate：`strict` 且 `passed=true`（五类动作均有真实落地证据）

最终指标摘要：

1. `status=SUCCESS`，`diagnosis=feasible`  
2. `best_cv_min=0.0`  
3. `strict_proxy_feasible=true`  
4. `strict_real_feasible=true`  
5. `first_feasible_eval=1`  
6. `comsol_calls_to_first_feasible=8`

### 6.8 一个容易误读但很重要的点

你会看到 `mission_keepout_violation=57.3565` 是正值，但这次仍判定可行。原因不是“系统忽略了问题”，而是：

1. 本次 L3 的 mandatory groups 不含 `mission_keepout`；  
2. 所以 mission 在该组实验里作为观测指标与 gate 审计上下文存在，但不作为本次可行性判定的硬约束目标值。  

这正是“按 level profile 分级约束”的设计结果，而不是逻辑冲突。

---

## 7. 给老师解释时可直接使用的“口语化版本”

可以用下面这段话：

“我们做的不是让大模型直接给卫星坐标，而是让系统先把工程需求变成一个数学优化问题，再让 NSGA-III 这样的多目标算法自动搜索。  
这个搜索每一步都要经过物理评估，保证不是纸面最优。  
以我们 2026 年 3 月 6 日刚跑的 L3 案例为例，系统在 1 次迭代里，NSGA-III 用 12 个个体跑 4 代，在第 1 代就找到可行解，并在第 2 代把可行比例提升到 75%。  
最后 strict 门禁（来源真实性、算子覆盖、算子真实落地）全通过，结果可追溯到 summary、phase、generation、physics audit 等全套日志。  
所以我们的贡献核心不是‘某个坐标答案’，而是‘可复现、可解释、可审计的自动布局优化流程’。”

---

## 8. 已实现边界与未实现边界（必须讲清）

当前可真实声称：

1. `mass` 模式 A/B/C/D 可执行；  
2. NSGA-II/NSGA-III/MOEAD 可切换；  
3. 约束统一 `g(x)<=0`；  
4. 几何+热+结构+电源+mission 接口进入执行链路；  
5. strict source/family/realization gate 可运行。

当前不能过度声称：

1. M4 神经模块（可行性预测器/神经算子策略/神经多保真调度）尚未实现；  
2. mission/FOV/EMC 的高保真能力仍依赖外部 evaluator，不是全内置闭环。

---

## 9. 证据路径（供答辩/汇报时随时点开）

核心运行产物：

1. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/summary.json`  
2. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/tables/phases.csv`  
3. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/tables/generations.csv`  
4. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/tables/attempts.csv`  
5. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/tables/physics_budget.csv`  
6. `experiments/run_bm_l3_operator_program_nsga3_s42_0306_nsgaiii_002/trace/iter_01_plan.json`  
7. `benchmarks/mass_benchmark_nsga3_l3_real_single_tune1/matrix_report.md`  
8. `benchmarks/mass_benchmark_nsga3_l3_real_single_tune1/matrix_runs.jsonl`

关键代码入口：

1. `run/run_scenario.py`（统一 stack/level 入口）  
2. `run/stack_contract.py`（stack-mode-config fail-fast 契约）  
3. `workflow/modes/mass/pipeline_service.py`（A/B/C/D 主管线）  
4. `workflow/modes/mass/runtime_support.py`（算法与搜索空间选择）  
5. `optimization/modes/mass/maas_compiler.py`（意图到可执行问题编译）  
6. `optimization/modes/mass/pymoo_integration/problem_generator.py`（`F/G/xl/xu`）  
7. `optimization/modes/mass/pymoo_integration/runner.py`（NSGA-III 执行与代际统计）  
8. `optimization/modes/mass/pymoo_integration/operator_program_codec.py`（动作基因编解码）  
9. `optimization/modes/mass/pymoo_integration/operator_problem_generator.py`（operator_program 搜索空间）

---

## 10. 一句话收束

MsGalaxy 的核心价值是：把“复杂工程约束下的卫星布局”从经验式试错，变成“可执行优化 + 物理校核 + 全流程可追溯”的系统工程流程。  
L3 NSGA-III 这个案例展示的，正是这条流程已经可以端到端跑通，并能给出可审计证据链，而不是只给一个看似漂亮却无法验证的坐标答案。

