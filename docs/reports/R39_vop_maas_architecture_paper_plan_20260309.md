# R39 MsGalaxy 系统架构、VOP-MaaS 论文创新与实验规划（预期方案）

## 1. 研究范围与表述口径

本文对 MsGalaxy 的系统架构、`VOP-MaaS` 方法定位、论文创新点与实验规划进行统一梳理。文本重点在于形成**面向正式学术汇报与论文写作的预期方案**，而非逐项复述当前阶段性实验进度。为避免概念混用，本文统一采用以下口径：

- `mass`：指当前仓库中最可信、最稳定的可执行数值优化主线，即 `LLM/规则建模 -> ModelingIntent -> pymoo -> multiphysics -> diagnosis`。
- `vop_maas`：指当前仓库中的 experimental mode；其真实运行方式是“**策略层控制 + 委托 `mass` 执行**”。
- `VOP-MaaS`：指拟在论文中主打的方法名称，完整含义为 **Verified Operator-Policy MaaS**。它以仓库中的 `vop_maas` 为原型，但论文写作口径应以**预期方法框架**为主，而不是被当前工程推进节奏完全绑定。

因此，本文遵循两个原则：

1. **当前真实实现**必须与仓库事实一致，不能把未完成模块写成已落地能力。
2. **论文预期方案**可以前瞻性设计，但必须建立在当前主线架构之上，不能偏离“`mass` 为可信执行内核、`VOP-MaaS` 为策略层增强”的基本边界。

---

## 2. 系统架构总结

### 2.1 问题定位

MsGalaxy 面向的是微小卫星三维布局自动设计问题。输入是需求文本、BOM 与工程约束，输出不是一组“语言模型直接猜出来的坐标”，而是经过约束建模、多目标搜索与多物理校核后得到的**可行布局候选及其诊断证据**。

该问题具有四个典型难点：

- 设计变量维度高，且连续、整数、二值变量可能混合出现；
- 几何、热、结构、功率、任务 keep-out 等约束耦合明显；
- 高保真物理评估代价高，无法简单依赖全量仿真暴力搜索；
- 论文与工程都要求可追溯、可审计、可复现实证，而不是黑箱式生成。

### 2.2 当前可信系统基线

当前最可信的系统基线仍然是 `mass`，其本质是一个**神经符号多物理约束优化框架**，可以分为四层：

| 层级 | 当前职责 | 当前口径 |
| --- | --- | --- |
| 需求与知识层 | 理解需求、读取 BOM、组织建模上下文 | LLM / structured retrieval / rule-based context |
| 建模与编译层 | 生成 `ModelingIntent`，将约束统一规范为 `g(x) <= 0`，形成可执行问题 | `ModelingIntent -> ElementwiseProblem` |
| 数值优化层 | 在显式 `xl/xu`、`F`、`G` 合同下执行多目标搜索 | `pymoo`，主核为 `NSGA-II/III`、`MOEA/D` |
| 物理评估与审计层 | 执行 proxy 评估、选择性 COMSOL 校核、top-k 审计与可行性诊断 | geometry + thermal + structural + power + mission keep-out（分阶段） |

`mass` 的运行闭环可以概括为 A/B/C/D：

1. **A — Understanding**：生成或修正 `ModelingIntent`；
2. **B — Formulation**：把硬约束统一成 `g(x) <= 0`；
3. **C — Coding/Execution**：编译为 `pymoo` 问题并执行 MOEA 搜索；
4. **D — Reflection/Audit**：对可行性、停滞、审计结果进行诊断，并在必要时触发受控重试。

这条主线的关键意义在于：**布局解始终由可执行优化问题求得，而不是由 LLM 直接生成。**

### 2.3 当前与预期的关系

为避免方法边界与工程状态混淆，本文明确区分“当前可信基线”和“预期论文方案”：

