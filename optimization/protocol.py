"""
优化模块核心数据协议

定义LLM语义层的输入输出数据结构，实现三层架构的通信协议。
"""

from typing import List, Dict, Any, Optional, Literal, Tuple
from pydantic import BaseModel, Field
from datetime import datetime
import json

# ============================================================================
# 基础数据结构
# ============================================================================

class ViolationItem(BaseModel):
    """约束违反项"""
    violation_id: str
    violation_type: Literal["geometry", "thermal", "structural", "power", "mission"]
    severity: Literal["critical", "major", "minor"]
    description: str
    affected_components: List[str]
    metric_value: float
    threshold: float

    def to_natural_language(self) -> str:
        """转换为自然语言描述"""
        severity_map = {"critical": "严重", "major": "重要", "minor": "轻微"}
        return (f"[{severity_map[self.severity]}] {self.description} "
                f"(当前值: {self.metric_value:.2f}, 阈值: {self.threshold:.2f})")


class KnowledgeItem(BaseModel):
    """知识库条目"""
    item_id: str
    category: Literal["standard", "case", "formula", "heuristic"]
    title: str
    content: str
    relevance_score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_citation(self) -> str:
        """生成引用格式"""
        category_map = {
            "standard": "工程规范",
            "case": "历史案例",
            "formula": "物理公式",
            "heuristic": "启发式规则"
        }
        return f"[{category_map[self.category]}] {self.title}"


class Constraint(BaseModel):
    """约束定义"""
    constraint_id: str
    category: Literal["geometry", "thermal", "structural", "power", "mission"]
    priority: int = Field(..., ge=1, le=3, description="1=must, 2=should, 3=nice-to-have")
    expression: str  # 形式化表示，如 "max_temp <= 60"
    description: str  # 自然语言描述
    consequence: str  # 违反后果

    def is_satisfied(self, metrics: Dict[str, float]) -> bool:
        """检查约束是否满足（简化实现）"""
        # 实际应用中需要更复杂的表达式解析
        try:
            return eval(self.expression, {"__builtins__": {}}, metrics)
        except:
            return False


# ============================================================================
# 多学科指标
# ============================================================================

class GeometryMetrics(BaseModel):
    """几何指标"""
    min_clearance: float = Field(..., description="最小间隙 (mm)")
    com_offset: List[float] = Field(..., description="质心偏移 [x,y,z] (mm)")
    moment_of_inertia: List[float] = Field(..., description="转动惯量 [Ixx,Iyy,Izz] (kg·m²)")
    packing_efficiency: float = Field(..., description="装填率 (%)")
    num_collisions: int = Field(default=0, description="碰撞数量")


class ThermalMetrics(BaseModel):
    """热控指标"""
    max_temp: float = Field(..., description="最高温度 (°C)")
    min_temp: float = Field(..., description="最低温度 (°C)")
    avg_temp: float = Field(..., description="平均温度 (°C)")
    temp_gradient: float = Field(..., description="最大温度梯度 (°C/m)")
    hotspot_components: List[str] = Field(default_factory=list, description="热点组件")


class StructuralMetrics(BaseModel):
    """结构指标"""
    max_stress: float = Field(..., description="最大应力 (MPa)")
    max_displacement: float = Field(..., description="最大位移 (mm)")
    first_modal_freq: float = Field(..., description="一阶模态频率 (Hz)")
    safety_factor: float = Field(..., description="安全系数")


class PowerMetrics(BaseModel):
    """电源指标"""
    total_power: float = Field(..., description="总功耗 (W)")
    peak_power: float = Field(..., description="峰值功耗 (W)")
    power_margin: float = Field(..., description="功率裕度 (%)")
    voltage_drop: float = Field(..., description="最大压降 (V)")


# ============================================================================
# 战略层协议
# ============================================================================

