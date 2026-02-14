# COMSOL模型文件目录

## 说明

此目录用于存放COMSOL Multiphysics模型文件（.mph）。

## 使用方法

1. 将你的COMSOL模型文件（.mph）放在此目录
2. 确保模型包含参数化几何（参见 [../docs/COMSOL_MODEL_GUIDE.md](../docs/COMSOL_MODEL_GUIDE.md)）
3. 在 `config/system.yaml` 中配置模型路径
4. 运行测试：`run_with_msgalaxy_env.bat test_comsol.py models/your_model.mph`

## 示例

```
models/
├── README.md                    # 本文件
├── satellite_thermal.mph        # 卫星热分析模型
└── satellite_structural.mph     # 卫星结构分析模型（可选）
```

## 模型要求

- 参数化几何（位置、尺寸使用全局参数）
- 参数命名：`<component_id>_<property>`
- 物理场：Heat Transfer in Solids
- 可成功求解

详细要求请参见：[../docs/COMSOL_MODEL_GUIDE.md](../docs/COMSOL_MODEL_GUIDE.md)

## 获取模型

如果你还没有COMSOL模型文件，请参考：
- [COMSOL模型文件获取指南](../docs/COMSOL_MODEL_GUIDE.md)
- [COMSOL仿真接入指南](../docs/COMSOL_GUIDE.md)
