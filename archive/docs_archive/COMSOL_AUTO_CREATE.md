# COMSOL模型自动创建工具使用说明

## 概述

本工具提供Python脚本，通过MPh库自动连接COMSOL并创建参数化的热分析模型，无需手动在COMSOL GUI中操作。

---

## 工具列表

### 1. create_simple_comsol_model.py
创建简化测试模型，用于快速验证系统集成。

**特点**:
- 单个立方体组件
- 5个参数（位置、尺寸、功率）
- 基本热源和边界条件
- 创建速度快（约30秒）
- 文件小（约100KB）

**用法**:
```bash
# 使用默认路径（models/simple_test.mph）
run_with_msgalaxy_env.bat create_simple_comsol_model.py

# 指定输出路径
run_with_msgalaxy_env.bat create_simple_comsol_model.py models/my_test.mph
```

### 2. create_comsol_model.py
创建完整卫星热分析模型，用于实际优化任务。

**特点**:
- 3个组件（电池、载荷、外壳）
- 15个参数（完整参数化）
- 2个热源（电池、载荷）
- 边界条件（外壳恒温20°C）
- 自动网格
- 稳态研究
- 创建时间较长（约1-2分钟）
- 文件较大（约500KB-1MB）

**用法**:
```bash
# 使用默认路径（models/satellite_thermal.mph）
run_with_msgalaxy_env.bat create_comsol_model.py

# 指定输出路径
run_with_msgalaxy_env.bat create_comsol_model.py models/my_satellite.mph
```

### 3. create_model.bat
便捷批处理文件，简化命令行操作。

**用法**:
```bash
# 创建简化模型（默认）
create_model.bat

# 创建简化模型（显式）
create_model.bat simple

# 创建完整模型
create_model.bat full
```

---

## 使用流程

### 快速测试流程（推荐新手）

1. **创建简化模型**
   ```bash
   create_model.bat simple
   ```

2. **测试模型连接**
   ```bash
   run_with_msgalaxy_env.bat test_comsol.py models/simple_test.mph
   ```

3. **配置系统**
   编辑 `config/system.yaml`:
   ```yaml
   simulation:
     type: "COMSOL"
     comsol_model: "e:/Code/msgalaxy/models/simple_test.mph"
     comsol_parameters:
       - "test_x"
       - "test_y"
       - "test_z"
       - "test_size"
       - "test_power"
   ```

4. **运行测试优化**
   ```bash
   run_with_msgalaxy_env.bat -m api.cli optimize --max-iter 3
   ```

### 完整优化流程

1. **创建完整模型**
   ```bash
   create_model.bat full
   ```

2. **测试模型**
   ```bash
   run_with_msgalaxy_env.bat test_comsol.py models/satellite_thermal.mph
   ```

3. **配置系统**
   编辑 `config/system.yaml`:
   ```yaml
   simulation:
     type: "COMSOL"
     comsol_model: "e:/Code/msgalaxy/models/satellite_thermal.mph"
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
   ```

4. **运行优化**
   ```bash
   run_with_msgalaxy_env.bat -m api.cli optimize --max-iter 20
   ```

---

## 前提条件

### 必需
- ✅ COMSOL Multiphysics已安装（任何版本，推荐6.0+）
- ✅ COMSOL许可证有效
- ✅ MPh库已安装（在msgalaxy环境中）
- ✅ Python 3.11或3.12（msgalaxy环境使用3.12）

### 检查清单
```bash
# 1. 检查MPh库
run_with_msgalaxy_env.bat -c "import mph; print(mph.__version__)"

# 2. 检查COMSOL安装
# 默认路径: D:\Program Files\COMSOL63

# 3. 确保没有其他COMSOL实例运行
# 关闭所有COMSOL窗口
```

---

## 脚本工作原理

### 创建流程

1. **连接COMSOL**
   - 使用 `mph.start()` 启动COMSOL客户端
   - 建立Python与COMSOL的通信

2. **创建模型**
   - 使用 `client.create()` 创建新模型
   - 设置模型名称

3. **定义参数**
   - 使用 `model.parameter()` 定义全局参数
   - 参数包括位置、尺寸、功率等

