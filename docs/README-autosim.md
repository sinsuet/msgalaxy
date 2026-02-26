# AutoFlowSim - 自动化流体仿真优化平台

## 项目概述

AutoFlowSim 是一个集成化的自动化流体仿真优化平台，专为工程优化设计而开发。该项目通过将 MATLAB 优化算法、CFD 仿真软件（BMDO/TBDO）、Python 自动化控制和3D几何变形技术有机结合，实现了从参数优化到流体仿真再到结果分析的完整自动化工作流程。

该平台主要面向涡轮机械、航空航天等领域的工程优化问题，支持多目标、多约束的复杂优化任务，能够显著提高工程设计效率，减少人工干预。

## 核心特性

- **多软件集成**：无缝集成 MATLAB 优化算法、BMDO/TBDO CFD 仿真软件
- **自动化控制**：基于 Python 的 GUI 自动化控制，实现无人值守运行
- **3D 几何变形**：内置 3D 自由变形（FFD）技术，支持复杂几何体参数化
- **并行计算支持**：支持多案例并行优化，提高计算效率
- **灵活的架构**：模块化设计，易于扩展和定制
- **跨平台兼容**：核心功能支持 Windows/Linux，部分 GUI 功能仅限 Windows

## 项目架构

### 整体架构图

```
+------------------+     +------------------+     +------------------+
|   MATLAB 优化    |<--->|   Python 主控    |<--->|   CFD 仿真软件   |
|   算法引擎       |     |   (AutoFlowSim)  |     |   (BMDO/TBDO)    |
+------------------+     +------------------+     +------------------+
         ^                        ^                        ^
         |                        |                        |
+------------------+     +------------------+     +------------------+
|   3D FFD 变形    |     |   服务器/客户端  |     |   Numeca/StarCCM |
|   几何处理       |     |   分布式计算     |     |   后处理工具     |
+------------------+     +------------------+     +------------------+
```

### 主要模块说明

#### 1. 主控模块 (`MAIN.py`)
- 程序入口点，协调各模块运行
- 控制优化迭代循环
- 配置工程路径和参数

#### 2. BMDO 模块 (`BMDO/`)
- **`run_BMDO.py`**: BMDO 仿真运行主接口
- **`FILE_BMDO.py`**: 文件操作和工程配置
- **`slave_BMDO.py`**: BMDO 自动化控制脚本
- **`clearFile.py`**: 清理临时文件
- **`mouseNow.py`**: 鼠标位置记录工具

#### 3. TBDO 模块 (`TBDO/`)
- **`Start_New_Cases.py`**: 批量创建优化案例
- **`STRUCTURE_TBDO.py`**: 工程目录结构管理
- **`MAIN_1.py` / `MAIN_2.py`**: 工程配置和结果提取
- **`make_Genetic.py`**: 遗传算法参数文件生成
- **`get_result.py`**: 仿真结果数据提取

#### 4. MATLAB 接口模块 (`MATLAB/`)
- **`run_Matlab.py`**: MATLAB 引擎调用接口
- 支持 MATLAB 优化算法的 Python 调用

#### 5. 服务器/客户端模块 (`Server/`, `EngClient/`)
- **`TsEngServer.py`**: 优化服务器，处理客户端请求
- **`EngCli.py`**: 客户端接口，发送样本数据
- **`EngServer_exe.py`**: 可执行服务器版本
- 支持分布式计算和远程优化

#### 6. 3D 自由变形模块 (`3D-Free-Form-Deformation/`)
- **`FFD.py`**: 3D 自由变形核心算法
- **`VtkModel.py`**: VTK 3D 可视化
- **`UI.py`**: PyQt 图形用户界面
- **`ObjProcessing.py`**: OBJ 文件处理

#### 7. CFD 后处理模块
- **`pythonForNumeca/`**: Numeca 仿真结果处理
- **`pythonForStarCCM/`**: StarCCM+ 自动化脚本

## 安装与配置

### 系统要求

- **操作系统**: Windows 10/11 (推荐), Linux (部分功能)
- **Python 版本**: Python 3.6+ (项目使用 Python 3.6 环境)
- **MATLAB 版本**: R2018a 或更高版本
- **CFD 软件**: BMDO, TBDO (商业软件)

### 依赖库安装

