# 卫星设计优化系统 - LLM语义层架构设计方案

## 1. 架构概述

基于对工程领域LLM应用的深入研究，我们提出一个**三层神经符号协同架构**，将LLM的语义理解能力与工程仿真工具的精确计算能力深度融合。

### 1.1 核心设计理念

**Neuro-Symbolic Collaboration（神经符号协同）**
- **神经层（Neural Layer）**: LLM负责战略决策、拓扑推理、约束理解
- **符号层（Symbolic Layer）**: 数值求解器负责精确优化、物理仿真
- **协同机制**: 通过结构化协议实现双向信息流动

### 1.2 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    战略层 (Strategic Layer)                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Meta-Reasoner (元推理器)                             │   │
│  │  - 多学科协调决策                                      │   │
│  │  - 设计空间探索策略                                    │   │
│  │  - 约束冲突解决方案                                    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↓↑
┌─────────────────────────────────────────────────────────────┐
│                    战术层 (Tactical Layer)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Geometry │  │ Thermal  │  │Structural│  │  Power   │   │
│  │  Agent   │  │  Agent   │  │  Agent   │  │  Agent   │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       ↓              ↓              ↓              ↓        │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         Knowledge Retrieval Layer (RAG)              │   │
│  │  - 工程规范库  - 历史案例库  - 物理公式库            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↓↑
┌─────────────────────────────────────────────────────────────┐
│                    执行层 (Execution Layer)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  Layout  │  │  MATLAB  │  │  COMSOL  │  │  Scipy   │   │
│  │  Engine  │  │  Driver  │  │  Driver  │  │  Solver  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 战略层设计：Meta-Reasoner

### 2.1 核心职责

Meta-Reasoner作为顶层决策者，负责：
1. **多学科协调**: 平衡几何、热控、结构、电源等多个约束域
2. **探索策略制定**: 决定优化方向（局部搜索 vs 全局重构）
3. **冲突解决**: 当多个约束冲突时，提供权衡方案

### 2.2 输入输出协议

**输入: GlobalContextPack**
```python
class GlobalContextPack(BaseModel):
    """全局上下文包"""
    iteration: int
    design_state: DesignState  # 当前设计状态

    # 多学科指标
    geometry_metrics: GeometryMetrics
    thermal_metrics: ThermalMetrics
    structural_metrics: StructuralMetrics
    power_metrics: PowerMetrics

    # 约束违反情况
    violations: List[ViolationItem]

    # 历史轨迹
    history_summary: str  # 最近3次迭代的自然语言总结

    # 知识检索结果
    retrieved_knowledge: List[KnowledgeItem]
```

**输出: StrategicPlan**
```python
class StrategicPlan(BaseModel):
    """战略计划"""
    plan_id: str
    reasoning: str  # Chain-of-Thought推理过程

    # 优化策略
    strategy_type: Literal["local_search", "global_reconfig", "hybrid"]

    # 分配给各专业Agent的任务
    tasks: List[AgentTask]

    # 预期效果
    expected_improvements: Dict[str, float]

    # 风险评估
    risks: List[str]
```

### 2.3 Prompt Engineering策略

**System Prompt结构**:
```
你是卫星设计优化系统的首席架构师（Meta-Reasoner）。

【角色定位】
- 你不直接修改设计参数，而是制定优化策略并协调专业Agent
- 你需要平衡多个学科的约束，做出权衡决策
- 你的决策必须有明确的工程依据

【输入信息】
1. 当前设计状态（几何布局、仿真结果）
2. 约束违反情况（几何干涉、热超标、结构应力等）
3. 历史优化轨迹（避免重复失败的尝试）
4. 检索到的工程知识（相关规范、案例）

【输出要求】
1. reasoning: 详细说明你的推理过程（Chain-of-Thought）
   - 当前问题的根本原因是什么？
   - 为什么选择这个策略而不是其他策略？
   - 预期会产生什么连锁反应？

2. strategy_type: 选择优化策略
   - local_search: 局部微调（适用于接近可行解）
   - global_reconfig: 全局重构（适用于严重违反约束）
   - hybrid: 混合策略

3. tasks: 分配给各Agent的具体任务
   - 每个任务必须指定目标、约束、优先级

【约束规则】
- 不得违反物理定律（如质心必须在支撑范围内）
- 优先保证安全裕度（不仅满足约束，还要留有余量）
- 考虑制造可行性（避免过于复杂的结构）
```

