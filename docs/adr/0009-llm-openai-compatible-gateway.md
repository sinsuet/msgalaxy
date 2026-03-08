# 0009-llm-openai-compatible-gateway

- status: accepted
- date: 2026-03-08
- deciders: msgalaxy-core
- cross-reference:
  - `R34_llm_unified_gateway_research_20260308`
  - `R35_llm_gateway_migration_plan_20260308`

## Context

MsGalaxy 当前真实运行基线中，LLM 访问路径存在以下问题：

1. `MetaReasoner` 与四个 agent 直接调用 DashScope 原生 SDK；
2. orchestrator 仅负责分发 `api_key / model / base_url`，并未形成统一 provider gateway；
3. runner 层仍存在 `qwen3-max` 与 `OPENAI_API_KEY` 的硬编码覆盖；
4. 配置层仍以单块 `openai:` 直配为主，不适合演进到多供应商 profile 治理；
5. 当前默认模型是 `qwen3-max`，但未来需要接入 GPT / Claude / GLM / MiniMax。

项目的目标不是立刻改变默认供应商，而是：

- 在保持当前 Qwen baseline 的前提下；
- 建立能支撑多供应商切换的统一接入层；
- 避免 provider-specific wire shape 继续渗入业务代码。

## Problem Statement

需要做出一个明确架构决策：

- 内部统一调用面应以什么 API 作为 canonical contract；
- Qwen 是否继续以 DashScope 原生 SDK 为主；
- 配置与 CLI 是否继续围绕单块 `openai:` 直配展开；
- embedding 是否纳入统一 provider 治理边界。

若不在本轮明确这些问题，后续实现会继续在以下两条相互冲突的路径之间摇摆：

- “Qwen 原生 SDK 主路径 + 其他供应商兼容层补丁”
- “OpenAI-compatible 主路径 + 供应商能力差异通过 profile 管理”

## Decision

### 1. canonical API surface 固定为 `chat.completions` + `embeddings`

MsGalaxy 内部统一的 LLM 主合同固定为：

- 文本生成：`chat.completions`
- 向量能力：`embeddings`

**不**将 `responses` 设为当前阶段的内部统一标准。

原因：

- 多家供应商官方兼容文档的最大公共子集是 `chat.completions`；
- 当前业务主需求是稳定生成结构化 JSON 文本输出；
- 若采用 `responses` 作为统一标准，会把 OpenAI 原生接口差异前移到业务层。

### 2. provider profile registry 替代单块 `openai:` 直配思路

运行时配置从“单一 `openai:` 配置块直配”演进为 provider profile registry。

registry 至少包含：

- 默认文本 profile
- 默认 embedding profile
- 每个 profile 的 provider / model / endpoint / key source / capability / fallback policy

业务层只消费“已解析 profile 的标准化结果”，而不是自己理解原始配置字段。

### 3. default profile 固定为 `qwen_max_default`

默认供应商策略保持不变：

- 默认仍是 Qwen Max；
- 当前治理规则中关于默认模型的要求，迁移为“默认文本 profile 指向 Qwen Max”。

这意味着：

- 默认供应商不变；
- 但默认访问实现可以改变。

### 4. 默认访问路径改为 DashScope OpenAI-compatible

`qwen_max_default` 的默认 transport 固定为：

- DashScope OpenAI-compatible endpoint

而不是 DashScope 原生 SDK 主路径。

这保证：

- 默认 baseline 仍是 Qwen；
- 业务层与供应商 SDK 解耦；
- Qwen 与未来其他供应商走同一种主抽象。

### 5. DashScope native adapter 仅作为显式受控 fallback

DashScope 原生 SDK 可以保留，但只能作为 fallback adapter。

触发条件必须同时受控：

- 当前 profile 明确允许 native fallback；
- 当前请求命中 capability gate，确认兼容层不满足该请求能力；
- 或 profile 被显式指定为 native transport。

以下场景 **禁止** 自动 native fallback：

- 鉴权失败；
- 模型名错误；
- `base_url` 错误；
- 供应商 endpoint 不可达；
- 配置缺失或 CLI 覆盖错误。

原因：

- 这些都属于配置/接线错误；
- 若静默回退，会掩盖真实问题并污染 observability。

### 6. legacy 配置保留一阶段兼容，但必须进入淘汰窗口

现有 `openai.model / api_key / base_url` 配置可以在一阶段被兼容读取。

但兼容方式必须是：

- 运行时先归一化为隐式 profile；
- 日志中打印 deprecation warning；
- 文档中明确后续淘汰窗口。

禁止长期维持“双语义配置”并列常态。

### 7. provider-specific wire shape 不得泄漏到业务层

业务层不得直接依赖：

- DashScope 原生响应结构；
- OpenAI SDK 原始对象结构；
- 任何单家供应商的错误字段或输出块格式。

允许存在 provider adapter，但 adapter 之外的代码只能消费统一后的：

- 文本内容
- 标准化元数据
- 标准化错误语义

### 8. embedding 纳入统一 provider 治理边界

embedding 能力纳入 provider registry 与统一 gateway。

但当前 `mass` 默认 RAG 行为不改变：

- `MassRAGSystem` 继续保持当前本地 feature-hashing 默认实现；
- 外部 embedding 作为可选后端接入点，而非立即替换默认路径。

## Implemented / Accepted Target / Deferred

### Implemented（截至 2026-03-08 的真实实现）

- 默认基线仍是 `qwen3-max`；
- 真实活跃 LLM 调用仍以 DashScope 原生 SDK 为主；
- orchestrator 负责分发 `openai` 配置字段；
- `MassRAGSystem` 默认外部 embedding 并未成为主路径。

### Accepted Target（本 ADR 接受的目标架构）

- 主合同迁移为 `chat.completions` + `embeddings`；
- 引入 provider profile registry；
- 默认 profile 为 `qwen_max_default`；
- 默认通过 DashScope OpenAI-compatible 访问 Qwen；
- DashScope 原生 SDK 仅作显式 fallback；
- 业务层不再感知 provider wire shape。

### Deferred（明确延后，不在本 ADR 落地范围）

- 不在本 ADR 中把默认供应商改为 GPT、Claude、GLM 或 MiniMax；
- 不在本 ADR 中把 `MassRAGSystem` 默认路径切到外部 embedding；
- 不在本 ADR 中引入 OpenAI `responses` 作为统一主合同；
- 不在本 ADR 中引入 provider-specific tool calling / multimodal event model。

## Consequences

### Positive

- 为 GPT / Claude / GLM / MiniMax 留出统一接入面；
- 保持 Qwen baseline 不变，降低迁移风险；
- runner、config、observability 的治理口径将显著简化；
- 业务层可摆脱对 DashScope SDK 的直接依赖。

### Negative

- 需要补一层 provider gateway 与 profile 解析；
- 需要对现有 runner、orchestrator、MetaReasoner、agents 做迁移；
- 需要明确 capability gate 与 fallback policy，避免“兼容层幻想”。

### Neutral / Tradeoff

- 本决策优先选择“跨供应商兼容公共面”，而不是追求 OpenAI 单家最新接口特性；
- `responses` 并未被否定，只是未被选为当前阶段统一主合同。

## Follow-up

后续实施必须依据 `R35_llm_gateway_migration_plan_20260308` 执行，并至少覆盖：

1. gateway 与 profile resolver 建立；
2. orchestrator 注入统一 gateway；
3. `MetaReasoner` 与四个 agent 迁移；
4. runner 与 CLI 覆写逻辑治理；
5. embedding 接口纳入统一边界；
6. observability 补齐 `profile/provider/model/api_style/fallback_used/key_source(masked)`。
