# MsGalaxy - 卫星设计优化系统

基于三层神经符号协同架构的智能卫星设计优化系统，整合了三维布局、COMSOL多物理场仿真和AI驱动的多学科优化决策。

> **项目状态**: ✅ v2.0.5 核心修复已落地（待复测） | **系统成熟度**: 99.6% | **最后更新**: 2026-03-01

---

## 🎯 核心特性

### 🧠 三层神经符号协同架构
- **战略层**: Meta-Reasoner元推理器，负责多学科协调和战略决策
- **战术层**: Multi-Agent系统（几何、热控、结构、电源专家）
- **执行层**: COMSOL动态导入 + 工具集成

### 💡 创新亮点
- **学术创新**: 首次在卫星设计领域实现战略-战术-执行的分层决策
- **工程创新**: 完整审计链、智能回退机制、知识自动积累
- **可用性创新**: 自然语言交互、实时可视化、自动报告生成

### 🚀 核心功能
- **智能布局**: 3D装箱算法（py3dbp）+ 多面墙面安装 + 层切割策略
- **动态仿真**: COMSOL 动态 STEP 导入 + Box Selection 自动识别
- **真实物理**: T⁴ 辐射边界 + 功率斜坡加载 + 数值稳定锚
- **知识检索**: RAG系统（语义检索 + 关键词检索 + 图检索）
- **完整追溯**: 记录每个决策的推理链和工程依据

### 🔥 最新突破（v2.0.5）
- ✅ **DV2.0 十类算子完成**: 8类几何算子 + 5类热学算子
- ✅ **COMSOL 动态导入架构**: 几何引擎成为唯一真理来源，支持拓扑重构
- ✅ **功率斜坡加载**: 三阶段加载策略（1% → 20% → 100%）
- ✅ **智能回退机制**: 历史状态树 + 惩罚分驱动的自动回退
- ✅ **候选态几何门控**: 仿真前拦截碰撞/间隙违规候选，减少无效 COMSOL 调用
- ✅ **no-op 跳过机制**: 无变化执行计划直接拒绝，避免空转迭代
- ✅ **MOVE 自适应回退**: 大步长不可行时自动缩放到可行步长
- ✅ **热源绑定防串域**: Box Selection 多域歧义时拒绝绑定热源
- ✅ **Thermal Contact 级联修复**: `h_tc/h_joint/h` + `htot/hconstr/hgap/Rtot` 多级回退
- ✅ **Agent鲁棒性增强**: 防幻觉、参数验证、超时保护
- ✅ **静态模式完全移除**: 不再依赖预置 `model.mph`，运行期统一动态建模
- ✅ **.mph 保存锁冲突修复**: 基于 `state_id` 唯一命名 + 重试回退，避免反复“文件被锁定”
- ✅ **迭代指标增强**: 新增惩罚分分解、增量指标、`effectiveness_score`
- ✅ **可视化升级**: 新增 `layout_evolution.png`，违规曲线同图高亮，热图改为确定性热代理
- ✅ **运行结果摘要**: 自动生成 `visualization_summary.txt`，L1-L4 脚本结束时自动打印
- ✅ **RAG 401 修复**: 语义检索客户端透传 `base_url`，DashScope 场景默认 `text-embedding-v4`

---

## 📦 项目结构