class GlobalContextPack(BaseModel):
    """全局上下文包 - Meta-Reasoner的输入"""
    iteration: int
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 当前设计状态（引用core.protocol.DesignState）
    design_state_summary: str  # 简化的自然语言描述

    # 多学科指标
    geometry_metrics: GeometryMetrics
    thermal_metrics: ThermalMetrics
    structural_metrics: StructuralMetrics
    power_metrics: PowerMetrics

    # 约束违反情况
    violations: List[ViolationItem]

    # 历史轨迹（最近3次迭代的总结）
    history_summary: str

    # 知识检索结果
    retrieved_knowledge: List[KnowledgeItem] = Field(default_factory=list)

    def to_markdown_prompt(self) -> str:
        """转换为Markdown格式的LLM Prompt"""
        md = f"""# 卫星设计优化 - 第{self.iteration}次迭代

## 1. 当前设计状态
{self.design_state_summary}

## 2. 多学科性能指标

### 几何指标
- 最小间隙: {self.geometry_metrics.min_clearance:.2f} mm
- 质心偏移: [{', '.join(f'{x:.2f}' for x in self.geometry_metrics.com_offset)}] mm
- 装填率: {self.geometry_metrics.packing_efficiency:.1f}%
- 碰撞数量: {self.geometry_metrics.num_collisions}

### 热控指标
- 温度范围: {self.thermal_metrics.min_temp:.1f}°C ~ {self.thermal_metrics.max_temp:.1f}°C
- 平均温度: {self.thermal_metrics.avg_temp:.1f}°C
- 温度梯度: {self.thermal_metrics.temp_gradient:.2f}°C/m
"""
        if self.thermal_metrics.hotspot_components:
            md += f"- 热点组件: {', '.join(self.thermal_metrics.hotspot_components)}\n"

        md += f"""
### 结构指标
- 最大应力: {self.structural_metrics.max_stress:.1f} MPa
- 最大位移: {self.structural_metrics.max_displacement:.3f} mm
- 一阶频率: {self.structural_metrics.first_modal_freq:.1f} Hz
- 安全系数: {self.structural_metrics.safety_factor:.2f}

### 电源指标
- 总功耗: {self.power_metrics.total_power:.1f} W
- 峰值功耗: {self.power_metrics.peak_power:.1f} W
- 功率裕度: {self.power_metrics.power_margin:.1f}%

## 3. 约束违反情况
"""
        if self.violations:
            for v in self.violations:
                md += f"- {v.to_natural_language()}\n"
        else:
            md += "✓ 所有约束均已满足\n"

        md += f"\n## 4. 历史轨迹\n{self.history_summary}\n"

        if self.retrieved_knowledge:
            md += "\n## 5. 相关工程知识\n"
            for k in self.retrieved_knowledge:
                md += f"\n### {k.to_citation()}\n{k.content}\n"

        return md


class AgentTask(BaseModel):
    """分配给Agent的任务"""
    task_id: str
    agent_type: Literal["geometry", "thermal", "structural", "power"]
    objective: str  # 任务目标的自然语言描述
    constraints: List[str]  # 约束条件列表
    priority: int = Field(..., ge=1, le=5)
    context: Dict[str, Any] = Field(default_factory=dict)  # 额外上下文


class StrategicPlan(BaseModel):
    """战略计划 - Meta-Reasoner的输出"""
    plan_id: str
    iteration: int
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Chain-of-Thought推理过程
    reasoning: str = Field(..., description="详细的推理过程，包括问题诊断、策略选择依据")

    # 优化策略类型
    strategy_type: Literal["local_search", "global_reconfig", "hybrid"]
    strategy_description: str

    # 分配给各Agent的任务
    tasks: List[AgentTask]

    # 预期改进
    expected_improvements: Dict[str, float] = Field(
        default_factory=dict,
        description="预期的指标改进，如 {'max_temp': -5.0, 'min_clearance': 2.0}"
    )

    # 风险评估
    risks: List[str] = Field(default_factory=list)

    def to_summary(self) -> str:
        """生成简要总结"""
        return (f"[{self.strategy_type}] {self.strategy_description}\n"
                f"分配任务: {len(self.tasks)}个 | "
                f"预期改进: {len(self.expected_improvements)}项指标")


# ============================================================================
# 战术层协议
# ============================================================================

class GeometryAction(BaseModel):
    """几何操作"""
    action_id: str
    op_type: Literal["MOVE", "ROTATE", "SWAP", "REPACK"]
    component_id: str
    parameters: Dict[str, Any]  # 操作参数，如 {"axis": "X", "range": [-5, 0]}
    rationale: str  # 操作理由


class GeometryProposal(BaseModel):
    """几何Agent的提案"""
    proposal_id: str
    task_id: str  # 对应的任务ID
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 推理过程
    reasoning: str

    # 具体操作
    actions: List[GeometryAction]

    # 预测影响
    predicted_metrics: GeometryMetrics
    side_effects: List[str] = Field(
        default_factory=list,
        description="对其他学科的潜在影响"
    )

    # 置信度
    confidence: float = Field(..., ge=0.0, le=1.0)


class ThermalAction(BaseModel):
    """热控操作"""
    action_id: str
    op_type: Literal["ADJUST_LAYOUT", "ADD_HEATSINK", "MODIFY_COATING", "CHANGE_ORIENTATION"]
    target_components: List[str]
    parameters: Dict[str, Any]
    rationale: str


class ThermalProposal(BaseModel):
    """热控Agent的提案"""
    proposal_id: str
    task_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    reasoning: str
    actions: List[ThermalAction]
    predicted_metrics: ThermalMetrics
    side_effects: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


