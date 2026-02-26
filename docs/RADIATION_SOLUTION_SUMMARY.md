# COMSOL辐射边界条件问题解决方案

## 问题总结

经过深入测试，发现COMSOL的表面对表面辐射（Surface-to-Surface Radiation）功能存在两个问题：

### 问题1: epsilon_rad属性未定义（已解决）

**错误信息**:
```
未定义"Radiation to Deep Space"所需的材料属性"epsilon rad"
```

**根本原因**:
- COMSOL的辐射边界条件默认从材料读取`epsilon_rad`
- 但API设置`epsilon_rad`值时，不会自动切换数据源为"用户定义"
- 物理场节点仍然尝试从材料查找属性，导致报错

**解决方案**:
```python
rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
rad.selection().all()

# 关键步骤：
# 1. 切换数据源为"用户定义"
rad.set('epsilon_rad_mat', 'userdef')

# 2. 设置发射率值
rad.set('epsilon_rad', '0.85')

# 3. 设置环境温度
rad.set('Tamb', 'T_space')
```

**验证**: ✓ 不再报"未定义epsilon rad"错误

### 问题2: 求解器不收敛（未完全解决）

**错误信息**:
```
找不到解。
达到最大牛顿迭代次数。
返回的解不收敛。
```

**根本原因**:
- Stefan-Boltzmann辐射公式包含T⁴项
- 非线性太强，导致牛顿迭代法难以收敛
- 特别是在深空温度（3K）和高功率（80W）的极端条件下

**可能的解决方案**（待测试）:
1. 使用更好的初始猜测值
2. 调整求解器设置（增加迭代次数、使用更稳定的求解器）
3. 使用瞬态求解逐步逼近稳态
4. 降低功率或增加表面积

## 当前临时方案

使用对流边界条件替代辐射边界条件：

```python
# 对流边界条件（物理上不完全正确，但可以求解）
hf = ht.create('hf1', 'HeatFluxBoundary', 2)
hf.selection().all()
hf.set('q0', '-h_conv*(T-ambient_temp)')  # 注意负号
```

**优点**:
- 求解器稳定，可以收敛
- 系统可以运行起来进行测试

**缺点**:
- 物理上不正确（卫星在真空中不存在对流）
- 温度结果可能不准确

## 已更新的文件

1. [simulation/comsol_model_generator.py](../simulation/comsol_model_generator.py#L265-L285)
   - 更新了`_setup_radiation_boundary`方法
   - 使用`epsilon_rad_mat='userdef'`切换数据源

2. [scripts/test_userdef_epsilon.py](../scripts/test_userdef_epsilon.py)
   - 验证用户定义epsilon方法
   - 确认不再报材料属性错误

3. [models/satellite_thermal_v2.mph](../models/satellite_thermal_v2.mph)
   - 当前使用v3模型（对流边界条件）
   - 可以正常求解

## 下一步工作

1. **短期**: 使用对流边界条件模型进行系统测试
2. **中期**: 研究COMSOL求解器设置，解决辐射边界条件的收敛问题
3. **长期**: 考虑使用其他辐射模型或简化的辐射公式

## 关键发现

感谢Gemini的建议，关键发现是：

1. **域材料 vs 边界材料**: COMSOL区分域（3D）和边界（2D）的材料属性
2. **数据源切换**: API设置值时需要显式切换数据源为"用户定义"
3. **epsilon_rad_mat参数**: 这是切换数据源的关键参数

## 参考

- 测试脚本: [scripts/test_userdef_epsilon.py](../scripts/test_userdef_epsilon.py)
- 边界材料测试: [scripts/test_boundary_material.py](../scripts/test_boundary_material.py)
- Heat Flux测试: [scripts/test_heatflux_radiation.py](../scripts/test_heatflux_radiation.py)

---

**创建时间**: 2026-02-27
**状态**: 部分解决（epsilon_rad设置问题已解决，求解器收敛问题待解决）
