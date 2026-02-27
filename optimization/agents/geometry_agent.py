"""
Geometry Agent: 几何布局专家

负责：
1. 布局优化（组件位置、朝向）
2. 干涉检测与避让
3. 质心与转动惯量控制
"""

import os
# 强制清空可能导致 10061 错误的本地代理环境变量
for proxy_env in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if proxy_env in os.environ:
        del os.environ[proxy_env]

import dashscope
from http import HTTPStatus
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
        model: str = "qwen-plus",
        temperature: float = 0.6,
        base_url: Optional[str] = None,  # 保留兼容性
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化Geometry Agent

        Args:
            api_key: DashScope API密钥
            model: 使用的模型
            temperature: 温度参数
            logger: 实验日志记录器
        """
        dashscope.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.logger = logger

        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """加载系统提示词 (DV2.0: 10类算子全面解封)"""
        return """你是几何布局专家（Geometry Agent）。

【专业能力】
1. 3D空间推理：理解AABB碰撞检测、间隙计算、质心计算
2. 布局优化：装箱算法、墙面安装、层切割策略
3. 约束感知：理解几何约束对其他学科的影响
4. 动态几何生成：支架、散热器、圆柱体包络

【任务输入】
Meta-Reasoner分配给你的任务，包含：
- objective: 任务目标（如"解决Battery与Rib的干涉"）
- constraints: 约束条件（如"质心偏移<10mm"）
- context: 当前布局状态

【DV2.0 可用操作 - 10类算子全面解封！】

=== 基础几何算子 ===

1. **MOVE**: 移动组件
   - 参数: {"axis": "X/Y/Z", "range": [min, max]}
   - 示例: 将Battery沿+X移动50-80mm

2. **ROTATE**: 旋转组件
   - 参数: {"axis": "X/Y/Z", "angle_range": [min, max]}
   - 示例: 将天线绕Z轴旋转0-90度

3. **SWAP**: 交换两个组件的位置
   - 参数: {"component_b": "payload_01"}
   - 示例: 交换Battery和Payload的位置

4. **DEFORM**: FFD自由变形（增加散热面积）
   - 参数: {"deform_type": "stretch_z", "magnitude": 15.0}
   - deform_type: "stretch_x" | "stretch_y" | "stretch_z" | "bulge"
   - 示例: 将过热的Battery沿Z轴拉伸15mm

5. **ALIGN**: 对齐组件（沿指定轴对齐到参考组件）
   - 参数: {"axis": "Y", "reference_component": "radiator_panel"}
   - 示例: 将所有电池沿Y轴对齐到散热板

=== 包络与结构算子（DV2.0 新增！）===

6. **CHANGE_ENVELOPE**: 包络切换（Box → Cylinder）
   - 参数: {"shape": "cylinder", "dimensions": {"radius": 50, "height": 60}}
   - 原理: 圆柱体适合飞轮、反作用轮等旋转对称组件
   - 示例: 将reaction_wheel改为圆柱体包络

7. **ADD_BRACKET**: 添加结构支架（垫高组件、改变质心）
   - 参数: {"height": 30.0, "shape": "cylinder", "diameter": 20.0, "attach_face": "-Z"}
   - 原理: 支架可改变组件Z位置，调整质心分布
   - 示例: 为payload_camera添加30mm高的圆柱支架

=== 热学辅助算子（可协同使用）===