**Few-Shot Examples**:
提供3-5个高质量的示例，展示：
- 如何分析复杂的多约束冲突
- 如何在热控和结构之间做权衡
- 如何从历史失败中学习

---

## 3. 战术层设计：Multi-Agent System

### 3.1 Agent架构

每个专业Agent负责一个学科领域，具有：
1. **专业知识**: 通过RAG检索相关工程规范和案例
2. **局部决策**: 在Meta-Reasoner的战略指导下，做出具体的参数调整建议
3. **约束感知**: 理解本学科的约束，并预测对其他学科的影响

### 3.2 Geometry Agent

**职责**:
- 布局优化（组件位置、朝向）
- 干涉检测与避让
- 质心与转动惯量控制

**输入: GeometryTask**
```python
class GeometryTask(BaseModel):
    task_id: str
    objective: str  # "Resolve clash between Battery and Rib"
    constraints: List[str]  # ["Keep CoM within [0,0,0]±10mm", "Maintain clearance ≥3mm"]
    priority: int

    # 当前状态
    current_layout: PackingResult
    violations: List[ViolationItem]
```

**输出: GeometryProposal**
```python
class GeometryProposal(BaseModel):
    proposal_id: str
    reasoning: str

    # 具体操作
    actions: List[GeometryAction]  # MOVE, ROTATE, SWAP

    # 预测影响
    predicted_metrics: GeometryMetrics
    side_effects: List[str]  # "Moving Battery may affect thermal distribution"
```

**Prompt Engineering**:
```
你是几何布局专家（Geometry Agent）。

【专业能力】
- 3D空间推理（AABB碰撞检测、间隙计算）
- 质心与转动惯量计算
- 布局优化算法（装箱、墙面安装）

【任务】
Meta-Reasoner分配给你的任务: {task.objective}
约束条件: {task.constraints}

【可用操作】
1. MOVE(component_id, axis, range): 沿指定轴移动组件
2. ROTATE(component_id, axis, angle_range): 旋转组件
3. SWAP(comp_a, comp_b): 交换两个组件的位置

【输出格式】
{
  "reasoning": "分析当前干涉的几何原因，为什么选择这个操作",
  "actions": [
    {
      "op_type": "MOVE",
      "component_id": "Battery_01",
      "axis": "X",
      "search_range": [-5.0, 0.0],
      "rationale": "向-X移动可增加与Rib的间隙"
    }
  ],
  "predicted_metrics": {
    "min_clearance": 5.2,
    "com_offset": [0.3, -0.1, 0.0]
  },
  "side_effects": ["移动Battery可能影响热分布，需Thermal Agent复核"]
}
```

### 3.3 Thermal Agent

**职责**:
- 热分析与优化
- 散热路径设计
- 温度约束验证

**关键特性**:
- 调用MATLAB/COMSOL进行高精度热仿真
- 理解热传导、对流、辐射的物理机制
- 提出散热改进方案（增加散热器、调整布局）

### 3.4 Structural Agent

**职责**:
- 结构强度分析
- 振动与模态分析
- 质量优化

### 3.5 Power Agent

**职责**:
- 功率预算管理
- 电源线路优化
- 电磁兼容性检查

### 3.6 Agent协调机制

**通信协议**:
```python
class AgentMessage(BaseModel):
    from_agent: str
    to_agent: str
    message_type: Literal["proposal", "feedback", "alert"]
    content: Dict[str, Any]
```

