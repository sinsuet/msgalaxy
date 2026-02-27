# MsGalaxy - 卫星设计优化系统

基于三层神经符号协同架构的智能卫星设计优化系统，整合了三维布局、COMSOL多物理场仿真和AI驱动的多学科优化决策。

> **项目状态**: ✅ 核心功能完成 | **系统成熟度**: 75% | **最后更新**: 2026-02-27

---

## 🎯 核心特性

### 🧠 三层神经符号协同架构
- **战略层**: Meta-Reasoner元推理器，负责多学科协调和战略决策
- **战术层**: Multi-Agent系统（几何、热控、结构、电源专家）
- **执行层**: 工具集成（COMSOL、MATLAB、简化物理引擎）

### 💡 创新亮点
- **学术创新**: 首次在卫星设计领域实现战略-战术-执行的分层决策
- **工程创新**: 完整审计链、安全裕度设计、知识自动积累
- **可用性创新**: 自然语言交互、实时可视化、自动报告生成

### 🚀 核心功能
- **智能布局**: 3D装箱算法（py3dbp）+ 多面墙面安装 + 层切割策略
- **真实仿真**: COMSOL MPh + MATLAB Engine API + 简化物理引擎
- **知识检索**: RAG系统（语义检索 + 关键词检索 + 图检索）
- **完整追溯**: 记录每个决策的推理链和工程依据

### 🔥 最新突破（v1.3.0）
- ✅ **COMSOL辐射问题解决**: 使用原生HeatFluxBoundary实现Stefan-Boltzmann辐射
- ✅ **端到端工作流验证**: BOM解析 → 几何布局 → COMSOL仿真 → 可视化生成
- ✅ **代码库清理**: 归档63个文件，保留29个核心文件，节省89%磁盘空间

---

## 📦 项目结构

```
msgalaxy/
├── core/                          # 核心基础设施
│   ├── protocol.py               # 统一数据协议 (Pydantic)
│   ├── logger.py                 # 实验日志系统
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
│   └── cad_export.py             # CAD导出 (STEP/IGES)
│
├── simulation/                    # 仿真驱动器
│   ├── base.py                   # 仿真驱动器基类
│   ├── comsol_driver.py          # COMSOL MPh集成 ⭐
│   ├── comsol_model_generator.py # 动态模型生成器
│   ├── matlab_driver.py          # MATLAB Engine API
│   └── physics_engine.py         # 简化物理引擎
│
├── optimization/                  # LLM语义优化层 ⭐⭐⭐
│   ├── protocol.py               # 优化协议定义
│   ├── meta_reasoner.py          # Meta-Reasoner (战略层)
│   ├── coordinator.py            # Agent协调器 (战术层)
│   ├── agents/                   # 专家Agent系统
│   │   ├── geometry_agent.py    # 几何专家
│   │   ├── thermal_agent.py     # 热控专家
│   │   ├── structural_agent.py  # 结构专家
│   │   └── power_agent.py       # 电源专家
│   ├── knowledge/                # 知识库系统
│   │   └── rag_system.py        # RAG混合检索
│   ├── multi_objective.py        # 多目标优化
│   └── parallel_optimizer.py     # 并行优化器
│
├── workflow/                      # 工作流编排
│   └── orchestrator.py           # 主编排器 ⭐
│
├── api/                           # API接口
│   ├── cli.py                    # 命令行接口
│   ├── server.py                 # FastAPI服务器
│   ├── client.py                 # Python客户端
│   └── websocket_client.py       # WebSocket客户端
│
├── config/                        # 配置文件
│   ├── system.yaml               # 系统配置
│   └── bom_example.json          # BOM示例
│
├── scripts/                       # 工具脚本 (3个核心脚本)
│   ├── create_complete_satellite_model.py  ⭐ 当前使用
│   └── clean_experiments.py
│
├── models/                        # COMSOL模型文件
│   └── satellite_thermal_heatflux.mph  ⭐ 当前使用 (5.1MB)
│
├── experiments/                   # 实验数据
│   └── run_YYYYMMDD_HHMMSS/      # 每次运行的实验目录
│
├── docs/                          # 文档 (15个核心文档)
│   ├── LLM_Semantic_Layer_Architecture.md  ⭐ 架构设计
│   ├── RADIATION_SOLUTION_SUMMARY.md       ⭐ 关键解决方案
│   ├── COMSOL_GUIDE.md
│   └── ...
│
├── tests/                         # 单元测试
├── archive/                       # 归档文件 (63个)
│   ├── scripts_old/              # 旧脚本
│   ├── models_debug/             # 调试模型
│   └── docs_archive/             # 旧文档
│
├── README.md                      # 本文档
├── PROJECT_SUMMARY.md             # 项目总结 ⭐
├── handoff.md                     # 项目交接文档 ⭐
├── TEST_WORKFLOW_ANALYSIS.md      # 测试分析 ⭐
├── CLEANUP_REPORT.md              # 清理报告
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

# 可选：安装MATLAB Engine（如果需要MATLAB仿真）
# cd "D:\Program Files\MATLAB\extern\engines\python"
# python setup.py install

# 可选：安装MPh（如果需要COMSOL仿真）
# pip install mph
```

