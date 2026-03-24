# Mass Hybrid Operator Integration Design

## 1. 背景与当前真相

本设计文档面向 `mass` 主线，目标是在不破坏当前稳定链路的前提下，把仓库中已经存在的 operator-program 能力正式纳入 `pymoo` 搜索主链。

截至 2026-03-24，当前稳定真相必须保持如下口径：

- 稳定执行主线仍是 `python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus`。
- 当前唯一稳定化场景仍是 `optical_remote_sensing_bus`。
- 当前稳定搜索空间仍是 `catalog-first position-only search`。
- 当前稳定变量覆盖仍是 `variable_coverage = ["position"]`。
- `mount_face / aperture_site / preferred_orientation` 仍由 deterministic seed 固定，不属于当前主线搜索变量。
- 2026-03-24 的三次独立复跑已证明：在该单场景下，当前 `position-only` 主线可以稳定跑到 `fields_exported`，且 `real_feasible=true`。
- 这只说明当前单场景主线具备可重复证据，不代表跨场景、跨环境、跨 seed 的 release-grade 稳定性结论。

同时，仓库中已经存在可执行但未接回稳定主线的 operator-program 基础设施：

- `optimization/modes/mass/operator_program.py`
- `optimization/modes/mass/pymoo_integration/operator_program_codec.py`
- `optimization/modes/mass/pymoo_integration/operator_problem_generator.py`

这些能力说明“算子表达、算子基因、算子应用”已经有雏形，但还没有与当前稳定 `mass` 主线形成受控、可审计、可回退的正式接入合同。

## 2. 目标与非目标

### 2.1 目标

- 在 `mass` 主线上新增显式搜索空间模式选择，而不是直接替换当前稳定主线。
- 正式支持三种搜索空间模式：
  - `position_only`
  - `operator_program`
  - `hybrid`
- 将 `hybrid` 作为本轮正式设计目标，使其支持“位置变量 + operator-program”联合搜索。
- 保持 `pymoo` 作为唯一数值优化核心，不允许绕过 `pymoo` 直接输出最终坐标。
- 维持当前 seed 场景语义合同，确保算子接入后不会破坏 mount/aperture/orientation 的真实约束来源。
- 为后续代理评估、真实 COMSOL 审计、A/B 对照和多 seed 统计提供可观测性字段。

### 2.2 非目标

- 本轮不把 `mount_face / aperture_site / preferred_orientation` 开放成新的搜索变量。
- 本轮不把 `hybrid` 直接提升为默认稳定主线。
- 本轮不宣称“所有 operator 对热/结构/电源/任务约束都已完成真实 COMSOL 全耦合验证”。
- 本轮不开放第二个真实稳定场景。

## 3. 方案比较

### 3.1 方案 A：直接统一成 hybrid-only

思路是把当前 `position_only` 与 `operator_program` 直接折叠成单一 `hybrid` 主线，所有运行都走统一 codec 和统一 problem generator。

优点：

- 用户心智最简单。
- 后续只维护一条搜索空间分支。

缺点：

- 风险最大，会立即影响当前稳定 `position-only` 主线。
- 任何 hybrid contract bug 都会污染当前可重复的 COMSOL 主线证据。
- 调试时难以区分“位置变量问题”还是“算子注入问题”。

结论：不推荐作为首轮正式接入方案。

### 3.2 方案 B：两阶段串联搜索

先运行 `operator_program` 搜索，再把结果交给 `position_only` 做局部 refine。

优点：

- 语义清晰，工程实现相对简单。
- 容易做阶段间诊断。

缺点：

- 不是真正的联合搜索空间。
- 会引入阶段切换偏差，难以直接与单次 `pymoo` budget 做公平对照。

结论：可作为后续实验路径，但不是本轮目标。

### 3.3 方案 C：显式模式开关 + hybrid 受控接入

保留当前 `position_only` 为默认稳定模式，同时新增 `operator_program` 和 `hybrid` 两种 opt-in 模式。

优点：

- 风险可控。
- 能与当前稳定主线做公平对照。
- 能逐步验证 `operator_program` 与 `hybrid` 的增益和副作用。

缺点：

- 会新增一层工厂选择和 observability 分支。

结论：推荐采用本方案。

## 4. 最终决策

在 `mass` 主线上新增配置项：

```yaml
optimization:
  search_space_mode: position_only
```

允许的模式值为：

- `position_only`
- `operator_program`
- `hybrid`

默认值必须保持为 `position_only`，以保证当前稳定主线行为完全不变。

其中：

- `position_only` 代表当前稳定主线。
- `operator_program` 代表纯算子搜索，定位为实验/对照模式。
- `hybrid` 代表位置变量与 operator-program 的联合搜索，定位为本轮正式接入目标，但初期仍应标注为 `experimental`。

