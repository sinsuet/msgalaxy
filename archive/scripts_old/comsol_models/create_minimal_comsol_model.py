#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建最简化但正确的COMSOL模型 - 单个立方体带热源

这个模型非常简单：
- 一个立方体
- 一个热源
- 固定温度边界条件
- 用于验证基本功能
"""

import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def create_minimal_model(output_path: str):
    """创建最简化的测试模型"""
    try:
        import mph
    except ImportError:
        print("[错误] MPh库未安装")
        return False

    print("=" * 70)
    print("最简化COMSOL测试模型创建")
    print("=" * 70)
    print()

    print("[1/8] 连接COMSOL...")
    try:
        client = mph.start()
        print("  ✓ 连接成功")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False

    print("\n[2/8] 创建模型...")
    try:
        model = client.create('MinimalTest')
        print("  ✓ 模型创建成功")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[3/8] 定义参数...")
    try:
        model.parameter('test_size', '100[mm]')
        model.parameter('test_power', '10[W]')
        model.parameter('ambient_temp', '293.15[K]')
        print("  ✓ 参数定义完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[4/8] 创建几何...")
    try:
        model.java.component().create('comp1', True)
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        block = geom.create('blk1', 'Block')
        block.set('size', ['test_size', 'test_size', 'test_size'])
        block.set('pos', ['-test_size/2', '-test_size/2', '-test_size/2'])

        geom.run()
        print("  ✓ 几何创建完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[5/8] 定义材料...")
    try:
        comp = model.java.component('comp1')
        mat = comp.material().create('mat1', 'Common')
        mat.label('Aluminum')
        mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
        mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
        mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
        mat.selection().all()
        print("  ✓ 材料定义完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[6/8] 设置物理场...")
    try:
        ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

        # 热源
        hs = ht.create('hs1', 'HeatSource', 3)
        hs.selection().all()
        hs.set('Q0', 1, 'test_power/(test_size^3*1e-9)')

        # 边界条件
        temp = ht.create('temp1', 'TemperatureBoundary', 2)
        temp.selection().all()
        temp.set('T0', 'ambient_temp')

        print("  ✓ 物理场设置完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[7/8] 创建网格和研究...")
    try:
        # 网格
        mesh = comp.mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)
        mesh.run()

        # 算子
        maxop = comp.cpl().create('maxop1', 'Maximum')
        maxop.selection().geom('geom1', 3)
        maxop.selection().all()

        aveop = comp.cpl().create('aveop1', 'Average')
        aveop.selection().geom('geom1', 3)
        aveop.selection().all()

        intop = comp.cpl().create('intop1', 'Integration')
        intop.selection().geom('geom1', 3)
        intop.selection().all()

        # 研究
        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')

        print("  ✓ 网格和研究创建完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    print("\n[8/8] 保存模型...")
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        model.save(output_path)
        print(f"  ✓ 模型已保存: {output_path}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    client.disconnect()

    print("\n" + "=" * 70)
    print("✓ 最简化模型创建成功！")
    print("=" * 70)
    print(f"\n文件: {output_path}")
    print(f"大小: {os.path.getsize(output_path) / 1024:.1f} KB")
    print()

    return True


if __name__ == '__main__':
    output_path = 'models/minimal_test.mph'
    if len(sys.argv) > 1:
        output_path = sys.argv[1]

    output_path = os.path.abspath(output_path)

    if os.path.exists(output_path):
        os.remove(output_path)

    success = create_minimal_model(output_path)
    sys.exit(0 if success else 1)
