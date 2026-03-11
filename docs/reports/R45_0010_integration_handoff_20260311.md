# R45 ADR-0010 Integration Handoff

## 任务范围

本次实现严格限定在 `docs/adr/0010-satellite-archetype-and-reference-baseline.md` 的最小可执行切片：

- 只落地 `SatelliteArchetype / MissionClass / MorphologyGrammar / SatelliteLikenessGate` 的最小合同
- 只落地公开参考基线与 `task_type -> archetype` 最小选择入口
- 只落地规则化 `SatelliteLikenessGate` 骨架与运行时最小接线
- 不实现 `0011`
- 不实现 `0012`
- 不改 COMSOL 契约
- 不改 STEP aperture
- 不改 DSL v4
- 不修改 `HANDOFF.md`
- 不修改 `README.md`

## 实际改动

新增模块与数据：

- `domain/satellite/contracts.py`
- `domain/satellite/baseline.py`
- `domain/satellite/selector.py`
- `domain/satellite/gate.py`
- `domain/satellite/runtime.py`
- `domain/satellite/__init__.py`
- `config/satellite_archetypes/public_reference_baseline.json`
- `tests/test_satellite_archetypes.py`

最小运行时接线：

- `workflow/orchestrator.py`
  - 初始化 `DesignState` 后注入 satellite context
  - strict 模式下可因 satellite gate 失败而阻断
  - requirement text 中补充 satellite task/archetype/misson class 线索

- `workflow/modes/mass/pipeline_service.py`
  - 在 `mass` 收尾阶段对 `final_state` 重新执行 satellite gate
  - 把 satellite 上下文与 gate 结果写入 `summary.json`
  - 把 satellite 上下文与 gate 结果写入 `events/run_manifest.json`
  - 补充 gate rule summary、failed rule details、resolution 明细与来源统计

- `core/logger.py`
  - `report.md` 新增 `## Satellite Context`
  - 展示 archetype、baseline、gate 结果、failed rule details、resolution 明细与来源统计

## 共享接口/配置/数据合同

核心数据合同：

- `MissionClass`
  - `navigation`
  - `earth_observation`
  - `communications`
  - `technology_demonstration`
  - `science`

- `MorphologyGrammar`
  - `bus_topology`
  - `bus_aspect_ratio_bounds`
  - `task_face_semantics`
  - `external_appendage_schema`
  - `interior_zone_schema`
  - `attitude_semantics`
  - `allowed_shell_variants`

- `SatelliteArchetype`
  - `archetype_id`
  - `mission_class`
  - `morphology`
  - `default_rule_profile`
  - `public_reference_notes`
  - `reference_boundary`

- `SatelliteReferenceBaseline`
  - `baseline_id`
  - `version`
  - `reference_boundary`
  - `archetypes`

- `SatelliteLayoutCandidate`
  - `archetype_id`
  - `bus_span_mm`
  - `task_face_assignments`
  - `appendages`
  - `interior_zone_assignments`
  - `metadata`

公开参考基线：

- `navigation_satellite`
- `optical_remote_sensing_microsat`
- `radar_or_comm_payload_microsat`
- `cubesat_modular_bus`
- `science_experiment_smallsat`

最小选择入口：

- `TaskTypeArchetypeSelector`
- `select_archetype_for_task(task_type)`

当前 gate 已实现的规则：

- `archetype_match`
- `bus_aspect_ratio_in_bounds`
- `task_faces_present`
- `appendage_templates_in_bounds`
- `interior_zone_assignments_in_bounds`

当前 satellite artifact 字段摘要：

- 上下文主字段
  - `satellite_archetype_id`
  - `satellite_mission_class`
  - `satellite_task_type`
  - `satellite_default_rule_profile`
  - `satellite_reference_baseline_id`
  - `satellite_reference_baseline_version`
  - `satellite_baseline_reference_boundary`
  - `satellite_archetype_reference_boundary`
  - `satellite_public_reference_notes`
  - `satellite_archetype_source`

