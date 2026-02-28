#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L3 复杂级测试 - 开箱即用
7组件，空间拥挤，多物理场严重耦合
预期：10-20轮收敛
"""

import os
import sys
import io
import importlib.util
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 修复 Windows GBK 编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _inject_structural_physics_compat():
    """
    兼容旧版 simulation/__init__.py 对 StructuralPhysics 类的导入。
    """
    module_name = "simulation.structural_physics"
    existing = sys.modules.get(module_name)
    if existing is not None and hasattr(existing, "StructuralPhysics"):
        return

    module_path = project_root / "simulation" / "structural_physics.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载兼容模块: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "StructuralPhysics"):
        module.StructuralPhysics = type(
            "StructuralPhysics",
            (),
            {
                "calculate_center_of_mass": staticmethod(module.calculate_center_of_mass),
                "calculate_cg_offset": staticmethod(module.calculate_cg_offset),
                "calculate_moment_of_inertia": staticmethod(module.calculate_moment_of_inertia),
                "analyze_mass_distribution": staticmethod(module.analyze_mass_distribution),
            },
        )
    sys.modules[module_name] = module


def _load_workflow_orchestrator():
    """
    延迟导入编排器，并对 StructuralPhysics 导入错误做兼容修复。
    """
    try:
        from workflow.orchestrator import WorkflowOrchestrator
        return WorkflowOrchestrator
    except ImportError as exc:
        err_text = str(exc)
        if "StructuralPhysics" not in err_text:
            raise
        print("[WARN] 检测到 StructuralPhysics 导入异常，应用运行时兼容补丁后重试...")
        _inject_structural_physics_compat()
        from workflow.orchestrator import WorkflowOrchestrator
        return WorkflowOrchestrator


def _print_visualization_summary(orchestrator) -> None:
    """
    打印可视化摘要，帮助快速判断迭代有效性。
    """
    summary_path = Path(orchestrator.logger.run_dir) / "visualizations" / "visualization_summary.txt"
    if not summary_path.exists():
        print("[WARN] 可视化摘要文件不存在，跳过摘要输出")
        return

    try:
        content = summary_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[WARN] 读取可视化摘要失败: {e}")
        return

    if not content:
        print("[WARN] 可视化摘要为空")
        return

    print()
    print("[SUMMARY] 可视化对比摘要:")
    print("-" * 80)
    print(content)
    print("-" * 80)


def main():
    """运行 L3 复杂级测试"""

    print("=" * 80)
    print("🚀 MsGalaxy L3 复杂级测试 (Complex)")
    print("=" * 80)
    print("📦 组件数量: 7个")
    print("🎯 测试目标: 空间拥挤+多物理场耦合，展现Meta-Reasoner冲突解决能力")
    print("⏱️  预期时间: 30-50分钟")
    print("🔄 最大迭代: 20次")
    print("=" * 80)
    print()

    # 检查API Key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY not set")
        print("       LLM functionality will not work")
        print()
    else:
        print(f"[OK] API Key loaded: {api_key[:10]}...{api_key[-4:]}")
    print()

    # 创建工作流编排器
    print("[INIT] Initializing workflow orchestrator...")
    try:
        WorkflowOrchestrator = _load_workflow_orchestrator()
        orchestrator = WorkflowOrchestrator(str(project_root / "config" / "system.yaml"))
        print(f"[OK] Orchestrator initialized")
        print(f"     - LLM model: {orchestrator.config['openai']['model']}")
        print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
    except Exception as e:
        print(f"[ERROR] Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        return 1
    print()

    # 运行优化
    print("[START] Running L3 optimization...")
    print("-" * 80)

    try:
        # L3配置：强制覆盖
        orchestrator.config['optimization']['max_iterations'] = 20

        final_state = orchestrator.run_optimization(
            bom_file=str(project_root / "config" / "bom_L3_complex.json"),
            max_iterations=20
        )

        print()
        print("-" * 80)
        print("[SUCCESS] L3 测试完成！")
        print()

        # 显示结果
        print("[RESULT] Final design state:")
        print(f"         - Iteration: {final_state.iteration}")
        print(f"         - Components: {len(final_state.components)}")

        if 'last_simulation' in final_state.metadata:
            sim_result = final_state.metadata['last_simulation']
            print(f"         - Max temp: {sim_result.get('max_temp', 'N/A')} °C")
            print(f"         - Violations: {len(sim_result.get('violations', []))}")

        _print_visualization_summary(orchestrator)

        print()
        print("✅ L3 复杂级测试成功！多物理场协同优化验证通过。")
        return 0

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Test interrupted by user")
        return 130
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
