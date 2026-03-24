# MsGalaxy 当前项目功能分类线详解

版本日期：2026-03-07  
适用对象：需要快速理解 MsGalaxy 当前“到底支持哪些功能、这些功能如何组合、哪些已实现哪些仍是规划”的项目成员、老师和评审  
文档定位：本文件重点回答“当前项目支持的功能分类线是什么”，并专门解释 `mode`、`NSGA-II/III/MOEAD`、`operator_program`、`hybrid`、`LLM intent`、物理评估、门禁机制之间的关系。  

---

## 1. 先给结论：MsGalaxy 不是只有一个“模式”

如果只看某个命令行，容易误以为项目只有一个 `mode`。  
但从当前代码真实实现来看，一次实验实际上是下面多条“功能分类线”的组合：

> 一次运行 = `stack/入口` + `level/场景等级` + `intent 来源` + `search space/搜索空间` + `MOEA 算法` + `physics evaluator/物理评估路径` + `strategy layers/策略增强层` + `gate/audit/门禁审计` + `knowledge/RAG`

也就是说，我们平时说“跑了一个 L3 NSGA-III”，这只是其中两条线：

1. `L3` 是场景等级线；
2. `NSGA-III` 是数值优化算法线。

但同一次运行里，还隐含着：

1. 它走的是 `mass` 栈还是 `agent_loop` 栈；
2. 它用的是 `deterministic intent` 还是 `LLM intent`；
3. 它优化的是“坐标向量”还是“算子程序向量”；
4. 它用的是 `proxy` 评估还是 `online COMSOL`；
5. 它是否启用了 `MCTS`、`meta_policy`、`physics audit`；
6. 它是否启用了 `source/operator family/operator realization` 这些严格门禁。

所以，MsGalaxy 当前最准确的理解方式，不是“我们有几个模式”，而是“我们有多条可叠加的功能线”。

---

## 2. 总览表：当前项目支持的功能分类线

| 分类线 | 主要选项 | 当前状态 | 主要控制参数/入口 | 它解决什么问题 |
| --- | --- | --- | --- | --- |
| 运行栈线 | `mass`、`agent_loop` | 已实现 | `run/run_scenario.py --stack --level` | 决定走哪套主流程 |
| 预留模式线 | `vop_maas` | 预留/预览态 | `optimization.mode=vop_maas` | 预留 LLM policy-program 方向 |
| 场景等级线 | `L1`、`L2`、`L3`、`L4` | 已实现 | `config/scenarios/registry.yaml`、`run/mass/run_L1~L4.py` | 决定问题规模、约束组合和 profile 覆盖 |
| 意图来源线 | `deterministic intent`、`LLM intent`、`LLM proof` | 已实现 | `--deterministic-intent`、`--use-llm-intent`、`--llm-proof` | 决定 `ModelingIntent` 从哪里来 |
| 搜索空间线 | `coordinate`、`operator_program`、`hybrid` | 已实现 | `optimization.mass_search_space` | 决定 pymoo 优化的变量到底是什么 |
| 数值优化算法线 | `nsga2`、`nsga3`、`moead` | 已实现 | `optimization.pymoo_algorithm` | 决定多目标优化器的选择方式 |
| 物理评估线 | `proxy`、`online_comsol` | 已实现 | `optimization.mass_thermal_evaluator_mode` | 决定热评估走快代理还是高保真在线路径 |
| 仿真后端线 | `simplified`、`comsol` | 已实现 | `--backend` | 决定场景脚本的仿真后端 |
| 在线 COMSOL 调度线 | `budget_only`、`ucb_topk` | 已实现 | `optimization.mass_online_comsol_schedule_mode` | 决定高成本评估预算如何分配 |
| MCTS 分支策略线 | 开/关 | 已实现 | `optimization.mass_enable_mcts` | 决定是否在求解外层做建模路径搜索 |
| Meta Policy 线 | 开/关 | 已实现 | `optimization.mass_enable_meta_policy` | 决定是否依据 trace 自适应调参 |
| Operator Program 分支线 | 开/关 | 已实现 | `optimization.mass_enable_operator_program` | 决定是否构造算子程序分支 |
| Seed Population 线 | 开/关 | 已实现 | `mass_enable_seed_population`、`mass_enable_operator_seed_population` | 决定是否给 pymoo 注入热启动种群 |
| Operator Bias/Credit 线 | 开/关 | 已实现 | `mass_enable_operator_credit_bias` | 决定是否根据动作族历史效果给采样/交叉/变异加偏置 |
| Physics Audit 线 | 开/关 | 已实现 | `mass_enable_physics_audit`、`mass_audit_top_k` | 决定是否对 Pareto Top-K 做后验物理审计 |
| 约束治理线 | `off/warn/strict` | 已实现 | `mass_hard_constraint_coverage_mode`、`mass_metric_registry_mode` | 决定约束覆盖和指标注册是否严格检查 |
| 物理来源门禁线 | `off/warn/strict` | 已实现 | `mass_source_gate_mode` | 决定是否允许代理/别名来源通过 |
| 算子家族门禁线 | `off/warn/strict` | 已实现 | `mass_operator_family_gate_mode` | 决定动作是否覆盖所需物理家族 |
| 算子真实落地门禁线 | `off/warn/strict` | 已实现 | `mass_operator_realization_gate_mode` | 决定动作是否在当前物理上下文中真的落地 |
| Real-only 线 | 开/关 | 已实现 | `mass_physics_real_only` | 决定是否强制真实物理来源，不接受代理回退 |
| 知识/RAG 线 | 规则检索、语义检索、运行时入库 | 已实现薄切片 | `knowledge.enable_semantic`、`mass_rag_runtime_ingest_enabled` | 决定知识证据如何参与 intent/反射链 |
| 神经策略线 | feasibility predictor、operator policy、MF scheduler | 未实现 | M4 规划 | 未来面向神经指导的增强 |

