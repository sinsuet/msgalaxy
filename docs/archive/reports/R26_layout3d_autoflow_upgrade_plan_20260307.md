# MsGalaxy `layout3d` / `autoflow` 升级适配与重构方案

版本日期：2026-03-07  
适用对象：需要判断既有 `Layout3DCube` / `AutoFlowSim` 导入资产是否仍适配当前 `MP-OP-MaaS v3 transition` 架构，并据此安排升级重构工作的项目成员。  
结论口径：本报告只陈述“当前仓内真实接入状态 + 面向现行架构的升级建议”，不把规划态能力表述成已实现能力。

---

## 1. 先给结论

结论是：**需要升级，而且不是“补几个接口”级别，而是要做一次带边界重定义的适配重构。**

但两部分的处理方式不一样：

1. **`layout3d` 需要保留并升级**  
   它在当前仓内已经成为几何初始化、禁区切分、AABB 装箱、初始布局生成的重要来源，属于“还在主链路上、但定位需要重构”的资产。

2. **`autoflow` 不应按原仓整体回迁，而应只保留可证明有价值的几何变形子能力并重构接入方式**  
   当前仓内真正留下来的 `AutoFlowSim` 资产本质上只有 FFD 变形思路；其原始 BMDO/TBDO、MATLAB、GUI 自动化、外部 CFD 流程，与当前项目批准基线（`pymoo + proxy + online COMSOL`）并不一致，不应直接并回现主架构。

3. **`agent_loop` 相关升级当前可以暂缓**  
   结合当前仓内主线与项目状态，`mass` 才是当前主执行主线；`agent_loop` 已不是当前优先演进对象。涉及 `agent_loop` 的动作语言统一、旧算子清理、legacy 兼容层等工作，应在文档中明确列为“中后期/待办事项”，而不是当前阶段的主交付阻塞项。

换句话说：

- **`layout3d` 应从“直接给最终布局的老式装箱器”升级为“几何种子/可行化/几何实现服务”**；
- **`autoflow` 应从“旧 CFD 自动化平台残片”重构为“受约束的参数化几何变换能力”，且只有在能与 STEP / COMSOL / gate 链路对齐时才能进入主线。**

---

## 2. 当前仓内真实接入范围

### 2.1 `layout3d` 的真实接入状态

当前仓内和 `Layout3DCube` 直接相关的主资产主要已经落在 `geometry/`：

- `geometry/schema.py`：AABB、Part、EnvelopeGeometry、PackingResult 等几何数据结构。
- `geometry/keepout.py`：包络构建、keepout AABB 切分、可用子容器生成。
- `geometry/packing.py`：基于 `py3dbp` 的多启动贴壁装箱。
- `geometry/layout_engine.py`：把 BOM / keepout / envelope / packing 串成初始布局引擎。
- `geometry/cad_export_occ.py`：几何状态到 STEP 的导出。

这些资产不是“孤立遗留代码”，而是仍在被当前系统使用：

- `workflow/orchestrator.py` 在模块初始化时创建 `LayoutEngine`；
- 初始布局阶段直接调用 `layout_engine.generate_layout()`；
- 生成结果会被转成统一 `DesignState` 后继续进入现运行时；
- `agent_loop` 的 `REPACK` 仍回调这个布局引擎。

因此，`layout3d` 不是“可删的历史附件”，而是**仍在起作用，但角色已经落后于当前架构目标**。

### 2.2 `autoflow` 的真实接入状态

当前仓内与 `AutoFlowSim` 明确相关的代码痕迹非常有限：

- `geometry/ffd.py` 标明“参考 AutoFlowSim 的 3D-Free-Form-Deformation 模块”；
- `agent_loop` / `operation_executor` 中的 `DEFORM` 会调用 `FFDDeformer`。

但它并没有保留原 `AutoFlowSim` 的主干：

- 未见 BMDO/TBDO 主流程接入；
- 未见 MATLAB 优化驱动接入；
- 未见 GUI 自动化、Server/Client、Numeca/StarCCM 后处理链接入；
- 当前真实仿真主线是 `proxy + online COMSOL`，不是旧 CFD 自动化平台。

所以从“仓内真实状态”看，**`autoflow` 现在并不是一个成体系接入模块，而只是留下了一段 FFD 变形能力。**

---

## 3. 为什么现状已经不满足当前架构要求

### 3.1 `layout3d` 的定位和当前 `mass` 主线冲突

当前批准基线要求：

