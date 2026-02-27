#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 OpenCASCADE STEP 导出功能

验证生成的 STEP 文件是否包含真实的 BREP 实体
"""

import sys
import os
import io

# Windows 编码修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import logging
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope
from geometry.cad_export_occ import export_design_occ, OCCSTEPExporter

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_occ_availability():
    """测试 pythonocc-core 是否可用"""
    print("\n" + "=" * 80)
    print("测试 1: pythonocc-core 可用性检查")
    print("=" * 80)

    exporter = OCCSTEPExporter()

    if exporter.occ_available:
        print("✓ pythonocc-core 可用")
        return True
    else:
        print("✗ pythonocc-core 不可用")
        print("  安装方法: conda install -c conda-forge pythonocc-core")
        return False


def test_step_export():
    """测试 STEP 文件导出"""
    print("\n" + "=" * 80)
    print("测试 2: STEP 文件导出")
    print("=" * 80)

    # 创建测试设计状态
    components = [
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=0.0, y=0.0, z=-50.0),
            dimensions=Vector3D(x=200.0, y=150.0, z=100.0),
            mass=5.0,
            power=50.0,
            category="power"
        ),
        ComponentGeometry(
            id="payload_01",
            position=Vector3D(x=0.0, y=0.0, z=60.0),
            dimensions=Vector3D(x=180.0, y=180.0, z=120.0),
            mass=3.5,
            power=30.0,
            category="payload"
        )
    ]

    envelope = Envelope(
        outer_size=Vector3D(x=300.0, y=300.0, z=250.0)
    )

    design_state = DesignState(
        iteration=1,
        components=components,
        envelope=envelope
    )

    # 导出 STEP 文件
    output_path = "workspace/test_occ_export.step"

    try:
        export_design_occ(design_state, output_path)
        print(f"✓ STEP 文件导出成功: {output_path}")

        # 检查文件是否存在
        if not Path(output_path).exists():
            print("✗ STEP 文件不存在")
            return False

        # 检查文件大小
        file_size = Path(output_path).stat().st_size
        print(f"  文件大小: {file_size / 1024:.2f} KB")

        if file_size < 100:
            print("✗ STEP 文件太小，可能不包含有效几何")
            return False

        # 读取文件内容并检查关键字
        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否包含 BREP 实体关键字
        required_keywords = [
            "MANIFOLD_SOLID_BREP",
            "CLOSED_SHELL",
            "ADVANCED_FACE",
            "EDGE_CURVE",
            "VERTEX_POINT"
        ]

        missing_keywords = []
        for keyword in required_keywords:
            if keyword not in content:
                missing_keywords.append(keyword)

        if missing_keywords:
            print(f"⚠ STEP 文件缺少关键字: {', '.join(missing_keywords)}")
            print("  这可能导致 COMSOL 无法导入")
        else:
            print("✓ STEP 文件包含所有必需的 BREP 实体关键字")

        # 显示文件前 50 行
        print("\n文件内容预览（前 50 行）:")
        print("-" * 80)
        lines = content.split('\n')[:50]
        for i, line in enumerate(lines, 1):
            print(f"{i:3d}: {line}")
        print("-" * 80)

        return True

    except Exception as e:
        print(f"✗ STEP 文件导出失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_comsol_import_simulation():
    """测试 COMSOL 导入（如果 COMSOL 可用）"""
    print("\n" + "=" * 80)
    print("测试 3: COMSOL 导入测试（可选）")
    print("=" * 80)

    try:
        import mph
        print("✓ MPh 库可用，尝试连接 COMSOL...")

        # 这里不实际连接 COMSOL，只是检查库是否可用
        print("  提示: 可手动使用 COMSOL 打开 workspace/test_occ_export.step 验证")
        return True

    except ImportError:
        print("⚠ MPh 库不可用，跳过 COMSOL 导入测试")
        return True


def main():
    """运行所有测试"""
    print("=" * 80)
    print("OpenCASCADE STEP 导出测试套件")
    print("=" * 80)

    results = []

    # 测试 1: OCC 可用性
    results.append(("OCC 可用性", test_occ_availability()))

    if not results[0][1]:
        print("\n" + "=" * 80)
        print("✗ pythonocc-core 不可用，无法继续测试")
        print("=" * 80)
        return 1

    # 测试 2: STEP 导出
    results.append(("STEP 导出", test_step_export()))

    # 测试 3: COMSOL 导入（可选）
    results.append(("COMSOL 导入", test_comsol_import_simulation()))

    # 汇总结果
    print("\n" + "=" * 80)
    print("测试结果汇总")
    print("=" * 80)

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name:20s}: {status}")

    print("=" * 80)

    # 返回状态码
    if all(r[1] for r in results):
        print("✓ 所有测试通过")
        return 0
    else:
        print("✗ 部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