下面所有内容，都是对这张表的逐项解释。

---

## 3. 第一条线：运行栈和统一入口

### 3.1 当前真正暴露给场景注册表的两条栈

| 栈 | 注册状态 | 典型入口 | 默认 `mode` | 适合怎么理解 |
| --- | --- | --- | --- | --- |
| `mass` | 已暴露 | `run.mass.run_L1~L4` | `mass` | 当前主线，A/B/C/D 闭环 |
| `agent_loop` | 已暴露 | `run.agent_loop.run_L1~L4` | `agent_loop` | 多代理迭代外层流程 |

当前统一入口是 `run/run_scenario.py`。它通过 `--stack` 和 `--level` 查场景注册表，再分发到对应 runner。  
所以，从“怎么启动”这个维度看，项目首先分成的是“栈”，而不是“NSGA-II 还是 NSGA-III”。

### 3.2 为什么先分栈，而不是先分算法

因为算法只是 `mass` 或 `agent_loop` 内部的一层。  
例如：

1. `mass` 栈会走从 `ModelingIntent` 到 `pymoo` 再到反射审计的完整闭环；
2. `agent_loop` 栈会先走多代理协调，再调用运行时支持；
3. 两条栈可以都用 `nsga2/nsga3/moead`，但外层组织方式完全不同。

所以，栈更像“总流程模板”；算法更像“流程内部使用的数值优化引擎”。

### 3.3 `vop_maas` 在哪里

`workflow/orchestrator.py` 当前接受 `agent_loop`、`mass`、`vop_maas` 三种 `optimization.mode`。  
但这里有一个非常关键的边界：

1. `vop_maas` 当前不是 L1-L4 主入口暴露的正式主线；
2. 它现在做的是 `policy_program` 预览/诊断，然后把真正的执行委托回 `mass`；
3. 换句话说，它现在更像“预留接口 + diagnostics 通道”，而不是“已经完整接管执行的 LLM policy-program 主线”。

这点在对外说明时一定要讲清楚，否则会把规划态能力说成已落地能力。

---

## 4. 第二条线：`mass`、`agent_loop`、`vop_maas` 到底各是什么

### 4.1 模式对照表

| 模式 | 当前状态 | 核心流程 | 真实定位 |
| --- | --- | --- | --- |
| `mass` | 主线、已实现 | A 理解 -> B 形式化 -> C 编译执行 -> D 反射审计 | 当前最重要、最适合论文和答辩讲的主线 |
| `agent_loop` | 已实现 | 多代理迭代 + 物理评估 | 更偏“代理协作式系统” |
| `vop_maas` | 预留、诊断态 | policy preview -> delegate to `mass` | 预留 LLM policy-program 主线，不应过度声明 |

### 4.2 为什么 `mass` 是现在最关键的主线

因为当前仓库里最完整、最可复现、最适合对外讲“可执行优化系统”的，是 `mass`：

1. 它把约束统一到 `g(x) <= 0`；
2. 它会生成真正的 `ElementwiseProblem`；
3. 它会调用 `NSGA-II/III/MOEAD` 去做数值搜索；
4. 它带反射、放松、重试、MCTS、审计和门禁；
5. 它已经把热、结构、功率、mission alias 纳入主评估链。

换句话说，`mass` 不只是“一个函数名”，而是“当前工程化最完整的可执行闭环”。

### 4.3 为什么 `vop_maas` 不能说成已经实现

很多人容易把“代码里有这个名字”和“能力已经主线落地”混为一谈。  
现在真实情况是：

1. `vop_maas` 这条线已经有目录和服务文件；
2. 它会尝试调用 `policy_programmer.generate_policy_program(...)`；
3. 但实际执行仍然委托给 `mass`；
4. 其 delegate 当前也没有成熟的主线 policy-program API。

所以它应该被描述成：

> 已有运行时分层预留；当前是 preview/diagnostics，不是当前 L1-L4 的主执行主线。

