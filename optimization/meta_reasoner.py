"""
Meta-Reasoner: 战略层元推理器

负责顶层决策：
1. 多学科协调 - 平衡几何、热控、结构、电源等约束
2. 探索策略制定 - 决定优化方向（局部搜索 vs 全局重构）
3. 冲突解决 - 提供多约束权衡方案
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
import yaml
from pathlib import Path

from .protocol import (
    GlobalContextPack,
    StrategicPlan,
    AgentTask,
    ViolationItem,
)
from core.logger import ExperimentLogger
from core.exceptions import LLMError


class MetaReasoner:
    """元推理器 - 战略决策层"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        temperature: float = 0.7,
        base_url: Optional[str] = None,  # 保留兼容性，但不使用
        logger: Optional[ExperimentLogger] = None
    ):
        """
        初始化Meta-Reasoner

        Args:
            api_key: API密钥（DashScope API Key）
            model: 使用的模型（qwen-plus, qwen-max等）
            temperature: 温度参数（0.0-1.0），控制创造性
            base_url: 保留兼容性参数（不使用）
            logger: 实验日志记录器
        """
        # 设置 DashScope API Key
        dashscope.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.logger = logger

        # 加载系统提示词
        self.system_prompt = self._load_system_prompt()

        # Few-shot示例
        self.few_shot_examples = self._load_few_shot_examples()

    def _load_system_prompt(self) -> str:
        """加载系统提示词"""
        return """你是卫星设计优化系统的首席架构师（Meta-Reasoner）。

【角色定位】
- 你不直接修改设计参数，而是制定优化策略并协调专业Agent
- 你需要平衡多个学科的约束，做出权衡决策
- 你的决策必须有明确的工程依据

【核心能力】
1. 多学科协调：理解几何、热控、结构、电源之间的耦合关系
2. 战略规划：选择合适的优化策略（局部搜索/全局重构/混合）
3. 任务分解：将复杂问题分解为可执行的Agent任务
4. 风险评估：预测优化方案的潜在风险

【输入信息】
1. 当前设计状态（几何布局、仿真结果）
2. 约束违反情况（几何干涉、热超标、结构应力等）
3. 历史优化轨迹（避免重复失败的尝试）
4. 检索到的工程知识（相关规范、案例）

【输出要求】
你必须输出一个JSON格式的StrategicPlan，包含以下字段：

```json
{
  "plan_id": "PLAN_YYYYMMDD_NNN",
  "reasoning": "详细的Chain-of-Thought推理过程：\\n1. 问题诊断：当前问题的根本原因是什么？\\n2. 策略选择：为什么选择这个策略而不是其他策略？\\n3. 预期效果：预期会产生什么连锁反应？",
  "strategy_type": "local_search | global_reconfig | hybrid",
  "strategy_description": "策略的简要描述",
  "tasks": [
    {
      "task_id": "TASK_001",
      "agent_type": "geometry | thermal | structural | power",
      "objective": "任务目标的自然语言描述",
      "constraints": ["约束条件1", "约束条件2"],
      "priority": 1-5,
      "context": {"额外上下文信息"}
    }
  ],
  "expected_improvements": {
    "max_temp": -5.0,
    "min_clearance": 2.0
  },
  "risks": ["风险1", "风险2"]
}
```

【策略类型说明】
1. **local_search**: 局部微调
   - 适用场景：接近可行解，只有少量约束违反
   - 特点：小步迭代，风险低，收敛快
   - 示例：微调组件位置以消除干涉

2. **global_reconfig**: 全局重构
   - 适用场景：严重违反约束，局部调整无效
   - 特点：大幅改动，风险高，可能突破局部最优
   - 示例：重新规划整体布局

3. **hybrid**: 混合策略
   - 适用场景：部分区域需要重构，部分区域可微调
   - 特点：平衡风险与收益
   - 示例：重构热点区域，保持其他区域不变

【约束规则】
1. 不得违反物理定律（如质心必须在支撑范围内）
2. 优先保证安全裕度（不仅满足约束，还要留有余量）
3. 考虑制造可行性（避免过于复杂的结构）
4. 尊重历史经验（避免重复已失败的尝试）

【决策原则】
1. **安全第一**: 结构安全 > 热控 > 性能优化
2. **渐进式**: 优先尝试风险低的方案
3. **可追溯**: 每个决策都要有明确的工程依据
4. **可回滚**: 考虑失败后的回退方案
"""

    def _load_few_shot_examples(self) -> List[Dict[str, str]]:
        """加载Few-Shot示例"""
        return [
            {
                "user": """# 卫星设计优化 - 第3次迭代

## 1. 当前设计状态
电池组(Battery_01)位于X=13.0mm, 与结构肋板(Rib_01)在X=10.0mm处间隙仅3.0mm

## 2. 多学科性能指标
### 几何指标
- 最小间隙: 3.0 mm (阈值: 3.0 mm)
- 质心偏移: [0.5, -0.2, 0.1] mm
- 碰撞数量: 0

### 热控指标
- 温度范围: 18.5°C ~ 58.2°C
- 平均温度: 35.6°C

## 3. 约束违反情况
- [重要] 电池与肋板间隙不足 (当前值: 3.00, 阈值: 3.00)

## 4. 历史轨迹
- Iter 1: 尝试向-X移动电池，导致与其他组件干涉
- Iter 2: 尝试向+X移动电池5mm，成功但接近边界""",
                "assistant": """{
  "plan_id": "PLAN_20260215_003",
  "reasoning": "问题诊断：电池与肋板间隙恰好在阈值边缘（3.0mm），存在数值容差风险。历史记录显示-X方向会导致新的干涉，+X方向已尝试过5mm且成功。\\n\\n策略选择：采用local_search策略，继续沿+X方向探索更大范围（5-10mm），原因：(1)历史证明+X方向可行；(2)当前无热控或结构问题，无需全局重构；(3)小步迭代风险低。\\n\\n预期效果：间隙增加到5-8mm，提供足够安全裕度。可能的副作用：质心略微向+X偏移，但当前偏移量很小（0.5mm），有充足余量。",
  "strategy_type": "local_search",
  "strategy_description": "沿+X方向继续移动电池，扩大与肋板的安全间隙",
  "tasks": [
    {
      "task_id": "TASK_003_001",
      "agent_type": "geometry",
      "objective": "将Battery_01沿+X方向移动，使其与Rib_01的间隙达到5-8mm",
      "constraints": [
        "移动后质心偏移不得超过±10mm",
        "不得与其他组件产生新的干涉",
        "移动范围限制在包络内"
      ],
      "priority": 1,
      "context": {
        "current_position": 13.0,
        "target_clearance": 6.0,
        "search_range": [5.0, 10.0]
      }
    }
  ],
  "expected_improvements": {
    "min_clearance": 3.0
  },
  "risks": [
    "质心可能向+X偏移约0.3-0.5mm，需监控",
    "如果移动超过10mm可能接近包络边界"
  ]
}"""
            },
            {
                "user": """# 卫星设计优化 - 第8次迭代

## 2. 多学科性能指标
### 几何指标
- 最小间隙: 5.2 mm
- 质心偏移: [1.2, 0.8, -0.3] mm
- 碰撞数量: 0

### 热控指标
- 温度范围: 22.3°C ~ 68.5°C
- 平均温度: 42.1°C
- 热点组件: Battery_01, PowerModule_02

## 3. 约束违反情况
- [严重] 电池温度超标 (当前值: 68.5, 阈值: 60.0)
- [严重] 电源模块温度超标 (当前值: 65.2, 阈值: 60.0)

## 4. 历史轨迹
- Iter 5-7: 多次尝试微调电池位置，温度仍超标
- 检索到的知识：[GJB 5236] 高功耗组件应安装在±Y面以利用辐射散热""",
                "assistant": """{
  "plan_id": "PLAN_20260215_008",
  "reasoning": "问题诊断：电池和电源模块温度严重超标（>8°C），历史记录显示局部微调无效，说明当前布局的散热路径存在根本性问题。检索到的工程规范明确指出高功耗组件应安装在±Y面。\\n\\n策略选择：采用global_reconfig策略，将高功耗组件重新布局到±Y面。原因：(1)局部调整已证明无效；(2)工程规范提供了明确指导；(3)温度超标8°C属于严重问题，必须采取根本性措施。\\n\\n预期效果：通过改善散热路径，温度预计降低10-15°C。风险：大幅度移动可能影响质心和布线，需要多学科协同。",
  "strategy_type": "global_reconfig",
  "strategy_description": "将高功耗组件（电池、电源模块）重新布局到±Y面，改善散热路径",
  "tasks": [
    {
      "task_id": "TASK_008_001",
      "agent_type": "thermal",
      "objective": "分析将Battery_01和PowerModule_02移至±Y面的散热效果",
      "constraints": [
        "目标温度<55°C（留5°C安全裕度）",
        "考虑辐射散热面积"
      ],
      "priority": 1,
      "context": {
        "current_temps": {"Battery_01": 68.5, "PowerModule_02": 65.2},
        "target_face": "±Y"
      }
    },
    {
      "task_id": "TASK_008_002",
      "agent_type": "geometry",
      "objective": "规划Battery_01和PowerModule_02到±Y面的布局方案",
      "constraints": [
        "保持质心偏移<±10mm",
        "避免与现有组件干涉",
        "优先选择+Y面（更大散热面积）"
      ],
      "priority": 2,
      "context": {
        "components_to_move": ["Battery_01", "PowerModule_02"],
        "target_face": "+Y"
      }
    },
    {
      "task_id": "TASK_008_003",
      "agent_type": "power",
      "objective": "评估重新布局后的电源线路长度和压降",
      "constraints": [
        "压降<0.5V",
        "线路长度增加<20%"
      ],
      "priority": 3,
      "context": {}
    }
  ],
  "expected_improvements": {
    "max_temp": -12.0,
    "avg_temp": -6.0
  },
  "risks": [
    "大幅度移动可能导致质心偏移增加2-3mm",
    "电源线路可能需要重新规划，增加复杂度",
    "如果+Y面空间不足，可能需要调整其他组件"
  ]
}"""
            }
        ]

    def generate_strategic_plan(
        self,
        context: GlobalContextPack
    ) -> StrategicPlan:
        """
        生成战略计划

        Args:
            context: 全局上下文包

        Returns:
            StrategicPlan: 战略计划

        Raises:
            LLMError: LLM调用失败
        """
        try:
            # 构建消息
            messages = [
                {"role": "system", "content": self.system_prompt}
            ]

            # 添加Few-Shot示例
            for example in self.few_shot_examples:
                messages.append({"role": "user", "content": example["user"]})
                messages.append({"role": "assistant", "content": example["assistant"]})

            # 添加当前上下文
            user_prompt = context.to_markdown_prompt()
            messages.append({"role": "user", "content": user_prompt})

            # 记录请求
            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="meta_reasoner",
                    request={"messages": messages, "model": self.model},
                    response=None
                )

            # 调用 DashScope API
            response = dashscope.Generation.call(
                model=self.model,
                messages=messages,
                result_format='message',  # 使用 message 格式
                temperature=self.temperature,
                response_format={'type': 'json_object'}  # 字典格式
            )

            # 检查响应状态
            if response.status_code != HTTPStatus.OK:
                raise LLMError(f"DashScope API 调用失败: {response.code} - {response.message}")

            # 解析响应
            response_text = response.output.choices[0].message.content
            response_json = json.loads(response_text)

            # 记录响应
            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="meta_reasoner",
                    request=None,
                    response=response_json
                )

            # 确保response_json包含iteration字段
            if 'iteration' not in response_json:
                response_json['iteration'] = context.iteration

            # 验证并构建StrategicPlan
            plan = StrategicPlan(**response_json)

            # 自动生成plan_id（如果LLM没有提供）
            if not plan.plan_id or plan.plan_id.startswith("PLAN_YYYYMMDD"):
                plan.plan_id = f"PLAN_{datetime.now().strftime('%Y%m%d')}_{context.iteration:03d}"

            return plan

        except json.JSONDecodeError as e:
            raise LLMError(f"Failed to parse LLM response as JSON: {e}")
        except Exception as e:
            raise LLMError(f"Meta-Reasoner failed: {e}")

    def evaluate_plan_quality(self, plan: StrategicPlan) -> Dict[str, Any]:
        """
        评估战略计划的质量

        Args:
            plan: 战略计划

        Returns:
            评估结果字典
        """
        quality_score = 0.0
        issues = []

        # 检查推理过程的完整性
        if len(plan.reasoning) < 100:
            issues.append("推理过程过于简短，缺乏详细分析")
        else:
            quality_score += 0.3

        # 检查任务分配的合理性
        if len(plan.tasks) == 0:
            issues.append("未分配任何任务")
        elif len(plan.tasks) > 5:
            issues.append("任务过多，可能导致协调困难")
        else:
            quality_score += 0.3

        # 检查预期改进的具体性
        if len(plan.expected_improvements) == 0:
            issues.append("未明确预期改进指标")
        else:
            quality_score += 0.2

        # 检查风险评估
        if len(plan.risks) == 0:
            issues.append("未进行风险评估")
        else:
            quality_score += 0.2

        return {
            "quality_score": quality_score,
            "issues": issues,
            "is_acceptable": quality_score >= 0.6
        }

    def refine_plan(
        self,
        plan: StrategicPlan,
        feedback: str
    ) -> StrategicPlan:
        """
        根据反馈优化战略计划

        Args:
            plan: 原始计划
            feedback: 反馈信息

        Returns:
            优化后的计划
        """
        # 构建优化提示
        refinement_prompt = f"""
原始计划：
{json.dumps(plan.model_dump(), indent=2, ensure_ascii=False)}

反馈意见：
{feedback}

请根据反馈优化计划，输出新的JSON格式的StrategicPlan。
"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": refinement_prompt}
        ]

        try:
            response = dashscope.Generation.call(
                model=self.model,
                messages=messages,
                result_format='message',
                temperature=self.temperature,
                response_format={'type': 'json_object'}
            )

            if response.status_code != HTTPStatus.OK:
                raise LLMError(f"DashScope API 调用失败: {response.code} - {response.message}")

            response_json = json.loads(response.output.choices[0].message.content)
            refined_plan = StrategicPlan(**response_json)
            refined_plan.plan_id = f"{plan.plan_id}_refined"

            return refined_plan

        except Exception as e:
            raise LLMError(f"Plan refinement failed: {e}")


if __name__ == "__main__":
    # 测试Meta-Reasoner
    print("Testing Meta-Reasoner...")

    # 创建示例上下文
    from optimization.protocol import GeometryMetrics, ThermalMetrics, StructuralMetrics, PowerMetrics

    context = GlobalContextPack(
        iteration=1,
        design_state_summary="电池组位于X=13.0mm，与肋板间隙3.0mm",
        geometry_metrics=GeometryMetrics(
            min_clearance=3.0,
            com_offset=[0.5, -0.2, 0.1],
            moment_of_inertia=[1.2, 1.3, 1.1],
            packing_efficiency=75.0,
            num_collisions=0
        ),
        thermal_metrics=ThermalMetrics(
            max_temp=58.2,
            min_temp=18.5,
            avg_temp=35.6,
            temp_gradient=2.5
        ),
        structural_metrics=StructuralMetrics(
            max_stress=45.0,
            max_displacement=0.12,
            first_modal_freq=85.0,
            safety_factor=2.1
        ),
        power_metrics=PowerMetrics(
            total_power=120.0,
            peak_power=150.0,
            power_margin=25.0,
            voltage_drop=0.3
        ),
        violations=[
            ViolationItem(
                violation_id="V001",
                violation_type="geometry",
                severity="major",
                description="电池与肋板间隙不足",
                affected_components=["Battery_01", "Rib_01"],
                metric_value=3.0,
                threshold=3.0
            )
        ],
        history_summary="Iter 1: 尝试向-X移动，失败"
    )

    print("\n✓ Context created successfully!")
    print(f"Violations: {len(context.violations)}")
    print(f"Markdown prompt length: {len(context.to_markdown_prompt())} chars")