- 由 `ModelingIntent -> compile -> pymoo problem -> solve` 驱动；
- 变量有显式边界；
- 约束统一落到 `g(x) <= 0`；
- 最终布局不能靠外部启发式直接替代数值搜索。

而 `layout3d` 的核心仍然是“直接装箱出坐标”。  
这在今天**可以作为初始布局/热启动/可行化启发式**，但已经不适合作为主优化器。

如果不重构，会出现两个问题：

1. 在概念上容易把“装箱结果”误当成“优化结果”；
2. 在工程上它和当前 `coordinate / operator_program / hybrid` 搜索空间体系是脱节的。

### 3.2 `layout3d` 的数据协议还停留在“装箱器视角”

当前仓内至少存在两套几何语义：

1. `geometry.Part` / `AABB`：偏装箱器语义，强调最小角、安装面、贴壁尺寸；
2. `core.protocol.ComponentGeometry` / `DesignState`：偏统一运行时语义，强调中心点、统一状态、跨物理域评估。

目前 `workflow/orchestrator.py` 里仍存在从 `Part` 最小角坐标转换到 `ComponentGeometry` 中心点坐标的桥接逻辑。  
这说明 `layout3d` 还没有被完全吸收到统一协议中，而是靠“运行时临时转译”接入。

这种状态在当前小规模可运行，但继续扩展会带来：

- 中心点 / 最小角语义漂移；
- clearance 语义在布局、评估、导出之间不一致；
- cylinder / bracket / heatsink 等动态几何属性无法在布局层获得统一约束语义。

### 3.3 `layout3d` 仍只覆盖“几何壳”，无法直接承载 v3 多物理约束契约

`layout3d` 的强项是：

- AABB 包络；
- keepout 切分；
- 贴壁排布；
- 初始装箱。

但当前基线要求主链路至少协同：

- `geometry`
- `thermal`
- `structural`
- `power`
- `mission(keepout)`

现状问题是：

- `layout3d` 本身并不理解结构 / 电源 / mission 约束；
- mission 默认仍以 keepout 代理接口为主；
- 布局层没有明确输出“这个几何状态在哪些物理域上已知可解释、哪些仍是代理近似”。

它不是不能用，而是**必须被降级为几何子系统，并接受上层多物理契约约束**。

### 3.4 几何指标存在占位值和重复实现

当前 `workflow/orchestrator.py` 的 `packing_efficiency` 仍是占位值。  
同时，clearance / collision / boundary 等几何逻辑也散落在不同模块里。

这带来两个直接后果：

1. `layout3d` 的输出质量难以被统一量化；
2. 后续 benchmark / gate / reflection 读到的几何信号不够可信，无法支撑 release 级结论。

### 3.5 `autoflow` 的 FFD 能力与“真实可执行几何”不闭环

当前 `DEFORM` 虽然会调用 `FFDDeformer`，但在主执行上更像：

- 生成一个局部控制点位移想法；
- 最终只是修改组件的盒体尺寸；
- 没有形成与 STEP / COMSOL 一致的真实几何实现链。

这意味着：

- 对 agent 来说它像“几何算子”；
- 对真实物理链来说它却仍近似成一个盒体尺寸变化。

如果继续保留这种状态，会出现“动作看起来很高级，但 realization 不可审计”的问题。  
这与当前 strict gate 的治理方向不一致。

### 3.6 `agent_loop` 仍在使用旧操作语义，和 v3 canonical operator family 不完全一致

当前 `agent_loop` 仍暴露旧操作族：

- `MOVE / ROTATE / SWAP / REPACK / DEFORM / ALIGN / CHANGE_ENVELOPE / ADD_BRACKET`

而当前 v3 canonical action family 是：

- `group_move / cg_recenter / hot_spread / swap`
- `add_heatstrap / set_thermal_contact`
- `add_bracket / stiffener_insert`
- `bus_proximity_opt / fov_keepout_push`

问题不在于名字不同，而在于：

- 老操作是“局部直接动作语义”；
- 新操作是“可审计、可门禁、可归因的 operator family 语义”。

如果 `layout3d` / `autoflow` 不做适配，`agent_loop` 与 `mass` 的动作语言会继续分叉，最终导致：

- 结果不可横向比较；
- operator-family gate 难以统一；
- action-level attribution 无法稳定落账。

但这里需要明确一个优先级边界：

- **该问题真实存在；**
- **但它不是当前主线阻塞项；**
- **当前阶段应先服务 `mass` 主线，`agent_loop` 相关升级可暂时搁置，仅在文档中保留风险说明与后续收敛方向。**

