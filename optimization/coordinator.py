"""
Agent Coordinator: 多Agent协调器

负责：
1. 分发Meta-Reasoner的任务给各Agent
2. 收集Agent提案
3. 检测提案冲突
4. 协调冲突解决
5. 生成统一的执行计划
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import json

from .protocol import (
    StrategicPlan,
    AgentTask,
    GeometryProposal,
    ThermalProposal,
    StructuralProposal,
    PowerProposal,
    ConflictReport,
    ConflictResolution,
    OptimizationPlan,
    AgentMessage,
)
from .agents import GeometryAgent, ThermalAgent, StructuralAgent, PowerAgent
from core.protocol import DesignState
from core.logger import ExperimentLogger
from core.exceptions import OptimizationError


class AgentCoordinator:
    """多Agent协调器"""

    def __init__(
        self,
        geometry_agent: GeometryAgent,
        thermal_agent: ThermalAgent,
        structural_agent: StructuralAgent,
        power_agent: PowerAgent,
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化协调器

        Args:
            geometry_agent: 几何Agent
            thermal_agent: 热控Agent
            structural_agent: 结构Agent
            power_agent: 电源Agent
            logger: 日志记录器
        """
        self.agents = {
            "geometry": geometry_agent,
            "thermal": thermal_agent,
            "structural": structural_agent,
            "power": power_agent
        }
        self.logger = logger

        # 消息队列
        self.message_queue: List[AgentMessage] = []

    def coordinate(
        self,
        strategic_plan: StrategicPlan,
        current_state: DesignState,
        current_metrics: Dict[str, Any]
    ) -> OptimizationPlan:
        """
        协调执行战略计划

        Args:
            strategic_plan: Meta-Reasoner生成的战略计划
            current_state: 当前设计状态
            current_metrics: 当前多学科指标

        Returns:
            OptimizationPlan: 统一的执行计划
        """
        # 1. 分发任务给各Agent
        proposals = self._dispatch_tasks(
            strategic_plan.tasks,
            current_state,
            current_metrics,
            strategic_plan.iteration
        )

        # 2. 验证提案
        validated_proposals = self._validate_proposals(proposals, current_state)

        # 3. 检测冲突
        conflicts = self._detect_conflicts(validated_proposals, current_state)

        # 4. 解决冲突
        if conflicts:
            resolved_proposals = self._resolve_conflicts(
                conflicts,
                validated_proposals,
                strategic_plan
            )
        else:
            resolved_proposals = validated_proposals

        # 5. 生成执行计划
        execution_plan = self._generate_execution_plan(
            strategic_plan,
            resolved_proposals,
            current_state
        )

        return execution_plan

    def _dispatch_tasks(
        self,
        tasks: List[AgentTask],
        current_state: DesignState,
        current_metrics: Dict[str, Any],
        iteration: int
    ) -> Dict[str, Any]:
        """分发任务给各Agent"""
        proposals = {
            "geometry": [],
            "thermal": [],
            "structural": [],
            "power": []
        }

        for task in tasks:
            agent_type = task.agent_type

            if agent_type not in self.agents:
                if self.logger:
                    self.logger.logger.warning(f"Unknown agent type: {agent_type}")
                continue

            agent = self.agents[agent_type]

            try:
                # 根据Agent类型调用相应方法
                if agent_type == "geometry":
                    from .protocol import GeometryMetrics
                    proposal = agent.generate_proposal(
                        task,
                        current_state,
                        current_metrics.get("geometry", GeometryMetrics(
                            min_clearance=0, com_offset=[0,0,0],
                            moment_of_inertia=[0,0,0], packing_efficiency=0
                        )),
                        iteration
                    )
                    proposals["geometry"].append(proposal)

                elif agent_type == "thermal":
                    from .protocol import ThermalMetrics
                    proposal = agent.generate_proposal(
                        task,
                        current_state,
                        current_metrics.get("thermal", ThermalMetrics(
                            max_temp=0, min_temp=0, avg_temp=0, temp_gradient=0
                        )),
                        iteration
                    )
                    proposals["thermal"].append(proposal)

                elif agent_type == "structural":
                    from .protocol import StructuralMetrics
                    proposal = agent.generate_proposal(
                        task,
                        current_state,
                        current_metrics.get("structural", StructuralMetrics(
                            max_stress=0, max_displacement=0,
                            first_modal_freq=0, safety_factor=0
                        )),
                        iteration
                    )
                    proposals["structural"].append(proposal)

                elif agent_type == "power":
                    from .protocol import PowerMetrics
                    proposal = agent.generate_proposal(
                        task,
                        current_state,
                        current_metrics.get("power", PowerMetrics(
                            total_power=0, peak_power=0,
                            power_margin=0, voltage_drop=0
                        )),
                        iteration
                    )
                    proposals["power"].append(proposal)

            except Exception as e:
                if self.logger:
                    import traceback
                    error_details = traceback.format_exc()
                    self.logger.logger.error(
                        f"Agent {agent_type} failed: {e}\n"
                        f"Task ID: {task.task_id}\n"
                        f"Full traceback:\n{error_details}"
                    )

        return proposals

    def _validate_proposals(
        self,
        proposals: Dict[str, List[Any]],
        current_state: DesignState
    ) -> Dict[str, List[Any]]:
        """验证所有提案"""
        validated = {
            "geometry": [],
            "thermal": [],
            "structural": [],
            "power": []
        }

        for agent_type, proposal_list in proposals.items():
            agent = self.agents[agent_type]

            for proposal in proposal_list:
                validation = agent.validate_proposal(proposal, current_state)

                if validation["is_valid"]:
                    validated[agent_type].append(proposal)
                else:
                    if self.logger:
                        self.logger.logger.warning(
                            f"{agent_type} proposal {proposal.proposal_id} invalid: "
                            f"{validation['issues']}"
                        )

        return validated

    def _detect_conflicts(
        self,
        proposals: Dict[str, List[Any]],
        current_state: DesignState
    ) -> List[ConflictReport]:
        """检测提案间的冲突"""
        conflicts = []

        # 收集所有提案
        all_proposals = []
        for agent_type, proposal_list in proposals.items():
            all_proposals.extend(proposal_list)

        # 检测直接冲突：多个Agent操作同一组件
        component_operations = {}
        for proposal in all_proposals:
            for action in proposal.actions:
                # 提取目标组件
                if hasattr(action, 'component_id'):
                    comp_ids = [action.component_id]
                elif hasattr(action, 'target_components'):
                    comp_ids = action.target_components
                else:
                    continue

                for comp_id in comp_ids:
                    if comp_id not in component_operations:
                        component_operations[comp_id] = []
                    component_operations[comp_id].append(proposal.proposal_id)

        # 找出冲突
        for comp_id, proposal_ids in component_operations.items():
            if len(proposal_ids) > 1:
                conflict = ConflictReport(
                    conflict_id=f"CONFLICT_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    conflicting_proposals=proposal_ids,
                    conflict_type="direct",
                    description=f"多个Agent同时操作组件 {comp_id}",
                    affected_metrics=["geometry"]
                )
                conflicts.append(conflict)

        # 检测间接冲突：预测的副作用
        for i, prop1 in enumerate(all_proposals):
            for prop2 in all_proposals[i+1:]:
                # 检查prop1的副作用是否与prop2的目标冲突
                if self._has_side_effect_conflict(prop1, prop2):
                    conflict = ConflictReport(
                        conflict_id=f"CONFLICT_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        conflicting_proposals=[prop1.proposal_id, prop2.proposal_id],
                        conflict_type="indirect",
                        description="提案间存在副作用冲突",
                        affected_metrics=["multiple"]
                    )
                    conflicts.append(conflict)

        return conflicts

    def _has_side_effect_conflict(self, prop1: Any, prop2: Any) -> bool:
        """检查两个提案是否有副作用冲突"""
        # 简化实现：检查side_effects中是否提到对方的学科
        if not hasattr(prop1, 'side_effects') or not hasattr(prop2, 'side_effects'):
            return False

        # 提取学科关键词
        disciplines = ["geometry", "thermal", "structural", "power"]

        prop1_affects = set()
        for effect in prop1.side_effects:
            for disc in disciplines:
                if disc.lower() in effect.lower():
                    prop1_affects.add(disc)

        prop2_affects = set()
        for effect in prop2.side_effects:
            for disc in disciplines:
                if disc.lower() in effect.lower():
                    prop2_affects.add(disc)

        # 如果互相影响，则可能冲突
        return len(prop1_affects & prop2_affects) > 0

    def _resolve_conflicts(
        self,
        conflicts: List[ConflictReport],
        proposals: Dict[str, List[Any]],
        strategic_plan: StrategicPlan
    ) -> Dict[str, List[Any]]:
        """解决冲突"""
        resolutions = []

        for conflict in conflicts:
            # 简化策略：按优先级选择
            # 优先级：geometry > thermal > structural > power
            priority_order = ["geometry", "thermal", "structural", "power"]

            conflicting_ids = set(conflict.conflicting_proposals)
            selected_proposals = []

            for agent_type in priority_order:
                for proposal in proposals[agent_type]:
                    if proposal.proposal_id in conflicting_ids:
                        selected_proposals.append(proposal.proposal_id)
                        break
                if selected_proposals:
                    break

            resolution = ConflictResolution(
                conflict_id=conflict.conflict_id,
                resolution_type="prioritize",
                selected_proposals=selected_proposals,
                rationale=f"按学科优先级选择: {priority_order}"
            )
            resolutions.append(resolution)

        # 应用解决方案
        resolved_proposals = {
            "geometry": [],
            "thermal": [],
            "structural": [],
            "power": []
        }

        selected_ids = set()
        for resolution in resolutions:
            selected_ids.update(resolution.selected_proposals)

        # 保留选中的提案
        for agent_type, proposal_list in proposals.items():
            for proposal in proposal_list:
                # 如果提案不在任何冲突中，或者被选中，则保留
                in_conflict = any(
                    proposal.proposal_id in c.conflicting_proposals
                    for c in conflicts
                )

                if not in_conflict or proposal.proposal_id in selected_ids:
                    resolved_proposals[agent_type].append(proposal)

        return resolved_proposals

    def _generate_execution_plan(
        self,
        strategic_plan: StrategicPlan,
        proposals: Dict[str, List[Any]],
        current_state: DesignState
    ) -> OptimizationPlan:
        """生成执行计划"""
        # 收集所有选中的提案
        selected_proposal_ids = []
        all_actions = []

        for agent_type, proposal_list in proposals.items():
            for proposal in proposal_list:
                selected_proposal_ids.append(proposal.proposal_id)
                all_actions.extend(proposal.actions)

        # 确定执行顺序（考虑依赖关系）
        execution_sequence = self._determine_execution_order(all_actions)

        # 聚合预期指标
        expected_metrics = self._aggregate_expected_metrics(proposals)

        # 提取各学科的第一个提案（用于执行层）
        geometry_proposal = proposals["geometry"][0] if proposals["geometry"] else None
        thermal_proposal = proposals["thermal"][0] if proposals["thermal"] else None
        structural_proposal = proposals["structural"][0] if proposals["structural"] else None
        power_proposal = proposals["power"][0] if proposals["power"] else None

        plan = OptimizationPlan(
            plan_id=f"EXEC_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            iteration=strategic_plan.iteration,
            strategic_plan_id=strategic_plan.plan_id,
            selected_proposals=selected_proposal_ids,
            execution_sequence=execution_sequence,
            geometry_proposal=geometry_proposal,
            thermal_proposal=thermal_proposal,
            structural_proposal=structural_proposal,
            power_proposal=power_proposal,
            expected_metrics=expected_metrics,
            rollback_enabled=True,
            checkpoint_state=current_state.model_dump_json()
        )

        return plan

    def _determine_execution_order(self, actions: List[Any]) -> List[Dict[str, Any]]:
        """确定执行顺序"""
        # 简化实现：按操作类型排序
        # 顺序：几何操作 -> 热控操作 -> 结构操作 -> 电源操作
        order_map = {
            "MOVE": 1, "ROTATE": 1, "SWAP": 1, "REPACK": 1,
            "ADJUST_LAYOUT": 2, "ADD_HEATSINK": 2, "MODIFY_COATING": 2,
            "REINFORCE": 3, "REDUCE_MASS": 3, "ADJUST_STIFFNESS": 3,
            "OPTIMIZE_ROUTING": 4, "ADJUST_VOLTAGE": 4, "LOAD_BALANCING": 4
        }

        sorted_actions = sorted(
            actions,
            key=lambda a: order_map.get(a.op_type, 99)
        )

        execution_sequence = []
        for i, action in enumerate(sorted_actions):
            execution_sequence.append({
                "step": i + 1,
                "action_id": action.action_id,
                "op_type": action.op_type,
                "parameters": action.parameters if hasattr(action, 'parameters') else {}
            })

        return execution_sequence

    def _aggregate_expected_metrics(
        self,
        proposals: Dict[str, List[Any]]
    ) -> Dict[str, Any]:
        """聚合预期指标"""
        expected = {}

        # 从各Agent的预测中提取指标
        for agent_type, proposal_list in proposals.items():
            if not proposal_list:
                continue

            # 取第一个提案的预测（简化）
            proposal = proposal_list[0]
            if hasattr(proposal, 'predicted_metrics'):
                metrics_dict = proposal.predicted_metrics.model_dump()
                expected[agent_type] = metrics_dict

        return expected


if __name__ == "__main__":
    print("✓ Agent Coordinator module created")
