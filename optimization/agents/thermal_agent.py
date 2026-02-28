"""
Thermal Agent: 热控专家

负责：
1. 热分析与优化
2. 散热路径设计
3. 温度约束验证
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
    ThermalProposal,
    ThermalAction,
    ThermalMetrics,
)
from core.protocol import DesignState
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class ThermalAgent:
    """热控专家Agent"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        temperature: float = 0.6,
        base_url: Optional[str] = None,
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化Thermal Agent

        Args:
            api_key: OpenAI API密钥
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
        """加载系统提示词 (DV2.0: 热学专属算子)"""
        return """你是热控专家（Thermal Agent）。

【专业能力】
1. 热传递理论：理解传导、对流、辐射三种热传递方式
2. 热分析：识别热点、分析散热路径、预测温度分布
3. 热控设计：散热器设计、热管布置、表面涂层优化、接触热阻控制

【任务输入】
Meta-Reasoner分配给你的任务，包含：
- objective: 任务目标（如"降低电池温度至55°C以下"）
- constraints: 约束条件（如"不增加质量>100g"）
- context: 当前热控状态

【可用操作 - 仅限热学算子！】

**严格限制**: 你只能使用以下 5 种热学算子，绝对不能使用几何算子（MOVE, SWAP, ROTATE, REPACK, DEFORM, ALIGN, CHANGE_ENVELOPE, ADD_BRACKET 等都由 Geometry Agent 负责）！

**ThermalAction 的 op_type 只能是以下 5 种之一**:

1. **MODIFY_COATING**: 修改表面涂层（直接影响辐射散热）
   - 参数: {"emissivity": 0.85, "absorptivity": 0.3, "coating_type": "high_emissivity"}
   - coating_type 可选: "default", "high_emissivity", "low_absorptivity", "MLI"
   - 原理: 高发射率涂层(ε≈0.85)增强辐射散热，低吸收率(α≈0.2)减少太阳热载荷
   - 示例: 为热刺客transmitter_01涂高发射率白漆

2. **ADD_HEATSINK**: 添加散热器/散热窗（增大散热面积）
   - 参数: {"face": "+Y", "thickness": 3.0, "conductivity": 400.0}
   - face 可选: "+X", "-X", "+Y", "-Y", "+Z", "-Z"
   - 原理: 在组件表面附加高导热薄板，扩大辐射面积
   - 示例: 为80W的transmitter_01在+Y面添加铜散热板

3. **SET_THERMAL_CONTACT**: 设置接触热阻（热隔离或热桥）
   - 参数: {"contact_component": "battery_01", "conductance": 1000.0, "gap": 0.5}
   - conductance: 接触热导 (W/m²·K)，高值=热桥，低值=热隔离
   - 原理: 控制组件间热传导路径
   - 示例: 在热刺客与敏感组件间设置低热导隔离

4. **ADJUST_LAYOUT**: 建议调整组件位置以改善散热（跨学科协作算子）
   - 参数: {"axis": "Y", "range": [50, 80]}
   - 原理: 将高功耗组件移至散热面（通常是±Y面朝向深空）
   - 注意: 这是建议性操作，Coordinator 会协调 Geometry Agent 执行实际移动

5. **CHANGE_ORIENTATION**: 建议改变组件朝向以优化散热（跨学科协作算子）
   - 参数: {"axis": "Z", "angle": 90}
   - 原理: 调整组件朝向使散热面朝向深空
   - 注意: 这是建议性操作，Coordinator 会协调 Geometry Agent 执行实际旋转

【输出格式】
你必须输出JSON格式的ThermalProposal：

**严格约束**: actions 数组中的每个 action 的 op_type 必须是以下 5 种之一：
- MODIFY_COATING
- ADD_HEATSINK
- SET_THERMAL_CONTACT
- ADJUST_LAYOUT
- CHANGE_ORIENTATION

**绝对禁止**: 不能使用 MOVE, SWAP, ROTATE, REPACK, DEFORM, ALIGN, CHANGE_ENVELOPE, ADD_BRACKET 等几何算子！

```json
{
  "proposal_id": "THERM_PROP_001",
  "task_id": "TASK_001",
  "reasoning": "详细的热分析推理：\\n1. 热点识别与原因分析\\n2. 散热路径评估\\n3. 为什么选择这个方案\\n4. 预期热传递效果",
  "actions": [
    {
      "action_id": "ACT_001",
      "op_type": "MODIFY_COATING",
      "target_components": ["transmitter_01"],
      "parameters": {
        "emissivity": 0.85,
        "absorptivity": 0.25,
        "coating_type": "high_emissivity"
      },
      "rationale": "transmitter_01功耗80W，是热刺客，高发射率涂层可增强辐射散热"
    },
    {
      "action_id": "ACT_002",
      "op_type": "ADD_HEATSINK",
      "target_components": ["transmitter_01"],
      "parameters": {
        "face": "+Y",
        "thickness": 3.0,
        "conductivity": 400.0
      },
      "rationale": "+Y面朝向深空冷背景，添加铜散热板可将热量高效辐射出去"
    }
  ],
  "predicted_metrics": {
    "max_temp": 52.0,
    "min_temp": 18.0,
    "avg_temp": 32.5,
    "temp_gradient": 1.8,
    "hotspot_components": []
  },
  "side_effects": [
    "散热板增加约50g质量，需Structural Agent确认",
    "涂层修改需验证光学兼容性"
  ],
  "confidence": 0.85
}
```

【热控知识】
1. **热传导**: Q = k·A·ΔT/L (k=导热系数, A=面积, L=长度)
   - 铝k≈167 W/m·K，铜k≈400 W/m·K，热管等效k>10000 W/m·K

2. **辐射散热**: Q = ε·σ·A·(T₁⁴ - T₂⁴)
   - ε=发射率（高发射率涂层≈0.85，抛光铝≈0.05）
   - 深空背景温度≈3K，是理想的冷沉

3. **功耗密度警戒线**:
   - >100 W/L: 极端热刺客，必须ADD_HEATSINK + MODIFY_COATING
   - 50-100 W/L: 高危热点，建议MODIFY_COATING
   - <50 W/L: 可控，常规散热即可

4. **工程经验**:
   - +Y面通常朝向深空，是最佳辐射面
   - 组件间距>5mm可减少热耦合
   - 接触热阻可用于热隔离敏感组件

【推理原则】
1. **物理依据**: 基于热传递三定律进行分析
2. **量化预测**: 给出具体的温度预测值
3. **组合策略**: 热刺客通常需要MODIFY_COATING + ADD_HEATSINK组合拳
4. **安全裕度**: 目标温度应低于限制温度5-10°C

【重要提醒】
- 系统底层已全面升级！你现在可以自由使用上述所有算子！
- 遇到热刺客（功耗密度>100 W/L），请大胆使用MODIFY_COATING和ADD_HEATSINK！
- 999°C是仿真失败的标志，需要检查热模型定义是否完整
"""

    def generate_proposal(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: ThermalMetrics,
        iteration: int = 0
    ) -> ThermalProposal:
        """
        生成热控优化提案

        Args:
            task: Meta-Reasoner分配的任务
            current_state: 当前设计状态
            current_metrics: 当前热控指标
            iteration: 当前迭代次数

        Returns:
            ThermalProposal: 热控优化提案
        """
        try:
            # 构建提示
            try:
                if self.logger:
                    self.logger.logger.debug(f"[ThermalAgent] Building prompt for task {task.task_id}")
                user_prompt = self._build_prompt(task, current_state, current_metrics)
                if self.logger:
                    self.logger.logger.debug(f"[ThermalAgent] Prompt built successfully, length: {len(user_prompt)}")
            except Exception as prompt_error:
                import traceback
                error_details = traceback.format_exc()
                if self.logger:
                    self.logger.logger.error(f"[ThermalAgent] Failed to build prompt:\n{error_details}")
                raise LLMError(f"Failed to build prompt: {prompt_error}")

            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 记录请求
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] Logging LLM request for task {task.task_id}")
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="thermal_agent",
                    request={"messages": messages},
                    response=None
                )

            # 调用LLM
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] Calling LLM API")
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
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] LLM API call successful")

            response_text = response.output.choices[0].message.content
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] Response text length: {len(response_text)}")

            response_json = json.loads(response_text)
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] JSON parsed successfully")

            # 记录响应
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] Logging LLM response")
                self.logger.log_llm_interaction(
                    iteration=iteration,
                    role="thermal_agent",
                    request=None,
                    response=response_json
                )

            # 构建Proposal
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] Creating ThermalProposal from JSON")
            proposal = ThermalProposal(**response_json)
            if self.logger:
                self.logger.logger.debug(f"[ThermalAgent] ThermalProposal created successfully")

            # 自动生成ID
            if not proposal.proposal_id or proposal.proposal_id.startswith("THERM_PROP"):
                proposal.proposal_id = f"THERM_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            proposal.task_id = task.task_id

            return proposal

        except Exception as e:
            raise LLMError(f"Thermal Agent failed: {e}")

    def _build_prompt(
        self,
        task: AgentTask,
        current_state: DesignState,
        current_metrics: ThermalMetrics
    ) -> str:
        """构建用户提示"""
        prompt = f"""# 热控优化任务

## 任务信息
- 任务ID: {task.task_id}
- 目标: {task.objective}
- 优先级: {task.priority}

## 约束条件
"""
        for i, constraint in enumerate(task.constraints, 1):
            prompt += f"{i}. {constraint}\n"

        prompt += f"""
## 当前热控状态
- 温度范围: {float(current_metrics.min_temp):.1f}°C ~ {float(current_metrics.max_temp):.1f}°C
- 平均温度: {float(current_metrics.avg_temp):.1f}°C
- 最大温度梯度: {float(current_metrics.temp_gradient):.2f}°C/m
"""
        if current_metrics.hotspot_components:
            prompt += f"- 热点组件: {', '.join(current_metrics.hotspot_components)}\n"

        prompt += "\n## 组件功耗信息\n"
        # 添加高功耗组件信息
        high_power_comps = [c for c in current_state.components if c.power > 5.0]
        for comp in high_power_comps[:5]:
            prompt += f"- {comp.id}: {float(comp.power):.1f}W, 位置 {comp.position}\n"

        # 添加完整的可用组件列表（防止幻觉）
        prompt += "\n## 可用组件列表（仅可引用以下组件ID）\n"
        for comp in current_state.components:
            prompt += f"- {comp.id} (类别: {comp.category})\n"
        prompt += "\n⚠️ 重要：在 SET_THERMAL_CONTACT 中，contact_component 参数必须是上述列表中的组件ID，不能使用不存在的组件名称（如 chassis, main_structure, payload_heavy_mount 等）！\n"

        # 添加任务上下文
        if task.context:
            prompt += "\n## 额外上下文\n"
            for key, value in task.context.items():
                prompt += f"- {key}: {value}\n"

        prompt += "\n请生成热控优化提案（JSON格式）。"

        return prompt

    def validate_proposal(
        self,
        proposal: ThermalProposal,
        current_state: DesignState
    ) -> Dict[str, Any]:
        """
        验证提案的可行性

        Args:
            proposal: 热控提案
            current_state: 当前设计状态

        Returns:
            验证结果
        """
        issues = []
        warnings = []

        component_ids = [c.id for c in current_state.components]

        # 检查操作的有效性
        for action in proposal.actions:
            # 检查目标组件是否存在
            for comp_id in action.target_components:
                if comp_id not in component_ids:
                    issues.append(f"组件 {comp_id} 不存在")

            # 检查参数合理性
            if action.op_type == "ADJUST_LAYOUT":
                # ADJUST_LAYOUT 参数: {"axis": "Y", "range": [50, 80]}
                axis = action.parameters.get("axis")
                valid_axes = ["X", "Y", "Z"]
                if axis not in valid_axes:
                    issues.append(f"无效的轴: {axis}，必须是 X, Y, Z 之一")

                range_param = action.parameters.get("range")
                if range_param and not isinstance(range_param, list):
                    warnings.append(f"range 参数应为列表: {range_param}")

            elif action.op_type == "CHANGE_ORIENTATION":
                # CHANGE_ORIENTATION 参数: {"axis": "Z", "angle": 90}
                axis = action.parameters.get("axis")
                valid_axes = ["X", "Y", "Z"]
                if axis not in valid_axes:
                    issues.append(f"无效的轴: {axis}，必须是 X, Y, Z 之一")

            elif action.op_type == "ADD_HEATSINK":
                # ADD_HEATSINK 参数: {"face": "+Y", "thickness": 3.0, "conductivity": 400.0}
                face = action.parameters.get("face")
                valid_faces = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
                if face and face not in valid_faces:
                    warnings.append(f"未知的散热器面: {face}")

            elif action.op_type == "MODIFY_COATING":
                # MODIFY_COATING 参数: {"emissivity": 0.85, "absorptivity": 0.3, "coating_type": "high_emissivity"}
                emissivity = action.parameters.get("emissivity")
                if emissivity and (emissivity < 0 or emissivity > 1):
                    warnings.append(f"发射率超出范围 [0, 1]: {emissivity}")

                absorptivity = action.parameters.get("absorptivity")
                if absorptivity and (absorptivity < 0 or absorptivity > 1):
                    warnings.append(f"吸收率超出范围 [0, 1]: {absorptivity}")

            elif action.op_type == "SET_THERMAL_CONTACT":
                # SET_THERMAL_CONTACT 参数: {"contact_component": "battery_01", "conductance": 1000.0, "gap": 0.5}
                contact_comp = action.parameters.get("contact_component")
                if contact_comp and contact_comp not in component_ids:
                    issues.append(f"接触组件 {contact_comp} 不存在")

        # 检查预测指标的合理性
        if proposal.predicted_metrics.max_temp < proposal.predicted_metrics.min_temp:
            issues.append("预测的最高温度低于最低温度，不合理")

        if proposal.predicted_metrics.max_temp > 100 or proposal.predicted_metrics.min_temp < -50:
            warnings.append("预测温度超出典型卫星工作范围")

        # 检查置信度
        if proposal.confidence < 0.3:
            warnings.append(f"置信度过低 ({proposal.confidence:.2f})，建议谨慎执行")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings
        }


if __name__ == "__main__":
    print("Testing Thermal Agent...")

    # 创建示例任务
    task = AgentTask(
        task_id="TASK_002",
        agent_type="thermal",
        objective="降低Battery_01温度至55°C以下",
        constraints=[
            "不增加质量>100g",
            "保持现有布局尽量不变"
        ],
        priority=1,
        context={
            "current_temp": 68.5,
            "target_temp": 55.0
        }
    )

    print(f"✓ Task created: {task.objective}")
