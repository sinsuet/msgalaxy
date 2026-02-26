# REST API文档

卫星设计优化系统REST API文档。

---

## 基础信息

- **Base URL**: `http://localhost:5000`
- **Content-Type**: `application/json`
- **WebSocket Namespace**: `/tasks`
- **版本**: v1.1

---

## WebSocket实时更新

系统支持通过WebSocket接收任务的实时更新。

### 连接

```javascript
const socket = io('http://localhost:5000/tasks');
```

### 事件

**客户端事件**:
- `connect`: 连接成功
- `disconnect`: 连接断开
- `subscribe`: 订阅任务更新 `{task_id: "..."}`

**服务器事件**:
- `connected`: 连接确认
- `task_update`: 任务更新通知
- `error`: 错误消息

### 任务更新事件格式

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "status_change|progress|iteration_complete|error",
  "data": {
    "status": "running",
    "message": "...",
    "progress_percent": 50,
    "current_iteration": 10,
    "max_iterations": 20
  },
  "timestamp": "2026-02-23T14:30:00"
}
```

**事件类型**:
- `status_change`: 任务状态变更 (pending → running → completed/failed)
- `progress`: 进度更新
- `iteration_complete`: 迭代完成
- `error`: 错误发生

---

## 端点列表

### 1. 健康检查

检查API服务器状态。

**端点**: `GET /api/health`

**响应**:
```json
{
  "status": "ok",
  "timestamp": "2026-02-16T14:30:00"
}
```

---

### 2. 创建优化任务

提交新的优化任务。

**端点**: `POST /api/tasks`

**请求体**:
```json
{
  "bom_file": "config/bom_example.json",
  "max_iterations": 20,
  "convergence_threshold": 0.01,
  "config_path": "config/system.yaml"
}
```

**参数说明**:
- `bom_file` (string, required): BOM文件路径
- `max_iterations` (integer, optional): 最大迭代次数，默认20
- `convergence_threshold` (float, optional): 收敛阈值，默认0.01
- `config_path` (string, optional): 配置文件路径，默认"config/system.yaml"

**响应** (201 Created):
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2026-02-16T14:30:00"
}
```

---

### 3. 获取任务状态

查询任务当前状态。

**端点**: `GET /api/tasks/{task_id}`

**路径参数**:
- `task_id` (string): 任务ID

**响应**:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "config": {
    "bom_file": "config/bom_example.json",
    "max_iterations": 20
  },
  "created_at": "2026-02-16T14:30:00",
  "started_at": "2026-02-16T14:30:05",
  "completed_at": null,
  "result": null,
  "error": null
}
```

**状态值**:
- `pending`: 等待执行
- `running`: 正在运行
- `completed`: 已完成
- `failed`: 失败

---

### 4. 列出所有任务

获取所有任务列表。

**端点**: `GET /api/tasks`

**响应**:
```json
{
  "tasks": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "created_at": "2026-02-16T14:30:00",
      ...
    }
  ],
  "total": 1
}
```

---

### 5. 获取任务结果

获取已完成任务的详细结果。

**端点**: `GET /api/tasks/{task_id}/result`

**路径参数**:
- `task_id` (string): 任务ID

**响应**:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "summary": {
    "status": "SUCCESS",
    "final_iteration": 15,
    "timestamp": "2026-02-16T14:45:00"
  },
  "evolution": [
    {
      "iteration": "1",
      "max_temp": "45.5",
      "min_clearance": "5.2",
      "total_mass": "15.3",
      "total_power": "120.0",
      "num_violations": "3"
    }
  ],
  "experiment_dir": "experiments/run_20260216_143000"
}
```

**错误响应** (400 Bad Request):
```json
{
  "error": "Task not completed"
}
```

---

### 6. 获取可视化图片

下载任务生成的可视化图片。

**端点**: `GET /api/tasks/{task_id}/visualizations/{filename}`

**路径参数**:
- `task_id` (string): 任务ID
- `filename` (string): 文件名

**可用文件名**:
- `evolution_trace.png`: 演化轨迹图
- `final_layout_3d.png`: 3D布局图
- `thermal_heatmap.png`: 热图

**响应**: PNG图片文件

---

### 7. 列出所有实验

获取所有历史实验列表。

**端点**: `GET /api/experiments`

**响应**:
```json
{
  "experiments": [
    {
      "name": "run_20260216_143000",
      "path": "experiments/run_20260216_143000",
      "summary": {
        "status": "SUCCESS",
        "final_iteration": 15
      }
    }
  ],
  "total": 1
}
```

---

### 8. 验证BOM文件

验证BOM文件格式和内容。

**端点**: `POST /api/bom/validate`

**请求体**:
```json
{
  "bom_file": "config/bom_example.json"
}
```

**响应**:
```json
{
  "valid": true,
  "num_components": 2,
  "errors": [],
  "components": [
    {
      "id": "battery_01",
      "name": "锂电池组",
      "mass": 5.0,
      "power": 50.0,
      "category": "power"
    }
  ]
}
```

---

## 错误响应

所有错误响应遵循统一格式：

```json
{
  "error": "错误描述"
}
```

