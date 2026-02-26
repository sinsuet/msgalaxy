# Scripts Directory

本目录包含用于开发和测试的辅助脚本。

## 目录结构

```
scripts/
├── comsol_models/    - COMSOL模型创建脚本
└── tests/            - 测试脚本
```

---

## COMSOL模型创建脚本

### 当前使用的脚本

**create_satellite_model.py** - 创建V2.0卫星热分析模型
- 用途: 创建当前系统使用的COMSOL模型
- 输出: models/satellite_thermal_v2.mph
- 特点: 包含算子定义，结果提取正常
- 使用:
  ```bash
  python scripts/comsol_models/create_satellite_model.py models/satellite_thermal_v2.mph
  ```

### 实验性脚本

**create_satellite_model_v3.py** - 创建V3.0模型（实验性）
- 用途: 尝试使用对流边界条件
- 输出: models/satellite_thermal_v3.mph
- 状态: 实验性，温度计算有问题

**create_simple_comsol_model.py** - 创建简化测试模型
- 用途: 快速测试基本功能
- 输出: models/simple_test.mph
- 特点: 单个立方体，5个参数

**create_minimal_comsol_model.py** - 创建最简化模型
- 用途: 调试和验证
- 输出: models/minimal_test.mph
- 特点: 最简单的配置

**create_comsol_model.py** - 原始模型创建脚本
- 状态: 已被新版本替代

---

## 测试脚本

### COMSOL测试

**test_satellite_v2_model.py** - 测试V2.0模型
- 用途: 验证V2.0模型的仿真功能
- 测试内容: 连接、加载、参数更新、求解、结果提取
- 使用:
  ```bash
  python scripts/tests/test_satellite_v2_model.py
  ```

**test_satellite_v3_model.py** - 测试V3.0模型
- 用途: 验证V3.0模型
- 状态: 实验性

**test_comsol.py** - COMSOL基础测试
- 用途: 测试COMSOL连接和基本功能

### 系统测试

**test_integration.py** - 集成测试
- 用途: 测试系统各模块的集成

**test_geometry.py** - 几何模块测试
- 用途: 测试布局引擎和装箱算法

**test_simulation.py** - 仿真模块测试
- 用途: 测试仿真驱动器

**test_qwen.py** - LLM测试
- 用途: 测试Qwen API连接

---

## 使用建议

### 开发流程

1. **创建新模型**
   ```bash
   # 使用V2.0脚本创建模型
   python scripts/comsol_models/create_satellite_model.py models/my_model.mph
   ```

2. **测试模型**
   ```bash
   # 修改测试脚本中的模型路径
   python scripts/tests/test_satellite_v2_model.py
   ```

3. **运行优化**
   ```bash
   # 更新config/system.yaml中的模型路径
   python -m api.cli optimize --max-iter 5
   ```

### 调试流程

1. **快速验证**
   ```bash
   # 使用简化模型快速测试
   python scripts/comsol_models/create_simple_comsol_model.py
   python scripts/tests/test_comsol.py
   ```

2. **完整测试**
   ```bash
   # 运行所有测试
   python scripts/tests/test_integration.py
   ```

---

## 维护说明

### 脚本命名规范

- `create_*.py` - 模型创建脚本
- `test_*.py` - 测试脚本
- 使用描述性名称，如 `create_satellite_model.py`

### 添加新脚本

1. 将脚本放在相应目录
2. 添加UTF-8编码支持
3. 添加使用说明
4. 更新本README

### 清理旧脚本

定期检查并移除不再使用的脚本：
```bash
# 移动到archive目录
mkdir -p scripts/archive
mv scripts/comsol_models/old_script.py scripts/archive/
```

---

## 依赖要求

### COMSOL脚本
- Python 3.12+
- mph库
- COMSOL Multiphysics 6.3

### 测试脚本
- 所有系统依赖（见requirements.txt）
- msgalaxy conda环境

---

## 常见问题

### Q: 为什么有这么多模型创建脚本？
A: 不同版本用于不同的实验和测试。V2.0是当前稳定版本。

### Q: 哪个脚本应该使用？
A: 对于生产使用，使用 `create_satellite_model.py` (V2.0)。

### Q: 如何运行这些脚本？
A: 必须使用msgalaxy conda环境：
```bash
D:/MSCode/miniconda3/envs/msgalaxy/python.exe scripts/comsol_models/create_satellite_model.py
```

---

**最后更新**: 2026-02-15
**维护者**: msgalaxy开发团队
