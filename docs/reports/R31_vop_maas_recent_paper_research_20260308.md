# R31 VOP-MaaS 近三年论文深度调研与路线收敛（2026-03-08）

## 1. 目标与范围

本报告面向 `MsGalaxy` 当前 `vop_maas -> mass` 主链，聚焦近三年（2023-01-01 至 2026-03-08）与以下问题最相关的高质量论文：

- 小卫星/航天器布局与热-结构协同优化；
- 航天系统级多学科设计优化（MDO）；
- 多保真 surrogate / 昂贵仿真优化；
- LLM 在工程布局/设计中的合适角色边界。

本次调研不是泛泛“搜网页”，而是以**论文全文、官方摘要、官方索引方法片段**为主证据，目标是回答一个工程问题：

> 对 `MsGalaxy` 而言，最优路线到底应是“LLM 直接布局”，还是“LLM 生成策略/算子/保真度计划，数值优化器继续做可审计求解”？

---

## 2. 调研方法与证据等级

### 2.1 筛选标准

- 时间：2023-01-01 至 2026-03-08；
- 主题：卫星布局、热/结构/功率等工程约束、多保真优化、LLM 辅助工程设计；
- 质量：优先期刊/会议正式论文，且尽量选择官方 DOI / 官方论文页；
- 可迁移性：必须能落到 `VOPG / PolicyPack / pymoo / proxy->COMSOL` 这条现有主链。

### 2.2 证据等级

- `A`：已获取本地全文并抽文本深读；
- `B`：已通过官方全文页/官方 PDF 页面阅读方法与结论；
- `C`：官方摘要 + 官方站点索引出的关键方法片段（受站点反爬限制，工具侧无法稳定直下全文）。

### 2.3 本轮已获取的本地全文

临时下载与文本抽取目录：

- `C:\Users\hymn\AppData\Local\Temp\msgalaxy_papers_20260308`

其中本轮成功本地全文抽取的主论文包括：

- `Applications of multi-fidelity multi-output Kriging to engineering design optimization`
- `Large Language Model (LLM) for Standard Cell Layout Design Optimization`

---

## 3. 主读论文池（5 篇）