| 维度 | 当前可信基线 | 预期论文方案 |
| --- | --- | --- |
| 主运行身份 | `mass` | `VOP-MaaS` |
| LLM 角色 | 需求理解、意图组织、反射辅助 | verified operator-policy controller |
| 优化内核 | `pymoo` MOEA | 仍然是同一 `pymoo` MOEA 内核 |
| 物理反馈 | proxy + selective COMSOL | policy-guided multi-fidelity governance |
| 输出对象 | 可行布局解与诊断 | 结构化策略 + 由 `mass` 求得的布局解 |
| 方法重点 | 可执行性与约束闭环 | 搜索策略增强、首次可行效率、多保真预算治理 |

### 2.4 预期论文方法的总架构

`VOP-MaaS` 可组织为“**证据层 -> 策略层 -> 求解层 -> 保真度治理层**”的四层结构。

```mermaid
flowchart LR
    A["需求 / BOM / 约束"] --> B["结构化证据层: VOPG++"]
    B --> C["LLM 策略层: PolicyPack"]
    C --> D["Validation / Screening / Fallback"]
    D --> E["委托执行: mass compiler"]
    E --> F["MOEA 数值核心: NSGA-II"]
    F --> G["Proxy / Selective COMSOL"]
    G --> H["审计与反馈"]
    H --> B
```

其中：

- **证据层 `VOPG++`**：把违规诊断、组件状态、算子历史、来源标签与空间区域信息压缩成结构化图；
- **策略层 `PolicyPack`**：由 LLM 输出结构化策略包，而不是坐标或自由代码；
- **求解层 `mass`**：保持为唯一可执行的数值求解内核；
- **保真度治理层**：通过 screening、candidate scorer、proxy 与 selective COMSOL 做预算分配与价值判断。

---

## 3. 为什么不能只停留在 `mass`

这里要特别注意汇报措辞。论文叙事不应说“`mass` 不行”，而应说：

> `mass` 是必要且可信的执行基线，但在高代价多物理约束场景下，仅靠统一建模与通用 MOEA 搜索，仍然存在早期可行域发现效率不足、策略归因不够集中、保真度预算利用不够精细等结构性局限，因此需要在其之上引入 `VOP-MaaS` 作为策略层增强。

更具体地说，`mass` 的局限主要体现在以下四点：

### 3.1 `mass` 能解问题，但不一定最会“先找到可行解”

在高约束布局问题中，最终目标值当然重要，但更关键的是：

- 多快进入可行域；
- 为此消耗了多少高保真评估预算；
- 可行域发现是否稳定、可复现。

单纯依赖通用 MOEA，即使能够逐步找到可行解，也未必最擅长在早期样本稀疏阶段快速缩小搜索范围。

### 3.2 `mass` 有求解器，但缺少显式的“策略层”

`mass` 的当前强项是把问题编译为可执行优化合同，并保证约束、目标和物理评估都可落地；但它并不天然提供：

- 哪类违规应优先处理；
- 哪类 operator family 更值得优先探索；
- 哪些区域、组件、子结构值得更早聚焦；
- 哪些候选更值得消耗真实 COMSOL 预算。

换言之，`mass` 的数值核心是强的，但在“**搜索策略组织**”上仍主要依赖通用演化机制与固定启发。

### 3.3 `mass` 当前对多保真预算的治理仍偏被动

在高代价物理评估问题中，真正的瓶颈往往不是“有没有优化器”，而是“高保真预算是否被用在高价值候选上”。如果没有显式的策略层，那么：

- proxy 和 COMSOL 的切换更像运行时规则，而不是与违规证据强关联的决策；
- operator 选择、search-space prior 与 fidelity plan 之间缺少统一协调；
- 很难把“为什么这次应该升级保真度”写成可审计的方法贡献。

### 3.4 仅有 `mass` 的论文故事还不够强

如果论文只写 `mass`，那么叙事容易停留在：

- 一个可执行的约束布局优化系统；
- 一个多物理评估 + MOEA 的工程平台；
- 若干可行案例与诊断结果。

这当然是有价值的，但相对更像**强工程系统论文**。若希望进一步形成更鲜明的方法创新，则需要回答：