### 3.7 `autoflow` 原主栈与当前批准技术栈不一致

从你提供的 `README-autosim.md` 看，旧 `AutoFlowSim` 的完整价值主张是：

- MATLAB 优化；
- BMDO/TBDO；
- GUI 自动化；
- 外部 CFD 平台；
- FFD；
- 分布式客户端/服务端。

而当前项目批准基线是：

- LLM + pymoo；
- proxy + online COMSOL；
- operator program + strict gate；
- run_scenario 统一栈入口。

因此，**我们需要的不是“把 AutoFlowSim 整仓重新并进来”，而是“只抽取仍与当前主线兼容的参数化几何能力，并用当前协议重写接口”**。

---

## 4. 升级重构总原则

### 原则 A：`layout3d` 只做几何服务，不再做主优化器

保留它的价值，但重新限定职责：

- 做初始布局生成；
- 做几何可行化修复；
- 做 seed population 提议；
- 做 geometry-only 局部 repair；
- 做真实导出前的几何 realization 辅助。

不再把它作为：

- 最终优化结果来源；
- 对 `mass` 的替代搜索器；
- 多物理约束解释的唯一依据。

### 原则 B：`autoflow` 只保留“可 realization 的变形能力”

保留 FFD 思想，但前提是它必须进入下面的闭环：

`operator action -> geometry mutation -> STEP realization -> evaluator trace -> gate`

不能进入闭环的部分，只能作为：

- 离线预处理；
- 可视化/演示工具；
- 研究性分支，不进入主线结论。

### 原则 C：统一动作语言是中后期治理项，当前先保证 `mass` 主线闭环

需要引入“兼容层”，把旧动作逐步映射到 canonical v3 action family：

- `MOVE` -> `group_move`
- `SWAP` -> `swap`
- `ADD_BRACKET` -> `add_bracket`
- `DEFORM` -> 拆解为受限的 `hot_spread` / `set_thermal_contact` / realization-aware geometry mutation
- `REPACK` -> 降级为 seed/regenerate，不再当作主算子
- `ALIGN` / `CHANGE_ENVELOPE` -> 只有在统一协议可审计后才保留，否则从主线剥离

但当前执行顺序应明确为：

- 先完成 `layout3d` 与 `autoflow/FFD` 对 `mass` 主线的适配；
- 再处理 `agent_loop` 的 legacy 动作兼容；
- 在此之前，只需在文档和风险项中说明 `agent_loop` 非主线、升级可延期。

### 原则 D：先做协议和指标治理，再做功能增强

先补：

- 统一 geometry contract；
- 真正的 packing efficiency；
- 统一 boundary / clearance / collision / keepout 计算；
- 布局种子与物理评估之间的 trace。

再谈：

- 更复杂的动态几何；
- FFD 真正上线；
- mission/FOV 更高保真耦合。

---

## 5. 建议的目标形态

## 5.1 `layout3d` 升级后的目标角色：`Geometry Seed + Feasibility Service`

建议把现有 `layout3d` 资产重构为四层：

### 第一层：协议适配层

目标：把 `Part/AABB/PackingResult` 与 `DesignState/ComponentGeometry` 的桥接收口到单一适配器。

建议新增或收敛的职责：

- `geometry/adapters.py`
  - `part_to_component_geometry(...)`
  - `packing_result_to_design_state(...)`
  - `design_state_to_packing_parts(...)`

这样 `workflow/orchestrator.py` 不再手写最小角 -> 中心点转换细节。

### 第二层：布局种子层

目标：把 `LayoutEngine` 从“直接给最终答案”改成“给 pymoo 提供高质量初始种群/初始化状态”。

建议新增：

- `geometry/layout_seed_service.py`

职责：

- 基于 BOM、envelope、keepout 生成 1~N 个 deterministic / stochastic seed；
- 输出统一 `DesignState`；
- 允许注入到 `workflow/modes/mass/runtime_support.py::_build_maas_seed_population(...)`；
- 为 `coordinate` 和 `hybrid_coordinate` 模式提供 warm-start；
- 对 `operator_program` 模式提供初始参考状态，而不是绕过 codec。

### 第三层：几何指标层

建议抽出：

- `geometry/metrics.py`

统一实现：

- `min_clearance`
- `collision_count`
- `boundary_violation`
- `packing_efficiency`
- keepout coverage / usable volume ratio（可选）

这层必须成为：

- orchestrator 几何评估的唯一来源；
- pymoo problem generator 的 geometry metric 复用来源；
- benchmark / gate / observability 的统一口径。

