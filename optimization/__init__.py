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
    ModelingVariable,
    ModelingObjective,
    ModelingConstraint,
    ModelingIntent,
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
from .modes.agent_loop import AgentCoordinator, GeometryAgent, PowerAgent, StructuralAgent, ThermalAgent
from .modes.mass.maas_audit import select_top_pareto_indices
from .modes.mass.maas_compiler import compile_intent_to_problem_spec, formulate_modeling_intent
from .modes.mass.maas_mcts import (
    MCTSEvaluation,
    MCTSNode,
    MCTSSearchResult,
    MCTSVariant,
    MaaSMCTSPlanner,
)
from .modes.mass.maas_reflection import diagnose_solver_outcome, suggest_constraint_relaxation
from .modes.mass.meta_policy import propose_meta_policy_actions
from .modes.mass.modeling_validator import validate_modeling_intent
from .modes.mass.operator_actions import (
    apply_operator_program_to_intent,
    build_operator_program_from_context,
)
from .modes.mass.operator_program import OperatorAction, OperatorProgram, validate_operator_program
from .modes.mass.trace_features import extract_maas_trace_features

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
    "ModelingVariable",
    "ModelingObjective",
    "ModelingConstraint",
    "ModelingIntent",
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
    "AgentCoordinator",
    "GeometryAgent",
    "ThermalAgent",
    "StructuralAgent",
    "PowerAgent",
    "validate_modeling_intent",
    "compile_intent_to_problem_spec",
    "formulate_modeling_intent",
    "diagnose_solver_outcome",
    "suggest_constraint_relaxation",
    "select_top_pareto_indices",
    "MaaSMCTSPlanner",
    "MCTSSearchResult",
    "MCTSNode",
    "MCTSVariant",
    "MCTSEvaluation",
    "extract_maas_trace_features",
    "propose_meta_policy_actions",
    "OperatorAction",
    "OperatorProgram",
    "validate_operator_program",
    "apply_operator_program_to_intent",
    "build_operator_program_from_context",
]