class StructuralAction(BaseModel):
    """结构操作"""
    action_id: str
    op_type: Literal["REINFORCE", "REDUCE_MASS", "ADJUST_STIFFNESS"]
    target_components: List[str]
    parameters: Dict[str, Any]
    rationale: str


class StructuralProposal(BaseModel):
    """结构Agent的提案"""
    proposal_id: str
    task_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    reasoning: str
    actions: List[StructuralAction]
    predicted_metrics: StructuralMetrics
    side_effects: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


class PowerAction(BaseModel):
    """电源操作"""
    action_id: str
    op_type: Literal["OPTIMIZE_ROUTING", "ADJUST_VOLTAGE", "LOAD_BALANCING"]
    target_components: List[str]
    parameters: Dict[str, Any]
    rationale: str


class PowerProposal(BaseModel):
    """电源Agent的提案"""
    proposal_id: str
    task_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    reasoning: str
    actions: List[PowerAction]
    predicted_metrics: PowerMetrics
    side_effects: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)


# ============================================================================
# Agent间通信协议
# ============================================================================

class AgentMessage(BaseModel):
    """Agent间消息"""
    message_id: str
    from_agent: str
    to_agent: str  # "broadcast"表示广播给所有Agent
    message_type: Literal["proposal", "feedback", "alert", "query"]
    content: Dict[str, Any]
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ConflictReport(BaseModel):
    """冲突报告"""
    conflict_id: str
    conflicting_proposals: List[str]  # Proposal IDs
    conflict_type: Literal["direct", "indirect", "resource"]
    description: str
    affected_metrics: List[str]


class ConflictResolution(BaseModel):
    """冲突解决方案"""
    conflict_id: str
    resolution_type: Literal["prioritize", "compromise", "redesign", "sequential"]
    selected_proposals: List[str]  # 选中的Proposal IDs
    modifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="对选中提案的修改"
    )
    rationale: str


# ============================================================================
# 执行层协议
# ============================================================================

class OptimizationPlan(BaseModel):
    """统一的优化执行计划"""
    plan_id: str
    iteration: int
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 来源
    strategic_plan_id: str
    selected_proposals: List[str]  # Proposal IDs

    # 执行序列（考虑依赖关系）
    execution_sequence: List[Dict[str, Any]]

    # 预期结果
    expected_metrics: Dict[str, Any]

    # 回滚策略
    rollback_enabled: bool = True
    checkpoint_state: Optional[str] = None


class ExecutionResult(BaseModel):
    """执行结果"""
    plan_id: str
    success: bool
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    # 实际结果
    actual_metrics: Dict[str, Any]

    # 与预期的对比
    prediction_errors: Dict[str, float] = Field(default_factory=dict)

    # 新的违反项
    new_violations: List[ViolationItem] = Field(default_factory=list)

    # 执行日志
    execution_log: List[str] = Field(default_factory=list)

    # 是否需要回滚
    needs_rollback: bool = False
    rollback_reason: Optional[str] = None


# ============================================================================
# 工具调用协议
# ============================================================================

class ToolCall(BaseModel):
    """工具调用请求"""
    tool_name: str
    parameters: Dict[str, Any]
    timeout: int = Field(default=300, description="超时时间（秒）")
    retry_on_failure: bool = True
    max_retries: int = 3


class ToolResult(BaseModel):
    """工具调用结果"""
    tool_name: str
    success: bool
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    execution_time: float  # 秒
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================================
# 辅助函数
# ============================================================================

def create_violation(
    violation_type: str,
    severity: str,
    description: str,
    affected_components: List[str],
    metric_value: float,
    threshold: float
) -> ViolationItem:
    """创建违反项的辅助函数"""
    violation_id = f"{violation_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    return ViolationItem(
        violation_id=violation_id,
        violation_type=violation_type,
        severity=severity,
        description=description,
        affected_components=affected_components,
        metric_value=metric_value,
        threshold=threshold
    )


def merge_metrics(
    base_metrics: Dict[str, Any],
    updates: Dict[str, Any]
) -> Dict[str, Any]:
    """合并指标更新"""
    merged = base_metrics.copy()
    merged.update(updates)
    return merged


if __name__ == "__main__":
    # 测试数据结构
    print("Testing optimization protocol...")

    # 创建示例违反项
    violation = create_violation(
        violation_type="thermal",
        severity="major",
        description="电池温度超标",
        affected_components=["Battery_01"],
        metric_value=65.5,
        threshold=60.0
    )
    print(f"Violation: {violation.to_natural_language()}")

    # 创建示例知识项
    knowledge = KnowledgeItem(
        item_id="K001",
        category="standard",
        title="GJB 5236-2004 卫星热控设计规范",
        content="高功耗组件（>10W）应安装在±Y面，以利用辐射散热"
    )
    print(f"Knowledge: {knowledge.to_citation()}")

    print("\n✓ Protocol definitions validated successfully!")
