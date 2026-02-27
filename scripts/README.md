# Scripts Directory

本目录包含用于开发和测试的核心脚本。

**最后更新**: 2026-02-27  
**状态**: 已清理，仅保留核心脚本

---

## 📁 目录结构

```
scripts/
├── create_complete_satellite_model.py  ⭐ 当前使用的COMSOL模型生成器
├── clean_experiments.py                 实验数据清理工具
└── README.md                            本文档
```

**注意**: 旧的测试脚本和实验性脚本已归档到 `archive/scripts_old/` 目录。

---

## 🚀 核心脚本

### create_complete_satellite_model.py ⭐

**当前使用的完整卫星热分析模型生成器**

**功能**:
- 创建工程级COMSOL多物理场模型
- 3个域: 外壳（空心结构）、电池、载荷
- 使用原生HeatFluxBoundary实现Stefan-Boltzmann辐射
- 包含6个后处理算子

**输出**: `models/satellite_thermal_heatflux.mph` (5.1MB)

**模型特点**:
- ✅ 多组件结构（外壳 + 电池 + 载荷）
- ✅ 统一铝合金材料 (k=167 W/m·K)
- ✅ 深空辐射散热 (ε=0.85, T_space=3K)
- ✅ 太阳辐射输入 (1367 W/m², 可控)
- ✅ 热源: 电池50W + 载荷30W
- ✅ 6个后处理算子（温度、热流）

**使用方法**:
```bash
# 使用msgalaxy环境
D:/MSCode/miniconda3/envs/msgalaxy/python.exe scripts/create_complete_satellite_model.py

# 或者如果已激活环境
python scripts/create_complete_satellite_model.py
```

**可调参数**:
```python
T_space = 3K                    # 深空温度
solar_flux = 1367 W/m²          # 太阳常数
eclipse_factor = 0              # 0=日照, 1=阴影
emissivity_external = 0.85      # 外表面发射率
emissivity_internal = 0.05      # 内表面发射率
absorptivity_solar = 0.25       # 太阳吸收率
contact_resistance = 1e-4 m²·K/W # 接触热阻
```

**后处理算子**:
- `maxop1(T)` - 全局最高温度
- `aveop1(T)` - 全局平均温度
- `minop1(T)` - 全局最低温度
- `maxop_battery(T)` - 电池最高温度
- `maxop_payload(T)` - 载荷最高温度
- `intop_flux(ht.ntflux)` - 外表面总热流

**技术亮点**:
- 使用COMSOL原生特征（不依赖已过时的SurfaceToSurfaceRadiation）
- 手动实现Stefan-Boltzmann辐射定律: `q = ε·σ·(T_space⁴ - T⁴)`
- 参数化设计，所有关键参数可调

**相关文档**:
- [docs/RADIATION_SOLUTION_SUMMARY.md](../docs/RADIATION_SOLUTION_SUMMARY.md) - 辐射问题解决方案
- [docs/COMSOL_GUIDE.md](../docs/COMSOL_GUIDE.md) - COMSOL使用指南

---

### clean_experiments.py

**实验数据清理工具**

**功能**:
- 清理旧的实验数据目录
- 保留最近N次实验
- 释放磁盘空间

**使用方法**:
```bash
# 清理7天前的实验数据
python scripts/clean_experiments.py --days 7

# 仅保留最近5次实验
python scripts/clean_experiments.py --keep 5

# 查看将被清理的文件（不实际删除）
python scripts/clean_experiments.py --dry-run
```

---

## 🗂️ 归档脚本

以下脚本已归档到 `archive/scripts_old/` 目录，如需使用可从归档中恢复：

### 探索性脚本 (3个)
- `explore_comsol_radiation.py` - 辐射特征探索
- `explore_material_groups.py` - 材料组探索
- `explore_radiation_property.py` - 辐射属性探索

### 测试脚本 (7个)
- `test_boundary_material.py` - 边界材料测试
- `test_builtin_material.py` - 内置材料测试
- `test_full_radiation.py` - 完整辐射测试
- `test_heatflux_radiation.py` - 热流辐射测试
- `test_radiation_fix.py` - 辐射修复测试
- `test_userdef_epsilon.py` - 用户定义epsilon测试

### 旧模型生成脚本 (5个)
- `create_convection_model.py` - 对流模型（已废弃）
- `create_minimal_working_radiation.py` - 最小辐射模型
- `create_official_convection_model.py` - 官方对流模型
- `create_simplified_radiation_model.py` - 简化辐射模型
- `fix_comsol_boundary.py` - 边界修复脚本

### 旧目录
- `comsol_models/` - 包含5个旧版本模型生成脚本
- `tests/` - 包含7个旧测试脚本

**恢复方法**:
```bash
# 从归档恢复某个脚本
cp archive/scripts_old/test_userdef_epsilon.py scripts/
```

---

## 🔧 开发流程

### 1. 创建新COMSOL模型

