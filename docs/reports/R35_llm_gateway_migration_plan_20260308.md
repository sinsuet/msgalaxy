# R35 LLM 统一网关迁移蓝图与执行清单（2026-03-08）

## 1. 目标与边界

本报告用于把 `0009-llm-openai-compatible-gateway` 转换为可执行迁移蓝图。

本轮迁移目标：

- 建立统一 LLM gateway；
- 主合同固定为 `chat.completions` + `embeddings`；
- 默认 profile 仍指向 `qwen3-max`；
- 默认通过 DashScope OpenAI-compatible 访问；
- DashScope 原生 SDK 只保留 fallback；
- 不改变当前 `mass` 默认 RAG 行为；
- 不改变现有 JSON 提案链路与 `vop_maas` 审计口径。

本轮不做：

- 不改变默认供应商；
- 不把 `responses` 设为内部主合同；
- 不强推 `MassRAGSystem` 默认切换到外部 embedding；
- 不在业务层引入 provider-specific tool/event 结构。

---

## 2. 迁移总顺序

迁移顺序固定为：

1. **gateway**
2. **orchestrator 注入**
3. **MetaReasoner**
4. **四个 agents**
5. **runners / CLI / config 归一化**
6. **embedding 接口预留**
7. **日志与可观测性补齐**
8. **清理 legacy 主路径**

不得跳步直接改 runner 或直接改业务调用点，否则会造成“调用面未统一、配置面先分裂”的中间态。

---

## 3. Phase-by-Phase 执行清单

### Phase 0：守住当前基线

目标：

- 在改动前固化当前 Qwen baseline；
- 明确哪些行为必须保持不变。

必须保持不变的内容：

- 默认文本模型口径仍是 `qwen3-max`；
- `MetaReasoner` 现有 JSON 抽取、autofill、fallback 诊断语义；
- 四个 agent 的 JSON proposal 输出契约；
- `vop_maas` 的 policy metadata、reflective replan、audit 字段；
- `MassRAGSystem` 默认本地 feature-hashing 路径。

### Phase 1：建立统一 gateway 与 profile resolver

新增组件：

- `LLMGateway`
- `LLMProfileResolver`
- `OpenAICompatibleAdapter`
- `DashScopeNativeAdapter`

统一网关至少暴露：

- `generate_text(...)`
- `generate_embeddings(...)`

网关返回统一结构，至少包含：

- `text`
- `provider`
- `profile`
- `model`
- `api_style`
- `fallback_used`
- `raw_response`（仅 adapter 内部或 debug 需要）

profile resolver 至少解决：

- 默认文本 profile
- 默认 embedding profile
- profile 到 provider/model/base_url/api_key_env/capabilities 的映射
- legacy `openai.*` 到隐式 profile 的归一化

### Phase 2：orchestrator 改为注入 gateway

`workflow/orchestrator.py` 改动原则：

- orchestrator 不再只分发原始 `api_key / model / base_url`；
- 改为初始化 `LLMProfileResolver` 与 `LLMGateway`；
- 把统一 gateway 注入给：
  - `MetaReasoner`
  - `GeometryAgent`
  - `ThermalAgent`
  - `StructuralAgent`
  - `PowerAgent`

orchestrator 仍保留：

- `.env` 加载；
- 配置文件环境变量替换；
- 模式路由与日志初始化。

但 LLM 初始化逻辑从“字段分发器”升级为“统一 gateway 装配器”。

### Phase 3：迁移 `MetaReasoner`

`MetaReasoner` 是第一优先迁移对象，因为它同时影响：

- `mass` 的 modeling intent；
- `vop_maas` 的 policy program；
- 反射、fallback、autofill 诊断链。

迁移要求：

- 不再直接调用 `dashscope.Generation.call(...)`；
- 改为调用 gateway 的 `generate_text(...)`；
- 保持以下逻辑不变：
  - JSON 抽取
  - fenced JSON 兼容
  - autofill
  - fallback diagnostics
  - logger 记录结构

若 profile 支持原生 JSON mode，则 gateway 负责打开；
若不支持，则 gateway 降级为普通文本返回，`MetaReasoner` 继续复用现有 JSON extraction 逻辑。

### Phase 4：迁移四个 agents

迁移对象：

- `GeometryAgent`
- `ThermalAgent`
- `StructuralAgent`
- `PowerAgent`

迁移要求：

- 四个 agent 的 prompt 逻辑不改；
- 结构化 proposal schema 不改；
- `log_llm_interaction(...)` 的记录时机不改；
- 仅替换底层调用实现为 gateway。

必须避免：

- 一部分 agent 仍走 DashScope native，另一部分 agent 已走 gateway；
- 不允许形成长期混用状态。

### Phase 5：治理 runner、CLI 与配置

这是本轮第二个高风险点，因为当前 runner 中存在模型与密钥硬编码。

必须完成：

- 去掉 runner 中对 `qwen3-max` 的直接强写死；
- 去掉 runner 中“只认 `OPENAI_API_KEY` / `DASHSCOPE_API_KEY`”的分散逻辑；
- 引入统一 CLI 覆盖项：`--llm-profile`；
- profile 优先级固定为：
  1. CLI 显式 profile
  2. config 默认 profile
  3. legacy `openai.*` 归一化 profile
  4. env 补齐 profile 字段

禁止出现：

- runner 根据模式偷偷改模型；
- CLI 参数只改模型、不改 profile；
- `vop_maas`、`mass`、`agent_loop` 使用不同的 LLM 解析规则。

### Phase 6：embedding 接口预留

本阶段目标不是切换默认 RAG，而是把 embedding 纳入统一 provider 治理边界。

