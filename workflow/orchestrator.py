"""
Workflow Orchestrator: 主工作流编排器

负责：
1. 初始化所有模块（几何、仿真、优化）
2. 执行完整的优化迭代循环
3. 管理实验生命周期
4. 生成最终报告
"""

import os
import re
import json
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
import yaml
import numpy as np
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

from core.protocol import DesignState, ComponentGeometry, Vector3D, EvaluationResult
from core.logger import ExperimentLogger
from core.exceptions import SatelliteDesignError

from geometry.layout_engine import LayoutEngine
from simulation.base import SimulationDriver
from simulation.comsol_driver import ComsolDriver

try:
    from simulation.matlab_driver import MatlabDriver
except ImportError:
    MatlabDriver = None

try:
    from simulation.physics_engine import (
        SimplifiedPhysicsEngine,
        estimate_proxy_thermal_metrics,
    )
except ImportError:
    SimplifiedPhysicsEngine = None
    estimate_proxy_thermal_metrics = None

from optimization.meta_reasoner import MetaReasoner
from optimization.agents import GeometryAgent, ThermalAgent, StructuralAgent, PowerAgent
from optimization.coordinator import AgentCoordinator
from optimization.knowledge.rag_system import RAGSystem
from optimization.maas_audit import select_top_pareto_indices
from optimization.maas_compiler import compile_intent_to_problem_spec, formulate_modeling_intent
from optimization.maas_mcts import MCTSEvaluation, MCTSNode, MCTSVariant, MaaSMCTSPlanner
from optimization.maas_reflection import diagnose_solver_outcome, suggest_constraint_relaxation
from optimization.operator_actions import (
    apply_operator_program_to_intent,
    build_operator_program_from_context,
)
from optimization.pymoo_integration import (
    CentroidPushApartRepair,
    PymooMOEADRunner,
    OperatorProgramProblemGenerator,
    PymooNSGA2Runner,
    PymooNSGA3Runner,
    PymooProblemGenerator,
)
from workflow.maas_pipeline_service import MaaSPipelineService
from optimization.protocol import (
    GlobalContextPack,
    GeometryMetrics,
    ThermalMetrics,
    StructuralMetrics,
    PowerMetrics,
    ViolationItem,
    ModelingIntent,
)


