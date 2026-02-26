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

from core.protocol import DesignState, ComponentGeometry, Vector3D
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
                # 2.1 评估当前状态
                current_metrics, violations = self._evaluate_design(current_state, iteration)

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
                    'llm_tokens': 0
                })

                # 保存设计状态（用于3D可视化）
                self.logger.save_design_state(iteration, current_state.dict())

                # 2.2 检查收敛
                if not violations:
                    self.logger.logger.info("✓ All constraints satisfied! Optimization converged.")
                    break

                # 2.3 构建全局上下文
                context = self._build_global_context(
                    iteration,
                    current_state,
                    current_metrics,
                    violations
                )

                # 2.4 Meta-Reasoner生成战略计划
                strategic_plan = self.meta_reasoner.generate_strategic_plan(context)
                self.logger.logger.info(f"Strategic plan: {strategic_plan.strategy_type}")

                # 2.5 Agent协调生成执行计划
                execution_plan = self.coordinator.coordinate(
                    strategic_plan,
                    current_state,
                    current_metrics
                )

                # 2.6 执行优化计划
                new_state = self._execute_plan(execution_plan, current_state)

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
                self.logger.logger.error(f"Iteration {iteration} failed: {e}")
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
            envelope=envelope
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
        sim_request = SimulationRequest(
            sim_type=SimulationType.SIMPLIFIED,
            design_state=design_state,
            parameters={}
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

    def _evaluate_geometry(self, design_state: DesignState) -> GeometryMetrics:
        """评估几何指标"""
        # 简化实现
        return GeometryMetrics(
            min_clearance=5.0,
            com_offset=[0.5, -0.2, 0.1],
            moment_of_inertia=[1.2, 1.3, 1.1],
            packing_efficiency=75.0,
            num_collisions=0
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
        # RAG检索相关知识
        context_pack = GlobalContextPack(
            iteration=iteration,
            design_state_summary=f"设计包含{len(design_state.components)}个组件",
            geometry_metrics=metrics["geometry"],
            thermal_metrics=metrics["thermal"],
            structural_metrics=metrics["structural"],
            power_metrics=metrics["power"],
            violations=violations,
            history_summary=f"第{iteration}次迭代"
        )

        # 检索知识
        retrieved_knowledge = self.rag_system.retrieve(context_pack, top_k=3)
        context_pack.retrieved_knowledge = retrieved_knowledge

        return context_pack

    def _execute_plan(self, execution_plan, current_state: DesignState) -> DesignState:
        """执行优化计划"""
        # 简化实现：返回当前状态的副本
        # 实际应用中需要根据execution_plan修改设计
        return current_state

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

        # 生成可视化
        if self.config.get('logging', {}).get('save_visualizations', True):
            try:
                from core.visualization import generate_visualizations
                generate_visualizations(self.logger.run_dir)
                self.logger.logger.info("✓ Visualizations generated")
            except Exception as e:
                self.logger.logger.warning(f"Visualization generation failed: {e}")


if __name__ == "__main__":
    print("✓ Workflow Orchestrator module created")
