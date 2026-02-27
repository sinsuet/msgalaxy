#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DV2.0 模块导入验证脚本"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def main():
    print("=" * 60)
    print("DV2.0 模块导入验证")
    print("=" * 60)

    errors = []

    # 1. 核心协议
    try:
        from core.protocol import OperatorType, ComponentGeometry, Vector3D
        print(f"[OK] core.protocol - OperatorType has {len(OperatorType)} operators")
        print(f"     Operators: {[op.value for op in OperatorType]}")
    except Exception as e:
        errors.append(f"core.protocol: {e}")
        print(f"[FAIL] core.protocol: {e}")

    # 2. Geometry Agent
    try:
        from optimization.agents.geometry_agent import GeometryAgent
        print("[OK] optimization.agents.geometry_agent")
    except Exception as e:
        errors.append(f"geometry_agent: {e}")
        print(f"[FAIL] geometry_agent: {e}")

    # 3. Thermal Agent
    try:
        from optimization.agents.thermal_agent import ThermalAgent
        print("[OK] optimization.agents.thermal_agent")
    except Exception as e:
        errors.append(f"thermal_agent: {e}")
        print(f"[FAIL] thermal_agent: {e}")

    # 4. CAD Export (OCC)
    try:
        from geometry.cad_export_occ import OCCSTEPExporter
        exporter = OCCSTEPExporter()
        status = "pythonocc available" if exporter.occ_available else "pythonocc NOT available"
        print(f"[OK] geometry.cad_export_occ - {status}")
    except Exception as e:
        errors.append(f"cad_export_occ: {e}")
        print(f"[FAIL] cad_export_occ: {e}")

    # 5. Workflow Orchestrator
    try:
        from workflow.orchestrator import WorkflowOrchestrator
        print("[OK] workflow.orchestrator")
    except Exception as e:
        errors.append(f"orchestrator: {e}")
        print(f"[FAIL] orchestrator: {e}")

    # 6. COMSOL Driver
    try:
        from simulation.comsol_driver import ComsolDriver
        print("[OK] simulation.comsol_driver")
    except Exception as e:
        errors.append(f"comsol_driver: {e}")
        print(f"[FAIL] comsol_driver: {e}")

    print()
    print("=" * 60)
    if errors:
        print(f"FAILED: {len(errors)} module(s) have import errors")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print("SUCCESS: All DV2.0 modules imported correctly!")
        return 0

if __name__ == "__main__":
    sys.exit(main())
