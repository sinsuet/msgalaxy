# 0015-same-repo-single-core-scenario-runtime

status: accepted

> 2026-03-24 状态更新：
> 当前主线已进一步收束为 `mass` 单栈；`vop_maas` 相关 runtime/config/test 表面已退出活跃代码面并转入归档策略管理。
> COMSOL active contract 也已收束到 canonical-only；旧 `diagnostic_simplified` profile 与简化热路兜底已退出主线代码面。
> `ScenarioRuntime` 现已进一步收敛为阶段化状态机，并在 `proxy_feasible` 前置闸门下稳定输出失败路径 artifact。
> `SatelliteLikenessGate` 现已作为主线前置审计接入 `ScenarioRuntime`：默认 `strict`，位于 `proxy_feasible` 之后、`STEP/COMSOL` 之前，失败路径会稳定写出 gate audit 并阻断真实物理链。
> 2026-03-24 同日补充：active optical bus 上的 gate 误判已修复；strict gate 三次独立复跑均恢复到 `fields_exported + real_feasible=true`。

## Context

到 2026-03-11 为止，仓库虽然已经有 `SatelliteArchetype`、`catalog/shell/aperture`、STEP 开孔、DSL v4、真实 COMSOL vertical smoke 等薄切片，但真实执行主线仍然是旧内核：

- `BOMParser + ComponentGeometry + layout_engine`
- L1-L4 分层入口
- `agent_loop / mass / vop_maas` 多套入口并存
- COMSOL 当时仍残留 `diagnostic_simplified` 与旧简化热路兼容面
- Blender / teacher-demo / review-package 仍在对主线施加复杂消费面

这使仓库维护和调试成本持续上升，并阻碍“目录件真值 + shell/aperture + canonical COMSOL”主链真正落地。

## Decision

接受“原仓重建”而不是新开仓。

具体决策如下：

1. 对外 stack 收束为 `mass` 单栈。
2. 主链统一固定为同仓 `scenario-driven runtime core`。
3. 主链输入从 generic BOM 切换为 `SatelliteScenarioSpec`。
4. 搜索空间切为 catalog-first，禁止主链自由改 catalog 尺寸。
5. 统一入口固定为 `run/run_scenario.py --stack mass --scenario <id>`。
6. COMSOL 对主链固定为 canonical-only request；canonical request 若降级则直接 block。
7. 旧 `agent_loop`、`vop_maas`、L1-L4 脚本、simplified backend 暴露面、Blender sidecar 不再作为主链保留。
8. `ScenarioRuntime` 主线阶段固定为：
   - `seed_built`
   - `proxy_optimized`
   - `proxy_feasible`
   - `step_exported`
   - `comsol_model_built`
   - `comsol_solved`
   - `fields_exported`
9. `proxy_feasible` 成为进入真实 COMSOL 的第一道硬闸门；任何硬约束为正时，主线只能诚实阻断并写出失败 artifact。
10. `SatelliteLikenessGate` 成为 `proxy_feasible -> STEP/COMSOL` 之间的第二道主线闸门；默认 `strict`，失败时以 `satellite_likeness_failed` 阻断。
11. `summary.json` 成为唯一主审计对象，必须能表达“未进 COMSOL / 已进 COMSOL 但未建模成功 / 已建模但未求解 / 已求解未导场”，并稳定沉淀 satellite likeness gate audit。

## Consequences

正向结果：

- 新卫星合同真正进入可执行主链，而不是只停留在 ADR / smoke / sidecar。
- `0010` 中的 archetype/baseline/gate 合同不再只停留在 sidecar，而是进入当前稳定主线审计与阻断链。
- 入口和运行配置显著收敛，便于后续物理删除遗留模式。
- 主线只围绕 `mass` 稳定性推进，避免实验性策略壳继续干扰运行面。
- 失败路径也终于成为一等公民，主线不再只有“跑通/没跑通”二值叙事。

代价与风险：

- 这是破坏性重构，旧 CLI/API/测试/文档会失效。
- 第二轮仍需继续删除深层 legacy 模块与文档索引，否则仓库仍存在历史噪声。
- canonical-only COMSOL 在真实环境下仍需重新做 release-grade 验证。
