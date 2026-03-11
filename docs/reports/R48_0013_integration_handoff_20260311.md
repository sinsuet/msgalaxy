# R48 0013 Integration Handoff 20260311

## 任务范围

本次只落地 `docs/adr/0013-operator-dsl-v4-and-placement-rule-engine.md` 的最小可执行切片，范围限定为：

- 卫星领域语义算子 `DSL v4`
- `hard_rules` 与 `soft_preferences` 分离的最小 rule engine
- `v4 -> 旧执行层(v3/stub)` 的最小 realization bridge
- `vop_maas` / operator policy 消费链对 `v4` 的最小识别、校验、筛选和审计

本次明确未做：

- `0010` satellite archetype / reference baseline
- `0011` catalog / shell / aperture geometry kernel
- `0012` COMSOL canonical satellite physics
- `0014` iteration review package / teacher demo chain

同时未修改：

- `HANDOFF.md`
- `README.md`
- `core/protocol.py`
- `simulation/` 主体实现

## 实际改动

### 1. DSL v4 schema / validator / normalized payload

主入口在 `optimization/modes/mass/operator_program_v4.py`：

- `validate_operator_program_v4(...)`
- `normalize_operator_program_v4_payload(...)`
- `build_operator_program_v4_payload(...)`
- `OperatorProgramV4`

公共导出入口在 `optimization/modes/mass/__init__.py`：

- `OperatorProgramV4`
- `validate_operator_program_v4`
- `evaluate_operator_rules_v4`
- `realize_operator_program_v4`

当前 `v4` 版本号固定为 `opmaas-r4`，支持的动作集合为：

- `place_on_panel`
- `align_payload_to_aperture`
- `reorient_to_allowed_face`
- `mount_to_bracket_site`
- `move_heat_source_to_radiator_zone`
- `separate_hot_pair`
- `add_heatstrap`
- `add_thermal_pad`
- `add_mount_bracket`
- `rebalance_cg_by_group_shift`
- `shorten_power_bus`
- `protect_fov_keepout`
- `activate_aperture_site`

每个 `v4 action` 目前都可绑定以下目标对象中的一部分：

- `component`
- `component_group`
- `panel`
- `aperture`
- `zone`
- `mount_site`

`normalized payload` 的稳定结构为：

- `program_id`
- `version`
- `rationale`
- `actions[]`
- `metadata`

其中每个 `actions[]` 项固定包含：

- `action`
- `targets[]`
- `params`
- `hard_rules[]`
- `soft_preferences[]`
- `expected_effects`
- `note`
- `metadata`

### 2. v4 与旧 v3 的关系

本次没有删除或重写旧 `DSL v3`。当前关系是：

- `v4` 是新增的语义层，负责表达卫星领域对象绑定、硬规则、软偏好和语义动作。
- `v3` 仍然是现有执行层，负责落到已有的 `OperatorProgram` / `OperatorAction` 执行合同。
- `optimization/modes/mass/operator_realization_v4.py` 负责把 `v4` 编译为 `v3` 或受控 stub。

在 `vop_maas` 里，`validate_vop_policy_pack(...)` 会同时保留：

- `program_v4`: 原始/规范化后的语义程序
- `program`: realization 后的旧执行层程序
- `dsl_version`: 当前候选是否为 `v4`
- `rule_engine_report`
- `realization`

因此当前真实关系是：

- `v4 = semantic contract`
- `v3 = executable kernel`

### 3. rule engine

主入口在 `optimization/modes/mass/operator_rule_engine.py`：

- `evaluate_operator_rules_v4(...)`

当前硬规则 allowlist：

- `shell_aperture_match`
- `mount_site_allowed`
- `allowed_face`
- `collision_free`
- `minimum_clearance`
- `fov_keepout`
- `cg_limit`
- `thermal_boundary`
- `structural_boundary`
- `power_boundary`
- `catalog_interface`

当前软偏好 allowlist：

