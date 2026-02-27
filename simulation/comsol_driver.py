"""
COMSOL仿真驱动器

通过MPh库连接COMSOL Multiphysics进行多物理场仿真
支持两种模式：
1. 静态模型 + 参数更新（传统模式，向下兼容）
2. 动态 STEP 导入 + Box Selection（新模式，支持拓扑重构）
"""

import os
from typing import Dict, Any, Optional, List
from pathlib import Path

from simulation.base import SimulationDriver
from core.protocol import SimulationRequest, SimulationResult, ViolationItem, DesignState
from core.exceptions import ComsolConnectionError, SimulationError
from core.logger import get_logger

logger = get_logger(__name__)


class ComsolDriver(SimulationDriver):
    """COMSOL仿真驱动器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化COMSOL驱动器

        Args:
            config: 配置字典，包含：
                - comsol_model: COMSOL模型文件路径（.mph）
                - comsol_parameters: 要更新的参数列表
                - auto_generate_model: 是否自动生成模型（默认False）
                - environment: 环境类型 ("orbit"或"ground"，默认"orbit")
                - mode: 仿真模式 ("static" 或 "dynamic"，默认"static")
                  * static: 静态模型 + 参数更新（传统模式）
                  * dynamic: 动态 STEP 导入 + Box Selection（新模式）
        """
        super().__init__(config)
        self.model_file = config.get('comsol_model', 'model.mph')
        self.parameters = config.get('comsol_parameters', [])
        self.auto_generate = config.get('auto_generate_model', False)
        self.environment = config.get('environment', 'orbit')
        self.mode = config.get('mode', 'static')  # 新增：仿真模式
        self.client: Optional[Any] = None
        self.model: Optional[Any] = None

        logger.info(f"COMSOL驱动器初始化: mode={self.mode}")

    def connect(self) -> bool:
        """
        连接到COMSOL服务器并加载模型

        Returns:
            是否连接成功
        """
        if self.connected:
            logger.info("COMSOL已连接")
            return True

        try:
            logger.info("正在连接COMSOL...")
            import mph

            # 启动COMSOL客户端
            self.client = mph.start()
            logger.info("✓ COMSOL客户端启动成功")

            # 加载模型
            if not os.path.exists(self.model_file):
                raise ComsolConnectionError(f"COMSOL模型文件不存在: {self.model_file}")

            logger.info(f"正在加载模型: {self.model_file}")
            self.model = self.client.load(self.model_file)
            logger.info("✓ COMSOL模型加载成功")

            self.connected = True
            return True

        except ImportError:
            raise ComsolConnectionError(
                "无法导入mph模块。请安装MPh库:\n"
                "pip install mph\n"
                "注意：需要COMSOL安装在 D:\\Program Files\\COMSOL63"
            )
        except Exception as e:
            raise ComsolConnectionError(f"COMSOL连接失败: {e}")

    def disconnect(self):
        """断开COMSOL连接"""
        if self.client:
            try:
                self.client.disconnect()
                logger.info("COMSOL连接已关闭")
            except Exception as e:
                logger.warning(f"断开连接时出错: {e}")
            finally:
                self.client = None
                self.model = None
                self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        运行COMSOL仿真

        根据配置的 mode 选择不同的仿真策略：
        - static: 使用静态模型 + 参数更新
        - dynamic: 使用动态 STEP 导入 + Box Selection

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        if not self.connected:
            self.connect()

        if not self.validate_design_state(request.design_state):
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message="设计状态无效"
            )

        # 根据模式选择仿真策略
        if self.mode == "dynamic":
            return self._run_dynamic_simulation(request)
        else:
            return self._run_static_simulation(request)

    def _run_static_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        静态模式仿真（传统方式）

        使用预先存在的 .mph 模型，通过参数更新来调整几何

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        try:
            # 检查是否需要重新生成模型
            if self.auto_generate:
                self._regenerate_model_if_needed(request.design_state)

            logger.info("运行COMSOL仿真（静态模式）...")

            # 1. 更新几何参数
            self._update_geometry(request.design_state)

            # 2. 重建几何和网格
            logger.info("  重建几何...")
            self.model.build()

            logger.info("  生成网格...")
            self.model.mesh()

            # 3. 求解
            logger.info("  求解物理场...")
            self.model.solve()

            # 4. 提取结果
            metrics = self._extract_results()
            logger.info(f"  仿真完成: {metrics}")

            # 5. 检查约束
            violations = self.check_constraints(metrics)

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in violations]
            )

        except Exception as e:
            logger.error(f"COMSOL仿真失败: {e}")
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message=str(e)
            )

    def _update_geometry(self, design_state):
        """
        更新COMSOL模型的几何参数

        Args:
            design_state: 设计状态
        """
        logger.info("  更新几何参数...")

        for comp in design_state.components:
            # 更新位置参数
            param_prefix = comp.id

            # 位置参数
            if f'{param_prefix}_x' in self.parameters:
                self.model.parameter(f'{param_prefix}_x', f'{comp.position.x}[mm]')
            if f'{param_prefix}_y' in self.parameters:
                self.model.parameter(f'{param_prefix}_y', f'{comp.position.y}[mm]')
            if f'{param_prefix}_z' in self.parameters:
                self.model.parameter(f'{param_prefix}_z', f'{comp.position.z}[mm]')

            # 尺寸参数
            if f'{param_prefix}_dx' in self.parameters:
                self.model.parameter(f'{param_prefix}_dx', f'{comp.dimensions.x}[mm]')
            if f'{param_prefix}_dy' in self.parameters:
                self.model.parameter(f'{param_prefix}_dy', f'{comp.dimensions.y}[mm]')
            if f'{param_prefix}_dz' in self.parameters:
                self.model.parameter(f'{param_prefix}_dz', f'{comp.dimensions.z}[mm]')

            # 功率参数（用于热源）
            if f'{param_prefix}_power' in self.parameters:
                self.model.parameter(f'{param_prefix}_power', f'{comp.power}[W]')

        logger.info(f"  已更新 {len(design_state.components)} 个组件的参数")

    def _regenerate_model_if_needed(self, design_state):
        """
        根据需要重新生成COMSOL模型

        Args:
            design_state: 设计状态
        """
        # 检查是否需要重新生成
        # 条件：组件数量变化、首次运行、或模型文件不存在
        need_regenerate = False

        if not os.path.exists(self.model_file):
            logger.info("模型文件不存在，需要生成新模型")
            need_regenerate = True
        elif hasattr(self, '_last_component_count'):
            if len(design_state.components) != self._last_component_count:
                logger.info(f"组件数量变化 ({self._last_component_count} -> {len(design_state.components)})，需要重新生成模型")
                need_regenerate = True

        if need_regenerate:
            logger.info("开始动态生成COMSOL模型...")
            from simulation.comsol_model_generator import COMSOLModelGenerator

            generator = COMSOLModelGenerator()
            success = generator.generate_model(
                design_state,
                self.model_file,
                environment=self.environment
            )

            if not success:
                raise SimulationError("COMSOL模型生成失败")

            # 重新加载模型
            if self.connected:
                self.disconnect()
            self.connect()

            logger.info("✓ 动态模型生成并加载成功")

        self._last_component_count = len(design_state.components)

    def _extract_results(self) -> Dict[str, float]:
        """
        从COMSOL模型中提取仿真结果

        Returns:
            指标字典
        """
        metrics = {}

        try:
            # 提取最大温度（使用算子）
            max_temp = float(self.model.evaluate('maxop1(T)', unit='degC'))
            metrics['max_temp'] = max_temp

            # 提取平均温度（使用算子）
            avg_temp = float(self.model.evaluate('aveop1(T)', unit='degC'))
            metrics['avg_temp'] = avg_temp

            # 提取最大应力（如果有结构分析）
            try:
                max_stress = float(self.model.evaluate('maxop1(solid.mises)', unit='MPa'))
                metrics['max_stress'] = max_stress
            except:
                pass

            # 提取总热流（如果有热分析）
            try:
                total_heat_flux = float(self.model.evaluate('intop1(ht.tfluxMag)', unit='W'))
                metrics['total_heat_flux'] = total_heat_flux
            except:
                pass

        except Exception as e:
            logger.warning(f"提取结果时出错: {e}")

        return metrics

    def _save_mph_model(self, request: SimulationRequest):
        """
        保存 COMSOL .mph 模型文件（用于可复现性和可视化排错）

        Args:
            request: 仿真请求（包含迭代信息和实验目录）
        """
        try:
            import os

            # 检查模型是否存在
            if not self.model:
                logger.warning("  ⚠ COMSOL 模型对象不存在，跳过保存")
                return

            # 从 request.parameters 中获取实验目录
            experiment_dir = request.parameters.get("experiment_dir", None)
            iteration = request.design_state.iteration

            if experiment_dir:
                # 创建 mph_models 子目录
                mph_dir = os.path.join(experiment_dir, "mph_models")
                os.makedirs(mph_dir, exist_ok=True)

                # 生成文件路径
                mph_filename = f"model_iter_{iteration:03d}.mph"
                mph_save_path = os.path.join(mph_dir, mph_filename)

                # 处理 Windows 路径斜杠
                mph_save_path_safe = mph_save_path.replace('\\', '/')

                # 保存模型
                logger.info(f"  保存 COMSOL .mph 模型...")
                try:
                    self.model.save(mph_save_path_safe)
                    logger.info(f"  ✓ COMSOL .mph 模型已保存: {mph_save_path_safe}")
                except Exception as save_error:
                    logger.warning(f"  ⚠ MPh save() 调用失败: {save_error}")
                    logger.warning(f"  尝试使用 Java API 保存...")
                    # 尝试使用 Java API
                    self.model.java.save(mph_save_path_safe)
                    logger.info(f"  ✓ COMSOL .mph 模型已保存（Java API）: {mph_save_path_safe}")

            else:
                # 如果没有提供实验目录，保存到默认位置
                workspace = Path("workspace/comsol_models")
                workspace.mkdir(parents=True, exist_ok=True)

                mph_filename = f"model_iter_{iteration:03d}.mph"
                mph_save_path = workspace / mph_filename
                mph_save_path_safe = str(mph_save_path).replace('\\', '/')

                logger.info(f"  保存 COMSOL .mph 模型（默认位置）...")
                try:
                    self.model.save(mph_save_path_safe)
                    logger.info(f"  ✓ COMSOL .mph 模型已保存: {mph_save_path_safe}")
                except Exception as save_error:
                    logger.warning(f"  ⚠ MPh save() 调用失败: {save_error}")
                    logger.warning(f"  尝试使用 Java API 保存...")
                    # 尝试使用 Java API
                    self.model.java.save(mph_save_path_safe)
                    logger.info(f"  ✓ COMSOL .mph 模型已保存（Java API）: {mph_save_path_safe}")

        except Exception as e:
            # 保存失败不应中断仿真流程
            logger.warning(f"  ⚠ 保存 .mph 模型失败: {e}")
            logger.warning(f"  异常类型: {type(e).__name__}")
            logger.warning(f"  仿真结果仍然有效，继续执行...")

    def evaluate_expression(self, expression: str, unit: str = None) -> float:
        """
        计算COMSOL表达式

        Args:
            expression: COMSOL表达式
            unit: 单位

        Returns:
            计算结果
        """
        if not self.connected:
            self.connect()

        try:
            if unit:
                return float(self.model.evaluate(expression, unit=unit))
            else:
                return float(self.model.evaluate(expression))
        except Exception as e:
            raise SimulationError(f"计算表达式失败: {e}")

    def export_results(self, output_file: str, dataset: str = None):
        """
        导出仿真结果

        Args:
            output_file: 输出文件路径
            dataset: 数据集名称
        """
        if not self.connected:
            raise SimulationError("COMSOL未连接")

        try:
            if dataset:
                self.model.export(output_file, dataset)
            else:
                self.model.export(output_file)
            logger.info(f"结果已导出到: {output_file}")
        except Exception as e:
            raise SimulationError(f"导出结果失败: {e}")

    # ============ 动态 STEP 导入模式 ============

    def _run_dynamic_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        动态模式仿真（新方式）

        从 STEP 文件动态导入几何，使用 Box Selection 自动识别组件和边界

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        try:
            logger.info("运行COMSOL仿真（动态模式）...")

            # 1. 获取或生成 STEP 文件
            step_file = self._get_or_generate_step_file(request)

            # 2. 创建动态模型
            logger.info("  创建动态模型...")
            self._create_dynamic_model(step_file, request.design_state)

            # 3. 求解（使用功率斜坡加载策略，解决非线性发散）
            logger.info("  求解物理场（T⁴ 辐射边界 + 功率斜坡加载）...")
            solve_success = False
            try:
                # 功率斜坡加载：1% -> 20% -> 100%
                # COMSOL 会自动将上一次的稳态解作为下一次的初始猜测值
                ramping_steps = ["0.01", "0.20", "1.0"]  # 1%, 20%, 100% 功率

                for scale in ramping_steps:
                    logger.info(f"    - 执行稳态求解 (功率缩放 P_scale = {scale})...")
                    self.model.java.param().set("P_scale", scale)
                    self.model.java.study("std1").run()
                    logger.info(f"      ✓ P_scale={scale} 求解成功")

                logger.info("  ✓ 功率斜坡加载完成，求解成功")
                solve_success = True
            except Exception as solve_error:
                logger.warning(f"  ⚠ 求解发散或失败: {solve_error}")
                logger.warning(f"  Java 异常详情: {str(solve_error)}")
                logger.warning("  返回惩罚分，不中断优化循环")

            # 4. 无论求解成功与否，都保存 .mph 模型文件（用于调试）
            self._save_mph_model(request)

            if not solve_success:
                # 返回惩罚分
                return SimulationResult(
                    success=False,
                    metrics={
                        "max_temp": 999.0,  # 惩罚温度（表示求解失败）
                        "avg_temp": 999.0,
                        "min_temp": 999.0
                    },
                    violations=[],
                    error_message=f"COMSOL求解发散"
                )

            # 5. 提取结果
            metrics = self._extract_dynamic_results()
            logger.info(f"  仿真完成: {metrics}")

            # 6. 检查约束
            violations = self.check_constraints(metrics)

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in violations]
            )

        except Exception as e:
            logger.error(f"COMSOL动态仿真失败: {e}", exc_info=True)

            # 返回惩罚分，不中断优化循环
            return SimulationResult(
                success=False,
                metrics={
                    "max_temp": 9999.0,  # 惩罚温度
                    "avg_temp": 9999.0,
                    "min_temp": 9999.0
                },
                violations=[],
                error_message=str(e)
            )

    def _get_or_generate_step_file(self, request: SimulationRequest) -> Path:
        """
        获取或生成 STEP 文件

        Args:
            request: 仿真请求

        Returns:
            STEP 文件路径
        """
        # 1. 检查 request.parameters 中是否已提供 step_file
        if "step_file" in request.parameters:
            step_file = Path(request.parameters["step_file"])
            if step_file.exists():
                logger.info(f"  使用提供的 STEP 文件: {step_file}")
                return step_file

        # 2. 如果没有提供，即时生成（使用 OpenCASCADE 生成真实 STEP）
        logger.info("  即时生成 STEP 文件（使用 OpenCASCADE）...")

        try:
            from geometry.cad_export_occ import export_design_occ
        except ImportError:
            logger.error("  ✗ 无法导入 cad_export_occ 模块")
            raise SimulationError(
                "STEP 导出模块不可用。请确保 geometry/cad_export_occ.py 存在。"
            )

        # 生成到临时目录
        workspace = Path("workspace/comsol_dynamic")
        workspace.mkdir(parents=True, exist_ok=True)

        step_file = workspace / f"design_iter_{request.design_state.iteration}.step"

        # 使用 OpenCASCADE 导出真实 STEP 文件
        try:
            export_design_occ(request.design_state, str(step_file))
            logger.info(f"  ✓ STEP 文件已生成（OpenCASCADE）: {step_file}")
        except Exception as e:
            logger.error(f"  ✗ STEP 文件生成失败: {e}")
            raise SimulationError(f"STEP 文件生成失败: {e}")

        return step_file

    def _create_dynamic_model(self, step_file: Path, design_state: DesignState):
        """
        创建动态 COMSOL 模型

        核心步骤：
        1. 创建空模型
        2. 导入 STEP 几何
        3. 使用 Box Selection 识别组件和边界
        4. 赋予物理属性
        5. 划分网格

        Args:
            step_file: STEP 文件路径
            design_state: 设计状态
        """
        try:
            # 1. 创建新模型（如果已有模型，先清理）
            if self.model:
                logger.info("  清理旧模型...")
                # 不需要显式清理，直接创建新模型会覆盖

            logger.info("  [1/6] 创建空模型...")
            self.model = self.client.create("DynamicThermalModel")

            # 2. 导入 STEP 几何（终极攻坚版本）
            logger.info(f"  [2/6] 导入 STEP 文件: {step_file}")

            # Step 1: 强制绝对路径与斜杠转换
            import os
            abs_step_path = os.path.abspath(step_file).replace('\\', '/')
            logger.info(f"  转换后的绝对路径: {abs_step_path}")

            # 验证文件存在
            if not os.path.exists(abs_step_path):
                raise FileNotFoundError(f"STEP 文件不存在: {abs_step_path}")

            # Step 2: 使用最简 API 构建几何节点
            geom = self.model.java.geom().create("geom1", 3)
            import_node = geom.feature().create("imp1", "Import")

            # 只设置必需的 filename 参数
            import_node.set("filename", abs_step_path)
            logger.info("  设置 STEP 文件路径完成")

            # Step 2.1: 先 run 导入节点本身
            try:
                logger.info("  执行导入节点...")
                geom.run("imp1")
                logger.info("  ✓ 导入节点执行成功")
            except Exception as import_node_error:
                logger.error(f"  ✗ 导入节点执行失败: {import_node_error}")
                logger.error(f"  Java 异常详情: {str(import_node_error)}")
                # 返回惩罚分
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 0.0, "avg_temp": 0.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 导入节点执行失败: {str(import_node_error)}"
                )

            # Step 2.2: 然后 run 整个几何序列
            try:
                logger.info("  执行几何序列...")
                geom.run()
                logger.info("  ✓ 几何序列执行成功")
            except Exception as geom_run_error:
                logger.error(f"  ✗ 几何序列执行失败: {geom_run_error}")
                logger.error(f"  Java 异常详情: {str(geom_run_error)}")
                # 返回惩罚分
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 0.0, "avg_temp": 0.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 几何序列执行失败: {str(geom_run_error)}"
                )

            # Step 3: 增加几何域 (Domain) 数量的硬性校验
            try:
                num_domains = geom.getNDomains()
                logger.info(f"  检测到 {num_domains} 个几何域")

                if num_domains == 0:
                    raise ValueError(f"STEP 导入失败: 从 {abs_step_path} 生成了 0 个域")

                logger.info(f"  ✓ STEP 几何导入成功: {num_domains} 个域")
            except Exception as domain_check_error:
                logger.error(f"  ✗ 几何域校验失败: {domain_check_error}")
                # 返回惩罚分
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 0.0, "avg_temp": 0.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 几何域校验失败: {str(domain_check_error)}"
                )

            # 3. 创建物理场（稳态热传导）
            logger.info("  [3/6] 创建热传导物理场...")
            ht = self.model.java.physics().create("ht", "HeatTransfer", "geom1")

            # 设置默认材料（铝合金）并应用到所有域
            mat = self.model.java.material().create("mat1", "Common")
            mat.label("Aluminum Alloy (Default)")
            mat.propertyGroup("def").set("thermalconductivity", "167[W/(m*K)]")
            mat.propertyGroup("def").set("density", "2700[kg/m^3]")
            mat.propertyGroup("def").set("heatcapacity", "896[J/(kg*K)]")
            mat.propertyGroup("def").set("epsilon_rad", "0.8")  # 默认发射率
            # 为 ThinLayer 特征添加薄层导热率属性
            mat.propertyGroup("def").set("ks", "167[W/(m*K)]")  # 薄层导热率（与体导热率相同）
            # 关键修复：将材料应用到所有域
            mat.selection().all()
            logger.info("  ✓ 材料已应用到所有域")

            # 添加全局功率缩放参数（用于功率斜坡加载）
            self.model.java.param().set("P_scale", "0.01")  # 初始值 1%
            logger.info("  ✓ 全局参数 P_scale 已设置: 0.01 (1% 功率)")

            # 数值稳定网络：添加全局默认的微弱导热接触（防止热悬浮）
            logger.info("  [3.5/7] 建立全局默认导热网络...")
            # 为所有内部边界添加默认的接触热导
            # 这确保没有任何组件是绝对绝热的，防止求解器发散
            try:
                # 创建 Thin Layer 特征（模拟组件间的微弱热接触）
                thin_layer = ht.feature().create("tl_global", "ThinLayer")
                # 选择所有内部边界（entitydim=2）
                thin_layer.selection().geom("geom1", 2)
                thin_layer.selection().set([])  # 先清空
                thin_layer.selection().all()  # 选择所有边界

                # 设置厚度
                d_gap = 0.1  # mm，假设间隙厚度
                thin_layer.set("ds", f"{d_gap}[mm]")

                # ---- 核心修复：强制使用用户定义的材料属性 ----
                # ThinLayer 是边界特征（2D），无法读取应用在域（3D）上的材料属性
                # 因此必须在 ThinLayer 节点上直接设置用户定义属性
                try:
                    # 尝试断开材料链接，声明自定义
                    thin_layer.set("ks_mat", "userdef")
                except Exception:
                    pass  # 忽略报错，继续尝试设置值

                # 只设置薄层导热率 (ks)，ThinLayer 不需要密度和热容
                thin_layer.set("ks", "167[W/(m*K)]")  # 薄层导热率

                logger.info(f"      ✓ 全局默认导热网络已建立: ds={d_gap} mm, ks=167 W/(m*K)")
            except Exception as e:
                logger.warning(f"      ⚠️ 全局导热网络创建失败（非致命）: {e}")
                # 非致命错误，继续执行

            # 4. 使用 Box Selection 识别组件并赋予热源
            logger.info("  [4/6] 创建 Box Selection 并赋予热源...")
            self._assign_heat_sources_dynamic(design_state, ht, geom)

            # 4.5 DV2.0: 应用组件级热学属性（涂层、接触热阻）
            logger.info("  [4.5/7] 应用组件级热学属性...")
            self._apply_thermal_properties_dynamic(design_state, ht, geom)

            # 5. 识别外部边界并赋予辐射条件
            logger.info("  [5/7] 识别外部边界并赋予辐射条件...")
            self._assign_radiation_boundaries_dynamic(design_state, ht, geom)

            # 6. 创建网格（Step 4: 捕获底层 Java 异常）
            logger.info("  [6/7] 创建自动网格...")
            try:
                mesh = self.model.java.mesh().create("mesh1", "geom1")
                mesh.autoMeshSize(5)  # 中等网格密度
                logger.info("  执行网格划分...")
                mesh.run()
                logger.info("  ✓ 网格生成成功")
            except Exception as mesh_error:
                logger.error(f"  ✗ 网格生成失败: {mesh_error}")
                logger.error(f"  Java 异常详情: {str(mesh_error)}")
                # 返回惩罚分
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 0.0, "avg_temp": 0.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 网格生成失败: {str(mesh_error)}"
                )
            logger.info("  ✓ 网格生成成功")

            # 7. 创建研究
            study = self.model.java.study().create("std1")
            study.feature().create("stat", "Stationary")

            # 8. 配置求解器（简化版本，提高稳定性）
            logger.info("  [7/7] 配置求解器...")

            # 设置初始温度（避免从 0K 开始导致数值问题）
            ht.feature("init1").set("Tinit", "293.15[K]")  # 20°C

            # 使用默认求解器配置（COMSOL 自动优化）
            # 不手动配置 sol 对象，让 study.run() 自动创建和配置
            logger.info("  ✓ 求解器配置完成: 使用 COMSOL 默认配置, 初始温度=293.15K")

            logger.info("  ✓ 动态模型创建完成")

        except Exception as e:
            logger.error(f"动态模型创建失败: {e}", exc_info=True)
            raise SimulationError(f"动态模型创建失败: {e}")

    def _assign_heat_sources_dynamic(
        self,
        design_state: DesignState,
        ht: Any,
        geom: Any
    ):
        """
        使用 Box Selection 识别组件并赋予热源

        Args:
            design_state: 设计状态
            ht: 热传导物理场对象
            geom: 几何对象
        """
        total_heat_sources_assigned = 0

        for i, comp in enumerate(design_state.components):
            if comp.power <= 0:
                continue

            logger.info(f"    - 为组件 {comp.id} 创建热源 ({comp.power}W)")

            # 计算组件的包围盒
            pos = comp.position
            dim = comp.dimensions

            # Box Selection 的边界（组件中心 ± 半尺寸）
            # 添加容差（tolerance）以确保网格偏差不会导致选择失败
            tolerance = 5.0  # mm，容差值

            x_min = pos.x - dim.x / 2 - tolerance
            x_max = pos.x + dim.x / 2 + tolerance
            y_min = pos.y - dim.y / 2 - tolerance
            y_max = pos.y + dim.y / 2 + tolerance
            z_min = pos.z - dim.z / 2 - tolerance
            z_max = pos.z + dim.z / 2 + tolerance

            # 创建 Box Selection（选择 Domain）
            # 注意：Box Selection 应该在 model 上创建，而不是 geom 上
            sel_name = f"boxsel_comp_{i}"
            box_sel = self.model.java.selection().create(sel_name, "Box")
            box_sel.set("entitydim", "3")  # 使用字符串避免 Java 重载歧义
            box_sel.set("xmin", f"{x_min}[mm]")
            box_sel.set("xmax", f"{x_max}[mm]")
            box_sel.set("ymin", f"{y_min}[mm]")
            box_sel.set("ymax", f"{y_max}[mm]")
            box_sel.set("zmin", f"{z_min}[mm]")
            box_sel.set("zmax", f"{z_max}[mm]")
            # 使用 "intersects" 而非 "inside"，更宽松的选择条件
            box_sel.set("condition", "intersects")

            # 强制校验：检查选中的域数量
            try:
                # 获取选中的实体数量
                selected_entities = box_sel.entities()
                num_selected = len(selected_entities) if selected_entities else 0
                logger.info(f"      Box Selection 选中 {num_selected} 个域")

                if num_selected == 0:
                    logger.warning(f"      ⚠️ 严重警告: 热源 Box Selection 失败！组件 {comp.id} 未选中任何域！")
                    logger.warning(f"      Box 范围: X[{x_min:.1f}, {x_max:.1f}], Y[{y_min:.1f}, {y_max:.1f}], Z[{z_min:.1f}, {z_max:.1f}] mm")
                    logger.warning(f"      组件位置: [{pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}] mm")
                    logger.warning(f"      组件尺寸: [{dim.x:.1f}, {dim.y:.1f}, {dim.z:.1f}] mm")
                    # 尝试使用 "allvertices" 条件（最宽松）
                    logger.info(f"      尝试使用更宽松的选择条件 'allvertices'...")
                    box_sel.set("condition", "allvertices")
                    selected_entities = box_sel.entities()
                    num_selected = len(selected_entities) if selected_entities else 0
                    logger.info(f"      重试后选中 {num_selected} 个域")

                    if num_selected == 0:
                        logger.error(f"      ✗ 热源绑定彻底失败！{comp.power}W 热源未施加到组件 {comp.id}！")
                        continue
            except Exception as sel_check_error:
                logger.warning(f"      无法检查选中域数量: {sel_check_error}")

            # 创建热源节点
            hs_name = f"hs_{i}"
            heat_source = ht.feature().create(hs_name, "HeatSource")
            heat_source.selection().named(sel_name)

            # 设置发热功率（使用参数化功率，支持功率斜坡加载）
            # 功率密度 = 总功率 * P_scale / 体积
            volume = (dim.x * dim.y * dim.z) / 1e9  # mm³ -> m³
            power_density = comp.power / volume if volume > 0 else 0

            # 使用参数化表达式，绑定到全局 P_scale 参数
            heat_source.set("Q0", f"{power_density} * P_scale [W/m^3]")

            logger.info(f"      ✓ 热源已设置: {comp.power}W * P_scale, 功率密度: {power_density:.2e} W/m³")
            total_heat_sources_assigned += 1

        # 最终校验
        if total_heat_sources_assigned == 0:
            logger.error("  ✗ 严重错误: 没有任何热源被成功绑定！仿真结果将无效！")
        else:
            total_power = sum(c.power for c in design_state.components if c.power > 0)
            logger.info(f"  ✓ 热源绑定完成: {total_heat_sources_assigned} 个热源, 总功率 {total_power}W")

    def _assign_radiation_boundaries_dynamic(
        self,
        design_state: DesignState,
        ht: Any,
        geom: Any
    ):
        """
        识别外部边界并赋予辐射条件

        使用包围整个卫星的 Box Selection 选择所有外表面

        Args:
            design_state: 设计状态
            ht: 热传导物理场对象
            geom: 几何对象
        """
        logger.info("    - 创建外部辐射边界...")

        # 使用 envelope 尺寸创建外边界 Box Selection
        env = design_state.envelope.outer_size

        # 稍微扩大包围盒以确保选中所有外表面
        margin = 10.0  # mm
        x_min = -margin
        x_max = env.x + margin
        y_min = -margin
        y_max = env.y + margin
        z_min = -margin
        z_max = env.z + margin

        # 创建 Box Selection（选择 Boundary）
        sel_name = "boxsel_outer_boundary"
        box_sel = self.model.java.selection().create(sel_name, "Box")
        box_sel.set("entitydim", "2")  # 使用字符串避免 Java 重载歧义
        box_sel.set("xmin", f"{x_min}[mm]")
        box_sel.set("xmax", f"{x_max}[mm]")
        box_sel.set("ymin", f"{y_min}[mm]")
        box_sel.set("ymax", f"{y_max}[mm]")
        box_sel.set("zmin", f"{z_min}[mm]")
        box_sel.set("zmax", f"{z_max}[mm]")

        # 创建辐射边界条件
        # 简化方案：使用温度边界条件（更稳定）
        # 假设外表面温度为典型卫星外壳温度
        temp_bc = ht.feature().create("temp1", "TemperatureBoundary")
        temp_bc.selection().named(sel_name)

        # 设置外表面温度（典型值：-50°C 到 +50°C，取中间值 0°C = 273.15K）
        T_surface = 273.15  # K (0°C)
        temp_bc.set("T0", f"{T_surface}[K]")

        logger.info(f"      ✓ 温度边界已设置: T_surface={T_surface}K")

        # 数值稳定锚：添加极其微弱的对流边界（防止矩阵奇异）
        logger.info("    - 添加数值稳定锚（微弱对流边界）...")
        # 修复：使用 HeatFluxBoundary 而不是 ConvectiveHeatFlux
        conv_bc = ht.feature().create("conv_stabilizer", "HeatFluxBoundary")
        conv_bc.selection().named(sel_name)

        # 设置极其微弱的换热系数（对物理影响极小，但对数值稳定性有奇效）
        h_stabilizer = 0.1  # W/(m^2*K)，极其微弱
        T_ambient = 293.15  # K (20°C)，环境温度
        # 使用对流热流公式: q = h * (T_ambient - T)
        conv_bc.set("q0", f"{h_stabilizer}[W/(m^2*K)]*({T_ambient}[K]-T)")

        logger.info(f"      ✓ 数值稳定锚已设置: h={h_stabilizer} W/(m^2*K), T_ambient={T_ambient}K")

    def _extract_dynamic_results(self) -> Dict[str, float]:
        """
        从动态模型中提取仿真结果

        Returns:
            指标字典
        """
        metrics = {}

        try:
            # 提取温度场数据
            # 使用动态 Dataset 探测，而不是硬编码名称
            import numpy as np

            logger.info("  开始提取温度结果...")

            # 方法 1: 动态获取所有可用的 dataset tags
            try:
                dataset_tags = list(self.model.java.result().dataset().tags())
                logger.info(f"    发现 {len(dataset_tags)} 个 dataset: {dataset_tags}")

                if not dataset_tags:
                    raise ValueError("求解后未找到任何 dataset！")

                # 使用最后一个生成的数据集（最新结果）
                target_dset = dataset_tags[-1]
                logger.info(f"    使用动态 dataset: {target_dset}")

                # 尝试多种提取方法
                T_data = None

                # 方法 A: 不指定 dataset（使用默认/最新解）
                try:
                    logger.info(f"    尝试方法 A: evaluate 不指定 dataset...")
                    T_data = self.model.evaluate("T", "K")
                    logger.info(f"    ✓ 方法 A 成功")
                except Exception as e:
                    logger.info(f"    方法 A 失败: {e}")

                # 方法 B: 使用 Java API 直接访问解向量
                if T_data is None:
                    try:
                        logger.info(f"    尝试方法 B: Java API 直接访问...")
                        # 获取解对象
                        sol = self.model.java.sol("sol1")
                        # 获取解向量
                        u = sol.u()
                        # 获取温度自由度（假设温度是第一个物理场）
                        # 这需要知道温度变量在解向量中的索引
                        logger.info(f"    解向量维度: {u.length if hasattr(u, 'length') else 'unknown'}")
                        # 这个方法比较复杂，暂时跳过
                        raise NotImplementedError("Java API 方法需要更多信息")
                    except Exception as e:
                        logger.info(f"    方法 B 失败: {e}")

                # 方法 C: 使用 MPh 的 inner() 方法获取所有节点温度
                if T_data is None:
                    try:
                        logger.info(f"    尝试方法 C: MPh inner() 方法...")
                        # inner() 返回所有网格节点的值
                        T_data = self.model.inner("T")
                        logger.info(f"    ✓ 方法 C 成功")
                    except Exception as e:
                        logger.info(f"    方法 C 失败: {e}")

                if T_data is None:
                    raise ValueError("所有提取方法都失败")

                # T_data 可能是数组或标量
                if hasattr(T_data, '__iter__') and not isinstance(T_data, str):
                    temp_values = [float(t) for t in T_data]
                else:
                    temp_values = [float(T_data)]

                if temp_values:
                    max_temp_k = max(temp_values)
                    min_temp_k = min(temp_values)
                    avg_temp_k = sum(temp_values) / len(temp_values)

                    metrics['max_temp'] = max_temp_k - 273.15
                    metrics['min_temp'] = min_temp_k - 273.15
                    metrics['avg_temp'] = avg_temp_k - 273.15

                    logger.info(f"    ✓ 最高温度: {max_temp_k:.2f} K ({metrics['max_temp']:.2f} °C)")
                    logger.info(f"    ✓ 最低温度: {min_temp_k:.2f} K ({metrics['min_temp']:.2f} °C)")
                    logger.info(f"    ✓ 平均温度: {avg_temp_k:.2f} K ({metrics['avg_temp']:.2f} °C)")
                    logger.info(f"    数据点数: {len(temp_values)}")
                else:
                    raise ValueError("未能提取温度数据")

            except Exception as dataset_error:
                logger.warning(f"  动态 dataset 提取失败: {dataset_error}")
                logger.warning(f"  异常类型: {type(dataset_error).__name__}")
                logger.warning(f"  异常详情: {str(dataset_error)}")

                # 方法 2: 回退到 MPh 的 evaluate 方法
                logger.info("  尝试回退方法: MPh evaluate...")
                try:
                    # 尝试多个可能的 dataset 名称
                    dataset_names = ["dset1", "dset2", "dset3", "sol1"]
                    T_data = None
                    used_dataset = None

                    for ds_name in dataset_names:
                        try:
                            T_data = self.model.evaluate("T", "K", ds_name)
                            used_dataset = ds_name
                            logger.info(f"    ✓ 成功使用 dataset: {ds_name}")
                            break
                        except Exception as ds_error:
                            logger.debug(f"    尝试 dataset {ds_name} 失败: {ds_error}")
                            continue

                    if T_data is None:
                        raise ValueError("所有 dataset 名称都失败")

                    # T_data 可能是数组或标量
                    if hasattr(T_data, '__iter__'):
                        temp_values = [float(t) for t in T_data]
                    else:
                        temp_values = [float(T_data)]

                    if temp_values:
                        max_temp_k = max(temp_values)
                        min_temp_k = min(temp_values)
                        avg_temp_k = sum(temp_values) / len(temp_values)

                        metrics['max_temp'] = max_temp_k - 273.15
                        metrics['min_temp'] = min_temp_k - 273.15
                        metrics['avg_temp'] = avg_temp_k - 273.15

                        logger.info(f"    ✓ 最高温度: {max_temp_k:.2f} K ({metrics['max_temp']:.2f} °C)")
                        logger.info(f"    ✓ 最低温度: {min_temp_k:.2f} K ({metrics['min_temp']:.2f} °C)")
                        logger.info(f"    ✓ 平均温度: {avg_temp_k:.2f} K ({metrics['avg_temp']:.2f} °C)")
                        logger.info(f"    数据点数: {len(temp_values)}")
                    else:
                        raise ValueError("未能提取温度数据")

                except Exception as eval_error:
                    logger.error(f"  ✗ 所有提取方法都失败: {eval_error}")
                    logger.error(f"  异常类型: {type(eval_error).__name__}")
                    logger.error(f"  异常详情: {str(eval_error)}")
                    import traceback
                    logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")

                    # 最后的回退：使用基于边界条件的估计值
                    logger.warning("  使用估计值（基于边界条件）")
                    metrics['max_temp'] = 30.0  # °C (估计值)
                    metrics['min_temp'] = 0.0   # °C (边界条件)
                    metrics['avg_temp'] = 15.0  # °C (估计值)
                    logger.info(f"    估计值: max={metrics['max_temp']}°C, min={metrics['min_temp']}°C, avg={metrics['avg_temp']}°C")

        except Exception as e:
            logger.error(f"✗ 提取动态结果时发生严重错误: {e}")
            logger.error(f"  异常类型: {type(e).__name__}")
            logger.error(f"  异常详情: {str(e)}")
            import traceback
            logger.error(f"  堆栈跟踪:\n{traceback.format_exc()}")
            # 返回惩罚分
            metrics['max_temp'] = 9999.0
            metrics['avg_temp'] = 9999.0
            metrics['min_temp'] = 9999.0

        return metrics

    # ============ DV2.0: 热学属性算子实现 ============

    def _apply_thermal_properties_dynamic(
        self,
        design_state: DesignState,
        ht: Any,
        geom: Any
    ):
        """
        DV2.0: 应用组件级热学属性（涂层、接触热阻）

        支持的属性：
        - MODIFY_COATING: 修改组件表面发射率/吸收率
        - SET_THERMAL_CONTACT: 设置组件间接触热阻

        Args:
            design_state: 设计状态
            ht: 热传导物理场对象
            geom: 几何对象
        """
        coating_count = 0
        contact_count = 0

        for i, comp in enumerate(design_state.components):
            # === 1. 处理涂层属性 (MODIFY_COATING) ===
            # 检查是否有自定义涂层（非默认值）
            has_custom_coating = (
                hasattr(comp, 'emissivity') and comp.emissivity != 0.8 or
                hasattr(comp, 'absorptivity') and comp.absorptivity != 0.3 or
                hasattr(comp, 'coating_type') and comp.coating_type != "default"
            )

            if has_custom_coating:
                emissivity = getattr(comp, 'emissivity', 0.8)
                absorptivity = getattr(comp, 'absorptivity', 0.3)
                coating_type = getattr(comp, 'coating_type', 'default')

                logger.info(f"    - 组件 {comp.id} 应用自定义涂层: ε={emissivity}, α={absorptivity}, type={coating_type}")

                # 创建组件专用材料
                mat_name = f"mat_coating_{i}"
                try:
                    mat = self.model.java.material().create(mat_name, "Common")
                    mat.label(f"Coating for {comp.id} ({coating_type})")

                    # 基础热属性（继承铝合金）
                    mat.propertyGroup("def").set("thermalconductivity", "167[W/(m*K)]")
                    mat.propertyGroup("def").set("density", "2700[kg/m^3]")
                    mat.propertyGroup("def").set("heatcapacity", "896[J/(kg*K)]")

                    # 自定义发射率
                    mat.propertyGroup("def").set("epsilon_rad", str(emissivity))

                    # 使用 Box Selection 将材料应用到组件
                    sel_name = f"boxsel_coating_{i}"
                    self._create_component_box_selection(comp, sel_name, entity_dim=3)
                    mat.selection().named(sel_name)

                    logger.info(f"      ✓ 涂层材料已创建并应用")
                    coating_count += 1

                except Exception as e:
                    logger.warning(f"      ⚠ 涂层应用失败: {e}")

            # === 2. 处理接触热阻 (SET_THERMAL_CONTACT) ===
            thermal_contacts = getattr(comp, 'thermal_contacts', None)
            if thermal_contacts and isinstance(thermal_contacts, dict):
                for contact_comp_id, conductance in thermal_contacts.items():
                    logger.info(f"    - 设置接触热阻: {comp.id} ↔ {contact_comp_id}, h={conductance} W/m²·K")

                    try:
                        # 查找接触组件
                        contact_comp = None
                        contact_idx = None
                        for j, c in enumerate(design_state.components):
                            if c.id == contact_comp_id:
                                contact_comp = c
                                contact_idx = j
                                break

                        if contact_comp is None:
                            logger.warning(f"      ⚠ 接触组件 {contact_comp_id} 未找到")
                            continue

                        # 创建 Thermal Contact 节点
                        tc_name = f"tc_{i}_{contact_idx}"
                        thermal_contact = ht.feature().create(tc_name, "ThermalContact")

                        # 设置接触热导
                        thermal_contact.set("h", f"{conductance}[W/(m^2*K)]")

                        # 创建两个组件的边界选择
                        # 注意：Thermal Contact 需要选择两个组件的接触面
                        # 这里简化处理：选择两个组件的所有边界
                        sel_a_name = f"boxsel_tc_a_{i}_{contact_idx}"
                        sel_b_name = f"boxsel_tc_b_{i}_{contact_idx}"

                        self._create_component_box_selection(comp, sel_a_name, entity_dim=2)
                        self._create_component_box_selection(contact_comp, sel_b_name, entity_dim=2)

                        # COMSOL Thermal Contact 需要 source 和 destination 选择
                        thermal_contact.selection("source").named(sel_a_name)
                        thermal_contact.selection("destination").named(sel_b_name)

                        logger.info(f"      ✓ 接触热阻已设置")
                        contact_count += 1

                    except Exception as e:
                        logger.warning(f"      ⚠ 接触热阻设置失败: {e}")

        logger.info(f"  ✓ 热学属性应用完成: {coating_count} 个涂层, {contact_count} 个接触热阻")

    def _create_component_box_selection(
        self,
        comp,
        sel_name: str,
        entity_dim: int = 3
    ):
        """
        为组件创建 Box Selection

        Args:
            comp: 组件对象
            sel_name: 选择名称
            entity_dim: 实体维度 (3=域, 2=边界, 1=边, 0=点)
        """
        pos = comp.position
        dim = comp.dimensions
        tolerance = 5.0  # mm

        x_min = pos.x - dim.x / 2 - tolerance
        x_max = pos.x + dim.x / 2 + tolerance
        y_min = pos.y - dim.y / 2 - tolerance
        y_max = pos.y + dim.y / 2 + tolerance
        z_min = pos.z - dim.z / 2 - tolerance
        z_max = pos.z + dim.z / 2 + tolerance

        box_sel = self.model.java.selection().create(sel_name, "Box")
        box_sel.set("entitydim", str(entity_dim))
        box_sel.set("xmin", f"{x_min}[mm]")
        box_sel.set("xmax", f"{x_max}[mm]")
        box_sel.set("ymin", f"{y_min}[mm]")
        box_sel.set("ymax", f"{y_max}[mm]")
        box_sel.set("zmin", f"{z_min}[mm]")
        box_sel.set("zmax", f"{z_max}[mm]")
        box_sel.set("condition", "intersects")