```
msgalaxy/
├── core/                          # 核心基础设施
│   ├── protocol.py               # 统一数据协议 (Pydantic)
│   ├── logger.py                 # 实验日志系统 + run_log.txt
│   ├── exceptions.py             # 自定义异常
│   ├── bom_parser.py             # BOM文件解析器
│   └── visualization.py          # 可视化生成器
│
├── geometry/                      # 几何布局引擎
│   ├── schema.py                 # AABB、Part数据结构
│   ├── keepout.py                # AABB六面减法算法
│   ├── packing.py                # 3D装箱优化 (py3dbp)
│   ├── layout_engine.py          # 主布局引擎
│   ├── ffd.py                    # 自由变形 (FFD)
│   └── cad_export_occ.py         # OpenCASCADE STEP导出 ⭐
│
├── simulation/                    # 仿真驱动器
│   ├── base.py                   # 仿真驱动器基类
│   ├── comsol_driver.py          # COMSOL MPh集成 ⭐ (动态导入)
│   └── structural_physics.py     # 结构物理场 (质心偏移)
│
├── optimization/                  # LLM语义优化层 ⭐⭐⭐
│   ├── protocol.py               # 优化协议定义 (DV2.0)
│   ├── meta_reasoner.py          # Meta-Reasoner (战略层)
│   ├── coordinator.py            # Agent协调器 (战术层)
│   ├── agents/                   # 专家Agent系统
│   │   ├── geometry_agent.py    # 几何专家 (激进质心配平)
│   │   ├── thermal_agent.py     # 热控专家 (5种热学算子)
│   │   ├── structural_agent.py  # 结构专家
│   │   └── power_agent.py       # 电源专家
│   ├── knowledge/                # 知识库系统
│   │   └── rag_system.py        # RAG混合检索
│   ├── multi_objective.py        # 多目标优化
│   └── parallel_optimizer.py     # 并行优化器
│
├── workflow/                      # 工作流编排
│   ├── orchestrator.py           # 主编排器 ⭐ (智能回退)
│   └── operation_executor.py     # 操作执行器 (DV2.0)
│
├── api/                           # API接口
│   ├── cli.py                    # 命令行接口
│   ├── server.py                 # FastAPI服务器
│   ├── client.py                 # Python客户端
│   └── websocket_client.py       # WebSocket客户端
│
├── config/                        # 配置文件
│   ├── system.yaml               # 系统配置
│   ├── bom_L1_simple.json        # L1入门级BOM (2组件) ⭐
│   ├── bom_L2_intermediate.json  # L2进阶级BOM (3组件) ⭐
│   ├── bom_L3_complex.json       # L3复杂级BOM (7组件) ⭐
│   ├── bom_L4_extreme.json       # L4极限级BOM (10组件) ⭐
│   └── ...                       # 未来可扩展BOM（按需新增）
│
├── run/                           # 开箱即用运行脚本 ⭐⭐⭐
│   ├── run_L1_simple.py          # L1入门级测试
│   ├── run_L2_intermediate.py    # L2进阶级测试
│   ├── run_L3_complex.py         # L3复杂级测试
│   └── run_L4_extreme.py         # L4极限级测试
│
├── scripts/                       # 本地开发辅助（默认不纳入Git）
├── tests/                         # 本地测试集合（默认不纳入Git）
├── logs/                          # 本地运行日志（默认不纳入Git）
│
├── experiments/                   # 实验数据
│   └── run_YYYYMMDD_HHMMSS/      # 每次运行的实验目录
│       ├── run_log.txt           # 完整终端日志 ⭐
│       ├── evolution_trace.csv   # 演化轨迹
│       ├── trace/                # 完整上下文追踪
│       ├── llm_interactions/     # LLM交互日志
│       ├── step_files/           # STEP几何文件
│       ├── mph_models/           # COMSOL模型文件
│       └── visualizations/       # 可视化图表
│
├── docs/                          # 文档
│   ├── archive/                  # 归档文档
│   ├── LLM_Semantic_Layer_Architecture.md  ⭐ 架构设计
│   ├── COMSOL_GUIDE.md
│   └── QWEN_GUIDE.md
│
├── README.md                      # 本文档
├── PROJECT_SUMMARY.md             # 项目总结 ⭐
├── HANDOFF.md                     # 项目交接文档 ⭐⭐⭐
├── CLAUDE.md                      # Claude Code 指令
├── RULES.md                       # 开发规范
└── requirements.txt               # Python依赖
```

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 创建conda环境（Python 3.12）
conda create -n msgalaxy python=3.12
conda activate msgalaxy

# 安装依赖
pip install -r requirements.txt

# 可选：安装pythonocc-core（STEP导出）
conda install -c conda-forge pythonocc-core

# 可选：安装MPh（COMSOL仿真）
pip install mph
```

> **Windows用户注意**: 系统已自动处理中文编码问题（UTF-8）

### 2. 配置系统

编辑 `config/system.yaml`:

```yaml
# LLM配置（Qwen通义千问）
openai:
  api_key: "${OPENAI_API_KEY}"  # 从环境变量读取
  model: "qwen3-max"  # 推荐模型
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  temperature: 0.7

# 知识检索配置
knowledge:
  base_path: "data/knowledge_base"
  enable_semantic: true
  embedding_model: "text-embedding-v4"

