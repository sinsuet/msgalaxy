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
from typing import Optional, Dict, Any
from pathlib import Path
import yaml
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

from core.protocol import DesignState, ComponentGeometry, Vector3D, EvaluationResult
from core.logger import ExperimentLogger
from core.exceptions import SatelliteDesignError

from geometry.layout_engine import LayoutEngine
from simulation.base import SimulationDriver
from simulation.matlab_driver import MatlabDriver
from simulation.comsol_driver import ComsolDriver
from simulation.physics_engine import SimplifiedPhysicsEngine

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

    def _initialize_modules(self):
        """初始化所有模块"""
        # 1. 几何模块
        geom_config = self.config.get("geometry", {})
        self.layout_engine = LayoutEngine(config=geom_config)

        # 2. 仿真模块
        sim_config = self.config.get("simulation", {})
        sim_backend = sim_config.get("backend", "simplified")

        if sim_backend == "matlab":
            self.sim_driver = MatlabDriver(
                matlab_path=sim_config.get("matlab_path"),
                script_path=sim_config.get("matlab_script")
            )
        elif sim_backend == "comsol":
            self.sim_driver = ComsolDriver(config=sim_config)
        else:
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
        self.rag_system = RAGSystem(
            api_key=api_key,
            knowledge_base_path=self.config.get("knowledge", {}).get("base_path", "data/knowledge_base"),
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
                penalty_score = self._calculate_penalty_score(current_metrics, violations)
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

                # 记录迭代数据
                self.logger.log_metrics({
                    'iteration': iteration,
                    'timestamp': __import__('datetime').datetime.now().isoformat(),
                    'max_temp': current_metrics['thermal'].max_temp,
                    'min_clearance': current_metrics['geometry'].min_clearance,
                    'total_mass': sum(c.mass for c in current_state.components),
                    'total_power': current_metrics['power'].total_power,
                    'num_violations': len(violations),
                    'is_safe': len(violations) == 0,
                    'solver_cost': 0,
                    'llm_tokens': 0,
                    'penalty_score': penalty_score,  # Phase 4: 记录惩罚分
                    'state_id': current_state.state_id  # Phase 4: 记录状态ID
                })

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
        if bom_file:
            # 从BOM文件加载
            from core.bom_parser import BOMParser

            self.logger.logger.info(f"Loading BOM from: {bom_file}")
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

        # 使用默认布局
        packing_result = self.layout_engine.generate_layout()

        # 转换为DesignState
        components = []
        for part in packing_result.placed:
            pos = part.get_actual_position()
            comp_geom = ComponentGeometry(
                id=part.id,
                position=Vector3D(x=float(pos[0]), y=float(pos[1]), z=float(pos[2])),
                dimensions=Vector3D(x=float(part.dims[0]), y=float(part.dims[1]), z=float(part.dims[2])),
                rotation=Vector3D(x=0, y=0, z=0),
                mass=part.mass,
                power=part.power,
                category=part.category if hasattr(part, 'category') else 'unknown'
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

        # 2.1 如果使用动态COMSOL模式，先导出STEP文件
        sim_params = {}
        sim_config = self.config.get("simulation", {})
        if sim_config.get("mode") == "dynamic" and sim_config.get("backend") == "comsol":
            step_file = self._export_design_to_step(design_state, iteration)
            sim_params["step_file"] = str(step_file)
            self.logger.logger.info(f"  导出STEP文件用于动态仿真: {step_file}")

        # 传递实验目录，用于保存 .mph 模型文件
        sim_params["experiment_dir"] = str(self.logger.run_dir)

        sim_request = SimulationRequest(
            sim_type=SimulationType.SIMPLIFIED,
            design_state=design_state,
            parameters=sim_params
        )

        sim_result = self.sim_driver.run_simulation(sim_request)

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
            "power": power_metrics
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

        return GeometryMetrics(
            min_clearance=5.0,  # TODO: 实现真实的间隙计算
            com_offset=com_offset_vector,
            cg_offset_magnitude=cg_offset,
            moment_of_inertia=list(moi),
            packing_efficiency=75.0,  # TODO: 实现真实的装填率计算
            num_collisions=0  # TODO: 实现碰撞检测
        )

    def _check_violations(
        self,
        geometry_metrics: GeometryMetrics,
        thermal_metrics: ThermalMetrics,
        structural_metrics: StructuralMetrics,
        power_metrics: PowerMetrics
    ) -> list[ViolationItem]:
        """检查约束违反"""
        violations = []

        # 几何约束
        if geometry_metrics.min_clearance < 3.0:
            violations.append(ViolationItem(
                violation_id=f"V_GEOM_{len(violations)}",
                violation_type="geometry",
                severity="major",
                description="最小间隙不足",
                affected_components=[],
                metric_value=geometry_metrics.min_clearance,
                threshold=3.0
            ))

        # 质心偏移约束
        if geometry_metrics.cg_offset_magnitude > 20.0:
            violations.append(ViolationItem(
                violation_id=f"V_CG_{len(violations)}",
                violation_type="geometry",
                severity="major",
                description="质心偏移过大，影响姿态控制",
                affected_components=[],
                metric_value=geometry_metrics.cg_offset_magnitude,
                threshold=20.0
            ))

        # 热控约束
        if thermal_metrics.max_temp > 60.0:
            violations.append(ViolationItem(
                violation_id=f"V_THERM_{len(violations)}",
                violation_type="thermal",
                severity="critical",
                description="温度超标",
                affected_components=[],
                metric_value=thermal_metrics.max_temp,
                threshold=60.0
            ))

        # 结构约束
        if structural_metrics.safety_factor < 2.0:
            violations.append(ViolationItem(
                violation_id=f"V_STRUCT_{len(violations)}",
                violation_type="structural",
                severity="critical",
                description="安全系数不足",
                affected_components=[],
                metric_value=structural_metrics.safety_factor,
                threshold=2.0
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
            design_state_summary=f"设计包含{len(design_state.components)}个组件",
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
        from geometry.ffd import FFDDeformer
        import numpy as np

        # 深拷贝当前状态
        new_state = copy.deepcopy(current_state)

        # 如果execution_plan为空，直接返回
        if not execution_plan:
            self.logger.logger.warning("执行计划为空")
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
                    for target_comp_id in target_components:
                        self._execute_single_action(
                            new_state, op_type, target_comp_id, parameters
                        )
                elif component_id:
                    self.logger.logger.info(f"  执行操作: {op_type} on {component_id}")
                    self._execute_single_action(
                        new_state, op_type, component_id, parameters
                    )
                else:
                    self.logger.logger.warning(f"  操作 {op_type} 缺少目标组件，跳过")

            except Exception as e:
                self.logger.logger.error(f"  执行操作失败: {e}", exc_info=True)
                continue

        # 更新迭代次数
        new_state.iteration = current_state.iteration + 1

        return new_state

    def _execute_single_action(
        self,
        new_state: DesignState,
        op_type: str,
        component_id: str,
        parameters: dict
    ):
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
            return

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

        # 执行不同类型的操作
        if op_type == "MOVE":
            # 移动组件
            axis = parameters.get("axis", "X")
            move_range = parameters.get("range", [0, 0])
            # 取范围中点作为移动距离
            delta = (move_range[0] + move_range[1]) / 2.0

            if axis == "X":
                new_state.components[comp_idx].position.x += delta
            elif axis == "Y":
                new_state.components[comp_idx].position.y += delta
            elif axis == "Z":
                new_state.components[comp_idx].position.z += delta

            self.logger.logger.info(f"    移动 {axis} 轴 {delta:.2f} mm")

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
            clearance = parameters.get("clearance", 20.0)

            self.logger.logger.info(f"    重新装箱: strategy={strategy}, clearance={clearance}")

            # 调用layout_engine重新布局
            # 注意：这会重置所有组件位置
            packing_result = self.layout_engine.generate_layout()

            # 更新组件位置
            for part in packing_result.placed:
                pos = part.get_actual_position()
                for idx, comp in enumerate(new_state.components):
                    if comp.id == part.id:
                        new_state.components[idx].position = Vector3D(
                            x=float(pos[0]),
                            y=float(pos[1]),
                            z=float(pos[2])
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

    def _should_accept(
        self,
        old_metrics: Dict[str, Any],
        new_metrics: Dict[str, Any],
        old_violations: list,
        new_violations: list
    ) -> bool:
        """判断是否接受新状态"""
        # 简化策略：违反数量减少则接受
        return len(new_violations) <= len(old_violations)

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

    def _calculate_penalty_score(
        self,
        metrics: Dict[str, Any],
        violations: list[ViolationItem]
    ) -> float:
        """
        计算惩罚分（越低越好）

        Args:
            metrics: 性能指标
            violations: 违规列表

        Returns:
            惩罚分
        """
        penalty = 0.0

        # 违规惩罚（每个违规 +100）
        penalty += len(violations) * 100.0

        # 温度惩罚（超过60°C）
        max_temp = metrics.get('thermal').max_temp
        if max_temp > 60.0:
            penalty += (max_temp - 60.0) * 10.0

        # 间隙惩罚（小于3mm）
        min_clearance = metrics.get('geometry').min_clearance
        if min_clearance < 3.0:
            penalty += (3.0 - min_clearance) * 50.0

        # 质心偏移惩罚（大于50mm）
        cg_offset = metrics.get('geometry').cg_offset_magnitude
        if cg_offset > 50.0:
            penalty += (cg_offset - 50.0) * 2.0

        return penalty

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
