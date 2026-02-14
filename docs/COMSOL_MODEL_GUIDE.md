# COMSOL模型文件获取指南

## 概述

要使用MsGalaxy系统的COMSOL仿真功能，你需要一个参数化的COMSOL Multiphysics模型文件（.mph）。本指南说明如何获取或创建这个模型文件。

---

## 方法0: 使用自动化脚本创建（最简单）⭐

### 快速开始

我们提供了自动化脚本，可以通过Python连接COMSOL并自动创建参数化模型，无需手动操作GUI。

#### 创建简化测试模型（推荐用于快速测试）

```bash
create_model.bat simple
# 或
run_with_msgalaxy_env.bat create_simple_comsol_model.py
```

这将创建 `models/simple_test.mph`，包含：
- 1个立方体组件
- 5个参数（位置、尺寸、功率）
- 基本热源和边界条件
- 适合快速测试系统集成

#### 创建完整卫星模型

```bash
create_model.bat full
# 或
run_with_msgalaxy_env.bat create_comsol_model.py
```

这将创建 `models/satellite_thermal.mph`，包含：
- 3个组件（电池、载荷、外壳）
- 15个参数（完整参数化）
- 2个热源
- 边界条件和网格
- 适合实际优化任务

#### 前提条件

- ✅ COMSOL Multiphysics已安装
- ✅ COMSOL许可证有效
- ✅ MPh库已安装（已在msgalaxy环境中）
- ✅ 没有其他COMSOL实例运行

#### 脚本说明

- [create_simple_comsol_model.py](../create_simple_comsol_model.py) - 创建简化测试模型
- [create_comsol_model.py](../create_comsol_model.py) - 创建完整卫星模型
- [create_model.bat](../create_model.bat) - 便捷批处理文件

---

## 方法1: 手动创建COMSOL模型

### 前提条件
- 已安装COMSOL Multiphysics（任何版本，推荐6.0+）
- 有效的COMSOL许可证
- 熟悉COMSOL基本操作

### 创建步骤

#### 1. 启动COMSOL并创建新模型

```
File > New > Model Wizard
```

选择：
- 3D空间维度
- 物理场：Heat Transfer in Solids（热传导）
- 研究类型：Stationary（稳态）或Time Dependent（瞬态）

#### 2. 定义全局参数

在Model Builder中，右键点击 `Global Definitions` > `Parameters`

添加以下参数（示例）：

| 参数名 | 表达式 | 描述 |
|--------|--------|------|
| `battery_01_x` | `0[mm]` | 电池X位置 |
| `battery_01_y` | `0[mm]` | 电池Y位置 |
| `battery_01_z` | `0[mm]` | 电池Z位置 |
| `battery_01_dx` | `200[mm]` | 电池X尺寸 |
| `battery_01_dy` | `150[mm]` | 电池Y尺寸 |
| `battery_01_dz` | `100[mm]` | 电池Z尺寸 |
| `battery_01_power` | `50[W]` | 电池功率 |
| `payload_01_x` | `0[mm]` | 载荷X位置 |
| `payload_01_y` | `0[mm]` | 载荷Y位置 |
| `payload_01_z` | `150[mm]` | 载荷Z位置 |
| `payload_01_dx` | `180[mm]` | 载荷X尺寸 |
| `payload_01_dy` | `180[mm]` | 载荷Y尺寸 |
| `payload_01_dz` | `120[mm]` | 载荷Z尺寸 |
| `payload_01_power` | `30[W]` | 载荷功率 |

#### 3. 创建参数化几何

在 `Geometry` 节点下创建组件：

**电池组件**:
```
Geometry > Block
- Width: battery_01_dx
- Depth: battery_01_dy
- Height: battery_01_dz
- Position: (battery_01_x, battery_01_y, battery_01_z)
```

**载荷组件**:
```
Geometry > Block
- Width: payload_01_dx
- Depth: payload_01_dy
- Height: payload_01_dz
- Position: (payload_01_x, payload_01_y, payload_01_z)
```

**外壳**:
```
Geometry > Block
- Width: 300[mm]
- Depth: 300[mm]
- Height: 300[mm]
- Position: (-150[mm], -150[mm], -150[mm])
```

#### 4. 设置材料属性

为每个几何体分配材料：
- 电池：自定义材料（热导率 k=5 W/(m·K)，密度 ρ=2700 kg/m³）
- 载荷：铝合金
- 外壳：铝合金

#### 5. 设置物理场

**Heat Transfer in Solids**:

1. 热源（Heat Source）:
   - 选择电池几何体
   - Heat source: `battery_01_power / (battery_01_dx*battery_01_dy*battery_01_dz*1e-9)` [W/m³]

2. 热源（Heat Source）:
   - 选择载荷几何体
   - Heat source: `payload_01_power / (payload_01_dx*payload_01_dy*payload_01_dz*1e-9)` [W/m³]

3. 边界条件:
   - 外壳外表面：Temperature = 20[degC]（或Heat Flux）

#### 6. 网格划分

