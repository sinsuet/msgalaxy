# R07 模式分拆与运行时解耦重构方案（20260304）

## 执行状态（2026-03-05 11:36 +08:00）

- `R1`：已完成（`runtime/contracts + mode_router` 已接入）。
- `R2`：已完成（runner 目录化 + `agent_loop` 主循环迁入 `loop_service.py` + 删除 `agent_loop/mass` 中间转发层 + `agent_loop` 运行时支撑方法迁入 `runtime_support.py`）。
- `R3`：进行中（MaaS 主实现迁入 `workflow/modes/mass/pipeline_service.py` 后，运行时大块方法已迁入 `workflow/modes/mass/runtime_support.py`；`orchestrator.py` 已降到约 `891` 行；`agent_loop/mass` mode service 已不再直接访问 `host._*` 私有成员）。
- `R4`：进行中（控制器契约已落位：`intent_modeler/strategic_planner/policy_programmer`；新增 `vop_maas` 预留模式骨架，策略提案走 `policy_programmer`，执行链仍委托 `mass`）。

## 1. 背景与问题确认

当前代码已同时承载三套能力：
- 2.0 时代 `agent_loop` 多智能体闭环；
- `mass` A/B/C/D 编译求解闭环；
- 正在推进的 LLM 强验真与后续策略程序化（V-OP-MaaS）。

混淆风险已在代码中可见：
- 单一入口编排器同时 import 两套运行栈：`workflow/orchestrator.py`。
- `run_optimization` 在同一方法中分支 `agent_loop` 与 `mass`。
- L1-L4 运行脚本同时承担配置装配、策略注入、LLM 验真、结果汇总。
- 运行脚本对 `meta_reasoner.generate_modeling_intent` 做 monkey patch（deterministic 注入），导致边界漂移。
- `run/agent_loop/` 与 `run/` 并行存在，职责重复。

这会带来：
- 架构边界不清，后续新 LLM 方案容易污染旧链路；
- 可读性下降，回归定位成本高；
- 变更风险外溢（一个入口改动影响两条历史链路）。

## 2. 重构目标与非目标

### 2.1 目标
- 将运行时按模式拆分，保留统一启动入口但隔离实现。
- 将 LLM 能力按职责拆分：`agent_loop 规划`、`MaaS intent`、`新策略程序控制` 三条独立控制器。
- 消除脚本层 monkey patch，改为标准化 provider/adapter 注入。
- 保持 `pymoo` 作为数值优化核心，不允许直接坐标输出旁路。

### 2.2 非目标
- 本轮不改科学约束语义（`g(x)<=0` 不变）。
- 本轮不引入 M4 神经模块训练实现（仅预留接口）。
- 本轮不改变已有 benchmark 指标定义。

## 3. 目标架构（落地形态）

```text
workflow/
  orchestrator.py                 # 薄启动器: 加载配置 + 路由模式 + 汇总产物
  runtime/
    contracts.py                  # ModeRunner/RuntimeContext/RunArtifacts 接口
    mode_router.py                # mode -> runner 映射
  modes/
    agent_loop/
      runner.py
      loop_service.py
    mass/
      runner.py
      pipeline_service.py
      intent_execution_service.py
    vop_maas/
      runner.py                   # 新LLM方案宿主（初期可薄实现）
      policy_program_service.py

optimization/
  llm/
    providers/qwen_client.py
    controllers/
      strategic_planner.py        # 原 generate_strategic_plan 归属
      intent_modeler.py           # 原 generate_modeling_intent 归属
      policy_programmer.py        # 新LLM方案归属
    contracts.py

run/
  scenarios/
    run_L1.py
    run_L2.py
    run_L3.py
    run_L4.py
  tools/
    run_mass_benchmark_matrix.py
  legacy/
    agent_loop/                   # 统一模式入口
```

## 4. 分阶段执行计划（严格顺序）