---

## 5. 第三条线：场景等级 L1-L4

### 5.1 L1-L4 不是“难度标签”这么简单

L1-L4 不只是“问题从简单到难”的编号，它们还会决定：

1. 使用哪个 BOM；
2. 默认最大迭代数；
3. 默认是否 deterministic intent；
4. 对运行参数做什么 level profile 覆盖；
5. 哪些硬约束必须覆盖；
6. 当前场景更强调哪一类物理问题。

### 5.2 L1-L4 分类表

| 等级 | 典型含义 | 重点关注 | 默认 runner |
| --- | --- | --- | --- |
| `L1` | Foundation | 基础几何、热、结构、功率、mission 全栈契约的基础可行性 | `run.mass.run_L1` |
| `L2` | Thermal-Power | 热控热点与功率路由协同 | `run.mass.run_L2` |
| `L3` | Structural-Mission | 结构、功率、mission keepout 在高密度载荷下协同收敛 | `run.mass.run_L3` |
| `L4` | Full-Stack Operator | 高密度全算子覆盖、全物理场协同 | `run.mass.run_L4` |

### 5.3 一个容易忽略但非常关键的事实

`config/system/mass/base.yaml` 的默认 `mass_search_space` 是 `coordinate`。  
但在 `config/system/mass/level_profiles_l1_l4.yaml` 中，L1-L4 的 `runtime_overrides` 当前都把它覆盖为 `operator_program`。

这意味着什么？

这意味着当你从 L1/L2/L3/L4 主入口脚本启动时，你看到的“真实默认行为”往往不是 base 配置里的通用缺省值，而是：

> base config + level profile runtime_overrides

这也是为什么“只看 base.yaml”和“看实际 L3 默认跑法”会得出不同理解。

---

## 6. 第四条线：`ModelingIntent` 从哪里来

### 6.1 当前支持的三种意图来源状态

| 状态 | 当前是否可用 | 典型入口 | 实际含义 |
| --- | --- | --- | --- |
| `deterministic intent` | 可用 | `--deterministic-intent` | 不调 LLM，脚本内构造意图 |
| `LLM intent` | 可用 | `--use-llm-intent`、`--llm-intent` | 调真实 LLM 生成 `ModelingIntent` |
| `LLM proof / strict` | 可用 | `--llm-proof`、`--llm-proof-strict` | 不只调用 LLM，还检查它是否真正进入可执行链路 |

### 6.2 为什么“LLM 用了”不等于“LLM 有效进入执行链”

这是当前项目里非常重要的一条认识。

如果只是“调用了 LLM API”，那最多只能说明：

1. LLM 参与了文本级建模；
2. 有一个 JSON 样子的输出；
3. 这个输出可能被 fallback 或修补过。

但如果要说“LLM 真正进了执行链”，还需要更强的条件。  
这就是 `run_L3.py` 里 `--llm-proof-strict` 的意义：

1. `parsed_variables > 0`
2. `dropped_constraints = 0`
3. `unsupported_metrics = 0`
4. 没有 variable mapping fallback warning

也就是说，它不是只看“有没有调 API”，而是看“LLM 生成的意图有没有真正变成可执行优化问题的一部分”。

### 6.3 当前 LLM 在主线中的真实角色

当前主线里，LLM 最明确、最稳定的角色是：

1. 理解需求；
2. 组织 `ModelingIntent`；
3. 为变量、目标、约束提供初始语义结构；
4. 为后续反射和策略更新提供上层语义上下文。

但当前主线里，LLM 还没有完整取代规则系统去做：

1. operator-program 动作实时选择；
2. 主执行链中的策略程序编排；
3. 神经多保真预算调度。

这部分属于 `MP-OP-MaaS v3` 的批准方向，但不是当前 mainline execution 的已完成状态。

---

## 7. 第五条线：搜索空间模式才决定“NSGA 在优化什么”

### 7.1 搜索空间总表

| 搜索空间模式 | 当前状态 | pymoo 优化的对象 | 最适合怎么理解 |
| --- | --- | --- | --- |
| `coordinate` | 已实现 | 坐标/连续变量向量 | 直接在布局变量空间搜索 |
| `operator_program` | 已实现 | 动作程序基因组 | 在“算子程序空间”搜索 |
| `hybrid` | 已实现 | 分支级混合 | 某些分支搜坐标，某些分支搜算子程序 |

### 7.2 为什么这条线最重要

因为很多人会把“算法”误认为“搜索对象”。  
实际上：

1. `NSGA-III` 只说明“用哪种 MOEA 规则做种群进化”；
2. `mass_search_space` 才说明“这个 MOEA 进化的变量到底是坐标，还是动作程序”。

这两者是不同维度。

### 7.3 `coordinate` 模式在做什么

在 `coordinate` 模式下：