> **Windows用户注意**: 如果遇到中文显示乱码问题，请参考 [编码问题解决方案](docs/ENCODING_FIX.md)

### 2. 配置系统

编辑 `config/system.yaml`:

```yaml
# OpenAI配置
openai:
  api_key: "your-api-key-here"  # 必填
  model: "qwen-plus"  # 或 gpt-4-turbo
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"  # Qwen API
  temperature: 0.7

# 仿真配置
simulation:
  backend: "comsol"  # simplified | matlab | comsol
  comsol_model: "e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph"

# 几何配置
geometry:
  envelope_dims: [300, 300, 400]  # mm
  clearance_mm: 3.0

# 优化配置
optimization:
  max_iterations: 20
  convergence_threshold: 0.01
```

### 3. 创建COMSOL模型

```bash
# 生成COMSOL模型（首次使用）
python scripts/create_complete_satellite_model.py

# 输出: models/satellite_thermal_heatflux.mph (5.1MB)
```

### 4. 运行测试

```bash
# 端到端工作流测试
python test_real_workflow.py

# 检查生成的可视化
ls experiments/run_*/visualizations/

# 运行单元测试
pytest tests/
```

> 详细的测试说明请参考 [测试指南](docs/TESTING_GUIDE.md)

### 5. 运行优化

```bash
# 使用CLI运行优化
python -m api.cli optimize --max-iter 5

# 查看实验列表
python -m api.cli list

# 查看实验详情
python -m api.cli show run_20260227_021304
```

---

## 📊 系统状态

### 模块成熟度

| 模块 | 状态 | 成熟度 | 备注 |
|------|------|--------|------|
| BOM解析 | ✅ | 95% | 稳定可靠 |
| 几何布局 | ✅ | 90% | 算法优秀 |
| COMSOL集成 | ⚠️ | 60% | 模型正确，求解器需调优 |
| 简化物理引擎 | ✅ | 80% | 适合快速测试 |
| Meta-Reasoner | ❓ | 50% | 未充分测试 |
| Multi-Agent | ❓ | 50% | 未充分测试 |
| 工作流编排 | ⚠️ | 65% | 核心逻辑正确，错误处理需改进 |
| 可视化 | ✅ | 85% | 图片生成正常 |
| API接口 | ✅ | 75% | 基本功能完整 |

**总体成熟度**: 75%

### 已知问题

#### 🔴 Critical (阻塞性)

1. **优化循环提前退出Bug** (P0)
   - 问题: 仿真失败时返回空metrics，不触发违规检查
   - 影响: LLM优化循环从未启动
   - 预计工作量: 2小时

2. **COMSOL求解器收敛失败** (P0)
   - 问题: T⁴非线性导致牛顿迭代不收敛
   - 影响: 无法获得真实温度分布
   - 预计工作量: 4-8小时（需COMSOL GUI调试）

详细问题分析请参考: [TEST_WORKFLOW_ANALYSIS.md](TEST_WORKFLOW_ANALYSIS.md)

---

## 🔧 工作流程

### 完整优化循环

```
1. 初始化设计
   └─> BOM解析 → 3D布局生成（装箱算法）

2. 迭代优化循环（最多20次）
   ├─> 物理仿真评估
   │   ├─ 几何分析（间隙、质心、转动惯量）
   │   ├─ 热分析（COMSOL/MATLAB/简化）
   │   ├─ 结构分析（应力、频率）
   │   └─ 电源分析（功耗、压降）
   │
   ├─> 约束检查
   │   └─ 生成违反项列表（ViolationItem）
   │
   ├─> RAG知识检索
   │   ├─ 语义检索（embedding相似度）
   │   ├─ 关键词检索（约束类型匹配）
   │   └─ 返回top-5相关知识
   │
   ├─> Meta-Reasoner战略决策
   │   ├─ 输入：GlobalContextPack（当前状态+违反+知识）
   │   ├─ 推理：Chain-of-Thought分析
   │   └─ 输出：StrategicPlan（策略+任务分配）
   │
   ├─> Multi-Agent战术执行
   │   ├─ Geometry Agent → GeometryProposal
   │   ├─ Thermal Agent → ThermalProposal
   │   ├─ Structural Agent → StructuralProposal
   │   └─ Power Agent → PowerProposal
   │
   ├─> Agent协调
   │   ├─ 提案验证
   │   ├─ 冲突检测
   │   ├─ 冲突解决
   │   └─ 生成OptimizationPlan
   │
   ├─> 执行优化操作
   │   ├─ 几何操作（MOVE/ROTATE/SWAP）
   │   ├─ 热控操作（ADJUST_LAYOUT/ADD_HEATSINK）
   │   ├─ 结构操作（REINFORCE/REDUCE_MASS）
   │   └─ 电源操作（OPTIMIZE_ROUTING）
   │
   ├─> 状态更新与验证
   │   ├─ 重新仿真
   │   ├─ 对比新旧指标
   │   └─ 决定接受/回滚
   │
   └─> 知识学习
       └─ 将成功/失败案例加入知识库

3. 输出结果
   ├─ evolution_trace.csv（量化指标）
   ├─ llm_interactions/（LLM输入输出）
   ├─ visualizations/（3D布局、热图、演化轨迹）
   └─ report.md（总结报告）
```

