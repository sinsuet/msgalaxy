"""
COMSOL simulation driver facade.

The runtime implementation is decomposed into `simulation/comsol/` mixins:
- model building
- solver scheduling
- dataset evaluation
- result extraction
- thermal operators
- artifact storage
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.exceptions import ComsolConnectionError, SimulationError
from core.logger import get_logger
from core.protocol import SimulationRequest, SimulationResult
from simulation.base import SimulationDriver
from simulation.comsol import (
    ComsolArtifactStoreMixin,
    ComsolDatasetEvaluatorMixin,
    ComsolFeatureDomainAuditMixin,
    ComsolModelBuilderMixin,
    ComsolResultExtractorMixin,
    ComsolSolverSchedulerMixin,
    ComsolThermalOperatorMixin,
)

logger = get_logger(__name__)


class ComsolDriver(
    ComsolArtifactStoreMixin,
    ComsolDatasetEvaluatorMixin,
    ComsolFeatureDomainAuditMixin,
    ComsolResultExtractorMixin,
    ComsolThermalOperatorMixin,
    ComsolModelBuilderMixin,
    ComsolSolverSchedulerMixin,
    SimulationDriver,
):
    """COMSOL driver with dynamic STEP import and multiphysics extraction."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.environment = config.get("environment", "orbit")
        self.client: Optional[Any] = None
        self.model: Optional[Any] = None
        self.save_mph_each_eval = bool(config.get("save_mph_each_eval", False))
        self.save_mph_on_failure = bool(config.get("save_mph_on_failure", True))
        self.save_mph_only_latest = bool(config.get("save_mph_only_latest", False))
        self._last_heat_binding_report: Dict[str, Any] = {
            "active_components": 0,
            "assigned_count": 0,
            "ambiguous_components": [],
            "disambiguated_components": [],
            "failed_components": [],
        }
        self.last_saved_mph_path: str = ""
        self.saved_mph_records: list[Dict[str, Any]] = []
        self.enable_structural_real = bool(config.get("enable_structural_real", True))
        self.enable_power_network_real = bool(config.get("enable_power_network_real", True))
        self.enable_power_comsol_real = bool(config.get("enable_power_comsol_real", True))
        self.enable_coupled_multiphysics_real = bool(
            config.get("enable_coupled_multiphysics_real", True)
        )
        self.structural_allowable_stress_mpa = float(config.get("structural_allowable_stress_mpa", 150.0))
        self.structural_modal_count = max(1, int(config.get("structural_modal_count", 6)))
        self.structural_launch_accel_g = float(config.get("structural_launch_accel_g", 6.0))
        self.structural_youngs_modulus_gpa = float(config.get("structural_youngs_modulus_gpa", 70.0))
        self.structural_poissons_ratio = float(config.get("structural_poissons_ratio", 0.33))
        self.structural_density_kg_m3 = float(config.get("structural_density_kg_m3", 2700.0))
        self.electrical_conductivity_s_per_m = float(
            config.get("electrical_conductivity_s_per_m", 3.5e7)
        )
        self.power_source_current_scale = float(config.get("power_source_current_scale", 1.0))
        self.power_sink_current_scale = float(config.get("power_sink_current_scale", 1.0))
        power_terminal_mode = str(config.get("power_terminal_mode", "auto") or "auto").strip().lower()
        if power_terminal_mode not in {"auto", "voltage", "current"}:
            power_terminal_mode = "auto"
        self.power_terminal_mode = power_terminal_mode
        self.power_network_cable_resistance_ohm_per_m = float(
            config.get("power_network_cable_resistance_ohm_per_m", 0.0085)
        )
        self.power_network_k_neighbors = max(1, int(config.get("power_network_k_neighbors", 3)))
        self.power_network_congestion_alpha = float(config.get("power_network_congestion_alpha", 0.12))
        self._structural_setup_ok = False
        self._power_setup_ok = False
        self._coupled_setup_ok = False
        logger.info("COMSOL驱动器初始化: dynamic-step")

    def connect(self) -> bool:
        """Connect to COMSOL client."""
        if self.connected:
            logger.info("COMSOL已连接")
            return True
        try:
            logger.info("正在连接COMSOL...")
            import mph

            self.client = mph.start()
            logger.info("✓ COMSOL客户端启动成功")
            self.model = None
            self.connected = True
            return True
        except ImportError:
            raise ComsolConnectionError(
                "无法导入mph模块。请安装 MPh 库: pip install mph，"
                "并确保本机已安装可用 COMSOL。"
            )
        except Exception as exc:
            raise ComsolConnectionError(f"COMSOL连接失败: {exc}")

    def disconnect(self):
        """Disconnect COMSOL client."""
        if self.client:
            try:
                self.client.disconnect()
                logger.info("COMSOL连接已关闭")
            except Exception as exc:
                logger.warning(f"断开连接时出错: {exc}")
            finally:
                self.client = None
                self.model = None
                self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """Run one COMSOL simulation request via dynamic model path."""
        if not self.connected:
            self.connect()
        if not self.validate_design_state(request.design_state):
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message="设计状态无效",
            )
        return self._run_dynamic_simulation(request)

    def evaluate_expression(self, expression: str, unit: str = None) -> float:
        """Evaluate COMSOL expression on current model."""
        if not self.connected:
            self.connect()
        try:
            if unit:
                return float(self.model.evaluate(expression, unit=unit))
            return float(self.model.evaluate(expression))
        except Exception as exc:
            raise SimulationError(f"计算表达式失败: {exc}")

    def export_results(self, output_file: str, dataset: str = None):
        """Export simulation results."""
        if not self.connected:
            raise SimulationError("COMSOL未连接")
        try:
            if dataset:
                self.model.export(output_file, dataset)
            else:
                self.model.export(output_file)
            logger.info(f"结果已导出到: {output_file}")
        except Exception as exc:
            raise SimulationError(f"导出结果失败: {exc}")
