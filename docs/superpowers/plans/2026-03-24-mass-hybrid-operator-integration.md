# Mass Hybrid Operator Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏当前 `mass` 稳定 `position_only` 主线的前提下，正式接入 `operator_program` 与 `hybrid` 搜索空间，并补齐合同保护、观测字段、测试和文档同步。

**Architecture:** 保持 `pymoo` 为唯一数值优化核心，通过显式 `search_space_mode` 工厂选择不同 generator/codec。`hybrid` 使用“先 position、后 operator、再 contract projection”的语义，并把 mount/aperture/axis-lock 保护集中在单独的 contract guard 中。

**Tech Stack:** Python, pymoo, NumPy, current `mass` scenario runtime, pytest, COMSOL-facing artifact contract

---

## Preflight

- 当前仓库 worktree 很脏，执行实现前应优先在独立 worktree 或独立 feature branch 中进行，避免把无关改动卷入本次接线。
- 本计划默认在 VS Code 集成终端执行 `python -m pytest ...`。
- 本计划只覆盖 `mass` + `optical_remote_sensing_bus` 首轮正式接线，不扩第二个真实场景。

## File Map

### New files

- `optimization/modes/mass/pymoo_integration/search_space_factory.py`
  - 统一根据 `search_space_mode` 选择 generator、repair、seed population 和 observability metadata。
- `optimization/modes/mass/pymoo_integration/contract_guard.py`
  - 集中实现 `mount_axis_locked / shell_contact_required / aperture / semantic zone / envelope` 保护和投影。
- `optimization/modes/mass/pymoo_integration/hybrid_codec.py`
  - 组合 `DesignStateVectorCodec` 与 `OperatorProgramGenomeCodec` 的联合 genome codec。
- `optimization/modes/mass/pymoo_integration/hybrid_problem_generator.py`
  - `hybrid` 搜索空间的 problem generator。
- `tests/test_mass_search_space_factory.py`
  - 覆盖 mode 选择、默认值、fail-fast、变量名接口和初始种群合同。
- `tests/test_contract_guard.py`
  - 覆盖 aperture 锁、mount axis 锁、semantic zone 投影、envelope clip。
- `tests/test_hybrid_codec.py`
  - 覆盖 hybrid decode 顺序、operator 后投影、变量名和 seed population 合同。

### Modified files

- `config/system/mass/base.yaml`
  - 增加 `optimization.search_space_mode` 默认配置和 hybrid/operator 相关参数。
- `workflow/scenario_runtime.py`
  - 用 factory 取代硬编码 `PymooProblemGenerator`，并写入新的 search-space observability 字段。
- `optimization/modes/mass/pymoo_integration/problem_generator.py`
  - 提供统一的变量名/搜索空间描述接口，避免 runtime 直接假设 `variable_specs` 存在。
- `optimization/modes/mass/pymoo_integration/operator_problem_generator.py`
  - 对齐统一 generator 接口，暴露 operator-mode 的变量名、seed population 和 search-space metadata。
- `optimization/modes/mass/pymoo_integration/operator_program_codec.py`
  - 补充 operator 基因命名接口，必要时接受外部 state 基底来应用 operator。
- `optimization/modes/mass/pymoo_integration/repair.py`
  - 明确 repair 只用于 `position_only`，禁止被 `operator_program/hybrid` 隐式复用。
- `tests/test_scenario_runtime_contract.py`
  - 增加默认 mode 不变、hybrid/operator observability、失败落盘字段的回归断言。
- `HANDOFF.md`
  - 在实现完成且验证通过后，更新 `mass` 主线真实状态与 experimental search-space 边界。
- `README.md`
  - 在实现完成且验证通过后，补充 `search_space_mode` 的使用说明和实验边界。
- `docs/adr/0016-mass-search-space-mode-hybrid-operator-integration.md`
  - 在实现完成且设计不再变化后，记录正式 ADR。