**协调流程**:
1. Meta-Reasoner发布StrategicPlan
2. 各Agent并行生成Proposal
3. Meta-Reasoner收集所有Proposal，检查冲突
4. 如有冲突，要求相关Agent协商
5. 最终形成统一的OptimizationPlan

---

## 4. 知识检索层：RAG System

### 4.1 知识库构建

**知识源**:
1. **工程规范库**: GJB、ISO等标准文档
2. **历史案例库**: 过去成功/失败的设计案例
3. **物理公式库**: 热传导方程、结构力学公式
4. **专家经验库**: 启发式规则（如"高功耗组件应靠近散热面"）

**向量化策略**:
```python
class KnowledgeItem(BaseModel):
    item_id: str
    category: Literal["standard", "case", "formula", "heuristic"]
    title: str
    content: str
    embedding: List[float]  # OpenAI text-embedding-3-large
    metadata: Dict[str, Any]
```

### 4.2 检索策略

**Hybrid Retrieval**:
1. **语义检索**: 使用embedding相似度
2. **关键词检索**: 基于约束类型（thermal, structural等）
3. **图检索**: 基于组件关系图（哪些组件相邻）

**检索流程**:
```python
def retrieve_knowledge(context: GlobalContextPack, top_k: int = 5) -> List[KnowledgeItem]:
    # 1. 构建查询
    query = f"""
    Current violations: {context.violations}
    Design state: {context.design_state.to_summary()}
    """

    # 2. 语义检索
    semantic_results = vector_db.similarity_search(query, k=top_k*2)

    # 3. 关键词过滤
    violation_types = [v.violation_type for v in context.violations]
    filtered = [r for r in semantic_results if r.category in violation_types]

    # 4. 重排序（考虑新鲜度、权威性）
    ranked = rerank(filtered, context)

    return ranked[:top_k]
```

### 4.3 知识注入方式

**In-Context Learning**:
```
【相关工程知识】
1. [GJB 5236-2004] 卫星热控设计规范
   "高功耗组件（>10W）应安装在±Y面，以利用辐射散热"

2. [历史案例] 某卫星电池过热问题
   "电池组初始布局在+Z面，导致温度超标15°C。
    解决方案：移至-Y面并增加热管，温度降至安全范围。"

3. [启发式规则] 质心控制
   "当质心偏移>5mm时，优先移动质量最大的组件（通常是电池）"
```

---

## 5. 执行层设计：Tool Integration

### 5.1 工具调用协议

**统一接口**:
```python
class ToolCall(BaseModel):
    tool_name: str  # "layout_engine", "matlab_sim", "comsol_sim", "scipy_solver"
    parameters: Dict[str, Any]
    timeout: int = 300  # 秒
```

**工具注册表**:
```python
TOOL_REGISTRY = {
    "layout_engine": {
        "description": "3D布局优化引擎，支持装箱、墙面安装、干涉检测",
        "input_schema": LayoutRequest,
        "output_schema": PackingResult,
        "executor": LayoutEngine
    },
    "matlab_sim": {
        "description": "MATLAB热仿真，调用自定义.m脚本",
        "input_schema": SimulationRequest,
        "output_schema": SimulationResult,
        "executor": MatlabDriver
    },
    # ...
}
```

### 5.2 LLM工具调用流程

**Function Calling**:
```python
# Agent生成工具调用请求
response = openai.ChatCompletion.create(
    model="gpt-4-turbo",
    messages=[...],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_thermal_simulation",
                "description": "运行MATLAB热仿真，返回温度分布",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "design_state": {"type": "object"},
                        "simulation_type": {"type": "string", "enum": ["steady", "transient"]}
                    }
                }
            }
        }
    ]
)

# 执行工具调用
if response.tool_calls:
    for tool_call in response.tool_calls:
        result = execute_tool(tool_call)
        # 将结果反馈给LLM
```