```
Mesh > Free Tetrahedral
- Element size: Normal 或 Fine
```

#### 7. 求解器设置

```
Study > Compute
```

确保模型可以成功求解。

#### 8. 保存模型

```
File > Save As
```

保存为：`satellite_thermal.mph`

建议保存位置：`e:\Code\msgalaxy\models\satellite_thermal.mph`

---

## 方法2: 使用示例模型

### COMSOL官方示例

COMSOL自带许多示例模型，可以作为起点：

1. 打开COMSOL
2. `File > Application Libraries`
3. 搜索 "heat transfer" 或 "thermal"
4. 选择合适的模型（如 "Heat Sink"）
5. 修改为参数化几何
6. 保存为新文件

### 在线资源

- COMSOL官方模型库: https://www.comsol.com/models
- COMSOL用户论坛: https://www.comsol.com/community
- COMSOL博客教程: https://www.comsol.com/blogs

---

## 方法3: 使用简化测试模型

如果只是想测试系统集成，可以创建一个最简单的模型：

### 最小化测试模型

1. 创建单个立方体（100mm × 100mm × 100mm）
2. 定义参数：
   - `test_x = 0[mm]`
   - `test_power = 10[W]`
3. 设置热源：`test_power / 0.001` [W/m³]
4. 边界条件：外表面温度20°C
5. 求解并保存

这个模型可以快速验证MPh库连接和参数更新功能。

---

## 模型要求清单

为了与MsGalaxy系统兼容，COMSOL模型必须满足：

### 必需项
- ✅ 参数化几何（位置、尺寸使用全局参数）
- ✅ 参数命名规范：`<component_id>_<property>`
- ✅ 热分析物理场（Heat Transfer in Solids）
- ✅ 可成功求解（无错误）

### 推荐项
- ✅ 合理的网格设置（不要太细，影响速度）
- ✅ 稳态求解器（比瞬态快）
- ✅ 简化几何（去除不必要的细节）
- ✅ 合理的边界条件

### 可选项
- 结构分析（Solid Mechanics）
- 多物理场耦合
- 瞬态分析

---

## 配置系统使用你的模型

创建或获取模型后，需要在系统配置中指定：

### 1. 编辑配置文件

编辑 `config/system.yaml`:

```yaml
simulation:
  type: "COMSOL"

  # 指定你的模型文件路径（使用绝对路径）
  comsol_model: "e:/Code/msgalaxy/models/satellite_thermal.mph"

  # 列出模型中定义的所有参数
  comsol_parameters:
    - "battery_01_x"
    - "battery_01_y"
    - "battery_01_z"
    - "battery_01_dx"
    - "battery_01_dy"
    - "battery_01_dz"
    - "battery_01_power"
    - "payload_01_x"
    - "payload_01_y"
    - "payload_01_z"
    - "payload_01_dx"
    - "payload_01_dy"
    - "payload_01_dz"
    - "payload_01_power"

  constraints:
    max_temp_c: 50.0
    max_stress_mpa: 100.0
```

### 2. 测试模型连接

```bash
run_with_msgalaxy_env.bat test_comsol.py e:/Code/msgalaxy/models/satellite_thermal.mph
```

预期输出：
```
[OK] MPh库导入成功
[OK] COMSOL连接成功
[OK] 模型加载成功
[OK] 参数更新成功
[OK] 仿真成功完成
```

### 3. 运行完整优化

```bash
run_with_msgalaxy_env.bat -m api.cli optimize --max-iter 5
```

---

## 故障排除

### 问题1: 没有COMSOL许可证

**解决方案**:
- 使用简化物理引擎（默认）
- 申请COMSOL试用许可证
- 使用学校/公司的COMSOL许可证

### 问题2: COMSOL版本不兼容

**解决方案**:
- MPh库支持COMSOL 5.x和6.x
- 如果遇到问题，尝试升级MPh库：`pip install --upgrade mph`

### 问题3: 模型太复杂，求解太慢

**解决方案**:
- 简化几何（去除圆角、倒角等细节）
- 使用粗网格
- 使用稳态求解器
- 减少组件数量

### 问题4: 参数未找到错误

**解决方案**:
- 检查参数名称拼写
- 确保参数在Global Definitions中定义
- 参数名称区分大小写

---

## 示例模型下载

如果你需要一个现成的示例模型，可以：

1. **联系COMSOL技术支持**获取卫星热分析示例
2. **参考COMSOL文档**中的航天器热管理案例
3. **使用本指南创建**一个简单的测试模型

---

## 总结

获取COMSOL模型文件的三种方式：

1. **自己创建**（推荐）- 完全控制，符合需求
2. **修改示例**（快速）- 基于COMSOL自带示例
3. **简化测试**（最快）- 仅用于验证集成

**下一步**:
1. 创建或获取.mph模型文件
2. 将模型文件放在 `models/` 目录
3. 更新 `config/system.yaml` 配置
4. 运行测试：`run_with_msgalaxy_env.bat test_comsol.py models/your_model.mph`

---

**更新时间**: 2026-02-15
**状态**: ✅ 就绪