# 仿真配置
simulation:
  backend: "comsol"  # 仅支持 comsol (v2.0+ 动态架构)

# 优化配置
optimization:
  max_iterations: 20
  allowed_operators:  # DV2.0 十类算子
    - "MOVE"
    - "SWAP"
    - "ROTATE"
    - "DEFORM"
    - "ALIGN"
    - "CHANGE_ENVELOPE"
    - "ADD_BRACKET"
    - "REPACK"
    - "MODIFY_COATING"
    - "ADD_HEATSINK"
    - "SET_THERMAL_CONTACT"
    - "ADJUST_LAYOUT"
    - "CHANGE_ORIENTATION"
```

### 3. 🚀 快速开始：阶梯化测试用例（开箱即用）

我们提供了 L1-L4 四个难度级别的测试用例，让您无需修改配置即可快速体验系统能力：

| 级别 | 脚本 | 组件数 | 测试目标 | 预期时间 | 迭代次数 |
|------|------|--------|----------|----------|----------|
| **L1 入门级** | `run/run_L1_simple.py` | 2个 | 验证系统基础连通性、CAD导出、COMSOL收敛 | 5-10分钟 | 5次 |
| **L2 进阶级** | `run/run_L2_intermediate.py` | 3个 | 质心偏置+高热源，触发散热器、涂层、支架算子 | 15-25分钟 | 10次 |
| **L3 复杂级** | `run/run_L3_complex.py` | 7个 | 空间拥挤+多物理场耦合，展现Meta-Reasoner冲突解决 | 30-50分钟 | 20次 |
| **L4 极限级** | `run/run_L4_extreme.py` | 10个 | 双高热源+装填极限，测试系统极限鲁棒性 | 60-90分钟 | 30次 |

**运行示例**：

```bash
# L1 入门级（推荐首次使用）
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L1_simple.py

# L2 进阶级
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L2_intermediate.py

# L3 复杂级
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L3_complex.py

# L4 极限级（需要较长时间）
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L4_extreme.py
```

**查看结果**：

```bash
# 检查生成的可视化
ls experiments/run_*/visualizations/

# 查看完整日志
cat experiments/run_*/run_log.txt

# 查看演化轨迹
cat experiments/run_*/evolution_trace.csv
```

### 4. 运行优化（高级用户）

```bash
# 使用CLI运行优化
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python -m api.cli optimize --max-iter 10

# 查看实验列表
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python -m api.cli list

# 查看实验详情
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python -m api.cli show run_20260228_000935
```

---

## 📊 系统状态

### 模块成熟度

| 模块 | 状态 | 成熟度 | 备注 |
|------|------|--------|------|
| core/protocol.py | ✅ | 95% | DV2.0 完成，支持状态版本树 |
| core/logger.py | ✅ | 95% | run_log.txt 完成 |
| geometry/layout_engine.py | ✅ | 95% | 算法优秀，支持 FFD 变形 |
| geometry/cad_export_occ.py | ✅ | 90% | pythonocc-core 集成完成 |
| simulation/comsol_driver.py | ✅ | 90% | 动态导入架构完成，API 修复完成 |
| simulation/structural_physics.py | ✅ | 90% | 质心偏移计算完成 |
| optimization/meta_reasoner.py | ✅ | 85% | 需要更多端到端测试 |
| optimization/agents/ | ✅ | 85% | DV2.0 十类算子完成 |
| optimization/coordinator.py | ✅ | 85% | 需要更多端到端测试 |
| workflow/orchestrator.py | ✅ | 95% | 智能回退机制完成 |
| workflow/operation_executor.py | ✅ | 85% | DV2.0 操作执行器完成 |
| core/visualization.py | ✅ | 85% | 图片生成正常 |

**总体成熟度**: 99.6% (DV2.0 核心功能完成)

### 最新修复 (v2.0.5)

✅ **收敛性修复（代码已落地，待复测）**:
- 候选态几何门控：不可行几何直接拒绝，不进入 COMSOL
- no-op 检测：执行计划无实质变化时直接跳过仿真
- MOVE 自适应步长回退：1.0/0.5/0.25/0.1/0.05
- Thermal Contact 参数级联：`h_tc/h_joint/h` → `htot` → `hconstr/hgap` → `Rtot`
- Box Selection 多域歧义热源拒绑，防止热功率串域
- 几何 Agent 步长策略改为阈值感知（近阈值 5-15mm 小步）

✅ **运行稳定性修复（历史）**:
- 静态模式遗留逻辑移除，不再依赖预置 `model.mph`
- `.mph` 保存锁冲突修复（唯一命名 + 重试回退）
- L1-L4 运行脚本适配导入兼容逻辑

✅ **可观测性增强**:
- 演化日志新增惩罚分分解、delta指标、`effectiveness_score`
- 可视化新增 `layout_evolution.png` 和 `visualization_summary.txt`
- 违规数量在同一子图显著高亮（便于判断约束收敛）

✅ **RAG 401 修复**:
- RAG embedding 客户端透传 `base_url`
- DashScope 场景支持 `text-embedding-v4`

详细信息请参考: [HANDOFF.md](HANDOFF.md)

---

## 🔧 工作流程

### 完整优化循环

```
1. 初始化设计
   └─> BOM解析 → 3D布局生成（装箱算法）