### 5.3 结果解释与验证

**结果解释器**:
```python
class ResultInterpreter:
    def interpret_simulation_result(self, result: SimulationResult) -> str:
        """将数值结果转换为自然语言描述"""
        summary = f"仿真完成。最高温度: {result.metrics['max_temp']:.1f}°C"

        if result.metrics['max_temp'] > 60:
            summary += f"（超标{result.metrics['max_temp']-60:.1f}°C）"
            hotspots = [d for d in result.details if d['temp'] > 60]
            summary += f"\n热点组件: {', '.join([h['component'] for h in hotspots])}"

        return summary
```

---

## 6. 约束管理系统

### 6.1 约束表示

**多层次约束**:
```python
class Constraint(BaseModel):
    constraint_id: str
    category: Literal["geometry", "thermal", "structural", "power", "mission"]
    priority: int  # 1=must, 2=should, 3=nice-to-have

    # 形式化表示
    expression: str  # "max_temp <= 60"

    # 自然语言描述
    description: str  # "所有组件温度不得超过60°C"

    # 违反后果
    consequence: str  # "可能导致电池寿命缩短"
```

### 6.2 约束冲突检测

**冲突类型**:
1. **直接冲突**: 两个约束无法同时满足
   - 例: "Battery必须在+Z面" vs "Battery温度<50°C（+Z面温度高）"

2. **间接冲突**: 满足A会导致违反B
   - 例: 移动Battery解决干涉 → 质心偏移超标

**冲突解决策略**:
```python
class ConflictResolution(BaseModel):
    conflict_id: str
    conflicting_constraints: List[str]

    resolution_type: Literal["prioritize", "compromise", "redesign"]

    # prioritize: 按优先级选择
    # compromise: 找到折中方案（如温度55°C，略超标但可接受）
    # redesign: 需要全局重构

    rationale: str
```

### 6.3 约束注入Prompt

**动态约束生成**:
```python
def generate_constraint_prompt(constraints: List[Constraint]) -> str:
    must_constraints = [c for c in constraints if c.priority == 1]
    should_constraints = [c for c in constraints if c.priority == 2]

    prompt = "【硬约束（必须满足）】\n"
    for c in must_constraints:
        prompt += f"- {c.description} ({c.expression})\n"

    prompt += "\n【软约束（尽量满足）】\n"
    for c in should_constraints:
        prompt += f"- {c.description}\n"

    return prompt
```

---

## 7. 可追溯性与可解释性

### 7.1 完整审计链

**日志结构**:
```
experiments/run_20260215_143022/
├── strategic_decisions/
│   ├── iter_001_meta_reasoning.json      # Meta-Reasoner决策
│   ├── iter_001_strategic_plan.json
│   └── ...
├── agent_proposals/
│   ├── iter_001_geometry_proposal.json   # 各Agent提案
│   ├── iter_001_thermal_proposal.json
│   └── ...
├── tool_executions/
│   ├── iter_001_layout_call.json         # 工具调用记录
│   ├── iter_001_matlab_call.json
│   └── ...
├── knowledge_retrieval/
│   ├── iter_001_retrieved_items.json     # RAG检索结果
│   └── ...
├── evolution_trace.csv                   # 量化指标演化
└── final_report.md                       # 自动生成报告
```

### 7.2 可视化Dashboard

**实时监控**:
1. **设计空间轨迹**: 3D可视化组件位置演化
2. **约束满足度**: 雷达图显示各约束的满足程度
3. **Agent活动**: 时间线显示各Agent的决策过程
4. **知识使用**: 哪些工程规范被引用

### 7.3 决策解释生成

