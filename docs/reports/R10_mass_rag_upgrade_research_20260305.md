# Mass 模式 RAG 升级深度调研与方案（2026-03-05）

## 0. 范围与约束
- 仅覆盖 `optimization.mode=mass` 主链，不讨论 `agent_loop`。
- 目标是服务 `A/B/C/D`：`ModelingIntent` 生成、约束编译、求解反射、重试策略。
- 不绕过现有 pymoo 优化契约，不把 RAG 变成“直接出坐标”。

## 1. 现状诊断（基于仓库实证）

### 1.1 调用链事实
- `mass` 调用 `runtime.build_global_context(...)`，最终走到 `workflow/modes/agent_loop/runtime_support.py::_build_global_context`，并调用 `self.rag_system.retrieve(...)`。
- `ModelingIntent` 生成时，`MetaReasoner.generate_modeling_intent(...)` 将 `context.to_markdown_prompt()`拼入提示词，间接消费 `retrieved_knowledge`。
- 这意味着：`mass` 虽使用 RAG，但当前 RAG 不是 `mass` 专用设计，缺少与约束编译/求解诊断的一致化结构。

### 1.2 数据质量实证（本地统计）
- `data/knowledge_base/knowledge_base.json` 当前总条目 `334`，其中 `case=328`，`standard/heuristic/formula` 仅各 `2` 条。
- 重复与污染严重：
  - `exact_duplicate_titles=122`
  - `prefix20_duplicate_items=185`
  - `case_items_with_anomaly_tokens=217`
  - `near_duplicate_signature_groups(>=3)=25`
  - `near_duplicate_signature_items=141`
- 结论：知识库已被大量相似迭代日志淹没，工程规则密度过低，检索稳定性与可解释性都会下降。

### 1.3 检索机制问题
- `_keyword_search` 中使用 `if item.category in violation_types`，但 `category` 是 `standard/case/formula/heuristic`，`violation_type` 是 `geometry/thermal/structural/power`，类别空间不对齐。
- `add_knowledge(...)` 每次追加后都全量 `_compute_embeddings()`，扩展性差。
- 语义相似度计算为直接点积，缺少归一化与可学习重排，检索排序对长度/向量尺度敏感。
- 当前仅有“异常 case 过滤”，没有“可行性来源门控（proxy/online COMSOL）+ 约束域门控 + 去重采样”。

### 1.4 运行层面的旁证
- `run/mass/run_L2.py`、`run/mass/run_L3.py`、`run/mass/run_L4.py` 默认 `disable_semantic=True`，仅可显式 `--enable-semantic` 打开。
- `tests/test_maas_pipeline.py` 多处强制 `base_config["knowledge"]["enable_semantic"] = False`，表明语义检索在回归路径中不是“默认可信组件”。

## 2. 近三年论文与网页调研结论（2023-2026）

### 2.1 对本项目最相关的研究脉络
- 自适应检索策略：
  - Self-RAG（2023）：按需检索 + 自反思 token，减少盲检索。
  - Adaptive-RAG（NAACL 2024）：按问题复杂度动态选 no-retrieval / single-step / iterative。
- 检索鲁棒与纠错：
  - CRAG（2024）：先评估检索质量，再触发纠错检索动作（含 web 扩展思路）。
- 结构化知识与长程记忆：
  - RAPTOR（2024）：分层摘要树，支持跨块全局语义。
  - GraphRAG（2024）：图结构与社区摘要，提升全局问题回答。
  - HippoRAG（2024）：长期记忆图式，强调多跳与低成本检索。
- 语义对齐与端到端优化：
  - R2AG（Findings EMNLP 2024）：把检索器内部信息显式注入生成，缓解 retriever-LLM 语义鸿沟。
  - OpenRAG（2025）：针对 RAG 任务端到端调检索器，提升 in-context relevance。
  - RAFT（2024）：面向领域 RAG 的后训练配方。
- 评测框架：
  - RAGAS（2023/2025v2）：无参考评测框架。
  - ARES（NAACL 2024）：轻量 judge + 少量人工校准（PPI）自动评测。
  - RAGChecker（NeurIPS 2024 D&B）：检索/生成双模块细粒度诊断。
  - RAGBench（2024/2025v2）：大规模可解释标签基准。
  - Judge-as-a-Judge（Findings ACL 2025）：提升 LLM-as-judge 一致性。
  - BenchmarkQED（Microsoft，2025）：AutoQ/AutoE/AutoD 的标准化评测流水。