```bash
# 基础依赖
pip install numpy scipy matplotlib
pip install pyautogui pywin32
pip install vtk==8.1.0  # 3D 可视化
pip install PyQt5      # GUI 界面 (Windows)

# MATLAB 引擎 (需要在 MATLAB 中配置)
# 进入 MATLAB 安装目录: matlabroot/extern/engines/python
# 执行: python setup.py install
```

### 环境配置步骤

1. **安装必要软件**
   - 安装 Anaconda (推荐)
   - 安装 MATLAB
   - 安装 BMDO/TBDO CFD 软件
   - 安装 PyCharm (可选，用于开发)

2. **配置 MATLAB Python API**
   ```bash
   # 进入 MATLAB 引擎目录
   cd "C:\Program Files\MATLAB\R2021a\extern\engines\python"
   python setup.py install
   ```

3. **配置工程文件**
   - 准备 BMDO/TBDO 工程文件
   - 准备 MATLAB 优化算法文件
   - 修改 `MAIN.py` 中的路径配置：
     ```python
     func_name = 'NashEngpy'  # MATLAB 函数名
     pathRoot = 'E:\WQN\EngTest_20210714\OptEng'  # 工程根目录
     outPutfileP = "E:\WQN\EngRecord\outputfileP.txt"  # 输出文件路径
     outPutfileV = "E:\WQN\EngRecord\outputfileV.txt"
     ```

4. **添加到环境变量**
   - 将 Python 3.6 环境路径添加到系统 PATH
   - 确保包含必要的第三方库：matlab.engine, win32com, pyautogui

## 使用方法

### 基本工作流程

1. **初始化阶段**
   - 运行 `TBDO/Start_New_Cases.py` 创建优化案例目录结构
   - 准备初始样本数据文件（LHS 采样等）

2. **优化循环**
   ```python
   # MAIN.py 中的标准循环
   for ii in range(500):  # 最大迭代次数
       run_Matlab.run_Matlab(func_name)  # MATLAB 优化算法
       sam = outPutfileP                 # 获取新样本
       run_BMDO.run_BMDO(pathRoot, outPutfileV, sam)  # CFD 仿真
   ```

3. **结果分析**
   - 提取 CFD 仿真结果
   - 更新优化模型
   - 生成最终优化结果

### 具体操作步骤

#### BMDO 优化流程

1. **获取新的优化样本数据**
2. **更新配置文件**
   - 修改 `Genetic.con` 文件（遗传算法参数）
   - 修改 `CFDmethod.con` 文件（CFD 进程数）
3. **启动 BMDO**
   - 管理员模式运行 BMDO
   - GUI 自动化操作：
     - 点击"文件" → "打开优化工程" (Ctrl+P)
     - 输入 .dop 工程文件名
     - 点击"开始"按钮
4. **执行优化**
   - 点击"性能分析与优化"
   - 点击"性能优化"
5. **结果导出**
   - 关闭程序并导出数据

#### TBDO 优化流程

1. **建立文件结构**
   ```
   Casei/
   ├── Point/
   │   ├── iter0_h.dat
   │   └── iter0_l.dat
   ├── Value/
   │   ├── iter0_h.dat
   │   └── iter0_l.dat
   └── Datavase/
       └── iter0_h
   Template/
   Data_collect.xlsx
   ```

2. **配置工程文件** (`MAIN_1.py`)
3. **运行 TBDO 仿真**
4. **提取结果并保存** (`MAIN_2.py`)
5. **MATLAB 建模和加点** (`Main_Adaptive.m`)

### 3D 自由变形使用

#### 命令行模式 (跨平台)
```bash
python 3D-Free-Form-Deformation/VtkModel.py
```

#### GUI 模式 (仅 Windows)
```bash
python 3D-Free-Form-Deformation/UI.py
```

#### 文件格式要求
- OBJ 文件：必须以 `.obj` 为后缀
- FFD 文件：必须以 `.FFD` 为后缀

## 优化算法特点

### 样本生成策略

1. **初始加点**
   - 第一次 PCE 建模后不进行单独加点
   - 使用 LHS (拉丁超立方采样) 生成初始样本

2. **局部优化**
   - 所有并行优化的加点集合作为一次加点
   - 不在优化过程中更新代理模型

3. **全局优化**
   - 所有全局加点集合作为一次加点
   - 期间不进行模型更新

### 优化状态管理

系统支持三种优化情况的自动识别：
1. 上次全局优化，本次局部优化
2. 上次局部优化，本次局部优化  
3. 上次局部优化，本次全局优化

