# R34 LLM 统一接入网关研究与方案比选（2026-03-08）

## 1. 本轮目标

本报告用于回答 3 个问题：

1. MsGalaxy 后续是否应把 LLM 主接入面统一到 OpenAI-compatible 层；
2. 若默认供应商仍保持 `qwen3-max`，是否应继续以 DashScope 原生 SDK 为主，还是改成 DashScope OpenAI-compatible 为主；
3. 若未来需要接入 GPT / Claude / GLM / MiniMax，最小治理成本、最大兼容性的统一抽象应该选什么。

结论先行：

- **应统一到 OpenAI-compatible 主层**；
- **统一标准应选 `chat.completions`，而不是 `responses`**；
- **默认供应商仍保持 Qwen Max**，但默认访问方式应切到 **DashScope OpenAI-compatible**；
- **DashScope 原生 SDK** 仅保留为 **显式受控 fallback**，不得继续作为主路径；
- **embedding 能力应纳入统一网关边界**，但当前 `mass` 默认 RAG 行为不应被这次重构强制改变。

---

## 2. 当前仓库真实接入现状

### 2.1 运行时调用面并未统一

当前真实活跃运行链路中，LLM 调用仍以 DashScope 原生 SDK 为主，主要散落在以下位置：

- `optimization/meta_reasoner.py`
- `optimization/modes/agent_loop/agents/geometry_agent.py`
- `optimization/modes/agent_loop/agents/thermal_agent.py`
- `optimization/modes/agent_loop/agents/structural_agent.py`
- `optimization/modes/agent_loop/agents/power_agent.py`

这些模块当前直接依赖 `dashscope.Generation.call(...)`，因此：

- 业务层直接耦合到供应商 SDK；
- 返回体结构与错误模型都带有 DashScope 语义；
- 若未来切到 GPT / Claude / GLM / MiniMax，无法只在配置层完成切换。

### 2.2 orchestrator 负责分发配置，但还不是 provider gateway

`workflow/orchestrator.py` 当前负责：

- 读取 `openai` 配置块；
- 解析 `api_key / model / base_url`；
- 将这些配置传给 `MetaReasoner` 与 4 个 agent；
- 初始化 `MassRAGSystem`。

但它当前并没有形成统一的 provider gateway，仅仅是“把同一组字段分发给多个调用方”。这意味着：

- 配置分发统一了；
- **调用实现没有统一**。

### 2.3 runner 层仍存在模型与密钥硬编码

当前 runner 层的典型问题：

- `run/mass/run_L1.py`
- `run/agent_loop/common.py`
- `run/vop_maas/common.py`

这些入口中仍存在以下治理问题：

- 将模型名硬编码回 `qwen3-max`；
- 直接读取 `OPENAI_API_KEY` 或 `DASHSCOPE_API_KEY`；
- CLI 与配置文件的优先级不稳定；
- 供应商切换不是通过“profile”完成，而是通过分散字段与局部逻辑完成。

这类入口硬编码是后续统一 provider 层的主要阻碍。

### 2.4 配置语义仍是单块 `openai:`，不是 provider registry

当前配置文件中的 `openai:` 语义本质上是“单一供应商直配”，例如：

- `config/system/agent_loop/base.yaml`
- `config/system/mass/base.yaml`
- `config/system/vop_maas/base.yaml`

问题在于：

- `openai:` 这一命名已经在语义上混合了“SDK 类型”和“真实供应商”；
- 未来如果接 GPT / Claude / GLM / MiniMax，这个配置块将同时承担 provider、模型、endpoint、兼容层类型 4 种语义；
- 一旦继续沿用当前模式，配置会快速演化成隐式 if/else 网络。

### 2.5 embedding 目前并未真正外部化

当前 `MassRAGSystem` 的语义检索默认实现仍是本地 feature-hashing 路径：

- `optimization/knowledge/mass/retrievers.py`
- `optimization/knowledge/mass/mass_rag_system.py`

虽然构造参数里有 `api_key / embedding_model / base_url`，但当前默认路径并未把外部 embedding 作为实际运行依赖。这一点非常关键：

- 说明本轮可以把 embedding 纳入统一网关边界；
- 但 **不应把“纳入统一边界”误写成“当前默认 RAG 已切到外部 embedding”**。

---

## 3. 外部官方兼容面调研

以下结论只依据官方文档，不依赖社区二手封装。

### 3.1 OpenAI 官方能力面

