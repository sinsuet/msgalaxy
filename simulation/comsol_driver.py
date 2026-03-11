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

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

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
from simulation.comsol.field_registry import COMSOL_FIELD_REGISTRY_VERSION, build_field_registry_manifest, get_field_spec
from simulation.comsol.metric_contracts import (
    COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION,
    build_simulation_metric_unit_contract,
)
from simulation.comsol.physics_profiles import (
    COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
    COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
    DEFAULT_COMSOL_PHYSICS_PROFILE,
    PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
    PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
    PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
    build_contract_bundle,
    build_physics_profile_manifest,
    build_source_claim,
    materialize_contract_payload,
    normalize_diagnostic_simplifications,
    normalize_physics_profile,
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
        self.requested_physics_profile = normalize_physics_profile(
            config.get("physics_profile", DEFAULT_COMSOL_PHYSICS_PROFILE)
        )
        self.physics_profile = self.requested_physics_profile
        self.orbital_thermal_loads_available = bool(
            config.get("orbital_thermal_loads_available", False)
        )
        self.initial_temperature_k = float(config.get("initial_temperature_k", 293.15))
        self.surface_temperature_k = float(config.get("surface_temperature_k", 273.15))
        self.ambient_temperature_k = float(
            config.get("ambient_temperature_k", self.initial_temperature_k)
        )
        self._active_profile_simplifications: list[str] = []
        self._field_export_registry = build_field_registry_manifest()
        self._physics_profile_contract = build_physics_profile_manifest()
        self._source_claim: Dict[str, Any] = {}
        self._contract_bundle: Dict[str, Any] = {}
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
        self.structural_lateral_accel_ratio = float(config.get("structural_lateral_accel_ratio", 0.0))
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
        self._reset_profile_contract_state()
        logger.info("COMSOL驱动器初始化: dynamic-step")

    def _uses_canonical_thermal_path(self) -> bool:
        requested = bool(
            self.requested_physics_profile
            in {
                PHYSICS_PROFILE_THERMAL_STATIC_CANONICAL,
                PHYSICS_PROFILE_THERMAL_ORBITAL_CANONICAL,
                PHYSICS_PROFILE_ELECTRO_THERMO_STRUCTURAL_CANONICAL,
            }
        )
        if not requested:
            return False
        return bool(self.config.get("enable_canonical_thermal_path", False))

    def _uses_diagnostic_power_scaling(self) -> bool:
        return not self._uses_canonical_thermal_path()

    def _uses_power_continuation_ramp(self) -> bool:
        configured = self.config.get("enable_power_continuation_ramp", None)
        if configured is not None:
            return bool(configured)
        return True

    def _heat_source_power_density_expression(self, power_density_w_m3: float) -> str:
        if self._uses_power_continuation_ramp():
            return f"{power_density_w_m3} * P_scale [W/m^3]"
        return f"{power_density_w_m3}[W/m^3]"

    def _reset_profile_contract_state(self) -> None:
        self.physics_profile = self.requested_physics_profile
        self._active_profile_simplifications = []
        self._source_claim = self._build_source_claim()

    def _mark_profile_simplification(self, marker: str) -> None:
        normalized = normalize_diagnostic_simplifications([marker])
        for item in normalized:
            if item not in self._active_profile_simplifications:
                self._active_profile_simplifications.append(item)

    def _build_source_claim(
        self,
        *,
        structural_runtime: Optional[Dict[str, Any]] = None,
        power_runtime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        struct_rt = dict(structural_runtime or {})
        power_rt = dict(power_runtime or {})
        claim = build_source_claim(
            requested_profile=self.requested_physics_profile,
            active_simplifications=list(self._active_profile_simplifications),
            orbital_thermal_loads_available=bool(self.orbital_thermal_loads_available),
            structural_enabled=bool(self.enable_structural_real),
            structural_setup_ok=struct_rt.get("setup_ok", self._structural_setup_ok),
            power_comsol_enabled=bool(self.enable_power_comsol_real),
            power_setup_ok=power_rt.get("setup_ok", self._power_setup_ok),
            power_network_enabled=bool(self.enable_power_network_real),
        )
        self.physics_profile = str(claim.get("physics_profile", self.requested_physics_profile))
        self._source_claim = dict(claim)
        self._contract_bundle = build_contract_bundle(
            claim,
            field_registry_version=COMSOL_FIELD_REGISTRY_VERSION,
            physics_profile_contract_version=COMSOL_PHYSICS_PROFILE_CONTRACT_VERSION,
            profile_audit_digest_version=COMSOL_PROFILE_AUDIT_DIGEST_VERSION,
            simulation_metric_unit_contract_version=(
                COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION
            ),
        )
        return dict(claim)

    def _attach_contract_metadata(
        self,
        raw_data: Optional[Dict[str, Any]] = None,
        *,
        structural_runtime: Optional[Dict[str, Any]] = None,
        power_runtime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(raw_data or {})
        claim = self._build_source_claim(
            structural_runtime=structural_runtime,
            power_runtime=power_runtime,
        )
        payload["source_claim"] = dict(claim)
        return materialize_contract_payload(
            payload,
            claim=claim,
            contract_bundle=self._contract_bundle,
            field_export_registry=self._field_export_registry,
            physics_profile_contract=self._physics_profile_contract,
            simulation_metric_unit_contract=build_simulation_metric_unit_contract(),
        )

    def get_registered_field_spec(self, field_name: str) -> Dict[str, Any]:
        spec = get_field_spec(field_name)
        return {
            "registry_key": spec.key,
            "label": spec.label,
            "expression": spec.expression,
            "expression_candidates": list(spec.expression_candidates),
            "unit": spec.unit,
            "export_basename": spec.export_basename,
            "dataset_role": spec.dataset_role,
        }

    def _build_dataset_candidates_for_registered_field(self, field_name: str) -> list[Optional[str]]:
        spec = get_field_spec(field_name)
        dataset_role = str(spec.dataset_role or "").strip().lower()

        if dataset_role == "thermal_stationary":
            return list(self._build_dataset_candidates(prefer_modal=False))

        if dataset_role == "structural_stationary":
            general_candidates = list(self._build_dataset_candidates(prefer_modal=False))
            modal_dataset = self._select_modal_result_dataset(
                dataset_candidates=general_candidates
            )
            structural_dataset = self._select_structural_stationary_dataset(
                dataset_candidates=general_candidates
            )

            ordered: list[Optional[str]] = []
            if structural_dataset is not None:
                ordered.append(structural_dataset)
            for candidate in general_candidates:
                if candidate == modal_dataset and modal_dataset != structural_dataset:
                    continue
                if candidate in ordered:
                    continue
                ordered.append(candidate)
            if not ordered:
                ordered = [None]
            return ordered

        return [None]

    def resolve_registered_field(
        self,
        field_name: str,
        *,
        dataset_candidates: Optional[Sequence[Optional[str]]] = None,
    ) -> Dict[str, Any]:
        spec = get_field_spec(field_name)
        candidates = list(dataset_candidates or self._build_dataset_candidates_for_registered_field(spec.key))
        if not candidates:
            candidates = [None]

        for expression in list(spec.expression_candidates):
            for dataset in candidates:
                try:
                    value = self._evaluate_expression_candidates(
                        expressions=[str(expression)],
                        unit=str(spec.unit or "") or None,
                        datasets=[dataset] if dataset is not None else [None],
                        reducer="max",
                    )
                except Exception:
                    value = None
                if value is None:
                    continue
                return {
                    "registry_key": spec.key,
                    "label": spec.label,
                    "expression": str(expression),
                    "unit": spec.unit,
                    "dataset": dataset,
                    "dataset_candidates": [item for item in candidates if item is not None],
                    "dataset_role": spec.dataset_role,
                    "export_basename": spec.export_basename,
                }

        raise SimulationError(f"注册字段无法解析: {field_name}")

    def _create_result_export_node(self, *, dataset: Optional[str] = None) -> tuple[Any, str]:
        if self.model is None:
            raise SimulationError("COMSOL模型不可用")

        export_root = self.model.java.result().export()
        export_tag = f"reg_export_{Path.cwd().name}_{abs(hash((id(self.model), dataset))) % 10_000_000}"
        try:
            export_root.remove(export_tag)
        except Exception:
            pass

        create_errors: list[str] = []
        node = None
        if dataset:
            for args in ((export_tag, str(dataset), "Data"), (export_tag, str(dataset), "data")):
                try:
                    node = export_root.create(*args)
                    break
                except Exception as exc:
                    create_errors.append(str(exc))
        if node is None:
            for args in ((export_tag, "Data"), (export_tag, "data")):
                try:
                    node = export_root.create(*args)
                    break
                except Exception as exc:
                    create_errors.append(str(exc))
        if node is None:
            raise SimulationError(
                "COMSOL 导出节点创建失败: " + " | ".join(create_errors[-3:])
            )
        if dataset:
            for key in ("data", "dataset"):
                try:
                    node.set(key, str(dataset))
                except Exception:
                    continue
        return node, export_tag

    def export_registered_field(
        self,
        field_name: str,
        output_file: str,
        *,
        dataset: Optional[str] = None,
        export_kind: str = "text",
        resolution: Optional[Sequence[int]] = None,
    ) -> Dict[str, Any]:
        if not self.connected:
            raise SimulationError("COMSOL未连接")
        if self.model is None:
            raise SimulationError("COMSOL模型不可用")

        resolved = self.resolve_registered_field(
            field_name,
            dataset_candidates=[dataset] if dataset is not None else None,
        )
        export_kind_name = str(export_kind or "text").strip().lower()
        if export_kind_name not in {"text", "vtu"}:
            raise SimulationError(f"不支持的导出类型: {export_kind}")

        output_path = str(output_file)
        export_node = None
        export_tag = ""
        try:
            export_node, export_tag = self._create_result_export_node(
                dataset=resolved.get("dataset")
            )
            export_node.set("expr", str(resolved.get("expression", "")))
            if str(resolved.get("unit", "")).strip():
                try:
                    export_node.set("unit", str(resolved.get("unit", "")))
                except Exception:
                    pass
            export_node.set("filename", output_path)

            if export_kind_name == "vtu":
                for key, value in (("location", "fromdataset"), ("exporttype", "vtu")):
                    try:
                        export_node.set(key, value)
                    except Exception:
                        continue
            else:
                grid = list(resolution or (64, 64, 64))
                while len(grid) < 3:
                    grid.append(64)
                for key, value in (
                    ("location", "regulargrid"),
                    ("exporttype", "text"),
                    ("regulargridx3", str(int(grid[0]))),
                    ("regulargridy3", str(int(grid[1]))),
                    ("regulargridz3", str(int(grid[2]))),
                    ("gridstruct", "spreadsheet"),
                    ("includecoords", "on"),
                    ("header", "off"),
                    ("separator", ","),
                    ("fullprec", "on"),
                ):
                    try:
                        export_node.set(key, value)
                    except Exception:
                        continue

            try:
                export_node.run()
            except Exception:
                self.model.java.result().export(export_tag).run()

            logger.info(
                "注册字段已导出: field=%s expr=%s unit=%s dataset=%s path=%s",
                resolved.get("registry_key"),
                resolved.get("expression"),
                resolved.get("unit"),
                resolved.get("dataset"),
                output_path,
            )
            payload = dict(resolved)
            payload.update(
                {
                    "path": output_path,
                    "export_kind": export_kind_name,
                }
            )
            return payload
        except Exception as exc:
            raise SimulationError(f"注册字段导出失败: {field_name}: {exc}")
        finally:
            if export_tag:
                try:
                    self.model.java.result().export().remove(export_tag)
                except Exception:
                    pass

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

    def export_results(
        self,
        output_file: str,
        dataset: str = None,
        *,
        field_name: Optional[str] = None,
        export_kind: str = "text",
        resolution: Optional[Sequence[int]] = None,
    ):
        """Export simulation results."""
        if not self.connected:
            raise SimulationError("COMSOL未连接")
        if field_name:
            return self.export_registered_field(
                str(field_name),
                output_file,
                dataset=dataset,
                export_kind=export_kind,
                resolution=resolution,
            )
        try:
            if dataset:
                self.model.export(output_file, dataset)
            else:
                self.model.export(output_file)
            logger.info(f"结果已导出到: {output_file}")
        except Exception as exc:
            raise SimulationError(f"导出结果失败: {exc}")
