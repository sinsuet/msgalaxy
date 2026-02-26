# WebSocket实时更新实现总结

**文档版本**: 1.0
**更新时间**: 2026-02-23
**实现状态**: ✅ 完成

---

## 一、功能概述

为MsGalaxy卫星设计优化系统添加了WebSocket实时更新功能,允许客户端实时接收任务执行过程中的状态变更、进度更新和错误通知。

### 核心特性

1. **实时状态推送** - 任务状态变更时立即通知客户端
2. **进度更新** - 优化迭代过程中实时推送进度信息
3. **错误通知** - 任务失败时立即推送错误详情
4. **多客户端支持** - 支持多个客户端同时连接和订阅
5. **跨域支持** - 完整的CORS配置,支持Web前端调用

---

## 二、技术实现

### 2.1 技术栈

- **Flask-SocketIO** (5.6.1) - Flask的WebSocket扩展
- **python-socketio** (5.16.1) - Socket.IO协议实现
- **python-engineio** (4.13.1) - Engine.IO协议实现
- **Socket.IO客户端** (4.5.4) - JavaScript客户端库

### 2.2 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    客户端层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Web浏览器    │  │ Python客户端 │  │ 其他客户端   │  │
│  │ (Socket.IO)  │  │ (socketio)   │  │              │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
                            ↓ WebSocket连接
┌─────────────────────────────────────────────────────────┐
│                  Flask-SocketIO服务器                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │ WebSocket命名空间: /tasks                       │   │
│  │ - connect: 客户端连接                           │   │
│  │ - disconnect: 客户端断开                        │   │
│  │ - subscribe: 订阅任务更新                       │   │
│  └─────────────────────────────────────────────────┘   │
│                            ↓                             │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 事件发射器: emit_task_update()                  │   │
│  │ - status_change: 状态变更                       │   │
│  │ - progress: 进度更新                            │   │
│  │ - iteration_complete: 迭代完成                  │   │
│  │ - error: 错误通知                               │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                  后台任务执行线程                        │
│  run_optimization_task()                                │
│  - 创建编排器                                           │
│  - 运行优化循环                                         │
│  - 每个关键步骤推送更新                                 │
└─────────────────────────────────────────────────────────┘
```

### 2.3 核心代码

#### 服务器端 (api/server.py)

```python
from flask_socketio import SocketIO, emit

# 创建SocketIO实例
socketio = SocketIO(app, cors_allowed_origins="*")

# 事件发射器
def emit_task_update(task_id: str, event_type: str, data: Dict[str, Any]):
    """通过WebSocket发送任务更新"""
    socketio.emit('task_update', {
        'task_id': task_id,
        'event_type': event_type,
        'data': data,
        'timestamp': datetime.now().isoformat()
    }, namespace='/tasks')

# WebSocket事件处理器
@socketio.on('connect', namespace='/tasks')
def handle_connect():
    """客户端连接"""
    emit('connected', {'message': 'Connected to task updates'})

@socketio.on('subscribe', namespace='/tasks')
def handle_subscribe(data):
    """订阅特定任务的更新"""
    task_id = data.get('task_id')
    emit('subscribed', {'task_id': task_id})

# 任务执行中推送更新
def run_optimization_task(task_id: str, config: Dict[str, Any]):
    # 状态变更
    emit_task_update(task_id, 'status_change', {
        'status': TaskStatus.RUNNING,
        'message': 'Optimization started'
    })

    # 进度更新
    emit_task_update(task_id, 'progress', {
        'current_iteration': 5,
        'max_iterations': 20,
        'progress_percent': 25
    })
```

#### Python客户端 (api/websocket_client.py)

```python
import socketio

class TaskWebSocketClient:
    def __init__(self, server_url: str):
        self.sio = socketio.Client()
        self.sio.on('task_update', self._on_task_update, namespace='/tasks')

    def connect(self):
        self.sio.connect(self.server_url, namespaces=['/tasks'])

    def subscribe(self, task_id: str):
        self.sio.emit('subscribe', {'task_id': task_id}, namespace='/tasks')

    def _on_task_update(self, data):
        event_type = data.get('event_type')
        if event_type == 'progress' and self.on_progress:
            self.on_progress(data['task_id'], data['data'])
```

#### JavaScript客户端 (websocket_demo.html)

```javascript
const socket = io('http://localhost:5000/tasks');

socket.on('connect', () => {
    console.log('Connected to WebSocket');
});

socket.on('task_update', (data) => {
    const { task_id, event_type, data: eventData } = data;

    if (event_type === 'progress') {
        updateProgressBar(eventData.progress_percent);
    }
});