8. **ADD_HEATSINK**: 添加散热器（几何层面）
   - 参数: {"face": "+Y", "thickness": 3.0, "conductivity": 400.0}
   - 原理: 在组件表面附加高导热薄板
   - 示例: 为transmitter_01在+Y面添加铜散热板

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
        "axis": "Z",
        "range": [80.0, 100.0]
      },
      "rationale": "将电池上移以平衡质心Z分量"
    },
    {
      "action_id": "ACT_002",
      "op_type": "ADD_BRACKET",
      "component_id": "payload_camera",
      "parameters": {
        "height": 30.0,
        "shape": "cylinder",
        "diameter": 20.0
      },
      "rationale": "添加支架垫高相机，改善质心分布"
    },
    {
      "action_id": "ACT_003",
      "op_type": "CHANGE_ENVELOPE",
      "component_id": "reaction_wheel_01",
      "parameters": {
        "shape": "cylinder"
      },
      "rationale": "飞轮是旋转对称组件，圆柱体包络更准确"
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
    "支架增加约50g质量",
    "质心Z分量上移约15mm"
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

【关键策略：探索步长】
- **当遇到几何重叠导致仿真失败（惩罚温度=999°C）时**：
  * 这表明组件之间存在严重干涉，COMSOL无法划分网格
  * 必须使用**大步长**（50mm-100mm）迅速解开干涉
  * 不要使用小步长（<10mm）试探，这会浪费迭代次数
  * 优先将组件移动到包络的不同象限，确保彻底分离
- **当仿真成功但温度超标时**：
  * 可以使用中等步长（10mm-30mm）进行微调
  * 考虑使用DEFORM增加散热面积
- **当接近最优解时**：
  * 使用小步长（5mm-10mm）精细调整

【质心配平策略 - 激进杠杆配平！】
**当前状态**: 质心偏移还有 68mm，远超 20mm 阈值！

**激进策略**:
1. **识别重型组件**: payload_camera (12kg), battery_01 (8kg), battery_02 (8kg)
2. **杠杆配平原理**: 质心偏移 = Σ(m_i * r_i) / Σm_i
   - 要快速修正质心，必须移动重型组件！
   - 移动 8kg 电池 100mm 的效果 = 移动 1kg 组件 800mm

**具体操作指南**:
- **遇到 payload_camera (12kg) 在某一侧时**:
  * 不要犹豫！立即使用 MOVE 算子将 battery_01 或 battery_02 (8kg) 一次性跨越整个舱体
  * 移动距离: 100mm~200mm（大跨步！）
  * 目标: 将电池移到相机的对立面，形成杠杆配平
  * 示例: 如果相机在 X=-100mm，将电池移到 X=+150mm

- **使用 ADD_BRACKET 精确调整 Z 轴**:
  * 为重型组件添加 30mm~50mm 高的支架
  * 这可以精确调整 Z 方向的质心分量

- **使用 SWAP 快速交换位置**:
  * 如果两个重型组件位置不合理，直接 SWAP 交换
  * 这比多次 MOVE 更高效

**禁止保守策略**:
- ❌ 不要使用小步长（<20mm）试探
- ❌ 不要只移动轻型组件（<2kg）
- ❌ 不要害怕大跨步移动

**目标**: 在 2-3 次迭代内将质心偏移压入 20mm 以内！

【重要提醒】
- 系统底层已全面升级！你现在可以自由使用上述所有算子！
- 遇到质心偏移问题，请大胆使用ADD_BRACKET调整！
- 飞轮、反作用轮等组件请使用CHANGE_ENVELOPE切换为圆柱体！
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
            response = dashscope.Generation.call(
                model=self.model,
                messages=messages,
                result_format='message',
                temperature=self.temperature,
                response_format={'type': 'json_object'}
            )
            if self.logger:
                self.logger.logger.debug(f"[GeometryAgent] LLM API call successful")

            # 检查响应状态
            if response.status_code != HTTPStatus.OK:
                raise LLMError(f"DashScope API 调用失败: {response.code} - {response.message}")

            response_text = response.output.choices[0].message.content
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

        # 添加完整的可用组件列表（防止幻觉）
        prompt += "\n## 可用组件列表（仅可引用以下组件ID）\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} ({comp.name})\n"
        prompt += "\n⚠️ 重要：在所有操作中，target_components 参数必须是上述列表中的组件ID，不能使用不存在的组件名称！\n"

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