## 5. Hybrid 的核心语义

### 5.1 基本原则

`hybrid` 不是简单地把两个向量拼起来就结束，而是要明确“状态生成顺序”和“语义合同保护顺序”。

本设计固定以下执行语义：

1. 从 deterministic seed 的 `base_state` 出发。
2. 先解码 `position` 子向量，得到候选几何布局。
3. 再解码 `operator_program` 子向量，得到 action sequence。
4. 将 action sequence 施加到第 2 步布局上。
5. 对施加后的状态执行 contract guard/projection。
6. 再进入现有 proxy / constraint / objective 评估链。

### 5.2 为什么 position 必须先于 operator

若先应用 operator，再把 position 子向量覆盖到状态上，会导致大量几何类 operator 实际失效，例如：

- `group_move`
- `cg_recenter`
- `hot_spread`
- `swap`
- `bus_proximity_opt`
- `fov_keepout_push`

因此，`hybrid` 的唯一推荐语义是：

- `position` 提供主布局自由度。
- `operator_program` 在主布局之上做局部几何、热学、结构、电气语义调整。

### 5.3 什么仍然不开放

首轮 `hybrid` 仍保持当前场景语义合同不变：

- 不开放 `mount_face` 搜索。
- 不开放 `aperture_site` 搜索。
- 不开放 `preferred_orientation` 搜索。
- 对 `shell_contact_required=true` 的实例，仍保持 mount normal flush-to-shell 的轴锁定。

也就是说，`hybrid` 不是“全自由 6D/离散联合搜索”，而是“固定真实场景语义合同上的位置 + 算子联合搜索”。

## 6. 架构设计

### 6.1 配置层

在 `config/system/mass/base.yaml` 中新增：

```yaml
optimization:
  search_space_mode: position_only
```

扩展建议：

```yaml
optimization:
  search_space_mode: hybrid
  hybrid_n_action_slots: 3
  hybrid_operator_weight: 1.0
  hybrid_contract_guard: strict
```

首轮必须只让默认值指向 `position_only`。

### 6.2 运行时选择层

在 `workflow/scenario_runtime.py` 中引入搜索空间工厂逻辑：

- `position_only` -> `PymooProblemGenerator`
- `operator_program` -> `OperatorProgramProblemGenerator`
- `hybrid` -> `HybridProblemGenerator`

该工厂只负责选择 problem generator / codec / repair / seed population 策略，不改变后续 `pymoo` runner、proxy evaluator、STEP/COMSOL 主线合同。

### 6.3 编码层

新增 `HybridGenomeCodec`，其职责是：

- 组合 `DesignStateVectorCodec` 与 `OperatorProgramGenomeCodec`
- 暴露统一的 `xl / xu / n_var`
- 支持 `encode / decode / clip / geometry_arrays_from_state`

推荐的向量布局：

- 前半段：`position` genes
- 后半段：`operator_program` genes

解码流程：

1. `position_codec.decode(position_genes)` 生成中间状态。
2. 使用中间状态作为 operator 的作用基底。
3. `operator_codec.decode_program(operator_genes)` 生成 operator program。
4. `operator_codec.apply_program_to_state(intermediate_state, program)`。
5. 执行 `contract_guard.project(intermediate_state)`。
6. 返回最终 `DesignState`。

### 6.4 合同保护层

新增 `PlacementContractGuard`，统一负责以下保护：

- `mount_axis_locked`
- `shell_contact_required`
- `aperture_site` 对齐
- `mount_face` 法向位置锁
- semantic zone center bounds
- envelope bounds
- 可选的 orientation immutability 审计

保护策略推荐为“投影回合法状态”，而不是简单报错：

- 若 operator 把 payload 从 aperture 对齐位置推离，则只回投影受限轴。
- 若 operator 把壳体挂载件向内推离 shell flush 位置，则把该轴投影回 anchor。
- 若 operator 把组件推出 semantic zone，则按 zone bounds clip 回合法区间。

同时保留审计计数，例如：

- `contract_guard_hits`
- `contract_guard_projection_count`
- `contract_guard_reasons`

### 6.5 Repair 层

当前 `CentroidPushApartRepair` 明确是 `position-only` 思路：

- 它依赖 `DesignStateVectorCodec`
- 它只会重编码位置
- 它不会理解 operator genes

因此首轮不建议直接把它硬套到 `hybrid`。

首轮建议：

- `position_only` 继续沿用当前 repair。
- `operator_program` 首轮可不启用 repair，只依赖 codec clip + contract guard。
- `hybrid` 首轮先用 “decode -> apply operator -> contract projection” 模式，不启用混合 repair。

后续如有必要，再引入 `HybridRepair`，并明确其策略只能修复位置子向量，不应擅自篡改离散算子语义。