## Task 1: 建立 Search-Space 合同与工厂

**Files:**
- Create: `optimization/modes/mass/pymoo_integration/search_space_factory.py`
- Modify: `config/system/mass/base.yaml`
- Modify: `optimization/modes/mass/pymoo_integration/problem_generator.py`
- Modify: `optimization/modes/mass/pymoo_integration/operator_problem_generator.py`
- Test: `tests/test_mass_search_space_factory.py`

- [ ] **Step 1: 写失败测试，锁定默认值和 mode 选择合同**

```python
def test_search_space_factory_defaults_to_position_only():
    bundle = build_search_space_bundle(problem_spec=spec, optimization_cfg={})
    assert bundle.mode == "position_only"
    assert bundle.generator.search_space_mode == "position_only"


def test_search_space_factory_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown_search_space_mode"):
        build_search_space_bundle(
            problem_spec=spec,
            optimization_cfg={"search_space_mode": "bad_mode"},
        )
```

- [ ] **Step 2: 运行测试，确认当前尚未实现**

Run: `python -m pytest tests/test_mass_search_space_factory.py -q`

Expected: FAIL，报 `ModuleNotFoundError` 或 `NameError`，因为 `search_space_factory.py` 和统一 mode 合同尚未实现。

- [ ] **Step 3: 增加配置默认值并实现工厂**

```python
@dataclass
class SearchSpaceBundle:
    mode: str
    lifecycle: str
    generator: Any
    repair: Any | None
    initial_population: np.ndarray | None
    metadata: dict[str, Any]


def build_search_space_bundle(*, problem_spec, optimization_cfg) -> SearchSpaceBundle:
    mode = str(optimization_cfg.get("search_space_mode", "position_only")).strip().lower()
    if mode == "position_only":
        ...
    if mode == "operator_program":
        ...
    if mode == "hybrid":
        ...
    raise ValueError(f"unknown_search_space_mode:{mode}")
```

实现要求：

- `config/system/mass/base.yaml` 默认写入 `search_space_mode: position_only`
- `PymooProblemGenerator` 增加：
  - `search_space_mode` 属性，默认 `position_only`
  - `variable_names()` 方法
  - `build_initial_population()` 方法，默认沿用当前位置扰动策略
- `OperatorProgramProblemGenerator` override：
  - `search_space_mode = "operator_program"`
  - `variable_names()` 返回 operator 基因名，例如 `slot0_action`, `slot0_comp_a`, `slot0_comp_b`, `slot0_axis`, `slot0_magnitude`, `slot0_focus`
  - `build_initial_population()` 优先走 `OperatorProgramGenomeCodec.build_seed_population()`

- [ ] **Step 4: 运行测试，确认工厂合同建立**

Run: `python -m pytest tests/test_mass_search_space_factory.py -q`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config/system/mass/base.yaml \
  optimization/modes/mass/pymoo_integration/search_space_factory.py \
  optimization/modes/mass/pymoo_integration/problem_generator.py \
  optimization/modes/mass/pymoo_integration/operator_problem_generator.py \
  tests/test_mass_search_space_factory.py
git commit -m "feat: add mass search-space factory contract"
```

## Task 2: 实现 PlacementContractGuard

**Files:**
- Create: `optimization/modes/mass/pymoo_integration/contract_guard.py`
- Modify: `domain/satellite/seed.py`
- Test: `tests/test_contract_guard.py`

- [ ] **Step 1: 写失败测试，锁定 seed 合同不可被 operator 破坏**

```python
def test_contract_guard_projects_shell_locked_axis_back_to_anchor():
    guarded = guard.project(mutated_state)
    payload = component_map(guarded)["payload_camera"]
    assert payload.position.z == 45.0


def test_contract_guard_projects_semantic_zone_violation_back_inside_bounds():
    guarded = guard.project(mutated_state)
    assert lower <= guarded_component.position.y <= upper