### 第四层：几何 realization 层

建议把 `cad_export_occ.py` 从“导出工具”升级为“几何真实化接口”：

- 支持 box / cylinder / heatsink / bracket 的一致 realization；
- 输出 realization metadata；
- 标注哪些几何修改是真实 STEP 几何、哪些仍是 proxy 语义；
- 为未来 mission/FOV、COMSOL 域审计提供统一入口。

---

## 5.2 `autoflow` 升级后的目标角色：`Parametric Geometry Mutation Service`

### 结论先行

**不建议把旧 `AutoFlowSim` 当成仿真平台重新接回来。**

建议只抽取它在当前体系仍有价值的一件事：  
**“参数化几何变换/FFD 思想”**。

### 建议重构方向

把 `geometry/ffd.py` 从“独立数学模块”升级为“受主线约束的几何变换服务”：

- 新增 `geometry/deformation_contracts.py`
- 新增 `geometry/deformation_service.py`

定义最小闭环接口：

- `preview_mutation(component, action) -> mutation_preview`
- `apply_mutation(component, action) -> updated_geometry_payload`
- `realize_mutation_to_step(component, mutation) -> realization_artifact`
- `validate_mutation_realization(mutation, realization) -> gate payload`

### 关键治理要求

只有满足下面条件的变形，才能进入主线 operator：

1. 变形后仍能被统一几何协议表达；
2. 变形后可被 STEP / COMSOL 真实化；
3. 指标变化能被 trace；
4. action family 能被 gate 正确归类。

否则就只能留在：

- agent_loop 研究模式；
- 可视化工具；
- 离线几何预处理脚本。

### 对 `DEFORM` 的建议

当前通用 `DEFORM` 语义过宽，不建议继续作为主线 canonical action。  
建议拆成两类：

1. **物理可解释的 canonical operator**
   - 例如 `add_heatstrap`
   - `set_thermal_contact`
   - `add_bracket`
   - `stiffener_insert`

2. **实验性 geometry mutation**
   - 明确标记 `source=proxy_geometry_mutation`
   - 不参与 strict-real 结论
   - 除非 realization gate 通过

---

## 6. 推荐实施路径

### Phase 0：冻结现状与边界定义（优先级最高）

目标：先防止历史导入资产继续“隐式扩张”。

建议动作：

- 在文档中明确：
  - `layout3d` 当前是 seed/init 子系统，不是主优化器；
  - `autoflow` 当前只保留 FFD 思想，不是当前仿真主链。
- 为以下内容建立显式 contract test：
  - `LayoutEngine -> DesignState`
  - `DesignState -> geometry metrics`
  - `geometry mutation -> realization metadata`

产出：

- 一组协议测试；
- 一份 geometry service 边界说明。

### Phase 1：重构 `layout3d` 为布局种子服务

目标：让 `layout3d` 服务于 `mass`，而不是与 `mass` 并行。

建议动作：

- 抽出 `packing_result_to_design_state` 适配器；
- 把初始布局生成逻辑从 `workflow/orchestrator.py` 下沉到 geometry service；
- 将 `LayoutEngine` 输出接入 `_build_maas_seed_population(...)`；
- 允许同一 BOM 生成多 seed，并记录 seed provenance；
- 为 `operator_program` 模式保留参考基态，但不直接覆盖 codec 搜索。

验收口径：

- `mass` 能注入 layout seeds；
- strict gate 统计不被破坏；
- 初始可行率或 first-feasible-eval 有可测改善。

### Phase 2：补齐几何指标治理

目标：让布局质量从“经验值”变成“统一可审计指标”。

建议动作：

- 实现真实 `packing_efficiency`；
- 统一 clearance / collision / boundary / keepout 口径；
- 在 observability 中补：
  - geometry seed source
  - seed rank
  - geometry repair count
  - packing efficiency

验收口径：

- `workflow/orchestrator.py` 不再硬编码 `packing_efficiency=75.0`；
- geometry metrics 可被 benchmark 与 reflection 直接消费。

### Phase 3：把 `autoflow` FFD 改造成 realization-aware mutation

目标：让几何变形从“动作幻觉”变成“可执行能力”。

建议动作：

- 把当前 `DEFORM` 从 agent_loop 旧动作中隔离；
- 定义 mutation contract；
- 先支持最小可实现子集：
  - 盒体拉伸（仍可映射为 box/cylinder 参数变化）
  - bracket / heatsink 这类可直接被 STEP 表达的附加结构
