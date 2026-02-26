#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建简化的COMSOL测试模型

此脚本创建一个最简单的COMSOL模型，用于快速测试系统集成。
模型只包含一个立方体和基本的热源。

用法:
    python create_simple_comsol_model.py [output_file.mph]

示例:
    python create_simple_comsol_model.py models/simple_test.mph
"""

import sys
import os

# 设置UTF-8编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def create_simple_model(output_path: str):
    """创建简化测试模型"""
    try:
        import mph
    except ImportError:
        print("[错误] MPh库未安装")
        print("请运行: pip install mph")
        return False

    print("=" * 60)
    print("COMSOL简化测试模型创建工具")
    print("=" * 60)
    print()

    # 连接COMSOL
    print("[1/5] 连接COMSOL...")
    try:
        client = mph.start()
        print("  ✓ 连接成功")
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        return False

    # 创建模型
    print()
    print("[2/5] 创建模型...")
    try:
        model = client.create('SimpleTest')
        print("  ✓ 模型创建成功")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 定义参数
    print()
    print("[3/5] 定义参数...")
    try:
        model.parameter('test_x', '0[mm]')
        model.parameter('test_y', '0[mm]')
        model.parameter('test_z', '0[mm]')
        model.parameter('test_size', '100[mm]')
        model.parameter('test_power', '10[W]')
        print("  ✓ 5个参数定义完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 创建几何
    print()
    print("[4/5] 创建几何...")
    try:
        # 首先创建组件
        model.java.component().create('comp1', True)

        # 创建几何
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        # 创建立方体
        block = geom.create('blk1', 'Block')
        block.set('size', ['test_size', 'test_size', 'test_size'])
        block.set('pos', ['test_x', 'test_y', 'test_z'])

        geom.run()
        print("  ✓ 几何创建完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 定义材料
    print()
    print("[5/7] 定义材料...")
    try:
        comp = model.java.component('comp1')

        # 铝合金材料
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

    # 设置物理场
    print()
    print("[6/7] 设置物理场...")
    try:
        # 热传导
        ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

        # 热源
        hs = ht.create('hs1', 'HeatSource', 3)
        hs.selection().all()
        hs.set('Q0', 1, 'test_power/(test_size*test_size*test_size*1e-9)')

        # 边界条件
        temp = ht.create('temp1', 'TemperatureBoundary', 2)
        temp.selection().all()
        temp.set('T0', '293.15[K]')

        print("  ✓ 物理场设置完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 网格和研究
    print()
    print("[7/7] 创建网格、研究并保存...")
    try:
        # 网格
        mesh = comp.mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)
        mesh.run()

        # 研究
        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')

        # 保存
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

    print()
    print("=" * 60)
    print("✓ 简化模型创建成功！")
    print("=" * 60)
    print()
    print(f"文件: {output_path}")
    print(f"大小: {os.path.getsize(output_path) / 1024:.1f} KB")
    print()
    print("测试命令:")
    print(f"  run_with_msgalaxy_env.bat test_comsol.py {output_path}")
    print()

    return True


def main():
    if len(sys.argv) > 1:
        output_path = sys.argv[1]
    else:
        output_path = 'models/simple_test.mph'

    output_path = os.path.abspath(output_path)

    if os.path.exists(output_path):
        response = input(f"文件已存在，是否覆盖? (y/n): ")
        if response.lower() != 'y':
            print("操作已取消")
            return

    success = create_simple_model(output_path)
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