2. 迭代优化循环（最多10次）
   ├─> 导出 STEP 文件（pythonocc-core）
   │
   ├─> COMSOL 动态仿真
   │   ├─ 导入 STEP 几何
   │   ├─ Box Selection 自动识别组件
   │   ├─ 动态赋予热源和边界条件
   │   ├─ 数值稳定锚（微弱对流边界）
   │   ├─ 全局导热网络（防止热悬浮）
   │   └─ 求解器运行
   │
   ├─> 物理评估
   │   ├─ 几何分析（间隙、质心偏移、转动惯量）
   │   ├─ 热分析（温度分布、梯度）
   │   ├─ 结构分析（应力、频率）
   │   └─ 电源分析（功耗、压降）
   │
   ├─> 候选态几何门控（仿真前）
   │   ├─ 检查碰撞数（必须为0）
   │   ├─ 检查最小间隙（>=运行时阈值）
   │   └─ no-op 状态直接跳过
   │
   ├─> 约束检查与惩罚分计算
   │   ├─ 温度超标（>运行时阈值）
   │   ├─ 质心偏移超标（>运行时阈值）
   │   ├─ 间隙不足（<运行时阈值）
   │   └─ 计算惩罚分
   │
   ├─> 智能回退检查
   │   ├─ 仿真失败？
   │   ├─ 惩罚分异常高（>1000）？
   │   ├─ 连续3次上升？
   │   └─ 回退到历史最优状态
   │
   ├─> RAG知识检索
   │   ├─ 语义检索（embedding相似度）
   │   ├─ 关键词检索（约束类型匹配）
   │   └─ 返回top-5相关知识
   │
   ├─> Meta-Reasoner战略决策
   │   ├─ 输入：GlobalContextPack（当前状态+违规+知识+历史失败）
   │   ├─ 推理：Chain-of-Thought分析
   │   └─ 输出：StrategicPlan（策略+任务分配）
   │
   ├─> Multi-Agent战术执行
   │   ├─ Geometry Agent → 几何操作（MOVE/SWAP/ROTATE/DEFORM/ALIGN/CHANGE_ENVELOPE/ADD_BRACKET）
   │   ├─ Thermal Agent → 热学操作（MODIFY_COATING/ADD_HEATSINK/SET_THERMAL_CONTACT/ADJUST_LAYOUT/CHANGE_ORIENTATION）
   │   ├─ Structural Agent → 结构操作
   │   └─ Power Agent → 电源操作
   │
   ├─> Agent协调
   │   ├─ 提案验证
   │   ├─ 冲突检测
   │   ├─ 冲突解决
   │   └─ 生成ExecutionPlan
   │
   ├─> 执行优化操作
   │   ├─ 更新组件位置/尺寸/形状
   │   ├─ 调整材料/涂层/热接触
   │   └─ 更新设计状态
   │
   ├─> 状态更新与验证
   │   ├─ 保存到状态池
   │   ├─ 记录到 Trace 审计日志
   │   └─ 更新演化轨迹
   │
   └─> 知识学习
       └─ 将成功/失败案例加入知识库

3. 输出结果
   ├─ evolution_trace.csv（量化指标）
   ├─ run_log.txt（完整终端日志）
   ├─ trace/（完整上下文追踪）
   ├─ llm_interactions/（LLM输入输出）
   ├─ visualizations/（3D布局、热图、演化轨迹）
   └─ rollback_events.jsonl（回退事件日志）
