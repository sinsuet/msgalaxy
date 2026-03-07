# data 目录说明

本目录用于存放 MsGalaxy 运行时会读取或生成的数据资产。当前仓库中的 `data` 目录主要承载 `mass` 模式下 `CGRAG-Mass` 检索后端使用的知识库数据。

## 当前结构

```text
data/
└─ knowledge_base/
   ├─ mass_evidence.jsonl
   ├─ embeddings.pkl
   └─ knowledge_base.json
```

## 子目录说明

### `knowledge_base/`

`mass` 模式的知识库目录。默认路径由配置和代码共同约定为 `data/knowledge_base`，相关入口包括：

- `optimization/knowledge/mass/mass_rag_system.py`
- `optimization/knowledge/mass/evidence_store.py`
- `api/cli.py`
- `config/system/agent_loop/base.yaml`

## 文件说明

### `knowledge_base/mass_evidence.jsonl`

当前**实际使用中的核心知识库文件**。  
它采用 JSON Lines 格式，一行表示一条结构化 evidence，供 `CGRAG-Mass` 检索、重排和反射流程使用。

典型字段包括：

- `evidence_id`：证据唯一 ID
- `mode`：适用模式，当前主要为 `mass`
- `phase_hint`：提示适用阶段，如 A/B/C/D
- `category`：证据类型，如 `standard`、`heuristic`、`case`
- `title` / `content`：证据标题和正文
- `query_signature`：适用问题特征，如 violation 类型
- `action_signature`：关联动作族或算子族
- `outcome_signature`：结果摘要，如是否 strict-feasible
- `physics_provenance`：物理来源或 source-gate 信息
- `tags`：检索辅助标签

说明：

- 如果该文件不存在或为空，`MassEvidenceStore` 会自动写入一组默认 evidence。
- 可以通过历史运行结果回灌该知识库，例如：

```bash
python -m optimization.knowledge.mass.ingest_from_runs --runs-root experiments --kb-path data/knowledge_base
```

### `knowledge_base/embeddings.pkl`

当前仓库中保留的二进制数据文件。  
从现有代码路径看，当前 `CGRAG-Mass` 检索实现已使用本地特征哈希语义检索，而不是从该文件加载向量，因此它**不是当前主运行链的必需输入**。

可将其视为：

- 历史实验或旧知识库流程遗留产物，或
- 供离线分析/兼容性保留的缓存文件

在未确认上游脚本仍依赖它之前，不建议手动删除。

### `knowledge_base/knowledge_base.json`

当前仓库中的该文件为空文件。  
从现有主代码路径搜索结果看，运行时 `mass` 检索后端并不直接读取它，因此它**不是当前 `CGRAG-Mass` 主链路的必需文件**。

可将其视为：

- 旧版知识库存储格式的占位文件，或
- 迁移过程中保留的兼容痕迹

如果后续清理历史资产，建议先确认相关研究脚本、报告生成脚本或人工流程是否仍引用该文件。

## 使用建议

- 日常维护 `mass` 知识库时，优先更新 `data/knowledge_base/mass_evidence.jsonl`。
- 新增 evidence 时，尽量保持字段完整，尤其是 `phase_hint`、`query_signature`、`action_signature` 和 `outcome_signature`。
- 不要把 `knowledge_base.json` 或 `embeddings.pkl` 误认为当前主检索链的唯一数据源。
- 若需要重建知识库，优先使用 `optimization.knowledge.mass.ingest_from_runs` 从 `experiments/` 产物导入。

## 注意事项

- 本目录存放的是运行数据与知识资产，不是正式设计文档。
- 这里的内容可能随实验运行、知识回灌或检索策略升级而变化。
- 根据当前项目基线，`mass` 模式默认知识后端是 `CGRAG-Mass`，旧通用 RAG 路径已移除，不应再按旧方案补回通用 `rag_system.py`。