1. 问题生成器把设计状态编码成数值向量；
2. 这个向量更接近“组件位置/相关连续变量”；
3. pymoo 在这些数值变量上做采样、交叉、变异；
4. 解码后得到候选布局，再去算几何、热、结构、功率、mission 指标。

所以它更接近大家直觉中的“进化算法直接搜解空间”。

### 7.4 `operator_program` 模式在做什么

在 `operator_program` 模式下：

1. 优化变量不再是“最终坐标”；
2. 而是“一个算子程序的数值编码”；
3. codec 再把这组数值解码成一串动作；
4. 动作作用到设计状态上；
5. 然后再对动作作用后的候选状态做物理评估。

这意味着：

> 优化器优化的是“怎么动、动哪些组件、往哪个方向动、动多大”，而不是直接给出最终绝对坐标。

这正是当前 OP-MaaS 薄切片的核心特点。

### 7.5 `hybrid` 模式到底是什么意思

`hybrid` 不是“坐标和算子混进同一个向量”这么简单。  
当前实现更准确的含义是：

1. 先看当前 branch source；
2. 如果这个分支来自 `operator_program`，则用 operator-program problem generator；
3. 否则用 coordinate problem generator。

所以 `hybrid` 是一种“分支级混合搜索机制”。

---

## 8. 第六条线：项目自己的“领域算子”和 NSGA 自己的“遗传算子”不是一回事

### 8.1 两类算子的区别

| 类型 | 由谁提供 | 例子 | 它的作用 |
| --- | --- | --- | --- |
| 遗传算子 | pymoo / NSGA | 采样、交叉、变异 | 在种群层面生成新候选解 |
| 领域算子 | MsGalaxy 自定义 | `group_move`、`hot_spread`、`add_bracket` 等 | 把领域知识转成可执行动作 |

### 8.2 NSGA-III 自己的算子是什么

在当前 runner 里，NSGA-II/III 主要使用：

1. 采样；
2. `SBX` 交叉；
3. `PM` 变异；
4. duplicate elimination。

这些算子不懂“热”“结构”“mission keepout”是什么。  
它们只知道：

1. 有一个数值向量；
2. 我可以把不同个体的向量拼接、扰动、变异；
3. 看新的向量在目标和约束上表现如何。

### 8.3 我们自己的领域算子是什么

当前 OP-MaaS DSL v3 已落地的 10 个动作如下：

| 动作 | 家族 | 核心含义 | 当前状态 |
| --- | --- | --- | --- |
| `group_move` | geometry | 一组组件沿某轴整体移动 | 已执行 |
| `cg_recenter` | geometry | 把布局向质心更合理的位置回拉 | 已执行 |
| `hot_spread` | geometry/thermal bridge | 拉开热点组件间距 | 已执行 |
| `swap` | geometry | 交换两个组件的相对位置角色 | 已执行 |
| `add_heatstrap` | thermal | 增强热点之间热传导路径 | 已执行 |
| `set_thermal_contact` | thermal | 修改部件之间热接触参数 | 已执行 |
| `add_bracket` | structural | 引入支架以增强结构刚度 | 已执行薄切片 |
| `stiffener_insert` | structural | 插入加强件提升结构性能 | 已执行薄切片 |
| `bus_proximity_opt` | power | 调整电源/母线近接关系以改善供电指标 | 已执行 |
| `fov_keepout_push` | mission | 把组件推离任务 keepout 带 | 已执行，真实路径依赖外部 evaluator |

### 8.4 为什么必须把这两类算子分开理解

如果不分开，就会出现很多误解，例如：

1. “用了 NSGA-III，所以用了我们的热算子”  
   这句话不对。NSGA-III 本身不会自动懂热算子。

2. “没开 LLM，就只会用 NSGA 的原生算子，不会用我们定义的算子”  
   这句话也不完整。是否用到我们定义的算子，关键要看搜索空间和 branch 策略，而不是只看有没有 LLM。

最准确的说法应该是：

> NSGA-II/III/MOEAD 是数值优化器；我们定义的领域算子是被优化对象、分支动作或搜索偏置的载体。两者在系统里是“协作关系”，不是“二选一关系”。

---

## 9. 第七条线：`operator_program` 在当前系统里到底怎么进入执行链

### 9.1 当前有两种进入方式

| 进入方式 | 当前状态 | 它在系统中的位置 | 是否依赖 LLM 实时决策 |
| --- | --- | --- | --- |
| 作为搜索空间 | 已实现 | `mass_search_space=operator_program/hybrid` | 不依赖，主要由数值编码 + codec 解码 |
| 作为 MCTS 分支 | 已实现 | `mass_enable_operator_program=true` | 不依赖，当前主要是规则驱动 |

### 9.2 作为搜索空间时，它是怎么工作的

当前 operator-program codec 采用固定槽位编码：

