# MsGalaxy - 卫星设计优化系统

基于三层神经符号协同架构的智能卫星设计优化系统，整合了三维布局、真实仿真和AI驱动的多学科优化决策。

> **项目状态**: ✅ 核心功能完成 | 📅 最后更新: 2026-02-15

## 核心特性

### 🧠 三层神经符号协同架构
- **战略层**: Meta-Reasoner元推理器，负责多学科协调和战略决策
- **战术层**: Multi-Agent系统（几何、热控、结构、电源专家）
- **执行层**: 工具集成（MATLAB、COMSOL、Scipy求解器）

### 🎯 创新亮点
- **学术创新**: 首次在卫星设计领域实现战略-战术-执行的分层决策
- **工程创新**: 完整审计链、安全裕度设计、知识自动积累
- **可用性创新**: 自然语言交互、实时可视化、自动报告生成

### 📐 核心功能
- **智能布局**: 3D装箱算法（py3dbp）+ 多面墙面安装 + 层切割策略
- **真实仿真**: MATLAB Engine API + COMSOL MPh + 简化物理引擎
- **知识检索**: RAG系统（语义检索 + 关键词检索 + 图检索）
- **完整追溯**: 记录每个决策的推理链和工程依据

## 项目结构

```
msgalaxy/
├── config/                    # 配置文件
│   └── system.yaml           # 系统配置模板
├── core/                      # 核心模块
│   ├── protocol.py           # 统一数据协议（Pydantic模型）
│   ├── logger.py             # 实验日志系统
│   └── exceptions.py         # 自定义异常
├── geometry/                  # 几何模块
│   ├── schema.py             # 几何数据结构（AABB、Part）
│   ├── keepout.py            # AABB六面减法算法
│   ├── packing.py            # 3D装箱优化（py3dbp集成）
│   └── layout_engine.py      # 布局引擎统一接口
├── simulation/                # 仿真模块
│   ├── base.py               # 仿真驱动器基类
│   ├── matlab_driver.py      # MATLAB Engine API集成
│   ├── comsol_driver.py      # COMSOL MPh集成
│   └── physics_engine.py     # 简化物理引擎
├── optimization/              # 优化模块（LLM语义层）
│   ├── protocol.py           # 优化协议（战略层、战术层数据结构）
│   ├── meta_reasoner.py      # Meta-Reasoner（战略层）
│   ├── agents/               # Multi-Agent系统（战术层）
│   │   ├── geometry_agent.py # 几何专家
│   │   ├── thermal_agent.py  # 热控专家
│   │   ├── structural_agent.py # 结构专家
│   │   └── power_agent.py    # 电源专家
│   ├── knowledge/            # 知识检索
│   │   └── rag_system.py     # RAG系统（混合检索）
│   └── coordinator.py        # Agent协调器
├── workflow/                  # 工作流模块
│   └── orchestrator.py       # 主编排器
├── api/                       # API接口
│   └── cli.py                # 命令行接口
├── docs/                      # 文档
│   ├── LLM_Semantic_Layer_Architecture.md  # LLM语义层架构设计
│   ├── QWEN_GUIDE.md         # Qwen使用指南
│   ├── QWEN_TEST_REPORT.md   # Qwen测试报告
│   ├── ENCODING_FIX.md       # 编码问题解决方案
│   ├── ENCODING_FIX_REPORT.md # 编码修复报告
│   ├── TEST_REPORT.md        # 测试报告
│   └── STATUS.md             # 项目状态
├── papers/                    # 参考论文
├── tests/                     # 测试套件
├── tmp_files/                 # 临时测试文件
│   ├── test_encoding.py      # 编码测试
│   └── run_qwen_test.bat     # Qwen测试批处理
├── test_integration.py        # 集成测试
├── test_geometry.py           # 几何模块测试
├── test_qwen.py              # Qwen API测试
├── test_simulation.py         # 仿真模块测试
├── .gitignore                # Git忽略文件
├── README.md                  # 本文档
├── PROJECT_SUMMARY.md         # 项目总结
├── STRUCTURE.md               # 结构说明
└── requirements.txt           # Python依赖
```

## 快速开始

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
  model: "gpt-4-turbo"
  temperature: 0.7

# 仿真配置
simulation:
  backend: "simplified"  # simplified | matlab | comsol
  matlab_path: "D:/Program Files/MATLAB"
  comsol_path: "D:/Program Files/COMSOL63"

# 几何配置
geometry:
  envelope_dims: [300, 300, 400]  # mm
  clearance_mm: 3.0

# 优化配置
optimization:
  max_iterations: 20
  convergence_threshold: 0.01
```

### 3. 运行测试

```bash
# 运行集成测试（不需要API key）
python test_integration.py

