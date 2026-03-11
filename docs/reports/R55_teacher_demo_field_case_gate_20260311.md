# R55 Teacher Demo Field-Case Gate Checkpoint（2026-03-11）

## 1. 范围

本次只推进 ADR-0014 的下一步最小执行切片：

- 在现有 `IterationReviewPackage` / `field_case_mapping` 审计之上，新增 `teacher_demo` 的可执行 gate；
- 不改 review package 主体结构；
- 不改 COMSOL 求解器；
- 不改 archetype / geometry / DSL 本体；
- `research_fast` 保持原行为，不把 teacher-facing 严格度扩散到轻量检查链。

## 2. 新增的 contract 规则

`teacher_demo` 现在显式声明：

- `field_case_gate.mode = strict_when_linked`
- `allowed_resolution_sources =`
  - `explicit_step_index`
  - `explicit_sequence`
  - `dataset_summary_case_order`

同时要求：

- `ambiguous_binding_count == 0`
- `incompatible_case_count == 0`
- `defaulted_step_count == 0`
- `unmapped_step_count == 0`

因此：

- 显式 step/sequence map 可以进入 `teacher_demo`；
- 带 dataset summary 的稳定 case-order 可以进入 `teacher_demo`；
- 纯目录顺序推断 `dataset_case_order` 不再允许进入 `teacher_demo`；
- `default_case_dir` 单 case 兜底也不再允许进入 `teacher_demo`。

## 3. gate 生效方式

只在“确实启用了 field-case linkage”时生效：

- `field_case_dir`
- `field_case_map`
- dataset root 解析出的 case linkage

若没有 linked field-case 输入，则：

- `teacher_demo.field_case_gate.status = not_applicable`
- 仍允许继续走本地 layout 视图 + lightweight review manifest 路径

若 gate 失败，则：

- 只阻断 `teacher_demo` 的 step package 产出
- `teacher_demo/package_index.json` 仍会写出，并明确：
  - `field_case_gate.status = blocked`
  - `field_case_gate.enforcement_action = skip_profile_packages`
- `research_fast` 继续正常构建

## 4. 典型结果

### 4.1 放行样例

输入：

- `field_case_map.steps[*].field_case_dir`
- 每步显式绑定到对应 case

结果：

- `teacher_demo.field_case_gate.status = passed`
- `teacher_demo.package_count > 0`
- `summary.json / report.md / visualization_summary.txt / Blender sidecar digest` 都会写出 passed 状态

### 4.2 阻断样例一：dataset root 仅靠目录顺序

输入：

- `field_case_dir = <dataset_root>`
- builder 只能按 `dataset_case_order` 推断 step -> case

结果：

- `teacher_demo.field_case_gate.status = blocked`
- 违规原因包含 `disallowed_resolution_source`
- `teacher_demo.package_count = 0`
- `research_fast.package_count` 仍正常

### 4.3 阻断样例二：单 case 目录默认复用到所有 step

输入：

- `field_case_dir = <case_dir>`
- 所有 step 都由 `default_case_dir` 兜底

结果：

- `defaulted_step_count > 0`
- `teacher_demo.field_case_gate.status = blocked`
- 违规原因包含 `defaulted_step_count`

## 5. 改动点

- `visualization/review_package/contracts.py`
  - 新增 `ReviewFieldCaseGateContract`
- `visualization/review_package/registry.py`
  - 给 `teacher_demo` / `research_fast` 注册 field-case gate 策略
- `visualization/review_package/iteration_builder.py`
  - 执行 profile 级 field-case gate
  - gate 失败时只跳过 `teacher_demo` package emission
- `visualization/review_summary_bridge.py`
  - 把 gate 结果纳入 summary digest / report / visualization block
- `tests/test_iteration_review_package.py`
  - 新增 gate pass / blocked(dataset_case_order) / blocked(default_case_dir) 定向测试

## 6. 最小验证

执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
conda run -n msgalaxy python -m pytest tests/test_iteration_review_package.py -q
```

结果：

- `7 passed`

## 7. 仍未兼容 / 仍未做

本次仍然没有做：

- 所有历史脏数据的泛兼容
- `teacher_demo` 自动修复不稳定映射
- 对 `research_fast` 增加同级 strict gate
- review package 主体重构

当前仍会被 `teacher_demo` gate 阻断的旧输入包括：

- 只能按 `dataset_case_order` 目录顺序推断绑定的 dataset
- 只能用 `default_case_dir` 把单 case 复用到全部 step 的输入
- 存在 `ambiguous_binding_count > 0` 的显式/混合绑定
- dataset 内仍含 `incompatible_case_count > 0` 的旧 case 集合
