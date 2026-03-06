"""
Agent-loop execution service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.protocol import DesignState, EvaluationResult

if TYPE_CHECKING:
    from workflow.orchestrator import WorkflowOrchestrator


@dataclass
class AgentLoopService:
    """Encapsulates legacy agent_loop iterative flow."""

    host: "WorkflowOrchestrator"

    def run(
        self,
        *,
        current_state: DesignState,
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        agent_loop 模式执行入口（legacy 主循环）。
        """
        host = self.host
        runtime = getattr(host, "runtime_facade", None)
        if runtime is None:
            raise RuntimeError("runtime_facade is not configured")
        _ = convergence_threshold
        for iteration in range(1, max_iterations + 1):
            host.logger.logger.info(f"\n{'='*60}")
            host.logger.logger.info(f"Iteration {iteration}/{max_iterations}")
            host.logger.logger.info(f"{'='*60}")

            try:
                # Phase 4: 为当前状态生成唯一ID（每次迭代都更新，避免回退后 ID 不变）
                current_state.state_id = f"state_iter_{iteration:02d}_a"

                # 2.1 评估当前状态
                current_metrics, violations = runtime.evaluate_design(current_state, iteration)

                # Phase 4: 计算惩罚分并记录到状态池
                penalty_breakdown = runtime.calculate_penalty_breakdown(current_metrics, violations)
                penalty_score = penalty_breakdown["total"]
                eval_result = EvaluationResult(
                    state_id=current_state.state_id,
                    iteration=iteration,
                    success=len(violations) == 0,
                    metrics={
                        'max_temp': current_metrics['thermal'].max_temp,
                        'min_clearance': current_metrics['geometry'].min_clearance,
                        'cg_offset': current_metrics['geometry'].cg_offset_magnitude,
                        'total_power': current_metrics['power'].total_power
                    },
                    violations=[v.dict() if hasattr(v, 'dict') else v for v in violations],  # 转换为字典
                    penalty_score=penalty_score,
                    timestamp=__import__('datetime').datetime.now().isoformat()
                )
                host.state_history[current_state.state_id] = (current_state.copy(deep=True), eval_result)
                host.logger.logger.info(f"  状态记录: {current_state.state_id}, 惩罚分={penalty_score:.2f}")

                curr_max_temp = float(current_metrics["thermal"].max_temp)
                curr_min_clearance = float(current_metrics["geometry"].min_clearance)
                curr_cg_offset = float(current_metrics["geometry"].cg_offset_magnitude)
                curr_num_collisions = int(current_metrics["geometry"].num_collisions)
                curr_solver_cost = float(current_metrics.get("diagnostics", {}).get("solver_cost", 0.0))

                prev_metrics = runtime.get_last_trace_metrics()
                if prev_metrics is None:
                    delta_penalty = 0.0
                    delta_cg_offset = 0.0
                    delta_max_temp = 0.0
                    delta_min_clearance = 0.0
                else:
                    delta_penalty = penalty_score - prev_metrics["penalty_score"]
                    delta_cg_offset = curr_cg_offset - prev_metrics["cg_offset"]
                    delta_max_temp = curr_max_temp - prev_metrics["max_temp"]
                    delta_min_clearance = curr_min_clearance - prev_metrics["min_clearance"]

                current_snapshot = {
                    "penalty_score": penalty_score,
                    "cg_offset": curr_cg_offset,
                    "max_temp": curr_max_temp,
                    "min_clearance": curr_min_clearance,
                    "num_violations": len(violations),
                }
                runtime.append_snapshot(
                    iteration=iteration,
                    snapshot=current_snapshot,
                    max_history=40,
                )
                cg_plateau = runtime.is_cg_plateau(iteration, current_snapshot, violations)
                effectiveness_score = runtime.compute_effectiveness_score(prev_metrics, current_snapshot)

                # 记录迭代数据
                host.logger.log_metrics({
                    'iteration': iteration,
                    'timestamp': __import__('datetime').datetime.now().isoformat(),
                    'max_temp': curr_max_temp,
                    'avg_temp': float(current_metrics['thermal'].avg_temp),
                    'min_temp': float(current_metrics['thermal'].min_temp),
                    'temp_gradient': float(current_metrics['thermal'].temp_gradient),
                    'min_clearance': curr_min_clearance,
                    'cg_offset': curr_cg_offset,
                    'num_collisions': curr_num_collisions,
                    'total_mass': sum(c.mass for c in current_state.components),
                    'total_power': current_metrics['power'].total_power,
                    'num_violations': len(violations),
                    'is_safe': len(violations) == 0,
                    'solver_cost': curr_solver_cost,
                    'llm_tokens': 0,
                    'penalty_score': penalty_score,  # Phase 4: 记录惩罚分
                    'penalty_violation': penalty_breakdown["violation"],
                    'penalty_temp': penalty_breakdown["temp"],
                    'penalty_clearance': penalty_breakdown["clearance"],
                    'penalty_cg': penalty_breakdown["cg"],
                    'penalty_collision': penalty_breakdown["collision"],
                    'delta_penalty': delta_penalty,
                    'delta_cg_offset': delta_cg_offset,
                    'delta_max_temp': delta_max_temp,
                    'delta_min_clearance': delta_min_clearance,
                    'effectiveness_score': effectiveness_score,
                    'state_id': current_state.state_id  # Phase 4: 记录状态ID
                })
                runtime.set_last_trace_metrics(current_snapshot)

                # 保存设计状态（用于3D可视化）
                host.logger.save_design_state(iteration, current_state.dict())

                # 2.2 检查收敛
                if not violations:
                    host.logger.logger.info("✓ All constraints satisfied! Optimization converged.")
                    break

                # Phase 4: 检查是否需要回退
                should_rollback, rollback_reason = runtime.should_rollback(iteration, eval_result)
                if should_rollback:
                    host.logger.logger.warning(f"⚠️ 触发回退机制: {rollback_reason}")
                    rollback_state, rollback_eval = runtime.execute_rollback()
                    if rollback_state:
                        # 记录回退事件
                        host.logger.save_rollback_event(
                            iteration=iteration,
                            rollback_reason=rollback_reason,
                            from_state_id=current_state.state_id,
                            to_state_id=rollback_state.state_id,
                            penalty_before=eval_result.penalty_score,
                            penalty_after=rollback_eval.penalty_score
                        )

                        current_state = rollback_state
                        host.rollback_count += 1
                        host.logger.logger.info(f"✓ 已回退到状态: {current_state.state_id} (惩罚分={rollback_eval.penalty_score:.2f})")
                        # 记录失败原因
                        host.recent_failures.append(rollback_reason)
                        if len(host.recent_failures) > 3:
                            host.recent_failures = host.recent_failures[-3:]  # 只保留最近3次失败
                        continue  # 跳过本次迭代，从回退状态重新开始

                # 单违规平台期救援：仅当持续卡在 CG 约束附近时启用确定性搜索
                if cg_plateau and (iteration - runtime.get_cg_rescue_last_iter()) >= 2:
                    rescue_result = runtime.run_cg_plateau_rescue(
                        current_state=current_state,
                        current_metrics=current_metrics,
                        violations=violations,
                        iteration=iteration
                    )
                    if rescue_result is not None:
                        rescue_state, rescue_metrics, rescue_violations, rescue_meta = rescue_result
                        current_state = rescue_state
                        runtime.set_cg_rescue_last_iter(iteration)

                        rescue_state_id = f"state_iter_{iteration:02d}_r"
                        rescue_state.state_id = rescue_state_id
                        rescue_eval = EvaluationResult(
                            state_id=rescue_state_id,
                            iteration=iteration,
                            success=len(rescue_violations) == 0,
                            metrics={
                                'max_temp': rescue_metrics['thermal'].max_temp,
                                'min_clearance': rescue_metrics['geometry'].min_clearance,
                                'cg_offset': rescue_metrics['geometry'].cg_offset_magnitude,
                                'total_power': rescue_metrics['power'].total_power
                            },
                            violations=[v.dict() if hasattr(v, 'dict') else v for v in rescue_violations],
                            penalty_score=runtime.calculate_penalty_score(rescue_metrics, rescue_violations),
                            timestamp=__import__('datetime').datetime.now().isoformat()
                        )
                        host.state_history[rescue_state_id] = (rescue_state.copy(deep=True), rescue_eval)
                        host.logger.logger.info(
                            "✓ CG 平台期救援成功: "
                            f"{rescue_meta['component']} {rescue_meta['axis']} {rescue_meta['delta']:.2f}mm, "
                            f"cg {rescue_meta['cg_before']:.2f} -> {rescue_meta['cg_after']:.2f}"
                        )
                        # 救援已替代本轮 LLM 计划，直接进入下一轮
                        continue

                # 2.3 构建全局上下文
                context = runtime.build_global_context(
                    iteration,
                    current_state,
                    current_metrics,
                    violations,
                    phase="A",
                )

                # Phase 4: 保存 ContextPack 到 Trace
                host.logger.save_trace_data(
                    iteration=iteration,
                    context_pack=context.dict() if hasattr(context, 'dict') else context.__dict__
                )

                # 2.4 Meta-Reasoner生成战略计划
                planner = getattr(host, "strategic_planner", None)
                if planner is None or not hasattr(planner, "generate_strategic_plan"):
                    raise RuntimeError("strategic_planner controller is not configured")
                strategic_plan = planner.generate_strategic_plan(context)
                runtime.inject_runtime_constraints_to_plan(strategic_plan)
                host.logger.logger.info(f"Strategic plan: {strategic_plan.strategy_type}")

                # Phase 4: 保存 StrategicPlan 到 Trace
                host.logger.save_trace_data(
                    iteration=iteration,
                    strategic_plan=strategic_plan.dict() if hasattr(strategic_plan, 'dict') else strategic_plan.__dict__
                )

                # 2.5 Agent协调生成执行计划
                execution_plan = host.coordinator.coordinate(
                    strategic_plan,
                    current_state,
                    current_metrics
                )

                # 2.6 执行优化计划
                new_state = runtime.execute_plan(execution_plan, current_state)
                execution_meta = (
                    (new_state.metadata or {}).get("execution_meta", {})
                    if hasattr(new_state, "metadata")
                    else {}
                )

                # no-op 直接拒绝：避免“无变化状态”重复触发高成本仿真
                if not bool(execution_meta.get("state_changed", True)):
                    host.logger.logger.warning(
                        "✗ New state rejected: 执行计划未产生几何/属性变化，跳过本轮仿真"
                    )
                    failure_desc = (
                        f"迭代{iteration}: 计划无有效变更 "
                        f"(执行={execution_meta.get('executed_actions', 0)}, "
                        f"生效={execution_meta.get('effective_actions', 0)})"
                    )
                    host.recent_failures.append(failure_desc)
                    if len(host.recent_failures) > 3:
                        host.recent_failures = host.recent_failures[-3:]
                    continue

                # 候选态几何门控：不通过则直接拒绝，避免无效 COMSOL 调用
                candidate_feasible, cand_clearance, cand_collisions = runtime.is_geometry_feasible(new_state)
                if not candidate_feasible:
                    host.logger.logger.warning(
                        "✗ New state rejected before simulation: "
                        f"几何不可行 (min_clearance={cand_clearance:.2f}mm, "
                        f"collisions={cand_collisions})"
                    )
                    failure_desc = (
                        f"迭代{iteration}: 候选几何不可行 "
                        f"(min_clearance={cand_clearance:.2f}mm, collisions={cand_collisions})"
                    )
                    host.recent_failures.append(failure_desc)
                    if len(host.recent_failures) > 3:
                        host.recent_failures = host.recent_failures[-3:]
                    continue

                # Phase 4: 为新状态设置版本树信息
                new_state.state_id = f"state_iter_{iteration:02d}_b"
                new_state.parent_id = current_state.state_id
                new_state.iteration = iteration

                # 2.7 验证新状态
                new_metrics, new_violations = runtime.evaluate_design(new_state, iteration)

                # 2.8 判断是否接受新状态
                allow_penalty_regression = 0.0
                require_cg_improve_on_regression = False
                if cg_plateau:
                    allow_penalty_regression = 2.0
                    require_cg_improve_on_regression = True

                if runtime.should_accept(
                    current_metrics,
                    new_metrics,
                    violations,
                    new_violations,
                    allow_penalty_regression=allow_penalty_regression,
                    require_cg_improve_on_regression=require_cg_improve_on_regression
                ):
                    current_state = new_state
                    host.logger.logger.info("✓ New state accepted")

                    # 学习：将成功案例加入知识库
                    runtime.learn_from_iteration(
                        iteration,
                        strategic_plan,
                        execution_plan,
                        current_metrics,
                        new_metrics,
                        success=True
                    )
                else:
                    host.logger.logger.warning("✗ New state rejected, rolling back")

                    # Phase 4: 记录失败操作
                    failure_desc = f"迭代{iteration}: {strategic_plan.strategy_type} 导致性能恶化"
                    host.recent_failures.append(failure_desc)
                    if len(host.recent_failures) > 3:
                        host.recent_failures = host.recent_failures[-3:]

                    # 学习：记录失败案例
                    runtime.learn_from_iteration(
                        iteration,
                        strategic_plan,
                        execution_plan,
                        current_metrics,
                        new_metrics,
                        success=False
                    )

            except Exception as e:
                host.logger.logger.error(f"Iteration {iteration} failed: {e}", exc_info=True)
                continue

        # 3. 生成最终报告
        runtime.generate_final_report(current_state, iteration)
        return current_state