1. 每个 action slot 用 6 个数值基因；
2. 这些基因分别决定动作类型、组件选择、轴向、幅度和聚焦比例；
3. 10 个动作槽就意味着约 60 维数值向量；
4. pymoo 在这个向量空间里做进化；
5. codec 再把它解码成真正的动作程序。

所以在这个路径里：

> 是 NSGA-II/III/MOEAD 在“选动作程序”，不是 LLM 在实时挑动作。

### 9.3 作为 MCTS 分支时，它是怎么来的

当前 `mass` 路径里，MCTS 会给某个节点提出若干建模分支。  
如果启用 `mass_enable_operator_program`，其中一种就是 operator-program 分支。

这时程序会：

1. 读取上一次评估 payload；
2. 识别 `dominant_violation`；
3. 根据主导违规家族拼一个轻量动作程序；
4. 再把这个程序作用到 `ModelingIntent` 上。

这是一个非常重要的事实：

> 当前主线里，operator-program 分支的动作选择主要由规则系统根据诊断结果构造，而不是由 LLM 直接调度。

### 9.4 当前“按违规家族拼动作”的直觉

| 主导问题 | 当前更偏向的动作 |
| --- | --- |
| `cg` | `cg_recenter`、`group_move`、`stiffener_insert` |
| `thermal` | `hot_spread`、`add_heatstrap` |
| `geometry` | `hot_spread`、`swap`、`group_move` |
| `structural` | `add_bracket`、`stiffener_insert` |
| `power` | `bus_proximity_opt`、`set_thermal_contact` |
| `mission` | `fov_keepout_push` |

这正体现了 OP-MaaS 的“神经符号”里“符号/规则”的那一部分：  
当前主线已经有可执行动作程序，但动作构造大多还是基于规则诊断，而不是完全学出来的神经 policy。

---

## 10. 第八条线：如果不启用 LLM，NSGA-III 还会不会用到我们定义的算子

这是最容易被问到的问题之一。

### 10.1 简短答案

会不会用到我们的算子，不由“是否启用 LLM”单独决定，而由以下几条线共同决定：

1. 你用的是什么搜索空间；
2. 你是否启用了 operator-program 分支；
3. 你的 level profile 有没有把搜索空间覆盖成 `operator_program`；
4. 你当前是不是在 `mass` 主线里。

### 10.2 分情况讲清楚

#### 情况 A：不开 LLM，但搜索空间是 `coordinate`

这时：

1. `ModelingIntent` 来自 deterministic 脚本；
2. NSGA-III 的主优化对象是坐标/连续变量；
3. 它主要使用的是 pymoo 自己的采样、交叉、变异；
4. 但如果 MCTS operator-program 分支开着，外层仍可能构造 operator-program 分支去改 intent。

所以这种情况下，项目自定义算子“可能出现在分支层/意图层”，但不是主基因空间。

#### 情况 B：不开 LLM，但搜索空间是 `operator_program`

这时：

1. `ModelingIntent` 仍然是 deterministic；
2. 但 NSGA-III 优化的是 operator-program 基因组；
3. 解码后自然会用到我们定义的动作；
4. 也就是说，即使完全不用 LLM，仍然可以大量使用我们定义的领域算子。

这点特别重要，因为它说明：

> 我们自己的算子体系不是依赖 LLM 才存在，而是已经能被数值优化器直接调用和搜索。

#### 情况 C：启用 LLM

这时 LLM 当前最直接的作用是：

1. 更好地组织 `ModelingIntent`；
2. 影响变量映射、约束表达、目标组织；
3. 间接影响后续搜索空间的编译质量和分支质量。

但当前主线里，LLM 还没有稳定地成为“operator selection controller”。

### 10.3 所以当前系统的真实定位应该怎么说

当前最准确的表述是：

1. 没开 LLM 时，系统仍然可以跑 `operator_program` 搜索空间，仍然会使用我们定义的动作；
2. 开了 LLM 时，当前最主要的收益预期在于 `ModelingIntent` 更贴近需求、更少变量映射错误、更少约束漏掉；
3. 未来目标才是：让 LLM 更深地参与 operator/policy 选择，使它能比规则系统更好地调度我们定义的动作族。

因此，“LLM 是否真的起调度策略作用”这个问题，当前最严谨的回答是：

> 在主线 `mass` 中，LLM 已经起到上层建模意图组织作用，但还没有完整成为主执行链里的 operator policy 调度器；这正是项目批准中的下一阶段方向。

---

## 11. 第九条线：数值优化算法线

### 11.1 当前三种算法

| 算法 | 当前状态 | 更适合怎么理解 | 当前实现特点 |
| --- | --- | --- | --- |
| `NSGA-II` | 已实现 | 通用可行性优先基线 | 逻辑直接、当前证据最稳 |
| `NSGA-III` | 已实现 | 多目标覆盖更均匀 | 使用 reference directions |
| `MOEA/D` | 已实现 | 分解式多目标搜索 | 约束路径用 penalty-objective 适配 |

