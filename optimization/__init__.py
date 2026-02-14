"""
优化引擎模块

实现基于LLM的三层神经符号协同优化架构：
- 战略层: Meta-Reasoner（元推理器）
- 战术层: Multi-Agent System（多智能体系统）
- 执行层: Tool Integration（工具集成）
"""

from .protocol import (
    # 基础数据结构
    ViolationItem,
    KnowledgeItem,
    Constraint,
    # 多学科指标
    GeometryMetrics,
    ThermalMetrics,
    StructuralMetrics,
    PowerMetrics,
    # 战略层协议
    GlobalContextPack,
    AgentTask,
    StrategicPlan,
    # 战术层协议
    GeometryAction,
    GeometryProposal,
    ThermalAction,
    ThermalProposal,
    StructuralAction,
    StructuralProposal,
    PowerAction,
    PowerProposal,
    # Agent通信
    AgentMessage,
    ConflictReport,
    ConflictResolution,
    # 执行层协议
    OptimizationPlan,
    ExecutionResult,
    ToolCall,
    ToolResult,
)

__all__ = [
    # 基础
    "ViolationItem",
    "KnowledgeItem",
    "Constraint",
    # 指标
    "GeometryMetrics",
    "ThermalMetrics",
    "StructuralMetrics",
    "PowerMetrics",
    # 战略层
    "GlobalContextPack",
    "AgentTask",
    "StrategicPlan",
    # 战术层
    "GeometryAction",
    "GeometryProposal",
    "ThermalAction",
    "ThermalProposal",
    "StructuralAction",
    "StructuralProposal",
    "PowerAction",
    "PowerProposal",
    # 通信
    "AgentMessage",
    "ConflictReport",
    "ConflictResolution",
    # 执行
    "OptimizationPlan",
    "ExecutionResult",
    "ToolCall",
    "ToolResult",
]
