# R28 VOP-MaaS 研究与开发总方案（2026-03-07）

## 1. 目的与范围

本文档将当前 `VOP-MaaS` 研究计划正式化为仓库内可维护的 report 文档，用于统一以下内容：

- 论文叙事与研究命题；
- 近三年相关文献对方法边界的启示；
- `VOPG` / `VOPPolicyPack` 的方法定义；
- `M0-M6` 分阶段开发路线；
- 当前仓库已实现切片与未实现边界；
- 实验设计、归因口径、风险与下一步行动。

本文档是研究/开发总方案，不等价于“全部能力已实现”。真实实现边界仍以 `HANDOFF.md` 为准，架构决策以 `docs/adr/0007-vop-maas-verified-operator-policy-experimental-mode.md` 为准。

### 2026-03-07 late sync

- `M0` 已冻结；
- `M1-M3` 已实现；
- `M4` 已实现第一可运行 thin-slice，但尚未扩展到完整 screening 阶段；
- `M5-min`（单轮、bounded、可审计 reflective replanning）已实现，但完整 `M5/M6` 仍未实现；
- release-grade audit 字段 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 已统一写入 `summary.json / run_manifest / report / visualization / tables/release_audit.csv`；
- 下游读取当前优先口径为 `tables/vop_rounds.csv` + `tables/release_audit.csv`。
- 历史产物补重建与 rollup helper 已补齐：`run/mass/tool_rebuild_run_artifacts.py`、`run/mass/audit_release_summary.py`。
- 非 release-grade run 的轻量 gap breakdown（`gap_category / primary_failure_signature / minimal_remediation / evidence_hint`）已补到 `audit_release_summary.py`。

---

## 2. 当前基线与问题定义

### 2.1 仓库当前最可信的基线

当前仓库中最强、最可信的主线仍是 `mass`：

- 外层由 LLM 负责需求理解、建模意图组织与约束/目标编排；
- 中层由 `ModelingIntent -> compile -> pymoo problem` 建立可执行优化问题；
- 内层由 `pymoo` (`NSGA-II/III`, `MOEA/D`) 负责数值搜索；
- 物理侧由 proxy + online COMSOL + 若干回退路径提供多物理反馈；
- 结果必须接受硬约束、source/operator-family/operator-realization gate 等工程治理。

因此，下一阶段最合理的研究问题不是“让 LLM 直接输出布局”，而是：

> 如何在不破坏 `mass` 可信内核的前提下，让 LLM 成为一个**受验证的策略层 / 算子层控制器**，从而更快、更稳、更早到达真实可行解？

### 2.2 不采用 “LLM 直接布局生成” 的原因

不采用 `LLM-to-Layout` 路线，主要是出于三点考虑：

1. **与当前架构冲突**  
   仓库的真实强项是 `mass` 数值优化内核，而不是 end-to-end 几何生成。
2. **工程可信性不足**  
   直接生成坐标很难满足硬约束治理、归因可追踪与 strict-real 审计。
3. **论文叙事不够稳**  
   当前最有说服力的故事不是“LLM 替代优化器”，而是“LLM 提供 verified operator-policy，使 baseline 更有效”。

---

## 3. 近三年文献调研结论

### 3.1 总体结论

近三年高质量相关工作给出的共同信号非常一致：

- LLM 最适合作为**程序/策略/启发式的生成器**，而不是直接替代优化器；
- LLM 在昂贵黑箱问题中最有价值的位置，往往是**早期样本稀疏期**、warm-start、候选策略提议与局部反射修正；
- 在工程/EDA/设计问题中，真正可落地的方法依赖 **RAG/结构化上下文/工具调用/受限接口/validator/fallback**，而不是自由生成；
- 对 3D packing / heuristic search 这类空间问题，free-form 代码生成或直接 heuristic synthesis 很脆弱；
- 因此，更适合本项目的路线是：**LLM-to-Operator-Policy，而不是 LLM-to-Layout / LLM-to-Solver-Code。**

### 3.2 关键文献与对本项目的启示

1. **FunSearch, Nature 2024**  
   链接：<https://www.nature.com/articles/s41586-023-06924-6>  
   启示：LLM 更适合生成可执行启发式或程序片段，再由外部 evaluator 负责筛选，而不是直接接管求解器。

2. **Eureka, ICLR 2024**  
   链接：<https://arxiv.org/abs/2310.12931>  
   启示：LLM 设计 reward / policy logic 的收益往往高于直接输出低层动作，这与“LLM 生成 operator-policy、`mass` 负责数值求解”高度一致。

