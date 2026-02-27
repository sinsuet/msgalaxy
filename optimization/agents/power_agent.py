"""
Power Agent: 电源专家

负责：
1. 功率预算管理
2. 电源线路优化
3. 电磁兼容性检查
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
    PowerProposal,
    PowerAction,
    PowerMetrics,
)
from core.protocol import DesignState
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class PowerAgent:
    """电源专家Agent"""

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
        return """你是电源专家（Power Agent）。

【专业能力】
1. 电源系统：功率预算、电压调节、电流分配
2. 线路设计：布线优化、压降计算、EMC设计
3. 能量管理：充放电策略、功耗优化

【可用操作】
1. **OPTIMIZE_ROUTING**: 优化电源线路
   - 参数: source, targets, routing_strategy (shortest/balanced/emc)
   - 原理: 减少线路长度和压降
   - 示例: 优化电池到负载的供电路径

2. **ADJUST_VOLTAGE**: 调整电压等级
   - 参数: target_components, voltage_level
   - 原理: 使用合适的电压等级减少损耗
   - 示例: 高功耗设备使用高电压（减小电流）

3. **LOAD_BALANCING**: 负载均衡
   - 参数: power_sources, load_distribution
   - 原理: 平衡各电源模块负载，提高效率
   - 示例: 将负载分配到多个电源模块

【输出格式】
```json
{
  "proposal_id": "POWER_PROP_001",
  "task_id": "TASK_001",
  "reasoning": "电源分析推理：\\n1. 功率预算分析\\n2. 压降/效率问题诊断\\n3. 方案选择依据\\n4. 预期电气性能",
  "actions": [
    {
      "action_id": "ACT_001",
      "op_type": "OPTIMIZE_ROUTING",
      "target_components": ["Battery_01", "PowerModule_02"],
      "parameters": {
        "routing_strategy": "shortest",
        "max_length": 500
      },
      "rationale": "缩短线路长度可减少压降和功耗"
    }
  ],
  "predicted_metrics": {
    "total_power": 120.0,
    "peak_power": 145.0,
    "power_margin": 28.0,
    "voltage_drop": 0.25
  },
  "side_effects": [
    "线路重新布置可能影响布局",
    "需要Geometry Agent确认空间"
  ],
  "confidence": 0.80
}
```

【电源知识】
1. **欧姆定律**: V = IR, P = VI = I²R
   - 压降：ΔV = I·R·L/A (R=电阻率, L=长度, A=截面积)
   - 铜线电阻率：1.7×10⁻⁸ Ω·m

2. **功率损耗**: P_loss = I²R
   - 高电压低电流可减少损耗（P=VI，提高V降低I）
   - 线径增大可减少电阻

3. **电源效率**: η = P_out/P_in
   - DC-DC转换器效率：85-95%
   - 线路损耗应<5%

4. **工程经验**:
   - 压降应<电压的5%（如28V系统压降<1.4V）
   - 功率裕度应≥20%（应对峰值负载）
   - 高频信号线与电源线保持距离>10mm（EMC）
   - 电池到负载距离应尽量短

【推理原则】
1. **效率优先**: 最小化功率损耗
2. **可靠性**: 避免单点故障，考虑冗余
3. **EMC**: 防止电磁干扰
4. **热管理**: 大电流线路会发热
"""

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: PowerMetrics,
        iteration: int = 0
    ) -> PowerProposal:
        try:
            user_prompt = self._build_prompt(task, current_state, current_metrics)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="power_agent",
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
                    role="power_agent",
                    request=None,
                    response=response_json
                )

            proposal = PowerProposal(**response_json)
            if not proposal.proposal_id or proposal.proposal_id.startswith("POWER_PROP"):
                proposal.proposal_id = f"POWER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            proposal.task_id = task.task_id

            return proposal

        except Exception as e:
            raise LLMError(f"Power Agent failed: {e}")

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: PowerMetrics
    ) -> str:
        prompt = f"""# 电源优化任务

## 任务信息
- 任务ID: {task.task_id}
- 目标: {task.objective}
- 优先级: {task.priority}

## 约束条件
"""
        for i, constraint in enumerate(task.constraints, 1):
            prompt += f"{i}. {constraint}\n"

        prompt += f"""
## 当前电源状态
- 总功耗: {current_metrics.total_power:.1f} W
- 峰值功耗: {current_metrics.peak_power:.1f} W
- 功率裕度: {current_metrics.power_margin:.1f}%
- 最大压降: {current_metrics.voltage_drop:.2f} V

## 组件功耗信息
"""
        for comp in current_state.components[:5]:
            if comp.power > 0:
                prompt += f"- {comp.id}: {comp.power:.1f}W\n"

        if task.context:
            prompt += "\n## 额外上下文\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\n请生成电源优化提案（JSON格式）。"
        return prompt

    def validate_proposal(
        self,
        proposal: PowerProposal,
        current_state: DesignState
    ) -> Dict[str, Any]:
        issues = []
        warnings = []

        component_ids = [c.id for c in current_state.components]

        for action in proposal.actions:
            for comp_id in action.target_components:
                if comp_id not in component_ids:
                    issues.append(f"组件 {comp_id} 不存在")

        if proposal.predicted_metrics.power_margin < 15:
            warnings.append(f"预测功率裕度过低 ({proposal.predicted_metrics.power_margin:.1f}%)")

        if proposal.predicted_metrics.voltage_drop > 1.0:
            warnings.append(f"预测压降过大 ({proposal.predicted_metrics.voltage_drop:.2f}V)")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings
        }


if __name__ == "__main__":
    print("✓ Power Agent module created")