socket.emit('subscribe', { task_id: taskId });
```

---

## 三、事件协议

### 3.1 客户端事件

| 事件名 | 方向 | 参数 | 说明 |
|--------|------|------|------|
| `connect` | 客户端→服务器 | - | 建立WebSocket连接 |
| `disconnect` | 客户端→服务器 | - | 断开WebSocket连接 |
| `subscribe` | 客户端→服务器 | `{task_id: string}` | 订阅任务更新 |

### 3.2 服务器事件

| 事件名 | 方向 | 数据格式 | 说明 |
|--------|------|----------|------|
| `connected` | 服务器→客户端 | `{message: string}` | 连接确认 |
| `subscribed` | 服务器→客户端 | `{task_id: string}` | 订阅确认 |
| `task_update` | 服务器→客户端 | 见下表 | 任务更新通知 |
| `error` | 服务器→客户端 | `{message: string}` | 错误消息 |

### 3.3 task_update事件格式

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "status_change|progress|iteration_complete|error",
  "data": {
    // 根据event_type不同而不同
  },
  "timestamp": "2026-02-23T14:30:00"
}
```

#### event_type: status_change

```json
{
  "status": "pending|running|completed|failed",
  "message": "状态描述",
  "result": {  // 仅completed状态
    "experiment_dir": "experiments/run_xxx",
    "final_iteration": 15,
    "num_components": 5
  }
}
```

#### event_type: progress

```json
{
  "current_iteration": 10,
  "max_iterations": 20,
  "progress_percent": 50,
  "message": "可选的进度消息"
}
```

#### event_type: iteration_complete

```json
{
  "iteration": 10,
  "metrics": {
    "max_temp": 45.5,
    "min_clearance": 5.2
  }
}
```

#### event_type: error

```json
{
  "status": "failed",
  "error": "错误详情"
}
```

---

## 四、使用示例

### 4.1 Python客户端完整示例

```python
from api.websocket_client import TaskWebSocketClient
from api.client import APIClient

# 创建REST API客户端
api_client = APIClient("http://localhost:5000")

# 创建WebSocket客户端
ws_client = TaskWebSocketClient("http://localhost:5000")

# 定义回调函数
def on_status_change(task_id, data):
    status = data.get('status')
    message = data.get('message')
    print(f"[STATUS] {status}: {message}")

def on_progress(task_id, data):
    percent = data.get('progress_percent', 0)
    current = data.get('current_iteration', 0)
    max_iter = data.get('max_iterations', 0)
    print(f"[PROGRESS] {current}/{max_iter} ({percent}%)")

def on_error(task_id, data):
    error = data.get('error')
    print(f"[ERROR] {error}")

# 设置回调
ws_client.on_status_change = on_status_change
ws_client.on_progress = on_progress
ws_client.on_error = on_error

# 连接WebSocket
ws_client.connect()

# 创建优化任务
task = api_client.create_task(
    bom_file="config/bom_example.json",
    max_iterations=20
)
task_id = task['task_id']
print(f"Task created: {task_id}")

# 订阅任务更新
ws_client.subscribe(task_id)

# 等待任务完成
ws_client.wait_for_completion(timeout=600)

# 断开连接
ws_client.disconnect()

# 获取最终结果
result = api_client.get_task_result(task_id)
print(f"Final result: {result}")
```

### 4.2 JavaScript客户端完整示例

```javascript
// 连接到WebSocket
const socket = io('http://localhost:5000/tasks');

// 监听连接事件
socket.on('connect', () => {
  console.log('Connected to WebSocket');
});

// 监听任务更新
socket.on('task_update', (data) => {
  const { task_id, event_type, data: eventData, timestamp } = data;

  switch (event_type) {
    case 'status_change':
      console.log(`[${timestamp}] Status: ${eventData.status} - ${eventData.message}`);
      if (eventData.status === 'completed') {
        console.log('Task completed!', eventData.result);
      }
      break;

    case 'progress':
      const percent = eventData.progress_percent || 0;
      console.log(`[${timestamp}] Progress: ${percent}%`);
      updateProgressBar(percent);
      break;

    case 'error':
      console.error(`[${timestamp}] Error: ${eventData.error}`);
      break;
  }
});

// 创建任务
async function createAndMonitorTask() {
  // 创建任务
  const response = await fetch('http://localhost:5000/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bom_file: 'config/bom_example.json',
      max_iterations: 20
    })
  });

  const task = await response.json();
  console.log('Task created:', task.task_id);

  // 订阅任务更新
  socket.emit('subscribe', { task_id: task.task_id });
}

// 执行
createAndMonitorTask();
```

### 4.3 HTML演示页面

系统提供了完整的HTML演示页面 `api/websocket_demo.html`,包含:

- 服务器连接管理
- 任务创建表单
- 实时连接状态显示
- 进度条可视化
- 实时日志展示
- 响应式设计

使用方法:
```bash
# 1. 启动服务器
python api/server.py

# 2. 在浏览器中打开
open api/websocket_demo.html
# 或访问: file:///path/to/msgalaxy/api/websocket_demo.html

# 3. 点击"连接服务器"
# 4. 填写BOM文件路径和迭代次数
# 5. 点击"创建优化任务"
# 6. 实时查看任务进度
```

---

## 五、测试

### 5.1 单元测试

创建了完整的测试套件 `tests/test_websocket.py`:

```python
class TestWebSocketClient:
    """WebSocket客户端测试"""
    def test_client_creation(self):
        """测试客户端创建"""

    def test_callback_assignment(self):
        """测试回调函数赋值"""

class TestWebSocketEventHandling:
    """WebSocket事件处理测试"""
    def test_status_change_event(self):
        """测试状态变更事件处理"""

    def test_progress_event(self):
        """测试进度事件处理"""

    def test_error_event(self):
        """测试错误事件处理"""
```