## 7. 约束与物理语义边界

### 7.1 当前必须诚实维持的约束口径

接入 `hybrid` 后，仍必须按当前 `mass` 主线口径表述：

- 几何约束：碰撞、间隙、包络边界
- 热约束：代理热学 + 真实 COMSOL 审计路径
- 质心约束：`cg_limit`
- 结构/电源/任务代理约束：仍沿用当前 executable proxy / online truth chain

### 7.2 非几何算子的语义说明

当前 operator-program 中包含以下非纯几何类动作：

- `add_heatstrap`
- `set_thermal_contact`
- `add_bracket`
- `stiffener_insert`

首轮接入时必须显式区分两层语义：

- 代理层是否消费这些状态改动。
- 真实 COMSOL 链是否在当前实现中真实使用这些状态改动。

若某类 action 当前只影响 proxy 而未进入真实 COMSOL truth chain，必须在日志和报告中诚实标为：

- `proxy_effective_only`

不能直接宣称其已经是“真实多物理场 fully validated action”。

## 8. 可观测性与产物合同

`summary.json / result_index.json / report.md` 需要增加以下字段或等价信息：

- `search_space_mode`
- `search_space_lifecycle`
- `operator_program_requested`
- `operator_program_applied`
- `operator_action_sequence`
- `operator_action_families`
- `contract_guard_hits`
- `contract_guard_reasons`
- `operator_delta_metrics`
- `first_feasible_eval`
- `comsol_calls_to_first_feasible`

对 `hybrid` 和 `operator_program` 模式，建议在日志中增加显式 banner：

- `EXPERIMENTAL SEARCH SPACE: HYBRID`
- `EXPERIMENTAL SEARCH SPACE: OPERATOR_PROGRAM`

## 9. 测试与验收

### 9.1 P0 验收

- 新增 `search_space_mode` 配置后，默认 `position_only` 行为完全不变。
- 当前 `optical_remote_sensing_bus` 的稳定主线回归不受影响。

### 9.2 P1 验收

- `operator_program` 模式可完成完整 proxy 搜索。
- 输出包含 action-level observability。
- 失败路径同样稳定落盘。

### 9.3 P2 验收

- `hybrid` 模式可完成完整 proxy 搜索。
- `position` 与 `operator_program` 均参与同一次 `pymoo` 搜索。
- `contract_guard` 能阻止算子破坏 mount/aperture/axis-lock 合同。
- `hybrid` 不影响 `position_only` 的默认行为与结果口径。

### 9.4 P3 验收

- 至少在 `optical_remote_sensing_bus` 上完成 `seed >= 3` 的 `position_only` vs `hybrid` 对照。
- 对照必须使用匹配预算、匹配约束、匹配物理 fidelity。
- 真实 COMSOL 结论只能在通过审计后给出，且必须带上 `first_feasible_eval / comsol_calls_to_first_feasible`。

## 10. 推进顺序

### 阶段 P0：工厂与开关落位

- 加入 `search_space_mode` 配置读取。
- 抽出 problem generator/codec 选择工厂。
- 确保默认值仍是 `position_only`。

### 阶段 P1：纯 operator_program 正式接线

- 让 `operator_program` 模式能从 `scenario_runtime` 走到 `pymoo` 主链。
- 补充 action-level 可观测性。
- 首轮不追求真实 COMSOL 增益，只追求链路完整、口径诚实。

### 阶段 P2：hybrid 接入

- 新增 `HybridGenomeCodec`
- 新增 `HybridProblemGenerator`
- 新增 `PlacementContractGuard`
- 完成 `position + operator` 的统一解码语义与合同投影

### 阶段 P3：验证与文档同步

- 补齐测试矩阵
- 完成 `seed >= 3` 的对照实验
- 根据实现真相更新 `HANDOFF.md`
- 更新 `README.md`
- 如架构决策最终落地，补充对应 ADR

## 11. 风险

- 最大风险是 operator 的几何动作破坏当前 seed 合同，导致现有稳定场景主线回退。
- 第二风险是把 proxy-only 的算子收益误表述为真实 COMSOL 收益。
- 第三风险是 `hybrid` 引入的 repair 语义不清，导致 operator genes 被隐式篡改，最终无法做可解释归因。

## 12. 结论

本设计采纳“默认稳定主线不变 + 显式模式开关 + `hybrid` 受控接入”的路线。

`position_only` 继续作为当前稳定主线。

`operator_program` 作为实验/对照模式接回主链。

`hybrid` 作为正式接入目标，采用“先位置、后算子、再合同投影”的统一语义，以确保：

- 不破坏当前 `mass` 稳定真相
- 能正式纳入仓库已有 operator-program 能力
- 能为后续多 seed、真实 COMSOL、A/B 对照提供清晰、可审计、可回退的推进路径