```

- [ ] **Step 2: 运行测试，确认 guard 尚不存在**

Run: `python -m pytest tests/test_contract_guard.py -q`

Expected: FAIL，报 `ModuleNotFoundError` 或 `AttributeError`

- [ ] **Step 3: 实现 guard 与 placement-state 读取**

```python
class PlacementContractGuard:
    def __init__(self, *, base_state, semantic_zones):
        ...

    def project(self, state: DesignState) -> tuple[DesignState, dict[str, Any]]:
        projected = state.model_copy(deep=True)
        audit = {"hits": 0, "reasons": []}
        ...
        return projected, audit
```

实现要求：

- 从 `DesignState.metadata["placement_state"]` 读取：
  - `mount_face`
  - `aperture_site`
  - `shell_contact_required`
  - `mount_axis_locked`
- 投影顺序固定为：
  1. envelope bounds
  2. semantic zone bounds
  3. mount-face anchor / shell flush 轴锁
  4. aperture 对齐轴
- `project()` 返回：
  - 投影后的状态
  - 审计字典，例如 `hits / projection_count / reasons / affected_components`

- [ ] **Step 4: 运行 guard 测试**

Run: `python -m pytest tests/test_contract_guard.py -q`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add optimization/modes/mass/pymoo_integration/contract_guard.py \
  domain/satellite/seed.py \
  tests/test_contract_guard.py
git commit -m "feat: add placement contract guard for mass search spaces"
```

## Task 3: 实现 HybridGenomeCodec

**Files:**
- Create: `optimization/modes/mass/pymoo_integration/hybrid_codec.py`
- Modify: `optimization/modes/mass/pymoo_integration/operator_program_codec.py`
- Test: `tests/test_hybrid_codec.py`

- [ ] **Step 1: 写失败测试，锁定 hybrid 解码顺序**

```python
def test_hybrid_codec_applies_position_before_operator_program():
    state = codec.decode(vector)
    assert component_map(state)["battery_pack"].position.x == expected_after_operator


def test_hybrid_codec_projects_back_to_seed_contract_after_operator():
    state = codec.decode(vector)
    payload = component_map(state)["payload_camera"]
    assert payload.position.z == 45.0
```

- [ ] **Step 2: 运行测试，确认 hybrid codec 尚不存在**

Run: `python -m pytest tests/test_hybrid_codec.py -q`

Expected: FAIL，报 `ModuleNotFoundError`

- [ ] **Step 3: 实现联合 codec**

```python
class HybridGenomeCodec:
    search_space_mode = "hybrid"

    def __init__(self, *, base_state, semantic_zones, ...):
        self.position_codec = DesignStateVectorCodec(...)
        self.operator_codec = OperatorProgramGenomeCodec(base_state=base_state, ...)
        self.contract_guard = PlacementContractGuard(...)

    def decode(self, x: np.ndarray) -> DesignState:
        pos_x, op_x = self._split(x)
        state = self.position_codec.decode(pos_x)
        program = self.operator_codec.decode_program(op_x)
        self.operator_codec.apply_program_to_state(state, program)
        guarded, _ = self.contract_guard.project(state)
        return guarded
```

实现要求：

- `n_var / xl / xu / clip()` 对外表现为一个统一向量。
- 提供 `variable_names()`：
  - 前段沿用 position 变量名
  - 后段追加 operator 基因名，建议前缀 `op_`
- 提供 `build_seed_population()`：
  - 第 1 个 seed 为 base position + neutral operator
  - 至少再提供若干 `cg_recenter/group_move/hot_spread` warm-start 组合
- 修改 `OperatorProgramGenomeCodec`，让它至少暴露：
  - `variable_names()`
  - `build_seed_population()`
  - `decode_program()` 可在 hybrid 中复用

- [ ] **Step 4: 运行 hybrid codec 测试**