- `battery_near_structure`
- `payload_on_mission_face`
- `heat_source_to_radiator`
- `adcs_near_cg`
- `short_power_bus`
- `layout_symmetry`
- `serviceability`

当前 rule engine 能检查：

- `hard_rules` / `soft_preferences` 是否分离且字段合法
- action 是否满足声明的必需 target group
- rule id 是否已知
- rule 与 target 类型是否基本兼容
- 可选 `object_catalog` / `binding_catalog` 中的对象是否存在
- `strict=True` 时是否缺少 `hard_rules`

当前 rule engine 不能检查：

- shell / aperture 真实几何关系
- 真实 collision / clearance
- 真实 thermal / structural / power 物理真值
- archetype 级基线语义

### 4. realization bridge

主入口在 `optimization/modes/mass/operator_realization_v4.py`：

- `realize_operator_program_v4(...)`

当前 `v4 -> v3/stub` realization mapping 如下：

- `place_on_panel -> group_move`
- `align_payload_to_aperture -> group_move`
- `reorient_to_allowed_face -> group_move`，必要时退化为 stub bias
- `mount_to_bracket_site -> add_bracket + group_move`
- `move_heat_source_to_radiator_zone -> group_move (+ add_heatstrap when resolvable)`
- `separate_hot_pair -> hot_spread`
- `add_heatstrap -> add_heatstrap`，组件不足时退化为 stub bias
- `add_thermal_pad -> set_thermal_contact`，组件不足时退化为 stub bias
- `add_mount_bracket -> add_bracket`
- `rebalance_cg_by_group_shift -> cg_recenter + group_move`
- `shorten_power_bus -> bus_proximity_opt`
- `protect_fov_keepout -> fov_keepout_push`
- `activate_aperture_site -> group_move`，无可解析主体时退化为 `cg_recenter` bias stub

stub 的当前定位是：

- 不伪装成真实卫星几何/物理实现
- 只保留合同连续性和可审计性
- 在 `strict=True` 时允许被上层拒绝

### 5. vop_maas 消费链接入

`v4` 当前已最小接入以下链路：

- `workflow/modes/vop_maas/policy_context.py`
  - 通过 `binding_catalog_hint` 给 `panel/aperture/zone/mount_site/component_group` 提供轻量绑定提示
- `workflow/modes/vop_maas/contracts.py`
  - `validate_vop_policy_pack(...)` 可校验 `v4`
  - `strict=True` 时会调用 rule engine，并禁止 stub realization 落地
- `workflow/modes/vop_maas/policy_compiler.py`
  - screening 可区分 `semantic_v4` 与 `legacy_v3`
  - 新增 `stubbed_actions / has_stub_realization / realization_status`
- `workflow/modes/vop_maas/policy_program_service.py`
  - round audit / summary / report / hydration 现可读写 `v4` 字段
  - 新增 CSV bool-ish 回填解析，避免字符串 `"False"` 被误判
- `optimization/meta_reasoner.py`
  - 保持对 `program_v4` 结构的兼容消费，不强依赖旧 `program` 形态

## 共享接口/配置/数据合同

### 1. v4 schema 合同

`OperatorProgramV4` 合同：

- `program_id: str`
- `version: str = "opmaas-r4"`
- `rationale: str`
- `actions: List[OperatorActionV4]`
- `metadata: Dict[str, Any]`

`OperatorActionV4` 合同：

- `action`
- `targets`
- `params`
- `hard_rules`
- `soft_preferences`
- `expected_effects`
- `note`
- `metadata`

`TargetBindingV4` 合同：

- `object_type`
- `object_id`
- `role`
- `attributes`

### 2. object catalog / binding catalog 合同

`v4` validator 和 realization 目前都接受轻量 catalog：

- key 为对象类型：`component / component_group / panel / aperture / zone / mount_site`
- value 可为对象 id 列表
- 也可为 `{object_id: {...attrs...}}` 的映射
- 若是映射，允许通过 `component_ids / members / components` 暴露解析后的组件集合