class WorkflowOrchestrator:
    """主工作流编排器"""

    def __init__(self, config_path: str = "config/system.yaml"):
        """
        初始化编排器

        Args:
            config_path: 配置文件路径
        """
        self.config = self._load_config(config_path)
        self.default_constraints = self._normalize_constraints(
            self.config.get("simulation", {}).get("constraints", {})
        )
        self.runtime_constraints = dict(self.default_constraints)
        self.optimization_mode = str(
            self.config.get("optimization", {}).get("mode", "agent_loop")
        ).strip().lower()
        if self.optimization_mode not in {"agent_loop", "pymoo_maas"}:
            raise SatelliteDesignError(
                f"Unsupported optimization.mode: {self.optimization_mode}. "
                "Use 'agent_loop' or 'pymoo_maas'."
            )

        # 初始化日志
        self.logger = ExperimentLogger(
            base_dir=self.config.get("logging", {}).get("base_dir", "experiments")
        )

        # 初始化各模块
        self._initialize_modules()
        self.maas_pipeline_service = MaaSPipelineService(host=self)
        # Runtime operator credits for adaptive operator-bias tuning.
        self._maas_operator_credit_stats: Dict[str, Dict[str, Any]] = {}

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """加载配置文件并替换环境变量"""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 递归替换环境变量
        config = self._replace_env_vars(config)
        return config

    def _replace_env_vars(self, obj):
        """递归替换配置中的环境变量占位符 ${VAR_NAME}"""
        if isinstance(obj, dict):
            return {k: self._replace_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._replace_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # 匹配 ${VAR_NAME} 格式
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, obj)
            for var_name in matches:
                env_value = os.environ.get(var_name, '')
                obj = obj.replace(f'${{{var_name}}}', env_value)
            return obj
        else:
            return obj

    def _normalize_constraints(self, raw_constraints: Optional[Dict[str, Any]]) -> Dict[str, float]:
        """标准化约束配置（统一键名与默认值）"""
        raw_constraints = raw_constraints or {}
        return {
            "max_temp_c": float(raw_constraints.get("max_temp_c", 60.0)),
            "min_clearance_mm": float(raw_constraints.get("min_clearance_mm", 3.0)),
            "max_cg_offset_mm": float(raw_constraints.get("max_cg_offset_mm", 20.0)),
            "min_safety_factor": float(raw_constraints.get("min_safety_factor", 2.0)),
        }

    def _extract_bom_overrides(self, bom_file: str) -> tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
        """
        从 BOM 文件中提取约束覆盖与组件扩展热学属性。

        Returns:
            (constraints_override, component_props_by_id)
        """
        path = Path(bom_file)
        if not path.exists():
            return {}, {}

        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            return {}, {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                if path.suffix.lower() == ".json":
                    raw = json.load(f)
                else:
                    raw = yaml.safe_load(f)
        except Exception as e:
            self.logger.logger.warning(f"Failed to parse BOM overrides from {bom_file}: {e}")
            return {}, {}

        if not isinstance(raw, dict):
            return {}, {}

        constraint_override = {}
        raw_constraints = raw.get("constraints", {})
        if isinstance(raw_constraints, dict):
            if "max_temperature" in raw_constraints:
                constraint_override["max_temp_c"] = float(raw_constraints["max_temperature"])
            if "min_clearance" in raw_constraints:
                constraint_override["min_clearance_mm"] = float(raw_constraints["min_clearance"])
            if "max_cg_offset" in raw_constraints:
                constraint_override["max_cg_offset_mm"] = float(raw_constraints["max_cg_offset"])

        component_props_by_id: Dict[str, Dict[str, Any]] = {}
        items = raw.get("components", [])
        if isinstance(items, list):
            quantity_map: Dict[str, int] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                comp_id = item.get("id")
                if not comp_id:
                    continue
                quantity_map[comp_id] = int(item.get("quantity", 1))

            for item in items:
                if not isinstance(item, dict):
                    continue
                base_id = item.get("id")
                if not base_id:
                    continue
                quantity = int(item.get("quantity", 1))
                thermal_contacts = item.get("thermal_contacts", {})
                if not isinstance(thermal_contacts, dict):
                    thermal_contacts = {}

                for idx in range(1, quantity + 1):
                    comp_id = base_id if quantity == 1 else f"{base_id}_{idx:02d}"
                    mapped_contacts = {}
                    for target_id_raw, conductance in thermal_contacts.items():
                        target_id = str(target_id_raw)
                        target_qty = quantity_map.get(target_id)
                        if target_qty is not None and target_qty > 1:
                            mapped_idx = min(idx, target_qty)
                            target_id = f"{target_id}_{mapped_idx:02d}"
                        mapped_contacts[target_id] = float(conductance)

                    component_props_by_id[comp_id] = {
                        "thermal_contacts": mapped_contacts,
                        "emissivity": item.get("emissivity"),
                        "absorptivity": item.get("absorptivity"),
                        "coating_type": item.get("coating_type"),
                    }

        return constraint_override, component_props_by_id

    def _initialize_modules(self):
        """初始化所有模块"""
        # 1. 几何模块
        geom_config = self.config.get("geometry", {})
        self.layout_engine = LayoutEngine(config=geom_config)

        # 2. 仿真模块
        sim_config = self.config.get("simulation", {})
        sim_backend = sim_config.get("backend", "simplified")

        if sim_backend == "matlab":
            if MatlabDriver is None:
                raise SatelliteDesignError("simulation.matlab_driver 不可用，无法使用 matlab backend")
            self.sim_driver = MatlabDriver(
                matlab_path=sim_config.get("matlab_path"),
                script_path=sim_config.get("matlab_script")
            )
        elif sim_backend == "comsol":
            self.sim_driver = ComsolDriver(config=sim_config)
        else:
            if SimplifiedPhysicsEngine is None:
                raise SatelliteDesignError("simulation.physics_engine 不可用，无法使用 simplified backend")
            self.sim_driver = SimplifiedPhysicsEngine(config=sim_config)

        # 3. LLM模块
        openai_config = self.config.get("openai", {})
        api_key = openai_config.get("api_key")
        base_url = openai_config.get("base_url")  # 获取base_url配置

        if not api_key:
            raise ValueError("API key not found in config")

        # Meta-Reasoner
        self.meta_reasoner = MetaReasoner(
            api_key=api_key,
            model=openai_config.get("model", "qwen3-max"),
            temperature=openai_config.get("temperature", 0.7),
            base_url=base_url,
            logger=self.logger
        )

        # Agents
        agent_model = openai_config.get("model", "qwen3-max")
        agent_temperature = openai_config.get("temperature", 0.7)

        self.geometry_agent = GeometryAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger
        )
        self.thermal_agent = ThermalAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger
        )
        self.structural_agent = StructuralAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger
        )
        self.power_agent = PowerAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger
        )

        # Coordinator
        self.coordinator = AgentCoordinator(
            geometry_agent=self.geometry_agent,
            thermal_agent=self.thermal_agent,
            structural_agent=self.structural_agent,
            power_agent=self.power_agent,
            logger=self.logger
        )

        # RAG System
        knowledge_config = self.config.get("knowledge", {})
        self.rag_system = RAGSystem(
            api_key=api_key,
            knowledge_base_path=knowledge_config.get("base_path", "data/knowledge_base"),
            embedding_model=knowledge_config.get("embedding_model"),
            base_url=base_url,
            enable_semantic=bool(knowledge_config.get("enable_semantic", True)),
            filter_anomalous_cases=bool(
                knowledge_config.get("filter_anomalous_cases", True)
            ),
            anomaly_temp_tokens=list(
                knowledge_config.get(
                    "anomaly_temp_tokens",
                    ["999.0", "9999.0", "999°C", "9999°C"],
                )
                or []
            ),
            anomaly_max_temp_delta_abs=float(
                knowledge_config.get("anomaly_max_temp_delta_abs", 200.0)
            ),
            logger=self.logger
        )

        # Phase 4: 状态池与回退机制
        self.state_history = {}  # {state_id: (DesignState, EvaluationResult)}
        self.recent_failures = []  # 最近失败的操作描述
        self.rollback_count = 0  # 回退次数统计
        self._snapshot_history: List[Dict[str, float]] = []  # 用于平台期检测
        self._cg_rescue_last_iter: int = -999  # 防止每轮都触发救援

        self.logger.logger.info(
            f"All modules initialized successfully (optimization_mode={self.optimization_mode})"
        )

    def run_optimization(
        self,
        bom_file: Optional[str] = None,
        max_iterations: int = 20,
        convergence_threshold: float = 0.01
    ) -> DesignState:
        """
        运行完整的优化流程

        Args:
            bom_file: BOM文件路径（可选）
            max_iterations: 最大迭代次数
            convergence_threshold: 收敛阈值

        Returns:
            最终设计状态
        """
        self.logger.logger.info(f"Starting optimization (max_iter={max_iterations})")
        self.logger.logger.info(f"Optimization mode: {self.optimization_mode}")
        self.runtime_constraints = dict(self.default_constraints)
        self._last_trace_metrics = None  # 用于计算迭代增量
        self._snapshot_history = []
        self._cg_rescue_last_iter = -999
        self._maas_operator_credit_stats = {}
        self.logger.logger.info(
            "Runtime constraints initialized: "
            f"T<= {self.runtime_constraints['max_temp_c']:.2f}°C, "
            f"clearance>= {self.runtime_constraints['min_clearance_mm']:.2f}mm, "
            f"CG<= {self.runtime_constraints['max_cg_offset_mm']:.2f}mm"
        )

        # 1. 初始化设计状态
        current_state = self._initialize_design_state(bom_file)

        if self.optimization_mode == "pymoo_maas":
            return self._run_pymoo_maas_pipeline(
                current_state=current_state,
                bom_file=bom_file,
                max_iterations=max_iterations,
                convergence_threshold=convergence_threshold,
            )

        # 2. 迭代优化
        for iteration in range(1, max_iterations + 1):
            self.logger.logger.info(f"\n{'='*60}")
            self.logger.logger.info(f"Iteration {iteration}/{max_iterations}")
            self.logger.logger.info(f"{'='*60}")

            try:
                # Phase 4: 为当前状态生成唯一ID（每次迭代都更新，避免回退后 ID 不变）
                current_state.state_id = f"state_iter_{iteration:02d}_a"

                # 2.1 评估当前状态
                current_metrics, violations = self._evaluate_design(current_state, iteration)

                # Phase 4: 计算惩罚分并记录到状态池
                penalty_breakdown = self._calculate_penalty_breakdown(current_metrics, violations)
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
                self.state_history[current_state.state_id] = (current_state.copy(deep=True), eval_result)
                self.logger.logger.info(f"  状态记录: {current_state.state_id}, 惩罚分={penalty_score:.2f}")

                curr_max_temp = float(current_metrics["thermal"].max_temp)
                curr_min_clearance = float(current_metrics["geometry"].min_clearance)
                curr_cg_offset = float(current_metrics["geometry"].cg_offset_magnitude)
                curr_num_collisions = int(current_metrics["geometry"].num_collisions)
                curr_solver_cost = float(current_metrics.get("diagnostics", {}).get("solver_cost", 0.0))

                prev_metrics = self._last_trace_metrics
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
                self._snapshot_history.append({"iteration": float(iteration), **current_snapshot})
                if len(self._snapshot_history) > 40:
                    self._snapshot_history = self._snapshot_history[-40:]
                cg_plateau = self._is_cg_plateau(iteration, current_snapshot, violations)
                effectiveness_score = self._compute_effectiveness_score(prev_metrics, current_snapshot)

                # 记录迭代数据
                self.logger.log_metrics({
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
                self._last_trace_metrics = current_snapshot

                # 保存设计状态（用于3D可视化）
                self.logger.save_design_state(iteration, current_state.dict())

                # 2.2 检查收敛
                if not violations:
                    self.logger.logger.info("✓ All constraints satisfied! Optimization converged.")
                    break

                # Phase 4: 检查是否需要回退
                should_rollback, rollback_reason = self._should_rollback(iteration, eval_result)
                if should_rollback:
                    self.logger.logger.warning(f"⚠️ 触发回退机制: {rollback_reason}")
                    rollback_state, rollback_eval = self._execute_rollback()
                    if rollback_state:
                        # 记录回退事件
                        self.logger.save_rollback_event(
                            iteration=iteration,
                            rollback_reason=rollback_reason,
                            from_state_id=current_state.state_id,
                            to_state_id=rollback_state.state_id,
                            penalty_before=eval_result.penalty_score,
                            penalty_after=rollback_eval.penalty_score
                        )

                        current_state = rollback_state
                        self.rollback_count += 1
                        self.logger.logger.info(f"✓ 已回退到状态: {current_state.state_id} (惩罚分={rollback_eval.penalty_score:.2f})")
                        # 记录失败原因
                        self.recent_failures.append(rollback_reason)
                        if len(self.recent_failures) > 3:
                            self.recent_failures = self.recent_failures[-3:]  # 只保留最近3次失败
                        continue  # 跳过本次迭代，从回退状态重新开始

                # 单违规平台期救援：仅当持续卡在 CG 约束附近时启用确定性搜索
                if cg_plateau and (iteration - self._cg_rescue_last_iter) >= 2:
                    rescue_result = self._run_cg_plateau_rescue(
                        current_state=current_state,
                        current_metrics=current_metrics,
                        violations=violations,
                        iteration=iteration
                    )
                    if rescue_result is not None:
                        rescue_state, rescue_metrics, rescue_violations, rescue_meta = rescue_result
                        current_state = rescue_state
                        self._cg_rescue_last_iter = iteration

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
                            penalty_score=self._calculate_penalty_score(rescue_metrics, rescue_violations),
                            timestamp=__import__('datetime').datetime.now().isoformat()
                        )
                        self.state_history[rescue_state_id] = (rescue_state.copy(deep=True), rescue_eval)
                        self.logger.logger.info(
                            "✓ CG 平台期救援成功: "
                            f"{rescue_meta['component']} {rescue_meta['axis']} {rescue_meta['delta']:.2f}mm, "
                            f"cg {rescue_meta['cg_before']:.2f} -> {rescue_meta['cg_after']:.2f}"
                        )
                        # 救援已替代本轮 LLM 计划，直接进入下一轮
                        continue

                # 2.3 构建全局上下文
                context = self._build_global_context(
                    iteration,
                    current_state,
                    current_metrics,
                    violations
                )

                # Phase 4: 保存 ContextPack 到 Trace
                self.logger.save_trace_data(
                    iteration=iteration,
                    context_pack=context.dict() if hasattr(context, 'dict') else context.__dict__
                )

                # 2.4 Meta-Reasoner生成战略计划
                strategic_plan = self.meta_reasoner.generate_strategic_plan(context)
                self._inject_runtime_constraints_to_plan(strategic_plan)
                self.logger.logger.info(f"Strategic plan: {strategic_plan.strategy_type}")

                # Phase 4: 保存 StrategicPlan 到 Trace
                self.logger.save_trace_data(
                    iteration=iteration,
                    strategic_plan=strategic_plan.dict() if hasattr(strategic_plan, 'dict') else strategic_plan.__dict__
                )

                # 2.5 Agent协调生成执行计划
                execution_plan = self.coordinator.coordinate(
                    strategic_plan,
                    current_state,
                    current_metrics
                )

                # 2.6 执行优化计划
                new_state = self._execute_plan(execution_plan, current_state)
                execution_meta = (
                    (new_state.metadata or {}).get("execution_meta", {})
                    if hasattr(new_state, "metadata")
                    else {}
                )

                # no-op 直接拒绝：避免“无变化状态”重复触发高成本仿真
                if not bool(execution_meta.get("state_changed", True)):
                    self.logger.logger.warning(
                        "✗ New state rejected: 执行计划未产生几何/属性变化，跳过本轮仿真"
                    )
                    failure_desc = (
                        f"迭代{iteration}: 计划无有效变更 "
                        f"(执行={execution_meta.get('executed_actions', 0)}, "
                        f"生效={execution_meta.get('effective_actions', 0)})"
                    )
                    self.recent_failures.append(failure_desc)
                    if len(self.recent_failures) > 3:
                        self.recent_failures = self.recent_failures[-3:]
                    continue

                # 候选态几何门控：不通过则直接拒绝，避免无效 COMSOL 调用
                candidate_feasible, cand_clearance, cand_collisions = self._is_geometry_feasible(new_state)
                if not candidate_feasible:
                    self.logger.logger.warning(
                        "✗ New state rejected before simulation: "
                        f"几何不可行 (min_clearance={cand_clearance:.2f}mm, "
                        f"collisions={cand_collisions})"
                    )
                    failure_desc = (
                        f"迭代{iteration}: 候选几何不可行 "
                        f"(min_clearance={cand_clearance:.2f}mm, collisions={cand_collisions})"
                    )
                    self.recent_failures.append(failure_desc)
                    if len(self.recent_failures) > 3:
                        self.recent_failures = self.recent_failures[-3:]
                    continue

                # Phase 4: 为新状态设置版本树信息
                new_state.state_id = f"state_iter_{iteration:02d}_b"
                new_state.parent_id = current_state.state_id
                new_state.iteration = iteration

                # 2.7 验证新状态
                new_metrics, new_violations = self._evaluate_design(new_state, iteration)

                # 2.8 判断是否接受新状态
                allow_penalty_regression = 0.0
                require_cg_improve_on_regression = False
                if cg_plateau:
                    allow_penalty_regression = 2.0
                    require_cg_improve_on_regression = True

                if self._should_accept(
                    current_metrics,
                    new_metrics,
                    violations,
                    new_violations,
                    allow_penalty_regression=allow_penalty_regression,
                    require_cg_improve_on_regression=require_cg_improve_on_regression
                ):
                    current_state = new_state
                    self.logger.logger.info("✓ New state accepted")

                    # 学习：将成功案例加入知识库
                    self._learn_from_iteration(
                        iteration,
                        strategic_plan,
                        execution_plan,
                        current_metrics,
                        new_metrics,
                        success=True
                    )
                else:
                    self.logger.logger.warning("✗ New state rejected, rolling back")

                    # Phase 4: 记录失败操作
                    failure_desc = f"迭代{iteration}: {strategic_plan.strategy_type} 导致性能恶化"
                    self.recent_failures.append(failure_desc)
                    if len(self.recent_failures) > 3:
                        self.recent_failures = self.recent_failures[-3:]

                    # 学习：记录失败案例
                    self._learn_from_iteration(
                        iteration,
                        strategic_plan,
                        execution_plan,
                        current_metrics,
                        new_metrics,
                        success=False
                    )

            except Exception as e:
                self.logger.logger.error(f"Iteration {iteration} failed: {e}", exc_info=True)
                continue

        # 3. 生成最终报告
        self._generate_final_report(current_state, iteration)

        return current_state

    def _is_maas_retryable(
        self,
        diagnosis: Dict[str, Any],
        retry_on_stall: bool,
    ) -> bool:
        """判断 MaaS 是否应触发下一轮自动松弛重求解。"""
        status = str(diagnosis.get("status", ""))
        if status in {"runtime_error", "no_feasible", "empty_solution"}:
            return True
        if retry_on_stall and status == "feasible_but_stalled":
            return True
        return False

    def _apply_relaxation_suggestions_to_intent(
        self,
        intent: ModelingIntent,
        suggestions: List[Dict[str, Any]],
        attempt: int,
    ) -> tuple[ModelingIntent, int]:
        """
        把反射阶段建议应用到下一轮 intent（仅处理 bound_relaxation）。
        """
        if not suggestions:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        by_name = {cons.name: cons for cons in next_intent.hard_constraints}
        applied_count = 0
        applied_notes: List[str] = []

        for suggestion in suggestions:
            if str(suggestion.get("type")) != "bound_relaxation":
                continue
            constraint_name = str(suggestion.get("constraint", ""))
            target = suggestion.get("suggested_target")
            if constraint_name not in by_name:
                continue
            try:
                numeric_target = float(target)
            except (TypeError, ValueError):
                continue
            by_name[constraint_name].target_value = numeric_target
            applied_count += 1
            applied_notes.append(f"{constraint_name}={numeric_target:.6g}")

        if applied_count > 0:
            next_intent.assumptions.append(
                f"auto_relax_attempt_{attempt}: " + ", ".join(applied_notes)
            )
        return next_intent, applied_count

    def _decode_maas_candidate_state(
        self,
        execution_result: Any,
        problem_generator: Any,
        base_state: DesignState,
        attempt: int,
    ) -> Optional[DesignState]:
        """从 Pareto 前沿选择代表解并解码。"""
        if execution_result is None or problem_generator is None:
            return None
        if not bool(getattr(execution_result, "success", False)):
            return None
        if getattr(execution_result, "pareto_X", None) is None:
            return None

        try:
            pareto_x = np.asarray(execution_result.pareto_X, dtype=float)
            if pareto_x.ndim == 1:
                best_x = pareto_x
            elif pareto_x.ndim >= 2 and pareto_x.shape[0] > 0:
                n_points = int(pareto_x.shape[0])
                best_idx = 0
                pareto_f_raw = getattr(execution_result, "pareto_F", None)
                pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
                pareto_f: Optional[np.ndarray] = None
                pareto_cv: Optional[np.ndarray] = None

                if pareto_f_raw is not None:
                    parsed_f = np.asarray(pareto_f_raw, dtype=float)
                    if parsed_f.ndim == 2 and parsed_f.shape[0] == n_points:
                        pareto_f = parsed_f

                if pareto_cv_raw is not None:
                    parsed_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
                    if parsed_cv.size == n_points:
                        pareto_cv = parsed_cv

                obj_sum: Optional[np.ndarray] = None
                if pareto_f is not None:
                    obj_sum = np.sum(pareto_f, axis=1)

                # Feasibility-first representative selection:
                # 1) prefer feasible points by CV<=0
                # 2) fallback to least CV if no feasible
                # 3) use objective sum as tie-breaker
                if pareto_cv is not None:
                    finite_cv_idx = np.where(np.isfinite(pareto_cv))[0]
                    feasible_idx = finite_cv_idx[pareto_cv[finite_cv_idx] <= 1e-9]
                    if feasible_idx.size > 0:
                        if obj_sum is not None:
                            finite_obj = feasible_idx[np.isfinite(obj_sum[feasible_idx])]
                            if finite_obj.size > 0:
                                best_idx = int(finite_obj[np.argmin(obj_sum[finite_obj])])
                            else:
                                best_idx = int(feasible_idx[0])
                        else:
                            best_idx = int(feasible_idx[0])
                    elif finite_cv_idx.size > 0:
                        min_cv = float(np.min(pareto_cv[finite_cv_idx]))
                        min_cv_idx = finite_cv_idx[np.isclose(pareto_cv[finite_cv_idx], min_cv)]
                        if obj_sum is not None:
                            finite_obj = min_cv_idx[np.isfinite(obj_sum[min_cv_idx])]
                            if finite_obj.size > 0:
                                best_idx = int(finite_obj[np.argmin(obj_sum[finite_obj])])
                            else:
                                best_idx = int(min_cv_idx[0])
                        else:
                            best_idx = int(min_cv_idx[0])
                elif obj_sum is not None:
                    finite_idx = np.where(np.isfinite(obj_sum))[0]
                    if finite_idx.size > 0:
                        best_idx = int(finite_idx[np.argmin(obj_sum[finite_idx])])

                best_x = pareto_x[best_idx]
            else:
                return None

            candidate = problem_generator.codec.decode(best_x)
            candidate.parent_id = base_state.state_id
            candidate.state_id = f"state_iter_02_maas_candidate_attempt_{attempt:02d}"
            candidate.iteration = 2
            return candidate
        except Exception as exc:
            self.logger.logger.warning(f"Failed to decode best pymoo solution: {exc}")
            return None

    def _build_maas_runtime_thermal_evaluator(
        self,
        mode: str,
        base_iteration: int,
    ):
        """
        构建运行时热评估回调。

        - `proxy`: 返回 None，使用 problem_generator 内部热代理模型。
        - `online_comsol`: 在 _evaluate_design 上做缓存包装，作为高保真热评估回调。
        """
        normalized = str(mode or "proxy").strip().lower()
        if normalized != "online_comsol":
            return None

        opt_cfg = self.config.get("optimization", {})
        eval_budget = int(opt_cfg.get("pymoo_maas_online_comsol_eval_budget", 0))
        eval_budget = max(eval_budget, 0)
        budget_control = {"eval_budget": int(eval_budget)}
        geometry_gate_enabled = bool(
            opt_cfg.get("pymoo_maas_online_comsol_geometry_gate", True)
        )
        progress_log_interval = int(
            opt_cfg.get("pymoo_maas_online_comsol_stats_log_interval", 0)
        )
        progress_log_interval = max(progress_log_interval, 0)
        cache_quantize_mm = max(
            float(opt_cfg.get("pymoo_maas_online_comsol_cache_quantize_mm", 0.0)),
            0.0,
        )
        def _safe_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)

        def _safe_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        def _normalize_schedule_mode(value: Any) -> str:
            parsed = str(value or "budget_only").strip().lower()
            if parsed not in {"budget_only", "ucb_topk"}:
                return "budget_only"
            return parsed

        def _sanitize_scheduler_params(params: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "mode": _normalize_schedule_mode(params.get("mode", "budget_only")),
                "top_fraction": float(
                    np.clip(
                        _safe_float(params.get("top_fraction", 0.20), 0.20),
                        0.01,
                        1.0,
                    )
                ),
                "min_observations": max(
                    1,
                    _safe_int(params.get("min_observations", 8), 8),
                ),
                "warmup_calls": max(
                    0,
                    _safe_int(params.get("warmup_calls", 2), 2),
                ),
                "explore_prob": float(
                    np.clip(
                        _safe_float(params.get("explore_prob", 0.05), 0.05),
                        0.0,
                        1.0,
                    )
                ),
                "uncertainty_weight": float(
                    np.clip(
                        _safe_float(params.get("uncertainty_weight", 0.35), 0.35),
                        0.0,
                        5.0,
                    )
                ),
                "uncertainty_scale_mm": max(
                    _safe_float(params.get("uncertainty_scale_mm", 25.0), 25.0),
                    1e-6,
                ),
            }

        schedule_control = _sanitize_scheduler_params({
            "mode": opt_cfg.get("pymoo_maas_online_comsol_schedule_mode", "budget_only"),
            "top_fraction": opt_cfg.get("pymoo_maas_online_comsol_schedule_top_fraction", 0.20),
            "min_observations": opt_cfg.get("pymoo_maas_online_comsol_schedule_min_observations", 8),
            "warmup_calls": opt_cfg.get("pymoo_maas_online_comsol_schedule_warmup_calls", 2),
            "explore_prob": opt_cfg.get("pymoo_maas_online_comsol_schedule_explore_prob", 0.05),
            "uncertainty_weight": opt_cfg.get("pymoo_maas_online_comsol_schedule_uncertainty_weight", 0.35),
            "uncertainty_scale_mm": opt_cfg.get("pymoo_maas_online_comsol_schedule_uncertainty_scale_mm", 25.0),
        })
        scheduler_rng = np.random.default_rng(
            int(opt_cfg.get("pymoo_seed", 42)) + int(base_iteration)
        )
        scheduler_state: Dict[str, Any] = {
            "scores": [],
            "vectors": [],
        }

        cache: Dict[Any, Dict[str, float]] = {}
        counter = {"n": 0}
        stats = {
            "requests_total": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "executed_online_comsol": 0,
            "fallback_proxy_budget_exhausted": 0,
            "fallback_proxy_geometry_infeasible": 0,
            "fallback_proxy_exceptions": 0,
            "eval_budget": int(budget_control["eval_budget"]),
            "budget_exhausted": False,
            "geometry_gate_enabled": bool(geometry_gate_enabled),
            "cache_quantize_mm": float(cache_quantize_mm),
            "stats_log_interval": int(progress_log_interval),
            "stats_progress_logs": 0,
            "schedule_mode": str(schedule_control["mode"]),
            "schedule_top_fraction": float(schedule_control["top_fraction"]),
            "schedule_min_observations": int(schedule_control["min_observations"]),
            "schedule_warmup_calls": int(schedule_control["warmup_calls"]),
            "schedule_explore_prob": float(schedule_control["explore_prob"]),
            "schedule_uncertainty_weight": float(schedule_control["uncertainty_weight"]),
            "schedule_uncertainty_scale_mm": float(schedule_control["uncertainty_scale_mm"]),
            "scheduler_candidates_seen": 0,
            "scheduler_selected_warmup": 0,
            "scheduler_selected_rank": 0,
            "scheduler_selected_explore": 0,
            "fallback_proxy_scheduler_skipped": 0,
        }
        budget_text = "unlimited" if int(budget_control["eval_budget"]) <= 0 else str(int(budget_control["eval_budget"]))
        self.logger.logger.info(
            "MaaS thermal evaluator mode=online_comsol "
            "(cached, eval_budget=%s, geometry_gate=%s, cache_quantize_mm=%.3f, "
            "schedule=%s, stats_log_interval=%s)",
            budget_text,
            "on" if geometry_gate_enabled else "off",
            cache_quantize_mm,
            str(schedule_control["mode"]),
            str(progress_log_interval) if progress_log_interval > 0 else "off",
        )

        def _proxy_thermal_payload(
            design_state: DesignState,
            *,
            min_clearance_hint: Optional[float] = None,
            num_collisions_hint: Optional[int] = None,
            source: str,
            acq_score: Optional[float] = None,
            uncertainty: Optional[float] = None,
        ) -> Dict[str, Any]:
            min_clearance_value = min_clearance_hint
            if min_clearance_value is None:
                min_clearance_value, _ = self._calculate_pairwise_clearance(design_state)
            num_collisions_value = int(num_collisions_hint or 0)
            if callable(estimate_proxy_thermal_metrics):
                thermal_proxy = estimate_proxy_thermal_metrics(
                    design_state,
                    min_clearance_mm=float(min_clearance_value),
                    num_collisions=int(num_collisions_value),
                )
                max_temp = float(thermal_proxy["max_temp"])
                min_temp = float(thermal_proxy["min_temp"])
                avg_temp = float(thermal_proxy["avg_temp"])
            else:
                total_power = float(sum(float(comp.power) for comp in design_state.components))
                clearance_term = 30.0 / max(float(min_clearance_value) + 10.0, 1.0)
                max_temp = 18.0 + 0.09 * total_power + clearance_term
                min_temp = float(max_temp - 8.0)
                avg_temp = float(max_temp - 3.0)
            payload: Dict[str, Any] = {
                "max_temp": float(max_temp),
                "min_temp": float(min_temp),
                "avg_temp": float(avg_temp),
                "_source": str(source),
                "_proxy_min_clearance": float(min_clearance_value),
            }
            if callable(estimate_proxy_thermal_metrics):
                payload["_proxy_num_collisions"] = int(num_collisions_value)
                payload["_proxy_hotspot_compaction"] = float(thermal_proxy.get("hotspot_compaction", 0.0))
                payload["_proxy_wall_cooling_score"] = float(thermal_proxy.get("wall_cooling_score", 0.0))
                payload["_proxy_power_spread_score"] = float(thermal_proxy.get("power_spread_score", 0.0))
            if acq_score is not None:
                payload["_schedule_acq_score"] = float(acq_score)
            if uncertainty is not None:
                payload["_schedule_uncertainty"] = float(uncertainty)
            return payload

        def _position_vector(design_state: DesignState) -> np.ndarray:
            vec: List[float] = []
            for comp in design_state.components:
                vec.extend([float(comp.position.x), float(comp.position.y), float(comp.position.z)])
            return np.asarray(vec, dtype=float)

        def _thermal_evaluator(state: DesignState) -> Dict[str, float]:
            stats["requests_total"] += 1

            def _maybe_log_progress() -> None:
                if (
                    progress_log_interval > 0 and
                    int(stats["requests_total"]) % progress_log_interval == 0
                ):
                    stats["stats_progress_logs"] += 1
                    cache_hits = int(stats["cache_hits"])
                    requests_total = int(stats["requests_total"])
                    hit_rate = (cache_hits / requests_total) if requests_total > 0 else 0.0
                    self.logger.logger.info(
                        "online_comsol evaluator progress: requests=%d, executed_online=%d, "
                        "cache_hits=%d, hit_rate=%.3f, geom_fallback=%d, budget_fallback=%d",
                        requests_total,
                        int(stats["executed_online_comsol"]),
                        cache_hits,
                        float(hit_rate),
                        int(stats["fallback_proxy_geometry_infeasible"]),
                        int(stats["fallback_proxy_budget_exhausted"]),
                    )

            fp = self._state_fingerprint_with_options(
                state,
                position_quantization_mm=cache_quantize_mm,
            )
            cached_payload = cache.get(fp)
            if cached_payload is not None:
                stats["cache_hits"] += 1
                _maybe_log_progress()
                return dict(cached_payload)

            stats["cache_misses"] += 1
            min_clearance_hint: Optional[float] = None
            num_collisions_hint: Optional[int] = None
            if geometry_gate_enabled:
                feasible, min_clearance, num_collisions = self._is_geometry_feasible(state)
                min_clearance_hint = float(min_clearance)
                num_collisions_hint = int(num_collisions)
                if not feasible:
                    stats["fallback_proxy_geometry_infeasible"] += 1
                    cache[fp] = {}
                    self.logger.logger.debug(
                        "online_comsol thermal evaluator skip (geometry infeasible): "
                        "min_clearance=%.3fmm, collisions=%d",
                        float(min_clearance),
                        int(num_collisions),
                    )
                    _maybe_log_progress()
                    return {}

            should_run_online = True
            proxy_payload: Optional[Dict[str, float]] = None
            schedule_mode = str(schedule_control["mode"])
            schedule_top_fraction = float(schedule_control["top_fraction"])
            schedule_min_observations = int(schedule_control["min_observations"])
            schedule_warmup_calls = int(schedule_control["warmup_calls"])
            schedule_explore_prob = float(schedule_control["explore_prob"])
            schedule_uncertainty_weight = float(schedule_control["uncertainty_weight"])
            schedule_uncertainty_scale_mm = max(
                float(schedule_control["uncertainty_scale_mm"]),
                1e-6,
            )

            if schedule_mode == "ucb_topk":
                position_vec = _position_vector(state)
                scores = list(scheduler_state.get("scores", []))
                vectors = list(scheduler_state.get("vectors", []))
                if vectors:
                    dist = min(float(np.linalg.norm(position_vec - prev)) for prev in vectors)
                else:
                    dist = float(schedule_uncertainty_scale_mm)
                uncertainty = float(np.clip(dist / schedule_uncertainty_scale_mm, 0.0, 1.0))
                proxy_payload = _proxy_thermal_payload(
                    state,
                    min_clearance_hint=min_clearance_hint,
                    num_collisions_hint=num_collisions_hint,
                    source="proxy_scheduler",
                    uncertainty=uncertainty,
                )
                thermal_limit = max(float(self.runtime_constraints.get("max_temp_c", 60.0)), 1.0)
                proxy_margin = (thermal_limit - float(proxy_payload["max_temp"])) / thermal_limit
                acq_score = float(proxy_margin + schedule_uncertainty_weight * uncertainty)
                proxy_payload["_schedule_acq_score"] = float(acq_score)

                scores.append(float(acq_score))
                vectors.append(position_vec)
                scheduler_state["scores"] = scores
                scheduler_state["vectors"] = vectors
                stats["scheduler_candidates_seen"] = int(len(scores))

                rank = int(1 + sum(1 for item in scores if float(item) > float(acq_score)))
                k_target = max(1, int(np.ceil(schedule_top_fraction * float(len(scores)))))
                if int(stats["executed_online_comsol"]) < schedule_warmup_calls:
                    should_run_online = True
                    stats["scheduler_selected_warmup"] += 1
                else:
                    rank_enabled = len(scores) >= schedule_min_observations
                    selected_by_rank = bool(rank_enabled and rank <= k_target)
                    selected_by_explore = bool(
                        (not selected_by_rank) and
                        (float(schedule_explore_prob) > 0.0) and
                        (float(scheduler_rng.random()) < float(schedule_explore_prob))
                    )
                    if selected_by_rank:
                        should_run_online = True
                        stats["scheduler_selected_rank"] += 1
                    elif selected_by_explore:
                        should_run_online = True
                        stats["scheduler_selected_explore"] += 1
                    else:
                        should_run_online = False

            if schedule_mode == "ucb_topk" and not should_run_online:
                stats["fallback_proxy_scheduler_skipped"] += 1
                payload = dict(proxy_payload or _proxy_thermal_payload(state, source="proxy_scheduler"))
                cache[fp] = payload
                _maybe_log_progress()
                return payload

            current_budget = int(budget_control.get("eval_budget", 0))
            current_budget = max(current_budget, 0)
            stats["eval_budget"] = int(current_budget)
            if current_budget > 0 and int(stats["executed_online_comsol"]) >= current_budget:
                stats["fallback_proxy_budget_exhausted"] += 1
                if not bool(stats["budget_exhausted"]):
                    stats["budget_exhausted"] = True
                    self.logger.logger.warning(
                        "online_comsol thermal evaluator budget exhausted (%s). "
                        "Remaining candidates fallback to proxy thermal model.",
                        current_budget,
                    )
                if schedule_mode == "ucb_topk":
                    payload = dict(
                        proxy_payload or _proxy_thermal_payload(
                            state,
                            min_clearance_hint=min_clearance_hint,
                            num_collisions_hint=num_collisions_hint,
                            source="proxy_budget_exhausted",
                        )
                    )
                    cache[fp] = payload
                    _maybe_log_progress()
                    return payload
                _maybe_log_progress()
                return {}

            counter["n"] += 1
            stats["executed_online_comsol"] += 1
            eval_iteration = int(base_iteration * 100000 + counter["n"])
            try:
                metrics, _ = self._evaluate_design(state, eval_iteration)
                thermal = metrics["thermal"]
                payload = {
                    "max_temp": float(thermal.max_temp),
                    "min_temp": float(thermal.min_temp),
                    "avg_temp": float(thermal.avg_temp),
                    "_source": "online_comsol",
                }
                cache[fp] = payload
                _maybe_log_progress()
                return payload
            except Exception as exc:
                stats["fallback_proxy_exceptions"] += 1
                self.logger.logger.warning(
                    "online_comsol thermal evaluator failed for candidate; "
                    f"fallback to proxy thermal model: {exc}"
                )
                if schedule_mode == "ucb_topk":
                    payload = dict(
                        proxy_payload or _proxy_thermal_payload(
                            state,
                            min_clearance_hint=min_clearance_hint,
                            num_collisions_hint=num_collisions_hint,
                            source="proxy_exception",
                        )
                    )
                    cache[fp] = payload
                    _maybe_log_progress()
                    return payload
                _maybe_log_progress()
                return {}

        def _set_scheduler_params(
            *,
            mode: Optional[str] = None,
            top_fraction: Optional[float] = None,
            min_observations: Optional[int] = None,
            warmup_calls: Optional[int] = None,
            explore_prob: Optional[float] = None,
            uncertainty_weight: Optional[float] = None,
            uncertainty_scale_mm: Optional[float] = None,
        ) -> Dict[str, Any]:
            updates: Dict[str, Any] = {}
            if mode is not None:
                updates["mode"] = mode
            if top_fraction is not None:
                updates["top_fraction"] = top_fraction
            if min_observations is not None:
                updates["min_observations"] = min_observations
            if warmup_calls is not None:
                updates["warmup_calls"] = warmup_calls
            if explore_prob is not None:
                updates["explore_prob"] = explore_prob
            if uncertainty_weight is not None:
                updates["uncertainty_weight"] = uncertainty_weight
            if uncertainty_scale_mm is not None:
                updates["uncertainty_scale_mm"] = uncertainty_scale_mm

            if not updates:
                return {}

            previous = dict(schedule_control)
            normalized = _sanitize_scheduler_params({**schedule_control, **updates})
            changes: Dict[str, Any] = {}
            for key, old_value in previous.items():
                new_value = normalized[key]
                if old_value != new_value:
                    changes[key] = (old_value, new_value)

            if not changes:
                return {}

            schedule_control.update(normalized)
            stats["schedule_mode"] = str(schedule_control["mode"])
            stats["schedule_top_fraction"] = float(schedule_control["top_fraction"])
            stats["schedule_min_observations"] = int(schedule_control["min_observations"])
            stats["schedule_warmup_calls"] = int(schedule_control["warmup_calls"])
            stats["schedule_explore_prob"] = float(schedule_control["explore_prob"])
            stats["schedule_uncertainty_weight"] = float(schedule_control["uncertainty_weight"])
            stats["schedule_uncertainty_scale_mm"] = float(schedule_control["uncertainty_scale_mm"])

            if "mode" in changes:
                scheduler_state["scores"] = []
                scheduler_state["vectors"] = []
                stats["scheduler_candidates_seen"] = 0

            self.logger.logger.info(
                "online_comsol evaluator scheduler updated: %s",
                changes,
            )
            return changes

        def _get_scheduler_params() -> Dict[str, Any]:
            return {
                "mode": str(schedule_control["mode"]),
                "top_fraction": float(schedule_control["top_fraction"]),
                "min_observations": int(schedule_control["min_observations"]),
                "warmup_calls": int(schedule_control["warmup_calls"]),
                "explore_prob": float(schedule_control["explore_prob"]),
                "uncertainty_weight": float(schedule_control["uncertainty_weight"]),
                "uncertainty_scale_mm": float(schedule_control["uncertainty_scale_mm"]),
            }

        def _set_eval_budget(new_budget: int) -> None:
            parsed = max(int(new_budget), 0)
            old_budget = int(budget_control.get("eval_budget", 0))
            if parsed == old_budget:
                return
            budget_control["eval_budget"] = int(parsed)
            stats["eval_budget"] = int(parsed)
            if parsed <= 0 or int(stats.get("executed_online_comsol", 0)) < parsed:
                stats["budget_exhausted"] = False
            self.logger.logger.info(
                "online_comsol evaluator budget updated: %s -> %s",
                old_budget,
                parsed,
            )

        def _get_eval_budget() -> int:
            return int(budget_control.get("eval_budget", 0))

        _thermal_evaluator.stats = stats  # type: ignore[attr-defined]
        _thermal_evaluator.set_eval_budget = _set_eval_budget  # type: ignore[attr-defined]
        _thermal_evaluator.get_eval_budget = _get_eval_budget  # type: ignore[attr-defined]
        _thermal_evaluator.set_scheduler_params = _set_scheduler_params  # type: ignore[attr-defined]
        _thermal_evaluator.get_scheduler_params = _get_scheduler_params  # type: ignore[attr-defined]
        return _thermal_evaluator

    def _run_maas_topk_physics_audit(
        self,
        execution_result: Any,
        problem_generator: Any,
        base_state: DesignState,
        top_k: int,
        base_iteration: int,
    ) -> tuple[Dict[str, Any], Optional[DesignState]]:
        """
        对最后一轮 Pareto 前沿做 Top-K 物理审计并返回候选状态。
        """
        report: Dict[str, Any] = {
            "enabled": True,
            "requested_top_k": int(top_k),
            "records": [],
            "selected_rank": None,
            "selected_pareto_index": None,
            "selected_reason": "",
        }

        if execution_result is None or problem_generator is None:
            report["enabled"] = False
            report["selected_reason"] = "missing_solver_result_or_problem_generator"
            return report, None

        pareto_x_raw = getattr(execution_result, "pareto_X", None)
        if pareto_x_raw is None:
            report["selected_reason"] = "pareto_X_missing"
            return report, None

        pareto_x = np.asarray(pareto_x_raw, dtype=float)
        if pareto_x.ndim == 1:
            pareto_x = pareto_x.reshape(1, -1)
        if pareto_x.ndim != 2 or pareto_x.shape[0] == 0:
            report["selected_reason"] = "pareto_X_empty"
            return report, None

        pareto_f_raw = getattr(execution_result, "pareto_F", None)
        if pareto_f_raw is not None:
            pareto_f = np.asarray(pareto_f_raw, dtype=float)
            if pareto_f.ndim == 1:
                pareto_f = pareto_f.reshape(1, -1)
            if pareto_f.ndim != 2 or pareto_f.shape[0] != pareto_x.shape[0]:
                pareto_f = np.zeros((pareto_x.shape[0], 1), dtype=float)
        else:
            pareto_f = np.zeros((pareto_x.shape[0], 1), dtype=float)

        selected_indices = select_top_pareto_indices(pareto_f, top_k=top_k)
        if not selected_indices:
            report["selected_reason"] = "no_indices_selected"
            return report, None

        pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
        pareto_cv = None
        if pareto_cv_raw is not None:
            parsed_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
            if parsed_cv.size == pareto_x.shape[0]:
                pareto_cv = parsed_cv

        feasible_selected = 0
        if pareto_cv is not None:
            feasible_bucket: List[int] = []
            infeasible_bucket: List[int] = []
            for idx in selected_indices:
                cv_val = float(pareto_cv[int(idx)])
                if np.isfinite(cv_val) and cv_val <= 1e-9:
                    feasible_bucket.append(int(idx))
                else:
                    infeasible_bucket.append(int(idx))
            infeasible_bucket = sorted(
                infeasible_bucket,
                key=lambda i: float(pareto_cv[i]) if np.isfinite(pareto_cv[i]) else float("inf"),
            )
            selected_indices = feasible_bucket + infeasible_bucket
            feasible_selected = len(feasible_bucket)

        report["selected_indices"] = [int(idx) for idx in selected_indices]
        report["feasible_candidate_count"] = int(feasible_selected)

        best_feasible_state: Optional[DesignState] = None
        best_any_state: Optional[DesignState] = None
        best_feasible_penalty = float("inf")
        best_any_penalty = float("inf")
        best_feasible_meta: tuple[int, int] | None = None
        best_any_meta: tuple[int, int] | None = None

        for rank, pareto_idx in enumerate(selected_indices, start=1):
            record: Dict[str, Any] = {
                "rank": int(rank),
                "pareto_index": int(pareto_idx),
                "success": False,
            }
            if pareto_cv is not None:
                record["solver_cv"] = float(pareto_cv[int(pareto_idx)])
            try:
                candidate = problem_generator.codec.decode(pareto_x[int(pareto_idx)])
                eval_iteration = int(base_iteration * 100 + rank)
                metrics, violations = self._evaluate_design(candidate, eval_iteration)
                penalty = float(self._calculate_penalty_score(metrics, violations))
                record.update({
                    "success": True,
                    "max_temp": float(metrics["thermal"].max_temp),
                    "min_clearance": float(metrics["geometry"].min_clearance),
                    "cg_offset": float(metrics["geometry"].cg_offset_magnitude),
                    "num_collisions": int(metrics["geometry"].num_collisions),
                    "num_violations": int(len(violations)),
                    "penalty_score": penalty,
                })

                if penalty < best_any_penalty:
                    best_any_penalty = penalty
                    best_any_state = candidate.model_copy(deep=True)
                    best_any_meta = (rank, int(pareto_idx))

                if len(violations) == 0 and penalty < best_feasible_penalty:
                    best_feasible_penalty = penalty
                    best_feasible_state = candidate.model_copy(deep=True)
                    best_feasible_meta = (rank, int(pareto_idx))
            except Exception as exc:
                record["error"] = str(exc)
            report["records"].append(record)

        selected_state: Optional[DesignState] = None
        opt_cfg = self.config.get("optimization", {})
        allow_infeasible_fallback = bool(
            opt_cfg.get("pymoo_maas_audit_allow_infeasible_fallback", False)
        )
        if best_feasible_state is not None and best_feasible_meta is not None:
            selected_state = best_feasible_state
            report["selected_rank"] = int(best_feasible_meta[0])
            report["selected_pareto_index"] = int(best_feasible_meta[1])
            report["selected_reason"] = "best_feasible_penalty"
        elif allow_infeasible_fallback and best_any_state is not None and best_any_meta is not None:
            selected_state = best_any_state
            report["selected_rank"] = int(best_any_meta[0])
            report["selected_pareto_index"] = int(best_any_meta[1])
            report["selected_reason"] = "best_any_penalty"
        elif best_any_state is not None:
            report["selected_reason"] = "no_feasible_after_audit"
            report["best_infeasible_penalty"] = float(best_any_penalty)
            return report, None
        else:
            report["selected_reason"] = "all_audit_failed"
            return report, None

        selected_state.parent_id = base_state.state_id
        selected_state.state_id = "state_iter_02_maas_candidate_audited"
        selected_state.iteration = base_iteration + 1
        return report, selected_state

    def _apply_uniform_relaxation_to_intent(
        self,
        intent: ModelingIntent,
        relax_ratio: float,
        tag: str,
    ) -> tuple[ModelingIntent, int]:
        """
        对全部 hard constraints 做统一幅度松弛。
        """
        ratio = max(0.0, float(relax_ratio))
        if ratio <= 0.0:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        applied = 0
        for cons in next_intent.hard_constraints:
            base = float(cons.target_value)
            delta = max(abs(base) * ratio, 0.2)
            if cons.relation == "<=":
                cons.target_value = float(base + delta)
                applied += 1
            elif cons.relation == ">=":
                cons.target_value = float(base - delta)
                applied += 1

        if applied > 0:
            next_intent.assumptions.append(f"{tag}: uniform_relax_count={applied}, ratio={ratio:.4f}")
        return next_intent, applied

    def _apply_objective_focus_to_intent(
        self,
        intent: ModelingIntent,
        focus_keywords: List[str],
        tag: str,
    ) -> tuple[ModelingIntent, int]:
        """
        将目标权重重分配到指定关注方向（温度/质心等）。
        """
        if not focus_keywords:
            return intent, 0

        next_intent = intent.model_copy(deep=True)
        hits = 0
        for obj in next_intent.objectives:
            metric = str(obj.metric_key or "").lower()
            focused = any(k in metric for k in focus_keywords)
            if focused:
                obj.weight = float(max(obj.weight * 1.8, 1e-6))
                hits += 1
            else:
                obj.weight = float(max(obj.weight * 0.85, 1e-6))

        if hits > 0:
            next_intent.assumptions.append(
                f"{tag}: objective_focus={','.join(focus_keywords)}, hits={hits}"
            )
        return next_intent, hits

    def _propose_maas_mcts_variants(
        self,
        node: MCTSNode,
        relax_ratio: float,
    ) -> List[MCTSVariant]:
        """
        为 MCTS 节点生成建模分支候选。
        """
        variants: List[MCTSVariant] = []
        opt_cfg = getattr(self, "config", {}).get("optimization", {})
        enable_operator_program = bool(
            opt_cfg.get("pymoo_maas_enable_operator_program", True)
        )

        # 1) Identity branch
        identity_intent = node.intent.model_copy(deep=True)
        identity_intent.assumptions.append(f"mcts_action=identity_d{node.depth+1}")
        variants.append(MCTSVariant(
            action=f"identity_d{node.depth+1}",
            intent=identity_intent,
            metadata={"source": "identity"},
        ))

        # 2) Operator program branch (R1 baseline)
        if enable_operator_program:
            operator_program = build_operator_program_from_context(
                intent=node.intent,
                depth=node.depth + 1,
                evaluation_payload=(node.evaluation.payload if node.evaluation is not None else None),
                max_components=int(opt_cfg.get("pymoo_maas_operator_program_max_components", 6)),
            )
            operator_intent, operator_report = apply_operator_program_to_intent(
                intent=node.intent,
                program=operator_program,
            )
            if (
                bool(operator_report.get("is_valid", False)) and
                int(operator_report.get("applied_actions", 0)) > 0
            ):
                operator_intent.assumptions.append(
                    "mcts_action=operator_program_d"
                    f"{node.depth+1}, program_id={operator_program.program_id}, "
                    f"applied={int(operator_report.get('applied_actions', 0))}"
                )
                variants.append(MCTSVariant(
                    action=f"operator_program_d{node.depth+1}",
                    intent=operator_intent,
                    metadata={
                        "source": "operator_program",
                        "program_id": operator_program.program_id,
                        "action_sequence": [
                            str(action.action) for action in list(operator_program.actions or [])
                        ],
                        "operator_program": dict(operator_report.get("program", {}) or {}),
                        "applied": int(operator_report.get("applied_actions", 0)),
                        "warnings": list(operator_report.get("warnings", []) or []),
                        "dominant_violation": str(
                            operator_program.metadata.get("dominant_violation", "")
                        ),
                    },
                ))

        # 3) Reflection-based relaxation from parent evaluation, if available
        if node.evaluation is not None:
            suggestions = list(node.evaluation.payload.get("relaxation_suggestions", []) or [])
            reflection_intent, reflection_applied = self._apply_relaxation_suggestions_to_intent(
                intent=node.intent,
                suggestions=suggestions,
                attempt=node.depth + 1,
            )
            if reflection_applied > 0:
                reflection_intent.assumptions.append(
                    f"mcts_action=reflection_relax_d{node.depth+1}, applied={reflection_applied}"
                )
                variants.append(MCTSVariant(
                    action=f"reflection_relax_d{node.depth+1}",
                    intent=reflection_intent,
                    metadata={"source": "reflection", "applied": reflection_applied},
                ))

        # 4) Uniform relaxation
        uniform_intent, uniform_applied = self._apply_uniform_relaxation_to_intent(
            intent=node.intent,
            relax_ratio=relax_ratio * 0.5,
            tag=f"mcts_uniform_relax_d{node.depth+1}",
        )
        if uniform_applied > 0:
            variants.append(MCTSVariant(
                action=f"uniform_relax_d{node.depth+1}",
                intent=uniform_intent,
                metadata={"source": "uniform_relax", "applied": uniform_applied},
            ))

        # 5) CG-focused objective reweight
        cg_focus_intent, cg_hits = self._apply_objective_focus_to_intent(
            intent=node.intent,
            focus_keywords=["cg", "centroid", "com_offset"],
            tag=f"mcts_cg_focus_d{node.depth+1}",
        )
        if cg_hits > 0:
            variants.append(MCTSVariant(
                action=f"cg_focus_d{node.depth+1}",
                intent=cg_focus_intent,
                metadata={"source": "objective_focus", "hits": cg_hits},
            ))

        # 6) Thermal-focused objective reweight
        thermal_focus_intent, thermal_hits = self._apply_objective_focus_to_intent(
            intent=node.intent,
            focus_keywords=["temp", "thermal", "hotspot"],
            tag=f"mcts_thermal_focus_d{node.depth+1}",
        )
        if thermal_hits > 0:
            variants.append(MCTSVariant(
                action=f"thermal_focus_d{node.depth+1}",
                intent=thermal_focus_intent,
                metadata={"source": "objective_focus", "hits": thermal_hits},
            ))

        if len(variants) <= 4:
            return variants

        # Keep branch diversity while ensuring CG branch is not truncated out.
        prioritized_prefixes = (
            "identity_d",
            "operator_program_d",
            "uniform_relax_d",
            "cg_focus_d",
            "reflection_relax_d",
            "thermal_focus_d",
        )
        selected: List[MCTSVariant] = []
        selected_actions: set[str] = set()

        for prefix in prioritized_prefixes:
            for variant in variants:
                if variant.action in selected_actions:
                    continue
                if variant.action.startswith(prefix):
                    selected.append(variant)
                    selected_actions.add(variant.action)
                    break
            if len(selected) >= 4:
                return selected[:4]

        for variant in variants:
            if variant.action in selected_actions:
                continue
            selected.append(variant)
            selected_actions.add(variant.action)
            if len(selected) >= 4:
                break

        return selected[:4]

    @staticmethod
    def _normalize_operator_action_name(action: Any) -> str:
        normalized = str(action or "").strip().lower()
        return normalized

    def _operator_credit_table(self) -> Dict[str, Dict[str, Any]]:
        table = getattr(self, "_maas_operator_credit_stats", None)
        if not isinstance(table, dict):
            table = {}
            self._maas_operator_credit_stats = table
        return table

    def _extract_operator_action_sequence(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
        attempt_payload: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        metadata = dict(branch_metadata or {})
        payload = dict(attempt_payload or {})
        source = str(metadata.get("source", payload.get("branch_source", "")) or "").strip().lower()
        action_name = str(branch_action or payload.get("branch_action", "")).strip().lower()
        if source != "operator_program" and not action_name.startswith("operator_program_d"):
            return []

        candidates = list(metadata.get("action_sequence", []) or [])
        if not candidates:
            candidates = list(payload.get("operator_actions", []) or [])

        operator_program = dict(metadata.get("operator_program", {}) or {})
        if not candidates:
            for action in list(operator_program.get("actions", []) or []):
                if isinstance(action, dict):
                    name = self._normalize_operator_action_name(action.get("action", ""))
                else:
                    name = self._normalize_operator_action_name(action)
                if name:
                    candidates.append(name)

        unique_actions: List[str] = []
        seen_actions: set[str] = set()
        for item in candidates:
            name = self._normalize_operator_action_name(item)
            if not name or name in seen_actions:
                continue
            seen_actions.add(name)
            unique_actions.append(name)
        return unique_actions

    def _summarize_operator_credit(
        self,
        *,
        action_sequence: List[str],
    ) -> Dict[str, Any]:
        table = self._operator_credit_table()
        if not action_sequence:
            return {}

        stats = [dict(table[action]) for action in action_sequence if action in table]
        if not stats:
            return {}

        observations = int(sum(int(item.get("count", 0) or 0) for item in stats))
        if observations <= 0:
            return {}

        weighted_score = 0.0
        weighted_feasible = 0.0
        weighted_cv = 0.0
        cv_weight = 0
        best_cv = float("inf")
        for item in stats:
            count = int(item.get("count", 0) or 0)
            mean_score = float(item.get("mean_score", 0.0) or 0.0)
            feasible_rate = float(item.get("feasible_rate", 0.0) or 0.0)
            weighted_score += float(count) * mean_score
            weighted_feasible += float(count) * feasible_rate

            mean_cv = item.get("mean_best_cv", None)
            if mean_cv is not None:
                try:
                    parsed_mean_cv = float(mean_cv)
                    if np.isfinite(parsed_mean_cv):
                        weighted_cv += float(count) * parsed_mean_cv
                        cv_weight += count
                except Exception:
                    pass

            item_best_cv = item.get("best_cv", None)
            if item_best_cv is not None:
                try:
                    parsed_best_cv = float(item_best_cv)
                    if np.isfinite(parsed_best_cv):
                        best_cv = min(best_cv, parsed_best_cv)
                except Exception:
                    pass

        return {
            "observations": int(observations),
            "mean_score": float(weighted_score / max(float(observations), 1.0)),
            "feasible_rate": float(weighted_feasible / max(float(observations), 1.0)),
            "mean_best_cv": (
                float(weighted_cv / max(float(cv_weight), 1.0))
                if cv_weight > 0
                else None
            ),
            "best_cv": float(best_cv) if np.isfinite(best_cv) else None,
            "action_count": int(len(stats)),
        }

    def _update_operator_credit_from_attempt(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
        attempt_payload: Optional[Dict[str, Any]] = None,
        score: float = 0.0,
    ) -> None:
        payload = dict(attempt_payload or {})
        actions = self._extract_operator_action_sequence(
            branch_action=branch_action,
            branch_metadata=branch_metadata,
            attempt_payload=payload,
        )
        if not actions:
            return

        diagnosis = dict(payload.get("diagnosis", {}) or {})
        status = str(diagnosis.get("status", "")).strip().lower()
        is_feasible = status in {"feasible", "feasible_but_stalled"}
        best_cv_raw = payload.get("best_cv", None)
        best_cv: Optional[float] = None
        if best_cv_raw is not None:
            try:
                parsed = float(best_cv_raw)
                if np.isfinite(parsed):
                    best_cv = max(parsed, 0.0)
            except Exception:
                best_cv = None

        table = self._operator_credit_table()
        for action in actions:
            stat = table.get(action)
            if stat is None:
                stat = {
                    "action": action,
                    "count": 0,
                    "sum_score": 0.0,
                    "mean_score": 0.0,
                    "best_score": float("-inf"),
                    "feasible_count": 0,
                    "feasible_rate": 0.0,
                    "best_cv": None,
                    "cv_count": 0,
                    "sum_best_cv": 0.0,
                    "mean_best_cv": None,
                    "last_score": None,
                    "last_status": "",
                    "last_attempt": None,
                }
                table[action] = stat

            stat["count"] = int(stat.get("count", 0) or 0) + 1
            stat["sum_score"] = float(stat.get("sum_score", 0.0) or 0.0) + float(score)
            stat["mean_score"] = float(stat["sum_score"]) / float(stat["count"])
            stat["best_score"] = max(float(stat.get("best_score", float("-inf"))), float(score))
            if is_feasible:
                stat["feasible_count"] = int(stat.get("feasible_count", 0) or 0) + 1
            stat["feasible_rate"] = (
                float(stat["feasible_count"]) / max(float(stat["count"]), 1.0)
            )

            if best_cv is not None:
                prev_best_cv = stat.get("best_cv", None)
                if prev_best_cv is None:
                    stat["best_cv"] = float(best_cv)
                else:
                    try:
                        prev_numeric = float(prev_best_cv)
                        stat["best_cv"] = float(min(prev_numeric, float(best_cv)))
                    except Exception:
                        stat["best_cv"] = float(best_cv)
                stat["cv_count"] = int(stat.get("cv_count", 0) or 0) + 1
                stat["sum_best_cv"] = float(stat.get("sum_best_cv", 0.0) or 0.0) + float(best_cv)
                stat["mean_best_cv"] = float(stat["sum_best_cv"]) / float(stat["cv_count"])

            stat["last_score"] = float(score)
            stat["last_status"] = status
            try:
                stat["last_attempt"] = int(payload.get("attempt", None))
            except Exception:
                stat["last_attempt"] = None

    def _build_operator_bias_from_branch(
        self,
        *,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Compile branch/operator metadata into runner-level operator bias config.
        """
        metadata = dict(branch_metadata or {})
        source = str(metadata.get("source", "") or "").strip().lower()
        action_name = str(branch_action or "").strip().lower()
        if source != "operator_program" and not action_name.startswith("operator_program_d"):
            return {}
        opt_cfg = getattr(self, "config", {}).get("optimization", {})
        enable_credit_bias = bool(
            opt_cfg.get("pymoo_maas_enable_operator_credit_bias", True)
        )

        unique_actions = self._extract_operator_action_sequence(
            branch_action=branch_action,
            branch_metadata=metadata,
            attempt_payload=None,
        )
        operator_program = dict(metadata.get("operator_program", {}) or {})

        bias: Dict[str, Any] = {
            "enabled": True,
            "strategy": "operator_program",
            "source": source or "operator_program",
            "branch_action": str(branch_action or ""),
            "program_id": str(
                metadata.get("program_id", "") or operator_program.get("program_id", "")
            ),
            "action_sequence": unique_actions,
            "sampling_sigma_ratio": 0.02,
            "sampling_jitter_count": 4,
            "crossover_prob": 0.90,
            "crossover_eta": 15.0,
            "mutation_prob": None,
            "mutation_eta": 20.0,
            "repair_push_ratio": None,
            "repair_cg_nudge_ratio": None,
            "repair_max_passes": None,
            "credit_bias_enabled": bool(enable_credit_bias),
        }

        if "hot_spread" in unique_actions:
            bias["sampling_sigma_ratio"] = max(float(bias["sampling_sigma_ratio"]), 0.04)
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 8)
            bias["mutation_prob"] = 0.28
            bias["mutation_eta"] = 14.0
            bias["crossover_eta"] = 12.0

        if "swap" in unique_actions:
            bias["mutation_prob"] = max(float(bias.get("mutation_prob") or 0.0), 0.30)
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 10)
            bias["crossover_prob"] = 0.95

        if "cg_recenter" in unique_actions:
            bias["sampling_sigma_ratio"] = min(float(bias["sampling_sigma_ratio"]), 0.02)
            bias["mutation_prob"] = 0.12
            bias["mutation_eta"] = 26.0
            bias["repair_cg_nudge_ratio"] = 1.15
            bias["repair_max_passes"] = 3

        if "group_move" in unique_actions:
            bias["sampling_jitter_count"] = max(int(bias["sampling_jitter_count"]), 6)
            if bias.get("mutation_prob", None) is None:
                bias["mutation_prob"] = 0.18

        credit_summary = self._summarize_operator_credit(action_sequence=unique_actions)
        if credit_summary:
            bias["credit_summary"] = dict(credit_summary)
        if enable_credit_bias and credit_summary:
            observations = int(credit_summary.get("observations", 0) or 0)
            feasible_rate = float(credit_summary.get("feasible_rate", 0.0) or 0.0)
            mean_best_cv = credit_summary.get("mean_best_cv", None)

            if observations >= 2:
                if feasible_rate >= 0.50:
                    bias["sampling_sigma_ratio"] = max(
                        0.01,
                        float(bias["sampling_sigma_ratio"]) * 0.85,
                    )
                    bias["sampling_jitter_count"] = max(
                        3,
                        int(bias["sampling_jitter_count"]) - 1,
                    )
                    if bias.get("mutation_prob", None) is not None:
                        bias["mutation_prob"] = max(
                            0.10,
                            float(bias["mutation_prob"]) * 0.85,
                        )
                    bias["mutation_eta"] = min(float(bias["mutation_eta"]) + 4.0, 45.0)
                elif feasible_rate <= 0.20:
                    bias["sampling_sigma_ratio"] = min(
                        0.09,
                        max(float(bias["sampling_sigma_ratio"]), 0.03) * 1.25,
                    )
                    bias["sampling_jitter_count"] = min(
                        16,
                        int(bias["sampling_jitter_count"]) + 2,
                    )
                    if bias.get("mutation_prob", None) is None:
                        bias["mutation_prob"] = 0.24
                    else:
                        bias["mutation_prob"] = min(
                            0.40,
                            max(float(bias["mutation_prob"]), 0.24),
                        )
                    bias["mutation_eta"] = max(float(bias["mutation_eta"]) - 3.0, 10.0)

            if mean_best_cv is not None:
                try:
                    parsed_cv = float(mean_best_cv)
                    if np.isfinite(parsed_cv) and parsed_cv > 0.0 and "cg_recenter" in unique_actions:
                        current = bias.get("repair_cg_nudge_ratio", None)
                        current_numeric = float(current) if current is not None else 1.0
                        bias["repair_cg_nudge_ratio"] = max(current_numeric, 1.15)
                except Exception:
                    pass

        return bias

    def _score_maas_attempt_result(
        self,
        diagnosis: Dict[str, Any],
        execution_result: Any,
        decoded_state: Optional[DesignState],
        attempt_payload: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        给单次 MaaS 求解结果打分，用于 MCTS 回传值。
        """
        payload = attempt_payload if isinstance(attempt_payload, dict) else {}
        status = str(diagnosis.get("status", ""))
        score_breakdown: Dict[str, float] = {
            "status_base": float({
                "feasible": 1500.0,
                "feasible_but_stalled": 1100.0,
                "no_feasible": 320.0,
                "empty_solution": 180.0,
                "runtime_error": -520.0,
                "missing_result": -320.0,
            }.get(status, -220.0))
        }

        if execution_result is not None:
            score_breakdown["aocc_cv_bonus"] = float(getattr(execution_result, "aocc_cv", 0.0) or 0.0) * 140.0
            score_breakdown["aocc_obj_bonus"] = float(
                getattr(execution_result, "aocc_objective", 0.0) or 0.0
            ) * 90.0
            best_cv_curve = list(getattr(execution_result, "best_cv_curve", []) or [])
            if best_cv_curve:
                best_cv = float(np.min(np.asarray(best_cv_curve, dtype=float)))
                if np.isfinite(best_cv):
                    score_breakdown["best_cv_penalty"] = -max(best_cv, 0.0) * 65.0

        if decoded_state is not None:
            try:
                geom = self._evaluate_geometry(decoded_state)
                score_breakdown["clearance_bonus"] = min(float(geom.min_clearance), 30.0) * 3.0
                score_breakdown["cg_penalty"] = -float(geom.cg_offset_magnitude) * 1.8
                score_breakdown["collision_penalty"] = -float(geom.num_collisions) * 320.0
            except Exception:
                pass

        if payload:
            attempt = payload.get("attempt", None)
            attempt_idx = None
            try:
                attempt_idx = int(attempt)
            except Exception:
                attempt_idx = None

            solver_cost_raw = payload.get("solver_cost", None)
            try:
                solver_cost = float(solver_cost_raw)
            except Exception:
                solver_cost = 0.0
            if np.isfinite(solver_cost) and solver_cost > 0.0:
                score_breakdown["solver_cost_penalty"] = -min(solver_cost, 1800.0) * 0.15

            is_feasible = status in {"feasible", "feasible_but_stalled"}
            if is_feasible and attempt_idx is not None and attempt_idx > 0:
                score_breakdown["first_feasible_bonus"] = max(0.0, 8.0 - float(attempt_idx)) * 95.0

            online_calls = payload.get("online_comsol_calls_so_far", None)
            if online_calls is None:
                runtime_snapshot = dict(payload.get("runtime_thermal_snapshot", {}) or {})
                online_calls = runtime_snapshot.get("executed_online_comsol", None)
            if online_calls is not None:
                try:
                    calls = max(int(online_calls), 0)
                    if is_feasible:
                        score_breakdown["comsol_efficiency_bonus"] = max(0.0, 40.0 - float(calls)) * 8.0
                    elif calls > 0:
                        score_breakdown["comsol_burn_penalty"] = -min(float(calls), 120.0) * 1.2
                except Exception:
                    pass

        score = float(sum(float(v) for v in score_breakdown.values()))
        if isinstance(attempt_payload, dict):
            attempt_payload["score_breakdown"] = {
                key: float(value) for key, value in score_breakdown.items()
            }
            attempt_payload["score_total"] = float(score)
        return float(score)

    def _resolve_pymoo_algorithm(self) -> str:
        """
        Resolve configured pymoo multi-objective algorithm.

        Supported values:
        - nsga2 (default)
        - nsga3
        - moead
        """
        opt_cfg = self.config.get("optimization", {})
        requested = str(opt_cfg.get("pymoo_algorithm", "nsga2")).strip().lower()
        if requested not in {"nsga2", "nsga3", "moead"}:
            self.logger.logger.warning(
                "Unknown pymoo_algorithm=%s, fallback to nsga2",
                requested,
            )
            return "nsga2"
        return requested

    def _create_pymoo_runner(
        self,
        *,
        pop_size: int,
        n_generations: int,
        seed: int,
        repair: Optional[Any],
        verbose: bool,
        return_least_infeasible: bool,
        initial_population: Optional[np.ndarray],
        operator_bias: Optional[Dict[str, Any]] = None,
    ) -> Any:
        opt_cfg = self.config.get("optimization", {})
        algorithm_key = self._resolve_pymoo_algorithm()
        shared_kwargs: Dict[str, Any] = {
            "pop_size": int(pop_size),
            "n_generations": int(n_generations),
            "seed": int(seed),
            "repair": repair,
            "verbose": bool(verbose),
            "return_least_infeasible": bool(return_least_infeasible),
            "initial_population": initial_population,
            "operator_bias": dict(operator_bias or {}),
            "nsga3_ref_dirs_partitions": int(
                opt_cfg.get("pymoo_nsga3_ref_dirs_partitions", 0)
            ),
            "moead_n_neighbors": int(
                opt_cfg.get("pymoo_moead_n_neighbors", 20)
            ),
            "moead_prob_neighbor_mating": float(
                opt_cfg.get("pymoo_moead_prob_neighbor_mating", 0.9)
            ),
            "moead_constraint_penalty": float(
                opt_cfg.get("pymoo_moead_constraint_penalty", 1000.0)
            ),
        }

        if algorithm_key == "nsga3":
            return PymooNSGA3Runner(**shared_kwargs)
        if algorithm_key == "moead":
            return PymooMOEADRunner(**shared_kwargs)
        return PymooNSGA2Runner(**shared_kwargs)

    def _resolve_maas_search_space_mode(self) -> str:
        """
        Resolve pymoo_maas search-space mode.

        Supported modes:
        - coordinate
        - operator_program
        - hybrid
        """
        opt_cfg = self.config.get("optimization", {})
        mode = str(
            opt_cfg.get("pymoo_maas_search_space", "coordinate")
        ).strip().lower()
        if mode not in {"coordinate", "operator_program", "hybrid"}:
            self.logger.logger.warning(
                "Unknown pymoo_maas_search_space=%s, fallback to coordinate",
                mode,
            )
            return "coordinate"
        return mode

    def _create_maas_problem_generator(
        self,
        *,
        spec: Any,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, str]:
        """
        Create problem generator according to configured MaaS search space.
        """
        mode = self._resolve_maas_search_space_mode()
        metadata = dict(branch_metadata or {})
        branch_source = str(metadata.get("source", "")).strip().lower()
        action_name = str(branch_action or "").strip().lower()

        use_operator_program = False
        resolved_mode = mode
        if mode == "operator_program":
            use_operator_program = True
        elif mode == "hybrid":
            use_operator_program = (
                branch_source == "operator_program" or
                action_name.startswith("operator_program_d")
            )
            resolved_mode = (
                "hybrid_operator_program"
                if use_operator_program
                else "hybrid_coordinate"
            )

        if use_operator_program:
            opt_cfg = self.config.get("optimization", {})
            n_action_slots = max(
                1,
                int(opt_cfg.get("pymoo_maas_operator_program_action_slots", 3)),
            )
            max_group_delta_mm = float(
                opt_cfg.get("pymoo_maas_operator_program_max_group_delta_mm", 10.0)
            )
            max_hot_distance_mm = float(
                opt_cfg.get("pymoo_maas_operator_program_max_hot_distance_mm", 12.0)
            )
            action_safety_tolerance = float(
                opt_cfg.get("pymoo_maas_operator_program_action_safety_tolerance", 0.5)
            )
            return (
                OperatorProgramProblemGenerator(
                    spec=spec,
                    n_action_slots=n_action_slots,
                    max_group_delta_mm=max_group_delta_mm,
                    max_hot_distance_mm=max_hot_distance_mm,
                    action_safety_tolerance=action_safety_tolerance,
                ),
                resolved_mode,
            )

        return PymooProblemGenerator(spec=spec), resolved_mode

    def _build_maas_seed_population(
        self,
        *,
        problem_generator: PymooProblemGenerator,
        current_state: DesignState,
    ) -> np.ndarray:
        """
        构建 NSGA-II 初始种群注入向量。

        策略:
        1) 始终注入当前布局（warm-start）。
        2) 追加整体平移种子（不改变相对布局）以快速降低 CG 偏移。
        3) 在高维场景追加重组件定向种子，提升大规模 BOM 可行化概率。
        """
        opt_cfg = self.config.get("optimization", {})
        if not bool(opt_cfg.get("pymoo_maas_enable_seed_population", True)):
            seed = problem_generator.codec.clip(problem_generator.codec.encode(current_state))
            return np.asarray([seed], dtype=float)

        codec = problem_generator.codec
        base_state = current_state.model_copy(deep=True)
        base_vector = codec.clip(codec.encode(base_state))
        seeds: List[np.ndarray] = [base_vector]

        seed_population_max = max(
            1,
            int(opt_cfg.get("pymoo_maas_seed_population_max", 8)),
        )
        component_threshold = max(
            2,
            int(opt_cfg.get("pymoo_maas_cg_seed_component_threshold", 12)),
        )

        try:
            centers, half_sizes = codec.geometry_arrays_from_state(base_state)
            masses = np.asarray(
                [max(float(comp.mass), 1e-9) for comp in base_state.components],
                dtype=float,
            )
            if centers.shape[0] == 0:
                return np.asarray([base_vector], dtype=float)

            env_min, env_max = codec.envelope_bounds
            lower_centers = env_min.reshape(1, 3) + half_sizes
            upper_centers = env_max.reshape(1, 3) - half_sizes
            comp_index = {comp.id: idx for idx, comp in enumerate(base_state.components)}
            axis_index = {"x": 0, "y": 1, "z": 2}
            for spec in codec.variable_specs:
                comp_i = comp_index.get(spec.component_id)
                axis_i = axis_index.get(spec.axis)
                if comp_i is None or axis_i is None:
                    continue
                lower_centers[comp_i, axis_i] = max(
                    float(lower_centers[comp_i, axis_i]),
                    float(spec.lower_bound),
                )
                upper_centers[comp_i, axis_i] = min(
                    float(upper_centers[comp_i, axis_i]),
                    float(spec.upper_bound),
                )
            lower_centers = np.minimum(lower_centers, upper_centers)

            # 每轴允许的全局平移区间（保证所有组件不越界）。
            shift_low = np.max(lower_centers - centers, axis=0)
            shift_high = np.min(upper_centers - centers, axis=0)

            total_mass = float(np.sum(masses))
            if total_mass <= 1e-9:
                return np.asarray([base_vector], dtype=float)

            if base_state.envelope.origin == "center":
                target_center = np.zeros(3, dtype=float)
            else:
                target_center = np.asarray(
                    [
                        float(base_state.envelope.outer_size.x) * 0.5,
                        float(base_state.envelope.outer_size.y) * 0.5,
                        float(base_state.envelope.outer_size.z) * 0.5,
                    ],
                    dtype=float,
                )

            com = np.sum(centers * masses.reshape(-1, 1), axis=0) / total_mass
            com_offset_vec = com - target_center

            # A) 全局平移种子：保持相对布局，优先快速拉回 CG。
            desired_shift = -com_offset_vec
            feasible_shift = np.clip(desired_shift, shift_low, shift_high)
            shift_norm = float(np.linalg.norm(feasible_shift))
            if shift_norm > 1e-9:
                for ratio in (0.35, 0.70, 1.00):
                    shifted_centers = centers + feasible_shift.reshape(1, 3) * ratio
                    state_shifted = base_state.model_copy(deep=True)
                    for idx, comp in enumerate(state_shifted.components):
                        comp.position.x = float(shifted_centers[idx, 0])
                        comp.position.y = float(shifted_centers[idx, 1])
                        comp.position.z = float(shifted_centers[idx, 2])
                    seeds.append(codec.clip(codec.encode(state_shifted)))

            # B) 重组件定向种子：在高组件数场景提升 CG 收敛概率。
            if (
                centers.shape[0] >= component_threshold and
                float(np.linalg.norm(com_offset_vec)) > 1e-9
            ):
                heavy_count = min(max(2, centers.shape[0] // 4), centers.shape[0])
                heavy_idx = np.argsort(-masses)[:heavy_count]
                heavy_mass = float(np.sum(masses[heavy_idx]))
                if heavy_mass > 1e-9:
                    ideal_delta = -(total_mass / heavy_mass) * com_offset_vec
                    for ratio in (0.20, 0.35, 0.50):
                        candidate_centers = np.array(centers, dtype=float, copy=True)
                        candidate_centers[heavy_idx, :] += ideal_delta.reshape(1, 3) * ratio
                        candidate_centers = np.minimum(
                            np.maximum(candidate_centers, lower_centers),
                            upper_centers,
                        )
                        state_shifted = base_state.model_copy(deep=True)
                        for idx, comp in enumerate(state_shifted.components):
                            comp.position.x = float(candidate_centers[idx, 0])
                            comp.position.y = float(candidate_centers[idx, 1])
                            comp.position.z = float(candidate_centers[idx, 2])
                        seeds.append(codec.clip(codec.encode(state_shifted)))
        except Exception as exc:
            self.logger.logger.warning("MaaS seed population generation failed, fallback warm-start: %s", exc)
            seeds = [base_vector]

        unique_seeds: List[np.ndarray] = []
        seen: set[tuple[float, ...]] = set()
        for vec in seeds:
            key = tuple(np.round(np.asarray(vec, dtype=float), 6).tolist())
            if key in seen:
                continue
            seen.add(key)
            unique_seeds.append(np.asarray(vec, dtype=float))
            if len(unique_seeds) >= seed_population_max:
                break

        return np.asarray(unique_seeds, dtype=float)

    def _extract_maas_candidate_diagnostics(
        self,
        *,
        execution_result: Any,
        problem_generator: Optional[Any],
    ) -> Dict[str, Any]:
        """
        从求解结果中提取候选主导违规项，供日志与 meta policy 分析使用。
        """
        payload: Dict[str, Any] = {
            "dominant_violation": "",
            "constraint_violation_breakdown": {},
            "best_candidate_metrics": {},
        }
        if execution_result is None or problem_generator is None:
            return payload

        try:
            pareto_x = np.asarray(getattr(execution_result, "pareto_X", np.empty((0, 0))), dtype=float)
            if pareto_x.size == 0:
                return payload
            if pareto_x.ndim == 1:
                pareto_x = pareto_x.reshape(1, -1)

            pareto_cv_raw = getattr(execution_result, "pareto_CV", None)
            if pareto_cv_raw is not None:
                pareto_cv = np.asarray(pareto_cv_raw, dtype=float).reshape(-1)
                if pareto_cv.size == pareto_x.shape[0] and np.any(np.isfinite(pareto_cv)):
                    best_idx = int(np.nanargmin(pareto_cv))
                else:
                    best_idx = 0
            else:
                best_idx = 0

            candidate_state = problem_generator.codec.decode(pareto_x[best_idx, :])
            evaluated = problem_generator.evaluate_state(candidate_state)
            metrics = dict(evaluated.get("metrics") or {})
            constraints = dict(evaluated.get("constraints") or {})

            violation_breakdown: Dict[str, float] = {}
            for name, value in constraints.items():
                numeric = float(value)
                if np.isfinite(numeric) and numeric > 0.0:
                    violation_breakdown[str(name)] = float(numeric)
            dominant_violation = ""
            if violation_breakdown:
                dominant_violation = max(
                    violation_breakdown.items(),
                    key=lambda item: float(item[1]),
                )[0]

            payload["dominant_violation"] = str(dominant_violation)
            payload["constraint_violation_breakdown"] = violation_breakdown
            payload["best_candidate_metrics"] = {
                "cg_offset": float(metrics.get("cg_offset", 0.0)),
                "max_temp": float(metrics.get("max_temp", 0.0)),
                "min_clearance": float(metrics.get("min_clearance", 0.0)),
                "num_collisions": float(metrics.get("num_collisions", 0.0)),
                "boundary_violation": float(metrics.get("boundary_violation", 0.0)),
            }
            return payload
        except Exception as exc:
            self.logger.logger.warning("extract_maas_candidate_diagnostics failed: %s", exc)
            return payload

    def _evaluate_maas_intent_once(
        self,
        *,
        iteration: int,
        attempt: int,
        intent: ModelingIntent,
        branch_action: str,
        branch_metadata: Optional[Dict[str, Any]],
        current_state: DesignState,
        runtime_thermal_evaluator: Any,
        pop_size: int,
        n_generations: int,
        seed: int,
        verbose: bool,
        return_least_infeasible: bool,
        maas_relax_ratio: float,
        thermal_evaluator_mode: str,
    ) -> Dict[str, Any]:
        """
        执行单次 intent 编译/求解/诊断并返回结构化结果。
        """
        branch_meta = dict(branch_metadata or {})
        operator_bias = self._build_operator_bias_from_branch(
            branch_action=branch_action,
            branch_metadata=branch_meta,
        )
        formulation_report = formulate_modeling_intent(intent)
        self.logger.log_llm_interaction(
            iteration=iteration,
            role=f"model_agent_formulation_attempt_{attempt:02d}",
            request={
                "intent_id": intent.intent_id,
                "branch_action": branch_action,
                "branch_source": str(branch_meta.get("source", "")),
                "operator_program_id": str(branch_meta.get("program_id", "")),
            },
            response=formulation_report,
        )

        spec, compile_report = compile_intent_to_problem_spec(
            intent=intent,
            base_state=current_state,
            runtime_constraints=self.runtime_constraints,
            thermal_evaluator=runtime_thermal_evaluator,
            enable_semantic_zones=bool(
                self.config.get("optimization", {}).get("pymoo_maas_enable_semantic_zones", True)
            ),
        )

        execution_result = None
        solver_exception = None
        solver_cost = 0.0
        problem_generator = None
        search_space_mode = self._resolve_maas_search_space_mode()
        try:
            solver_tic = time.perf_counter()
            problem_generator, search_space_mode = self._create_maas_problem_generator(
                spec=spec,
                branch_action=branch_action,
                branch_metadata=branch_meta,
            )
            problem = problem_generator.create_problem()
            opt_cfg = self.config.get("optimization", {})
            enable_coordinate_repair = search_space_mode in {"coordinate", "hybrid_coordinate"}
            repair = None
            if enable_coordinate_repair:
                repair = CentroidPushApartRepair(
                    codec=problem_generator.codec,
                    cg_limit_mm=float(problem_generator.max_cg_offset_mm),
                    cg_nudge_ratio=float(opt_cfg.get("pymoo_maas_repair_cg_nudge_ratio", 0.90)),
                )

            if bool(opt_cfg.get("pymoo_maas_enable_seed_population", True)):
                if enable_coordinate_repair:
                    seed_population = self._build_maas_seed_population(
                        problem_generator=problem_generator,
                        current_state=current_state,
                    )
                else:
                    codec = problem_generator.codec
                    if (
                        hasattr(codec, "build_seed_population") and
                        bool(opt_cfg.get("pymoo_maas_enable_operator_seed_population", True))
                    ):
                        try:
                            seed_population = np.asarray(
                                codec.build_seed_population(
                                    reference_state=current_state,
                                    max_count=max(
                                        1,
                                        int(opt_cfg.get("pymoo_maas_seed_population_max", 8)),
                                    ),
                                ),
                                dtype=float,
                            )
                        except Exception:
                            seed_population = np.asarray(
                                [
                                    codec.clip(
                                        codec.encode(current_state)
                                    )
                                ],
                                dtype=float,
                            )
                    else:
                        seed_population = np.asarray(
                            [
                                codec.clip(
                                    codec.encode(current_state)
                                )
                            ],
                            dtype=float,
                        )
            else:
                seed_population = None
            runner = self._create_pymoo_runner(
                pop_size=pop_size,
                n_generations=n_generations,
                seed=seed + (attempt - 1),
                repair=repair,
                verbose=verbose,
                return_least_infeasible=return_least_infeasible,
                initial_population=seed_population,
                operator_bias=operator_bias,
            )
            execution_result = runner.run(problem)
            solver_cost = time.perf_counter() - solver_tic
        except Exception as exc:
            solver_exception = str(exc)
            self.logger.logger.warning(f"pymoo solve phase failed: {exc}")

        if execution_result is not None:
            diagnosis = diagnose_solver_outcome(execution_result)
            relaxation_suggestions = suggest_constraint_relaxation(
                intent,
                diagnosis,
                max_relax_ratio=maas_relax_ratio,
            )
        else:
            diagnosis = {
                "status": "runtime_error",
                "reason": "solver_exception",
                "traceback": solver_exception or "",
            }
            relaxation_suggestions = []

        best_cv = float("inf")
        aocc_cv = 0.0
        aocc_obj = 0.0
        if execution_result is not None:
            curve = list(getattr(execution_result, "best_cv_curve", []) or [])
            if curve:
                try:
                    best_cv = float(np.min(np.asarray(curve, dtype=float)))
                except Exception:
                    best_cv = float("inf")
            aocc_cv = float(getattr(execution_result, "aocc_cv", 0.0) or 0.0)
            aocc_obj = float(getattr(execution_result, "aocc_objective", 0.0) or 0.0)

        decoded_state = self._decode_maas_candidate_state(
            execution_result=execution_result,
            problem_generator=problem_generator,
            base_state=current_state,
            attempt=attempt,
        )
        candidate_diagnostics = self._extract_maas_candidate_diagnostics(
            execution_result=execution_result,
            problem_generator=problem_generator,
        )

        runtime_thermal_snapshot: Dict[str, Any] = {}
        online_comsol_calls_so_far: Optional[int] = None
        if runtime_thermal_evaluator is not None:
            try:
                runtime_thermal_snapshot = dict(
                    getattr(runtime_thermal_evaluator, "stats", {}) or {}
                )
            except Exception:
                runtime_thermal_snapshot = {}
            if runtime_thermal_snapshot:
                try:
                    online_comsol_calls_so_far = int(
                        runtime_thermal_snapshot.get("executed_online_comsol", 0) or 0
                    )
                except Exception:
                    online_comsol_calls_so_far = None

        attempt_payload: Dict[str, Any] = {
            "attempt": attempt,
            "intent_id": intent.intent_id,
            "branch_action": branch_action,
            "branch_source": str(branch_meta.get("source", "")),
            "pymoo_algorithm_requested": self._resolve_pymoo_algorithm(),
            "search_space_mode": str(search_space_mode),
            "operator_program_id": str(branch_meta.get("program_id", "")),
            "operator_actions": list(branch_meta.get("action_sequence", []) or []),
            "operator_bias": dict(operator_bias or {}),
            "thermal_evaluator_mode": thermal_evaluator_mode,
            "formulation": formulation_report,
            "compile_report": compile_report.to_dict(),
            "diagnosis": diagnosis,
            "relaxation_suggestions": relaxation_suggestions,
            "solver_message": execution_result.message if execution_result is not None else solver_exception,
            "solver_metadata": (
                dict(getattr(execution_result, "metadata", {}) or {})
                if execution_result is not None else {}
            ),
            "solver_cost": float(solver_cost),
            "has_candidate_state": decoded_state is not None,
            "relaxation_applied_count": 0,
            "score": 0.0,
            "best_cv": best_cv if np.isfinite(best_cv) else None,
            "aocc_cv": float(aocc_cv),
            "aocc_objective": float(aocc_obj),
            "dominant_violation": str(candidate_diagnostics.get("dominant_violation", "")),
            "constraint_violation_breakdown": dict(
                candidate_diagnostics.get("constraint_violation_breakdown", {}) or {}
            ),
            "best_candidate_metrics": dict(
                candidate_diagnostics.get("best_candidate_metrics", {}) or {}
            ),
        }

        generation_records: List[Dict[str, Any]] = []
        if execution_result is not None:
            try:
                generation_records = list(
                    (getattr(execution_result, "metadata", {}) or {}).get("generation_records", [])
                    or []
                )
            except Exception:
                generation_records = []
        if generation_records:
            self.logger.log_maas_generation_events(
                {
                    "iteration": int(iteration),
                    "attempt": int(attempt),
                    "branch_action": str(branch_action),
                    "branch_source": str(branch_meta.get("source", "")),
                    "search_space_mode": str(search_space_mode),
                    "pymoo_algorithm": str(self._resolve_pymoo_algorithm()),
                    "records": generation_records,
                }
            )

        if runtime_thermal_snapshot:
            attempt_payload["runtime_thermal_snapshot"] = {
                key: value for key, value in runtime_thermal_snapshot.items()
            }
        if online_comsol_calls_so_far is not None:
            attempt_payload["online_comsol_calls_so_far"] = int(online_comsol_calls_so_far)

        score = self._score_maas_attempt_result(
            diagnosis=diagnosis,
            execution_result=execution_result,
            decoded_state=decoded_state,
            attempt_payload=attempt_payload,
        )
        attempt_payload["score"] = float(score)

        self._update_operator_credit_from_attempt(
            branch_action=branch_action,
            branch_metadata=branch_meta,
            attempt_payload=attempt_payload,
            score=float(score),
        )
        attempt_payload["operator_credit_snapshot"] = self._summarize_operator_credit(
            action_sequence=self._extract_operator_action_sequence(
                branch_action=branch_action,
                branch_metadata=branch_meta,
                attempt_payload=attempt_payload,
            )
        )

        return {
            "intent": intent,
            "formulation_report": formulation_report,
            "compile_report": compile_report.to_dict(),
            "execution_result": execution_result,
            "solver_exception": solver_exception,
            "solver_cost": float(solver_cost),
            "problem_generator": problem_generator,
            "diagnosis": diagnosis,
            "relaxation_suggestions": relaxation_suggestions,
            "decoded_state": decoded_state,
            "attempt_payload": attempt_payload,
            "score": float(score),
        }

    def _run_pymoo_maas_pipeline(
        self,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        pymoo_maas 入口：委托给 MaaSPipelineService 执行闭环建模流程。
        """
        return self.maas_pipeline_service.run_pipeline(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
        )

    def _run_pymoo_maas_phase_a(
        self,
        current_state: DesignState,
        bom_file: Optional[str],
        max_iterations: int,
        convergence_threshold: float,
    ) -> DesignState:
        """
        兼容旧入口，转发到新的 pymoo_maas 闭环实现。
        """
        return self._run_pymoo_maas_pipeline(
            current_state=current_state,
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
        )

    def _build_maas_requirement_text(self, bom_file: Optional[str]) -> str:
        """构建供 Modeling Agent 使用的任务文本。"""
        lines = [
            "目标: 生成可执行建模意图（Variables/Objectives/Hard/Soft Constraints）。",
            "硬约束必须可映射到 g(x)<=0 或 h(x)=0。",
            f"运行时硬约束: {json.dumps(self.runtime_constraints, ensure_ascii=False)}",
        ]

        if bom_file:
            path = Path(bom_file)
            lines.append(f"BOM路径: {path.as_posix()}")
            if path.exists() and path.suffix.lower() in {".json", ".yaml", ".yml"}:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        if path.suffix.lower() == ".json":
                            payload = json.load(f)
                        else:
                            payload = yaml.safe_load(f)
                    if isinstance(payload, dict):
                        constraints = payload.get("constraints", {})
                        components = payload.get("components", [])
                        lines.append(
                            f"BOM约束: {json.dumps(constraints, ensure_ascii=False)}"
                        )
                        lines.append(f"BOM组件数: {len(components) if isinstance(components, list) else 0}")
                except Exception as exc:
                    lines.append(f"BOM解析失败: {exc}")

        return "\n".join(lines)

    def _initialize_design_state(self, bom_file: Optional[str]) -> DesignState:
        """初始化设计状态"""
        component_props_by_id: Dict[str, Dict[str, Any]] = {}
        if bom_file:
            # 从BOM文件加载
            from core.bom_parser import BOMParser

            self.logger.logger.info(f"Loading BOM from: {bom_file}")
            constraint_override, component_props_by_id = self._extract_bom_overrides(bom_file)
            if constraint_override:
                self.runtime_constraints.update(constraint_override)
                self.logger.logger.info(
                    "BOM constraints override applied: "
                    f"T<= {self.runtime_constraints['max_temp_c']:.2f}°C, "
                    f"clearance>= {self.runtime_constraints['min_clearance_mm']:.2f}mm, "
                    f"CG<= {self.runtime_constraints['max_cg_offset_mm']:.2f}mm"
                )

            bom_components = BOMParser.parse(bom_file)

            # 验证BOM
            errors = BOMParser.validate(bom_components)
            if errors:
                raise ValueError(f"BOM验证失败: {errors}")

            self.logger.logger.info(f"BOM loaded: {len(bom_components)} components")

            # 更新layout_engine的配置
            # 将BOM组件转换为layout_engine需要的格式
            geom_config = self.config.get('geometry', {})
            geom_config['components'] = []

            for bom_comp in bom_components:
                for i in range(bom_comp.quantity):
                    comp_id = f"{bom_comp.id}_{i+1:02d}" if bom_comp.quantity > 1 else bom_comp.id
                    geom_config['components'].append({
                        'id': comp_id,
                        'dims_mm': [
                            bom_comp.dimensions['x'],
                            bom_comp.dimensions['y'],
                            bom_comp.dimensions['z']
                        ],
                        'mass_kg': bom_comp.mass,
                        'power_w': bom_comp.power,
                        'category': bom_comp.category
                    })

            # 重新初始化layout_engine
            from geometry.layout_engine import LayoutEngine
            self.layout_engine = LayoutEngine(config=geom_config)

        # 设置随机种子以确保布局可重复
        import random
        import numpy as np
        random.seed(42)
        np.random.seed(42)

        # 使用默认布局
        packing_result = self.layout_engine.generate_layout()

        # 转换为DesignState
        components = []
        for part in packing_result.placed:
            pos_min = part.get_actual_position()
            dims = np.array([float(part.dims[0]), float(part.dims[1]), float(part.dims[2])], dtype=float)
            # LayoutEngine 输出的是最小角坐标；系统其他模块统一使用中心点坐标。
            center_pos = pos_min + dims / 2.0
            comp_props = component_props_by_id.get(part.id, {})
            comp_geom = ComponentGeometry(
                id=part.id,
                position=Vector3D(
                    x=float(center_pos[0]),
                    y=float(center_pos[1]),
                    z=float(center_pos[2])
                ),
                dimensions=Vector3D(x=float(part.dims[0]), y=float(part.dims[1]), z=float(part.dims[2])),
                rotation=Vector3D(x=0, y=0, z=0),
                mass=part.mass,
                power=part.power,
                category=part.category if hasattr(part, 'category') else 'unknown',
                thermal_contacts=comp_props.get("thermal_contacts", {}) or {},
                emissivity=(
                    float(comp_props.get("emissivity"))
                    if comp_props.get("emissivity") is not None
                    else 0.8
                ),
                absorptivity=(
                    float(comp_props.get("absorptivity"))
                    if comp_props.get("absorptivity") is not None
                    else 0.3
                ),
                coating_type=comp_props.get("coating_type") or "default",
            )
            components.append(comp_geom)

        # 创建envelope信息
        from core.protocol import Envelope
        envelope_geom = self.layout_engine.envelope
        outer_size = envelope_geom.outer_size()
        inner_size = envelope_geom.inner_size()
        envelope = Envelope(
            outer_size=Vector3D(
                x=float(outer_size[0]),
                y=float(outer_size[1]),
                z=float(outer_size[2])
            ),
            inner_size=Vector3D(
                x=float(inner_size[0]),
                y=float(inner_size[1]),
                z=float(inner_size[2])
            ),
            thickness=float(envelope_geom.thickness_mm),
            fill_ratio=envelope_geom.fill_ratio,
            origin="center"
        )

        design_state = DesignState(
            iteration=0,
            components=components,
            envelope=envelope,
            state_id="state_iter_00_init",  # Phase 4: 初始状态ID
            parent_id=None
        )

        return design_state

    def _evaluate_design(
        self,
        design_state: DesignState,
        iteration: int
    ) -> tuple[Dict[str, Any], list[ViolationItem]]:
        """评估设计状态"""
        # 1. 几何评估
        geometry_metrics = self._evaluate_geometry(design_state)

        # 2. 仿真评估
        from core.protocol import SimulationRequest, SimulationType

        # 2.1 COMSOL 后端统一使用动态导入模式，先导出 STEP 文件
        sim_params = {}
        sim_config = self.config.get("simulation", {})
        if sim_config.get("backend") == "comsol":
            step_file = self._export_design_to_step(design_state, iteration)
            sim_params["step_file"] = str(step_file)
            self.logger.logger.info(f"  导出STEP文件用于动态仿真: {step_file}")

        # 传递实验目录，用于保存 .mph 模型文件
        sim_params["experiment_dir"] = str(self.logger.run_dir)

        sim_request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters=sim_params
        )

        sim_start = time.perf_counter()
        sim_result = self.sim_driver.run_simulation(sim_request)
        solver_cost = time.perf_counter() - sim_start
        sim_raw_data = dict(sim_result.raw_data or {})
        mph_model_path = str(sim_raw_data.get("mph_model_path", "") or "")

        thermal_metrics = ThermalMetrics(
            max_temp=sim_result.metrics.get("max_temp", 0),
            min_temp=sim_result.metrics.get("min_temp", 0),
            avg_temp=sim_result.metrics.get("avg_temp", 0),
            temp_gradient=sim_result.metrics.get("temp_gradient", 0)
        )

        # 3. 结构评估（简化）
        structural_metrics = StructuralMetrics(
            max_stress=50.0,
            max_displacement=0.1,
            first_modal_freq=60.0,
            safety_factor=2.2
        )

        # 4. 电源评估（简化）
        total_power = sum(c.power for c in design_state.components)
        power_metrics = PowerMetrics(
            total_power=total_power,
            peak_power=total_power * 1.2,
            power_margin=25.0,
            voltage_drop=0.3
        )

        # 5. 检查约束违反
        violations = self._check_violations(
            geometry_metrics,
            thermal_metrics,
            structural_metrics,
            power_metrics
        )

        metrics = {
            "geometry": geometry_metrics,
            "thermal": thermal_metrics,
            "structural": structural_metrics,
            "power": power_metrics,
            "diagnostics": {
                "solver_cost": solver_cost,
                "simulation_success": bool(sim_result.success),
                "mph_model_path": mph_model_path,
            }
        }

        return metrics, violations

    def _export_design_to_step(self, design_state: DesignState, iteration: int) -> Path:
        """
        导出设计状态为STEP文件（用于动态COMSOL仿真）
        使用 OpenCASCADE 生成真实的 BREP 实体

        Args:
            design_state: 设计状态
            iteration: 当前迭代次数

        Returns:
            STEP文件路径
        """
        from geometry.cad_export_occ import export_design_occ
        from pathlib import Path

        # 创建临时目录
        temp_dir = Path(self.logger.run_dir) / "step_files"
        temp_dir.mkdir(parents=True, exist_ok=True)

        step_file = temp_dir / f"design_iter_{iteration:03d}.step"

        # 使用 OpenCASCADE 导出真实 STEP 文件
        export_design_occ(design_state, str(step_file))

        return step_file

    def _evaluate_geometry(self, design_state: DesignState) -> GeometryMetrics:
        """评估几何指标"""
        from simulation.structural_physics import (
            calculate_cg_offset,
            calculate_moment_of_inertia,
            calculate_center_of_mass
        )

        # 计算质心偏移
        cg_offset = calculate_cg_offset(design_state)

        # 计算质心位置（向量）
        com = calculate_center_of_mass(design_state)
        com_offset_vector = [com.x, com.y, com.z]

        # 计算转动惯量
        moi = calculate_moment_of_inertia(design_state)

        min_clearance, num_collisions = self._calculate_pairwise_clearance(design_state)

        return GeometryMetrics(
            min_clearance=min_clearance,
            com_offset=com_offset_vector,
            cg_offset_magnitude=cg_offset,
            moment_of_inertia=list(moi),
            packing_efficiency=75.0,  # TODO: 实现真实的装填率计算
            num_collisions=num_collisions
        )

    def _calculate_pairwise_clearance(self, design_state: DesignState) -> tuple[float, int]:
        """
        计算组件两两间最小净间隙与碰撞对数（基于中心点坐标 + 轴对齐包围盒）。

        Returns:
            (min_clearance_mm, num_collisions)
        """
        if len(design_state.components) < 2:
            return float("inf"), 0

        min_signed_clearance = float("inf")
        collision_pairs = 0

        comps = design_state.components
        for i in range(len(comps)):
            a = comps[i]
            ax, ay, az = a.position.x, a.position.y, a.position.z
            ahx, ahy, ahz = a.dimensions.x / 2.0, a.dimensions.y / 2.0, a.dimensions.z / 2.0

            for j in range(i + 1, len(comps)):
                b = comps[j]
                bx, by, bz = b.position.x, b.position.y, b.position.z
                bhx, bhy, bhz = b.dimensions.x / 2.0, b.dimensions.y / 2.0, b.dimensions.z / 2.0

                sep_x = abs(ax - bx) - (ahx + bhx)
                sep_y = abs(ay - by) - (ahy + bhy)
                sep_z = abs(az - bz) - (ahz + bhz)

                if sep_x <= 0 and sep_y <= 0 and sep_z <= 0:
                    # 重叠：将“最小间隙”记为负值，幅度为最浅穿透深度。
                    penetration = min(-sep_x, -sep_y, -sep_z)
                    signed_clearance = -penetration
                    collision_pairs += 1
                else:
                    gap_x = max(sep_x, 0.0)
                    gap_y = max(sep_y, 0.0)
                    gap_z = max(sep_z, 0.0)
                    signed_clearance = float((gap_x ** 2 + gap_y ** 2 + gap_z ** 2) ** 0.5)

                min_signed_clearance = min(min_signed_clearance, signed_clearance)

        return min_signed_clearance, collision_pairs

    def _is_geometry_feasible(self, design_state: DesignState) -> tuple[bool, float, int]:
        """
        几何可行性快速判定（用于仿真前门控与动作缩放）。

        判据：
        - 无碰撞对（num_collisions == 0）
        - 最小净间隙不低于运行时阈值
        """
        min_clearance, num_collisions = self._calculate_pairwise_clearance(design_state)
        min_clearance_limit = float(self.runtime_constraints.get("min_clearance_mm", 3.0))
        feasible = num_collisions == 0 and min_clearance >= (min_clearance_limit - 1e-6)
        return feasible, float(min_clearance), int(num_collisions)

    def _state_fingerprint(self, design_state: DesignState) -> tuple:
        """
        生成设计状态指纹，用于检测 no-op / 零变化执行。
        """
        return self._state_fingerprint_with_options(design_state, position_quantization_mm=0.0)

    def _state_fingerprint_with_options(
        self,
        design_state: DesignState,
        position_quantization_mm: float = 0.0,
    ) -> tuple:
        """
        生成设计状态指纹（可选位置量化），用于检测 no-op / 零变化执行。

        Args:
            design_state: 设计状态
            position_quantization_mm: >0 时按该步长量化位置坐标
        """
        quant_step = max(float(position_quantization_mm), 0.0)

        def _round_float(value: Any, *, quantize_position: bool = False) -> float:
            numeric = float(value)
            if quantize_position and quant_step > 0.0 and np.isfinite(numeric):
                numeric = float(np.round(numeric / quant_step) * quant_step)
            return round(numeric, 6)

        comp_fp = []
        for comp in design_state.components:
            thermal_contacts = tuple(
                sorted(
                    (str(k), _round_float(v))
                    for k, v in (getattr(comp, "thermal_contacts", {}) or {}).items()
                )
            )
            heatsink = tuple(
                sorted((str(k), str(v)) for k, v in (getattr(comp, "heatsink", {}) or {}).items())
            )
            bracket = tuple(
                sorted((str(k), str(v)) for k, v in (getattr(comp, "bracket", {}) or {}).items())
            )
            comp_fp.append(
                (
                    comp.id,
                    _round_float(comp.position.x, quantize_position=True),
                    _round_float(comp.position.y, quantize_position=True),
                    _round_float(comp.position.z, quantize_position=True),
                    _round_float(comp.dimensions.x),
                    _round_float(comp.dimensions.y),
                    _round_float(comp.dimensions.z),
                    _round_float(comp.rotation.x),
                    _round_float(comp.rotation.y),
                    _round_float(comp.rotation.z),
                    str(getattr(comp, "envelope_type", "box")),
                    _round_float(getattr(comp, "emissivity", 0.8)),
                    _round_float(getattr(comp, "absorptivity", 0.3)),
                    str(getattr(comp, "coating_type", "default")),
                    thermal_contacts,
                    heatsink,
                    bracket,
                )
            )
        return tuple(sorted(comp_fp, key=lambda x: x[0]))

    def _is_cg_violation(self, violation: Any) -> bool:
        """判断违规项是否属于质心偏移违规。"""
        violation_id = str(getattr(violation, "violation_id", ""))
        description = str(getattr(violation, "description", ""))
        return (
            violation_id.startswith("V_CG") or
            ("质心" in description) or
            ("cg" in description.lower())
        )

    def _is_cg_only_violation(self, violations: list[ViolationItem]) -> bool:
        """是否仅剩质心违规（可触发平台期定向策略）。"""
        return bool(violations) and all(self._is_cg_violation(v) for v in violations)

    def _is_cg_plateau(
        self,
        iteration: int,
        current_snapshot: Dict[str, float],
        violations: list[ViolationItem],
        window: int = 4
    ) -> bool:
        """
        检测是否进入“单违规 + 小改进平台期”。

        判据：
        - 仅剩 CG 违规
        - 最近 `window` 轮违规数量不变
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
        CG 平台期的确定性局部搜索。

        思路：
        - 在 CG 主导方向上对重型组件做小步坐标搜索（带几何可行性预检）
        - 先用几何评估挑选最优候选，再做一次真实仿真验证
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
            f"⚙ 触发 CG 平台期救援: cg={current_cg:.2f}mm, COM=({com_offset[0]:.2f},{com_offset[1]:.2f},{com_offset[2]:.2f})"
        )

        for axis, axis_value in axes:
            if abs(axis_value) < 1e-6:
                continue
            # 当 COM 在 +axis 方向时，沿 -axis 方向移动组件可降低该分量，反之亦然。
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
            self.logger.logger.info("⚠ CG 平台期救援未找到可行候选，回退到常规策略")
            return None

        self.logger.logger.info(
            "  CG 救援候选: "
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
                "⚠ CG 平台期救援候选被拒绝: "
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
        """检查约束违反"""
        violations = []
        min_clearance_limit = self.runtime_constraints.get("min_clearance_mm", 3.0)
        max_cg_offset_limit = self.runtime_constraints.get("max_cg_offset_mm", 20.0)
        max_temp_limit = self.runtime_constraints.get("max_temp_c", 60.0)
        min_safety_factor = self.runtime_constraints.get("min_safety_factor", 2.0)

        # 几何约束
        if geometry_metrics.min_clearance < min_clearance_limit:
            violations.append(ViolationItem(
                violation_id=f"V_GEOM_{len(violations)}",
                violation_type="geometry",
                severity="major",
                description="最小间隙不足",
                affected_components=[],
                metric_value=geometry_metrics.min_clearance,
                threshold=min_clearance_limit
            ))

        if geometry_metrics.num_collisions > 0:
            violations.append(ViolationItem(
                violation_id=f"V_COLLISION_{len(violations)}",
                violation_type="geometry",
                severity="critical",
                description="存在组件几何重叠",
                affected_components=[],
                metric_value=float(geometry_metrics.num_collisions),
                threshold=0.0
            ))

        # 质心偏移约束
        if geometry_metrics.cg_offset_magnitude > max_cg_offset_limit:
            violations.append(ViolationItem(
                violation_id=f"V_CG_{len(violations)}",
                violation_type="geometry",
                severity="major",
                description="质心偏移过大，影响姿态控制",
                affected_components=[],
                metric_value=geometry_metrics.cg_offset_magnitude,
                threshold=max_cg_offset_limit
            ))

        # 热控约束
        if thermal_metrics.max_temp > max_temp_limit:
            violations.append(ViolationItem(
                violation_id=f"V_THERM_{len(violations)}",
                violation_type="thermal",
                severity="critical",
                description="温度超标",
                affected_components=[],
                metric_value=thermal_metrics.max_temp,
                threshold=max_temp_limit
            ))

        # 结构约束
        if structural_metrics.safety_factor < min_safety_factor:
            violations.append(ViolationItem(
                violation_id=f"V_STRUCT_{len(violations)}",
                violation_type="structural",
                severity="critical",
                description="安全系数不足",
                affected_components=[],
                metric_value=structural_metrics.safety_factor,
                threshold=min_safety_factor
            ))

        return violations

    def _build_global_context(
        self,
        iteration: int,
        design_state: DesignState,
        metrics: Dict[str, Any],
        violations: list[ViolationItem]
    ) -> GlobalContextPack:
        """构建全局上下文"""
        # Phase 4: 构建历史摘要和回退警告
        history_summary = f"第{iteration}次迭代"
        if self.rollback_count > 0:
            history_summary += f"（已回退{self.rollback_count}次）"

        # RAG检索相关知识
        context_pack = GlobalContextPack(
            iteration=iteration,
            design_state_summary=(
                f"设计包含{len(design_state.components)}个组件。"
                f"当前硬约束: 温度≤{self.runtime_constraints.get('max_temp_c', 60.0):.2f}°C, "
                f"最小间隙≥{self.runtime_constraints.get('min_clearance_mm', 3.0):.2f}mm, "
                f"质心偏移≤{self.runtime_constraints.get('max_cg_offset_mm', 20.0):.2f}mm"
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
                f"最近失败: {self.recent_failures[-1]}"
            )
            if hasattr(context_pack, 'rollback_warning'):
                context_pack.rollback_warning = rollback_warning

        # 检索知识
        retrieved_knowledge = self.rag_system.retrieve(context_pack, top_k=3)
        context_pack.retrieved_knowledge = retrieved_knowledge

        return context_pack

    def _inject_runtime_constraints_to_plan(self, strategic_plan) -> None:
        """
        将运行时硬约束注入到 StrategicPlan 的任务中，避免 Agent 使用过期阈值。
        """
        if not strategic_plan or not getattr(strategic_plan, "tasks", None):
            return

        limits = {
            "max_temp_c": float(self.runtime_constraints.get("max_temp_c", 60.0)),
            "min_clearance_mm": float(self.runtime_constraints.get("min_clearance_mm", 3.0)),
            "max_cg_offset_mm": float(self.runtime_constraints.get("max_cg_offset_mm", 20.0)),
            "min_safety_factor": float(self.runtime_constraints.get("min_safety_factor", 2.0)),
        }
        hard_constraint_text = (
            "硬约束(必须满足): "
            f"max_temp<= {limits['max_temp_c']:.2f}°C, "
            f"min_clearance>= {limits['min_clearance_mm']:.2f}mm, "
            f"cg_offset<= {limits['max_cg_offset_mm']:.2f}mm"
        )

        for task in strategic_plan.tasks:
            if not isinstance(task.context, dict):
                task.context = {}
            task.context.setdefault("constraint_limits", limits.copy())
            task.context.setdefault("max_temp_limit_c", limits["max_temp_c"])
            task.context.setdefault("min_clearance_limit_mm", limits["min_clearance_mm"])
            task.context.setdefault("max_cg_offset_limit_mm", limits["max_cg_offset_mm"])

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
            current_state: 当前设计状态

        Returns:
            新的设计状态
        """
        import copy

        # 深拷贝当前状态
        new_state = copy.deepcopy(current_state)
        start_fingerprint = self._state_fingerprint(current_state)
        requested_targets = 0
        executed_actions = 0
        effective_actions = 0

        # 如果execution_plan为空，直接返回
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

        # 收集所有需要执行的操作（来自 geometry_proposal 和 thermal_proposal）
        all_actions = []

        # 提取几何操作
        geometry_proposal = getattr(execution_plan, 'geometry_proposal', None)
        if geometry_proposal and hasattr(geometry_proposal, 'actions') and geometry_proposal.actions:
            self.logger.logger.info(f"  📐 几何提案包含 {len(geometry_proposal.actions)} 个操作")
            all_actions.extend(geometry_proposal.actions)

        # 提取热学操作（DV2.0 关键修复：打通 thermal_proposal 数据流）
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

                # 获取目标组件（支持 component_id 或 target_components）
                component_id = getattr(action, 'component_id', None)
                target_components = getattr(action, 'target_components', None)

                # 如果是批量操作（target_components），对每个组件执行
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
                "  ⚠ 执行完成但状态未发生变化（no-op），后续将跳过候选态仿真评估"
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
            new_state: 设计状态（会被修改）
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

        # 记录操作前的状态（强力日志追踪）
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

        # 执行不同类型的操作
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
            # 目标：避免大步长 MOVE 把候选态直接推入碰撞/间隙违规区。
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
                # 全部步长不可行，回滚位置并标记 no-op
                if axis == "X":
                    comp_ref.position.x = original_value
                elif axis == "Y":
                    comp_ref.position.y = original_value
                else:
                    comp_ref.position.z = original_value

                if last_probe:
                    _, _, probe_clearance, probe_collisions = last_probe
                    self.logger.logger.warning(
                        "    ⚠ MOVE 被几何门控拒绝: 所有缩放步长均不可行 "
                        f"(最后探测 min_clearance={probe_clearance:.2f}mm, "
                        f"collisions={probe_collisions})"
                    )
                else:
                    self.logger.logger.warning("    ⚠ MOVE 被几何门控拒绝: 未找到可行步长")
                return False

            self.logger.logger.info(
                f"    MOVE 自适应应用: {axis} 轴 {accepted_delta:.2f} mm "
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

            self.logger.logger.info(f"    旋转 {axis} 轴 {angle:.2f} 度")

        elif op_type == "SWAP":
            # 交换两个组件的位置
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
                self.logger.logger.info(f"    交换 {component_id} 和 {component_b} 的位置")
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

            # 计算包围盒
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

            # 创建FFD变形器
            ffd = FFDDeformer(nx=3, ny=3, nz=3)
            lattice = ffd.create_lattice(bbox_min, bbox_max, margin=0.1)

            # 根据变形类型设置控制点位移
            displacements = {}

            if deform_type == "stretch_x":
                # 沿X轴拉伸：移动右侧控制点
                for j in range(3):
                    for k in range(3):
                        displacements[(2, j, k)] = np.array([magnitude, 0, 0])
                # 更新组件尺寸
                new_state.components[comp_idx].dimensions.x += magnitude

            elif deform_type == "stretch_y":
                # 沿Y轴拉伸
                for i in range(3):
                    for k in range(3):
                        displacements[(i, 2, k)] = np.array([0, magnitude, 0])
                new_state.components[comp_idx].dimensions.y += magnitude

            elif deform_type == "stretch_z":
                # 沿Z轴拉伸
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
                                # 外侧控制点
                                direction = np.array([
                                    (i - 1) * scale,
                                    (j - 1) * scale,
                                    (k - 1) * scale
                                ])
                                displacements[(i, j, k)] = direction
                # 膨胀会增加所有维度
                new_state.components[comp_idx].dimensions.x += magnitude * 0.5
                new_state.components[comp_idx].dimensions.y += magnitude * 0.5
                new_state.components[comp_idx].dimensions.z += magnitude * 0.5

            self.logger.logger.info(f"    ✓ FFD变形完成，新尺寸: {new_state.components[comp_idx].dimensions}")

        elif op_type == "REPACK":
            # 重新装箱
            strategy = parameters.get("strategy", "greedy")
            clearance = parameters.get(
                "clearance",
                self.config.get("geometry", {}).get("clearance_mm", 5.0)
            )

            self.logger.logger.info(f"    重新装箱: strategy={strategy}, clearance={clearance}")

            # 调用layout_engine重新布局
            # 注意：这会重置所有组件位置
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

            self.logger.logger.info(f"    ✓ 重新装箱完成")

        # === DV2.0: 热学算子 ===
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
                # 初始化 thermal_contacts 字典（如果不存在）
                if not hasattr(new_state.components[comp_idx], 'thermal_contacts') or \
                   new_state.components[comp_idx].thermal_contacts is None:
                    new_state.components[comp_idx].thermal_contacts = {}

                new_state.components[comp_idx].thermal_contacts[contact_component] = conductance

                self.logger.logger.info(
                    f"    🔗 接触热阻: {component_id} ↔ {contact_component}, "
                    f"h={conductance} W/m²·K, gap={gap}mm"
                )
            else:
                self.logger.logger.warning(f"    SET_THERMAL_CONTACT 缺少 contact_component 参数")

        elif op_type == "ADD_HEATSINK":
            # 添加散热器（记录到组件属性，实际几何在 CAD 导出时生成）
            face = parameters.get("face", "+Y")
            thickness = parameters.get("thickness", 2.0)  # mm
            conductivity = parameters.get("conductivity", 400.0)  # W/m·K (铜)

            new_state.components[comp_idx].heatsink = {
                "face": face,
                "thickness": thickness,
                "conductivity": conductivity
            }

            self.logger.logger.info(
                f"    🧊 散热器添加: {component_id} face={face}, thickness={thickness}mm, k={conductivity} W/m·K"
            )

        elif op_type == "ADD_BRACKET":
            # 添加结构支架（记录到组件属性，实际几何在 CAD 导出时生成）
            height = parameters.get("height", 20.0)  # mm
            material = parameters.get("material", "aluminum")
            attach_face = parameters.get("attach_face", "-Z")

            new_state.components[comp_idx].bracket = {
                "height": height,
                "material": material,
                "attach_face": attach_face
            }

            # 支架会改变组件的有效Z位置（如果是底部支架）
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
                # 查找参考组件
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
                        f"    📐 对齐: {component_id} 沿 {axis} 轴对齐到 {reference_component}"
                    )
                else:
                    self.logger.logger.warning(f"    参考组件 {reference_component} 未找到")
            else:
                self.logger.logger.warning(f"    ALIGN 缺少 reference_component 参数")

        elif op_type == "CHANGE_ENVELOPE":
            # 包络切换（Box → Cylinder 等）
            # 这个操作修改组件的包络类型，CAD 导出时会生成对应几何
            shape = parameters.get("shape", "box")
            dimensions = parameters.get("dimensions", {})

            # 更新组件的包络类型
            new_state.components[comp_idx].envelope_type = shape

            # 如果提供了新尺寸，更新组件尺寸
            if dimensions:
                if "x" in dimensions:
                    new_state.components[comp_idx].dimensions.x = dimensions["x"]
                if "y" in dimensions:
                    new_state.components[comp_idx].dimensions.y = dimensions["y"]
                if "z" in dimensions:
                    new_state.components[comp_idx].dimensions.z = dimensions["z"]
                # 圆柱体特殊参数
                if "radius" in dimensions:
                    # 圆柱体：X/Y 设为直径
                    diameter = dimensions["radius"] * 2
                    new_state.components[comp_idx].dimensions.x = diameter
                    new_state.components[comp_idx].dimensions.y = diameter
                if "height" in dimensions:
                    new_state.components[comp_idx].dimensions.z = dimensions["height"]

            self.logger.logger.info(
                f"    📦 包络切换: {component_id} → {shape}"
            )

        else:
            self.logger.logger.warning(f"    未知操作类型: {op_type}")

        # 记录操作后的状态（强力日志追踪）
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
                f"[{old_pos[0]:.2f}, {old_pos[1]:.2f}, {old_pos[2]:.2f}] → "
                f"[{new_pos[0]:.2f}, {new_pos[1]:.2f}, {new_pos[2]:.2f}]"
            )
        if old_dims != new_dims:
            self.logger.logger.info(
                f"    📐 {component_id} 尺寸变化: "
                f"[{old_dims[0]:.2f}, {old_dims[1]:.2f}, {old_dims[2]:.2f}] → "
                f"[{new_dims[0]:.2f}, {new_dims[1]:.2f}, {new_dims[2]:.2f}]"
            )
        if old_rot != new_rot:
            self.logger.logger.info(
                f"    🔄 {component_id} 旋转变化: "
                f"[{old_rot[0]:.2f}, {old_rot[1]:.2f}, {old_rot[2]:.2f}] → "
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
        判断是否接受新状态（违规数量 + 惩罚分双判据）。

        allow_penalty_regression:
            允许在平台期接受“小幅惩罚上升”的候选，用于穿越局部最优。
        require_cg_improve_on_regression:
            当发生惩罚上升时，要求 CG 必须实质改善，避免无效放宽。
        """
        old_count = len(old_violations)
        new_count = len(new_violations)

        # 一级判据：违规数量必须不增加
        if new_count < old_count:
            return True
        if new_count > old_count:
            return False

        # 二级判据：违规数量相同时，惩罚分不能恶化
        old_penalty = self._calculate_penalty_score(old_metrics, old_violations)
        new_penalty = self._calculate_penalty_score(new_metrics, new_violations)
        tolerance = max(float(allow_penalty_regression), 0.0)
        if new_penalty <= old_penalty + max(1e-6, tolerance):
            # 平台期放宽仅在“确实换来 CG 改善”时才生效
            if (
                tolerance > 1e-9 and
                new_penalty > old_penalty + 1e-6 and
                require_cg_improve_on_regression
            ):
                old_cg = float(old_metrics["geometry"].cg_offset_magnitude)
                new_cg = float(new_metrics["geometry"].cg_offset_magnitude)
                if new_cg >= old_cg - 0.5:
                    self.logger.logger.info(
                        "  拒绝新状态: 虽在放宽窗口内，但 CG 改善不足 "
                        f"({old_cg:.2f} -> {new_cg:.2f})"
                    )
                    return False

                self.logger.logger.info(
                    "  平台期受控接受: 允许小幅惩罚上升以换取 CG 改善 "
                    f"(penalty {old_penalty:.2f} -> {new_penalty:.2f}, "
                    f"cg {old_cg:.2f} -> {new_cg:.2f})"
                )
            return True

        self.logger.logger.info(
            "  拒绝新状态: 违规数未减少且惩罚分恶化 "
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
        """生成最终报告"""
        self.logger.logger.info(f"\n{'='*60}")
        self.logger.logger.info("Optimization Complete")
        self.logger.logger.info(f"{'='*60}")
        self.logger.logger.info(f"Total iterations: {iterations}")
        self.logger.logger.info(f"Final design: {len(final_state.components)} components")
        self.logger.logger.info(f"Total rollbacks: {self.rollback_count}")  # Phase 4: 记录回退次数

        # 生成可视化
        if self.config.get('logging', {}).get('save_visualizations', True):
            try:
                from core.visualization import generate_visualizations
                generate_visualizations(self.logger.run_dir)
                self.logger.logger.info("✓ Visualizations generated")
            except Exception as e:
                self.logger.logger.warning(f"Visualization generation failed: {e}")

    # ============ Phase 4: 回退机制辅助方法 ============

    def _calculate_penalty_breakdown(
        self,
        metrics: Dict[str, Any],
        violations: list[ViolationItem]
    ) -> Dict[str, float]:
        """
        计算惩罚分分项（越低越好）

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

        # 违规惩罚（每个违规 +100）
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
        计算单轮迭代有效性分数（-100 ~ 100，越高越好）。

        分数由惩罚分改善、违规数量改善、以及关键连续指标改善共同决定。
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

        # 归一化增益（>0 代表改善）
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
        # 条件1: 仿真失败（如COMSOL网格崩溃）
        if not current_eval.success and current_eval.error_message:
            return True, f"仿真失败: {current_eval.error_message}"

        # 条件2: 惩罚分异常高（>1000，说明严重恶化）
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
                    return True, f"惩罚分过高 ({current_eval.penalty_score:.1f}), 设计严重恶化"
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
                    return True, f"连续3次迭代惩罚分上升: {penalties[0]:.1f} → {penalties[1]:.1f} → {penalties[2]:.1f}"

        return False, ""

    def _execute_rollback(self) -> tuple[Optional[DesignState], Optional[EvaluationResult]]:
        """
        执行回退：找到历史上惩罚分最低的状态

        Returns:
            (回退后的状态, 评估结果) 或 (None, None) 如果无法回退
        """
        if not self.state_history:
            self.logger.logger.warning("状态池为空，无法回退")
            return None, None

        # 找到惩罚分最低的状态
        best_state_id = min(
            self.state_history.keys(),
            key=lambda sid: self.state_history[sid][1].penalty_score
        )

        best_state, best_eval = self.state_history[best_state_id]

        self.logger.logger.info(f"  回退目标: {best_state_id}")
        self.logger.logger.info(f"  - 迭代: {best_eval.iteration}")
        self.logger.logger.info(f"  - 惩罚分: {best_eval.penalty_score:.2f}")
        self.logger.logger.info(f"  - 违规数: {len(best_eval.violations)}")

        return best_state.copy(deep=True), best_eval


if __name__ == "__main__":
    print("✓ Workflow Orchestrator module created")