### 2.2 对 MsGalaxy 的直接启发
- `mass` 的核心任务不是“开放问答”，而是“约束驱动优化控制”；检索单元必须从文本块升级为“约束-动作-结果证据单元”。
- 仅做向量相似检索不足以支撑工程可行性；必须引入结构化索引（constraint graph + provenance）。
- 检索策略应按 `A/B/C/D` 阶段与问题复杂度切换，不应固定 top-k。
- 评测必须拆成三层：`retrieval质量 -> intent编译有效性 -> 求解可行性收益`，否则无法定位收益来源。

## 3. 升级方案：CGRAG-Mass（Constraint-Graph RAG for MaaS）

### 3.1 核心思想
- 将知识项从“文本经验”升级为“可执行证据元组（Evidence Tuple）”：
  - `query_signature`：约束违规向量、BOM特征、level、profile、search_space
  - `action_signature`：operator family / 参数摘要 / 变量边界策略
  - `outcome_signature`：strict/relaxed feasibility、CV变化、first_feasible_eval
  - `physics_provenance`：proxy / online_comsol / network_dc_solver 来源标签
  - `artifact_ref`：run_id、attempt_id、trace路径
- 在检索层采用“双通道 + 重排”：
  - 通道A（symbolic）：按约束组、违规主导项、组件集合、来源门控做硬过滤。
  - 通道B（semantic）：embedding 召回同类问题证据。
  - 通道C（graph walk，可选）：在约束-组件-动作图中做 1~2 跳扩展。
  - 最终由 `feasibility-aware reranker` 重排并输出 top-k 证据。

### 3.2 面向 mass 四阶段的注入点
- A Understanding：
  - 为 `generate_modeling_intent` 提供“同类约束图谱 + 历史可行模板 + 失败反例”。
  - 输出中附 `intent_support_evidence_ids`，便于归因。
- B Formulation：
  - 对 hard constraints 做检索式校验：是否有历史可执行映射、是否触发过 unknown metric。
- C Coding/Execution：
  - 提供变量边界先验与 operator seed 先验（不替代 pymoo 搜索）。
- D Reflection：
  - 检索“当前主导违规 -> 最可能有效动作族”的证据，优先推荐已在 strict 口径通过的策略。

### 3.3 学术创新点（可写论文）
- 创新1：`Constraint-Pareto Evidence Graph (CPEG)`
  - 把 RAG 单元从文本 chunk 扩展为“约束-动作-结果”三元证据图，直接对齐多目标优化任务。
- 创新2：`Provenance-Aware Retrieval Gate`
  - 检索时引入物理来源门控（proxy vs online）与 strict/relaxed 口径区分，避免“伪可行经验”污染。
- 创新3：`Feasibility-Calibrated Reranking`
  - 用历史运行产物自动生成弱监督标签（是否降低 CV、是否更快首个可行解）训练重排器。
- 创新4：`Phase-Adaptive Retrieval Policy`
  - A/B/C/D 分阶段检索策略（复杂度路由 + 预算路由），降低无效检索与提示长度浪费。

## 4. 落地架构（建议代码分层）

### 4.1 新增模块
- `optimization/knowledge/mass/evidence_schema.py`
- `optimization/knowledge/mass/evidence_store.py`
- `optimization/knowledge/mass/retrievers.py`
- `optimization/knowledge/mass/reranker.py`
- `optimization/knowledge/mass/policy_router.py`
- `optimization/knowledge/mass/ingest_from_runs.py`

### 4.2 兼容现有入口
- 保留 `optimization/knowledge/rag_system.py` 作为兼容层。
- 在 `mass` 模式切换到 `MassRAGFacade`，`agent_loop` 维持旧逻辑（按你当前需求可不继续增强）。

### 4.3 数据治理改造
- 强制字段：
  - `mode=mass`
  - `strict_proxy_feasible`
  - `diagnosis_status`
  - `metric_sources`
  - `dominant_violation`
- 写入策略：
  - 不再“每轮必写 case”；改为“事件驱动写入”：
    - 首次可行
    - 主导违规切换
    - CV 显著下降
    - strict replay 通过/失败

## 5. 实验设计（科研可发表）

### 5.1 对照组
- Baseline-0：当前 `rag_system.py`（flat + keyword/semantic）。
- Baseline-1：仅向量检索（去除 symbolic gate）。
- Baseline-2：向量 + symbolic gate（无重排）。
- Ours：CGRAG-Mass（向量+符号+图扩展+可行性重排+分阶段策略）。

### 5.2 评测任务
- 使用现有 `run/mass/benchmark_matrix.py`，levels 至少 `L2/L3/L4`。
- profile 至少覆盖：`baseline`、`operator_program`、`multi_fidelity`。
- seeds：`>=3`（建议 5）。

