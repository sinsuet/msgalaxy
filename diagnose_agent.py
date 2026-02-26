#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Agent深度诊断脚本
逐步测试Agent的每个组件，找出格式化错误的确切位置
"""

import sys
import traceback
sys.path.insert(0, '.')

from optimization.protocol import (
    AgentTask, ThermalMetrics, GeometryMetrics
)
from core.protocol import (
    DesignState, ComponentGeometry, Vector3D, Envelope
)

print("=" * 80)
print("Agent Deep Diagnosis")
print("=" * 80)

# 1. 创建测试数据
print("\n[Step 1] Creating test data...")
try:
    comp = ComponentGeometry(
        id='battery_01',
        position=Vector3D(x=-120.37, y=-128.92, z=-123.27),
        dimensions=Vector3D(x=200.0, y=150.0, z=100.0),
        mass=5.0,
        power=50.0,
        category='power'
    )

    envelope = Envelope(
        outer_size=Vector3D(x=290.74, y=307.84, z=256.53)
    )

    state = DesignState(
        iteration=1,
        components=[comp],
        envelope=envelope
    )

    print("[OK] Test data created")
    print(f"  Component: {comp.id}, power={comp.power}, mass={comp.mass}")
except Exception as e:
    print(f"[FAIL] Failed to create test data: {e}")
    traceback.print_exc()
    sys.exit(1)

# 2. 创建Task
print("\n[Step 2] Creating AgentTask...")
try:
    task = AgentTask(
        task_id='TEST_001',
        agent_type='thermal',
        objective='Test thermal optimization',
        constraints=['Constraint 1', 'Constraint 2'],
        priority=1,
        context={'test_key': 'test_value', 'number': 123}
    )
    print("[OK] AgentTask created")
    print(f"  Task ID: {task.task_id}, priority={task.priority}")
except Exception as e:
    print(f"[FAIL] Failed to create task: {e}")
    traceback.print_exc()
    sys.exit(1)

# 3. 创建Metrics
print("\n[Step 3] Creating ThermalMetrics...")
try:
    metrics = ThermalMetrics(
        max_temp=221735840.05,
        min_temp=0.0,
        avg_temp=69048346.99,
        temp_gradient=0.0,
        hotspot_components=['battery_01']
    )
    print("[OK] ThermalMetrics created")
    print(f"  Max temp: {metrics.max_temp}")
except Exception as e:
    print(f"[FAIL] Failed to create metrics: {e}")
    traceback.print_exc()
    sys.exit(1)

# 4. 测试_build_prompt (不调用LLM)
print("\n[Step 4] Testing _build_prompt...")
try:
    from optimization.agents.thermal_agent import ThermalAgent

    # 创建agent (不需要真实API key)
    agent = ThermalAgent(api_key='test_key', model='test_model')

    # 调用_build_prompt
    prompt = agent._build_prompt(task, state, metrics)

    print("[OK] _build_prompt succeeded")
    print(f"  Prompt length: {len(prompt)} chars")
    print(f"  First 200 chars: {prompt[:200]}...")

except Exception as e:
    print(f"[FAIL] _build_prompt failed: {e}")
    print("\n[ERROR DETAILS]")
    traceback.print_exc()

    # 尝试找出具体哪一行出错
    print("\n[DETAILED ANALYSIS]")
    try:
        # 测试基本信息
        test_str = f"Task ID: {task.task_id}\n"
        test_str += f"Objective: {task.objective}\n"
        test_str += f"Priority: {task.priority}\n"
        print("[OK] Basic info formatting works")

        # 测试约束条件
        for i, constraint in enumerate(task.constraints, 1):
            test_str += f"{i}. {constraint}\n"
        print("[OK] Constraints formatting works")

        # 测试metrics格式化
        test_str += f"Max temp: {metrics.max_temp:.1f}\n"
        test_str += f"Min temp: {metrics.min_temp:.1f}\n"
        test_str += f"Avg temp: {metrics.avg_temp:.1f}\n"
        test_str += f"Gradient: {metrics.temp_gradient:.2f}\n"
        print("[OK] Metrics formatting works")

        # 测试组件信息
        for comp in state.components:
            test_str += f"Component: {comp.id}, power={comp.power:.1f}W\n"
            test_str += f"Position: {comp.position}\n"
        print("[OK] Component formatting works")

        # 测试context
        if task.context:
            for key, value in task.context.items():
                test_str += f"{key}: {value}\n"
        print("[OK] Context formatting works")

    except Exception as detail_error:
        print(f"[FAIL] Detailed test failed at: {detail_error}")
        traceback.print_exc()

    sys.exit(1)

# 5. 测试GeometryAgent
print("\n[Step 5] Testing GeometryAgent._build_prompt...")
try:
    from optimization.agents.geometry_agent import GeometryAgent

    geom_metrics = GeometryMetrics(
        min_clearance=5.0,
        com_offset=[0.0, 0.0, 0.0],
        moment_of_inertia=[100.0, 100.0, 100.0],
        packing_efficiency=75.0,
        num_collisions=0
    )

    geom_task = AgentTask(
        task_id='TEST_002',
        agent_type='geometry',
        objective='Test geometry optimization',
        constraints=['Constraint 1'],
        priority=2
    )

    geom_agent = GeometryAgent(api_key='test_key', model='test_model')
    geom_prompt = geom_agent._build_prompt(geom_task, state, geom_metrics)

    print("[OK] GeometryAgent._build_prompt succeeded")
    print(f"  Prompt length: {len(geom_prompt)} chars")

except Exception as e:
    print(f"[FAIL] GeometryAgent._build_prompt failed: {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 80)
print("[SUCCESS] All tests passed!")
print("=" * 80)