---

## 📁 输出文件

每次运行会在`experiments/run_YYYYMMDD_HHMMSS/`目录下生成：

- `evolution_trace.csv` - 迭代指标数据
- `llm_interactions/` - LLM输入输出记录
- `visualizations/` - 可视化图表
  - `evolution_trace.png` - 演化轨迹图
  - `final_layout_3d.png` - 3D布局图
  - `thermal_heatmap.png` - 温度热图
- `design_state_iter_XX.json` - 每次迭代的设计状态
- `report.md` - 优化总结报告

---

## 📚 重要文档

### 核心文档
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - 项目总结 ⭐
- [handoff.md](handoff.md) - 项目交接文档 ⭐
- [QUICKSTART.md](QUICKSTART.md) - 快速开始指南

### 技术文档
- [docs/LLM_Semantic_Layer_Architecture.md](docs/LLM_Semantic_Layer_Architecture.md) - 架构设计
- [docs/RADIATION_SOLUTION_SUMMARY.md](docs/RADIATION_SOLUTION_SUMMARY.md) - 辐射问题解决方案 ⭐
- [docs/COMSOL_GUIDE.md](docs/COMSOL_GUIDE.md) - COMSOL使用指南
- [docs/QWEN_GUIDE.md](docs/QWEN_GUIDE.md) - Qwen模型使用指南

### 测试报告
- [TEST_WORKFLOW_ANALYSIS.md](TEST_WORKFLOW_ANALYSIS.md) - 工作流测试分析 ⭐
- [CLEANUP_REPORT.md](CLEANUP_REPORT.md) - 代码清理报告
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - 测试指南

### API文档
- [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md) - API文档
- [docs/WEBSOCKET_IMPLEMENTATION.md](docs/WEBSOCKET_IMPLEMENTATION.md) - WebSocket实现

---

## 🛠️ 开发指南

### 添加新的仿真驱动器

1. 在`simulation/`目录下创建新的驱动器文件
2. 继承`simulation/base.py`中的基类
3. 实现`run_simulation()`方法
4. 在配置中添加新的仿真类型

### 添加新的优化算子

1. 在`optimization/operators.py`中定义新算子
2. 在`core/protocol.py`的`OperatorType`枚举中添加
3. 在LLM的System Prompt中说明新算子的用法

### 代码维护

- 定期清理实验数据: `python scripts/clean_experiments.py --days 7`
- 归档不再使用的脚本到 `archive/` 目录
- 更新文档以反映最新的项目状态

---

## 🧪 测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/

# 运行集成测试
pytest tests/integration/

# 端到端测试
python test_real_workflow.py

# 生成覆盖率报告
pytest --cov=. --cov-report=html
```

---

## 🔬 技术栈

- **语言**: Python 3.12
- **LLM**: Qwen-Plus / GPT-4-Turbo
- **数据验证**: Pydantic 2.6+
- **几何算法**: py3dbp（3D装箱）
- **仿真接口**: COMSOL MPh, MATLAB Engine API
- **数值优化**: Scipy
- **向量检索**: OpenAI Embeddings
- **Web框架**: FastAPI
- **可视化**: Matplotlib

---

## 📈 项目统计

- **总代码行数**: ~8000行
- **核心模块**: 12个
- **Agent数量**: 4个（几何、热控、结构、电源）
- **数据协议**: 30+ Pydantic模型
- **知识库**: 8个默认知识项（可扩展）
- **测试覆盖**: 集成测试 + 单元测试
- **异常类型**: 10个自定义异常
- **可视化类型**: 3种（演化轨迹、3D布局、热图）
- **文档数量**: 15个核心文档 + 3个主要报告
- **归档文件**: 63个（已清理）

---

## 📄 许可证

MIT License

---

## ⚠️ 注意事项

**仿真软件许可**: 使用MATLAB和COMSOL仿真功能需要相应的合法许可证。简化物理引擎可以在没有这些软件的情况下运行。

**API密钥**: 使用LLM功能需要有效的OpenAI API密钥或Qwen API密钥。

**COMSOL求解器**: 当前模型使用T⁴非线性辐射公式，求解器可能无法收敛。这是已知问题，需要在COMSOL GUI中调整求解器设置。详见 [TEST_WORKFLOW_ANALYSIS.md](TEST_WORKFLOW_ANALYSIS.md)。

---

## 🤝 贡献

欢迎提交Issue和Pull Request！

---

**开发团队**: MsGalaxy Project
**项目版本**: v1.3.0
**系统成熟度**: 75%
**最后更新**: 2026-02-27
