# 项目结构

```
msgalaxy/
├── api/                       # API接口
│   └── cli.py                # 命令行接口
├── config/                    # 配置文件
│   └── system.yaml           # 系统配置
├── core/                      # 核心模块
│   ├── protocol.py           # 数据协议
│   ├── logger.py             # 日志系统
│   └── exceptions.py         # 异常定义
├── geometry/                  # 几何模块
│   ├── schema.py             # 数据结构
│   ├── keepout.py            # AABB算法
│   ├── packing.py            # 3D装箱
│   └── layout_engine.py      # 布局引擎
├── simulation/                # 仿真模块
│   ├── base.py               # 基类
│   ├── matlab_driver.py      # MATLAB接口
│   ├── comsol_driver.py      # COMSOL接口
│   └── physics_engine.py     # 简化物理
├── optimization/              # 优化模块（LLM语义层）
│   ├── protocol.py           # 优化协议
│   ├── meta_reasoner.py      # Meta-Reasoner（战略层）
│   ├── agents/               # Multi-Agent系统（战术层）
│   │   ├── geometry_agent.py
│   │   ├── thermal_agent.py
│   │   ├── structural_agent.py
│   │   └── power_agent.py
│   ├── knowledge/            # 知识检索
│   │   └── rag_system.py     # RAG系统
│   └── coordinator.py        # Agent协调器
├── workflow/                  # 工作流模块
│   └── orchestrator.py       # 主编排器
├── docs/                      # 文档
│   └── LLM_Semantic_Layer_Architecture.md
├── papers/                    # 参考论文
├── tests/                     # 测试
├── README.md                  # 项目说明
├── PROJECT_SUMMARY.md         # 项目总结
├── requirements.txt           # Python依赖
└── test_integration.py        # 集成测试
```

## 快速开始

```bash
# 1. 安装依赖
conda activate msgalaxy
pip install -r requirements.txt

# 2. 配置API key
# 编辑 config/system.yaml

# 3. 运行测试
python test_integration.py

# 4. 运行优化
python -m api.cli optimize
```
