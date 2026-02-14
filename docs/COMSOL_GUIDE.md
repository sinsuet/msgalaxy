# COMSOL仿真接入指南

**状态**: ✅ MPh库已安装，驱动已实现
**测试时间**: 2026-02-15

---

## 概述

MsGalaxy系统已完整实现COMSOL Multiphysics真实物理仿真接入，支持：
- 热分析（Heat Transfer）
- 结构分析（Structural Mechanics）
- 多物理场耦合分析

---

## 前提条件

### 1. 软件要求

- ✅ **COMSOL Multiphysics** - 已安装（默认路径: D:\Program Files\COMSOL63）
- ✅ **MPh库** - 已安装（版本: 1.3.1）
- ✅ **Python环境** - msgalaxy conda环境
- ⚠️ **COMSOL许可证** - 需要有效许可证

### 2. 环境检查

```bash
# 激活环境
conda activate msgalaxy

# 检查MPh库
python -c "import mph; print(f'MPh版本: {mph.__version__}')"

# 运行测试
python test_comsol.py
```

---

## COMSOL模型准备

### 模型要求

1. **参数化几何**
   - 组件位置参数: `<component_id>_x`, `<component_id>_y`, `<component_id>_z`
   - 组件尺寸参数: `<component_id>_dx`, `<component_id>_dy`, `<component_id>_dz`
   - 功率参数: `<component_id>_power`

2. **物理场设置**
   - 热分析: Heat Transfer in Solids
   - 结构分析: Solid Mechanics（可选）
   - 边界条件: 根据实际情况设置

3. **求解器配置**
   - 稳态或瞬态求解器
   - 网格设置: 自适应或固定网格

### 示例参数配置

```
# 电池组件
battery_01_x = 0 [mm]
battery_01_y = 0 [mm]
battery_01_z = 0 [mm]
battery_01_dx = 200 [mm]
battery_01_dy = 150 [mm]
battery_01_dz = 100 [mm]
battery_01_power = 50 [W]

# 载荷组件
payload_01_x = 0 [mm]
payload_01_y = 0 [mm]
payload_01_z = 150 [mm]
payload_01_dx = 180 [mm]
payload_01_dy = 180 [mm]
payload_01_dz = 120 [mm]
payload_01_power = 30 [W]
```

---

## 配置系统使用COMSOL

### 1. 修改配置文件

编辑 `config/system.yaml`:

```yaml
# 仿真配置
simulation:
  type: "COMSOL"  # 改为COMSOL

  # COMSOL配置
  comsol_model: "path/to/your/model.mph"
  comsol_parameters:
    - "battery_01_x"
    - "battery_01_y"
    - "battery_01_z"
    - "battery_01_power"
    - "payload_01_x"
    - "payload_01_y"
    - "payload_01_z"
    - "payload_01_power"

  # 约束条件
  constraints:
    max_temp_c: 50.0
    max_stress_mpa: 100.0
    min_clearance_mm: 3.0
```

### 2. 运行优化

```bash
# 使用COMSOL仿真运行优化
python -m api.cli optimize --max-iter 5
```

---

## 测试COMSOL集成

### 基础测试

```bash
# 测试MPh库导入
python test_comsol.py

# 输出:
# [OK] MPh库导入成功
#   MPh版本: 1.3.1
```

### 连接测试

```bash
# 测试COMSOL连接（需要模型文件）
python test_comsol.py path/to/model.mph

# 预期输出:
# [OK] COMSOL连接成功
# [OK] 模型加载成功
```

### 完整仿真测试

```bash
# 运行完整仿真测试
python test_comsol.py path/to/model.mph

# 预期输出:
# [OK] 仿真成功完成
# 仿真结果:
#   max_temp: 45.23
#   avg_temp: 32.15
#   max_stress: 78.50
```

---

## COMSOL驱动API

### 初始化驱动

```python
from simulation.comsol_driver import ComsolDriver

config = {
    'comsol_model': 'model.mph',
    'comsol_parameters': ['battery_01_x', 'battery_01_y', ...],
    'constraints': {
        'max_temp_c': 50.0,
        'max_stress_mpa': 100.0
    }
}

driver = ComsolDriver(config)
```

### 连接COMSOL

```python
# 连接到COMSOL服务器
driver.connect()

# 检查连接状态
if driver.connected:
    print("COMSOL已连接")
```

### 运行仿真

```python
from core.protocol import SimulationRequest, SimulationType

# 创建仿真请求
request = SimulationRequest(
    sim_type=SimulationType.COMSOL,
    design_state=design_state,
    parameters={}
)

# 运行仿真
result = driver.run_simulation(request)

# 检查结果
if result.success:
    print(f"最高温度: {result.metrics['max_temp']:.2f}°C")
    print(f"违规数: {len(result.violations)}")
```

### 计算自定义表达式

```python
# 计算COMSOL表达式
max_temp = driver.evaluate_expression('maxop1(T)', unit='degC')
avg_stress = driver.evaluate_expression('aveop1(solid.mises)', unit='MPa')
```

### 导出结果

```python
# 导出仿真结果
driver.export_results('results.txt', dataset='dset1')
```

### 断开连接

```python
# 断开COMSOL连接
driver.disconnect()
```

---

## 工作流程

### 1. 优化循环中的COMSOL仿真

```
初始化设计
    ↓
[迭代循环]
    ↓
更新COMSOL几何参数
    ↓
重建几何和网格
    ↓
求解物理场
    ↓
提取结果（温度、应力等）
    ↓
检查约束违反
    ↓
LLM生成优化策略
    ↓
执行优化操作
    ↓
[下一次迭代]
```

