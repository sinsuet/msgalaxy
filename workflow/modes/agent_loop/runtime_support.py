"""
Runtime support mixin for agent-loop and shared workflow helpers.
"""
from __future__ import annotations
from typing import Any, Dict, Optional, TYPE_CHECKING
import numpy as np
from core.protocol import DesignState, EvaluationResult
from optimization.protocol import (
    GlobalContextPack,
    GeometryMetrics,
    PowerMetrics,
    StructuralMetrics,
    ThermalMetrics,
    ViolationItem,
)
from simulation.contracts import build_runtime_violations
if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator
class AgentLoopRuntimeSupport:
    """Behavior mixin extracted from orchestrator for runtime decoupling."""
    def _is_cg_violation(self, violation: Any) -> bool:
        """判断违规项是否属于质心偏移违规?"""
        violation_id = str(getattr(violation, "violation_id", ""))
        description = str(getattr(violation, "description", ""))
        return (
            violation_id.startswith("V_CG") or
            ("质心" in description) or
            ("cg" in description.lower())
        )

    def _is_cg_only_violation(self, violations: list[ViolationItem]) -> bool:
        """是否仅剩质心违规（可触发平台期定向策略）?"""
        return bool(violations) and all(self._is_cg_violation(v) for v in violations)

    def _is_cg_plateau(
        self,
        iteration: int,
        current_snapshot: Dict[str, float],
        violations: list[ViolationItem],
        window: int = 4
    ) -> bool:
        """
        检测是否进入“单违规 + 小改进平台期”?

        判据?
        - 仅剩 CG 违规
        - 最?`window` 轮违规数量不?
        - 惩罚分单步改变量整体很小
        - CG 总改善有限（说明陷入局部平台）
        """
        if iteration < window:
            return False
        if not self._is_cg_only_violation(violations):
            return False
        if len(self._snapshot_history) < window:
            return False

        recent = self._snapshot_history[-window:]
        if any(int(r.get("num_violations", -1)) != int(current_snapshot.get("num_violations", -2)) for r in recent):
            return False

        penalty_deltas = []
        for i in range(1, len(recent)):
            penalty_deltas.append(abs(float(recent[i]["penalty_score"]) - float(recent[i - 1]["penalty_score"])))
        if not penalty_deltas:
            return False

        total_cg_gain = float(recent[0]["cg_offset"]) - float(recent[-1]["cg_offset"])
        return (max(penalty_deltas) <= 1.5) and (total_cg_gain <= 2.0)

    def _run_cg_plateau_rescue(
        self,
        current_state: DesignState,
        current_metrics: Dict[str, Any],
        violations: list[ViolationItem],
        iteration: int
    ) -> Optional[tuple[DesignState, Dict[str, Any], list[ViolationItem], Dict[str, float]]]:
        """
        CG 平台期的确定性局部搜索?

        思路?
        - ?CG 主导方向上对重型组件做小步坐标搜索（带几何可行性预检?
        - 先用几何评估挑选最优候选，再做一次真实仿真验?
        """
        if not self._is_cg_only_violation(violations):
            return None

        geom = current_metrics.get("geometry")
        if geom is None:
            return None

        current_cg = float(getattr(geom, "cg_offset_magnitude", 0.0))
        com_offset = [float(x) for x in getattr(geom, "com_offset", [0.0, 0.0, 0.0])]
        axes = [("X", com_offset[0]), ("Y", com_offset[1]), ("Z", com_offset[2])]
        axes.sort(key=lambda x: abs(x[1]), reverse=True)

        if not axes or abs(axes[0][1]) < 1e-6:
            return None

        step_mm = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0]
        heavy_components = sorted(
            current_state.components,
            key=lambda c: float(getattr(c, "mass", 0.0)),
            reverse=True
        )[:6]

        best: Optional[Dict[str, Any]] = None
        min_cg_improvement = 0.2

        self.logger.logger.info(
            f"?触发 CG 平台期救? cg={current_cg:.2f}mm, COM=({com_offset[0]:.2f},{com_offset[1]:.2f},{com_offset[2]:.2f})"
        )

        for axis, axis_value in axes:
            if abs(axis_value) < 1e-6:
                continue
            # ?COM ?+axis 方向时，?-axis 方向移动组件可降低该分量，反之亦然?
            direction = -1.0 if axis_value > 0 else 1.0

            for comp in heavy_components:
                for step in step_mm:
                    delta = direction * step
                    candidate_state = current_state.copy(deep=True)
                    changed = self._execute_single_action(
                        candidate_state,
                        "MOVE",
                        comp.id,
                        {"axis": axis, "range": [delta, delta]}
                    )
                    if not changed:
                        continue

                    feasible, min_clearance, num_collisions = self._is_geometry_feasible(candidate_state)
                    if not feasible:
                        continue

                    candidate_geom = self._evaluate_geometry(candidate_state)
                    candidate_cg = float(candidate_geom.cg_offset_magnitude)
                    cg_improvement = current_cg - candidate_cg
                    if cg_improvement < min_cg_improvement:
                        continue

                    score = (
                        cg_improvement * 10.0 +
                        min(float(getattr(comp, "mass", 0.0)), 20.0) * 0.05 +
                        min(float(min_clearance), 20.0) * 0.01
                    )
                    if best is None or score > float(best["score"]):
                        best = {
                            "state": candidate_state,
                            "component": comp.id,
                            "axis": axis,
                            "delta": delta,
                            "cg_before": current_cg,
                            "cg_after": candidate_cg,
                            "clearance": float(min_clearance),
                            "collisions": int(num_collisions),
                            "score": float(score),
                        }

        if best is None:
            self.logger.logger.info("CG plateau rescue found no feasible candidate; fallback to normal strategy")
            return None

        self.logger.logger.info(
            "  CG rescue candidate: "
            f"{best['component']} {best['axis']} {best['delta']:.2f}mm, "
            f"cg {best['cg_before']:.2f} -> {best['cg_after']:.2f}"
        )

        new_state = best["state"]
        new_metrics, new_violations = self._evaluate_design(new_state, iteration)
        old_penalty = self._calculate_penalty_score(current_metrics, violations)
        new_penalty = self._calculate_penalty_score(new_metrics, new_violations)

        accepted = False
        if len(new_violations) < len(violations):
            accepted = True
        elif len(new_violations) == len(violations):
            if new_penalty <= old_penalty + 1e-6:
                accepted = True
            elif (
                self._is_cg_only_violation(new_violations) and
                new_penalty <= old_penalty + 2.0 and
                float(new_metrics["geometry"].cg_offset_magnitude) < current_cg - 0.5
            ):
                accepted = True

        if not accepted:
            self.logger.logger.warning(
                "?CG 平台期救援候选被拒绝: "
                f"penalty {old_penalty:.2f} -> {new_penalty:.2f}, "
                f"viol {len(violations)} -> {len(new_violations)}"
            )
            return None

        best["cg_after"] = float(new_metrics["geometry"].cg_offset_magnitude)
        return new_state, new_metrics, new_violations, best

    def _check_violations(
        self,
        geometry_metrics: GeometryMetrics,
        thermal_metrics: ThermalMetrics,
        structural_metrics: StructuralMetrics,
        power_metrics: PowerMetrics
    ) -> list[ViolationItem]:
        """检查约束违反（统一契约实现）。"""
        return build_runtime_violations(
            geometry_metrics=geometry_metrics,
            thermal_metrics=thermal_metrics,
            structural_metrics=structural_metrics,
            power_metrics=power_metrics,
            runtime_constraints=dict(self.runtime_constraints or {}),
        )

    def _build_global_context(
        self,
        iteration: int,
        design_state: DesignState,
        metrics: Dict[str, Any],
        violations: list[ViolationItem],
        phase: str = "A",
    ) -> GlobalContextPack:
        """构建全局上下?"""
        # Phase 4: 构建历史摘要和回退警告
        history_summary = f"第{iteration}次迭代"
        if self.rollback_count > 0:
            history_summary += f"（已回退{self.rollback_count}次）"

        # RAG检索相关知?
        context_pack = GlobalContextPack(
            iteration=iteration,
            design_state_summary=(
                f"设计包含{len(design_state.components)}个组件。"
                f"当前硬约束: 温度≤{self.runtime_constraints.get('max_temp_c', 60.0):.2f}°C, "
                f"最小间隙≥{self.runtime_constraints.get('min_clearance_mm', 3.0):.2f}mm, "
                f"质心偏移≤{self.runtime_constraints.get('max_cg_offset_mm', 20.0):.2f}mm, "
                f"安全系数≥{self.runtime_constraints.get('min_safety_factor', 2.0):.2f}, "
                f"模态频率≥{self.runtime_constraints.get('min_modal_freq_hz', 55.0):.2f}Hz, "
                f"压降≤{self.runtime_constraints.get('max_voltage_drop_v', 0.5):.3f}V, "
                f"功率裕度≥{self.runtime_constraints.get('min_power_margin_pct', 10.0):.2f}%"
            ),
            geometry_metrics=metrics["geometry"],
            thermal_metrics=metrics["thermal"],
            structural_metrics=metrics["structural"],
            power_metrics=metrics["power"],
            violations=violations,
            history_summary=history_summary
        )

        # Phase 4: 添加失败记录和回退警告
        if hasattr(context_pack, 'recent_failures'):
            context_pack.recent_failures = self.recent_failures.copy()
        if self.rollback_count > 0 and self.recent_failures:
            rollback_warning = (
                f"系统已回退{self.rollback_count}次！"
                f"最近失? {self.recent_failures[-1]}"
            )
            if hasattr(context_pack, 'rollback_warning'):
                context_pack.rollback_warning = rollback_warning

        # 检索知?
        phase_norm = str(phase or "A").strip().upper()
        retrieved_knowledge = self.rag_system.retrieve(
            context_pack,
            top_k=4 if phase_norm == "D" else 3,
            phase=phase_norm,
        )
        context_pack.retrieved_knowledge = retrieved_knowledge

        return context_pack

    def _inject_runtime_constraints_to_plan(self, strategic_plan) -> None:
        """
        将运行时硬约束注入到 StrategicPlan 的任务中，避?Agent 使用过期阈值?
        """
        if not strategic_plan or not getattr(strategic_plan, "tasks", None):
            return

        limits = {
            "max_temp_c": float(self.runtime_constraints.get("max_temp_c", 60.0)),
            "min_clearance_mm": float(self.runtime_constraints.get("min_clearance_mm", 3.0)),
            "max_cg_offset_mm": float(self.runtime_constraints.get("max_cg_offset_mm", 20.0)),
            "min_safety_factor": float(self.runtime_constraints.get("min_safety_factor", 2.0)),
            "min_modal_freq_hz": float(self.runtime_constraints.get("min_modal_freq_hz", 55.0)),
            "max_voltage_drop_v": float(self.runtime_constraints.get("max_voltage_drop_v", 0.5)),
            "min_power_margin_pct": float(self.runtime_constraints.get("min_power_margin_pct", 10.0)),
            "max_power_w": float(self.runtime_constraints.get("max_power_w", 500.0)),
            "bus_voltage_v": float(self.runtime_constraints.get("bus_voltage_v", 28.0)),
            "enforce_power_budget": self._to_bool(
                self.runtime_constraints.get("enforce_power_budget", False),
                default=False,
            ),
        }
        optional_budget_text = ""
        if limits["enforce_power_budget"]:
            optional_budget_text = f", peak_power<= {limits['max_power_w']:.2f}W"
        hard_constraint_text = (
            "硬约?必须满足): "
            f"max_temp<= {limits['max_temp_c']:.2f}°C, "
            f"min_clearance>= {limits['min_clearance_mm']:.2f}mm, "
            f"cg_offset<= {limits['max_cg_offset_mm']:.2f}mm, "
            f"safety_factor>= {limits['min_safety_factor']:.2f}, "
            f"modal_freq>= {limits['min_modal_freq_hz']:.2f}Hz, "
            f"voltage_drop<= {limits['max_voltage_drop_v']:.3f}V, "
            f"power_margin>= {limits['min_power_margin_pct']:.2f}%"
            f"{optional_budget_text}"
        )

        for task in strategic_plan.tasks:
            if not isinstance(task.context, dict):
                task.context = {}
            task.context.setdefault("constraint_limits", limits.copy())
            task.context.setdefault("max_temp_limit_c", limits["max_temp_c"])
            task.context.setdefault("min_clearance_limit_mm", limits["min_clearance_mm"])
            task.context.setdefault("max_cg_offset_limit_mm", limits["max_cg_offset_mm"])
            task.context.setdefault("min_safety_factor", limits["min_safety_factor"])
            task.context.setdefault("min_modal_freq_hz", limits["min_modal_freq_hz"])
            task.context.setdefault("max_voltage_drop_v", limits["max_voltage_drop_v"])
            task.context.setdefault("min_power_margin_pct", limits["min_power_margin_pct"])
            task.context.setdefault("max_power_w", limits["max_power_w"])
            task.context.setdefault("bus_voltage_v", limits["bus_voltage_v"])
            task.context.setdefault("enforce_power_budget", limits["enforce_power_budget"])

            if hard_constraint_text not in task.constraints:
                task.constraints.append(hard_constraint_text)

    def _execute_plan(self, execution_plan, current_state: DesignState) -> DesignState:
        """
        执行优化计划

        支持的操作：
        - MOVE: 移动组件
        - ROTATE: 旋转组件
        - SWAP: 交换组件位置
        - DEFORM: FFD自由变形
        - REPACK: 重新装箱

        Args:
            execution_plan: 执行计划（包含多个Agent的提案）
            current_state: 当前设计状?

        Returns:
            新的设计状?
        """
        import copy

        # 深拷贝当前状?
        new_state = copy.deepcopy(current_state)
        start_fingerprint = self._state_fingerprint(current_state)
        requested_targets = 0
        executed_actions = 0
        effective_actions = 0

        # 如果execution_plan为空，直接返?
        if not execution_plan:
            self.logger.logger.warning("执行计划为空")
            new_state.metadata = dict(new_state.metadata or {})
            new_state.metadata["execution_meta"] = {
                "requested_actions": 0,
                "requested_targets": 0,
                "executed_actions": 0,
                "effective_actions": 0,
                "state_changed": False,
            }
            return new_state

        # 收集所有需要执行的操作（来?geometry_proposal ?thermal_proposal?
        all_actions = []

        # 提取几何操作
        geometry_proposal = getattr(execution_plan, 'geometry_proposal', None)
        if geometry_proposal and hasattr(geometry_proposal, 'actions') and geometry_proposal.actions:
            self.logger.logger.info(f"  📐 几何提案包含 {len(geometry_proposal.actions)} 个操作")
            all_actions.extend(geometry_proposal.actions)

        # 提取热学操作（保持 thermal_proposal 数据流）
        thermal_proposal = getattr(execution_plan, 'thermal_proposal', None)
        if thermal_proposal and hasattr(thermal_proposal, 'actions') and thermal_proposal.actions:
            self.logger.logger.info(f"  🔥 热学提案包含 {len(thermal_proposal.actions)} 个操作")
            all_actions.extend(thermal_proposal.actions)

        if not all_actions:
            self.logger.logger.info("无操作需要执行")
            new_state.metadata = dict(new_state.metadata or {})
            new_state.metadata["execution_meta"] = {
                "requested_actions": 0,
                "requested_targets": 0,
                "executed_actions": 0,
                "effective_actions": 0,
                "state_changed": False,
            }
            return new_state

        self.logger.logger.info(f"  📋 总计 {len(all_actions)} 个操作待执行")

        # 执行每个操作
        for action in all_actions:
            try:
                op_type = action.op_type
                parameters = getattr(action, 'parameters', {}) or {}

                # 获取目标组件（支?component_id ?target_components?
                component_id = getattr(action, 'component_id', None)
                target_components = getattr(action, 'target_components', None)

                # 如果是批量操作（target_components），对每个组件执?
                if target_components and isinstance(target_components, list):
                    self.logger.logger.info(f"  执行批量操作: {op_type} on {len(target_components)} 个组件")
                    requested_targets += len(target_components)
                    for target_comp_id in target_components:
                        changed = self._execute_single_action(
                            new_state, op_type, target_comp_id, parameters
                        )
                        executed_actions += 1
                        if changed:
                            effective_actions += 1
                elif component_id:
                    self.logger.logger.info(f"  执行操作: {op_type} on {component_id}")
                    requested_targets += 1
                    changed = self._execute_single_action(
                        new_state, op_type, component_id, parameters
                    )
                    executed_actions += 1
                    if changed:
                        effective_actions += 1
                else:
                    self.logger.logger.warning(f"  操作 {op_type} 缺少目标组件，跳过")

            except Exception as e:
                self.logger.logger.error(f"  执行操作失败: {e}", exc_info=True)
                continue

        state_changed = self._state_fingerprint(new_state) != start_fingerprint
        new_state.metadata = dict(new_state.metadata or {})
        new_state.metadata["execution_meta"] = {
            "requested_actions": len(all_actions),
            "requested_targets": requested_targets,
            "executed_actions": executed_actions,
            "effective_actions": effective_actions,
            "state_changed": state_changed,
        }
        if not state_changed:
            self.logger.logger.warning(
                "  执行完成但状态未发生变化（no-op），后续将跳过候选态仿真评估"
            )

        # 更新迭代次数
        new_state.iteration = current_state.iteration + 1

        return new_state

    def _execute_single_action(
        self,
        new_state: DesignState,
        op_type: str,
        component_id: str,
        parameters: dict
    ) -> bool:
        """
        执行单个操作（内部方法）

        Args:
            new_state: 设计状态（会被修改?
            op_type: 操作类型
            component_id: 目标组件ID
            parameters: 操作参数
        """
        from geometry.ffd import FFDDeformer
        import numpy as np

        # 查找目标组件
        comp_idx = None
        for idx, comp in enumerate(new_state.components):
            if comp.id == component_id:
                comp_idx = idx
                break

        if comp_idx is None:
            self.logger.logger.warning(f"    组件 {component_id} 未找到，跳过")
            return False

        # 记录操作前的状态（强力日志追踪?
        old_pos = [
            new_state.components[comp_idx].position.x,
            new_state.components[comp_idx].position.y,
            new_state.components[comp_idx].position.z
        ]
        old_dims = [
            new_state.components[comp_idx].dimensions.x,
            new_state.components[comp_idx].dimensions.y,
            new_state.components[comp_idx].dimensions.z
        ]
        old_rot = [
            new_state.components[comp_idx].rotation.x,
            new_state.components[comp_idx].rotation.y,
            new_state.components[comp_idx].rotation.z,
        ]

        def _component_fp(comp_obj) -> tuple:
            thermal_contacts = tuple(
                sorted(
                    (str(k), round(float(v), 6))
                    for k, v in (getattr(comp_obj, "thermal_contacts", {}) or {}).items()
                )
            )
            heatsink = tuple(
                sorted((str(k), str(v)) for k, v in (getattr(comp_obj, "heatsink", {}) or {}).items())
            )
            bracket = tuple(
                sorted((str(k), str(v)) for k, v in (getattr(comp_obj, "bracket", {}) or {}).items())
            )
            return (
                round(float(comp_obj.position.x), 6),
                round(float(comp_obj.position.y), 6),
                round(float(comp_obj.position.z), 6),
                round(float(comp_obj.dimensions.x), 6),
                round(float(comp_obj.dimensions.y), 6),
                round(float(comp_obj.dimensions.z), 6),
                round(float(comp_obj.rotation.x), 6),
                round(float(comp_obj.rotation.y), 6),
                round(float(comp_obj.rotation.z), 6),
                str(getattr(comp_obj, "envelope_type", "box")),
                round(float(getattr(comp_obj, "emissivity", 0.8)), 6),
                round(float(getattr(comp_obj, "absorptivity", 0.3)), 6),
                str(getattr(comp_obj, "coating_type", "default")),
                thermal_contacts,
                heatsink,
                bracket,
            )

        old_comp_fp = _component_fp(new_state.components[comp_idx])

        # 执行不同类型的操?
        if op_type == "MOVE":
            # 移动组件
            axis = str(parameters.get("axis", "X")).upper()
            move_range = parameters.get("range", [0, 0])
            if isinstance(move_range, (list, tuple)) and len(move_range) >= 2:
                delta = (float(move_range[0]) + float(move_range[1])) / 2.0
            elif isinstance(move_range, (int, float)):
                delta = float(move_range)
            else:
                delta = float(parameters.get("delta", 0.0))

            if axis not in {"X", "Y", "Z"}:
                self.logger.logger.warning(f"    MOVE 轴非法: {axis}，跳过")
                return False

            if abs(delta) < 1e-9:
                self.logger.logger.info("    MOVE 位移为 0，跳过")
                return False

            # 自适应缩放：优先尝试全步长，不可行时逐级回退
            # 目标：避免大步长 MOVE 把候选态直接推入碰?间隙违规区?
            scales = [1.0, 0.5, 0.25, 0.1, 0.05]
            clearance_limit = float(self.runtime_constraints.get("min_clearance_mm", 3.0))
            comp_ref = new_state.components[comp_idx]
            if axis == "X":
                original_value = float(comp_ref.position.x)
            elif axis == "Y":
                original_value = float(comp_ref.position.y)
            else:
                original_value = float(comp_ref.position.z)

            accepted_scale = None
            accepted_delta = 0.0
            last_probe = None

            for scale in scales:
                candidate_delta = delta * scale
                candidate_value = original_value + candidate_delta
                if axis == "X":
                    comp_ref.position.x = candidate_value
                elif axis == "Y":
                    comp_ref.position.y = candidate_value
                else:
                    comp_ref.position.z = candidate_value

                min_clearance, num_collisions = self._calculate_pairwise_clearance(new_state)
                last_probe = (scale, candidate_delta, min_clearance, num_collisions)
                is_feasible = (
                    num_collisions == 0 and
                    min_clearance >= (clearance_limit - 1e-6)
                )
                if is_feasible:
                    accepted_scale = scale
                    accepted_delta = candidate_delta
                    break

            if accepted_scale is None:
                # 全部步长不可行，回滚位置并标?no-op
                if axis == "X":
                    comp_ref.position.x = original_value
                elif axis == "Y":
                    comp_ref.position.y = original_value
                else:
                    comp_ref.position.z = original_value

                if last_probe:
                    _, _, probe_clearance, probe_collisions = last_probe
                    self.logger.logger.warning(
                        "    ?MOVE 被几何门控拒? 所有缩放步长均不可?"
                        f"(最后探?min_clearance={probe_clearance:.2f}mm, "
                        f"collisions={probe_collisions})"
                    )
                else:
                    self.logger.logger.warning("    MOVE 被几何门控拒绝: 未找到可行步长")
                return False

            self.logger.logger.info(
                f"    MOVE 自适应应用: {axis} ?{accepted_delta:.2f} mm "
                f"(原始 {delta:.2f} mm, scale={accepted_scale:.2f})"
            )

        elif op_type == "ROTATE":
            # 旋转组件
            axis = parameters.get("axis", "Z")
            angle_range = parameters.get("angle_range", [0, 0])
            angle = (angle_range[0] + angle_range[1]) / 2.0

            if axis == "X":
                new_state.components[comp_idx].rotation.x += angle
            elif axis == "Y":
                new_state.components[comp_idx].rotation.y += angle
            elif axis == "Z":
                new_state.components[comp_idx].rotation.z += angle

            self.logger.logger.info(f"    旋转 {axis} 轴 {angle:.2f}°")

        elif op_type == "SWAP":
            # 交换两个组件的位?
            component_b = parameters.get("component_b")
            comp_b_idx = None
            for idx, comp in enumerate(new_state.components):
                if comp.id == component_b:
                    comp_b_idx = idx
                    break

            if comp_b_idx is not None:
                # 交换位置
                pos_a = new_state.components[comp_idx].position
                pos_b = new_state.components[comp_b_idx].position
                new_state.components[comp_idx].position = pos_b
                new_state.components[comp_b_idx].position = pos_a
                self.logger.logger.info(f"    交换 {component_id} 与 {component_b} 的位置")
            else:
                self.logger.logger.warning(f"    组件 {component_b} 未找到，跳过交换")

        elif op_type == "DEFORM":
            # FFD自由变形
            deform_type = parameters.get("deform_type", "stretch_z")
            magnitude = parameters.get("magnitude", 10.0)

            self.logger.logger.info(f"    FFD变形: {deform_type}, 幅度 {magnitude:.2f} mm")

            # 获取组件的包围盒
            comp = new_state.components[comp_idx]
            pos = comp.position
            dim = comp.dimensions

            # 计算包围?
            bbox_min = np.array([
                pos.x - dim.x / 2,
                pos.y - dim.y / 2,
                pos.z - dim.z / 2
            ])
            bbox_max = np.array([
                pos.x + dim.x / 2,
                pos.y + dim.y / 2,
                pos.z + dim.z / 2
            ])

            # 创建FFD变形?
            ffd = FFDDeformer(nx=3, ny=3, nz=3)
            lattice = ffd.create_lattice(bbox_min, bbox_max, margin=0.1)

            # 根据变形类型设置控制点位?
            displacements = {}

            if deform_type == "stretch_x":
                # 沿X轴拉伸：移动右侧控制?
                for j in range(3):
                    for k in range(3):
                        displacements[(2, j, k)] = np.array([magnitude, 0, 0])
                # 更新组件尺寸
                new_state.components[comp_idx].dimensions.x += magnitude

            elif deform_type == "stretch_y":
                # 沿Y轴拉?
                for i in range(3):
                    for k in range(3):
                        displacements[(i, 2, k)] = np.array([0, magnitude, 0])
                new_state.components[comp_idx].dimensions.y += magnitude

            elif deform_type == "stretch_z":
                # 沿Z轴拉?
                for i in range(3):
                    for j in range(3):
                        displacements[(i, j, 2)] = np.array([0, 0, magnitude])
                new_state.components[comp_idx].dimensions.z += magnitude

            elif deform_type == "bulge":
                # 膨胀：所有外侧控制点向外移动
                scale = magnitude / 2.0
                for i in range(3):
                    for j in range(3):
                        for k in range(3):
                            if i == 0 or i == 2 or j == 0 or j == 2 or k == 0 or k == 2:
                                # 外侧控制?
                                direction = np.array([
                                    (i - 1) * scale,
                                    (j - 1) * scale,
                                    (k - 1) * scale
                                ])
                                displacements[(i, j, k)] = direction
                # 膨胀会增加所有维?
                new_state.components[comp_idx].dimensions.x += magnitude * 0.5
                new_state.components[comp_idx].dimensions.y += magnitude * 0.5
                new_state.components[comp_idx].dimensions.z += magnitude * 0.5

            self.logger.logger.info(f"    ?FFD变形完成，新尺寸: {new_state.components[comp_idx].dimensions}")

        elif op_type == "REPACK":
            # 重新装箱
            strategy = parameters.get("strategy", "greedy")
            clearance = parameters.get(
                "clearance",
                self.config.get("geometry", {}).get("clearance_mm", 5.0)
            )

            self.logger.logger.info(f"    重新装箱: strategy={strategy}, clearance={clearance}")

            # 调用layout_engine重新布局
            # 注意：这会重置所有组件位?
            packing_result = self.layout_engine.generate_layout()

            # 更新组件位置
            for part in packing_result.placed:
                pos_min = part.get_actual_position()
                dims = np.array([float(part.dims[0]), float(part.dims[1]), float(part.dims[2])], dtype=float)
                center_pos = pos_min + dims / 2.0
                for idx, comp in enumerate(new_state.components):
                    if comp.id == part.id:
                        new_state.components[idx].position = Vector3D(
                            x=float(center_pos[0]),
                            y=float(center_pos[1]),
                            z=float(center_pos[2])
                        )
                        break

            self.logger.logger.info(f"    ?重新装箱完成")

        # === 热学算子 ===
        elif op_type == "MODIFY_COATING":
            # 修改组件涂层（表面发射率/吸收率）
            emissivity = parameters.get("emissivity", 0.85)
            absorptivity = parameters.get("absorptivity", 0.3)
            coating_type = parameters.get("coating_type", "high_emissivity")

            new_state.components[comp_idx].emissivity = emissivity
            new_state.components[comp_idx].absorptivity = absorptivity
            new_state.components[comp_idx].coating_type = coating_type

            self.logger.logger.info(
                f"    🎨 涂层修改: {component_id} ε={emissivity}, α={absorptivity}, type={coating_type}"
            )

        elif op_type == "SET_THERMAL_CONTACT":
            # 设置接触热阻
            contact_component = parameters.get("contact_component")
            conductance = parameters.get("conductance", 1000.0)  # W/m²·K
            gap = parameters.get("gap", 0.0)  # mm

            if contact_component:
                # 初始?thermal_contacts 字典（如果不存在?
                if not hasattr(new_state.components[comp_idx], 'thermal_contacts') or \
                   new_state.components[comp_idx].thermal_contacts is None:
                    new_state.components[comp_idx].thermal_contacts = {}

                new_state.components[comp_idx].thermal_contacts[contact_component] = conductance

                self.logger.logger.info(
                    f"    🔗 接触热阻: {component_id} ?{contact_component}, "
                    f"h={conductance} W/m²·K, gap={gap}mm"
                )
            else:
                self.logger.logger.warning(f"    SET_THERMAL_CONTACT 缺少 contact_component 参数")

        elif op_type == "ADD_HEATSINK":
            # 添加散热器（记录到组件属性，实际几何?CAD 导出时生成）
            face = parameters.get("face", "+Y")
            thickness = parameters.get("thickness", 2.0)  # mm
            conductivity = parameters.get("conductivity", 400.0)  # W/m·K (?

            new_state.components[comp_idx].heatsink = {
                "face": face,
                "thickness": thickness,
                "conductivity": conductivity
            }

            self.logger.logger.info(
                f"    🧊 散热器添? {component_id} face={face}, thickness={thickness}mm, k={conductivity} W/m·K"
            )

        elif op_type == "ADD_BRACKET":
            # 添加结构支架（记录到组件属性，实际几何?CAD 导出时生成）
            height = parameters.get("height", 20.0)  # mm
            material = parameters.get("material", "aluminum")
            attach_face = parameters.get("attach_face", "-Z")

            new_state.components[comp_idx].bracket = {
                "height": height,
                "material": material,
                "attach_face": attach_face
            }

            # 支架会改变组件的有效Z位置（如果是底部支架?
            if attach_face == "-Z":
                new_state.components[comp_idx].position.z += height / 2.0
                self.logger.logger.info(
                    f"    🔩 支架添加: {component_id} height={height}mm, 组件Z位置上移 {height/2.0}mm"
                )
            else:
                self.logger.logger.info(
                    f"    🔩 支架添加: {component_id} height={height}mm, face={attach_face}"
                )

        elif op_type == "ALIGN":
            # 对齐组件（沿指定轴对齐到参考组件）
            axis = parameters.get("axis", "X")
            reference_component = parameters.get("reference_component")

            if reference_component:
                # 查找参考组?
                ref_idx = None
                for idx, comp in enumerate(new_state.components):
                    if comp.id == reference_component:
                        ref_idx = idx
                        break

                if ref_idx is not None:
                    ref_pos = new_state.components[ref_idx].position
                    if axis == "X":
                        new_state.components[comp_idx].position.x = ref_pos.x
                    elif axis == "Y":
                        new_state.components[comp_idx].position.y = ref_pos.y
                    elif axis == "Z":
                        new_state.components[comp_idx].position.z = ref_pos.z

                    self.logger.logger.info(
                        f"    📐 对齐: {component_id} ?{axis} 轴对齐到 {reference_component}"
                    )
                else:
                    self.logger.logger.warning(f"    参考组件 {reference_component} 未找到")
            else:
                self.logger.logger.warning(f"    ALIGN 缺少 reference_component 参数")

        elif op_type == "CHANGE_ENVELOPE":
            # 包络切换（Box ?Cylinder 等）
            # 这个操作修改组件的包络类型，CAD 导出时会生成对应几何
            shape = parameters.get("shape", "box")
            dimensions = parameters.get("dimensions", {})

            # 更新组件的包络类?
            new_state.components[comp_idx].envelope_type = shape

            # 如果提供了新尺寸，更新组件尺?
            if dimensions:
                if "x" in dimensions:
                    new_state.components[comp_idx].dimensions.x = dimensions["x"]
                if "y" in dimensions:
                    new_state.components[comp_idx].dimensions.y = dimensions["y"]
                if "z" in dimensions:
                    new_state.components[comp_idx].dimensions.z = dimensions["z"]
                # 圆柱体特殊参?
                if "radius" in dimensions:
                    # 圆柱体：X/Y 设为直径
                    diameter = dimensions["radius"] * 2
                    new_state.components[comp_idx].dimensions.x = diameter
                    new_state.components[comp_idx].dimensions.y = diameter
                if "height" in dimensions:
                    new_state.components[comp_idx].dimensions.z = dimensions["height"]

            self.logger.logger.info(
                f"    📦 包络切换: {component_id} ?{shape}"
            )

        else:
            self.logger.logger.warning(f"    未知操作类型: {op_type}")

        # 记录操作后的状态（强力日志追踪?
        new_pos = [
            new_state.components[comp_idx].position.x,
            new_state.components[comp_idx].position.y,
            new_state.components[comp_idx].position.z
        ]
        new_dims = [
            new_state.components[comp_idx].dimensions.x,
            new_state.components[comp_idx].dimensions.y,
            new_state.components[comp_idx].dimensions.z
        ]
        new_rot = [
            new_state.components[comp_idx].rotation.x,
            new_state.components[comp_idx].rotation.y,
            new_state.components[comp_idx].rotation.z,
        ]
        if old_pos != new_pos:
            self.logger.logger.info(
                f"    📍 {component_id} 坐标变化: "
                f"[{old_pos[0]:.2f}, {old_pos[1]:.2f}, {old_pos[2]:.2f}] ?"
                f"[{new_pos[0]:.2f}, {new_pos[1]:.2f}, {new_pos[2]:.2f}]"
            )
        if old_dims != new_dims:
            self.logger.logger.info(
                f"    📐 {component_id} 尺寸变化: "
                f"[{old_dims[0]:.2f}, {old_dims[1]:.2f}, {old_dims[2]:.2f}] ?"
                f"[{new_dims[0]:.2f}, {new_dims[1]:.2f}, {new_dims[2]:.2f}]"
            )
        if old_rot != new_rot:
            self.logger.logger.info(
                f"    🔄 {component_id} 旋转变化: "
                f"[{old_rot[0]:.2f}, {old_rot[1]:.2f}, {old_rot[2]:.2f}] ?"
                f"[{new_rot[0]:.2f}, {new_rot[1]:.2f}, {new_rot[2]:.2f}]"
            )

        new_comp_fp = _component_fp(new_state.components[comp_idx])
        return bool(new_comp_fp != old_comp_fp)

    def _should_accept(
        self,
        old_metrics: Dict[str, Any],
        new_metrics: Dict[str, Any],
        old_violations: list,
        new_violations: list,
        allow_penalty_regression: float = 0.0,
        require_cg_improve_on_regression: bool = False
    ) -> bool:
        """
        判断是否接受新状态（违规数量 + 惩罚分双判据）?

        allow_penalty_regression:
            允许在平台期接受“小幅惩罚上升”的候选，用于穿越局部最优?
        require_cg_improve_on_regression:
            当发生惩罚上升时，要?CG 必须实质改善，避免无效放宽?
        """
        old_count = len(old_violations)
        new_count = len(new_violations)

        # 一级判据：违规数量必须不增?
        if new_count < old_count:
            return True
        if new_count > old_count:
            return False

        # 二级判据：违规数量相同时，惩罚分不能恶化
        old_penalty = self._calculate_penalty_score(old_metrics, old_violations)
        new_penalty = self._calculate_penalty_score(new_metrics, new_violations)
        tolerance = max(float(allow_penalty_regression), 0.0)
        if new_penalty <= old_penalty + max(1e-6, tolerance):
            # 平台期放宽仅在“确实换?CG 改善”时才生?
            if (
                tolerance > 1e-9 and
                new_penalty > old_penalty + 1e-6 and
                require_cg_improve_on_regression
            ):
                old_cg = float(old_metrics["geometry"].cg_offset_magnitude)
                new_cg = float(new_metrics["geometry"].cg_offset_magnitude)
                if new_cg >= old_cg - 0.5:
                    self.logger.logger.info(
                        "  拒绝新状? 虽在放宽窗口内，?CG 改善不足 "
                        f"({old_cg:.2f} -> {new_cg:.2f})"
                    )
                    return False

                self.logger.logger.info(
                    "  平台期受控接? 允许小幅惩罚上升以换?CG 改善 "
                    f"(penalty {old_penalty:.2f} -> {new_penalty:.2f}, "
                    f"cg {old_cg:.2f} -> {new_cg:.2f})"
                )
            return True

        self.logger.logger.info(
            "  拒绝新状? 违规数未减少且惩罚分恶化 "
            f"({old_penalty:.2f} -> {new_penalty:.2f}, "
            f"tolerance={tolerance:.2f})"
        )
        return False

    def _learn_from_iteration(
        self,
        iteration: int,
        strategic_plan,
        execution_plan,
        old_metrics: Dict[str, Any],
        new_metrics: Dict[str, Any],
        success: bool
    ):
        """从迭代中学习"""
        # 计算指标改进
        improvements = {}
        if "thermal" in old_metrics and "thermal" in new_metrics:
            old_temp = old_metrics["thermal"].max_temp
            new_temp = new_metrics["thermal"].max_temp
            improvements["max_temp"] = new_temp - old_temp

        # 添加到知识库
        self.rag_system.add_case_from_iteration(
            iteration=iteration,
            problem=strategic_plan.reasoning[:100],
            solution=strategic_plan.strategy_description,
            success=success,
            metrics_improvement=improvements
        )

    def _generate_final_report(self, final_state: DesignState, iterations: int):
        """生成最终报?"""
        self.logger.logger.info(f"\n{'='*60}")
        self.logger.logger.info("Optimization Complete")
        self.logger.logger.info(f"{'='*60}")
        self.logger.logger.info(f"Total iterations: {iterations}")
        self.logger.logger.info(f"Final design: {len(final_state.components)} components")
        self.logger.logger.info(f"Total rollbacks: {self.rollback_count}")  # Phase 4: 记录回退次数

        # 生成可视?
        if self.config.get('logging', {}).get('save_visualizations', True):
            try:
                from core.visualization import generate_visualizations
                generate_visualizations(self.logger.run_dir)
                self.logger.logger.info("?Visualizations generated")
            except Exception as e:
                self.logger.logger.warning(f"Visualization generation failed: {e}")

    # ============ Phase 4: 回退机制辅助方法 ============

    def _calculate_penalty_breakdown(
        self,
        metrics: Dict[str, Any],
        violations: list[ViolationItem]
    ) -> Dict[str, float]:
        """
        计算惩罚分分项（越低越好?

        Args:
            metrics: 性能指标
            violations: 违规列表

        Returns:
            惩罚分分项与总分
        """
        penalty_violation = 0.0
        penalty_temp = 0.0
        penalty_clearance = 0.0
        penalty_cg = 0.0
        penalty_collision = 0.0
        max_temp_limit = self.runtime_constraints.get("max_temp_c", 60.0)
        min_clearance_limit = self.runtime_constraints.get("min_clearance_mm", 3.0)
        max_cg_offset_limit = self.runtime_constraints.get("max_cg_offset_mm", 20.0)

        # 违规惩罚（每个违?+100?
        penalty_violation += len(violations) * 100.0

        # 温度惩罚
        max_temp = metrics.get('thermal').max_temp
        if max_temp > max_temp_limit:
            penalty_temp += (max_temp - max_temp_limit) * 10.0

        # 间隙惩罚
        min_clearance = metrics.get('geometry').min_clearance
        if min_clearance < min_clearance_limit:
            penalty_clearance += (min_clearance_limit - min_clearance) * 50.0

        # 质心偏移惩罚（与违规阈值一致）
        cg_offset = metrics.get('geometry').cg_offset_magnitude
        if cg_offset > max_cg_offset_limit:
            penalty_cg += (cg_offset - max_cg_offset_limit) * 2.0

        # 碰撞惩罚（强惩罚，显式驱动远离重叠态）
        num_collisions = metrics.get('geometry').num_collisions
        if num_collisions > 0:
            penalty_collision += num_collisions * 500.0

        total = penalty_violation + penalty_temp + penalty_clearance + penalty_cg + penalty_collision
        return {
            "violation": penalty_violation,
            "temp": penalty_temp,
            "clearance": penalty_clearance,
            "cg": penalty_cg,
            "collision": penalty_collision,
            "total": total,
        }

    def _calculate_penalty_score(
        self,
        metrics: Dict[str, Any],
        violations: list[ViolationItem]
    ) -> float:
        """计算惩罚分总分（向后兼容）"""
        return self._calculate_penalty_breakdown(metrics, violations)["total"]

    def _compute_effectiveness_score(
        self,
        previous: Optional[Dict[str, float]],
        current: Dict[str, float]
    ) -> float:
        """
        计算单轮迭代有效性分数（-100 ~ 100，越高越好）?

        分数由惩罚分改善、违规数量改善、以及关键连续指标改善共同决定?
        """
        if not previous:
            return 0.0

        prev_penalty = float(previous.get("penalty_score", 0.0))
        curr_penalty = float(current.get("penalty_score", 0.0))

        prev_cg = float(previous.get("cg_offset", 0.0))
        curr_cg = float(current.get("cg_offset", 0.0))

        prev_temp = float(previous.get("max_temp", 0.0))
        curr_temp = float(current.get("max_temp", 0.0))

        prev_clearance = float(previous.get("min_clearance", 0.0))
        curr_clearance = float(current.get("min_clearance", 0.0))

        prev_violations = float(previous.get("num_violations", 0.0))
        curr_violations = float(current.get("num_violations", 0.0))

        max_temp_limit = max(float(self.runtime_constraints.get("max_temp_c", 60.0)), 1.0)
        min_clearance_limit = max(float(self.runtime_constraints.get("min_clearance_mm", 3.0)), 1.0)
        max_cg_offset_limit = max(float(self.runtime_constraints.get("max_cg_offset_mm", 20.0)), 1.0)

        # 归一化增益（>0 代表改善?
        penalty_gain = (prev_penalty - curr_penalty) / max(prev_penalty, 1.0)
        cg_gain = (prev_cg - curr_cg) / max_cg_offset_limit
        temp_gain = (prev_temp - curr_temp) / max_temp_limit
        clearance_gain = (curr_clearance - prev_clearance) / min_clearance_limit
        violation_gain = prev_violations - curr_violations

        score = 100.0 * (
            0.55 * penalty_gain +
            0.20 * cg_gain +
            0.10 * temp_gain +
            0.10 * clearance_gain +
            0.05 * violation_gain
        )
        return float(np.clip(score, -100.0, 100.0))

    def _should_rollback(
        self,
        iteration: int,
        current_eval: EvaluationResult
    ) -> tuple[bool, str]:
        """
        判断是否需要回退

        Args:
            iteration: 当前迭代次数
            current_eval: 当前评估结果

        Returns:
            (是否回退, 回退原因)
        """
        # 条件1: 仿真失败（如COMSOL网格崩溃?
        if not current_eval.success and current_eval.error_message:
            return True, f"仿真失败: {current_eval.error_message}"

        # 条件2: 惩罚分异常高?1000，说明严重恶化）
        # 但是：如果状态池里只有一个状态（或者最优状态就是当前状态），则不回退
        # 否则会导致无限循环！
        if current_eval.penalty_score > 1000.0:
            # 检查是否有更好的历史状态可以回退
            if len(self.state_history) > 1:
                best_penalty = min(
                    ev.penalty_score for _, ev in self.state_history.values()
                )
                # 只有当存在明显更好的历史状态时才回退
                if best_penalty < current_eval.penalty_score * 0.8:
                    return True, f"惩罚分过?({current_eval.penalty_score:.1f}), 设计严重恶化"
            # 否则不回退，让 LLM 尝试优化

        # 条件3: 连续3次迭代惩罚分持续上升
        if iteration >= 4:
            recent_states = sorted(
                [(sid, ev) for sid, (st, ev) in self.state_history.items() if ev.iteration >= iteration - 3],
                key=lambda x: x[1].iteration
            )
            if len(recent_states) >= 3:
                penalties = [ev.penalty_score for _, ev in recent_states[-3:]]
                if penalties[0] < penalties[1] < penalties[2]:
                    return True, f"连续3次迭代惩罚分上升: {penalties[0]:.1f} ?{penalties[1]:.1f} ?{penalties[2]:.1f}"

        return False, ""