**测试结果**: 5/5 通过 ✅

```bash
$ pytest tests/test_websocket.py -v
tests/test_websocket.py::TestWebSocketClient::test_client_creation PASSED
tests/test_websocket.py::TestWebSocketClient::test_callback_assignment PASSED
tests/test_websocket.py::TestWebSocketEventHandling::test_status_change_event PASSED
tests/test_websocket.py::TestWebSocketEventHandling::test_progress_event PASSED
tests/test_websocket.py::TestWebSocketEventHandling::test_error_event PASSED
```

### 5.2 集成测试

集成测试需要服务器运行,已标记为跳过:

```python
@pytest.mark.skip(reason="需要服务器运行")
def test_connection(self):
    """测试连接到服务器"""

@pytest.mark.skip(reason="需要服务器运行")
def test_subscribe(self):
    """测试订阅任务"""

@pytest.mark.skip(reason="需要服务器运行")
def test_receive_updates(self):
    """测试接收任务更新"""
```

手动集成测试步骤:

```bash
# 终端1: 启动服务器
python api/server.py

# 终端2: 运行Python客户端
python api/websocket_client.py

# 或在浏览器中打开HTML演示页面
open api/websocket_demo.html
```

---

## 六、部署说明

### 6.1 开发环境

```bash
# 安装依赖
pip install flask-socketio python-socketio

# 启动服务器
python api/server.py
```

### 6.2 生产环境

WebSocket需要特殊的WSGI服务器配置:

```bash
# 安装eventlet
pip install eventlet

# 使用gunicorn + eventlet
gunicorn -w 1 -k eventlet -b 0.0.0.0:5000 api.server:app

# 或使用gevent
pip install gevent gevent-websocket
gunicorn -w 1 -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -b 0.0.0.0:5000 api.server:app
```

**重要注意事项**:
1. **不能使用多进程worker** (`-w 1`) - WebSocket需要单进程
2. **必须使用异步worker** - eventlet或gevent
3. **CORS配置** - 已在代码中配置 `cors_allowed_origins="*"`

### 6.3 Nginx反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 七、性能考虑

### 7.1 连接管理

- **连接数限制**: 当前实现支持无限连接,生产环境建议限制
- **心跳机制**: Socket.IO自动处理心跳,默认25秒
- **重连机制**: 客户端自动重连,默认重试间隔1秒

### 7.2 消息频率

- **状态变更**: 每个任务生命周期约3-5次
- **进度更新**: 每次迭代1次,20次迭代约20次
- **总消息数**: 每个任务约25-30条消息

### 7.3 带宽消耗

- **单条消息**: 约200-500字节
- **单个任务**: 约5-15 KB
- **100并发任务**: 约0.5-1.5 MB

---

## 八、已知限制

### 8.1 当前限制

1. **单进程部署** - WebSocket要求单进程,限制了并发能力
2. **内存存储** - 任务信息存储在内存中,服务器重启会丢失
3. **无认证** - 当前版本未实现认证,任何人都可以连接
4. **无房间隔离** - 所有客户端在同一命名空间,未实现房间隔离

### 8.2 安全考虑

1. **CORS配置** - 当前允许所有来源,生产环境应限制
2. **认证授权** - 应添加JWT或Session认证
3. **速率限制** - 应添加连接和消息速率限制
4. **输入验证** - 应验证客户端发送的数据

---

## 九、未来改进

### 9.1 短期改进

- [ ] 添加房间(Room)支持,实现任务隔离
- [ ] 添加认证中间件
- [ ] 添加连接数限制
- [ ] 添加消息速率限制

### 9.2 中期改进

- [ ] 实现Redis作为消息代理,支持多进程部署
- [ ] 添加消息持久化
- [ ] 添加断线重连后的消息补发
- [ ] 添加WebSocket连接监控

### 9.3 长期改进

- [ ] 实现分布式WebSocket集群
- [ ] 添加消息压缩
- [ ] 添加二进制消息支持
- [ ] 实现自定义协议优化

---

## 十、总结

### 10.1 实现成果

✅ **核心功能完成**
- WebSocket服务器集成
- Python客户端库
- JavaScript客户端示例
- HTML演示页面
- 完整的事件协议
- 单元测试覆盖

✅ **文档完善**
- API文档更新
- 使用示例
- 部署说明
- 实现总结

### 10.2 技术亮点

1. **实时性** - 毫秒级延迟的状态推送
2. **易用性** - 简洁的API设计,易于集成
3. **可扩展性** - 清晰的事件协议,易于扩展
4. **跨平台** - 支持Python、JavaScript、Web浏览器

### 10.3 应用价值

1. **用户体验** - 实时反馈提升用户体验
2. **调试便利** - 实时日志便于问题诊断
3. **监控能力** - 实时监控任务执行状态
4. **集成友好** - 标准协议易于第三方集成

---

**文档结束**

**作者**: MsGalaxy开发团队
**版本**: 1.0
**最后更新**: 2026-02-23