### 2. 自动参数更新

系统会自动将设计状态转换为COMSOL参数：

```python
# 设计状态
component.position = Vector3D(x=10.0, y=20.0, z=30.0)
component.power = 50.0

# 自动更新为COMSOL参数
model.parameter('battery_01_x', '10.0[mm]')
model.parameter('battery_01_y', '20.0[mm]')
model.parameter('battery_01_z', '30.0[mm]')
model.parameter('battery_01_power', '50.0[W]')
```

---

## 性能优化

### 1. 网格设置

- 使用自适应网格减少计算时间
- 对关键区域使用细网格
- 对非关键区域使用粗网格

### 2. 求解器设置

- 使用直接求解器（MUMPS）提高稳定性
- 使用迭代求解器（GMRES）提高速度
- 调整收敛容差平衡精度和速度

### 3. 并行计算

```python
# COMSOL支持多核并行
# 在COMSOL设置中启用并行计算
```

---

## 故障排除

### 问题1: MPh库导入失败

**错误**: `ModuleNotFoundError: No module named 'mph'`

**解决**:
```bash
conda activate msgalaxy
pip install mph
```

**注意**: 如果遇到jpype1编译错误（需要Microsoft Visual C++ Build Tools），这通常是因为：
1. Python版本过新（如3.14），jpype1没有预编译的wheel
2. 缺少C++编译器

**推荐解决方案**:
- 使用Python 3.11或3.12（有预编译wheel）
- 或安装Microsoft Visual C++ Build Tools: https://visualstudio.microsoft.com/visual-cpp-build-tools/

### 问题2: COMSOL连接失败

**错误**: `ComsolConnectionError: COMSOL连接失败`

**检查**:
1. COMSOL是否已安装
2. COMSOL许可证是否有效
3. COMSOL服务是否运行

**解决**:
```bash
# 启动COMSOL服务器
# 在COMSOL中: File > Client-Server > Server > Start
```

### 问题3: 模型文件不存在

**错误**: `COMSOL模型文件不存在: model.mph`

**解决**:
- 检查模型文件路径是否正确
- 使用绝对路径: `D:/Models/satellite_thermal.mph`

### 问题4: 参数未找到

**错误**: `Parameter 'battery_01_x' not found`

**解决**:
- 在COMSOL模型中定义所有需要的参数
- 确保参数名称与配置文件一致

### 问题5: 求解失败

**错误**: `Solver failed to converge`

**解决**:
1. 检查几何是否有效（无重叠、无间隙）
2. 检查边界条件是否合理
3. 调整求解器设置（松弛因子、迭代次数）
4. 使用更细的网格

---

## 最佳实践

### 1. 模型设计

- ✅ 使用参数化几何，便于自动更新
- ✅ 简化几何，去除不必要的细节
- ✅ 使用对称性减少计算量
- ✅ 合理设置边界条件

### 2. 参数命名

- ✅ 使用一致的命名规范: `<component_id>_<parameter>`
- ✅ 使用有意义的组件ID: `battery_01`, `payload_01`
- ✅ 在配置文件中列出所有参数

### 3. 结果验证

- ✅ 检查温度分布是否合理
- ✅ 检查应力分布是否合理
- ✅ 对比简化模型和COMSOL结果
- ✅ 验证约束是否正确检测

### 4. 性能优化

- ✅ 使用合适的网格密度
- ✅ 启用并行计算
- ✅ 缓存不变的几何和网格
- ✅ 仅在必要时重建几何

---

## 示例：完整工作流

```python
from simulation.comsol_driver import ComsolDriver
from core.protocol import *

# 1. 配置COMSOL驱动
config = {
    'comsol_model': 'D:/Models/satellite_thermal.mph',
    'comsol_parameters': [
        'battery_01_x', 'battery_01_y', 'battery_01_z',
        'battery_01_power',
        'payload_01_x', 'payload_01_y', 'payload_01_z',
        'payload_01_power'
    ],
    'constraints': {
        'max_temp_c': 50.0
    }
}

driver = ComsolDriver(config)

# 2. 连接COMSOL
driver.connect()

# 3. 创建设计状态
design_state = DesignState(
    iteration=1,
    components=[...],
    envelope=Envelope(...)
)

# 4. 运行仿真
request = SimulationRequest(
    sim_type=SimulationType.COMSOL,
    design_state=design_state
)

result = driver.run_simulation(request)

# 5. 分析结果
if result.success:
    print(f"最高温度: {result.metrics['max_temp']:.2f}°C")
    print(f"平均温度: {result.metrics['avg_temp']:.2f}°C")

    if result.violations:
        print(f"发现 {len(result.violations)} 个违规")
        for v in result.violations:
            print(f"  - {v.description}")
else:
    print(f"仿真失败: {result.error_message}")

# 6. 断开连接
driver.disconnect()
```

---

## 相关文档

- [COMSOL驱动实现](../simulation/comsol_driver.py)
- [COMSOL测试脚本](../test_comsol.py)
- [系统配置](../config/system.yaml)
- [MPh库文档](https://mph.readthedocs.io/)

---

## 总结

✅ **COMSOL集成已完成**

- MPh库已安装（版本1.3.1）
- COMSOL驱动已实现
- 测试脚本已创建
- 文档已完善

**下一步**: 准备COMSOL模型文件并运行完整测试

---

**更新时间**: 2026-02-15
**状态**: ✅ 就绪
