"""
Structural Agent: 结构专家

负责：
1. 结构强度分析
2. 振动与模态分析
3. 质量优化
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
    StructuralProposal,
    StructuralAction,
    StructuralMetrics,
)
from core.protocol import DesignState
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class StructuralAgent:
    """结构专家Agent"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        temperature: float = 0.6,
        base_url: Optional[str] = None,
        logger: Optional[ExperimentLogger] = None
    ):
        dashscope.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.logger = logger
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        return """你是结构专家（Structural Agent）。

【专业能力】
1. 结构力学：应力分析、变形计算、稳定性评估
2. 振动分析：模态分析、频率响应、阻尼设计
3. 质量优化：轻量化设计、材料选择、拓扑优化

【可用操作】
1. **REINFORCE**: 加强结构
   - 参数: target_components, reinforcement_type (rib/bracket/stiffener)
   - 原理: 增加结构刚度，降低应力和变形
   - 示例: 为薄壁结构添加加强筋

2. **REDUCE_MASS**: 减轻质量
   - 参数: target_components, method (material_change/topology_opt/thickness_reduce)
   - 原理: 在满足强度前提下减轻质量
   - 示例: 将铝合金改为碳纤维复合材料

3. **ADJUST_STIFFNESS**: 调整刚度
   - 参数: target_components, target_frequency
   - 原理: 调整结构刚度以避开共振频率
   - 示例: 增加板厚以提高一阶频率至>50Hz

【输出格式】
```json
{
  "proposal_id": "STRUCT_PROP_001",
  "task_id": "TASK_001",
  "reasoning": "结构分析推理：\\n1. 应力/变形/频率问题诊断\\n2. 结构力学原理分析\\n3. 方案选择依据\\n4. 预期结构性能",
  "actions": [
    {
      "action_id": "ACT_001",
      "op_type": "REINFORCE",
      "target_components": ["Panel_01"],
      "parameters": {
        "reinforcement_type": "rib",
        "location": "center",
        "dimensions": [100, 10, 2]
      },
      "rationale": "中心区域应力集中，添加加强筋可降低应力30%"
    }
  ],
  "predicted_metrics": {
    "max_stress": 120.0,
    "max_displacement": 0.08,
    "first_modal_freq": 65.0,
    "safety_factor": 2.5
  },
  "side_effects": [
    "增加质量约50g",
    "可能影响热传导路径"
  ],
  "confidence": 0.85
}
```

【结构知识】
1. **应力分析**: σ = F/A (正应力), τ = F/A (剪应力)
   - 屈服强度：铝合金≈300MPa，钛合金≈900MPa，碳纤维≈1500MPa
   - 安全系数：一般取2.0-3.0

2. **变形计算**: δ = FL/(EA) (轴向), δ = FL³/(3EI) (悬臂梁)
   - E=弹性模量：铝≈70GPa，钛≈110GPa，碳纤维≈150GPa

3. **模态分析**: f = (1/2π)√(k/m)
   - 一阶频率应>50Hz（避开发射段低频振动）
   - 避开整星频率（防止共振）

4. **工程经验**:
   - 薄壁结构（t<2mm）易失稳，需加强筋
   - 悬臂结构刚度低，需增大截面或缩短长度
   - 质量集中会降低频率，应分散布置
   - 碳纤维复合材料可减重40-60%但成本高

【推理原则】
1. **安全第一**: 确保安全系数≥2.0
2. **频率裕度**: 一阶频率应高于要求10-20%
3. **质量敏感**: 每增加1kg质量成本显著增加
4. **制造可行**: 考虑加工工艺和装配难度
"""

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: StructuralMetrics,
        iteration: int = 0
    ) -> StructuralProposal:
        try:
            user_prompt = self._build_prompt(task, current_state, current_metrics)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="structural_agent",
                    request={"messages": messages},
                    response=None
                )

            response = dashscope.Generation.call(
                model=self.model,
                messages=messages,
                result_format='message',
                temperature=self.temperature,
                response_format={'type': 'json_object'}
            )
            
            # 检查响应状态
            if response.status_code != HTTPStatus.OK:
                raise LLMError(f"DashScope API 调用失败: {response.code} - {response.message}")

            response_json = json.loads(response.output.choices[0].message.content)

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="structural_agent",
                    request=None,
                    response=response_json
                )

            proposal = StructuralProposal(**response_json)
            if not proposal.proposal_id or proposal.proposal_id.startswith("STRUCT_PROP"):
                proposal.proposal_id = f"STRUCT_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            proposal.task_id = task.task_id

            return proposal

        except Exception as e:
            raise LLMError(f"Structural Agent failed: {e}")

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: StructuralMetrics
    ) -> str:
        prompt = f"""# 结构优化任务

## 任务信息
- 任务ID: {task.task_id}
- 目标: {task.objective}
- 优先级: {task.priority}

## 约束条件
"""
        for i, constraint in enumerate(task.constraints, 1):
            prompt += f"{i}. {constraint}\n"

        prompt += f"""
## 当前结构状态
- 最大应力: {current_metrics.max_stress:.1f} MPa
- 最大位移: {current_metrics.max_displacement:.3f} mm
- 一阶模态频率: {current_metrics.first_modal_freq:.1f} Hz
- 安全系数: {current_metrics.safety_factor:.2f}

## 结构组件信息
"""
        for comp in current_state.components[:5]:
            prompt += f"- {comp.id}: 质量 {comp.mass:.2f}kg\n"

        # 添加完整的可用组件列表（防止幻觉）
        prompt += "\n## 可用组件列表（仅可引用以下组件ID）\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} (类别: {comp.category})\n"
        prompt += "\n⚠️ 重要：在所有操作中，target_components 参数必须是上述列表中的组件ID，不能使用不存在的组件名称！\n"

        if task.context:
            prompt += "\n## 额外上下文\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\n请生成结构优化提案（JSON格式）。"
        return prompt

    def validate_proposal(
        self,
        proposal: StructuralProposal,
        current_state: DesignState
    ) -> Dict[str, Any]:
        issues = []
        warnings = []

        component_ids = [c.id for c in current_state.components]

        for action in proposal.actions:
            for comp_id in action.target_components:
                if comp_id not in component_ids:
                    issues.append(f"组件 {comp_id} 不存在")

        if proposal.predicted_metrics.safety_factor < 1.5:
            warnings.append(f"预测安全系数过低 ({proposal.predicted_metrics.safety_factor:.2f})")

        if proposal.predicted_metrics.first_modal_freq < 40:
            warnings.append(f"预测一阶频率过低 ({proposal.predicted_metrics.first_modal_freq:.1f}Hz)")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings
        }


if __name__ == "__main__":
    print("✓ Structural Agent module created")