### 11.2 它们改变的是什么

算法线改变的是：

1. 种群如何进化；
2. 父代和子代如何竞争；
3. 多目标之间如何取舍；
4. 前沿如何保持分布。

算法线不直接改变的是：

1. 变量是什么；
2. 是否启用我们的领域算子；
3. 是否启用 LLM；
4. 是否启用 COMSOL。

所以算法线应该被理解成“数值搜索内核”，不是整个系统的全部。

### 11.3 为什么老师容易把 `NSGA-III` 和“算子策略”混掉

因为在传统进化算法语境里，“算子”经常指交叉、变异。  
但在我们这个项目里，“算子”还有一层领域意义，指的是 `group_move`、`add_heatstrap` 这类物理语义动作。

所以你讲时最好明确说：

1. “NSGA-III 的交叉变异算子”是进化算法意义的算子；
2. “我们的 operator program 动作”是布局/物理语义意义的算子。

只有把这两者分开，对面才不会混乱。

---

## 12. 第十条线：物理评估和多保真路径

### 12.1 物理评估不是单层开关，而是三层

| 层次 | 可选项 | 作用 |
| --- | --- | --- |
| 场景仿真后端 | `simplified`、`comsol` | 决定整体运行后端 |
| 热评估模式 | `proxy`、`online_comsol` | 决定热指标用快代理还是在线高保真 |
| 在线调度模式 | `budget_only`、`ucb_topk` | 决定高成本在线调用怎么分配预算 |

### 12.2 为什么要有 `proxy`

因为高保真评估贵。  
如果所有候选点都调 COMSOL：

1. 预算不够；
2. 搜索会非常慢；
3. 大量明显不可行的点浪费高成本调用。

所以 `proxy` 的作用是：

1. 快速过滤；
2. 提供粗粒度方向感；
3. 让数值优化先在大盘上收缩到更有希望的区域。

### 12.3 为什么又要有 `online_comsol`

因为 `proxy` 再快，也只是近似。  
最终如果要严肃说“物理上成立”，必须有真实路径复核。

所以 `online_comsol` 的作用是：

1. 对关键候选做高保真校验；
2. 为 strict-real 口径提供证据；
3. 作为动作真实落地和物理来源门禁的一部分依据。

### 12.4 `budget_only` 和 `ucb_topk` 的区别

| 调度模式 | 直觉理解 | 适合场景 |
| --- | --- | --- |
| `budget_only` | 只要预算还没用完，就按规则继续调 | 简单、稳妥 |
| `ucb_topk` | 根据代理分数和不确定度，挑最值得调的一部分 | 预算更紧、希望把高保真花在刀刃上 |

这就是当前项目“多保真调度”已经有的工程薄切片。  
但要注意，它目前还是规则型和启发式的，不是神经调度器。

---

## 13. 第十一条线：策略增强层

当前系统并不是“只有一个 NSGA”。在数值优化器外面，还包了几层策略增强。

### 13.1 策略增强总表

| 机制 | 当前状态 | 它调什么 | 它不是什么 |
| --- | --- | --- | --- |
| `MCTS` | 已实现 | 建模分支选择 | 不是直接替代 pymoo |
| `meta_policy` | 已实现 | runtime knobs 自适应调优 | 不是 LLM policy |
| `operator_program` branch | 已实现 | 根据违规家族生成动作程序分支 | 当前不是 LLM 实时选动作 |
| `operator_bias` | 已实现 | 调整采样/交叉/变异偏置 | 不是独立优化器 |
| `operator_credit` | 已实现 | 记录动作历史效果并反馈偏置 | 不是神经学习器 |
| `seed_population` | 已实现 | 给初始种群热启动 | 不是最终求解逻辑 |
| `physics_audit` | 已实现 | 对 Top-K Pareto 点做后验审计 | 不是主搜索器 |

### 13.2 MCTS 在这里做什么

MCTS 当前做的是“建模路径搜索”，不是替代 NSGA。  
它主要负责：

1. 给一个节点提出若干分支候选；
2. 依据历史分支表现做 UCT 选择；
3. 使用动作先验和 CV 惩罚给分支排序；
4. 用评估结果回传更新分支价值。

这意味着，当前系统实际上是：

> 外层用 MCTS 探索“怎么建模/怎么偏置搜索”，内层用 NSGA-II/III/MOEAD 做连续数值优化。

### 13.3 `meta_policy` 在这里做什么

`meta_policy` 不是 LLM，也不是神经网络。  
它是一个规则型 runtime knob 调参器，会根据 trace 特征去调整：

1. `maas_relax_ratio`
2. `mcts_action_prior_weight`
3. `mcts_cv_penalty_weight`
4. `online_comsol_eval_budget`
5. `online_comsol_schedule_mode`
6. `online_comsol_schedule_top_fraction`
7. `online_comsol_schedule_explore_prob`
8. `online_comsol_schedule_uncertainty_weight`