当前 `vop_maas` 通过 `VOPGraph.metadata.binding_catalog_hint` 生成并传递这个合同。

### 3. VOPPolicyPack 候选合同

当候选是 `v4` 时，当前共享字段为：

- `dsl_version = "v4"`
- `program_v4`
- `program`
- `rule_engine_report`
- `realization`

其中 `realization` 当前至少包含：

- `source_version`
- `source_program_id`
- `realized_program_id`
- `action_reports`
- `stubbed_actions`
- `realization_status`

### 4. strict 行为合同

`validate_vop_policy_pack(..., strict=True)` 的当前合同是：

- `evaluate_operator_rules_v4(..., strict=True)` 必须通过
- `realize_operator_program_v4(..., allow_stub=False)` 必须成功
- 如果 realization 有 `stubbed_actions`，候选会被拒绝

`strict=False` 的当前合同是：

- 允许 `v4` 候选经过受控 stub 保持合同连续性
- screening 会对 stub candidate 打分惩罚并暴露审计字段

## 最小验证与结果

本次只运行了 ADR-0013 范围内的最小测试，没有跑全量测试。

### 已执行命令

1. `conda run -n msgalaxy python -m pytest tests/test_operator_program_v4.py tests/test_operator_rule_engine.py tests/test_vop_v4_bridge.py -q`
   - 结果：`16 passed`

2. `conda run -n msgalaxy python -m pytest tests/test_vop_maas_mode.py -k 'mock_policy_run_records_metadata or round_audit_semantic_fields_flow_to_table_digest_and_hydration or boolish_csv_payloads' -q`
   - 结果：`3 passed, 24 deselected`

3. `conda run -n msgalaxy python -m pytest tests/test_vop_v4_bridge.py -k 'strict_mode_rejects_stubbed_realization or penalizes_stubbed_semantic_candidates' -q`
   - 结果：`2 passed, 8 deselected`

4. `conda run -n msgalaxy python -m py_compile workflow/modes/vop_maas/policy_program_service.py`
   - 结果：通过

## 未完成项/风险

1. `v4` rule engine 目前只是合同级治理层，不是几何内核，也不是物理求解器。

2. `shell_aperture_match / allowed_face / fov_keepout / thermal_boundary / structural_boundary / power_boundary` 当前都还不是高保真判定，只是 action-target-rule 的合同检查。

3. realization bridge 里仍有受控 stub，特别是：

- `reorient_to_allowed_face`
- `add_heatstrap`
- `add_thermal_pad`
- `activate_aperture_site`
- 以及任何未来未显式覆盖的 `v4 action`

4. `strict=False` 路径下允许 stub 保留，因此下游必须读取：

- `realization.stubbed_actions`
- `realization.realization_status`
- screening 的 `has_stub_realization`

否则容易把“合同成立但仅 bias/stub”误读成“真实可执行实现”。

5. 当前 object catalog 仍依赖轻量 hint 和适配层，不依赖 `0010/0011` 的 archetype / catalog / shell / aperture 正式本体。

## 对集成测试的建议

1. 增加 `validate_vop_policy_pack(strict=True)` 的 family 分组测试，至少覆盖：

- thermal
- structural
- mission
- cg/power

2. 增加 `binding_catalog_hint -> validate_vop_policy_pack -> screen_policy_pack -> vop_rounds.csv` 的端到端单轮测试，重点锁定：

- `dsl_version`
- `semantic_program_id`
- `stubbed_actions`
- `realization_status`

3. 对 13 个 `v4 action` 做最少一轮 realization 分组回归，不需要逐动作全量场景，但至少要覆盖：

- `panel/aperture`
- `zone`
- `mount_site`
- `component_group`

4. 在后续真正进入 `0011/0012` 集成前，不要把当前 `rule engine + realization` 结果宣传为真实卫星几何/物理校核结论。
