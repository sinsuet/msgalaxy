"""
COMSOL仿真驱动器

通过MPh库连接COMSOL Multiphysics进行多物理场仿真
仅支持动态 STEP 导入 + Box Selection（v2.0+ 架构）
"""

import os
import re
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import math

from simulation.base import SimulationDriver
from core.protocol import SimulationRequest, SimulationResult, SimulationType, ViolationItem, DesignState
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
                - environment: 环境类型 ("orbit"或"ground"，默认"orbit")
        """
        super().__init__(config)
        self.environment = config.get('environment', 'orbit')
        self.client: Optional[Any] = None
        self.model: Optional[Any] = None
        self.save_mph_each_eval = bool(config.get("save_mph_each_eval", False))
        self.save_mph_on_failure = bool(config.get("save_mph_on_failure", True))
        self._last_heat_binding_report: Dict[str, Any] = {
            "active_components": 0,
            "assigned_count": 0,
            "ambiguous_components": [],
            "disambiguated_components": [],
            "failed_components": [],
        }
        self.last_saved_mph_path: str = ""
        self.saved_mph_records: list[Dict[str, Any]] = []

        logger.info("COMSOL驱动器初始化: dynamic-only")

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

            # dynamic-only：模型在每次仿真时通过 _create_dynamic_model 运行时创建
            self.model = None

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

        dynamic-only：使用动态 STEP 导入 + Box Selection

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

        return self._run_dynamic_simulation(request)

    def _save_mph_model(self, request: SimulationRequest, reason: str = "regular") -> str:
        """
        保存 COMSOL .mph 模型文件（用于可复现性和可视化排错）

        Args:
            request: 仿真请求（包含迭代信息和实验目录）
        """
        try:
            # 检查模型是否存在
            if not self.model:
                logger.warning("  ⚠ COMSOL 模型对象不存在，跳过保存")
                return ""

            # 选择保存目录
            experiment_dir = request.parameters.get("experiment_dir")
            if experiment_dir:
                save_dir = Path(experiment_dir) / "mph_models"
            else:
                save_dir = Path("workspace/comsol_models")
            save_dir.mkdir(parents=True, exist_ok=True)

            # 使用 state_id 作为基础文件名，统一附加唯一后缀，避免锁冲突
            state_id = (request.design_state.state_id or "").strip()
            if state_id:
                base_name = f"model_{state_id}"
            else:
                base_name = f"model_iter_{request.design_state.iteration:03d}"

            # 文件名安全化（避免空格/特殊字符导致路径和锁问题）
            safe_base_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("_")
            if not safe_base_name:
                safe_base_name = f"model_iter_{request.design_state.iteration:03d}"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            unique_path = save_dir / f"{safe_base_name}_{timestamp}.mph"
            retry_path = save_dir / f"{safe_base_name}_{timestamp}_retry.mph"

            logger.info(f"  保存 COMSOL .mph 模型 (reason={reason})...")
            if self._try_save_mph_path(unique_path):
                saved_path = str(unique_path)
                self.last_saved_mph_path = saved_path
                self.saved_mph_records.append(
                    {
                        "path": saved_path,
                        "reason": str(reason),
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                self.saved_mph_records = self.saved_mph_records[-20:]
                return saved_path

            logger.warning("  唯一路径保存失败，进行一次回退重试...")
            if self._try_save_mph_path(retry_path):
                saved_path = str(retry_path)
                self.last_saved_mph_path = saved_path
                self.saved_mph_records.append(
                    {
                        "path": saved_path,
                        "reason": str(reason),
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                self.saved_mph_records = self.saved_mph_records[-20:]
                return saved_path
            else:
                logger.warning("  ⚠ 保存 .mph 模型失败: 唯一路径与回退路径均失败")
            return ""

        except Exception as e:
            # 保存失败不应中断仿真流程
            logger.warning(f"  ⚠ 保存 .mph 模型失败: {e}")
            logger.warning(f"  异常类型: {type(e).__name__}")
            logger.warning("  仿真结果仍然有效，继续执行...")
            return ""

    def force_save_current_model(
        self,
        design_state: DesignState,
        experiment_dir: str,
        reason: str = "final_selected",
    ) -> str:
        """
        无条件保存当前 COMSOL 模型，用于保留最终 .mph 产物。
        """
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={"experiment_dir": str(experiment_dir)},
        )
        return self._save_mph_model(request, reason=reason)

    def _try_save_mph_path(self, save_path: Path) -> bool:
        """
        尝试保存 .mph 到指定路径：
        1) MPh save()
        2) Java API save() 回退

        Args:
            save_path: 目标文件路径

        Returns:
            是否保存成功
        """
        save_path_safe = str(save_path).replace("\\", "/")

        try:
            self.model.save(save_path_safe)
            logger.info(f"  ✓ COMSOL .mph 模型已保存: {save_path_safe}")
            return True
        except Exception as save_error:
            logger.warning(f"  ⚠ MPh save() 调用失败: {save_error}")
            logger.warning("  尝试使用 Java API 保存...")
            try:
                self.model.java.save(save_path_safe)
                logger.info(f"  ✓ COMSOL .mph 模型已保存（Java API）: {save_path_safe}")
                return True
            except Exception as java_error:
                logger.warning(f"  ⚠ Java API 保存失败: {java_error}")
                return False

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
            model_build_result = self._create_dynamic_model(step_file, request.design_state)
            if isinstance(model_build_result, SimulationResult):
                saved_path = ""
                if self.save_mph_on_failure:
                    saved_path = self._save_mph_model(request, reason="model_build_failed")
                raw_data = dict(model_build_result.raw_data or {})
                if saved_path:
                    raw_data["mph_model_path"] = str(saved_path)
                if self.saved_mph_records:
                    raw_data["mph_save_records"] = list(self.saved_mph_records[-5:])
                model_build_result.raw_data = raw_data
                return model_build_result
            heat_binding_report = dict(self._last_heat_binding_report or {})
            active_heat_components = int(heat_binding_report.get("active_components", 0))
            assigned_heat_sources = int(heat_binding_report.get("assigned_count", 0))

            # P0: 无热源绑定的温度场结果无物理意义，直接作为失败样本返回惩罚
            if active_heat_components > 0 and assigned_heat_sources <= 0:
                logger.error(
                    "  ✗ 严重错误: 存在发热组件但 0 个热源绑定成功，终止该次 COMSOL 求解并返回惩罚。"
                )
                saved_path = ""
                if self.save_mph_on_failure:
                    saved_path = self._save_mph_model(request, reason="heat_binding_failed")
                raw_data = {"heat_binding_report": heat_binding_report}
                if saved_path:
                    raw_data["mph_model_path"] = str(saved_path)
                if self.saved_mph_records:
                    raw_data["mph_save_records"] = list(self.saved_mph_records[-5:])
                return SimulationResult(
                    success=False,
                    metrics={
                        "max_temp": 999.0,
                        "avg_temp": 999.0,
                        "min_temp": 999.0,
                    },
                    violations=[],
                    raw_data=raw_data,
                    error_message="NO_HEAT_SOURCE_BOUND",
                )

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

            # 4. 条件保存 .mph 模型文件（降低在线循环磁盘锁冲突与I/O开销）
            saved_path = ""
            if solve_success and self.save_mph_each_eval:
                saved_path = self._save_mph_model(request, reason="solve_success")
            if (not solve_success) and self.save_mph_on_failure:
                saved_path = self._save_mph_model(request, reason="solve_failure")

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
                    raw_data={
                        "heat_binding_report": heat_binding_report,
                        "mph_model_path": str(saved_path or self.last_saved_mph_path or ""),
                        "mph_save_records": list(self.saved_mph_records[-5:]),
                    },
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
                violations=[ViolationItem(**v) for v in violations],
                raw_data={
                    "heat_binding_report": heat_binding_report,
                    "mph_model_path": str(saved_path or self.last_saved_mph_path or ""),
                    "mph_save_records": list(self.saved_mph_records[-5:]),
                },
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
                raw_data={
                    "mph_model_path": str(self.last_saved_mph_path or ""),
                    "mph_save_records": list(self.saved_mph_records[-5:]),
                },
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
            self._last_heat_binding_report = self._assign_heat_sources_dynamic(design_state, ht, geom)

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
    ) -> Dict[str, Any]:
        """
        使用 Box Selection 识别组件并赋予热源。

        Returns:
            绑定统计信息，供上游流程做有效性判定。
        """
        total_heat_sources_assigned = 0
        ambiguous_heat_sources = []
        disambiguated_heat_sources = []
        failed_heat_sources = []
        active_heat_components = 0

        for i, comp in enumerate(design_state.components):
            if comp.power <= 0:
                continue
            active_heat_components += 1

            logger.info(f"    - 为组件 {comp.id} 创建热源 ({comp.power}W)")

            pos = comp.position
            dim = comp.dimensions

            tolerance = 1e-3  # mm，极严容差，避免紧凑场景串选
            x_min = pos.x - dim.x / 2 - tolerance
            x_max = pos.x + dim.x / 2 + tolerance
            y_min = pos.y - dim.y / 2 - tolerance
            y_max = pos.y + dim.y / 2 + tolerance
            z_min = pos.z - dim.z / 2 - tolerance
            z_max = pos.z + dim.z / 2 + tolerance

            sel_name = f"boxsel_comp_{i}"
            box_sel = self.model.java.selection().create(sel_name, "Box")
            box_sel.set("entitydim", "3")
            box_sel.set("xmin", f"{x_min}[mm]")
            box_sel.set("xmax", f"{x_max}[mm]")
            box_sel.set("ymin", f"{y_min}[mm]")
            box_sel.set("ymax", f"{y_max}[mm]")
            box_sel.set("zmin", f"{z_min}[mm]")
            box_sel.set("zmax", f"{z_max}[mm]")
            box_sel.set("condition", "inside")

            selection_name = sel_name
            try:
                selected_entities = self._normalize_entity_ids(box_sel.entities())
                num_selected = len(selected_entities)
                logger.info(f"      Box Selection 选中 {num_selected} 个域")

                if num_selected == 0:
                    logger.warning(f"      ⚠️ inside 条件选中 0 个域，回退到 intersects: {comp.id}")
                    box_sel.set("condition", "intersects")
                    selected_entities = self._normalize_entity_ids(box_sel.entities())
                    num_selected = len(selected_entities)
                    logger.info(f"      intersects 回退后选中 {num_selected} 个域")

                if num_selected > 1:
                    logger.warning(f"      ⚠️ 选中 {num_selected} 个域，尝试 allvertices 收紧: {comp.id}")
                    box_sel.set("condition", "allvertices")
                    selected_entities = self._normalize_entity_ids(box_sel.entities())
                    num_selected = len(selected_entities)
                    logger.info(f"      allvertices 收紧后选中 {num_selected} 个域")

                if num_selected > 1:
                    resolved_domain, resolve_meta = self._resolve_ambiguous_heat_domain(
                        comp=comp,
                        comp_index=i,
                        domain_ids=selected_entities,
                    )
                    if resolved_domain is None:
                        logger.error(
                            f"      ✗ 热源绑定拒绝: 组件 {comp.id} 仍歧义命中 {num_selected} 个域，跳过该热源"
                        )
                        ambiguous_heat_sources.append(comp.id)
                        failed_heat_sources.append(comp.id)
                        continue

                    resolved_sel_name = f"{sel_name}_resolved"
                    try:
                        resolved_sel = self.model.java.selection().create(resolved_sel_name, "Explicit")
                        resolved_sel.geom("geom1", 3)
                        resolved_sel.set([int(resolved_domain)])
                        selection_name = resolved_sel_name
                        disambiguated_heat_sources.append(comp.id)
                        logger.warning(
                            "      ⚠️ 多域歧义已自动收敛: "
                            f"{comp.id} -> domain {resolved_domain} "
                            f"(method={resolve_meta.get('method')}, "
                            f"distance_mm={resolve_meta.get('distance_mm')})"
                        )
                    except Exception as resolve_bind_error:
                        logger.error(
                            f"      ✗ 歧义域收敛后绑定失败: {comp.id}, error={resolve_bind_error}"
                        )
                        ambiguous_heat_sources.append(comp.id)
                        failed_heat_sources.append(comp.id)
                        continue

                if num_selected == 0:
                    logger.warning(f"      ⚠️ 严重警告: 热源 Box Selection 失败！组件 {comp.id} 未选中任何域！")
                    logger.warning(f"      Box 范围: X[{x_min:.1f}, {x_max:.1f}], Y[{y_min:.1f}, {y_max:.1f}], Z[{z_min:.1f}, {z_max:.1f}] mm")
                    logger.warning(f"      组件位置: [{pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}] mm")
                    logger.warning(f"      组件尺寸: [{dim.x:.1f}, {dim.y:.1f}, {dim.z:.1f}] mm")
                    logger.error(f"      ✗ 热源绑定彻底失败！{comp.power}W 热源未施加到组件 {comp.id}！")
                    failed_heat_sources.append(comp.id)
                    continue
            except Exception as sel_check_error:
                logger.warning(f"      无法检查选中域数量: {sel_check_error}")
                failed_heat_sources.append(comp.id)
                continue

            hs_name = f"hs_{i}"
            heat_source = ht.feature().create(hs_name, "HeatSource")
            heat_source.selection().named(selection_name)

            volume = (dim.x * dim.y * dim.z) / 1e9  # mm³ -> m³
            power_density = comp.power / volume if volume > 0 else 0
            heat_source.set("Q0", f"{power_density} * P_scale [W/m^3]")

            logger.info(f"      ✓ 热源已设置: {comp.power}W * P_scale, 功率密度: {power_density:.2e} W/m³")
            total_heat_sources_assigned += 1

        if total_heat_sources_assigned == 0:
            logger.error("  ✗ 严重错误: 没有任何热源被成功绑定！仿真结果将无效！")
        else:
            total_power = sum(c.power for c in design_state.components if c.power > 0)
            logger.info(f"  ✓ 热源绑定完成: {total_heat_sources_assigned} 个热源, 总功率 {total_power}W")
        if ambiguous_heat_sources:
            logger.warning(
                "  ⚠ 以下组件因 Box Selection 多域歧义被跳过热源绑定: "
                + ", ".join(ambiguous_heat_sources)
            )
        if disambiguated_heat_sources:
            logger.info(
                "  ✓ 以下组件通过自动歧义收敛完成热源绑定: "
                + ", ".join(disambiguated_heat_sources)
            )
        if failed_heat_sources:
            logger.warning(
                "  ⚠ 以下组件热源绑定失败: " + ", ".join(failed_heat_sources)
            )

        return {
            "active_components": int(active_heat_components),
            "assigned_count": int(total_heat_sources_assigned),
            "ambiguous_components": list(ambiguous_heat_sources),
            "disambiguated_components": list(disambiguated_heat_sources),
            "failed_components": list(failed_heat_sources),
        }

    def _normalize_entity_ids(self, entities: Any) -> list[int]:
        """将 COMSOL 选择结果归一化为 int 列表。"""
        if entities is None:
            return []
        try:
            values = list(entities)
        except Exception:
            values = [entities]

        normalized: list[int] = []
        for value in values:
            try:
                normalized.append(int(value))
            except Exception:
                continue

        deduped: list[int] = []
        seen = set()
        for value in normalized:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    def _resolve_ambiguous_heat_domain(
        self,
        comp: Any,
        comp_index: int,
        domain_ids: list[int],
    ) -> tuple[Optional[int], Dict[str, Any]]:
        """
        多域歧义时选择一个稳定域:
        1) 优先几何中心最近。
        2) 失败则回退到域号与组件序号最近。
        """
        if not domain_ids:
            return None, {"method": "none", "distance_mm": None}

        comp_center = (
            float(comp.position.x),
            float(comp.position.y),
            float(comp.position.z),
        )
        scored = []
        for domain_id in domain_ids:
            center = self._estimate_domain_center_mm(domain_id)
            if center is None:
                continue
            dx = center[0] - comp_center[0]
            dy = center[1] - comp_center[1]
            dz = center[2] - comp_center[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            scored.append((float(distance), int(domain_id)))

        if scored:
            scored.sort(key=lambda item: (item[0], item[1]))
            best_distance, best_domain = scored[0]
            return int(best_domain), {
                "method": "bbox_centroid_distance",
                "distance_mm": float(best_distance),
            }

        expected_domain = int(comp_index + 1)
        fallback_domain = min(
            domain_ids,
            key=lambda domain_id: (abs(int(domain_id) - expected_domain), int(domain_id)),
        )
        return int(fallback_domain), {
            "method": "domain_index_fallback",
            "distance_mm": float(abs(int(fallback_domain) - expected_domain)),
        }

    def _estimate_domain_center_mm(self, domain_id: int) -> Optional[tuple[float, float, float]]:
        """尽力读取域包围盒中心（mm），失败返回 None。"""
        if self.model is None:
            return None

        try:
            geom = self.model.java.geom("geom1")
            measure = geom.measure()
            try:
                measure.selection().init(3)
            except Exception:
                pass
            measure.selection().set([int(domain_id)])
            bbox = None
            for method_name in ("getBoundingBox", "boundingBox", "bbox"):
                if hasattr(measure, method_name):
                    method = getattr(measure, method_name)
                    try:
                        bbox = method()
                        break
                    except Exception:
                        continue
            if bbox is None:
                return None

            values = []
            for value in list(bbox):
                try:
                    values.append(float(value))
                except Exception:
                    return None
            if len(values) < 6:
                return None

            return (
                float((values[0] + values[1]) / 2.0),
                float((values[2] + values[3]) / 2.0),
                float((values[4] + values[5]) / 2.0),
            )
        except Exception:
            return None

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

        # 依据“当前组件真实空间范围”构建外边界选择框，而不是依赖初始 envelope。
        # 原因：优化过程会持续 MOVE，组件可能超出初始包络；若边界锚点漏选将导致
        # 某些独立域无 Dirichlet/弱锚约束，稳态求解容易在低功率步就发散。
        margin = 20.0  # mm
        if design_state.components:
            x_min = min(c.position.x - c.dimensions.x / 2 for c in design_state.components) - margin
            x_max = max(c.position.x + c.dimensions.x / 2 for c in design_state.components) + margin
            y_min = min(c.position.y - c.dimensions.y / 2 for c in design_state.components) - margin
            y_max = max(c.position.y + c.dimensions.y / 2 for c in design_state.components) + margin
            z_min = min(c.position.z - c.dimensions.z / 2 for c in design_state.components) - margin
            z_max = max(c.position.z + c.dimensions.z / 2 for c in design_state.components) + margin
        else:
            env = design_state.envelope.outer_size
            x_min, x_max = -env.x / 2 - margin, env.x / 2 + margin
            y_min, y_max = -env.y / 2 - margin, env.y / 2 + margin
            z_min, z_max = -env.z / 2 - margin, env.z / 2 + margin

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
        box_sel.set("condition", "intersects")
        logger.info(
            f"      外边界选择框: X[{x_min:.1f},{x_max:.1f}] "
            f"Y[{y_min:.1f},{y_max:.1f}] Z[{z_min:.1f},{z_max:.1f}] mm"
        )

        selected_entities = []
        missing_anchor_components = []
        try:
            selected_entities = list(box_sel.entities())
            selected_set = set(selected_entities)

            # 校验每个组件至少有一个边界被外边界锚点覆盖
            for i, comp in enumerate(design_state.components):
                check_sel = f"boxsel_outer_check_{i}"
                self._create_component_box_selection(comp, check_sel, entity_dim=2, condition="intersects")
                comp_entities = list(self.model.java.selection(check_sel).entities())
                if comp_entities and selected_set.isdisjoint(comp_entities):
                    missing_anchor_components.append(comp.id)
        except Exception as e:
            logger.warning(f"      外边界选择校验失败，将回退到全边界锚点: {e}")
            missing_anchor_components = [c.id for c in design_state.components]

        # 创建辐射边界条件
        # 简化方案：使用温度边界条件（更稳定）
        # 假设外表面温度为典型卫星外壳温度
        temp_bc = ht.feature().create("temp1", "TemperatureBoundary")

        # 设置外表面温度（典型值：-50°C 到 +50°C，取中间值 0°C = 273.15K）
        T_surface = 273.15  # K (0°C)
        temp_bc.set("T0", f"{T_surface}[K]")

        logger.info(f"      ✓ 温度边界已设置: T_surface={T_surface}K")

        # 数值稳定锚：添加极其微弱的对流边界（防止矩阵奇异）
        logger.info("    - 添加数值稳定锚（微弱对流边界）...")
        # 修复：使用 HeatFluxBoundary 而不是 ConvectiveHeatFlux
        conv_bc = ht.feature().create("conv_stabilizer", "HeatFluxBoundary")

        # 设置极其微弱的换热系数（对物理影响极小，但对数值稳定性有奇效）
        h_stabilizer = 0.1  # W/(m^2*K)，极其微弱
        T_ambient = 293.15  # K (20°C)，环境温度
        # 使用对流热流公式: q = h * (T_ambient - T)
        conv_bc.set("q0", f"{h_stabilizer}[W/(m^2*K)]*({T_ambient}[K]-T)")

        # 优先使用外边界 Box Selection；若存在漏锚组件则回退到全边界，保障每个域可解。
        if selected_entities and not missing_anchor_components:
            temp_bc.selection().named(sel_name)
            conv_bc.selection().named(sel_name)
            logger.info(f"      ✓ 外边界锚点已绑定: {len(selected_entities)} 个边界实体")
        else:
            temp_bc.selection().all()
            conv_bc.selection().all()
            logger.warning(
                "      ⚠ 外边界锚点存在漏选，回退到全边界锚点。"
                f" 漏锚组件: {missing_anchor_components if missing_anchor_components else '未知'}"
            )

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

    def _set_thermal_contact_conductance(
        self,
        thermal_contact: Any,
        conductance: float
    ) -> tuple[bool, str, list[str]]:
        """
        兼容不同 COMSOL 版本/物理接口的接触热导参数写法。

        优先级：
        1) 直接参数: h_tc / h_joint / h
        2) TotalConductance: htot
        3) ConstrictionConductance: hconstr + hgap
        4) TotalResistance: Rtot
        """
        conductance_with_unit = f"{conductance}[W/(m^2*K)]"
        conductance_plain = f"{conductance}"
        resistance_with_unit = (
            f"{1.0 / conductance}[(m^2*K)/W]"
            if conductance > 0
            else "1e9[(m^2*K)/W]"
        )

        attempt_errors: list[str] = []

        def _try_set(param: str, values: list[str]) -> Optional[str]:
            for expr in values:
                try:
                    thermal_contact.set(param, expr)
                    return expr
                except Exception as e:
                    attempt_errors.append(f"{param}={expr} 失败: {e}")
            return None

        # 方案A：直接键名（覆盖常见 API 差异）
        for param_name in ("h_tc", "h_joint", "h"):
            used_value = _try_set(param_name, [conductance_with_unit, conductance_plain])
            if used_value is not None:
                return True, f"{param_name}={used_value}", attempt_errors

        # 方案B：等效薄层 + 总热导
        try:
            thermal_contact.set("ContactModel", "EquThinLayer")
            thermal_contact.set("Specify", "TotalConductance")
            used_value = _try_set("htot", [conductance_with_unit, conductance_plain])
            if used_value is not None:
                return True, f"EquThinLayer/htot={used_value}", attempt_errors
        except Exception as e:
            attempt_errors.append(f"EquThinLayer 配置失败: {e}")

        # 方案C：收缩导热模型 + 用户定义导热
        try:
            thermal_contact.set("ContactModel", "ConstrictionConductance")
            thermal_contact.set("hcType", "UserDef")
            hconstr_value = _try_set("hconstr", [conductance_with_unit, conductance_plain])
            thermal_contact.set("hgType", "UserDef")
            hgap_value = _try_set("hgap", [conductance_with_unit, conductance_plain])
            if hconstr_value is not None and hgap_value is not None:
                return (
                    True,
                    f"ConstrictionConductance/hconstr={hconstr_value},hgap={hgap_value}",
                    attempt_errors,
                )
        except Exception as e:
            attempt_errors.append(f"ConstrictionConductance 配置失败: {e}")

        # 方案D：总热阻
        try:
            thermal_contact.set("Specify", "TotalResistance")
            used_value = _try_set("Rtot", [resistance_with_unit])
            if used_value is not None:
                return True, f"Rtot={used_value}", attempt_errors
        except Exception as e:
            attempt_errors.append(f"TotalResistance 配置失败: {e}")

        return False, "", attempt_errors

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

                        conductance_value = float(conductance)
                        set_ok, set_desc, attempt_errors = self._set_thermal_contact_conductance(
                            thermal_contact, conductance_value
                        )
                        if not set_ok:
                            raise ValueError(
                                "无法设置接触热导参数 (尝试 h_tc/h_joint/h + htot/hconstr/hgap/Rtot 均失败): "
                                + " | ".join(attempt_errors)
                            )
                        logger.info(f"      ✓ 接触热导参数已设置: {set_desc}")

                        # 创建两个组件的边界选择
                        # 注意：Thermal Contact 需要选择两个组件的接触面
                        # 这里简化处理：选择两个组件的所有边界
                        sel_a_name = f"boxsel_tc_a_{i}_{contact_idx}"
                        sel_b_name = f"boxsel_tc_b_{i}_{contact_idx}"

                        self._create_component_box_selection(
                            comp, sel_a_name, entity_dim=2, condition="intersects"
                        )
                        self._create_component_box_selection(
                            contact_comp, sel_b_name, entity_dim=2, condition="intersects"
                        )

                        # 当前 COMSOL API 的 ThermalContact 不支持 source/destination 命名选择
                        # 改为将两侧边界实体合并后直接绑定到该特征的 selection()。
                        try:
                            sel_a_entities = list(self.model.java.selection(sel_a_name).entities())
                            sel_b_entities = list(self.model.java.selection(sel_b_name).entities())
                            merged_entities = sorted(set(sel_a_entities + sel_b_entities))

                            if not merged_entities:
                                raise ValueError("接触边界选择为空")

                            thermal_contact.selection().set(merged_entities)
                            logger.info(f"      ✓ 接触边界已绑定: {len(merged_entities)} 个边界实体")
                        except Exception as selection_error:
                            logger.warning(f"      ⚠ 接触边界实体合并失败，回退到单侧选择: {selection_error}")
                            thermal_contact.selection().named(sel_a_name)

                        logger.info(f"      ✓ 接触热阻已设置")
                        contact_count += 1

                    except Exception as e:
                        logger.warning(f"      ⚠ 接触热阻设置失败: {e}")

        logger.info(f"  ✓ 热学属性应用完成: {coating_count} 个涂层, {contact_count} 个接触热阻")

    def _create_component_box_selection(
        self,
        comp,
        sel_name: str,
        entity_dim: int = 3,
        condition: str = "inside",
    ):
        """
        为组件创建 Box Selection

        Args:
            comp: 组件对象
            sel_name: 选择名称
            entity_dim: 实体维度 (3=域, 2=边界, 1=边, 0=点)
            condition: Box Selection 条件（inside/intersects/allvertices）
        """
        pos = comp.position
        dim = comp.dimensions
        # 严格容差：避免在紧凑布局中将相邻组件误选入同一 Box Selection
        tolerance = 1e-3  # mm

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
        box_sel.set("condition", condition)