**自动生成解释**:
```python
def generate_explanation(iteration: int) -> str:
    """为每次迭代生成人类可读的解释"""

    explanation = f"""
## 第{iteration}次迭代决策说明

### 问题诊断
{meta_reasoner.reasoning}

### 采取的策略
{strategic_plan.strategy_type}: {strategic_plan.description}

### 各专业Agent的贡献
- Geometry Agent: {geometry_proposal.reasoning}
- Thermal Agent: {thermal_proposal.reasoning}

### 工具调用结果
- MATLAB仿真: 最高温度从{prev_temp}°C降至{curr_temp}°C
- 布局优化: 最小间隙从{prev_clearance}mm增至{curr_clearance}mm

### 引用的工程知识
- [GJB 5236-2004] 热控设计规范第3.2节
- [历史案例] 某卫星电池布局优化案例

### 下一步计划
{next_strategic_plan.preview}
"""
    return explanation
```

---

## 8. 实现路线图

### Phase 1: 核心协议与基础设施 (1周)
- [ ] 定义GlobalContextPack, StrategicPlan等核心数据结构
- [ ] 实现Meta-Reasoner的基础框架
- [ ] 搭建实验日志系统

### Phase 2: 单Agent实现 (1周)
- [ ] 实现Geometry Agent（最复杂）
- [ ] 实现Thermal Agent
- [ ] 测试单Agent与工具的集成

### Phase 3: Multi-Agent协调 (1周)
- [ ] 实现Agent间通信协议
- [ ] 实现冲突检测与解决机制
- [ ] 端到端测试

### Phase 4: RAG系统 (1周)
- [ ] 构建工程知识库
- [ ] 实现混合检索策略
- [ ] 集成到Agent决策流程

### Phase 5: 优化与评估 (1周)
- [ ] 性能优化（缓存、并行）
- [ ] 在真实案例上评估
- [ ] 撰写技术文档

---

## 9. 创新点总结

### 9.1 学术创新
1. **三层神经符号架构**: 首次在卫星设计领域实现战略-战术-执行的分层决策
2. **Multi-Agent协同**: 多个专业Agent并行工作，模拟真实工程团队
3. **约束感知RAG**: 检索不仅基于语义，还考虑约束类型和冲突关系
4. **工具增强推理**: LLM不仅生成文本，还主动调用仿真工具验证假设

### 9.2 工程创新
1. **完整可追溯**: 每个决策都有明确的推理链和工程依据
2. **安全裕度设计**: 不仅满足约束，还主动留有安全余量
3. **知识积累**: 每次优化的经验自动沉淀为案例库
4. **人机协同**: 支持工程师介入关键决策点

### 9.3 可用性创新
1. **自然语言交互**: 工程师可用自然语言描述需求
2. **实时可视化**: 直观展示优化过程
3. **自动报告生成**: 输出符合工程规范的设计文档
4. **渐进式优化**: 支持从简化模型到高精度仿真的平滑过渡

---

## 10. 与现有系统的对比

| 维度 | 传统优化系统 | 简单LLM集成 | 本方案 |
|------|------------|------------|--------|
| 决策层次 | 单层（数值优化） | 单层（LLM） | 三层（战略-战术-执行） |
| 多学科协调 | 手动权重 | 无 | Multi-Agent自动协商 |
| 工程知识 | 硬编码 | 无 | RAG动态检索 |
| 可解释性 | 无 | 弱 | 完整审计链 |
| 约束处理 | 罚函数 | Prompt描述 | 多层次约束管理 |
| 工具集成 | 紧耦合 | 无 | Function Calling |

---

## 11. 下一步行动

1. **立即开始**: 实现核心数据协议（`optimization/protocol.py`）
2. **并行开发**: Meta-Reasoner和Geometry Agent可并行实现
3. **迭代测试**: 每完成一个模块立即在简化案例上测试
4. **文档先行**: 边实现边完善API文档

**预期成果**: 一个学术上严谨、工程上可用、创新性强的卫星设计优化系统，可发表高水平论文并实际应用于工程项目。