3. **LLAMBO, ICLR 2024 Poster**  
   链接：<https://arxiv.org/abs/2402.03921>  
   启示：LLM 在昂贵黑箱优化中最有价值的是早期 warm-start、proposal 与 surrogate hint，而不是替代整个优化流程。

4. **Autonomous Multi-Objective Optimization Using LLM, 2024**  
   链接：<https://arxiv.org/abs/2406.08987>  
   启示：LLM 参与多目标进化算子设计本身就是可发表方向，说明“LLM 驱动 operator 层”具有学术正当性。

5. **LLM for Standard Cell Layout Design Optimization, 2024**  
   链接：<https://arxiv.org/abs/2406.06549>  
   启示：在真实布局问题里，LLM 更适合作为约束/聚类先验生成器与局部 debug 助手，而不是 placement engine。

6. **Atelier, 2025**  
   链接：<https://d197for5662m48.cloudfront.net/documents/publicationstatus/286994/preprint_pdf/f8da5d2df8e47e3df3c5d09d951a3697.pdf>  
   启示：知识密集工程任务的关键是“紧凑领域知识 + 明确角色 + 工具联动”，不是单个大 prompt。

7. **Optimization through In-Context Learning for Nuclear Engineering Design, 2025**  
   链接：<https://arxiv.org/abs/2503.19620>  
   启示：直接让 LLM 做优化器可以作为概念验证，但不适合本项目当前阶段，因为会冲掉 `pymoo` 为核心的架构边界。

8. **LLM-to-Phy3D, 2025**  
   链接：<https://arxiv.org/abs/2506.11148>  
   启示：physics-in-the-loop refinement 有价值，但重点在“物理反馈闭环”，而不是直接生成几何结果。

9. **Agentic LLMs for Conceptual Systems Engineering and Design, 2025**  
   链接：<https://arxiv.org/abs/2507.08619>  
   启示：多 agent 角色增多不自动等于可执行性提升，对本项目是明确警示：不要把 `vop_maas` 做成角色爆炸的 MAS。

10. **MeLA, 2025**  
    链接：<https://arxiv.org/abs/2507.20541>  
    启示：元认知式 prompt/policy evolution 有启发，但更适合作为第二阶段增强，而不是第一版核心复杂度来源。

11. **Re-evaluating LLM-based Heuristic Search on 3D Packing, 2025**  
    链接：<https://arxiv.org/abs/2509.02297>  
    启示：直接让 LLM 生成 3D packing solver/heuristic 很脆弱，恰好支持本项目坚持 `DSL + validator + screening + fallback` 路线。

12. **AEI 2025 综述**  
    链接：<https://doi.org/10.1016/j.aei.2024.103066>  
    启示：机械/产品设计/制造领域对 LLM 落地的共识是：必须依赖 RAG、agentic orchestration、tool use、domain constraints 与可验证接口。

### 3.3 对本项目的直接结论

基于上述文献和仓库现实，本项目最合适的方法命题是：

**VOP-MaaS = Verified Operator-Policy MaaS**

其核心边界是：

- LLM 不生成最终布局坐标；
- LLM 不替代 `pymoo` 优化器；
- LLM 只生成**可验证、可执行、可回退**的 operator-policy；
- 真正的布局解仍由 `mass` 的 `pymoo + multiphysics` 内核求得。

---

## 4. 论文主命题与研究问题

### 4.1 论文主命题

建议论文主命题写为：

> 从可信的 physics + pymoo baseline 出发，引入 LLM 驱动的 verified operator-policy layer，使多物理布局搜索更快、更稳、更早到达真实可行解。

### 4.2 核心研究问题（RQ）

- **RQ1**：LLM 生成的 operator-policy 是否能提高 `first_feasible` 效率，而不仅仅是改善最终目标值？
- **RQ2**：LLM 的收益主要来自哪里：operator prior、search-space prior，还是 fidelity planning？
- **RQ3**：在 real/proxy source gate 下，LLM 增强是否还能保持 attribution 清晰与工程可信性？

---

## 5. 方法总览：VOP-MaaS

### 5.1 方法总结构

`VOP-MaaS` 采用双层结构：

- **外层优化对象：策略 `p`**  
  由 LLM 生成、验证、筛选 operator-policy。
- **内层优化对象：设计变量 `x`**  
  由 `mass` 编译为 `pymoo` 问题并执行数值搜索。

可以概括为：

> LLM 选策略，MaaS 解布局。

### 5.2 核心对象一：`VOPG`

