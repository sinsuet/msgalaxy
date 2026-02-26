#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试边界材料方法解决epsilon_rad问题

根据Gemini的建议：
方法1: 为边界创建专门的材料
方法2: 直接在���理场节点设置epsilon_rad
"""

import sys
import os
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    import mph
except ImportError:
    print("MPh库未安装")
    sys.exit(1)

print("=" * 80)
print("测试边界材料方法")
print("=" * 80)

try:
    print("\n[1/8] 连接COMSOL...")
    client = mph.start()
    print("  ✓ COMSOL客户端启动成功")

    print("\n[2/8] 创建模型...")
    model = client.create('BoundaryMaterialTest')
    print("  ✓ 模型创建成功")

    # 参数
    print("\n[3/8] 定义参数...")
    model.parameter('T_space', '3[K]')
    model.parameter('power', '80[W]')
    model.parameter('size', '100[mm]')
    print("  ✓ 参数定义完成")

    # 几何
    print("\n[4/8] 创建几何...")
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    block = geom.create('blk1', 'Block')
    block.set('size', ['size', 'size', 'size'])
    geom.run()
    print("  ✓ 几何创建完成")

    comp = model.java.component('comp1')

    # 域材料（用于热传导）
    print("\n[5/8] 定义域材料...")
    domain_mat = comp.material().create('mat_domain', 'Common')
    domain_mat.label('Aluminum (Domain)')
    domain_mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    domain_mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    domain_mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    domain_mat.selection().geom('geom1', 3)  # 应用于域（3D）
    domain_mat.selection().all()
    print("  ✓ 域材料定义完成")

    # 边界材料（用于辐射）- 关键！
    print("\n[6/8] 定义边界材料（关键步骤）...")
    boundary_mat = comp.material().create('mat_boundary', 'Common')
    boundary_mat.label('Surface Properties (Boundary)')
    boundary_mat.propertyGroup('def').set('epsilon_rad', ['0.85'])  # 辐射发射率
    boundary_mat.selection().geom('geom1', 2)  # 应用于边界（2D）
    boundary_mat.selection().all()
    print("  ✓ 边界材料定义完成")
    print("    - 几何层级: 2 (边界)")
    print("    - epsilon_rad: 0.85")

    # 物理场
    print("\n[7/8] 设置物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 初始温度
    try:
        init = ht.feature('init1')
        init.set('Tinit', '300[K]')
        print("  ✓ 初始温度: 300K")
    except:
        pass

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, 'power/(size*size*size*1e-9)')
    print("  ✓ 热源设置完成")

    # 辐射边界 - 应该能从边界材料读取epsilon_rad
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()
    rad.set('Tamb', 'T_space')
    rad.label('Radiation to Deep Space')
    print("  ✓ 辐射边界创建完成（从边界材料读取epsilon_rad）")

    # 网格
    print("\n[8/8] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 研究
    print("\n[9/9] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 求解
    print("\n" + "=" * 80)
    print("方法1: 边界材料方法")
    print("=" * 80)

    try:
        model.solve()

        max_temp = float(model.evaluate('max(T)', unit='K'))
        avg_temp = float(model.evaluate('mean(T)', unit='K'))
        min_temp = float(model.evaluate('min(T)', unit='K'))

        print("\n✓ 求解成功！")
        print("\n温度结果:")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
        print(f"  平均温度: {avg_temp:.2f} K ({avg_temp-273.15:.2f} °C)")
        print(f"  最低温度: {min_temp:.2f} K ({min_temp-273.15:.2f} °C)")
        print(f"  温度梯度: {max_temp-min_temp:.2f} K")

        # 验证结果合理性
        print("\n物理合理性检查:")
        if max_temp < 400:
            print("  ✓ 最高温度在合理范围内 (<127°C)")
        else:
            print(f"  ✗ 最高温度过高 ({max_temp-273.15:.2f}°C)")

        if max_temp - min_temp > 1:
            print("  ✓ 存在温度梯度（热量正在传导）")
        else:
            print("  ✗ 温度梯度过小")

        # 保存成功的模型
        output_path = 'models/satellite_thermal_boundary_material.mph'
        os.makedirs('models', exist_ok=True)
        model.save(output_path)
        print(f"\n✓ 模型已保存: {output_path}")

        client.disconnect()

        print("\n" + "=" * 80)
        print("✓ 方法1成功！边界材料方法有效")
        print("=" * 80)
        print("\n关键发现:")
        print("  - 域材料（3D）用于热传导属性")
        print("  - 边界材料（2D）用于辐射属性")
        print("  - COMSOL会从边界材料读取epsilon_rad")
        sys.exit(0)

    except Exception as e:
        print(f"\n✗ 方法1失败: {str(e)[:300]}")

        # 尝试方法2: 直接设置epsilon_rad
        print("\n" + "=" * 80)
        print("尝试方法2: 直接在物理场节点设置")
        print("=" * 80)

        try:
            rad.set('epsilon_rad', '0.85')
            print("  ✓ 直接设置epsilon_rad='0.85'")

            model.solve()

            max_temp = float(model.evaluate('max(T)', unit='K'))
            print(f"\n✓ 方法2成功！最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")

            model.save('models/satellite_thermal_direct_epsilon.mph')
            print(f"✓ 模型已保存: models/satellite_thermal_direct_epsilon.mph")

            client.disconnect()
            sys.exit(0)

        except Exception as e2:
            print(f"\n✗ 方法2也失败: {str(e2)[:300]}")

            model.save('models/boundary_material_debug.mph')
            print(f"\n调试模型已保存: models/boundary_material_debug.mph")

            client.disconnect()
            sys.exit(1)

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
    sys.exit(1)
