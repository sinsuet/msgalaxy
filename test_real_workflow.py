#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
真实工作流测试 - 端到端验证

测试完整流程：
1. BOM解析
2. 几何布局
3. COMSOL仿真
4. LLM语义推理
5. 可视化生成
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

from workflow.orchestrator import WorkflowOrchestrator
from core.logger import get_logger

def main():
    """运行真实工作流测试"""

    print("=" * 80)
    print("MsGalaxy Real Workflow Test")
    print("=" * 80)
    print()

    # 设置日志
    logger = get_logger("real_workflow_test")

    # 检查COMSOL模型
    comsol_model = "e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph"
    if not Path(comsol_model).exists():
        print(f"[ERROR] COMSOL model not found: {comsol_model}")
        return 1

    print(f"[OK] COMSOL model: {comsol_model}")

    # 检查API Key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY not set in environment or .env file")
        print("       LLM functionality will not work")
        print()
    else:
        print(f"[OK] API Key loaded: {api_key[:10]}...{api_key[-4:]}")
    print()

    # 创建工作流编排器
    print("[INIT] Initializing workflow orchestrator...")
    try:
        orchestrator = WorkflowOrchestrator("config/system.yaml")
        print(f"[OK] Orchestrator initialized")
        print(f"     - LLM model: {orchestrator.config['openai']['model']}")
        print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
        print(f"     - Max iterations: {orchestrator.config['optimization']['max_iterations']}")
    except Exception as e:
        print(f"[ERROR] Failed to initialize orchestrator: {e}")
        import traceback
        traceback.print_exc()
        return 1
    print()

    # 运行优化（限制迭代次数以加快测试）
    print("[START] Running optimization workflow...")
    print("-" * 80)

    try:
        # 修改配置以加快测试
        orchestrator.config['optimization']['max_iterations'] = 3  # 只运行3次迭代

        final_state = orchestrator.run_optimization(
            bom_file="config/bom_example.json",
            max_iterations=3
        )

        print()
        print("-" * 80)
        print("[SUCCESS] Optimization completed!")
        print()

        # 显示最终结果
        print("[RESULT] Final design state:")
        print(f"         - Iteration: {final_state.iteration}")
        print(f"         - Components: {len(final_state.components)}")

        if 'last_simulation' in final_state.metadata:
            sim_result = final_state.metadata['last_simulation']
            print(f"         - Max temp: {sim_result.get('max_temp', 'N/A')} C")
            print(f"         - Avg temp: {sim_result.get('avg_temp', 'N/A')} C")
            print(f"         - Min clearance: {sim_result.get('min_clearance', 'N/A')} mm")

        print()

        # 检查可视化文件
        print("[CHECK] Visualization files...")
        exp_dir = Path(orchestrator.logger.run_dir)

        viz_files = list(exp_dir.glob("*.png"))
        if viz_files:
            print(f"[OK] Found {len(viz_files)} visualization files:")
            for viz_file in sorted(viz_files):
                size_kb = viz_file.stat().st_size / 1024
                print(f"     - {viz_file.name} ({size_kb:.1f} KB)")
        else:
            print("[WARN] No visualization files found")

        print()

        # 检查日志文件
        print("[CHECK] Log files...")
        log_files = list(exp_dir.glob("*.log"))
        if log_files:
            print(f"[OK] Found {len(log_files)} log files:")
            for log_file in sorted(log_files):
                size_kb = log_file.stat().st_size / 1024
                print(f"     - {log_file.name} ({size_kb:.1f} KB)")
        else:
            print("[WARN] No log files found")

        print()

        # 检查LLM交互记录
        print("[CHECK] LLM interaction logs...")
        llm_files = list(exp_dir.glob("llm_*.json"))
        if llm_files:
            print(f"[OK] Found {len(llm_files)} LLM interaction logs:")
            for llm_file in sorted(llm_files):
                size_kb = llm_file.stat().st_size / 1024
                print(f"     - {llm_file.name} ({size_kb:.1f} KB)")
        else:
            print("[WARN] No LLM interaction logs found")

        print()

        # 检查仿真结果文件
        print("[CHECK] Simulation result files...")
        sim_files = list(exp_dir.glob("simulation_*.json"))
        if sim_files:
            print(f"[OK] Found {len(sim_files)} simulation result files:")
            for sim_file in sorted(sim_files):
                size_kb = sim_file.stat().st_size / 1024
                print(f"     - {sim_file.name} ({size_kb:.1f} KB)")
        else:
            print("[WARN] No simulation result files found")

        print()
        print("=" * 80)
        print("[SUCCESS] Real workflow test completed!")
        print(f"[INFO] Experiment directory: {exp_dir}")
        print("=" * 80)

        return 0

    except Exception as e:
        print()
        print("=" * 80)
        print(f"[ERROR] Test failed: {e}")
        print("=" * 80)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