`VOPG`（Violation-Operator Provenance Graph）是 LLM 的结构化输入接口，用于把多物理证据压缩为一个可推理的图表示。

#### 节点类型

- `constraint`
- `metric`
- `component`
- `operator_family`
- `evidence_source`

#### 边属性

- `severity`
- `sensitivity`
- `historical_credit`
- `source_realness`

#### 输入来源

当前建议固定来自：

- constraint violation breakdown
- dominant violation family
- component-level thermal / cg / structural / power diagnostics
- operator history / branch history / seed population source
- proxy vs real source tags

### 5.3 核心对象二：`VOPPolicyPack`

`VOPPolicyPack` 是 LLM 的结构化输出接口，而不是坐标、代码或最终解。

最小字段集合：

- `constraint_focus`
- `operator_candidates`
- `search_space_prior`
- `runtime_knob_priors`
- `fidelity_plan`
- `confidence`
- `rationale`
- `expected_effects`
- `policy_source`

### 5.4 Counterfactual Screening

`VOP-MaaS` 的关键创新不是“LLM 生成一个策略”，而是：

> LLM 生成多个候选策略，再通过 cheap proxy rollout / candidate scoring 做反事实筛选，只把少量优选策略送入正式 `mass` 求解。

这一步使方法在昂贵仿真预算下仍然工程可用。

---

## 6. 核心创新点

### 创新 1：策略作用于搜索，不直接作用于最终设计解

现有许多工作让 LLM 直接生成设计、代码或 reward；本项目让 LLM 生成的是 **operator-policy over search**，更贴合当前架构，也更安全。

### 创新 2：`VOPG` 作为多物理证据压缩接口

LLM 不直接阅读混乱日志，而是通过 `VOPG` 读取结构化证据图，从而保持输入稳定、可审计、可演化。

### 创新 3：Counterfactual Policy Screening

先筛选、再执行，把昂贵预算留给更有希望的策略，而不是把所有 LLM 输出都直接送入正式求解。

### 创新 4：Source-aware First-Feasible Objective

把以下指标放到与最终目标值同等重要的位置：

- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`
- real-source coverage

这比只比较最终 Pareto/frontier 更符合本项目的工程特征。

### 创新 5：Metacognitive Policy Memory（二阶段）

后续如果扩展到 `M6`，建议演化的是 **policy template / policy memory**，而不是 solver code；这样能保持方法边界稳定。

---

## 7. 当前仓库实现与计划映射

### 7.1 已实现切片（截至 2026-03-07）

当前仓库已完成 `M1-M4` 的第一可运行切片：

#### M1：`vop_maas` 正式 experimental mode

- 已新增独立 stack / base config / run 入口；
- 已在统一 scenario registry 与 stack contract 中注册；
- 真实执行仍委托 `mass` 内核；
- 已支持 fallback-to-`mass`。

#### M2：`VOPG` 与 `VOPPolicyPack`

- 已定义 `VOPGraph` / `VOPPolicyPack` / `VOPOperatorCandidate` 等契约；
- 已实现 schema validator；
- 已实现 repair / rejection；
- 未知字段、越权 action、无效 operator candidate 不会 silent pass。

#### M3：单次 pre-search policy generation

- 已支持 mock policy；
- 已为真实 LLM 留出 `generate_policy_program(...)` 入口；
- 已把 `policy_priors` 注入 `mass` 的 intent patch / operator seed / search-space prior / runtime knobs。

#### M4：screening

- 已实现 lightweight screening；
- 已记录 `vop_policy_screening` attribution；
- 已把 screened policy 与 fallback 行为写入 metadata。

### 7.2 当前实现文件映射

#### `vop_maas` mode 层

- `workflow/modes/vop_maas/contracts.py`
- `workflow/modes/vop_maas/policy_context.py`
- `workflow/modes/vop_maas/policy_compiler.py`
- `workflow/modes/vop_maas/policy_program_service.py`
- `workflow/modes/vop_maas/__init__.py`

#### `mass` 内核注入点

- `workflow/modes/mass/pipeline_service.py`

#### LLM 接口挂点

- `optimization/meta_reasoner.py`

#### stack / run / config

- `run/stack_contract.py`
- `run/run_scenario.py`
- `config/scenarios/registry.yaml`
- `config/system/vop_maas/base.yaml`
- `run/vop_maas/run_L1.py`
- `run/vop_maas/run_L2.py`
- `run/vop_maas/run_L3.py`
- `run/vop_maas/run_L4.py`

### 7.3 当前未实现内容

以下仍属于规划态，不得对外宣称为已实现：

- `M0` 的实验矩阵扩展、benchmark 资产与论文级对照仍未完成；
- 完整 `M5` reflective replanning（multi-round / memory-aware）；
- `M6` policy memory / template evolution；
- 神经可行性预测器；
- 神经 operator policy；
- 多保真神经调度器；
- 完整论文级对照实验矩阵。

---

## 8. 分阶段开发路线

### M0：研究冻结与实验口径统一

目标：

- 冻结三条主对照线：`mass_baseline`、`mass_llm_intent_only`、`vop_maas`
- 冻结 L1-L4 BOM、随机种子、physics gate、日志 schema、summary 字段
- 当前只冻结 `NSGA-III` 这一条已经跑通的执行算法；`NSGA-II`、`MOEA/D` 与其他方法扩展全部后置
- 固定论文主指标、主图、ablation 清单

交付物：

- 研究假设文档
- 实验矩阵
- 指标口径文档
- `M0` 可执行研究包定义文档：`docs/reports/R29_vop_maas_m0_execution_package_20260307.md`

完成标准：

- `mass` 基线重复结果稳定；
- 口径不再频繁变动。

### M1：把 `vop_maas` 升为正式 experimental mode

目标：

- 注册 `stack=vop_maas`
- 独立 `config/system/vop_maas/base.yaml`
- 统一入口、stack contract、scenario registry 全接入
- 委托 `mass` 执行，但新增 `policy_priors` 注入点

当前状态：**已完成第一切片**

### M2：定义 `VOPG` 与 `VOPPolicyPack`

目标：

- 固定结构化输入与输出；
- schema validator、repair、rejection 完整落地；
- unknown / unsupported / unimplemented 不能 silent pass。

当前状态：**已完成第一切片**

### M3：单次 pre-search policy generation

目标：

- 第一版只做单次 policy 生成；
- 策略只注入 intent patch / operator seed / search-space prior / runtime knobs；
- 严禁 LLM 直接输出最终 coordinates。

当前状态：**已完成第一切片**

### M4：Counterfactual Screening

目标：

- 对候选策略做 proxy-based screening；
- 用 cheap scoring 先筛、再执行。

建议 screening 目标：

- 预计 `Δbest_cv`
- 预计 dominant violation 缓解幅度
- diversity 改善
- 预算代价惩罚

当前状态：**已完成第一切片**

### M5：反射式重规划与多保真调度

目标：

- 在 retry / reflection 阶段再次调用 LLM；
- 二次输入仅允许使用 `VOPG`、上一轮 `VOPPolicyPack`、上一轮 policy effect summary；
- 新增 `fidelity_plan` 控制 proxy / COMSOL / audit 的预算分配。

当前状态：**已实现 `M5-min` 单轮薄切片；完整 `M5` 未实现**

已实现边界：

- 仅允许一次 reflective replanning；
- 仅在首轮 policy 已应用且结果明确失败/停滞时触发；
- feedback-aware fidelity escalation 只在真实 `comsol` backend 且命中特定 failure signature 时 bounded 生效；
- 不包含 policy memory、template evolution 或 multi-round reflective loop。

### M6：Policy Memory / Template Evolution

目标：

- 构建 policy memory；
- 检索高成功率 template，再生成新策略；
- 如需引入元认知演化，只演化 policy template，不演化 solver code。

当前状态：**未实现**

---

## 9. 最低可投稿切片

如果目标是尽快形成一篇有说服力的方法论文，做到 `M0-M4` 即可形成完整故事：

- `mass_baseline` 是可信 physics baseline；
- `mass_llm_intent_only` 说明仅靠 intent 不够；
- `vop_maas` 通过 verified operator-policy 真正改变求解轨迹与效率。

因此，最小投稿切片建议为：

- 固化 `M0`
- 做实 `M1-M4`
- 先在 `L1-L2` 上完成原型与对照，不急于一开始就攻 `L3-L4`

---

## 10. 实验设计与评价框架

### 10.1 实验组

- `mass_baseline`
- `mass_llm_intent_only`
- `vop_static`
- `vop_screened`
- `vop_reflective`（若后续实现）
- `vop_reflective + memory`（若后续实现）

### 10.2 实验任务

- 第一轮：`L1-L2`，5 个 seeds
- 第二轮：`L3-L4`，3-5 个 seeds
- 所有组共享同一 BOM、约束、physics gate 与预算上限

### 10.3 主指标

- feasibility rate
- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`
- best CV
- best feasible objective vector
- real-source coverage
- operator-family coverage
- policy validity rate
- policy fallback rate

### 10.4 核心 ablation