> 在不牺牲可执行性和可审计性的前提下，LLM 究竟应当插在什么位置，才能真正改善约束搜索效率？

`VOP-MaaS` 正是对这个问题的正面回答。

---

## 4. 为什么要引入 `VOP-MaaS`

### 4.1 核心思想

`VOP-MaaS` 不是要替代 `mass`，也不是要把 LLM 变成“直接摆放组件的求解器”。它的定位是：

> 让 LLM 作为一个受验证、受约束、可回退的 operator-policy controller，作用于搜索过程，而不是直接作用于最终设计解。

这一定义有三个重要收益：

- **不破坏当前可信内核**：最终布局仍由 `mass` 和 `pymoo` 求得；
- **可以做方法归因**：可以分析“哪种策略改变了哪段搜索轨迹”；
- **符合近年文献共识**：LLM 更适合生成结构化策略、启发式与保真度计划，而不是直接替代求解器。

### 4.2 预期运行逻辑

`VOP-MaaS` 的预期运行逻辑如下：

1. 从当前违规分解、组件诊断、operator 历史与 source tags 构建 `VOPG++`；
2. 让 LLM 基于 `VOPG++` 输出结构化 `PolicyPack`；
3. 对 `PolicyPack` 做 schema validation、allowlist repair、screening 与 fallback；
4. 将通过筛选的策略注入 `mass`，影响 `constraint_focus / operator_candidates / search_space_prior / runtime_knob_priors / fidelity_plan`；
5. 由 `mass` 委托 `NSGA-II` 等 MOEA 在相同约束合同下执行搜索；
6. 根据 round-level 结果，记录 `first_feasible_eval`、`COMSOL_calls_to_first_feasible`、policy attribution，并在需要时做有界 reflective replanning。

### 4.3 方法主线表述

本文采用如下主线表述：

> `VOP-MaaS` 并不以大模型替代优化器，而是在可信的 `mass` 数值优化内核之上引入一个可验证的算子策略层，使模型能够根据多物理违规证据组织 operator 优先级、搜索空间先验和保真度计划，从而更早、更稳地到达真实可行解。

---

## 5. 论文创新点分析（重点：`VOP-MaaS`）

### 5.1 四个核心创新

为保持论文主命题集中，创新点收束为四个核心创新。

#### 创新 1：提出“作用于搜索而非作用于最终解”的 verified operator-policy 框架

现有很多 LLM + 设计优化工作要么让 LLM 直接输出设计结果，要么让 LLM 生成自由代码或 reward。`VOP-MaaS` 的关键区别在于：

- LLM 不输出最终组件坐标；
- LLM 不替代 `pymoo`；
- LLM 只输出结构化、可验证、可回退的策略包；
- 策略的作用对象是搜索轨迹、候选筛选和保真度调度。

这使得方法既保留工程可信性，又形成明确的方法边界。

#### 创新 2：提出 `VOPG++` 作为多物理违规证据的结构化接口

`VOP-MaaS` 不让 LLM 直接阅读零散日志，而是通过 `VOPG++` 接收：

- constraint / metric / component / violation family / operator family；
- source realness、historical credit、estimated delta-CV、uncertainty；
- zone、keep-out、thermal path 等空间与物理上下文。

这使 LLM 输入从“松散文本提示”变成“有语义约束的证据图接口”。从论文角度看，这一层是方法可审计、可复现、可演化的关键。

#### 创新 3：提出面向昂贵仿真的 counterfactual policy screening 机制

不是把所有 LLM 输出都直接送入正式求解，而是：

- 先生成多个候选策略；
- 再通过 cheap rollout / candidate scoring / proxy diagnostics 做反事实筛选；
- 只把少量优选策略送入正式 `mass` 求解。

这一步的价值在于：它把“LLM 会提出很多策略”转化成“哪些策略值得消耗高保真预算”的可执行机制，是工程可用性的关键。

#### 创新 4：把 `first_feasible` 与高保真预算消耗提升为一等评价目标