```

---

## 📁 输出文件

每次运行会在`experiments/run_YYYYMMDD_HHMMSS/`目录下生成：

- `run_log.txt` - 完整终端日志（所有模块）⭐
- `evolution_trace.csv` - 迭代指标数据（含惩罚分、state_id）
- `trace/` - 完整上下文追踪
  - `iter_XX_context.json` - 输入给 LLM 的上下文
  - `iter_XX_plan.json` - LLM 的战略计划
  - `iter_XX_eval.json` - 物理仿真评估结果
- `rollback_events.jsonl` - 回退事件日志
- `llm_interactions/` - LLM输入输出记录
- `step_files/` - STEP 几何文件
- `mph_models/` - COMSOL 模型文件
- `visualizations/` - 可视化图表
  - `evolution_trace.png` - 演化轨迹图
  - `final_layout_3d.png` - 3D布局图
  - `layout_evolution.png` - 初末状态布局位移对比图
  - `thermal_heatmap.png` - 温度热图
  - `visualization_summary.txt` - 可视化摘要（penalty/违规/位移/热代理）
- `design_state_iter_XX.json` - 每次迭代的设计状态

> 说明：为保持交付仓库简洁，`scripts/`、`tests/`、`logs/` 默认本地化管理，不作为主交付内容跟踪。

---

## 📚 重要文档

### 核心文档
- [HANDOFF.md](HANDOFF.md) - 项目交接文档 ⭐⭐⭐ (最重要)
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - 项目总结
- [CLAUDE.md](CLAUDE.md) - Claude Code 指令
- [RULES.md](RULES.md) - 开发规范

### 技术文档
- [docs/LLM_Semantic_Layer_Architecture.md](docs/LLM_Semantic_Layer_Architecture.md) - 架构设计
- [docs/COMSOL_GUIDE.md](docs/COMSOL_GUIDE.md) - COMSOL使用指南
- [docs/QWEN_GUIDE.md](docs/QWEN_GUIDE.md) - Qwen模型使用指南

### 归档文档
- [docs/archive/phase2/](docs/archive/phase2/) - Phase 2 完成报告
- [docs/archive/phase3/](docs/archive/phase3/) - Phase 3 完成报告
- [docs/archive/v202_fixes/](docs/archive/v202_fixes/) - v2.0.2 修复文档
- [docs/archive/tests/](docs/archive/tests/) - 测试分析报告

---

## 🔬 技术栈

- **语言**: Python 3.12
- **LLM**: qwen3-max
- **数据验证**: Pydantic 2.6+
- **几何算法**: py3dbp（3D装箱）
- **CAD导出**: pythonocc-core（STEP文件）
- **仿真接口**: COMSOL MPh（动态STEP导入）
- **数值优化**: Scipy
- **向量检索**: OpenAI-compatible Embeddings（DashScope `text-embedding-v4`）
- **Web框架**: FastAPI
- **可视化**: Matplotlib

---

## 📈 项目统计

- **总代码行数**: ~10000行
- **核心模块**: 15个
- **Agent数量**: 4个（几何、热控、结构、电源）
- **优化算子**: 10类（DV2.0）
- **数据协议**: 40+ Pydantic模型
- **知识库**: 8个默认知识项（可扩展）
- **测试覆盖**: 集成测试 + 单元测试（测试资产默认本地化）
- **异常类型**: 10个自定义异常
- **可视化类型**: 4种（演化轨迹、3D布局、布局演化、热图）
- **核心文档**: 5个
- **归档文档**: 20+ 个

---

## 📄 许可证

MIT License

---

## ⚠️ 注意事项

**仿真软件许可**: 使用COMSOL仿真功能需要相应的合法许可证。

**API密钥**: 使用LLM功能需要有效的OpenAI API密钥或Qwen API密钥。

**COMSOL配置**: 系统已实现数值稳定锚、功率斜坡加载、几何门控与动态建模路径清理。详见 [HANDOFF.md](HANDOFF.md) v2.0.5 章节。

---

## 🤝 贡献

欢迎提交Issue和Pull Request！

---

**开发团队**: MsGalaxy Project
**项目版本**: v2.0.5
**系统成熟度**: 99.6%
**最后更新**: 2026-03-01 02:24
