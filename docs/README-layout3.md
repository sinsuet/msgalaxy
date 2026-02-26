# 卫星舱三维自动布局系统 (Layout3DCube)

![卫星舱三维自动布局系统](https://img.shields.io/badge/Python-3.8%2B-blue) ![License](https://img.shields.io/badge/License-MIT-green)

**Layout3DCube** 是一个专业的三维自动布局优化工具，专为卫星舱、航天器设备舱等复杂空间内的设备布局问题而设计。该系统基于先进的3D装箱算法，支持多种约束条件，提供完整的从布局计算到CAD导出的工作流程。

## 目录

- [项目概述](#项目概述)
- [核心功能特性](#核心功能特性)
- [技术架构](#技术架构)
- [安装指南](#安装指南)
  - [系统要求](#系统要求)
  - [快速安装](#快速安装)
  - [可选CAD支持](#可选cad支持)
- [快速开始](#快速开始)
- [配置详解](#配置详解)
  - [舱体包络配置](#舱体包络配置)
  - [禁区配置](#禁区配置)
  - [BOM合成配置](#bom合成配置)
  - [装箱参数配置](#装箱参数配置)
- [高级使用](#高级使用)
  - [批量生成](#批量生成)
  - [外部数据加载](#外部数据加载)
  - [数据集生成](#数据集生成)
- [输出文件说明](#输出文件说明)
- [API文档](#api文档)
- [示例与用例](#示例与用例)
- [性能优化](#性能优化)
- [贡献指南](#贡献指南)
- [许可证](#许可证)
- [常见问题](#常见问题)

## 项目概述

### 背景与目标

在航空航天、电子设备制造、物流仓储等领域，三维空间内的设备布局是一个复杂的优化问题。传统的手动布局方法效率低下，难以满足现代工程对精度和效率的要求。

**Layout3DCube** 旨在解决以下核心问题：

1. **自动化布局优化**：基于智能算法自动完成三维空间内的设备排布
2. **约束处理**：支持AABB（轴对齐包围盒）舱体约束、Keep-out禁区处理
3. **工程实用性**：提供可直接用于工程设计的CAD输出格式
4. **数据驱动**：支持生成训练数据集，用于机器学习和仿真研究

### 应用场景

- **卫星舱设备布局**：航天器内部设备的最优排布
- **电子机柜设计**：服务器机柜、控制柜内的设备布局
- **物流仓储优化**：货物在集装箱或仓库中的三维排布
- **制造车间规划**：生产设备在车间内的空间布局
- **AI辅助设计**：为机器学习模型生成训练数据

## 核心功能特性

### ✅ 基础功能

- **AABB舱体约束**：支持任意尺寸的轴对齐包围盒作为布局空间
- **Keep-out禁区处理**：定义禁止放置区域，确保关键区域不受干扰
- **自动壳体计算**：根据占空比和壁厚自动计算最优外壳尺寸
- **多启动优化策略**：通过多次随机启动提高装箱率和布局质量

### ✅ 设备管理

- **BOM随机合成**：自动生成符合指定参数范围的设备清单
- **设备间隙控制**：精确控制设备间的最小安全距离
- **多类别支持**：支持不同类别的设备，便于分类管理和可视化
- **质量与功率属性**：跟踪设备的质量和功率消耗，用于系统级分析

### ✅ 可视化与输出

- **三维可视化预览**：基于matplotlib的3D渲染，直观展示布局结果
- **STEP文件导出**：通过CadQuery导出标准CAD格式，支持直接导入CAD软件
- **JSON元数据输出**：完整的布局信息，便于后续处理和分析
- **版本化输出管理**：自动创建带版本号的输出目录，避免文件覆盖

### ✅ 高级特性

- **多面贴壁布局**：支持设备沿舱体六个面进行贴壁安装
- **切层算法**：将复杂3D空间分解为多个2D平面进行优化
- **重合度检测**：自动检测和量化布局中的重合冲突
- **可扩展架构**：模块化设计，便于添加新功能和算法

## 技术架构

### 系统架构图

```
┌─────────────────────────────────────────────────────────┐
│                    用户接口层                           │
│  ┌─────────────┐    ┌─────────────────┐               │
│  │  main.py    │    │ batch_generate.py│               │
│  └─────────────┘    └─────────────────┘               │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                    核心业务层                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ dataset_    │  │ synth_bom.py│  │ keepout_    │     │
│  │ generator.py│  │             │  │ split.py    │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
│        │                │                │              │
│  ┌─────────────────────────────────────────────────┐   │
│  │            pack_py3dbp.py                      │   │
│  │  (基于py3dbp的多启动装箱算法)                   │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                    输出层                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │ viz3d.py    │    │ export_cad.py│    │ util.py     │ │
│  │ (3D可视化)  │    │ (CAD导出)   │    │ (工具函数)  │ │
│  └─────────────┘    └─────────────┘    └─────────────┘ │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                    数据结构层                           │
│                  ┌─────────────┐                        │
│                  │ schema.py   │                        │
│                  │ (Part, AABB,│                        │
│                  │  Envelope)  │                        │
│                  └─────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

### 核心模块说明

| 模块 | 功能描述 | 依赖 |
|------|----------|------|
| `schema.py` | 定义核心数据结构：`Part`(设备)、`AABB`(包围盒)、`Envelope`(舱体) | numpy |
| `synth_bom.py` | BOM(物料清单)随机合成，生成符合参数范围的设备列表 | random, numpy |
| `keepout_split.py` | AABB六面切分算法，处理禁区并生成可用子容器 | numpy |
| `pack_py3dbp.py` | 装箱核心算法，实现多启动、多面贴壁、切层布局 | py3dbp, numpy |
| `viz3d.py` | 三维可视化，生成布局预览图 | matplotlib |
| `export_cad.py` | CAD导出，生成STEP文件和元数据 | cadquery |
| `util.py` | 工具函数，包括配置加载、版本目录管理等 | pyyaml, pathlib |

### 数据流

1. **输入阶段**：加载YAML配置文件 → 生成设备BOM → 创建舱体和禁区
2. **处理阶段**：切分可用空间 → 多启动装箱优化 → 重合度评估
3. **输出阶段**：3D可视化 → CAD导出 → 元数据保存 → 运行信息记录

## 安装指南

### 系统要求

- **操作系统**：Windows, macOS, Linux
- **Python版本**：3.8 或更高版本
- **内存**：建议8GB以上（大型布局可能需要更多内存）
- **磁盘空间**：至少1GB可用空间

### 快速安装

```bash
# 1. 克隆项目
git clone https://github.com/your-username/layout3dcube.git
cd layout3dcube

# 2. 创建虚拟环境（推荐）
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# 3. 安装核心依赖
pip install -r requirements.txt
```

### 可选CAD支持

如果需要STEP文件导出功能，需要额外安装CadQuery：

```bash
# 推荐使用conda安装（CadQuery官方推荐方式）
conda install -c conda-forge cadquery

# 或者使用pip（可能遇到依赖问题）
pip install cadquery
```

> **注意**：CadQuery的pip安装在某些系统上可能比较困难，强烈建议使用conda。

### 验证安装

```bash
# 测试基本功能
python main.py config/demo.yaml

# 如果安装了CAD支持，检查STEP文件是否生成
ls out/demo_v001/layout.step
```

## 快速开始

### 基本使用

```bash
# 使用默认配置文件
python main.py

# 使用自定义配置文件
python main.py config/your_config.yaml
```

### 输出目录结构

每次运行都会自动生成带版本号的输出目录：

```
out/
├── demo_v001/           # 第一次运行
│   ├── layout.step      # CAD几何模型（STEP格式）
│   ├── layout.meta.json # 设备元数据（JSON格式）
│   ├── bins_preview.png # 子容器预览图
│   ├── packed_preview.png # 装箱结果预览图
│   └── run_info.txt     # 运行信息日志
├── demo_v002/           # 第二次运行
└── your_config_v001/    # 其他配置文件的输出
```

### 示例输出

运行成功后，您将看到类似以下的输出：

```
============================================================
卫星舱三维自动布局系统 v0.2
============================================================

[1/3] 加载配置: config/demo.yaml
  输出目录: out/demo_v001
  外壳尺寸: [2000.0, 1500.0, 1000.0]
  内部尺寸: [1990.0, 1490.0, 990.0]
  禁区数量: 0

  === 启动 1/10 ===
  The face 0 of bin 0 placed_face: ['part_001', 'part_002', ...]

最优结果: 重合对数=0, 放置 20/20 件, 体积 1234567, 使用 1 个容器

[8/8] 导出CAD

已保存运行信息: out/demo_v001/run_info.txt

============================================================
完成！输出目录: out/demo_v001
============================================================
```

## 配置详解

配置文件采用YAML格式，分为四个主要部分：

### 舱体包络配置 (`envelope`)

```yaml
envelope:
  auto_envelope: true          # 是否自动计算外壳尺寸
  size_mm: [2000, 1500, 1000]  # 手动指定外壳尺寸（当auto_envelope=false时使用）
  origin: center               # 原点位置：center（中心）或 corner（一角）
  fill_ratio: 0.30             # 占空比（设备总体积 / 内部可用体积）
  size_ratio: [1.7, 1.8, 1.5]  # 自动尺寸比例 x:y:z
  shell_thickness_mm: 5.0      # 壁厚（mm），0表示片体
```

#### 自动壳体计算逻辑

当 `auto_envelope=true` 时，系统按以下步骤计算外壳尺寸：

1. **汇总设备体积**：计算所有设备的总体积
2. **计算目标内部体积**：`内部体积 = 设备总体积 / fill_ratio`
3. **按比例分配尺寸**：根据 `size_ratio` 计算各方向尺寸
4. **添加壁厚**：`外壳尺寸 = 内部尺寸 + 2 * shell_thickness_mm`

#### 原点设置

- `center`：坐标系原点位于舱体几何中心
- `corner`：坐标系原点位于舱体最小角点（min_x, min_y, min_z）

### 禁区配置 (`keep_out`)

```yaml
keep_out:
  - min_mm: [500, 200, 200]
    max_mm: [800, 500, 600]
    tag: "sensor_fov"           # 传感器视场禁区
  
  - min_mm: [-400, -300, -200]
    max_mm: [-200, 300, 200]
    tag: "antenna_clearance"    # 天线净空禁区
```

每个禁区由最小点和最大点定义的AABB包围盒，`tag`字段用于标识禁区用途。

### BOM合成配置 (`synth`)

```yaml
synth:
  n_parts: 20                   # 设备数量
  dims_min_mm: [50, 50, 50]     # 最小尺寸 [x, y, z]
  dims_max_mm: [180, 200, 150]  # 最大尺寸 [x, y, z]
  mass_range_kg: [0.5, 5.0]     # 质量范围 [min, max] kg
  power_range_W: [1, 50]        # 功率范围 [min, max] W
  categories:                   # 设备类别列表
    - payload
    - avionics
    - power
  seed: 42                      # 随机种子（确保结果可复现）
```

### 装箱参数配置

```yaml
clearance_mm: 20                 # 设备间最小间隙（mm）
multistart: 10                  # 多启动次数（值越大，结果越好但耗时越长）
```

## 高级使用

### 批量生成

使用 `batch_generate.py` 脚本进行批量布局生成：

```bash
# 批量生成多个配置
python batch_generate.py --config-dir config/ --output-dir output/

# 指定特定配置文件
python batch_generate.py --config-file config/demo.yaml --count 5
```

### 外部数据加载

系统支持从外部数据源加载设备信息：

```python
# examples/load_external_data.py
from src.dataset_generator import load_external_parts

# 从CSV文件加载
parts = load_external_parts("data/external_devices.csv")

# 从JSON文件加载
parts = load_external_parts("data/devices.json")
```

### 数据集生成

为机器学习和仿真研究生成训练数据集：

```bash
# 生成网格化数据集
python examples/generate_dataset.py

# 自定义参数
python examples/generate_dataset.py --input out/demo_v001/layout.meta.json --output dataset/ --grid 128,128,128
```

生成的数据集包含：
- **Occupancy Grid**：3D占用网格
- **Semantic Labels**：语义标签（按设备类别）
- **Instance Masks**：实例分割掩码
- **Metadata**：设备属性信息

## 输出文件说明

### STEP文件 (`layout.step`)

- **格式**：ISO 10303-21 (STEP AP214)
- **内容**：包含舱体外壳和所有设备的3D几何模型
- **兼容性**：可在SolidWorks、AutoCAD、Fusion 360等主流CAD软件中打开

### 元数据文件 (`layout.meta.json`)

```json
{
  "_envelope": {
    "outer_size": [2000.0, 1500.0, 1000.0],
    "inner_size": [1990.0, 1490.0, 990.0],
    "thickness_mm": 5.0,
    "fill_ratio": 0.3
  },
  "parts": [
    {
      "id": "part_001",
      "dims": [120.0, 80.0, 60.0],
      "mass": 2.3,
      "power": 15.5,
      "category": "payload",
      "color": [255, 0, 0, 255],
      "position": [100.0, 200.0, 300.0],
      "bin_index": 0,
      "mount_face": 4,
      "mount_point": [160.0, 240.0, 300.0]
    }
  ],
  "keepouts": [],
  "stats": {
    "total_parts": 20,
    "placed_parts": 20,
    "unplaced_parts": 0,
    "total_volume": 1234567.0,
    "total_mass": 45.6,
    "total_power": 345.2
  }
}
```

### 运行信息文件 (`run_info.txt`)

包含详细的运行统计信息，便于结果分析和比较。

## API文档

### 核心类

#### `Part` 类

```python
class Part:
    def __init__(self, id: str, dims: Tuple[float, float, float], 
                 mass: float, power: float, category: str, 
                 color: Tuple[int, int, int, int], clearance_mm: float = 0.0):
        pass
    
    def get_actual_dims(self) -> np.ndarray:
        """返回实际尺寸"""
    
    def get_install_dims(self, face_id: int) -> np.ndarray:
        """根据安装面计算安装尺寸"""
    
    def get_actual_position(self) -> np.ndarray:
        """计算实际部件的最小角坐标"""
```

#### `AABB` 类

```python
class AABB:
    def __init__(self, min: np.ndarray, max: np.ndarray):
        pass
    
    def volume(self) -> float:
        """计算体积"""
    
    def size(self) -> np.ndarray:
        """获取尺寸"""
    
    def center(self) -> np.ndarray:
        """获取中心点"""
```

#### `Envelope` 类

```python
class Envelope:
    def __init__(self, outer: AABB, inner: AABB, thickness_mm: float, 
                 fill_ratio: float, size_ratio: Tuple[float, float, float]):
        pass
    
    def outer_size(self) -> np.ndarray:
        """返回外壳尺寸"""
    
    def inner_size(self) -> np.ndarray:
        """返回内部尺寸"""
```

### 主要函数

#### `main(config_path: str)`

主流程入口函数，执行完整的布局优化流程。

#### `synth_bom(config: dict, clearance_mm: float = 0.0) -> List[Part]`

根据配置生成设备BOM列表。

#### `build_bins(envelope: AABB, keepouts: List[AABB]) -> List[AABB]`

将舱体空间切分为可用的子容器。

#### `export_cad(envelope: Envelope, parts: List[Part], 
             step_path: str, meta_path: str)`

导出CAD文件和元数据。

## 示例与用例

### 示例1：基本卫星舱布局

```yaml
# config/satellite_basic.yaml
envelope:
  auto_envelope: true
  fill_ratio: 0.25
  size_ratio: [2.0, 1.5, 1.0]
  shell_thickness_mm: 10.0

synth:
  n_parts: 15
  dims_min_mm: [100, 80, 50]
  dims_max_mm: [300, 250, 200]
  mass_range_kg: [1.0, 10.0]
  power_range_W: [5, 100]
  categories: ["payload", "avionics", "power", "thermal"]
  seed: 123

clearance_mm: 25
multistart: 5
```

### 示例2：带禁区的复杂布局

```yaml
# config/satellite_complex.yaml
envelope:
  auto_envelope: false
  size_mm: [3000, 2000, 1500]
  origin: center
  shell_thickness_mm: 0.0  # 片体模式

keep_out:
  - min_mm: [-500, -300, -200]
    max_mm: [500, 300, 200]
    tag: "central_tube"
  
  - min_mm: [1000, -1000, -500]
    max_mm: [1200, 1000, 500]
    tag: "solar_panel_mount"

synth:
  n_parts: 30
  dims_min_mm: [60, 60, 60]
  dims_max_mm: [250, 200, 180]
  mass_range_kg: [0.8, 8.0]
  power_range_W: [2, 80]
  categories: ["payload", "avionics", "power", "communication"]
  seed: 456

clearance_mm: 15
multistart: 15
```

## 性能优化

### 参数调优建议

| 场景 | `multistart` | `clearance_mm` | 备注 |
|------|-------------|----------------|------|
| 快速原型 | 1-3 | 较大值 | 快速验证概念 |
| 工程应用 | 5-10 | 精确值 | 平衡质量和效率 |
| 高精度要求 | 15-20 | 最小值 | 最优布局结果 |

### 内存优化

对于大型布局（>100个设备），建议：

1. **增加虚拟内存**：确保系统有足够的交换空间
2. **分批处理**：将大型布局分解为多个子任务
3. **降低可视化分辨率**：减少3D渲染的内存消耗

### 并行处理

当前版本为单线程实现，但可以通过以下方式实现并行：

```python
# 批量生成时使用多进程
import multiprocessing as mp

def process_config(config_file):
    # 处理单个配置文件
    pass

if __name__ == "__main__":
    config_files = ["config1.yaml", "config2.yaml", ...]
    with mp.Pool(processes=4) as pool:
        pool.map(process_config, config_files)
```

## 贡献指南

欢迎贡献代码、文档或报告问题！

### 开发环境设置

```bash
# 克隆项目
git clone https://github.com/your-username/layout3dcube.git
cd layout3dcube

# 安装开发依赖
pip install -r requirements.txt
pip install pytest black flake8

# 运行测试
python -m pytest tests/

# 代码格式化
black .
flake8 .
```

### 提交规范

1. **分支命名**：`feature/xxx`、`fix/xxx`、`docs/xxx`
2. **提交信息**：使用清晰的描述，遵循[Conventional Commits](https://www.conventionalcommits.org/)
3. **测试覆盖**：新增功能必须包含相应的单元测试
4. **文档更新**：修改功能时同步更新相关文档

### 测试策略

- **单元测试**：覆盖核心算法和数据结构
- **集成测试**：验证端到端工作流程
- **回归测试**：确保新功能不破坏现有功能

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

```
MIT License

Copyright (c) 2026 Layout3DCube Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 常见问题

### Q1: CadQuery安装失败怎么办？

**A**: CadQuery在某些系统上安装比较困难，建议：

1. 使用conda安装：`conda install -c conda-forge cadquery`
2. 如果不需要CAD导出功能，可以跳过CadQuery安装，系统会自动降级为仅生成元数据
3. 在Docker容器中运行：使用包含CadQuery的预配置镜像

### Q2: 布局结果中有设备重合怎么办？

**A**: 设备重合通常由以下原因造成：

1. **间隙设置过小**：增加 `clearance_mm` 参数值
2. **启动次数不足**：增加 `multistart` 参数值
3. **空间不足**：检查 `fill_ratio` 是否设置过高，或手动增大舱体尺寸

### Q3: 如何处理非矩形设备？

**A**: 当前版本仅支持矩形设备（AABB）。对于非矩形设备，建议：

1. **包围盒近似**：使用设备的最小包围盒进行布局
2. **后处理验证**：在CAD软件中进行精确的干涉检查
3. **自定义扩展**：修改 `schema.py` 添加自定义几何类型支持

### Q4: 系统运行很慢怎么办？

**A**: 性能优化建议：

1. **减少设备数量**：先用少量设备测试配置
2. **降低启动次数**：将 `multistart` 设置为较小值进行快速迭代
3. **简化禁区**：减少禁区数量和复杂度
4. **硬件升级**：使用更高性能的CPU和更多内存

### Q5: 如何自定义设备类别和属性？

**A**: 修改配置文件中的 `categories` 列表，并在代码中扩展颜色映射：

```python
# 在viz3d.py中添加自定义颜色映射
CATEGORY_COLORS = {
    "payload": (255, 0, 0, 255),      # 红色
    "avionics": (0, 255, 0, 255),     # 绿色
    "power": (0, 0, 255, 255),        # 蓝色
    "thermal": (255, 255, 0, 255),    # 黄色
    "custom": (255, 0, 255, 255)      # 紫色
}
```

---

**Layout3DCube** 致力于为工程师和研究人员提供强大而易用的三维布局优化解决方案。如果您有任何问题、建议或需要技术支持，请通过GitHub Issues联系我们！