对于多物理约束布局问题，单纯比较最终 Pareto front 不足以体现方法价值。更合理的评价维度应包括：

- `feasibility_rate`
- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`
- `best_cv`
- `real_source_coverage`

也就是说，`VOP-MaaS` 的目标不是只优化“最后最优值”，而是优化“**多快、以多小代价找到可信可行解**”。这比传统只看最终目标值更契合工程问题本质。

### 5.2 扩展创新与后续工作

以下内容可作为第二阶段增强，或写入扩展实验 / future work，而不宜作为首稿必须全部实证完成的核心创新：

- 多轮 reflective replanning；
- policy memory / template evolution；
- 神经可行性预测器；
- 神经算子策略网络；
- 完整多输出多保真 residual ranker。

原因不是这些方向不重要，而是它们会显著扩张论文范围，弱化“verified operator-policy + first-feasible efficiency”这一更集中、更稳的主命题。

### 5.3 论文贡献表述

论文贡献可概括为：

1. 提出一种面向多物理硬约束布局优化的 `VOP-MaaS` 框架，在不替代 MOEA 数值内核的前提下，用 verified operator-policy 对搜索过程进行结构化控制。
2. 设计 `VOPG++` 与 `PolicyPack` 两个接口，使多物理违规证据与策略注入都具有明确的结构化合同、验证路径与回退机制。
3. 引入 counterfactual policy screening 与 source-aware `first_feasible` 评价口径，使 LLM 增强能够在昂贵仿真预算下保持工程可执行性。
4. 在多层级卫星布局任务上验证该方法相对 `mass` baseline 在首次可行效率、高保真预算利用与轨迹归因上的优势。

---

## 6. 预期实验设置：baseline、消融与评价计划

### 6.1 总体实验原则

实验设计要服务于一个核心问题：

> `VOP-MaaS` 的收益是否真的来自“策略层增强”，而不是来自算法、预算或任务设置的变化？

因此，所有主实验均应遵守以下公平性原则：

- 同一 BOM；
- 同一 level profile；
- 同一硬约束合同；
- 同一随机种子集合；
- 同一总评估预算；
- 同一高保真预算上限；
- 同一数值内核。

**主论文以 `NSGA-II` 作为统一数值核心。**  
理由是：预期方案强调的是策略层贡献，而不是算法横向比较。`NSGA-III`、`MOEA/D` 更适合作为附录或补充泛化实验，而不应在主实验中与策略层增益混在一起。

### 6.2 Baseline 设计

主实验至少包含以下五组：

| 组别 | 定义 | 作用 |
| --- | --- | --- |
| `B1: mass_det` | `mass` + deterministic intent / 无 LLM 策略层 | 最强非 LLM 执行基线 |
| `B2: mass_llm_intent` | `mass` + LLM 仅参与需求理解/建模意图，不参与策略注入 | 隔离“前端语义理解”收益 |
| `B3: mass_static_policy` | `mass` + 人工/模板化静态 policy prior | 对比 LLM 策略是否优于固定启发 |
| `B4: vop_noscreen` | `VOP-MaaS`，有 `PolicyPack`，但不做 screening | 隔离 screening 的贡献 |
| `B5: vop_full` | `VOP-MaaS` 全流程：`VOPG++ + PolicyPack + validation + screening + delegated mass` | 主方法 |

如果实验资源允许，可追加一组增强版：

| 组别 | 定义 | 作用 |
| --- | --- | --- |
| `B6: vop_reflective` | `vop_full` + bounded reflective replanning | 检验二阶段反馈收益 |

### 6.3 不纳入主 baseline 的对照

以下对照可以作为补充负例或附录，但不进入主表：

- `agent_loop`：其定位已偏向 legacy compatibility，不适合作为面向未来主线的公平对照；
- `LLM -> final coordinates`：与系统边界冲突，更多像负控制实验，不宜占据主论文篇幅；
- “换优化器即换方法”类对照：会混淆“策略层贡献”和“数值算法贡献”。

### 6.4 任务矩阵与数据划分

实验采用 `L1-L4` 分层任务矩阵：

| 层级 | 主要约束压力 | 主要用途 |
| --- | --- | --- |
| `L1` | geometry + thermal + cg 基础约束 | 观察早期可行域发现 |
| `L2` | 加入 power 相关压力 | 检验跨物理扩展性 |
| `L3` | 加入 structural / mission 压力 | 检验复杂耦合场景 |
| `L4` | 全约束合同 | 作为最强综合验证 |

实验节奏如下：

- `L1-L2`：每组 `5` 个 seeds；
- `L3-L4`：每组 `3-5` 个 seeds；
- 每个 seed 保持相同预算与相同 physics gate。

### 6.5 主指标与辅助指标

评价指标分为三层。

#### 第一层：主指标

- `feasibility_rate`
- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`