- gate 主字段
  - `satellite_likeness_gate_mode`
  - `satellite_likeness_gate_passed`
  - `satellite_gate_evaluation_stage`
  - `satellite_likeness_gate_final_warning`
  - `satellite_likeness_gate_final_warning_failed_rules`
  - `satellite_likeness_gate_rule_results`
  - `satellite_gate_total_rule_count`
  - `satellite_gate_passed_rule_count`
  - `satellite_gate_failed_rule_count`
  - `satellite_gate_failed_rule_details`
  - `satellite_likeness_gate_failed_rules`

- 形态与规则摘要
  - `satellite_bus_span_mm`
  - `satellite_bus_aspect_ratio_evaluated`
  - `satellite_bus_aspect_ratio_violations`
  - `satellite_task_face_missing_requirements`
  - `satellite_appendage_template_violations`
  - `satellite_interior_zone_violations`

- 解析来源摘要
  - `satellite_task_face_resolution`
  - `satellite_task_face_resolution_source_counts`
  - `satellite_interior_zone_resolution`
  - `satellite_interior_zone_resolution_source_counts`
  - `satellite_interior_zone_unassigned_components`

- candidate 计数字段
  - `satellite_candidate_task_face_count`
  - `satellite_candidate_appendage_count`
  - `satellite_candidate_interior_zone_assignment_count`
  - `satellite_candidate_interior_zone_resolution_count`
  - `satellite_candidate_interior_zone_unassigned_count`

## 最小验证与结果

仅执行本次范围内的最小验证：

- 命令
  - `conda run -n msgalaxy python -m pytest tests/test_satellite_archetypes.py -q`

- 结果
  - `19 passed in 9.63s`

已覆盖的验证点：

- 5 类 archetype baseline 加载
- `task_type -> archetype` 选择
- gate 正反例
- strict gate 阻断
- `mass` pipeline 将 satellite context 写入 summary / manifest / report
- final-state gate warning 路径
- gate failed rule details 摘要
- task-face / interior-zone resolution 明细与来源统计

## 未完成项/风险

未完成项：

- 没有实现 `0011`
  - catalog/shell/aperture geometry kernel
  - STEP aperture
  - catalog geometry 接线

- 没有实现 `0012`
  - COMSOL canonical satellite physics contract
  - profile/study/dataset/result extractor 改造
  - archetype 到 canonical physics 参数接线

- 没有改 DSL v4

当前风险：

- `SatelliteLikenessGate` 仍是规则化最小骨架，不是 teacher/demo 级几何审校器
- 当前不检查真实壳体外形相似度
- 当前不检查 appendage 展开学 / 干涉
- 当前不检查 FOV / EMC / 遮挡
- 当前不检查 attitude 语义一致性
- 当前不接 canonical thermal / structural / power physics 合同
- final-state gate failure 当前落为 warning/summary，不会在 diagnostic 模式自动升级成 pipeline failure

## 对集成测试的建议

建议后续集成测试至少覆盖以下最小场景：

- 显式 archetype BOM
  - `satellite.archetype_id` 直接指定
  - 验证 summary / manifest / report 一致

- task-type 推断 archetype
  - 无显式 archetype，仅靠 mission/description/组件关键词推断
  - 验证 `satellite_archetype_source=task_type_selector`

- strict gate 阻断
  - 使用越界 appendage
  - 验证初始化阶段抛出 `satellite_likeness_gate_failed:*`

- final-state warning
  - 初始化通过，solver candidate 导致 bus ratio 越界
  - 验证 final-state gate warning 正常写出

- resolution 来源一致性
  - 一部分 task face / zone 由 BOM 指定
  - 一部分由启发式补全
  - 验证 `*_resolution` 与 `*_source_counts` 一致

- artifact reader 回归
  - 如果后续有消费 `summary.json` / `run_manifest.json` 的下游脚本
  - 需要确认新增 satellite 字段不会破坏既有 reader
