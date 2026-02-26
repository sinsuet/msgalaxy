# COMSOL辐射问题快速修复指南

## 问题
温度异常高（2.2亿°C），原因是辐射边界条件设置错误

## 根本原因
COMSOL的表面对表面辐射要求：
- ✓ 在**材料**中定义`epsilon_rad`
- ✗ **不要**在辐射边界上显式设置`epsilon_rad`

## 修复步骤

### 方法1: 自动修复（推荐）

```bash
# 运行修复脚本
python scripts/fix_comsol_boundary.py

# 替换旧模型
cp models/satellite_thermal_fixed.mph models/satellite_thermal_v2.mph

# 验证修复
python test_real_workflow.py
```

### 方法2: 测试验证

```bash
# 运行最小测试
python scripts/test_radiation_fix.py

# 如果成功，应该看到：
# ✓ 求解成功！
# 最高温度: ~350K (~77°C)
# 温度梯度: ~50K
```

## 代码修改

已修复的文件：
- [simulation/comsol_model_generator.py](../simulation/comsol_model_generator.py) - 移除了辐射边界上的epsilon_rad设置
- [scripts/create_minimal_working_radiation.py](../scripts/create_minimal_working_radiation.py) - 更新了测试脚本

关键修改：
```python
# 之前（错误）
rad.set('epsilon_rad', 1, '0.85')  # ❌ 导致错误

# 现在（正确）
# 不设置epsilon_rad，让COMSOL从材料读取  # ✓ 正确
```

## 预期效果

修复后：
- 温度: 2.2亿°C → ~70°C
- 温度梯度: 0°C/m → ~12°C/m
- 物理合理性: ✓

## 技术细节

参考文档：
- [RADIATION_FIX_SOLUTION.md](RADIATION_FIX_SOLUTION.md) - 详细技术方案
- [COMSOL_TEMPERATURE_FIX.md](COMSOL_TEMPERATURE_FIX.md) - 完整分析报告

---

**状态**: ✓ 已修复
**测试**: 待验证