- OpenAI 官方当前提供并主推 [Responses API](https://platform.openai.com/docs/api-reference/responses)；
- 同时仍提供 [Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)；
- OpenAI 官方 SDK 也继续支持 `chat.completions.create(...)` 调用形式。

对 MsGalaxy 的意义：

- `responses` 代表 OpenAI 自身更现代的能力面；
- 但 `chat.completions` 仍是稳定、成熟、广泛兼容的兼容层基准。

### 3.2 DashScope 官方兼容结论

阿里云官方文档已明确给出 [DashScope 与 OpenAI 兼容的调用方式](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)，其示例直接使用：

- `OpenAI(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", ...)`
- `client.chat.completions.create(...)`

这说明：

- **Qwen 并不要求业务层必须直接依赖 DashScope 原生 SDK**；
- 使用 OpenAI-compatible 访问 Qwen 是官方支持路径；
- 因此“默认仍是 Qwen Max”与“内部主层改成 OpenAI-compatible”并不冲突。

### 3.3 Anthropic 官方兼容结论

Anthropic 官方文档提供 [OpenAI SDK compatibility](https://docs.anthropic.com/en/api/openai-sdk)，同样展示了：

- `from openai import OpenAI`
- `client.chat.completions.create(...)`

这说明 Claude 的一条官方支持路径就是 OpenAI-compatible 调用。

### 3.4 GLM 官方兼容结论

智谱官方在 [OpenAI 接口兼容说明](https://docs.bigmodel.cn/cn/guide/openai/introduction) 中给出 OpenAI SDK 接入方式，示例同样围绕：

- OpenAI client
- 兼容 `chat.completions`

这表明 GLM 也可以被纳入同一个兼容抽象。

### 3.5 MiniMax 官方兼容结论

MiniMax 官方在 [Chat Completion v2 文档](https://platform.minimaxi.com/document/ChatCompletion_v2) 中给出 OpenAI SDK 风格调用示例，同样围绕：

- OpenAI client
- `chat.completions`

因此 MiniMax 也能落入统一兼容层。

### 3.6 兼容层结论

从官方文档交集来看：

- OpenAI：`chat.completions` 可用
- DashScope：OpenAI-compatible + `chat.completions` 可用
- Anthropic：OpenAI-compatible + `chat.completions` 可用
- GLM：OpenAI-compatible + `chat.completions` 可用
- MiniMax：OpenAI-compatible + `chat.completions` 可用

因此跨厂商的最大公共子集不是 `responses`，而是：

- **OpenAI-compatible SDK**
- **`chat.completions`**

---

## 4. 为什么统一标准选 `chat.completions`，而不是 `responses`

### 4.1 `responses` 的优势

- 对 OpenAI 原生能力表达更现代；
- 更适合未来多模态、工具调用、统一输出事件流；
- 是 OpenAI 自身后续演进的主方向。

### 4.2 `responses` 不适合作为当前 MsGalaxy 的跨供应商统一层

当前阶段不选择 `responses` 作为内部统一标准，原因不是它“不先进”，而是它 **不适合作为多供应商交集**：

1. **供应商兼容交集不足**
   - 本轮调研到的多家官方兼容文档，绝大多数都以 OpenAI SDK 的 `chat.completions` 为示例；
   - `responses` 更偏 OpenAI 原生能力面，而不是跨厂商兼容合同。

2. **当前业务需求以结构化 JSON 文本输出为主**
   - `MetaReasoner` 与四个 agent 当前核心需求是稳定地产出 JSON 提案；
   - 现阶段并不需要为了统一层引入 OpenAI 特有的更复杂事件模型。

3. **会把 provider 差异前移到业务层**
   - 若将 `responses` 设为内部标准，后续接 Claude / GLM / MiniMax / DashScope 时，大概率需要 provider-specific 特判；
   - 这违背本轮“让业务层不感知供应商 wire shape”的目标。

### 4.3 结论

MsGalaxy 当前阶段的统一目标是：

- **先统一接入治理与最小公共能力面**；
- 不是优先追求某一家厂商的最新接口特性。

因此内部主标准应固定为：

- `chat.completions`
- `embeddings`

而不是 `responses`。

---

## 5. 为什么默认 Qwen 仍保留，但实现层应迁到 OpenAI-compatible

### 5.1 默认 Qwen 仍合理

这与当前项目规则和现有运行基线一致：

- `RULES.md` 明确默认模型为 `qwen3-max`；
- `HANDOFF.md` 也将其视为当前 baseline。

因此短期内不应把“统一兼容层”误解为“立刻改变默认供应商”。

### 5.2 但默认访问方式不应继续是 DashScope 原生 SDK

若继续保持 DashScope 原生 SDK 为主，会带来 3 个长期问题：

1. 业务逻辑直接耦合供应商 SDK；
2. 新增供应商时需要进入多处业务代码修改；
3. Qwen 与非 Qwen 的接入方式不对称，最终会形成“Qwen 一套、其他供应商一套”的双轨技术债。

### 5.3 正确做法

应把策略改为：

- **默认模型仍是 `qwen3-max`**
- **默认 profile 改为 `qwen_max_default`**
- **默认通过 DashScope OpenAI-compatible endpoint 访问**

这样可以同时满足：

- 与现有规则兼容；
- 与多供应商统一方向兼容；
- 与未来 runner / config / audit 治理兼容。

---

## 6. embedding 是否纳入本轮统一

结论：**纳入统一网关边界，但不改变当前 `mass` 默认 RAG 行为。**

### 6.1 为什么要纳入

如果本轮只统一文本生成，不统一 embedding，则 provider 治理会在第二阶段重新裂开：

- 文本 profile 一套；
- embedding profile 另一套；
- key / endpoint / capability / logging 需要重复设计。

从架构完整性看，embedding 应与文本生成一并进入 provider registry。

### 6.2 为什么不应强推默认 RAG 立刻切换

当前 `MassRAGSystem` 默认语义检索仍是本地 feature-hashing；这条路径：

- 无外部 API 依赖；
- 无 token 成本；
- 与当前 `mass` baseline 一致。

因此本轮正确边界是：

- 在 gateway / profile 层 **支持 embedding**；
- 但 `MassRAGSystem` 默认实现 **保持不变**；
- 未来若启用外部 embedding，应通过显式 profile 与 capability gate 打开。

---

## 7. 风险矩阵

| 风险项 | 说明 | 若继续现状 | 若采用统一网关 | 处置建议 |
|---|---|---:|---:|---|
| 兼容层能力差异 | 各家对 JSON mode、tooling、streaming 支持不完全一致 | 高 | 中 | 通过 profile capability flags 显式治理 |
| `responses` 误选为统一标准 | 会把 OpenAI 原生接口特性前移到业务层 | 高 | 低 | 内部固定 `chat.completions` |
| JSON 输出差异 | 不同 provider 对原生 JSON 输出支持不一致 | 中 | 中 | 统一 `expects_json` 语义与文本降级抽取 |
| fallback 误触发 | 鉴权/模型名/base_url 错误被静默回退掩盖 | 高 | 低 | 禁止在配置错误场景自动 native fallback |
| runner 硬编码覆盖 | 配置与 CLI 结果被局部脚本覆盖 | 高 | 低 | 引入 profile registry 与统一 CLI |
| embedding 能力不齐 | 各供应商 embedding 支持和维度不一致 | 中 | 中 | profile 层加入 capability，不假设全量可用 |
| 供应商 wire shape 泄漏 | 业务代码直接解析供应商响应 | 高 | 低 | 统一返回结构，只在 adapter 内部消化差异 |

---

## 8. 建议的正式决策

建议以 ADR 固化以下决策：

1. **canonical API surface = `chat.completions` + `embeddings`**
2. **provider profile registry** 替代单块 `openai:` 直配思路
3. **default profile = `qwen_max_default`**
4. **default transport = DashScope OpenAI-compatible**
5. **DashScope native SDK 仅作显式受控 fallback**
6. **禁止 provider-specific wire shape 泄漏到业务层**
7. **legacy `openai.model/api_key/base_url` 保留一阶段兼容，但必须进入迁移窗口**

---

## 9. 对后续实施的直接影响

若接受上述结论，则后续实现必须遵守：

- runner 不再直接覆写模型名；
- orchestrator 负责组装统一 gateway，而不是只分发字段；
- `MetaReasoner` 与四个 agent 全部改为依赖 gateway；
- `MassRAGSystem` 只接入 embedding 能力边界，不改变默认本地检索行为；
- LLM observability 必须追加：
  - `profile`
  - `provider`
  - `model`
  - `api_style`
  - `fallback_used`
  - `key_source(masked)`

---

## 10. 本报告的最终结论

MsGalaxy 当前最优解不是“继续维持 DashScope 原生 SDK 主路径”，也不是“直接全面切到 OpenAI Responses API”，而是：

- **以 OpenAI-compatible `chat.completions` + `embeddings` 作为主合同**
- **默认 profile 仍指向 Qwen Max**
- **默认经 DashScope OpenAI-compatible 访问**
- **DashScope 原生 SDK 保留为显式 fallback**

这一路径在“兼容当前 baseline”和“为多供应商扩展清障”之间取得了最好的平衡。
