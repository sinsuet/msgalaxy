# R24 Blender MCP / Codex / Blender Windows 接入说明（2026-03-06）

## 1. 目标

本文档给出 Windows 环境下的最小可用接入方式：
- 安装 `uv`；
- 在 Codex 中注册 `blender-mcp` STDIO server；
- 在 Blender 中安装并启用 `addon.py`；
- 用 `run/render_blender_scene.py` 生成 `render_bundle.json`、Blender 场景脚本和 Codex brief；
- 让 Codex + Blender MCP 或 Blender 直连渲染 MsGalaxy 最终布局。

## 2. 官方来源

- `blender-mcp` GitHub README：`https://github.com/ahujasid/blender-mcp`
- README 中的完整教程视频：`https://www.youtube.com/watch?v=lCyQ717DuzQ`
- OpenAI Codex MCP 文档：`https://developers.openai.com/codex/mcp/`

## 3. Codex 设置

### 3.1 本机已落地配置

本机已通过 CLI 注册：

```bash
codex mcp add blender --env DISABLE_TELEMETRY=true --env BLENDER_HOST=127.0.0.1 --env BLENDER_PORT=9876 -- uvx blender-mcp
```

可通过下列命令查看：

```bash
codex mcp get blender
```

### 3.2 Codex UI 填写值

在 Codex Settings -> 连接至自定义 MCP 中填写：

- 名称：`blender`
- 传输：`STDIO`
- 启动命令：`uvx`
- 参数 1：`blender-mcp`
- 环境变量：
  - `DISABLE_TELEMETRY=true`
  - `BLENDER_HOST=127.0.0.1`
  - `BLENDER_PORT=9876`

### 3.3 Windows 兼容回退

如果 UI 直接运行 `uvx` 失败，可改为：

- 启动命令：`cmd`
- 参数 1：`/c`
- 参数 2：`uvx`
- 参数 3：`blender-mcp`

环境变量保持不变。

## 4. Blender 侧

### 4.1 Addon 安装

按 `blender-mcp` README 的方式：
- 打开 Blender；
- `Edit > Preferences > Add-ons`；
- `Install...` 选择 `addon.py`；
- 启用 `Interface: Blender MCP`。

### 4.2 本机已完成状态

本机已使用 Blender CLI 完成安装并启用 addon，Blender 路径为：

```text
D:\Program Files\Blender\blender.exe
```

Blender 已写入用户配置并启用 `addon` 模块。

### 4.3 使用时动作

- 打开 Blender；
- 在 3D 视图按 `N` 打开侧边栏；
- 进入 `BlenderMCP` 标签；
- 点击 `Connect to MCP server`。

## 5. 仓内命令

### 5.1 生成 bundle / script / brief

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/render_blender_scene.py --run-dir experiments/run_bm_l1_operator_program_nsga3_s42_0306_nsgaiii_003
```

生成产物：
- `visualizations/blender/render_bundle.json`
- `visualizations/blender/blender_scene_builder.py`
- `visualizations/blender/render_brief.md`
- `visualizations/blender/render_manifest.json`

### 5.2 直接 Blender 出图

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/render_blender_scene.py --run-dir experiments/run_bm_l1_operator_program_nsga3_s42_0306_nsgaiii_003 --render-direct --blender-exe "D:\Program Files\Blender\blender.exe"
```

输出：
- `visualizations/blender/final_satellite_render.png`
- `visualizations/blender/final_satellite_scene.blend`

## 6. Codex + Blender MCP 推荐提示词

在 Blender 已点击 `Connect to MCP server` 后，可对 Codex 说：

```text
Use the `blender` MCP server. First call `get_scene_info`.
Then read `experiments/.../visualizations/blender/blender_scene_builder.py`
and execute its contents with `execute_blender_code`.
After that call `get_viewport_screenshot` for inspection.
```

更完整版本已自动写入对应 run 目录下的 `visualizations/blender/render_brief.md`。

## 7. 当前边界

- 当前已实现的是 P0：`bundle + scene script + brief + optional direct render`。
- 当前尚未实现仓内自动 MCP client；也就是说，仓库本身不会自动调用 Codex 的 MCP tool。
- 太阳翼、天线、payload lens、radiator fins 目前属于可视化启发式，不参与物理/约束真值判定。
- 如需高保真 CAD mesh，还需后续补 `STEP -> mesh` bridge。