4. **创建几何**
   - 使用Java API创建几何组件
   - 设置参数化尺寸和位置
   - 构建几何

5. **设置物理场**
   - 添加热传导物理场
   - 设置热源（功率密度）
   - 设置边界条件

6. **创建网格**
   - 自动网格生成
   - 使用Normal精度

7. **创建研究**
   - 稳态热分析研究

8. **保存模型**
   - 保存为.mph文件
   - 断开COMSOL连接

---

## 故障排除

### 问题1: COMSOL连接失败

**错误信息**:
```
[1/8] 连接COMSOL服务器...
  ✗ COMSOL连接失败: ...
```

**解决方案**:
1. 确保COMSOL已安装
2. 检查COMSOL许可证是否有效
3. 关闭所有COMSOL窗口
4. 重启计算机（如果问题持续）

### 问题2: 参数定义失败

**错误信息**:
```
[3/8] 定义全局参数...
  ✗ 参数定义失败: ...
```

**解决方案**:
- 检查COMSOL版本兼容性
- 尝试升级MPh库: `pip install --upgrade mph`

### 问题3: 几何创建失败

**错误信息**:
```
[4/8] 创建参数化几何...
  ✗ 几何创建失败: ...
```

**解决方案**:
- 检查参数值是否合理（不能为负数或零）
- 检查COMSOL版本（推荐6.0+）

### 问题4: 模型保存失败

**错误信息**:
```
[8/8] 保存模型...
  ✗ 模型保存失败: ...
```

**解决方案**:
- 检查输出目录是否存在写权限
- 确保磁盘空间充足
- 检查文件路径是否包含特殊字符

### 问题5: 脚本运行很慢

**原因**:
- COMSOL启动需要时间（首次启动较慢）
- 网格生成需要时间
- 完整模型比简化模型慢

**解决方案**:
- 首次使用简化模型测试
- 等待COMSOL完全启动
- 使用SSD硬盘

---

## 高级用法

### 自定义参数

修改脚本中的参数定义部分：

```python
# 在 create_comsol_model.py 中
model.parameter('my_param', '100[mm]', description='My custom parameter')
```

### 修改几何

修改脚本中的几何创建部分：

```python
# 添加新的几何组件
new_block = geom.create('my_block', 'Block')
new_block.set('size', ['100[mm]', '100[mm]', '100[mm]'])
new_block.set('pos', ['0', '0', '0'])
```

### 修改物理场

修改脚本中的物理场设置：

```python
# 修改边界条件温度
temp.set('T0', '273.15[K]')  # 0°C

# 修改热源功率
hs.set('Q0', 1, 'my_power/(volume*1e-9)')
```

---

## 与手动创建的对比

| 特性 | 脚本创建 | 手动创建 |
|------|---------|---------|
| 速度 | 快（30秒-2分钟） | 慢（10-30分钟） |
| 一致性 | 高（每次相同） | 低（可能有差异） |
| 可重复性 | 完美 | 依赖操作者 |
| 学习曲线 | 低（运行脚本即可） | 高（需要熟悉COMSOL） |
| 灵活性 | 中（需修改脚本） | 高（GUI操作灵活） |
| 适用场景 | 标准模型、批量创建 | 复杂模型、特殊需求 |

---

## 最佳实践

1. **先测试简化模型**
   - 验证COMSOL连接
   - 验证系统集成
   - 快速迭代

2. **再使用完整模型**
   - 实际优化任务
   - 更真实的物理仿真

3. **保存模型文件**
   - 定期备份.mph文件
   - 使用版本控制（git-lfs）

4. **检查模型有效性**
   - 在COMSOL GUI中打开检查
   - 运行一次求解验证
   - 检查结果是否合理

---

## 相关文档

- [COMSOL模型文件获取指南](COMSOL_MODEL_GUIDE.md) - 完整获取指南
- [COMSOL仿真接入指南](COMSOL_GUIDE.md) - API使用和配置
- [models/README.md](../models/README.md) - 模型目录说明

---

**更新时间**: 2026-02-15
**状态**: ✅ 就绪
