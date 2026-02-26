"""
Geometry Agent: 几何布局专家

负责：
1. 布局优化（组件位置、朝向）
2. 干涉检测与避让
3. 质心与转动惯量控制
"""

import openai
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

from ..protocol import (
    AgentTask,
    GeometryProposal,
    GeometryAction,
    GeometryMetrics,
)
from core.protocol import DesignState
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class GeometryAgent:
    """几何布局专家Agent"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4-turbo",
        temperature: float = 0.6,
        base_url: Optional[str] = None,
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化Geometry Agent

        Args:
            api_key: OpenAI API密钥
            model: 使用的模型
            temperature: 温度参数
            logger: 实验日志记录器
        """
        if base_url:
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        else:
            self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.logger = logger

        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """加载系统提示词"""
        return """你是几何布局专家（Geometry Agent）。

【专业能力】
1. 3D空间推理：理解AABB碰撞检测、间隙计算、质心计算
2. 布局优化：装箱算法、墙面安装、层切割策略
3. 约束感知：理解几何约束对其他学科的影响

【任务输入】
Meta-Reasoner分配给你的任务，包含：
- objective: 任务目标（如"解决Battery与Rib的干涉"）
- constraints: 约束条件（如"质心偏移<10mm"）
- context: 当前布局状态

【可用操作】
1. **MOVE**: 移动组件
   - 参数: component_id, axis (X/Y/Z), range [min, max]
   - 示例: 将Battery沿+X移动5-10mm

2. **ROTATE**: 旋转组件
   - 参数: component_id, axis, angle_range [min, max]
   - 示例: 将天线绕Z轴旋转0-90度

3. **SWAP**: 交换两个组件的位置
   - 参数: component_a, component_b
   - 示例: 交换Battery和Payload的位置

4. **REPACK**: 重新执行装箱算法
   - 参数: strategy (greedy/multistart), clearance
   - 示例: 使用multistart策略重新装箱

【输出格式】
你必须输出JSON格式的GeometryProposal：

```json
{
  "proposal_id": "GEOM_PROP_001",
  "task_id": "TASK_001",
  "reasoning": "详细的推理过程：\\n1. 当前问题分析\\n2. 为什么选择这个操作\\n3. 预期效果",
  "actions": [
    {
      "action_id": "ACT_001",
      "op_type": "MOVE",
      "component_id": "Battery_01",
      "parameters": {
        "axis": "X",
        "range": [5.0, 10.0]
      },
      "rationale": "向+X移动可增加与Rib的间隙"
    }
  ],
  "predicted_metrics": {
    "min_clearance": 8.0,
    "com_offset": [1.2, -0.2, 0.1],
    "moment_of_inertia": [1.2, 1.3, 1.1],
    "packing_efficiency": 75.0,
    "num_collisions": 0
  },
  "side_effects": [
    "移动Battery可能影响热分布，需Thermal Agent复核",
    "质心向+X偏移约0.7mm"
  ],
  "confidence": 0.85
}
```

【推理原则】
1. **空间推理**: 明确说明移动方向和距离的几何依据
2. **约束检查**: 确保操作不违反几何约束
3. **影响预测**: 预测对质心、转动惯量、热分布的影响
4. **置信度评估**: 根据问题复杂度和历史经验给出置信度

【几何知识】
1. 质心计算: CoM = Σ(m_i * r_i) / Σm_i
2. AABB碰撞: 两个AABB相交当且仅当所有轴上的投影都重叠
3. 间隙计算: clearance = min(|A.max - B.min|, |B.max - A.min|) for each axis
4. 转动惯量: I = Σm_i * r_i²（简化）

【注意事项】
- 移动高质量组件（如电池）对质心影响大
- 旋转细长组件（如天线）可能改变包络
- SWAP操作风险高，需谨慎使用
- REPACK会重置所有位置，仅在必要时使用
"""

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: GeometryMetrics,
        iteration: int = 0
    ) -> GeometryProposal:
        """
        生成几何优化提案

        Args:
            task: Meta-Reasoner分配的任务
            current_state: 当前设计状态
            current_metrics: 当前几何指标
            iteration: 当前迭代次数

        Returns:
            GeometryProposal: 几何优化提案
        """
        try:
            # 构建提示
            try:
                if self.logger:
                    self.logger.logger.debug(f"[GeometryAgent] Building prompt for task {task.task_id}")
                user_prompt = self._build_prompt(task, current_state, current_metrics)
                if self.logger:
                    self.logger.logger.debug(f"[GeometryAgent] Prompt built successfully, length: {len(user_prompt)}")
            except Exception as prompt_error:
                import traceback
                error_details = traceback.format_exc()
                if self.logger:
                    self.logger.logger.error(f"[GeometryAgent] Failed to build prompt:\n{error_details}")
                raise LLMError(f"Failed to build prompt: {prompt_error}")

            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 记录请求
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] Logging LLM request for task {task.task_id}")
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="geometry_agent",
                    request={"messages": messages},
                    response=None
                )

            # 调用LLM
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] Calling LLM API")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] LLM API call successful")

            response_text = response.choices[0].message.content
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] Response text length: {len(response_text)}")

            response_json = json.loads(response_text)
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] JSON parsed successfully")

            # 记录响应
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] Logging LLM response")
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="geometry_agent",
                    request=None,
                    response=response_json
                )

            # 构建Proposal
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] Creating GeometryProposal from JSON")
            proposal = GeometryProposal(**response_json)
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] GeometryProposal created successfully")

            # 自动生成ID
            if not proposal.proposal_id or proposal.proposal_id.startswith("GEOM_PROP"):
                proposal.proposal_id = f"GEOM_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            proposal.task_id = task.task_id

            return proposal

        except Exception as e:
            raise LLMError(f"Geometry Agent failed: {e}")

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: GeometryMetrics
    ) -> str:
        """构建用户提示"""
        prompt = f"""# 几何优化任务

## 任务信息
- 任务ID: {task.task_id}
- 目标: {task.objective}
- 优先级: {task.priority}

## 约束条件
"""
        for i, constraint in enumerate(task.constraints, 1):
            prompt += f"{i}. {constraint}\n"

        prompt += f"""
## 当前几何状态
- 最小间隙: {float(current_metrics.min_clearance):.2f} mm
- 质心偏移: [{', '.join(f'{float(x):.2f}' for x in current_metrics.com_offset)}] mm
- 转动惯量: [{', '.join(f'{float(x):.2f}' for x in current_metrics.moment_of_inertia)}] kg·m²
- 装填率: {float(current_metrics.packing_efficiency):.1f}%
- 碰撞数量: {int(current_metrics.num_collisions)}

## 组件布局
"""
        # 添加组件位置信息
        for comp in current_state.components[:5]:  # 只显示前5个组件
            prompt += f"- {comp.id}: 位置 {comp.position}, 尺寸 {comp.dimensions}\n"

        if len(current_state.components) > 5:
            prompt += f"... (共{len(current_state.components)}个组件)\n"

        # 添加任务上下文
        if task.context:
            prompt += "\n## 额外上下文\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\n请生成几何优化提案（JSON格式）。"

        return prompt

    def validate_proposal(
        self,
        proposal: GeometryProposal,
        current_state: DesignState
    ) -> Dict[str, Any]:
        """
        验证提案的可行性

        Args:
            proposal: 几何提案
            current_state: 当前设计状态

        Returns:
            验证结果
        """
        issues = []
        warnings = []

        # 检查操作的有效性
        for action in proposal.actions:
            # 检查组件是否存在
            component_ids = [c.id for c in current_state.components]

            if action.op_type in ["MOVE", "ROTATE"]:
                if action.component_id not in component_ids:
                    issues.append(f"组件 {action.component_id} 不存在")

            elif action.op_type == "SWAP":
                comp_a = action.parameters.get("component_a")
                comp_b = action.parameters.get("component_b")
                if comp_a not in component_ids:
                    issues.append(f"组件 {comp_a} 不存在")
                if comp_b not in component_ids:
                    issues.append(f"组件 {comp_b} 不存在")

            # 检查参数合理性
            if action.op_type == "MOVE":
                range_param = action.parameters.get("range", [])
                if len(range_param) != 2:
                    issues.append(f"MOVE操作的range参数必须是2个数字")
                elif range_param[0] > range_param[1]:
                    issues.append(f"MOVE操作的range参数顺序错误: {range_param}")

        # 检查预测指标的合理性
        if proposal.predicted_metrics.min_clearance < 0:
            issues.append("预测的最小间隙为负数，不合理")

        if proposal.predicted_metrics.packing_efficiency > 100:
            issues.append("预测的装填率>100%，不合理")

        # 检查置信度
        if proposal.confidence < 0.3:
            warnings.append(f"置信度过低 ({proposal.confidence:.2f})，建议谨慎执行")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings
        }


if __name__ == "__main__":
    print("Testing Geometry Agent...")

    # 创建示例任务
    task = AgentTask(
        task_id="TASK_001",
        agent_type="geometry",
        objective="将Battery_01沿+X方向移动，使其与Rib_01的间隙达到5-8mm",
        constraints=[
            "移动后质心偏移不得超过±10mm",
            "不得与其他组件产生新的干涉"
        ],
        priority=1,
        context={
            "current_position": 13.0,
            "target_clearance": 6.0
        }
    )

    print(f"✓ Task created: {task.objective}")