这三项是论文主结果的核心，因为它们直接回答“是否更快、更省、更稳地到达可信可行解”。

#### 第二层：求解质量指标

- `best_cv`
- `best_feasible_objective_vector`
- feasible-set hypervolume（仅在可行解集上统计，作为辅助）
- final audit status

#### 第三层：策略有效性与工程治理指标

- `policy_validity_rate`
- `policy_fallback_rate`
- `real_source_coverage`
- `operator_family_coverage`
- runtime overhead

### 6.6 核心消融

围绕方法模块设置六类消融：

| 消融编号 | 变体 | 要回答的问题 |
| --- | --- | --- |
| `A1` | 去掉 `VOPG++`，改为简化文本摘要输入 | 结构化证据接口是否必要 |
| `A2` | 去掉 screening | screening 是否是工程收益关键 |
| `A3` | 去掉 `fidelity_plan` | 多保真预算治理是否带来真实收益 |
| `A4` | 只保留 `operator prior` | 收益主要来自 operator 建议还是其他先验 |
| `A5` | 只保留 `runtime prior` | runtime knob 调整是否单独有效 |
| `A6` | 去掉 reflective replanning | 二阶段反馈是否值得额外复杂度 |

如果篇幅允许，可增加两类分析型消融：

- 去掉 `source_realness` 与 provenance 字段，观察真实源标签是否影响稳定性；
- 去掉 operator history / credit，观察历史经验是否改善策略选择。

### 6.7 统计与结果呈现

统计与结果呈现采用以下规范：

- paired seeds 对照；
- 报告均值、标准差与 `95%` bootstrap CI；
- 对主指标使用 Wilcoxon signed-rank test 或 permutation test；
- 同时报告 effect size，而不只给 `p-value`。

主图表包括：

1. `first_feasible_eval` 箱线图；
2. `COMSOL_calls_to_first_feasible` 箱线图；
3. feasibility rate 柱状图；
4. 预算-性能曲线；
5. operator family × violation family 的 credit heatmap；
6. 典型案例的 round-level policy attribution 图。

---

## 7. 论文叙事逻辑：为什么要从 `mass` 走向 `VOP-MaaS`

### 7.1 一句话主线

全文叙事主线如下：

> `mass` 已经证明“多物理硬约束布局问题可以被可靠地编译并执行求解”；而 `VOP-MaaS` 要解决的是“在高代价物理预算下，如何让策略层显式介入搜索组织与保真度治理，从而更快、更稳地找到真实可行解”。

### 7.2 论文逻辑链条

论文按照如下逻辑推进：

1. **问题提出**  
   微小卫星三维布局是高维、强约束、昂贵评估的问题，单纯生成式方法难以满足工程可信性。

2. **可信基线建立**  
   `mass` 已经给出一个可信的执行基线：显式变量、显式目标、显式约束、可审计的多物理评估。

3. **基线不足暴露**  
   但是，在高代价场景下，`mass` 仍缺少显式策略层，导致 early feasible search 与高保真预算分配效率受限。

4. **方法提出**  
   因此提出 `VOP-MaaS`：让 LLM 不直接输出解，而是生成 verified operator-policy，通过 screening 和 fallback 有界地影响搜索。