| 编号 | 论文 | 年份 | 证据 | 与项目的直接关系 |
| --- | --- | --- | --- | --- |
| P1 | [A satellite layout-structure integrated optimization method based on thermal metamaterials](https://doi.org/10.1016/j.cja.2025.103842) | 2025 online / 2026 issue | C | 最接近“卫星布局 + 热-结构一体化” |
| P2 | [Thermal Optimization Design for a Small Flat-Panel Synthetic Aperture Radar Satellite](https://doi.org/10.3390/aerospace11120982) | 2024 | B/C | 小卫星热设计与低成本热代理建模 |
| P3 | [Multidisciplinary design and optimization of intelligent Distributed Satellite Systems for EARTH observation](https://doi.org/10.1016/j.actaastro.2023.12.055) | 2024 | C | 航天系统级 NSGA-II + surrogate MDO |
| P4 | [Applications of multi-fidelity multi-output Kriging to engineering design optimization](https://doi.org/10.1007/s00158-023-03567-z) | 2023 | A | 多保真/多输出 surrogate 在工程优化中的系统比较 |
| P5 | [Large Language Model (LLM) for Standard Cell Layout Design Optimization](https://doi.org/10.48550/arXiv.2406.06549) | 2024 | A | LLM 作为“布局策略/约束生成器”而非直接求解器 |

补充参考但不作为主结论承载论文：

- [Generative AI-Enabled Facility Layout Design Paradigm for Dynamic Manufacturing Environments](https://doi.org/10.3390/app15105697)（2025）

---

## 4. 逐篇深度分析

## 4.1 P1：卫星布局-结构-热超材料一体化优化

论文：

- [A satellite layout-structure integrated optimization method based on thermal metamaterials](https://doi.org/10.1016/j.cja.2025.103842)

### 4.1.1 论文做了什么

从官方摘要与官方检索片段可确认，该文不是单纯做“热分析后微调布局”，而是把：

- **离散部件布局**；
- **支撑结构拓扑**；
- **局部导热能力分配**

放进一个统一框架里联合求解。官方片段显示其方法核心为：

- 用 **genetic algorithm** 处理部件布局；
- 用 **BSTO**（官方片段写法）处理结构/材料拓扑；
- 在需要热耗散的位置**最大化局部热导能力**。

这篇论文最重要的地方不在于具体 GA/BSTO 细节，而在于它证明了：

> 航天器内部布局问题，真正有工程价值的不是“组件坐标优化”本身，而是“组件位置 + 导热通路 + 支撑结构”一体联动。

### 4.1.2 对 `MsGalaxy` 的启示

这与 `MsGalaxy` 当前 `thermal / structural / power / mission` 多类违规诊断非常契合。它意味着当前 `operator family` 里的：

- `add_heatstrap`
- `set_thermal_contact`
- `add_bracket`
- `stiffener_insert`

不应长期停留在“启发式局部动作”层，而应逐步升级成**参数化的热-结构联合设计变量/动作变量**。

也就是说，未来 `VOPPolicyPack` 不应只说“把热点件挪开”，还应允许表达：

- 是否增加导热桥；
- 是否提高局部接触导热；
- 是否新增/加厚支撑件；
- 是否在满足刚度前提下改变热流路径。

### 4.1.3 可迁移部分

- 强可迁移：**布局与结构/热通路联动建模思想**；
- 强可迁移：把“热导路径”当成设计变量而非后处理补丁；
- 中等可迁移：离散布局变量与连续结构变量分层求解。

### 4.1.4 不可直接照搬部分

- 文中并不是面向 LLM/策略层设计；
- 未证明适用于 `thermal + structural + power + mission keep-out` 的完整硬约束合同；
- 论文更像“联合优化建模模板”，不是 `vop_maas` 的直接软件架构模板。

### 4.1.5 对项目的结论

这篇论文支持 `MsGalaxy` 继续坚持：

- **不能把问题降格为纯坐标搜索**；
- 要把**热路径与结构补强**正式并入动作空间与约束空间；
- `VOP-MaaS` 应该是“operator-policy over a multi-physics design space”，而不是“LLM 摆坐标”。

---

## 4.2 P2：小型平板 SAR 卫星热优化设计

论文：

- [Thermal Optimization Design for a Small Flat-Panel Synthetic Aperture Radar Satellite](https://doi.org/10.3390/aerospace11120982)

### 4.2.1 论文做了什么

从官方页与官方检索片段可确认，这篇论文的关键点是：

- 先建立**基于自适应拟合热导率的热阻网络模型**；
- 再用 **particle swarm optimization** 做热设计优化；
- 最后用**综合数值模型**做校核。

这说明作者采用了典型的：

> 便宜热代理模型 -> 优化搜索 -> 高保真数值验证

工作流，而不是直接把所有候选都扔给高成本热仿真。

### 4.2.2 为什么它对我们重要

这篇论文虽然主要聚焦热设计，但它给 `MsGalaxy` 一个非常强的工程信号：

- **热代理网络**不是“简化版玩具模型”，而是可以成为正式优化前端；
- 便宜代理模型足以承担大部分搜索预算；
- 高保真热仿真更适合承担**验证、筛选和边界纠正**角色。

这与我们已经在做的 `proxy / online COMSOL` 双层结构天然一致。

### 4.2.3 可迁移部分

- 强可迁移：构建**低成本热网络代理**作为 `mass` 常驻 evaluator；
- 强可迁移：把高保真 COMSOL 用在 top-K 或边界样本上；
- 中等可迁移：温度相关/材料相关参数通过拟合修正代理模型。

### 4.2.4 不可直接照搬部分

- 该文主要是热学单学科，不是完整多物理合同；
- 优化器用的是 PSO，而 `MsGalaxy` 当前更适合保留 `pymoo NSGA-II/III/MOEAD` 主核；
- 文中没有 LLM/策略层，不涉及 `operator-policy` 注入。

### 4.2.5 对项目的结论

这篇论文最直接支持：

- `proxy thermal` 必须继续做强，而不是尽快“全量 COMSOL”；
- `fidelity_plan` 应该成为 `VOPPolicyPack` 的一等公民；
- `first_feasible_eval / COMSOL_calls_to_first_feasible` 这种预算敏感指标是正确方向。

---

## 4.3 P3：智能分布式卫星系统多学科设计优化

论文：

- [Multidisciplinary design and optimization of intelligent Distributed Satellite Systems for EARTH observation](https://doi.org/10.1016/j.actaastro.2023.12.055)

### 4.3.1 论文做了什么

官方摘要与官方检索片段给出的关键信息比较清晰：

- 这是一个**航天系统级 bi-objective MDO**；
- 目标是**最大化 revisit performance**、**最小化 life-cycle cost**；
- 设计空间包含 **24 个系统级设计参数**；
- 方法组合是 **surrogate models + NSGA-II**；
- 结论之一是引入**reconfigurable communication** 与较传统子系统配置的组合，可以同时改善成本与性能。

### 4.3.2 对 `MsGalaxy` 的启示

它虽不是 3D 布局问题，但对我们的主线判断非常关键：

- 在航天系统设计里，**NSGA-II 仍然是完全合理的主优化内核**；
- surrogate model 适合包在外层，做大范围设计空间探索；
- 高维系统参数下，先做**系统级 trade study**，再下钻到几何级布局，是合理分层。

### 4.3.3 可迁移部分

- 强可迁移：`NSGA-II + surrogate` 的主骨架；
- 强可迁移：在多目标场景下保留 Pareto 思维而不是单目标化；
- 中等可迁移：把通信/功耗/任务类变量与几何变量分层。

### 4.3.4 不可直接照搬部分

- 该文的变量主要是系统级参数，不是内部组件布局；
- 几何碰撞、间隙、边界、CG 等 layout hard constraints 不是本文重点；
- 不涉及 LLM、算子搜索、反思重规划。

### 4.3.5 对项目的结论

这篇论文进一步说明：

- **保留 `pymoo NSGA-II` 作为数值核心是正确的**；
- `vop_maas` 不应替代 `mass`，而应增强 `mass` 的搜索效率与多保真预算使用效率；
- 我们完全可以把 `PolicyPack` 定位成“NSGA-II 的外层指导器”，这与当前系统级航天优化文献一致。

---

## 4.4 P4：多保真多输出 Kriging 在工程设计优化中的应用

论文：

- [Applications of multi-fidelity multi-output Kriging to engineering design optimization](https://doi.org/10.1007/s00158-023-03567-z)

### 4.4.1 论文做了什么

这篇论文本轮已下载全文并本地抽文本深读。其核心贡献非常扎实：

- 不是只比较 surrogate 的拟合精度，而是比较其**优化性能**；
- 系统比较了五类模型：
  - 普通单输出 Kriging；
  - 单输出多保真 Kriging；
  - `MFMO Kriging`；
  - `MO Kriging(x)`；
  - `MO Kriging(x×y)`；
- 覆盖三个工程案例：
  - 多点翼型并行优化；
  - 材料可变的振动桁架优化；
  - 带拓扑变化的燃烧室优化。

### 4.4.2 关键方法细节

全文显示其方法不是直接用 surrogate 替代优化器，而是：

- 先建立 surrogate；
- 再在 surrogate predictor 上做全局搜索；
- 再把 surrogate 推荐点作为 update point 回灌高保真/真实函数。

文中一个很值得注意的实现细节：

- 翼型案例中假定总高保真预算为 `45` 次，其中 `25` 次用于初始采样、`20` 次用于更新；
- 他们用 `Matlab genetic algorithm`，`population size = 200`，`1000 generations` 来定位 surrogate predictor 的最优点；
- 多输出模型会把不同类别/材料/保真层的更新点统一回灌到整体数据集中。

### 4.4.3 最重要的结论

论文结论并不是“传统 difference-based MF 一定最好”。相反，它指出：

- 当不同输出/类别之间相关性足够强时，**把 fidelity 当作 categorical output 的纯 multi-output 模型**，往往比经典多保真差分模型更好；
- 在三类案例上，**pure multi-output approach** 的整体优化表现经常优于更传统的 MFMO 差分建模；
- 作者还明确提出：优化过程中可能应**动态切换模型族**，在数据稀疏期与数据充足期采用不同 surrogate。

### 4.4.4 对 `MsGalaxy` 的直接价值

这是本轮调研里，对 `VOP-MaaS` 架构最具可执行价值的论文之一。

它直接支持我们把 `proxy / real physics / scenario family / operator family` 视为一种“相关输出/类别”问题，而不是简单二元“低保真 vs 高保真”问题。对应到 `MsGalaxy`，可以形成如下设计：

- surrogate 不只预测单一温度，而是预测**多物理残差向量**：
  - `thermal_max_residual`
  - `cg_residual`
  - `safety_factor_residual`
  - `modal_freq_residual`
  - `voltage_drop_residual`
  - `power_margin_residual`
- fidelity 不仅仅是 `proxy / comsol`，还可作为 categorical variable；
- `operator family`、`stack/scenario`、`BOM archetype` 也可以成为相关类别。

### 4.4.5 迁移建议

这篇论文直接建议 `MsGalaxy` 下一阶段做的不是“纯 Bayesian optimization 替换 MOEA”，而是：

- 在 `mass` 外围加入**多输出多保真风险排序器**；
- 用它来预测候选解/候选算子的**约束违反下降潜力**与**真实仿真价值**；
- 在搜索早期偏向多输出/跨类别共享，在搜索后期允许切换为更局部、更精细的单输出或局部 surrogate。

### 4.4.6 局限

- 论文案例主要还是 surrogate-assisted optimization，不是 LLM-guided optimization；
- 多数实验仍偏单目标或经加权处理，不是完整硬约束多目标 MOEA 合同；
- 未直接讨论物理求解器调用治理、审计与 fallback。

---

## 4.5 P5：LLM 用于标准单元布局设计优化

论文：

- [Large Language Model (LLM) for Standard Cell Layout Design Optimization](https://doi.org/10.48550/arXiv.2406.06549)

### 4.5.1 为什么这篇论文极其重要

这是本轮最像 `VOP-MaaS` 的论文，不是因为它和卫星同域，而是因为它的**角色分工**几乎与我们希望的路线完全同构。

它没有让 LLM 直接输出最终版图，而是让 LLM：

- 读入结构化上下文；
- 借助工具调用；
- 生成更好的 **cluster constraints**；
- 用便宜评分器筛选；
- 再把结果交给真正的 layout automation backend。

这与我们现在的目标非常接近：

> LLM 负责 `operator-policy / constraint focus / search-space prior / fidelity plan`，而 `mass`/`pymoo` 继续负责可审计求解。

### 4.5.2 关键方法细节

全文显示，该文的主流程包括：

- `knowledge extraction`：把 netlist topology、physical layout、routability report 与设计者经验整理成 prompt；
- `netlist tools`：提供若干结构化工具，如：
  - `cluster evaluator`
  - `get group devices from nets`
  - `save potential cluster`
  - `get best cluster result`
- `ReAct`：让 LLM 通过 `Thought -> Action -> Observation` 迭代搜索；
- 便宜 evaluator 不是最终 PPA，而是一个**simple cluster score**，用于近似捕捉 diffusion sharing 与 common gate；
- 当候选足够好后，再交给 `NVCell` 生成并验证布局。

### 4.5.3 实验设置与结果

全文给出的实验设置包括：

- `Python + LangChain` 实现；
- LLM 使用 `gpt-3.5-turbo-16k-0613`；
- 采样温度 `0.1`；
- ReAct 最多 `15` 步；
- 在 `17` 个复杂 sequential standard cells 上实验。

论文报告的关键结果包括：

- 最多实现 **19.4%** 更小 cell area；
- 生成 **23.5%** 更多 LVS/DRC clean layouts；
- 平均 cell area 下降约 **4.65%**；
- 在选定基准上把 success rate 从 **76.5%** 提升到 **100%**。

### 4.5.4 对 `MsGalaxy` 的直接映射

这篇论文对 `VOP-MaaS` 几乎是直接可翻译的：

- 它证明了 **LLM 适合做“结构化策略搜索代理”**；
- 它证明了 **cheap evaluator + tool-use + ReAct** 是比“直接让 LLM 出最终设计”更稳的路线；
- 它证明了 **先筛选 cluster/strategy，再调用昂贵 layout backend**，比一上来跑昂贵后端更有效。

映射到 `MsGalaxy`：

- `knowledge extraction` -> `VOPG / CGRAG-Mass / violation digest`
- `netlist tools` -> `operator simulator / metric estimator / genome patcher / candidate scorer`
- `simple cluster score` -> `expected ΔCV / predicted first_feasible_eval / COMSOL value-of-information`
- `NVCell backend` -> `mass + pymoo + proxy/COMSOL evaluator`

### 4.5.5 对项目的最强结论

这篇论文给出的最强结论是：

> **LLM 最合适的角色不是替代数值优化器，而是为数值优化器生成更好的结构化搜索策略。**

这与 `MsGalaxy` 当前 `vop_maas` 路线高度一致，而且比“LLM 直接摆组件坐标”更容易审计、复现和写成论文。

### 4.5.6 局限

- 领域是 EDA，不是航天布局；
- 便宜评分器仍是人工设计的近似指标；
- 没有处理真实多物理约束与多保真物理求解。

---

## 5. 横向综合：五篇论文共同指向的路线

把五篇论文放在一起看，会得到非常一致的结论。

### 5.1 结论一：LLM 最适合做“策略层”，不适合替代求解器

最强证据来自 P5，补充支持来自 P1/P3：

- P5 证明 LLM 通过工具与中间评分器生成更优结构化约束是有效的；
- P1/P3 则说明工程设计问题的可用解空间由多物理耦合、系统 trade-off 和硬约束支配，不是简单语言生成问题。

因此：

- **不建议**走 `LLM -> final coordinates`；
- **建议**走 `LLM -> Policy / Operator / Fidelity Plan -> MOEA Search`。

### 5.2 结论二：真正的工程增益来自“多保真预算治理”

最强证据来自 P2/P4：

- P2 证明低成本热网络代理能承担绝大多数搜索工作；
- P4 证明多保真/多输出 surrogate 不是只提升拟合精度，而能提升优化效率本身。

因此：

- `proxy -> surrogate/ranker -> selective COMSOL` 应成为主线；
- `COMSOL_calls_to_first_feasible` 必须是核心审计指标。

### 5.3 结论三：多物理动作必须进入搜索空间，而不是后处理补丁

最强证据来自 P1：

- 热路径、支撑结构、局部导热能力都应作为设计变量；
- 这直接支持把热/结构动作正式纳入 `operator family`。

因此：

- `add_heatstrap / set_thermal_contact / add_bracket / stiffener_insert`
  应逐步从 heuristic action 升级为参数化可学习动作。

### 5.4 结论四：类别相关性值得利用，但不能无节制扩维

最强证据来自 P4：

- fidelity、材料、拓扑、类别之间的相关性能显著提升优化效率；
- 但 categorical levels 太多时，会导致超参数优化复杂度与稳定性问题上升。

因此：

- `MsGalaxy` 应采用**分层类别化**而不是“一锅端大一统类别变量”；
- 推荐先从：
  - `fidelity level`
  - `violation family`
  - `operator family`
  这三类做起。

### 5.5 结论五：系统级与几何级优化应分层，而非混为一体

最强证据来自 P3：

- 系统级参数优化与几何级布局优化是两个不同尺度的问题；
- surrogate + NSGA-II 可以先做系统级缩圈，再进入布局级精化。

因此：

- `VOP-MaaS` 里要区分：
  - 系统级先验/策略；
  - 布局级 operator / zone prior；
  - 物理级验证与预算。

---

## 6. 对 `MsGalaxy` 的最终推荐方案

## 6.1 推荐路线名称

建议把下一阶段方法叙事明确收敛为：

> **Operator-Policy-Guided Multi-Fidelity Constrained MOEA**

中文可表述为：

> **面向多物理硬约束布局优化的“算子策略引导 + 多保真调度 + MOEA 数值核心”框架**

### 6.1.1 其核心思想

- `LLM` 不输出最终布局；
- `LLM` 输出结构化 `PolicyPack`；
- `mass/pymoo` 保持数值求解核心；
- surrogate/ranker 负责保真度分配与候选筛选；
- `COMSOL` 只用于高价值样本与审计闭环。

---

## 6.2 推荐的四层架构

### A. 证据层：`VOPG++`

在现有 `VOPG` 上继续增强，节点建议至少包括：

- `component`
- `constraint`
- `metric`
- `violation_family`
- `operator_family`
- `fidelity_source`
- `zone / keepout / thermal path`

边属性建议保留或增强：

- `severity`
- `sensitivity`
- `historical_credit`
- `source_realness`
- `estimated_delta_cv`
- `uncertainty`

### B. 策略层：`PolicyPack v2`

建议 `PolicyPack` 至少稳定输出：

- `constraint_focus`
- `operator_candidates`
- `search_space_prior`
- `runtime_knob_priors`
- `fidelity_plan`
- `screening_budget`
- `rollback_conditions`
- `confidence / rationale / expected_effects`

### C. 求解层：`mass + NSGA-II`

继续保留：

- 显式 `xl/xu`
- 显式 `F`
- 显式 `G`
- `g(x) <= 0` 硬约束合同
- `eliminate_duplicates=True`

不建议让 BO/LLM 替代该层。

### D. 保真度治理层：`Surrogate/Ranker + Selective COMSOL`

建议新增一个围绕候选解与候选动作的多输出排序器，重点预测：

- 约束残差下降潜力；
- 首次可行解命中概率；
- 真实 COMSOL 调用价值；
- operator family 在当前 violation family 下的成功率。

---

## 6.3 最推荐的算法实现细化

### 6.3.1 数值内核

- 默认 `NSGA-II`；
- `NSGA-III / MOEA/D` 作为对照或特定场景补充；
- 严格保持可审计的 `g(x) <= 0` 硬约束表达。

### 6.3.2 多保真排序器

参考 P4，建议先做 **multi-output multi-fidelity residual model**，预测：

- `thermal_max - threshold`
- `cg_offset - threshold`
- `threshold - safety_factor`
- `threshold - modal_freq`
- `voltage_drop - threshold`
- `threshold - power_margin`

类别变量优先级建议为：

1. `fidelity_level`（proxy / calibrated proxy / comsol）
2. `violation_family`
3. `operator_family`

不建议第一版就把所有 `BOM archetype / mission / stack` 全部并进去，否则类别爆炸。

### 6.3.3 策略搜索器

参考 P5，建议把 `vop_maas` 的动作循环正式写成：

- `Thought`：LLM 读 `VOPG++` 和历史 round 摘要；
- `Action`：提议 `operator`、`zone prior`、`fidelity shift`、`runtime knob`；
- `Observation`：读取 candidate scorer、cheap rollout、历史 operator credit；
- `Final Answer`：输出结构化 `PolicyPack`。

### 6.3.4 便宜评分器

不要直接用最终物理目标做每步评分，建议先定义一个**中间工程评分器**，类似 P5 的 `simple cluster score`，但面向卫星布局：

- `predicted_delta_total_cv`
- `predicted_first_feasible_gain`
- `predicted_comsol_saving`
- `operator_realness_score`
- `constraint_coverage_score`

这个评分器不是最终发布指标，但非常适合做 screening。

---

## 6.4 最不推荐的三条路线

### 6.4.1 不推荐：`LLM -> 最终组件坐标`

原因：

- 与 P5 的有效路线相反；
- 难以满足硬约束合同与审计；
- 与当前 `mass` 主链冲突；
- 论文叙事也更弱。

### 6.4.2 不推荐：用 BO 完全替换 `pymoo`

原因：

- P4 证明 surrogate 更适合做外层加速与筛选；
- 当前问题是高维、混合变量、硬约束、多物理，多数场景仍更适合 MOEA 内核；
- BO 可作为 ranking / local refinement，而不是整个主搜索器。

### 6.4.3 不推荐：直接上生成式扩散布局作为执行器

原因：

- 补充参考的 facility-layout 论文更适合启发 `graph + knowledge + prompt` 结构；
- 生成式方法目前更适合做 layout proposal / prior，不适合直接承担 release-grade feasible solver。

---

## 7. 最适合当前项目的落地执行顺序

### 阶段 S1：把 `fidelity_plan` 做实

目标：

- 让 `PolicyPack` 真正能影响 `proxy -> COMSOL` 预算；
- 把 `first_feasible_eval / COMSOL_calls_to_first_feasible` 作为 round-level 审计核心。

### 阶段 S2：引入多输出残差排序器

目标：

- 不替代 `mass`；
- 仅负责 candidate ranking、operator ranking、COMSOL value-of-information 估计。

### 阶段 S3：把热/结构 operator 参数化

目标：

- 从启发式动作升级到参数化动作；
- 至少覆盖：
  - `heatstrap`
  - `thermal_contact`
  - `bracket`
  - `stiffener`

### 阶段 S4：强化 `VOPG++`

目标：

- 显式加入 spatial zones、thermal paths、fidelity provenance；
- 为后续 `policy memory / template evolution` 打基础。

### 阶段 S5：最后才考虑更强的生成式模块

目标：

- 仅作为 prior/proposal，不替代 `mass` 求解主链。

---

## 8. 最终结论

如果把本轮五篇论文压成一句话，那么最适合 `MsGalaxy` 的路线不是：

> `LLM directly generates layout`

而是：

> `LLM generates audited operator-policy and fidelity plan, while pymoo-based constrained search remains the executable optimization core`

对应到项目上，最值得坚持并加强的是：

- 保留 `mass/pymoo` 作为可信数值求解内核；
- 把 `vop_maas` 收敛为策略/算子/保真度控制层；
- 加入多输出多保真 residual ranking；
- 用 `proxy -> calibrated proxy -> selective COMSOL` 做预算治理；
- 把热/结构/功率动作真正并入可执行搜索空间。

这条路线同时满足三件事：

- **工程上可执行**：不破坏现有主链；
- **科研上可发表**：有清晰的方法创新与归因口径；
- **系统上可审计**：符合你们当前对 strict-real、fallback、summary/tables 的治理要求。

---

## 9. 下一步建议

基于本轮调研，最建议的后续动作不是继续泛搜论文，而是做两项实证：

1. 做一个 `vop_maas + multi-output residual ranker` 的薄切片；
2. 做一个 `operator family x violation family` 的 credit/ablation 基准。

如果后续继续推进，我建议下一份报告直接进入“算法设计稿 + 实验设计稿”，而不是再停留在文献综述层。