```bash
# 使用当前脚本创建模型
python scripts/create_complete_satellite_model.py

# 模型将保存到 models/satellite_thermal_heatflux.mph
```

### 2. 测试模型

```bash
# 运行端到端工作流测试
python test_real_workflow.py

# 检查生成的可视化
ls experiments/run_*/visualizations/
```

### 3. 运行优化

```bash
# 确保config/system.yaml中的模型路径正确
# comsol_model: "e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph"

# 运行优化
python -m api.cli optimize --max-iter 5
```

---

## 📋 使用建议

### 快速开始

1. **首次使用**:
   ```bash
   # 1. 创建COMSOL模型
   python scripts/create_complete_satellite_model.py
   
   # 2. 运行测试验证
   python test_real_workflow.py
   
   # 3. 检查结果
   ls experiments/run_*/visualizations/
   ```

2. **日常开发**:
   - 模型已创建，直接运行优化即可
   - 定期清理实验数据: `python scripts/clean_experiments.py --days 7`

### 调试流程

1. **COMSOL连接问题**:
   ```bash
   # 测试COMSOL连接
   python -c "import mph; client = mph.start(); print('OK'); client.disconnect()"
   ```

2. **模型参数问题**:
   - 在COMSOL GUI中打开模型: `models/satellite_thermal_heatflux.mph`
   - 检查参数定义和边界条件
   - 手动求解验证

3. **求解器收敛问题**:
   - 参考 [TEST_WORKFLOW_ANALYSIS.md](../TEST_WORKFLOW_ANALYSIS.md)
   - 在COMSOL GUI中调整求解器设置
   - 尝试瞬态求解逐步逼近稳态

---

## 🛠️ 维护说明

### 脚本命名规范

- `create_*.py` - 模型创建脚本
- `test_*.py` - 测试脚本
- `clean_*.py` - 清理工具
- 使用描述性名称，如 `create_complete_satellite_model.py`

### 添加新脚本

1. 将脚本放在 `scripts/` 目录
2. 添加UTF-8编码支持（Windows环境）:
   ```python
   import sys
   import io
   if sys.platform == 'win32':
       sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
       sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
   ```
3. 添加使用说明和文档字符串
4. 更新本README

### 清理旧脚本

定期检查并归档不再使用的脚本：
```bash
# 移动到归档目录
mv scripts/old_script.py archive/scripts_old/
```

---

## 📦 依赖要求

### COMSOL脚本
- Python 3.12+
- mph库 (COMSOL Python接口)
- COMSOL Multiphysics 6.3+

### 系统脚本
- 所有系统依赖（见 `requirements.txt`）
- msgalaxy conda环境

### 安装依赖
```bash
# 创建环境
conda create -n msgalaxy python=3.12
conda activate msgalaxy

# 安装依赖
pip install -r requirements.txt
```

---

## ❓ 常见问题

### Q: 为什么只有一个模型创建脚本？
A: 经过多次迭代和测试，`create_complete_satellite_model.py` 是当前最稳定和功能最完整的版本。旧版本已归档。

### Q: 如何运行这些脚本？
A: 必须使用msgalaxy conda环境：
```bash
# 方法1: 使用完整路径
D:/MSCode/miniconda3/envs/msgalaxy/python.exe scripts/create_complete_satellite_model.py

# 方法2: 激活环境后运行
conda activate msgalaxy
python scripts/create_complete_satellite_model.py
```

### Q: COMSOL模型求解失败怎么办？
A: 这是已知问题，T⁴非线性导致求解器收敛困难。解决方案：
1. 在COMSOL GUI中打开模型
2. 调整求解器设置（增加迭代次数、使用更稳定的求解器）
3. 尝试瞬态求解逐步逼近稳态
4. 参考 [TEST_WORKFLOW_ANALYSIS.md](../TEST_WORKFLOW_ANALYSIS.md) 的详细分析

### Q: 如何查看归档的脚本？
A: 所有归档脚本保存在 `archive/scripts_old/` 目录：
```bash
# 查看归档内容
ls archive/scripts_old/

# 恢复某个脚本
cp archive/scripts_old/test_userdef_epsilon.py scripts/
```

---

## 📚 相关文档

- [CLEANUP_REPORT.md](../CLEANUP_REPORT.md) - 代码清理报告
- [TEST_WORKFLOW_ANALYSIS.md](../TEST_WORKFLOW_ANALYSIS.md) - 工作流测试分析
- [docs/RADIATION_SOLUTION_SUMMARY.md](../docs/RADIATION_SOLUTION_SUMMARY.md) - 辐射问题解决方案
- [docs/COMSOL_GUIDE.md](../docs/COMSOL_GUIDE.md) - COMSOL使用指南
- [handoff.md](../handoff.md) - 项目交接文档

---

**维护者**: MsGalaxy开发团队  
**项目**: MsGalaxy v1.3.0  
**系统成熟度**: 75%
