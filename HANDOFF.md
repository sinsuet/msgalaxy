# MsGalaxy HANDOFF

**文档级别**: 核心交接文档（Single Source of Truth）  
**最后更新**: 2026-03-01 02:24  
**当前版本**: v2.0.5  
**状态说明**: 已完成代码修复，尚未执行本轮复测验证

---

## 1. 当前状态

系统继续采用 **COMSOL 动态 STEP 导入** 架构，核心链路（几何导出、物理映射、日志追踪）完整可用。  
本轮聚焦修复 L3/L4 长轮次“不收敛但非求解器发散”的问题，已完成以下实现（待复测）：

- 候选态几何可行性门控（仿真前拒绝不可行几何，避免无效 COMSOL 调用）。
- no-op 识别与跳过（无变化候选不再进入仿真）。
- MOVE 自适应步长回退（1.0/0.5/0.25/0.1/0.05 缩放，优先可行性）。
- Box Selection 多域歧义热源禁绑（避免热功率串域污染）。
- Thermal Contact 参数级联（优先 `h_tc/h_joint/h`，回退 `htot/hconstr/hgap/Rtot`）。
- 几何 Agent 步长策略重写（近阈值改小步，移除“<20mm 禁止”）。

---

## 2. 架构约束（必须遵守）

### 2.1 仿真层
- 仅维护动态路径：STEP 导入 + Box Selection 映射。
- 禁止回退到 static 预置 `model.mph` 路径。
- 仿真失败时不得使用伪数据替代真实物理结果。

### 2.2 运行环境
- 所有 Python 命令必须使用：
  - `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...`
- 禁止直接调用系统 `python` / `pytest`。

### 2.3 约束一致性
- 运行时硬约束统一来源：`runtime_constraints`。
- BOM 约束会覆盖默认配置（包含 `max_cg_offset`）。
- 惩罚分/违规判据/Prompt 注入均使用运行时阈值，不再出现 20mm 与 50mm 逻辑脱节。

---

## 3. 本轮修复（v2.0.5）

### 3.1 非收敛根因结论（来自 L3/L4 日志）
- 热求解链路基本稳定，功率斜坡可反复通过，不是 COMSOL 求解器本体发散。
- 优化停滞主要由“候选态不可行 + 反复拒绝 + no-op 空转”造成。
- 典型表现：CG 指标下降但伴随碰撞/负间隙，候选被拒绝后进入平台期。

### 3.2 编排层修复（`workflow/orchestrator.py`）
- 新增 `_is_geometry_feasible()`：统一几何可行判定（`num_collisions==0` 且 `min_clearance` 达标）。
- 新增 `_state_fingerprint()`：用于检测状态是否真实变化。
- `_execute_plan()` 增加 `execution_meta`：
  - `requested_actions`
  - `requested_targets`
  - `executed_actions`
  - `effective_actions`
  - `state_changed`
- `run_optimization()` 在 `_evaluate_design()` 之前新增两道门：
  - no-op 直接拒绝并跳过仿真。
  - 几何不可行直接拒绝并跳过仿真。
- `MOVE` 改为自适应回退：
  - 在当前方向上试探缩放步长 `[1.0, 0.5, 0.25, 0.1, 0.05]`
  - 选择首个满足可行性的步长
  - 全部失败则回滚该动作并记为 no-op

### 3.3 COMSOL 驱动修复（`simulation/comsol_driver.py`）
- Box Selection 容差维持极严值 `1e-3 mm`。
- 热源绑定增加硬规则：
  - `inside -> intersects -> allvertices` 后若仍命中多域，**拒绝绑定**并跳过该组件热源。
- 新增 `_set_thermal_contact_conductance()` 参数级联：
  - 先试 `h_tc` / `h_joint` / `h`
  - 再试 `EquThinLayer + htot`
  - 再试 `ConstrictionConductance + hconstr/hgap`
  - 最后试 `TotalResistance + Rtot`
- 失败日志改为完整尝试链，便于定位具体 API 分支失效点。

### 3.4 几何 Agent 策略修复（`optimization/agents/geometry_agent.py`）
- 重写质心配平步长建议为阈值感知策略：
  - `exceeds_by > 30mm`: 40-100mm
  - `10mm < exceeds_by <= 30mm`: 15-40mm
  - `0mm < exceeds_by <= 10mm`: 5-15mm
- 删除“禁止 <20mm”的激进约束，防止接近阈值阶段过冲。

---

## 4. 关键文件

- 编排器: `workflow/orchestrator.py`
- COMSOL 驱动: `simulation/comsol_driver.py`
- 几何 Agent: `optimization/agents/geometry_agent.py`
- RAG: `optimization/knowledge/rag_system.py`
- 系统配置: `config/system.yaml`
- 分级 BOM: `config/bom_L1_simple.json` ~ `config/bom_L4_extreme.json`

---

## 5. 标准运行命令（复测时使用）

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L1_simple.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L2_intermediate.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L3_complex.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_L4_extreme.py
```

---

## 6. 当前待办

- 尚未执行本轮 L1/L3/L4 复测（按用户要求暂缓验证）。
- 下一轮应重点核对：
  - 前 3 轮日志中 Box Selection 域命中是否稳定为单域。
  - 是否仍出现 `无法设置接触热导参数` 的整链失败。
  - no-op 是否显著减少，COMSOL 调用次数是否下降。
  - CG 违规是否能在不引入碰撞的前提下持续下降。

---

## 7. 文档同步规则

- 本交接文档使用固定名称 `HANDOFF.md`（全大写）。
- 每次关键修复先更新本文件，再同步 `README.md` 与 `PROJECT_SUMMARY.md`。
- 为保持交付仓库可执行主链路清晰，`scripts/`、`tests/`、`logs/` 默认本地化管理，不纳入 Git 跟踪。
