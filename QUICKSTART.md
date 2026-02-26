# 快速开始指南

本指南帮助你快速上手卫星设计优化系统。

---

## 环境准备

### 1. 安装依赖

```bash
# 创建conda环境
conda create -n msgalaxy python=3.12
conda activate msgalaxy

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置系统

编辑 `config/system.yaml`:

```yaml
openai:
  api_key: "your-api-key-here"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-plus"

simulation:
  backend: "simplified"  # simplified | matlab | comsol

geometry:
  envelope_size: [1000, 800, 600]  # mm
```

---

## 基础使用

### 方式1: 使用BOM文件

#### 创建BOM文件

```bash
# 生成模板
python core/bom_parser.py template json my_bom.json

# 编辑my_bom.json，添加你的组件
```

示例BOM文件 (`config/bom_example.json`):

```json
{
  "components": [
    {
      "id": "battery_01",
      "name": "锂电池组",
      "dimensions": {"x": 200, "y": 150, "z": 100},
      "mass": 5.0,
      "power": 50.0,
      "category": "power",
      "quantity": 1,
      "material": "aluminum",
      "thermal_conductivity": 237.0,
      "max_temp": 60.0
    }
  ]
}
```

#### 运行优化

```python
from workflow.orchestrator import WorkflowOrchestrator

# 初始化
orchestrator = WorkflowOrchestrator("config/system.yaml")

# 从BOM文件运行优化
final_state = orchestrator.run_optimization(
    bom_file="config/bom_example.json",
    max_iterations=20
)
```

### 方式2: 使用命令行

```bash
# 运行优化
python -m api.cli optimize

# 查看实验列表
python -m api.cli list

# 查看实验详情
python -m api.cli show run_20260216_143022
```

---

## 查看结果

### 自动生成的文件

优化完成后，在 `experiments/run_YYYYMMDD_HHMMSS/` 目录下会生成：

```
experiments/run_20260216_143022/
├── evolution_trace.csv           # 量化指标演化
├── design_state_iter_*.json      # 每次迭代的设计状态
├── llm_interactions/             # LLM交互记录
│   ├── iter_01_meta_reasoner_req.json
│   └── iter_01_meta_reasoner_resp.json
├── visualizations/               # 可视化图表
│   ├── evolution_trace.png       # 演化轨迹
│   ├── final_layout_3d.png       # 3D布局
│   └── thermal_heatmap.png       # 热图
├── summary.json                  # 总结信息
└── report.md                     # Markdown报告
```

### 手动生成可视化

```python
from core.visualization import generate_visualizations

generate_visualizations("experiments/run_20260216_143022")
```

或使用命令行：

```bash
python core/visualization.py experiments/run_20260216_143022
```

---

## 测试系统

### 运行单元测试

```bash
# 所有测试
python -m pytest tests/ -v

# BOM解析器测试
python -m pytest tests/test_bom_parser.py -v

# 可视化测试
python -m pytest tests/test_visualization.py -v
```

### 运行集成测试

```bash
# 完整集成测试（不需要API key）
python test_integration.py

# 几何模块测试
python test_geometry.py

# 仿真模块测试
python test_simulation.py
```

---

## 常见任务

### 1. 解析和验证BOM文件

```bash
# 解析BOM文件
python core/bom_parser.py parse config/bom_example.json

# 输出：
# 解析成功: 2 个组件
# ------------------------------------------------------------
# battery_01: 锂电池组
#   尺寸: 200x150x100 mm
#   质量: 5.0 kg, 功率: 50.0 W
#   类别: power, 数量: 1
# ...
# [OK] 验证通过
```

### 2. 生成3D布局图

```python
from core.protocol import DesignState
from core.visualization import plot_3d_layout

# 加载设计状态
import json
with open("experiments/run_xxx/design_state_iter_10.json") as f:
    data = json.load(f)
    design_state = DesignState(**data)

# 生成3D图
plot_3d_layout(design_state, "my_layout.png")
```

### 3. 生成热图

```python
from core.visualization import plot_thermal_heatmap

# 准备热数据
thermal_data = {
    "battery_01": 55.3,
    "payload_01": 42.7,
    "antenna_01": 38.2
}

# 生成热图
plot_thermal_heatmap(design_state, thermal_data, "my_heatmap.png")
```

### 4. 自定义优化参数

```python
orchestrator = WorkflowOrchestrator("config/system.yaml")

final_state = orchestrator.run_optimization(
    bom_file="my_bom.json",
    max_iterations=50,           # 最大迭代次数
    convergence_threshold=0.01   # 收敛阈值
)
```

---

## 高级功能

### 使用MATLAB仿真

1. 安装MATLAB Engine API:
```bash
cd "D:\Program Files\MATLAB\R20XXx\extern\engines\python"
python setup.py install
```

2. 修改配置:
```yaml
simulation:
  backend: "matlab"
  matlab_path: "D:/Program Files/MATLAB/R2025b"
  matlab_script: "scripts/matlab/thermal_sim.m"
```

### 使用COMSOL仿真

1. 安装MPh:
```bash
pip install mph
```

2. 修改配置:
```yaml
simulation:
  backend: "comsol"
  comsol_model_path: "models/satellite_thermal_v3.mph"
```

---

## 故障排除

### 问题1: 找不到模块

```bash
# 确保在项目根目录
cd /path/to/msgalaxy

# 确保conda环境激活
conda activate msgalaxy
```

### 问题2: API密钥错误

检查 `config/system.yaml` 中的API密钥是否正确配置。

### 问题3: 编码错误

系统已处理Windows GBK编码问题，如果仍有问题：

```python
import sys
sys.stdout.reconfigure(encoding='utf-8')
```

### 问题4: 测试失败

```bash
# 清理缓存
rm -rf .pytest_cache __pycache__

# 重新运行
python -m pytest tests/ -v
```

---

## 下一步

- 阅读 [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) 了解系统架构
- 阅读 [docs/SHORT_TERM_IMPLEMENTATION.md](docs/SHORT_TERM_IMPLEMENTATION.md) 了解最新功能
- 查看 [CHANGELOG.md](CHANGELOG.md) 了解版本历史
- 探索 `examples/` 目录（如果有）查看更多示例

---

## 获取帮助

- 查看文档: `docs/` 目录
- 运行测试: `python -m pytest tests/ -v`
- 查看日志: `logs/` 目录

---

**祝你使用愉快！** 🚀