它还会考虑当前算法是 `nsga2/nsga3/moead`，以及搜索空间是不是 `operator_program/hybrid`。

所以它是“算法感知 + 搜索空间感知”的规则策略层。

### 13.4 operator bias / credit 的意义

当某个 branch 是 operator-program 分支时，系统会：

1. 读取当前动作序列属于哪些物理家族；
2. 调整采样扰动强度；
3. 调整交叉和变异超参数；
4. 记录这些动作过去的平均得分、最佳 CV、可行率；
5. 再把这些信用统计回灌到下一轮偏置。

它的意义是：

> 不改变“pymoo 是数值优化核心”这个事实，但让搜索更偏向历史上更可能有效的动作族。

### 13.5 physics audit 的意义

`physics_audit` 会对最后 Pareto 集合中选出来的 Top-K 点再做一轮更严格的后验评估。  
它的目的不是“再优化一次”，而是：

1. 防止只靠前沿目标值选出来的点其实物理质量不够稳；
2. 给最终选点增加审计层；
3. 提供可追溯证据。

---

## 14. 第十二条线：门禁、严格性与“能不能过线”

### 14.1 当前门禁不是一个，而是一组

| 门禁 | 可选项 | 它管什么 |
| --- | --- | --- |
| `hard_constraint_coverage_mode` | `off/warn/strict` | 硬约束有没有被完整覆盖到执行链 |
| `metric_registry_mode` | `off/warn/strict` | 指标是否在注册表中被认可 |
| `source_gate_mode` | `off/warn/strict` | 物理来源是否允许代理/alias |
| `operator_family_gate_mode` | `off/warn/strict` | 动作是否覆盖必需物理家族 |
| `operator_realization_gate_mode` | `off/warn/strict` | 动作是否在当前上下文中真实落地 |
| `mass_physics_real_only` | `true/false` | 是否强制只接受真实物理来源 |

### 14.2 为什么这些门禁很重要

因为如果没有它们，系统很容易出现两类“假通过”：

1. 优化器看起来收敛了，但其实某些关键硬约束根本没进入执行；
2. 动作程序看起来很漂亮，但其实用了代理路径或没有真实物理证据。

所以这些门禁本质上是在回答：

> 这次运行不仅“算出来了”，而且“算得合规吗、可信吗、真的落地了吗”。

### 14.3 算子家族门禁和真实落地门禁的区别

#### 算子家族门禁

它主要看：

1. 当前动作序列覆盖了哪些家族；
2. 缺没缺 `geometry/thermal/structural/power/mission` 这些必需家族。

也就是说，它更偏“覆盖性检查”。

#### 真实落地门禁

它主要看：

1. 这些动作是不是只存在于名字层面；
2. 当前是否满足真实落地所需条件；
3. 例如热动作是否有真实热评估证据，mission 动作是否有真实 evaluator 支撑。

也就是说，它更偏“执行真实性检查”。

这两个门禁一起，才能防止“写了动作名但实际上没物理落地”的情况。

---

## 15. 第十三条线：知识检索与运行时证据

### 15.1 当前不是传统松散 RAG，而是 `CGRAG-Mass`

当前 `mass` 线路的唯一检索后端已经切成 `CGRAG-Mass`。  
它不是主数值搜索器，但会影响：

1. Phase A 的上下文供给；
2. Phase D 的反射/诊断；
3. 运行结果如何反向入库，形成后续证据记忆。

### 15.2 当前知识线的几个开关

| 功能 | 当前状态 | 主要参数 | 作用 |
| --- | --- | --- | --- |
| 语义检索 | 已实现薄切片 | `knowledge.enable_semantic` | 决定是否启用语义特征检索 |
| 运行时证据入库 | 已实现 | `mass_rag_runtime_ingest_enabled` | 决定是否把关键 attempt 自动写入证据库 |
| semantic zoning | 已实现 | `mass_enable_semantic_zones` | 决定是否启用语义分区辅助建模/编译 |

### 15.3 它在系统里的位置

知识线影响的是“怎么理解问题、怎么解释问题、怎么积累证据”，不是直接替代数值优化器。  
所以它应该被理解为“语义支持层”，不是“求解核心层”。

---

## 16. 一个具体例子：当前 L3 + NSGA-III 运行，应该怎样分类理解

为了避免概念太抽象，下面用当前主线中最典型的一类例子来说明。

### 16.1 如果我们说“跑了一个 L3 NSGA-III”，更完整的说法是什么

更完整的说法通常应该是：

| 分类线 | 本次例子的典型取值 |
| --- | --- |
| 运行栈 | `mass` |
| 场景等级 | `L3` |
| 意图来源 | `deterministic intent` 或 `LLM intent` |
| 搜索空间 | 当前默认主线更接近 `operator_program`（由 level profile 覆盖） |
| 数值算法 | `NSGA-III` |
| 仿真后端 | `simplified` 或 `comsol` |
| 热评估模式 | `proxy` 或 `online_comsol` |
| MCTS | 开 |
| Meta Policy | 开 |
| Operator Program 分支 | 开 |
| Physics Audit | 开 |
| 门禁 | 常见为 `warn` 或 strict campaign 中更严 |

