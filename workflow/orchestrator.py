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
from typing import Optional, Dict, Any
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
    from simulation.physics_engine import SimplifiedPhysicsEngine
except ImportError:
    SimplifiedPhysicsEngine = None

from optimization.meta_reasoner import MetaReasoner
from optimization.agents import GeometryAgent, ThermalAgent, StructuralAgent, PowerAgent
from optimization.coordinator import AgentCoordinator
from optimization.knowledge.rag_system import RAGSystem
from optimization.protocol import (
    GlobalContextPack,
    GeometryMetrics,
    ThermalMetrics,
    StructuralMetrics,
    PowerMetrics,
    ViolationItem,
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

        # 初始化日志
        self.logger = ExperimentLogger(
            base_dir=self.config.get("logging", {}).get("base_dir", "experiments")
        )

        # 初始化各模块
        self._initialize_modules()

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
            model=openai_config.get("model", "gpt-4-turbo"),
            temperature=openai_config.get("temperature", 0.7),
            base_url=base_url,
            logger=self.logger
        )

        # Agents
        agent_model = openai_config.get("model", "gpt-4-turbo")
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
            logger=self.logger
        )

        # Phase 4: 状态池与回退机制
        self.state_history = {}  # {state_id: (DesignState, EvaluationResult)}
        self.recent_failures = []  # 最近失败的操作描述
        self.rollback_count = 0  # 回退次数统计

        self.logger.logger.info("All modules initialized successfully")

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
        self.runtime_constraints = dict(self.default_constraints)
        self._last_trace_metrics = None  # 用于计算迭代增量
        self.logger.logger.info(
            "Runtime constraints initialized: "
            f"T<= {self.runtime_constraints['max_temp_c']:.2f}°C, "
            f"clearance>= {self.runtime_constraints['min_clearance_mm']:.2f}mm, "
            f"CG<= {self.runtime_constraints['max_cg_offset_mm']:.2f}mm"
        )

        # 1. 初始化设计状态
        current_state = self._initialize_design_state(bom_file)

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
                if self._should_accept(current_metrics, new_metrics, violations, new_violations):
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
                "solver_cost": solver_cost
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
        comp_fp = []
        for comp in design_state.components:
            thermal_contacts = tuple(
                sorted(
                    (str(k), round(float(v), 6))
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
                    round(float(comp.position.x), 6),
                    round(float(comp.position.y), 6),
                    round(float(comp.position.z), 6),
                    round(float(comp.dimensions.x), 6),
                    round(float(comp.dimensions.y), 6),
                    round(float(comp.dimensions.z), 6),
                    round(float(comp.rotation.x), 6),
                    round(float(comp.rotation.y), 6),
                    round(float(comp.rotation.z), 6),
                    str(getattr(comp, "envelope_type", "box")),
                    round(float(getattr(comp, "emissivity", 0.8)), 6),
                    round(float(getattr(comp, "absorptivity", 0.3)), 6),
                    str(getattr(comp, "coating_type", "default")),
                    thermal_contacts,
                    heatsink,
                    bracket,
                )
            )
        return tuple(sorted(comp_fp, key=lambda x: x[0]))

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
        new_violations: list
    ) -> bool:
        """判断是否接受新状态（违规数量 + 惩罚分双判据）"""
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
        if new_penalty <= old_penalty + 1e-6:
            return True

        self.logger.logger.info(
            "  拒绝新状态: 违规数未减少且惩罚分恶化 "
            f"({old_penalty:.2f} -> {new_penalty:.2f})"
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
