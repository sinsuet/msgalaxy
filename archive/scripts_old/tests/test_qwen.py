"""
Qwen API测试脚本

用于测试Qwen API连接和基本功能
"""

import sys
import io
from openai import OpenAI

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    try:
        # Python 3.7+ 方法
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python 3.6 及更早版本
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def test_qwen_connection(api_key: str):
    """测试Qwen API连接"""
    print("="*60)
    print("测试Qwen API连接")
    print("="*60)

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        print("\n[1/3] 发送测试请求...")
        response = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": "你是一个卫星设计专家。"},
                {"role": "user", "content": "请用一句话介绍卫星热控系统的作用。"}
            ],
            temperature=0.7
        )

        print("[OK] API连接成功！")
        print(f"\n[2/3] 模型响应:")
        print(f"  {response.choices[0].message.content}")

        print(f"\n[3/3] Token使用:")
        print(f"  输入: {response.usage.prompt_tokens} tokens")
        print(f"  输出: {response.usage.completion_tokens} tokens")
        print(f"  总计: {response.usage.total_tokens} tokens")

        print("\n" + "="*60)
        print("[OK] Qwen API测试通过！")
        print("="*60)

        return True

    except Exception as e:
        print(f"\n[FAIL] API测试失败: {e}")
        print("\n请检查:")
        print("  1. API密钥是否正确")
        print("  2. 网络连接是否正常")
        print("  3. 账户余额是否充足")
        return False


def test_meta_reasoner(api_key: str):
    """测试Meta-Reasoner与Qwen集成"""
    print("\n" + "="*60)
    print("测试Meta-Reasoner与Qwen集成")
    print("="*60)

    try:
        from optimization.meta_reasoner import MetaReasoner
        from optimization.protocol import (
            GlobalContextPack,
            GeometryMetrics,
            ThermalMetrics,
            StructuralMetrics,
            PowerMetrics,
            ViolationItem
        )

        print("\n[1/4] 初始化Meta-Reasoner...")
        reasoner = MetaReasoner(
            api_key=api_key,
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.7
        )
        print("[OK] Meta-Reasoner初始化成功")

        print("\n[2/4] 创建测试上下文...")
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
                temp_gradient=2.5,
                hotspot_components=[]
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
            history_summary="第1次迭代"
        )
        print("[OK] 上下文创建成功")

        print("\n[3/4] 调用Meta-Reasoner生成战略计划...")
        print("  (这可能需要几秒钟...)")
        plan = reasoner.generate_strategic_plan(context)

        print("[OK] 战略计划生成成功！")
        print(f"\n[4/4] 计划详情:")
        print(f"  计划ID: {plan.plan_id}")
        print(f"  策略类型: {plan.strategy_type}")
        print(f"  任务数量: {len(plan.tasks)}")
        print(f"  推理摘要: {plan.reasoning[:100]}...")

        print("\n" + "="*60)
        print("[OK] Meta-Reasoner测试通过！")
        print("="*60)

        return True

    except Exception as e:
        print(f"\n[FAIL] Meta-Reasoner测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("\n" + "="*60)
    print("Qwen API 测试工具")
    print("="*60)

    # 获取API密钥
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        print("\n请输入你的Qwen API密钥:")
        print("(格式: sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx)")
        api_key = input("> ").strip()

    if not api_key or not api_key.startswith("sk-"):
        print("\n[ERROR] 无效的API密钥格式")
        print("API密钥应以 'sk-' 开头")
        sys.exit(1)

    # 运行测试
    print(f"\n使用API密钥: {api_key[:10]}...{api_key[-4:]}")

    # 测试1: 基础连接
    if not test_qwen_connection(api_key):
        print("\n[FAIL] 基础连接测试失败，停止后续测试")
        sys.exit(1)

    # 测试2: Meta-Reasoner集成（自动运行）
    if test_meta_reasoner(api_key):
        print("\n" + "="*60)
        print("[SUCCESS] 所有测试通过！")
        print("="*60)
        print("\n下一步:")
        print("  1. 将API密钥添加到 config/system.yaml")
        print("  2. 运行: python -m api.cli optimize")
    else:
        print("\n[FAIL] Meta-Reasoner测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
