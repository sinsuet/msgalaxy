# COMSOL模型创建快速参考

## 一键创建模型

```bash
# 简化测试模型（推荐新手）
create_model.bat simple

# 完整卫星模型
create_model.bat full
```

## 测试模型

```bash
# 测试简化模型
run_with_msgalaxy_env.bat test_comsol.py models/simple_test.mph

# 测试完整模型
run_with_msgalaxy_env.bat test_comsol.py models/satellite_thermal.mph
```

## 运行优化

```bash
# 使用COMSOL仿真运行优化
run_with_msgalaxy_env.bat -m api.cli optimize --max-iter 5
```

## 前提条件检查

- [ ] COMSOL Multiphysics已安装
- [ ] COMSOL许可证有效
- [ ] 没有其他COMSOL实例运行
- [ ] MPh库已安装（在msgalaxy环境中）

## 故障排除

**连接失败**: 关闭所有COMSOL窗口，重试

**创建慢**: 正常，首次启动COMSOL需要时间

**保存失败**: 检查磁盘空间和写权限

## 详细文档

- [自动创建工具说明](COMSOL_AUTO_CREATE.md)
- [模型获取指南](COMSOL_MODEL_GUIDE.md)
- [COMSOL集成指南](COMSOL_GUIDE.md)
