# 测试指南

本文档说明如何运行项目测试。

## 前提条件

### 1. 创建并激活conda环境

```bash
# 创建环境
conda create -n msgalaxy python=3.12

# 激活环境
conda activate msgalaxy
```

### 2. 安装依赖

```bash
# 安装Python依赖
pip install -r requirements.txt

# 可选：安装MATLAB Engine（如果需要MATLAB仿真）
# cd "D:\Program Files\MATLAB\R2025b\extern\engines\python"
# python setup.py install

# 可选：安装COMSOL接口
# pip install mph
```

### 3. 配置API密钥（可选）

创建 `.env` 文件：

```bash
OPENAI_API_KEY=sk-your-qwen-api-key-here
```

## 运行测试

### 方法1: 使用Python测试运行器（推荐）

```bash
# 确保已激活msgalaxy环境
conda activate msgalaxy

# 运行所有测试
python run_tests.py
```

### 方法2: 使用批处理文件（Windows）

```bash
# 自动激活环境并运行测试
run_tests.bat
```

### 方法3: 单独运行测试

```bash
# 激活环境
conda activate msgalaxy

# 几何模块测试
python test_geometry.py

# 仿真模块测试
python test_simulation.py

# 系统集成测试
python test_integration.py

# Qwen API测试（需要API密钥）
python test_qwen.py sk-your-api-key
```

## 测试说明

### test_geometry.py
测试几何模块功能：
- AABB数据结构
- 六面减法算法
- 3D装箱算法
- 布局引擎

**依赖**: numpy, py3dbp

### test_simulation.py
测试仿真模块功能：
- 简化物理引擎
- 热分析
- 结构分析
- 电源分析

**依赖**: numpy, scipy, pydantic

### test_integration.py
测试系统集成：
- Meta-Reasoner战略决策
- Multi-Agent系统
- RAG知识检索
- 数据协议验证

**依赖**: pydantic, openai（可选）

### test_qwen.py
测试Qwen API集成：
- API连接测试
- Meta-Reasoner与Qwen集成
- JSON响应解析

**依赖**: openai, pydantic
**要求**: 需要有效的Qwen API密钥

## 常见问题

### Q1: ModuleNotFoundError: No module named 'xxx'

**原因**: 依赖未安装或未激活正确的conda环境

**解决**:
```bash
# 激活环境
conda activate msgalaxy

# 安装依赖
pip install -r requirements.txt
```

### Q2: 测试运行器找不到API密钥

**原因**: 未创建 `.env` 文件或格式不正确

**解决**:
```bash
# 创建.env文件
echo "OPENAI_API_KEY=sk-your-key" > .env
```

### Q3: Windows控制台中文乱码

**原因**: 控制台编码问题

**解决**: 参考 [编码问题解决方案](ENCODING_FIX.md)

### Q4: conda activate 命令不可用

**原因**: conda未初始化

**解决**:
```bash
# 初始化conda
conda init bash  # Linux/Mac
conda init cmd.exe  # Windows CMD
conda init powershell  # Windows PowerShell
```

## 测试输出

### 成功输出示例

```
============================================================
MsGalaxy 测试套件
============================================================
开始时间: 2026-02-15 14:30:00

[1/3] 检查Python版本
  Python: 3.12.0

[2/3] 检查关键依赖
  [OK] numpy: 1.26.0
  [OK] pydantic: 2.6.0
  [OK] openai: 1.12.0
  [OK] pyyaml: 6.0
  [OK] scipy: 1.12.0

[3/3] 检查环境变量
  [OK] .env 文件存在

============================================================
开始运行测试
============================================================

[TEST] 几何模块测试
------------------------------------------------------------
[OK] 几何模块测试通过

[TEST] 仿真模块测试
------------------------------------------------------------
[OK] 仿真模块测试通过

[TEST] 系统集成测试
------------------------------------------------------------
[OK] 集成测试通过

[TEST] Qwen API测试
------------------------------------------------------------
[OK] Qwen API测试通过

============================================================
测试总结
============================================================

总计: 4 个测试
通过: 4 个
失败: 0 个

总耗时: 45.23 秒

============================================================
[SUCCESS] 所有测试通过！
============================================================
```

## 持续集成

如果需要在CI/CD中运行测试：

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: conda-incubator/setup-miniconda@v2
        with:
          python-version: 3.12
          environment-file: environment.yml
      - name: Run tests
        run: python run_tests.py
```

## 相关文档

- [编码问题解决方案](ENCODING_FIX.md)
- [Qwen使用指南](QWEN_GUIDE.md)
- [项目README](../README.md)

---

**更新时间**: 2026-02-15