Run: `python -m pytest tests/test_hybrid_codec.py -q`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add optimization/modes/mass/pymoo_integration/hybrid_codec.py \
  optimization/modes/mass/pymoo_integration/operator_program_codec.py \
  tests/test_hybrid_codec.py
git commit -m "feat: add hybrid genome codec for mass search"
```

## Task 4: 实现 HybridProblemGenerator 并接入 ScenarioRuntime

**Files:**
- Create: `optimization/modes/mass/pymoo_integration/hybrid_problem_generator.py`
- Modify: `workflow/scenario_runtime.py`
- Modify: `optimization/modes/mass/pymoo_integration/repair.py`
- Test: `tests/test_scenario_runtime_contract.py`

- [ ] **Step 1: 写失败测试，锁定 runtime 能按 mode 选择搜索空间**

```python
def test_scenario_runtime_defaults_to_position_only_search_space(tmp_path):
    result = runtime.execute()
    summary = load_summary(result)
    assert summary["search_space_mode"] == "position_only"


def test_scenario_runtime_records_hybrid_search_space_metadata(tmp_path):
    result = runtime.execute()
    summary = load_summary(result)
    assert summary["search_space_mode"] == "hybrid"
    assert summary["search_space_lifecycle"] == "experimental"
```

- [ ] **Step 2: 运行定向测试，确认尚未接线**

Run: `python -m pytest tests/test_scenario_runtime_contract.py -q`

Expected: FAIL，新增断言缺失 `search_space_mode`，或 runtime 仍硬编码 `PymooProblemGenerator`

- [ ] **Step 3: 实现 hybrid generator 和 runtime 接线**

```python
class HybridProblemGenerator(PymooProblemGenerator):
    def __init__(self, *, spec, codec=None, ...):
        hybrid_codec = codec or HybridGenomeCodec(...)
        self.search_space_mode = "hybrid"
        super().__init__(spec=spec, codec=hybrid_codec)
```

`workflow/scenario_runtime.py` 改动要求：

- `_run_optimizer()` 不再硬编码 `PymooProblemGenerator(problem_spec)`
- 改为：
  - 调 factory 取 `SearchSpaceBundle`
  - 用 `bundle.generator.create_problem()`
  - 用 `bundle.initial_population`
  - 用 `bundle.repair`
- `repair.py` 明确只允许被 `position_only` 绑定：
  - 若误绑定到 `operator_program/hybrid`，应 fail-fast 或在 factory 中直接返回 `None`

- [ ] **Step 4: 跑定向 runtime 合同测试**

Run: `python -m pytest tests/test_scenario_runtime_contract.py -q`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add optimization/modes/mass/pymoo_integration/hybrid_problem_generator.py \
  workflow/scenario_runtime.py \
  optimization/modes/mass/pymoo_integration/repair.py \
  tests/test_scenario_runtime_contract.py
git commit -m "feat: wire hybrid and operator search spaces into runtime"
```

## Task 5: 补齐 Observability 与失败落盘字段

**Files:**
- Modify: `workflow/scenario_runtime.py`
- Modify: `optimization/modes/mass/pymoo_integration/search_space_factory.py`
- Test: `tests/test_scenario_runtime_contract.py`

- [ ] **Step 1: 写失败测试，锁定产物字段**

```python
def test_runtime_summary_includes_search_space_observability(tmp_path):
    result = runtime.execute()
    summary = load_summary(result)
    assert summary["search_space_mode"] == "hybrid"
    assert summary["optimizer_metadata"]["search_space_mode"] == "hybrid"
    assert "contract_guard_hits" in summary
    assert "operator_action_sequence" in summary
```

- [ ] **Step 2: 运行测试，确认字段尚未完全落盘**

Run: `python -m pytest tests/test_scenario_runtime_contract.py -q`

Expected: FAIL，提示 `search_space_mode`、`operator_action_sequence` 或 `contract_guard_hits` 缺失

- [ ] **Step 3: 在 summary/result/report 中写入字段**

实现要求：

