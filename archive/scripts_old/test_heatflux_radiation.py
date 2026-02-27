#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用Heat Flux边界条件实现Stefan-Boltzmann辐射

由于COMSOL的Surface-to-Surface Radiation功能存在材料属性问题，
我们使用Heat Flux边界条件手动实现辐射散热：
Q = ε·σ·(T⁴ - T_space⁴)
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
print("使用Heat Flux实现辐射边界条件")
print("=" * 80)

try:
    print("\n[1/8] 连接COMSOL...")
    client = mph.start()
    print("  ✓ COMSOL客户端启动成功")

    print("\n[2/8] 创建模型...")
    model = client.create('RadiationHeatFlux')
    print("  ✓ 模型创建成功")

    # 参数
    print("\n[3/8] 定义参数...")
    model.parameter('T_space', '3[K]')  # 深空温度
    model.parameter('emissivity', '0.85')  # 发射率
    model.parameter('sigma', '5.67e-8[W/(m^2*K^4)]')  # Stefan-Boltzmann常数
    model.parameter('power', '80[W]')  # 总功率
    model.parameter('size', '100[mm]')  # 尺寸
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

    # 材料
    print("\n[5/8] 定义材料...")
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('Aluminum')

    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    mat.selection().all()

    print("  ✓ 材料定义完成")

    # 物理场
    print("\n[6/8] 设置物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 设置初始温度（使用默认的init特征）
    try:
        init = ht.feature('init1')
        init.set('Tinit', '300[K]')  # 初始温度设为室温
        print("  ✓ 初始温度设置完成 (300K)")
    except:
        print("  ⚠ 无法���置初始温度，使用默认值")

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, 'power/(size*size*size*1e-9)')
    print("  ✓ 热源设置完成")

    # Heat Flux边界条件 - 实现Stefan-Boltzmann辐射
    hf = ht.create('hf1', 'HeatFluxBoundary', 2)
    hf.selection().all()
    # 辐射热流：q = ε·σ·(T⁴ - T_space⁴)
    # 注意：COMSOL中热流为正表示流入，所以辐射散热应该是负值
    hf.set('q0', '-emissivity*sigma*(T^4-T_space^4)')
    hf.label('Radiation Heat Flux')

    print("  ✓ 辐射边界条件设置完成（Heat Flux方式）")
    print("    - 公式: q = -ε·σ·(T⁴ - T_space⁴)")
    print("    - 发射率: 0.85")
    print("    - 深空温度: 3K")

    # 网格
    print("\n[7/8] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 研究
    print("\n[8/8] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 求解
    print("\n" + "=" * 80)
    print("开始求解...")
    print("=" * 80)

    try:
        model.solve()

        # 提取结果
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
        if max_temp < 400:  # < 127°C
            print("  ✓ 最高温度在合理范围内 (<127°C)")
        else:
            print(f"  ✗ 最高温度过高 ({max_temp-273.15:.2f}°C)")

        if max_temp - min_temp > 1:
            print("  ✓ 存在温度梯度（热量正在传导）")
        else:
            print("  ✗ 温度梯度过小")

        # 保存成功的模型
        output_path = 'models/satellite_thermal_heatflux.mph'
        os.makedirs('models', exist_ok=True)
        model.save(output_path)
        print(f"\n✓ 模型已保存: {output_path}")

        client.disconnect()

        print("\n" + "=" * 80)
        print("✓ 测试成功！使用Heat Flux实现辐射边界条件")
        print("=" * 80)
        sys.exit(0)

    except Exception as e:
        print(f"\n✗ 求解失败: {e}")

        debug_path = 'models/heatflux_debug.mph'
        model.save(debug_path)
        print(f"\n调试模型已保存: {debug_path}")

        client.disconnect()

        print("\n" + "=" * 80)
        print("✗ 测试失败")
        print("=" * 80)
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