# 几何模块测试
python test_geometry.py

# 仿真模块测试
python test_simulation.py

# 或使用测试运行器运行所有测试
python run_tests.py
```

> 详细的测试说明请参考 [测试指南](docs/TESTING_GUIDE.md)

### 4. 运行优化

```bash
# 使用CLI运行优化
python -m api.cli optimize

# 使用自定义配置
python -m api.cli optimize --config my_config.yaml --max-iter 30

# 查看实验列表
python -m api.cli list

# 查看实验详情
python -m api.cli show run_20260215_143022
```

## 配置说明

主配置文件：`config/system.yaml`

```yaml
# 几何配置
geometry:
  envelope:
    auto_envelope: true
    fill_ratio: 0.30
  components:
    - id: "battery_01"
      dims_mm: [200, 150, 100]
      mass_kg: 5.0
      power_w: 50.0

# 仿真配置
simulation:
  type: "SIMPLIFIED"  # MATLAB | COMSOL | SIMPLIFIED
  constraints:
    max_temp_c: 50.0
    min_clearance_mm: 3.0

# 优化配置
optimization:
  max_iterations: 20
  allowed_operators: ["MOVE", "ROTATE"]

# OpenAI配置
openai:
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4"
```

## 工作流程

### 完整优化循环

```
1. 初始化设计
   └─> 3D布局生成（装箱算法）

2. 迭代优化循环（最多20次）
   ├─> 物理仿真评估
   │   ├─ 几何分析（间隙、质心、转动惯量）
   │   ├─ 热分析（MATLAB/COMSOL/简化）
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
   ├─ design_dashboard.png（可视化）
   └─ report.md（总结报告）
```

## 输出文件

每次运行会在`experiments/run_YYYYMMDD_HHMMSS/`目录下生成：

- `evolution_trace.csv` - 迭代指标数据
- `llm_interactions/` - LLM输入输出记录
- `visualizations/` - 可视化图表
- `design_state_iter_XX.json` - 每次迭代的设计状态
- `report.md` - 优化总结报告

## 开发指南

### 添加新的仿真驱动器

1. 在`simulation/`目录下创建新的驱动器文件
2. 继承`simulation/base.py`中的基类
3. 实现`run_simulation()`方法
4. 在配置中添加新的仿真类型

### 添加新的优化算子

1. 在`optimization/operators.py`中定义新算子
2. 在`core/protocol.py`的`OperatorType`枚举中添加
3. 在LLM的System Prompt中说明新算子的用法

## 测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/

# 运行集成测试
pytest tests/integration/

# 生成覆盖率报告
pytest --cov=. --cov-report=html
```

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！

## 相关文档

- 📖 [项目总结](PROJECT_SUMMARY.md) - 完整的项目实现总结
- 📖 [结构说明](STRUCTURE.md) - 项目结构快速参考
- 📖 [文件组织规范](docs/FILE_ORGANIZATION.md) - 文件放置和命名规范
- 📖 [测试指南](docs/TESTING_GUIDE.md) - 测试运行完整指南
- 📖 [测试状态](docs/TEST_STATUS.md) - 当前测试状态
- 📖 [LLM语义层架构](docs/LLM_Semantic_Layer_Architecture.md) - 详细的架构设计文档
- 📖 [Qwen使用指南](docs/QWEN_GUIDE.md) - 使用通义千问进行测试
- 📖 [Qwen测试报告](docs/QWEN_TEST_REPORT.md) - Qwen集成测试结果
- 📖 [编码问题解决方案](docs/ENCODING_FIX.md) - Windows控制台中文显示修复
- 📖 [编码修复报告](docs/ENCODING_FIX_REPORT.md) - 编码问题修复详情
- 📖 [测试报告](docs/TEST_REPORT.md) - 系统测试报告
- 📖 [项目状态](docs/STATUS.md) - 当前项目状态

## 技术栈

- **语言**: Python 3.12
- **LLM**: OpenAI GPT-4-turbo
- **数据验证**: Pydantic 2.6+
- **几何算法**: py3dbp (3D装箱)
- **仿真接口**: MATLAB Engine API, COMSOL MPh
- **数值优化**: Scipy
- **向量检索**: OpenAI Embeddings
- **可视化**: Matplotlib

## 许可证

MIT License

## 注意事项

⚠️ **仿真软件许可**: 使用MATLAB和COMSOL仿真功能需要相应的合法许可证。简化物理引擎可以在没有这些软件的情况下运行。

⚠️ **API密钥**: 使用LLM功能需要有效的OpenAI API密钥。

---

**开发团队**: MsGalaxy Project
**最后更新**: 2026-02-15