## 分布式计算支持

### 服务器模式

- **启动服务器**: `Server/TsEngServer.py`
- **客户端调用**: `EngClient/EngCli.py`
- **端口配置**: 默认 21567
- **支持并发**: 多客户端同时连接

### 并行优化实现

1. **CFD 计算前**
   - 记录 `num.dat` 文件
   - 合并所有加点数据

2. **CFD 计算后**
   - 分别提取计算结果为 dat 文件
   - 分开保存计算结果文件
   - 在 Excel 中分别记录数据

## CFD 后处理工具

### Numeca 相关工具

- **`extractrst.py`**: 提取仿真结果
- **`readmf.py`**: 读取网格文件
- **`makeSTG.py`**: 生成 STG 文件
- **`ExtractMap2Tec.py`**: 导出 Tecplot 格式

### StarCCM+ 相关工具

- **`autoCFD_ParkB.py`**: 自动化 CFD 脚本
- **`autooptimization.py`**: 优化自动化脚本

## 注意事项与最佳实践

### 运行注意事项

1. **计算机使用**
   - 优化过程中不要操作计算机
   - 确保没有其他 CFD 计算同时进行
   - 保持屏幕分辨率和窗口位置稳定

2. **文件管理**
   - 定期备份重要工程文件
   - 清理临时文件避免磁盘空间不足
   - 使用 `clearFile.py` 和 `Clear.py` 进行清理

3. **错误处理**
   - 检查 MATLAB 函数是否已添加到路径
   - 验证 CFD 软件许可证状态
   - 确认文件路径权限

### 性能优化建议

1. **硬件配置**
   - 推荐多核 CPU 和大内存
   - SSD 存储提高 I/O 性能
   - 独立显卡支持 3D 可视化

2. **并行策略**
   - 根据 CPU 核心数设置 CFD 进程数
   - 合理分配内存给每个仿真任务
   - 使用分布式计算处理大规模优化

## 项目文件结构详解

```
AutoFlowSim-master/
├── 3D-Free-Form-Deformation/    # 3D 几何变形模块
├── BMDO/                       # BMDO 仿真控制模块
├── EngClient/                  # 优化客户端接口
├── MATLAB/                     # MATLAB 引擎接口
├── Server/                     # 优化服务器模块
├── TBDO/                       # TBDO 仿真控制模块
├── TEST/                       # 测试脚本和示例
├── pythonForNumeca/           # Numeca 后处理工具
├── pythonForStarCCM/          # StarCCM+ 自动化工具
├── MAIN.py                    # 主程序入口
├── readSam.py                 # 样本读取工具
├── updateMAPs.py              # MAP 文件更新工具
├── README20200329.txt         # 历史文档
└── README20210701.txt         # 配置说明
```

## 开发与维护

### 代码规范

- Python 代码遵循 PEP 8 规范
- MATLAB 代码使用英文注释
- 文件命名使用驼峰命名法或下划线命名法
- 模块间接口保持向后兼容

### 扩展开发

1. **添加新 CFD 软件支持**
   - 创建新的自动化控制模块
   - 实现相应的文件处理函数
   - 更新主控程序接口

2. **集成新优化算法**
   - 在 MATLAB 中实现算法
   - 更新 Python-MATLAB 接口
   - 配置相应的输入输出格式

3. **增强 3D 可视化**
   - 扩展 FFD 控制点类型
   - 添加新的几何处理算法
   - 改进用户交互体验

## 版本历史

- **2020.03**: 初始版本，基础 BMDO/TBDO 集成
- **2021.07**: 完善 MATLAB 接口，添加分布式计算支持
- **2021.08**: 增强服务器/客户端架构，改进 3D FFD 模块
- **持续更新**: CFD 后处理工具完善，性能优化

## 贡献者

- **3D-Free-Form-Deformation 模块**:
  - 何占魁 (AaronHeee)
  - 冉诗菡 (Rshcaroline)  
  - 王艺楷 (Yikai-Wang)

- **核心优化平台**: 项目原作者团队

## 许可证

本项目仅供学术研究和工程应用参考。具体许可证信息请参考各子模块的 LICENSE 文件。

---

*文档最后更新: 2026年2月14日*

*注意: 本项目涉及商业软件 (BMDO/TBDO/MATLAB)，请确保您拥有相应的合法授权。*