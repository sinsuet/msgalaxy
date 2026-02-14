# 使用Qwen（通义千问）进行测试

本指南说明如何使用Qwen-Plus替代OpenAI进行测试。

## 为什么选择Qwen？

- ✅ **完全兼容OpenAI接口**: 无需修改代码
- ✅ **国内访问稳定**: 阿里云服务，无需代理
- ✅ **性价比高**: 价格更优惠
- ✅ **中文理解强**: 对中文工程文档理解更好

---

## 快速配置

### 1. 获取Qwen API密钥

1. 访问阿里云百炼平台: https://dashscope.console.aliyun.com/
2. 注册/登录账号
3. 进入"API-KEY管理"
4. 创建新的API-KEY并复制

### 2. 配置系统

编辑 `config/system.yaml`:

```yaml
# LLM配置（支持OpenAI和Qwen）
openai:
  # 填入你的Qwen API密钥
  api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

  # 使用Qwen-Plus模型
  model: "qwen-plus"

  # Qwen API Base URL
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"

  # 生成参数
  temperature: 0.7
  max_tokens: 2000
```

### 3. 运行测试

```bash
# 激活环境
conda activate msgalaxy

# 运行集成测试
python test_integration.py

# 运行完整优化
python -m api.cli optimize
```

---

## 支持的Qwen模型

| 模型名称 | 说明 | 适用场景 |
|---------|------|---------|
| `qwen-plus` | 通义千问Plus | **推荐**，性能强，性价比高 |
| `qwen-turbo` | 通义千问Turbo | 快速响应，成本更低 |
| `qwen-max` | 通义千问Max | 最强性能，复杂任务 |
| `qwen-long` | 通义千问Long | 超长上下文（100万token） |

**推荐使用**: `qwen-plus` - 平衡性能和成本

---

## 配置示例

### 使用Qwen-Plus（推荐）

```yaml
openai:
  api_key: "sk-your-qwen-api-key"
  model: "qwen-plus"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  temperature: 0.7
```

### 使用Qwen-Turbo（快速）

```yaml
openai:
  api_key: "sk-your-qwen-api-key"
  model: "qwen-turbo"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  temperature: 0.7
```

### 使用Qwen-Max（最强）

```yaml
openai:
  api_key: "sk-your-qwen-api-key"
  model: "qwen-max"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  temperature: 0.7
```

### 切换回OpenAI

```yaml
openai:
  api_key: "sk-your-openai-api-key"
  model: "gpt-4-turbo"
  # base_url: ""  # 留空或删除此行
  temperature: 0.7
```

---

## 测试步骤

### 1. 基础测试

```bash
# 测试API连接
python -c "
from openai import OpenAI
client = OpenAI(
    api_key='sk-your-qwen-api-key',
    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
)
response = client.chat.completions.create(
    model='qwen-plus',
    messages=[{'role': 'user', 'content': '你好'}]
)
print(response.choices[0].message.content)
"
```

### 2. Meta-Reasoner测试

```bash
# 测试Meta-Reasoner
python -c "
from optimization.meta_reasoner import MetaReasoner
from optimization.protocol import *

reasoner = MetaReasoner(
    api_key='sk-your-qwen-api-key',
    model='qwen-plus',
    base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
)

context = GlobalContextPack(
    iteration=1,
    design_state_summary='测试设计',
    geometry_metrics=GeometryMetrics(
        min_clearance=5.0,
        com_offset=[0,0,0],
        moment_of_inertia=[1,1,1],
        packing_efficiency=75.0
    ),
    thermal_metrics=ThermalMetrics(
        max_temp=50.0, min_temp=20.0,
        avg_temp=35.0, temp_gradient=2.0
    ),
    structural_metrics=StructuralMetrics(
        max_stress=100.0, max_displacement=0.1,
        first_modal_freq=60.0, safety_factor=2.0
    ),
    power_metrics=PowerMetrics(
        total_power=100.0, peak_power=120.0,
        power_margin=20.0, voltage_drop=0.3
    ),
    violations=[],
    history_summary='第1次迭代'
)

plan = reasoner.generate_strategic_plan(context)
print(f'生成计划: {plan.plan_id}')
print(f'策略类型: {plan.strategy_type}')
"
```

### 3. 完整优化测试

```bash
# 运行完整优化流程
python -m api.cli optimize --max-iter 5
```

---

## 常见问题

### Q1: 如何获取Qwen API密钥？

**A**: 访问 https://dashscope.console.aliyun.com/ 注册并创建API-KEY

### Q2: Qwen和OpenAI有什么区别？

**A**:
- Qwen完全兼容OpenAI接口格式
- 只需修改`api_key`、`model`和`base_url`三个参数
- 代码无需任何修改

### Q3: 如何切换模型？

**A**: 修改`config/system.yaml`中的`model`字段：
```yaml
model: "qwen-plus"    # 或 qwen-turbo, qwen-max
```

### Q4: 价格如何？

**A**: Qwen价格参考（2026年）：
- qwen-turbo: ¥0.003/千tokens
- qwen-plus: ¥0.008/千tokens
- qwen-max: ¥0.04/千tokens

相比OpenAI GPT-4约便宜10-20倍。

### Q5: 性能如何？

**A**:
- qwen-plus性能接近GPT-4
- qwen-max性能超过GPT-4
- 对中文理解更好

### Q6: 遇到连接错误怎么办？

**A**: 检查：
1. API密钥是否正确
2. base_url是否正确设置
3. 网络连接是否正常
4. 账户余额是否充足

---

## 性能对比

| 指标 | Qwen-Plus | GPT-4-Turbo |
|------|-----------|-------------|
| 中文理解 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 英文理解 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 推理能力 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 响应速度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 价格 | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| 国内访问 | ⭐⭐⭐⭐⭐ | ⭐⭐ |

**结论**: 对于国内用户和中文工程文档，推荐使用Qwen-Plus。

---

## 完整配置示例

```yaml
# config/system.yaml

# 项目信息
project:
  name: "msgalaxy"
  version: "1.0.0"

# 几何配置
geometry:
  envelope:
    auto_envelope: true
    fill_ratio: 0.30
  clearance_mm: 20

# 仿真配置
simulation:
  type: "SIMPLIFIED"
  constraints:
    max_temp_c: 50.0
    min_clearance_mm: 3.0

# 优化配置
optimization:
  max_iterations: 20
  convergence_threshold: 0.01

# LLM配置 - 使用Qwen
openai:
  api_key: "sk-your-qwen-api-key-here"
  model: "qwen-plus"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  temperature: 0.7
  max_tokens: 2000

# 日志配置
logging:
  level: "INFO"
  output_dir: "experiments"
  save_llm_interactions: true
```

---

## 下一步

1. ✅ 获取Qwen API密钥
2. ✅ 修改`config/system.yaml`
3. ✅ 运行`python test_integration.py`
4. ✅ 运行`python -m api.cli optimize`

**准备就绪！开始使用Qwen进行卫星设计优化吧！** 🚀

---

**相关链接**:
- Qwen官网: https://tongyi.aliyun.com/
- API文档: https://help.aliyun.com/zh/dashscope/
- 控制台: https://dashscope.console.aliyun.com/