5. **实验验证**  
   在保持数值内核、约束合同和预算上限不变的前提下，比较 `mass` 与 `VOP-MaaS` 的首次可行效率、高保真预算消耗和最终求解质量。

6. **结果解释**  
   若 `VOP-MaaS` 的优势主要体现在 `first_feasible_eval`、`COMSOL_calls_to_first_feasible` 和轨迹归因，则说明其真正贡献是“策略层提升搜索效率”，而不是“碰巧换了一套优化器”。

### 7.3 需要避免的叙事误区

论文中应避免以下三种写法：

#### 误区 1：把 `mass` 写成失败方案

错误写法是：“因为 `mass` 不行，所以我们换 `VOP-MaaS`。”  
更严谨的写法应是：“`mass` 作为可信执行基线是成立的，但在高代价多物理搜索中仍存在策略层空缺，因此引入 `VOP-MaaS` 进行增强。”

#### 误区 2：把 `VOP-MaaS` 写成“大模型直接做布局”

错误写法会导致审稿人自然追问：

- 为什么不用更强的生成模型？
- 为什么不直接 end-to-end？
- 如何保证硬约束与高保真可行性？

因此必须坚持：`VOP-MaaS` 的对象是 **policy over search**，不是 **solution over geometry**。

#### 误区 3：把所有未来模块都写成本文核心贡献

如果把 policy memory、神经 ranker、多轮反思、全多保真大模型调度全部同时写进核心贡献，论文会变得过宽。更稳妥的做法是：

- 首稿聚焦 `verified operator-policy + screening + first-feasible efficiency`；
- 其余模块作为扩展实验或后续工作递进展开。

### 7.4 论文章节组织

论文结构如下：

1. **Introduction**：问题背景、工程约束、研究空缺、核心贡献；
2. **Background and Baseline**：介绍 `mass` 的执行合同与当前多物理布局问题；
3. **Method**：提出 `VOPG++`、`PolicyPack`、screening、delegated `mass`、fidelity plan；
4. **Experimental Setup**：任务层级、baselines、指标、统计检验；
5. **Main Results**：主结果对比；
6. **Ablation and Attribution**：消融、operator credit、case study；
7. **Discussion**：适用边界、失败模式、工程与学术意义；
8. **Conclusion**：总结与下一步。

---

## 8. 系统定位总结

MsGalaxy 当前已经形成一个可信的 `mass` 基线，能够将卫星布局问题从需求、BOM 与工程约束编译为显式的多目标约束优化问题，并在 `pymoo` 与多物理评估闭环下求得可行布局。下一阶段的研究重点并非以大模型替代求解器，而是在此基础上引入 `VOP-MaaS`。`VOP-MaaS` 的核心思想是：基于多物理违规证据生成可验证的算子策略、搜索先验与保真度计划，再通过筛选、验证与回退机制将这些策略有界地注入 `mass`，从而提升首次可行解到达效率与高保真预算利用效率。由此，`mass` 回答的是“多物理硬约束布局问题能否被可靠求解”，而 `VOP-MaaS` 回答的是“在昂贵约束场景下能否更快、更稳、更有归因地求解”。

---

## 9. 结论

综合当前系统状态与预期论文路线，后续汇报与论文应统一坚持以下三条原则：

1. **坚持 `mass` 是可信执行内核**：不要把 `VOP-MaaS` 讲成替代者，而要讲成增强者。
2. **坚持 `VOP-MaaS` 的对象是搜索策略而不是最终解**：这是方法边界清晰、工程上可审计、论文上可发表的关键。
3. **坚持以 `first_feasible` 与高保真预算为主指标**：这决定了论文从“工程系统描述”提升为“方法层贡献验证”。

据此，整篇论文可以形成如下完整叙事逻辑：

- `mass` 证明问题可解；
- `VOP-MaaS` 证明问题可以解得更有策略、更有效率；
- 二者合起来形成一个既可信又有方法创新的多物理布局优化框架。