### 5.3 指标
- 任务级：
  - `strict_proxy_feasible_ratio`
  - `feasible_rate`
  - `first_feasible_eval`
  - `comsol_calls_to_first_feasible`
  - `best_cv_min`
- 检索级：
  - evidence@k 覆盖率（命中历史有效动作族）
  - context usefulness（被最终采用的证据占比）
- 编译级：
  - `llm_effective_passed`
  - `parsed_variables`
  - dropped/unsupported constraints 数量

### 5.4 统计与显著性
- 遵循仓库 RULES：不做单 seed 结论。
- 采用 paired bootstrap 或 Wilcoxon 检验，报告均值+方差+显著性。

## 6. 分阶段实施计划（建议 6~8 周）

### Phase 0（1周）数据治理
- 建立 evidence schema、清洗重复/异常条目、构建离线导入脚本。

### Phase 1（2周）检索底座
- 实现 symbolic gate + vector recall + provenance gate。
- 替换现有 category/violation_type 不对齐逻辑。

### Phase 2（2周）mass 集成
- 接入 A/B/D 三阶段提示构造。
- 增加 evidence trace 到 `summary.json` 与 `events`.

### Phase 3（2~3周）重排与策略学习
- 训练 feasibility-aware reranker（弱监督）。
- 增加 phase-adaptive policy router。

## 7. 风险与控制
- 风险：图索引/重排增加延迟。
  - 控制：先离线构图 + 在线轻量重排；top-k 限制。
- 风险：历史数据标签噪声。
  - 控制：strict/relaxed 分桶 + source gate + 异常检测。
- 风险：与现有 LLM 提示冲突。
  - 控制：保留 fallback，逐 profile 灰度启用。

## 8. 结论（针对“最适合 mass”）
- 最优策略不是“继续堆 embedding”，而是把 RAG 与 `mass` 的约束-求解闭环深度对齐。
- `CGRAG-Mass` 同时满足：
  - 工程可落地：兼容现有 A/B/C/D 与 pymoo；
  - 科研创新性：结构化证据图 + 可行性校准重排 + 分阶段策略路由；
  - 可验证性：可直接接入现有 benchmark/strict gate 体系做统计评估。

## 9. 关键参考文献与资料链接
- Self-RAG（NeurIPS 2023 Workshop）: https://openreview.net/forum?id=jbNjgmE0OP
- Adaptive-RAG（NAACL 2024）: https://aclanthology.org/2024.naacl-long.389/
- RAPTOR（arXiv 2024）: https://arxiv.org/abs/2401.18059
- GraphRAG（arXiv 2024）: https://arxiv.org/abs/2404.16130
- Corrective RAG / CRAG（arXiv 2024）: https://arxiv.org/abs/2401.15884
- HippoRAG（arXiv 2024）: https://arxiv.org/abs/2405.14831
- LongRAG（arXiv 2024）: https://arxiv.org/abs/2406.15319
- RAFT（arXiv 2024）: https://arxiv.org/abs/2403.10131
- R2AG（Findings EMNLP 2024）: https://aclanthology.org/2024.findings-emnlp.678/
- OpenRAG（arXiv 2025）: https://arxiv.org/abs/2503.08398
- M3-Embedding（arXiv 2024）: https://arxiv.org/abs/2402.03216
- NV-Embed（arXiv 2024）: https://arxiv.org/abs/2405.17428
- RAGAS（arXiv 2023/2025v2）: https://arxiv.org/abs/2309.15217
- ARES（NAACL 2024）: https://aclanthology.org/2024.naacl-long.20/
- RAGChecker（NeurIPS 2024 Datasets & Benchmarks）: https://openreview.net/forum?id=J9oefdGUuM
- RAGBench（arXiv 2024/2025v2）: https://arxiv.org/abs/2407.11005
- Evaluating Retrieval Quality in RAG / eRAG（arXiv 2024）: https://arxiv.org/abs/2404.13781
- Judge as A Judge（Findings ACL 2025）: https://aclanthology.org/2025.findings-acl.301/
- Utility-Focused LLM Annotation（EMNLP 2025）: https://aclanthology.org/anthology-files/pdf/emnlp/2025.emnlp-main.88.pdf
- GraphRAG 文档: https://microsoft.github.io/graphrag/
- GraphRAG 仓库: https://github.com/microsoft/graphrag
- BenchmarkQED 文档: https://microsoft.github.io/benchmark-qed/
- BenchmarkQED 介绍（Microsoft Research）: https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/