### 16.2 它不是“单纯跑了个 NSGA-III”

因为这次运行至少同时包含：

1. 上层 `ModelingIntent` 组织；
2. L3 结构-任务场景 profile 覆盖；
3. operator-program 搜索空间；
4. NSGA-III 数值进化；
5. MCTS 分支选择；
6. meta-policy 自适应调参；
7. 物理评估和审计；
8. 门禁检查。

所以老师如果问“你们到底做了什么”，最准确的回答不是：

> 我们用了 NSGA-III。

而是：

> 我们用 NSGA-III 作为数值优化核心，但外层是一个带建模意图、算子程序、策略增强和物理审计的多层系统。

### 16.3 如果这个例子不开 LLM，会怎样

不开 LLM 时：

1. `ModelingIntent` 由脚本 deterministic 构造；
2. 但如果 level profile 把搜索空间设成了 `operator_program`，NSGA-III 仍然会搜索我们的动作程序；
3. 也就是说，仍然会用到我们定义的领域算子；
4. 只是“算子程序搜索”的上层语义起点来自 deterministic，而不是来自 LLM。

### 16.4 如果这个例子开 LLM，会怎样

开 LLM 时：

1. `ModelingIntent` 可能更贴近需求描述；
2. 变量、目标、约束的初始组织方式可能更合理；
3. 这会影响后续编译质量和搜索表现；
4. 但当前主线下，算子程序的主选择仍不是由 LLM 直接接管。

所以你可以把当前主线理解成：

> LLM 当前主要优化的是“怎么把问题表达给优化器”，而不是“在主执行链里完全替代规则系统挑动作”。

---

## 17. 最后总结：当前项目该怎样对外概括

如果你要给老师或评审一个足够严谨、又不至于太技术化的概括，可以这样说：

### 17.1 一句话版本

MsGalaxy 当前不是单一优化器，而是一套“多条功能线可组合”的神经符号布局优化系统：  
上层用 deterministic/LLM 生成建模意图，中层用 `mass` 主线把问题编译成可执行多目标优化问题，核心数值求解器用 `NSGA-II/NSGA-III/MOEAD`，再结合算子程序、MCTS、meta-policy、物理评估和严格门禁，搜索满足几何、热、结构、功率、任务约束的可行布局。

### 17.2 更强调当前边界的版本

当前已经实现的是：

1. `mass` 与 `agent_loop` 两条栈；
2. `coordinate/operator_program/hybrid` 三种搜索空间；
3. `NSGA-II/NSGA-III/MOEAD` 三种数值优化器；
4. 10 个 OP-MaaS DSL v3 动作；
5. MCTS、meta-policy、physics audit、source/family/realization gate；
6. deterministic intent 与 LLM intent 两条建模入口。

当前还不能过度声明的是：

1. `vop_maas` 已成为 L1-L4 主执行主线；
2. LLM 已经完整接管 operator policy/program 调度；
3. M4 神经模块已经落地；
4. mission/FOV 已经实现完全内建的高保真统一真实路径。

### 17.3 你在答辩时最值得反复强调的三句话

1. `mode` 不等于全部，系统是多条功能线叠加出来的。  
2. `NSGA-III` 是数值优化核心，不等于我们自定义的物理领域算子。  
3. 当前 LLM 主要已经作用于建模意图层；更深的 operator/policy 调度是项目批准中的下一阶段方向。  

---

## 18. 附：最常见混淆问题速查表

| 常见问题 | 正确回答 |
| --- | --- |
| “我们现在有几个模式？” | 严格说不是几个模式，而是多条功能分类线；最外层主栈是 `mass` 和 `agent_loop`。 |
| “`NSGA-III` 是不是就代表用了我们的物理场算子？” | 不是。`NSGA-III` 是优化算法；是否用到我们的算子，要看搜索空间和分支策略。 |
| “不开 LLM，系统是不是就只剩原生进化算法？” | 不是。不开 LLM 仍可使用 `operator_program` 搜索空间和我们定义的动作族。 |
| “开了 LLM，是不是它就在主链里挑动作？” | 当前主线里还不能这么说。LLM 当前主要作用在 `ModelingIntent` 层。 |
| “`hybrid` 是不是坐标和算子混在一个编码里？” | 当前实现更接近分支级混合：不同 branch 走不同 problem generator。 |
| “`vop_maas` 是不是已经可正式替代 `mass`？” | 不是。当前还是 preview/diagnostics，再委托给 `mass` 执行。 |
| “strict gate 主要在管什么？” | 管约束覆盖、指标合规、物理来源合规、算子家族覆盖和算子真实落地。 |