**HTTP状态码**:
- `400 Bad Request`: 请求参数错误
- `404 Not Found`: 资源不存在
- `500 Internal Server Error`: 服务器内部错误

---

## 使用示例

### Python客户端

```python
from api.client import APIClient

# 创建客户端
client = APIClient("http://localhost:5000")

# 健康检查
health = client.health_check()
print(health)

# 运行优化
task = client.run_optimization(
    bom_file="config/bom_example.json",
    max_iterations=20,
    wait=True
)

# 获取结果
result = client.get_task_result(task['id'])
print(result)

# 下载可视化
client.download_visualization(
    task['id'],
    "evolution_trace.png",
    "my_evolution.png"
)
```

### cURL

```bash
# 健康检查
curl http://localhost:5000/api/health

# 创建任务
curl -X POST http://localhost:5000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "bom_file": "config/bom_example.json",
    "max_iterations": 20
  }'

# 查询任务状态
curl http://localhost:5000/api/tasks/{task_id}

# 获取结果
curl http://localhost:5000/api/tasks/{task_id}/result

# 下载可视化
curl http://localhost:5000/api/tasks/{task_id}/visualizations/evolution_trace.png \
  -o evolution.png
```

### JavaScript (Fetch API)

```javascript
// 创建任务
const response = await fetch('http://localhost:5000/api/tasks', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    bom_file: 'config/bom_example.json',
    max_iterations: 20
  })
});

const task = await response.json();
console.log('Task ID:', task.task_id);

// 轮询任务状态
const pollTask = async (taskId) => {
  while (true) {
    const response = await fetch(`http://localhost:5000/api/tasks/${taskId}`);
    const task = await response.json();

    if (task.status === 'completed' || task.status === 'failed') {
      return task;
    }

    await new Promise(resolve => setTimeout(resolve, 2000));
  }
};

const completedTask = await pollTask(task.task_id);
console.log('Task completed:', completedTask);
```

### WebSocket客户端示例

```javascript
// 连接到WebSocket
const socket = io('http://localhost:5000/tasks');

// 监听连接事件
socket.on('connect', () => {
  console.log('Connected to WebSocket');
});

// 监听任务更新
socket.on('task_update', (data) => {
  const { task_id, event_type, data: eventData } = data;

  if (event_type === 'status_change') {
    console.log(`Status: ${eventData.status} - ${eventData.message}`);
  } else if (event_type === 'progress') {
    console.log(`Progress: ${eventData.progress_percent}%`);
  }
});

// 创建任务后订阅更新
const response = await fetch('http://localhost:5000/api/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    bom_file: 'config/bom_example.json',
    max_iterations: 20
  })
});

const task = await response.json();
socket.emit('subscribe', { task_id: task.task_id });
```

### Python WebSocket客户端

```python
from api.websocket_client import TaskWebSocketClient

# 创建客户端
client = TaskWebSocketClient("http://localhost:5000")

# 定义回调函数
def on_progress(task_id, data):
    percent = data.get('progress_percent', 0)
    print(f"Progress: {percent}%")

client.on_progress = on_progress

# 连接并订阅
client.connect()
client.subscribe(task_id)
client.wait_for_completion(timeout=600)
```

### HTML演示页面

系统提供了一个完整的HTML演示页面，展示WebSocket实时更新功能:

```bash
# 打开演示页面
open api/websocket_demo.html
# 或在浏览器中访问: file:///path/to/msgalaxy/api/websocket_demo.html
```

---

## 命令行工具

系统提供命令行工具用于快速调用API：

```bash
# 健康检查
python api/client.py health

# 运行优化
python api/client.py run config/bom_example.json --max-iterations 20

# 列出所有任务
python api/client.py list

# 查看任务状态
python api/client.py status {task_id}

# 获取任务结果
python api/client.py result {task_id}

# 验证BOM文件
python api/client.py validate config/bom_example.json
```

---

## 启动服务器

```bash
# 开发模式（支持WebSocket）
python api/server.py

# 生产模式（使用gunicorn + eventlet）
pip install eventlet
gunicorn -w 1 -k eventlet -b 0.0.0.0:5000 api.server:app
```

**注意**: WebSocket需要使用eventlet或gevent worker，不能使用多进程worker。

---

## 注意事项

1. **并发限制**: 当前实现使用线程池，建议不要同时运行过多任务
2. **任务持久化**: 任务信息存储在内存中，服务器重启后会丢失
3. **文件路径**: BOM文件路径相对于服务器工作目录
4. **CORS**: 已启用CORS支持，允许跨域请求
5. **认证**: 当前版本未实现认证，生产环境需要添加
6. **WebSocket**: 生产环境需要使用eventlet或gevent worker

---

## 未来改进

- [x] 添加WebSocket支持实时进度推送 (v1.1完成)
- [ ] 实现任务持久化（数据库）
- [ ] 添加用户认证和授权
- [ ] 支持任务取消和暂停
- [ ] 添加速率限制
- [ ] 实现任务队列管理
- [ ] 添加OpenAPI/Swagger文档

---

**版本**: 1.1.0
**更新时间**: 2026-02-23