- `summary.json` 至少新增：
  - `search_space_mode`
  - `search_space_lifecycle`
  - `optimizer_metadata.search_space_mode`
  - `optimizer_metadata.variable_names`
  - `operator_action_sequence`
  - `operator_action_families`
  - `contract_guard_hits`
  - `contract_guard_reasons`
- `result_index.json` 同步保留面向工具消费的同类字段
- `report.md` 增加一节 `## Search Space`

- [ ] **Step 4: 重新跑定向测试**

Run: `python -m pytest tests/test_scenario_runtime_contract.py -q`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add workflow/scenario_runtime.py \
  optimization/modes/mass/pymoo_integration/search_space_factory.py \
  tests/test_scenario_runtime_contract.py
git commit -m "feat: add search-space observability to runtime artifacts"
```

## Task 6: 端到端验证与文档同步

**Files:**
- Modify: `HANDOFF.md`
- Modify: `README.md`
- Create: `docs/adr/0016-mass-search-space-mode-hybrid-operator-integration.md`
- Test: `tests/test_mass_search_space_factory.py`
- Test: `tests/test_contract_guard.py`
- Test: `tests/test_hybrid_codec.py`
- Test: `tests/test_scenario_runtime_contract.py`
- Test: `tests/test_satellite_runtime.py`
- Test: `tests/test_run_scenario_cli.py`

- [ ] **Step 1: 运行全套定向回归**

Run:

```bash
python -m pytest \
  tests/test_mass_search_space_factory.py \
  tests/test_contract_guard.py \
  tests/test_hybrid_codec.py \
  tests/test_scenario_runtime_contract.py \
  tests/test_satellite_runtime.py \
  tests/test_run_scenario_cli.py -q
```

Expected: PASS

- [ ] **Step 2: 运行一条 dry-run 和一条 hybrid proxy smoke**

Run:

```bash
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --dry-run
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus --run-label hybrid_proxy_smoke
```

Expected:

- 第一个命令成功返回 dry-run contract
- 第二个命令产出 `summary.json / report.md / result_index.json`
- 若 `search_space_mode=hybrid`，artifact 中包含新增 observability 字段

- [ ] **Step 3: 更新文档真相**

文档更新要求：

- `HANDOFF.md`
  - 说明 `position_only` 仍为默认稳定主线
  - 说明 `operator_program/hybrid` 已接入但生命周期为 `experimental`
- `README.md`
  - 增加 `search_space_mode` 说明和示例
- `docs/adr/0016-mass-search-space-mode-hybrid-operator-integration.md`
  - ADR 字段至少包含 `status / context / decision / consequences`

- [ ] **Step 4: 提交最终实现**

```bash
git add HANDOFF.md README.md docs/adr/0016-mass-search-space-mode-hybrid-operator-integration.md
git commit -m "docs: record mass hybrid search-space integration"
```

- [ ] **Step 5: 记录人工验证结论**

在最终交付说明中明确写出：

- 哪些 mode 已可执行
- 默认 mode 是什么
- 是否运行了真实 COMSOL
- 若未运行真实 COMSOL，需要明确说明“当前只完成 proxy/runtime 合同接线验证”

## Rollout Gates

只有当以下条件全部满足时，才允许把本次接线视为完成：

- `position_only` 默认行为未回退
- `operator_program` 能走完整 proxy 链并稳定落盘
- `hybrid` 能走完整 proxy 链并稳定落盘
- `contract_guard` 能拦住 mount/aperture/axis-lock 破坏
- artifact 中包含 search-space observability
- `HANDOFF.md -> README.md -> ADR` 已同步

## Deferred Work

以下内容明确不在本次计划的完成定义内：

- 把 `hybrid` 提升为默认稳定主线
- 把 `mount_face / aperture / orientation` 开放成搜索变量
- 多场景 release-grade 结论
- 把所有 operator 效果都宣称为真实 COMSOL fully coupled 验证