- 去掉 `VOPG`
- 去掉 screening
- 去掉 reflective replanning
- 去掉 fidelity plan
- 只保留 operator prior
- 只保留 runtime prior

### 10.5 论文级完成标准

- 至少一个主命题在 paired setting 下取得显著优势；
- 优先看 `first_feasible_eval` 与 `COMSOL_calls_to_first_feasible`；
- `mass_baseline` 行为零回归；
- invalid policy 100% 被拦截、修复或回退；
- attribution 必须可追踪到“哪个策略改动了哪条求解路径”。

---

## 11. 当前风险与治理要求

### 11.1 最大风险：归因不清

如果后续实验无法证明策略确实改变了搜索轨迹，论文会显得只是“加了一层提示词”。

### 11.2 proxy overfitting

screening 可能偏爱只在 proxy 上表现好的策略，因此必须保留 source-aware audit 与 real-source coverage。

### 11.3 角色膨胀

文献已表明 agent 数量增加往往带来复杂度与脆弱性，不一定带来更高可执行性，因此不建议把 `vop_maas` 扩展成多角色 MAS。

### 11.4 heuristic/code generation 诱惑

3D packing 负面结果已经说明 free-form heuristic generation 很脆，因此第一版不应走这条路。

### 11.5 方法治理要求

- 不绕过 `mass` 数值优化内核；
- 不让 LLM 直接输出最终布局坐标；
- 不允许 unknown fields / unsupported operators / unimplemented metric keys silent pass；
- 所有 experimental 结论都应显式标注 mode 状态与 fallback 情况。

---

## 12. 建议的论文贡献写法

建议将论文主贡献表述为：

1. 提出一种 `LLM-to-Operator-Policy` 的中层接口，而非 `LLM-to-Layout`。
2. 提出 `VOPG`，将多物理违规与算子历史压缩为结构化可推理上下文。
3. 提出 source-aware counterfactual screening，使 LLM policy 在昂贵仿真预算下仍然工程可用。
4. 提出以 `first_feasible_eval` 与 `COMSOL_calls_to_first_feasible` 为核心的评价框架。

相对已有文献的创新边界：

- 相对 FunSearch / Eureka：本项目搜索的是**受物理契约与 DSL 约束的策略程序**；
- 相对 LLAMBO：本项目不只做 warm-start，而是统一控制 operator prior、search prior 与 fidelity planning；
- 相对 Atelier / MDO Agent：本项目不重做全栈 CAD/CAE agent，而是集中创新于高杠杆策略层；
- 相对 Autonomous MOO：本项目不让 LLM 设计泛化 EA 代码，而是在现有 operator DSL v3 上做 physics-grounded selection/composition；
- 相对 3D packing 的负面结果：本项目主动用 `DSL + validator + screening + fallback` 避免 free-form heuristic fragility。

---

## 13. 当前阶段优先动作

1. 对历史 strict-real 产物做最小必要的 artifact rebuild，统一 `summary / run_manifest / report / visualization / tables/release_audit.csv` 口径；
2. 继续补 targeted `L3/L4` 高价值回归与下游消费脚本，优先围绕 `tables/vop_rounds.csv`、`tables/release_audit.csv` 与非 release-grade gap breakdown；
3. benchmark、M0 扩展矩阵与论文级对照继续后置，待当前审计口径稳定后再推进。

---

## 14. 最终推荐口径

### 方法名

`VOP-MaaS: Verified Operator-Policy MaaS for Multiphysics Layout Optimization`

### 当前实现口径

截至 2026-03-07，仓库中已经存在：

- `vop_maas` experimental mode；
- `VOPG` / `VOPPolicyPack` 第一版契约；
- validator / screening / fallback；
- `policy_priors` 注入 `mass` 的第一版运行链；
- round-level attribution metadata（含 `tables/vop_rounds.csv`）；
- release-grade audit 字段写出（含 `final_audit_status / first_feasible_eval / comsol_calls_to_first_feasible` 与 `tables/release_audit.csv`）；
- `M5-min` 单轮 reflective replanning + bounded feedback-aware fidelity escalation。

但当前仍未形成：

- 论文级完整 benchmark；
- 完整 `M5` reflective replanning；
- policy memory；
- 神经 guidance 模块；
- 完整 `M5/M6` 级别的策略记忆与演化闭环。

因此，对外表述应保持为：

> `vop_maas` 已进入可运行的 experimental/operator-policy mode，当前真实边界是 `M1-M4` + `M5-min` 单轮 reflective replanning；完整 `M5/M6` 与 stable 阶段仍未实现。
