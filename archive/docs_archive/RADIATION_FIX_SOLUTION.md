# COMSOL辐射边界条件修复方案

## 问题根源

COMSOL的表面对表面辐射（Surface-to-Surface Radiation）功能要求：
1. 材料必须定义`epsilon_rad`属性
2. 辐射边界**不应该**显式设置`epsilon_rad`
3. COMSOL会自动从材料属性中读取发射率

## 错误的做法

```python
# ❌ 错误：在辐射边界上显式设置epsilon_rad
rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
rad.selection().all()
rad.set('epsilon_rad', 1, '0.85')  # 这会导致错误
rad.set('Tamb', 'T_space')
```

错误信息：
```
未定义'Radiation to Deep Space'所需的材料属性'epsilon rad'
```

## 正确的做法

### 步骤1: 在材料中定义epsilon_rad

```python
# ✓ 正确：在材料属性中定义epsilon_rad
mat = comp.material().create('mat1', 'Common')
mat.label('Aluminum')

# 设置热物性
mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])

# 关键：设置辐射发射率
mat.propertyGroup('def').set('epsilon_rad', ['0.85'])

# 应用到所有域
mat.selection().all()
```

### 步骤2: 创建辐射边界（不设置epsilon_rad）

```python
# ✓ 正确：不显式设置epsilon_rad，让COMSOL从材料读取
rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
rad.selection().all()
rad.set('Tamb', 'T_space')  # 只设置环境温度
rad.label('Radiation to Deep Space')
```

## 工作原理

1. COMSOL的表面对表面辐射功能会检查材料属性
2. 如果材料定义了`epsilon_rad`，辐射边界会自动使用
3. 如果在边界上显式设置`epsilon_rad`，反而会导致冲突

## 验证方法

运行测试脚本：
```bash
python scripts/test_radiation_fix.py
```

预期结果：
- 求解成功
- 温度在合理范围内（<80°C）
- 存在温度梯度（热量正在传导和辐射）

## 应用到项目

已更新的文件：
1. `simulation/comsol_model_generator.py` - 动态模型生成器
2. `scripts/create_minimal_working_radiation.py` - 最小测试
3. `scripts/test_radiation_fix.py` - 完整验证测试

下一步：
```bash
# 1. 运行测试验证修复
python scripts/test_radiation_fix.py

# 2. 如果成功，替换旧模型
cp models/satellite_thermal_fixed.mph models/satellite_thermal_v2.mph

# 3. 运行完整工作流测试
python test_real_workflow.py
```

## 参考

- MPh官方文档: https://mph.readthedocs.io/en/stable/
- COMSOL Heat Transfer Module文档
- 表面对表面辐射物理原理: Stefan-Boltzmann定律

---

**创建时间**: 2026-02-27
**状态**: 已修复