### R0 基线冻结（1天）
- 内容：冻结当前行为与产物字段，补齐 golden run 清单。
- 产物：`docs/reports/R07_*`、回归命令清单、golden summary 对照表。
- 验收：L3 `--llm-proof-strict` 和现有 smoke 全通过。

### R1 运行时接口抽象（1-2天）
- 内容：新增 `ModeRunner`、`RuntimeContext`、`RunArtifacts`；`orchestrator` 仅负责 route。
- 产物：`workflow/runtime/contracts.py`、`workflow/runtime/mode_router.py`。
- 验收：`optimization.mode` 仅影响 router，不再在大方法中分叉逻辑。

### R2 拆出 agent_loop 子系统（2天）
- 内容：将 agent_loop 循环与执行计划逻辑迁入 `workflow/modes/agent_loop/`。
- 产物：`agent_loop/runner.py` + `loop_service.py`。
- 验收：`agent_loop` 回归输出与基线同口径。

### R3 拆出 mass 子系统（2-3天）
- 内容：将 MaaS 运行逻辑集中到 `workflow/modes/mass/`，移除对 host 私有方法的大量直接依赖。
- 产物：`mass/runner.py`、服务内聚化。
- 验收：L3/L4 smoke 行为等价，`summary.json` 字段不回退。

### R4 LLM 控制器分仓（2天）
- 内容：拆分 `meta_reasoner`：
  - `strategic_planner`（agent_loop）
  - `intent_modeler`（mass）
  - `policy_programmer`（新方案）
- 产物：`optimization/llm/controllers/*` + 统一诊断契约。
- 验收：三控制器可独立单测；LLM 诊断字段一致。

### R5 入口与配置治理（2天）
- 内容：
  - 运行脚本改为 provider 参数化，删除 monkey patch；
  - `run/agent_loop` 迁至 `run/legacy/agent_loop` 并打弃用提示；
  - 配置拆分为 base + mode profile overlay。
- 产物：`run/scenarios/*`、`config/profiles/modes/*.yaml`。
- 验收：入口职责单一；同一参数不再在多脚本重复定义。

### R6 收口发布（1天）
- 内容：清理废弃路径、补文档、建立静态边界检查。
- 产物：ADR 状态更新、HANDOFF/PROJECT_SUMMARY/README 同步。
- 验收：
  - `orchestrator.py` 行数显著下降（目标 < 1200）；
  - 禁止跨模式 import（静态扫描通过）；
  - 回归矩阵通过。

## 5. 验收门槛（DoD）

- 架构门槛：
  - 不允许 `agent_loop` 直接 import `mass` 内部服务，反之亦然。
  - 运行脚本不允许 monkey patch 业务对象方法。
- 行为门槛：
  - `mass` 路径上 `--llm-proof-strict` 继续可用。
  - 关键 summary 字段保持兼容：`solver_diagnosis`、`compile_report`、`llm_effective_*`。
- 科学门槛：
  - 硬约束语义和 mandatory coverage 不弱化。

## 6. 风险与回滚

- 风险1：拆分过程中产生日志/summary 字段漂移。
  - 缓解：golden artifact 对照 + schema 断言测试。
- 风险2：旧脚本仍被外部调用。
  - 缓解：`legacy` 目录保留一阶段，并输出弃用告警。
- 风险3：大脏工作区下并行改动冲突。
  - 缓解：分阶段小 PR，每阶段只改一个子系统。

回滚策略：
- 每阶段保留 `mode_router` 回退到旧 runner 的开关；
- 产物不兼容时，单阶段回滚，不连带撤销其它阶段。

## 7. 本阶段立即执行建议

1. 先做 R1（接口抽象）+ R2（agent_loop 拆出），风险最低。
2. 随后做 R3（mass 拆出），再接 R4 新 LLM 控制器落位。
3. 最后执行 R5/R6 收口，避免边拆边改入口引发混乱。