必须完成：

- gateway 暴露 `generate_embeddings(...)`；
- profile schema 支持 `default_embedding_profile`；
- capability flags 中至少包含 `embeddings`；
- `MassRAGSystem` 增加可选外部 embedding backend 插槽。

本阶段明确不做：

- 不强制修改 `MassRAGSystem` 默认 feature-hashing 逻辑；
- 不要求 benchmark 立即引入外部 embedding 成本。

### Phase 7：日志与可观测性补齐

所有 LLM 交互记录至少补齐：

- `profile`
- `provider`
- `model`
- `api_style`
- `fallback_used`
- `key_source`
- `key_source_masked`

并明确记录：

- 是否命中 native fallback；
- fallback 的触发原因；
- fallback 的上一层错误语义。

禁止出现：

- 配置错误被 native fallback 掩盖；
- 审计中无法区分“兼容层成功”与“native 回退成功”。

### Phase 8：清理 legacy 主路径

当以下条件全部满足后，才能进入清理阶段：

- `MetaReasoner` 与四个 agent 已全部走 gateway；
- 三条模式 runner 已统一 profile 解析链；
- targeted regression 已通过；
- observability 已能完整记录 profile 与 fallback 信息。

清理动作：

- 活跃主路径中移除直接 `dashscope.Generation.call(...)`；
- DashScope SDK 只保留在 fallback adapter 内；
- legacy `openai.*` 直配保留兼容窗口，但输出 deprecation warning。

---

## 4. 配置、CLI、日志接口变更

### 4.1 配置接口

目标形态：

- `default_text_profile`
- `default_embedding_profile`
- `profiles.<name>.provider`
- `profiles.<name>.api_style`
- `profiles.<name>.model`
- `profiles.<name>.base_url`
- `profiles.<name>.api_key_env`
- `profiles.<name>.capabilities`
- `profiles.<name>.native_fallback`

推荐默认 profile：

- `qwen_max_default`
- `qwen_embedding_default`

保留的 legacy 映射：

- `openai.model`
- `openai.api_key`
- `openai.base_url`

这些 legacy 字段只用于一阶段归一化，不再作为长期治理中心。

### 4.2 CLI 覆盖项

新增或统一：

- `--llm-profile`

本轮不引入：

- `--model`
- `--provider`
- `--base-url`

作为长期主覆盖项；这些字段会导致 profile 治理失效。必要时只作为 debug override，且不得进入常规 runner 主路径。

### 4.3 日志字段

必须新增或统一：

- `profile`
- `provider`
- `model`
- `api_style`
- `fallback_used`
- `fallback_reason`
- `key_source`
- `key_source_masked`

与现有字段的关系：

- 不替换现有 `llm_interaction` 主体；
- 只做补充，不破坏历史 reader。

---

## 5. 验收标准

### 5.1 文本生成验收

以下都必须满足：

- `mass / vop_maas / agent_loop` 三条模式都通过同一 profile 解析链获得 LLM 配置；
- 默认 profile 下仍等价访问 Qwen Max；
- 切换到 GPT / Claude / GLM / MiniMax 的兼容 profile 时，不需要修改业务代码；
- `MetaReasoner` 与四个 agent 的 JSON 输出契约不退化。

### 5.2 runner 与配置验收

- runner 不再覆写模型名；
- CLI 显式 profile 优先级最高；
- legacy `openai.*` 配置仍可运行，但会输出迁移提示；
- 不同模式不再使用不同的 key 解析逻辑。

### 5.3 fallback 验收

必须验证：

- capability 不满足时，可按 profile 策略触发 native fallback；
- 鉴权失败、模型名错误、base_url 错误时，**不得**自动 fallback；
- 日志中能明确区分：
  - compat 成功
  - compat 失败 + native fallback 成功
  - compat 失败 + native fallback 禁止

### 5.4 embedding 验收

- gateway 可暴露统一 embedding 调用面；
- profile 层可声明 embedding 能力；
- `MassRAGSystem` 默认行为不变；
- 外部 embedding 插槽可在后续显式启用。

---

## 6. 测试矩阵

### 6.1 单元测试

- profile 解析优先级
- legacy 配置归一化
- gateway 标准化返回结构
- JSON mode capability gate
- fallback gate
- key source 解析与 masked logging

### 6.2 集成测试

- `MetaReasoner.generate_modeling_intent(...)`
- `MetaReasoner.generate_policy_program(...)`
- 四个 agent proposal 生成
- `vop_maas` reflective replan 链
- runner `--llm-profile` 覆盖链

### 6.3 回归测试

- targeted regression 保持通过
- `summary/events/tables` 既有字段不退化
- `vop_maas` round-level 审计字段稳定
- 默认 Qwen baseline 输出不发生非预期漂移

---

## 7. 回滚策略

若迁移过程中出现不可接受回归，回滚顺序固定为：

1. 保留 profile resolver，但临时把默认 transport 固定回 DashScope native；
2. 若仍有问题，则仅回滚 gateway 注入与调用点迁移，保留文档与配置 schema；
3. 不回滚 ADR 决策，只回滚实现节奏。

原因：

- 这次的方向性决策已经在 ADR 中被接受；
- 真正需要可逆的是实现节奏，不是架构方向。

---

## 8. 最终执行口径

后续代码实现必须遵守以下一句话原则：

> **默认仍是 Qwen Max，但 Qwen 也必须先被纳入 OpenAI-compatible 主层；DashScope 原生 SDK 只是受控 fallback，而不是默认入口。**

只要后续实现偏离这句话，就说明迁移执行已经偏离 `0009` 的设计目标。