- 暂不把自由曲面 FFD 直接送入 strict-real 主线，除非 realization fully closed。

验收口径：

- 每个 mutation 都能产出 realization metadata；
- 不能 realization 的 mutation 自动降级为 research-only。

### Phase 4：统一 `agent_loop` 与 `mass` 的动作语言（可延期）

目标：避免双语义系统长期并存。

建议动作：

- 增加 legacy -> canonical operator adapter；
- 在 agent prompt 与 runtime 执行层统一 family 标签；
- 把 `REPACK` 改造成“几何 seed regenerate”而不是主操作；
- 对 `ALIGN` / `CHANGE_ENVELOPE` 做保留审查：
  - 能归入 canonical family 的留下；
  - 不能归入的退出主线。

验收口径：

- `agent_loop` 和 `mass` 的 action attribution 可以并表；
- `operator_family_gate_passed` 统计口径一致。

阶段说明：

- 该阶段**不是当前主线里程碑前置条件**；
- 当前项目若聚焦 `mass + online COMSOL + strict gate` 主线，可将本阶段放入 backlog；
- 现阶段只需在文档中保留边界声明，避免外部把 `agent_loop` 误认为当前升级重点。

### Phase 5：接入真实 benchmark 评估升级收益

关注指标：

- first feasible eval
- feasible ratio
- best CV min
- geometry seed 命中率
- operator-family gate 通过率
- operator-realization gate 通过率
- online COMSOL calls to first feasible

注意：

- 对 FFD/geometry mutation 的结论，必须区分 proxy-only 与 strict-real。

---

## 7. 我建议的具体落地顺序

如果按“投入产出比”和“是否服务当前主线”排序，我建议这样做：

1. **先改 `layout3d`，后改 `autoflow`**  
   因为 `layout3d` 已在主链路上，升级它能立刻改善 `mass` 初始化与几何治理质量。

2. **先改协议和指标，再改复杂几何能力**  
   否则会把更多 legacy 代码接到一个尚未统一的几何语义层上。

3. **先把 `DEFORM` 从主线可信能力里降级，再决定是否重新升格**  
   当前它缺 realization gate，不适合继续模糊地留在主线动作集合里。

4. **`agent_loop` 升级放到后序 backlog，不占用当前主线改造预算**  
   当前只需保留文档说明：`agent_loop` 非当前主线，相关适配不是本轮升级阻塞项。

简化版排期建议：

- Sprint A：`layout3d seed service + geometry metrics`
- Sprint B：`FFD mutation contract + realization metadata`
- Sprint C：`strict-real benchmark recheck`
- Backlog：`agent_loop canonical operator adapter`

---

## 8. 风险与边界

### 风险 1：把 `layout3d` 升级成“另一个优化器”

这是最需要避免的错误。  
它必须服务 `pymoo`，不能替代 `pymoo`。

### 风险 2：把 `autoflow` 的旧仿真栈重新并回当前主线

这会直接引入：

- MATLAB 依赖；
- GUI 自动化不稳定性；
- 外部 CFD 工具链耦合；
- 与当前 COMSOL 主线冲突的维护成本。

### 风险 3：过早把 FFD 纳入 strict-real 结论

如果几何 realization 还不闭环，FFD 只能作为研究功能，不能作为正式性能结论依据。

### 风险 4：继续容忍多套几何语义并存

中心点、最小角、clearance、install dims、STEP realization 如果继续分散，会越来越难做 release 级治理。

---

## 9. 最终建议

最终建议可以概括成一句话：

> **要升级，但方式不是“把旧仓再贴一层胶水”，而是按当前 `mass + pymoo + multiphysics + operator-program + strict gate` 架构，把 `layout3d` 重构为几何种子/可行化服务，把 `autoflow` 重构为受 realization gate 约束的参数化几何变换服务。**

更明确一点：

- **`layout3d`：建议立即进入升级改造清单，优先级高。**
- **`autoflow`：建议只保留 FFD/参数化几何变换思想，拒绝整仓回迁，优先级中。**
- **`agent_loop`：明确不是当前主线，相关升级可暂缓，但需在文档中持续声明该边界。**
- **`DEFORM`：建议先降级为 research-only 或拆解映射，再决定是否重回主线。**

如果只允许做一个最小高价值动作，我建议先做：

> **“把 `layout3d` 变成 `mass` 的 layout seed service，并补齐真实几何指标口径。”**

这是当前最稳、最符合现架构、也最容易在 benchmark 上体现收益的一步。
