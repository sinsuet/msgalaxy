from __future__ import annotations

from typing import Any, Dict

from core.logger import get_logger
from core.protocol import SimulationRequest, SimulationResult, ViolationItem
from simulation.contracts import (
    POWER_SOURCE_ONLINE_COMSOL,
    POWER_SOURCE_ONLINE_COMSOL_PARTIAL,
    POWER_SOURCE_NETWORK_SOLVER,
    POWER_SOURCE_NETWORK_SOLVER_PARTIAL,
    THERMAL_SOURCE_ONLINE_COMSOL,
    THERMAL_SOURCE_PENALTY,
    STRUCTURAL_SOURCE_ONLINE_COMSOL,
    STRUCTURAL_SOURCE_ONLINE_COMSOL_PARTIAL,
)

logger = get_logger(__name__)


class ComsolSolverSchedulerMixin:
    """Solve scheduling and fallback control for dynamic COMSOL runs."""

    def _run_dynamic_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        Dynamic simulation flow:
        - resolve STEP
        - build model
        - solve (power ramping)
        - extract multiphysics metrics
        """
        try:
            logger.info("运行COMSOL仿真（动态模式）...")

            def _collect_feature_domain_audit(
                *,
                heat_binding_report: Dict[str, Any] | None = None,
                structural_runtime: Dict[str, Any] | None = None,
                power_runtime: Dict[str, Any] | None = None,
                coupled_runtime: Dict[str, Any] | None = None,
            ) -> Dict[str, Any]:
                audit_builder = getattr(self, "_build_comsol_feature_domain_audit", None)
                if not callable(audit_builder):
                    return {}
                try:
                    return dict(
                        audit_builder(
                            design_state=request.design_state,
                            heat_binding_report=dict(heat_binding_report or {}),
                            structural_runtime=dict(structural_runtime or {}),
                            power_runtime=dict(power_runtime or {}),
                            coupled_runtime=dict(coupled_runtime or {}),
                        )
                        or {}
                    )
                except Exception as audit_exc:
                    logger.warning("  ⚠ COMSOL feature/domain 审计构建失败: %s", audit_exc)
                    return {
                        "enabled": True,
                        "passed": False,
                        "failed_checks": ["audit_runtime_error"],
                        "error": str(audit_exc),
                    }

            step_file = self._get_or_generate_step_file(request)

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
                raw_data["comsol_feature_domain_audit"] = _collect_feature_domain_audit(
                    heat_binding_report=dict(self._last_heat_binding_report or {}),
                )
                model_build_result.raw_data = raw_data
                return model_build_result

            heat_binding_report = dict(self._last_heat_binding_report or {})
            active_heat_components = int(heat_binding_report.get("active_components", 0))
            assigned_heat_sources = int(heat_binding_report.get("assigned_count", 0))

            if active_heat_components > 0 and assigned_heat_sources <= 0:
                logger.error("  ✗ 严重错误: 存在发热组件但 0 个热源绑定成功，终止该次 COMSOL 求解并返回惩罚。")
                saved_path = ""
                if self.save_mph_on_failure:
                    saved_path = self._save_mph_model(request, reason="heat_binding_failed")
                raw_data = {"heat_binding_report": heat_binding_report}
                if saved_path:
                    raw_data["mph_model_path"] = str(saved_path)
                if self.saved_mph_records:
                    raw_data["mph_save_records"] = list(self.saved_mph_records[-5:])
                raw_data["comsol_feature_domain_audit"] = _collect_feature_domain_audit(
                    heat_binding_report=heat_binding_report,
                )
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

            logger.info("  求解物理场（T⁴ 辐射边界 + 功率斜坡加载）...")
            solve_success = False
            structural_runtime: Dict[str, Any] = {
                "enabled": bool(self.enable_structural_real),
                "setup_ok": bool(self._structural_setup_ok),
                "stat_solved": False,
                "modal_solved": False,
                "error": "",
            }
            power_runtime: Dict[str, Any] = {
                "enabled": bool(self.enable_power_comsol_real),
                "setup_ok": bool(self._power_setup_ok),
                "stat_solved": False,
                "error": "",
            }
            coupled_runtime: Dict[str, Any] = {
                "enabled": bool(self.enable_coupled_multiphysics_real),
                "setup_ok": bool(self._coupled_setup_ok),
                "stat_solved": False,
                "error": "",
            }
            try:
                ramping_steps = ["0.01", "0.20", "1.0"]
                for scale in ramping_steps:
                    logger.info(f"    - 执行稳态求解 (功率缩放 P_scale = {scale})...")
                    self.model.java.param().set("P_scale", scale)
                    self.model.java.study("std1").run()
                    logger.info(f"      ✓ P_scale={scale} 求解成功")
                logger.info("  ✓ 功率斜坡加载完成，求解成功")
                solve_success = True
                if bool(self.enable_power_comsol_real) and bool(self._power_setup_ok):
                    power_runtime = self._run_power_studies()
                if bool(self.enable_coupled_multiphysics_real) and bool(self._coupled_setup_ok):
                    coupled_runtime = self._run_coupled_study()
                if bool(self.enable_structural_real) and bool(self._structural_setup_ok):
                    structural_runtime = self._run_structural_studies()
            except Exception as solve_error:
                logger.warning(f"  ⚠ 求解发散或失败: {solve_error}")
                logger.warning(f"  Java 异常详情: {str(solve_error)}")
                logger.warning("  返回惩罚分，不中断优化循环")

            saved_path = ""
            if solve_success and self.save_mph_each_eval:
                saved_path = self._save_mph_model(request, reason="solve_success")
            if (not solve_success) and self.save_mph_on_failure:
                saved_path = self._save_mph_model(request, reason="solve_failure")

            if not solve_success:
                fallback_metrics = {
                    "max_temp": 999.0,
                    "avg_temp": 999.0,
                    "min_temp": 999.0,
                }
                metric_sources = {
                    "thermal_source": THERMAL_SOURCE_PENALTY,
                    "structural_source": "",
                    "power_source": "",
                }
                if bool(self.enable_power_network_real) and request.design_state is not None:
                    try:
                        power_metrics = self._solve_power_network_metrics(request.design_state)
                        fallback_metrics.update(
                            {
                                "total_power": float(power_metrics.get("total_power", 0.0)),
                                "peak_power": float(power_metrics.get("peak_power", 0.0)),
                                "power_margin": float(power_metrics.get("power_margin", 0.0)),
                                "voltage_drop": float(power_metrics.get("voltage_drop", 0.0)),
                            }
                        )
                        metric_sources["power_source"] = POWER_SOURCE_NETWORK_SOLVER
                    except Exception as power_exc:
                        logger.warning("  ⚠ 求解失败分支电源网络计算失败: %s", power_exc)
                feature_domain_audit = _collect_feature_domain_audit(
                    heat_binding_report=heat_binding_report,
                    structural_runtime=structural_runtime,
                    power_runtime=power_runtime,
                    coupled_runtime=coupled_runtime,
                )
                return SimulationResult(
                    success=False,
                    metrics=fallback_metrics,
                    violations=[],
                    raw_data={
                        "heat_binding_report": heat_binding_report,
                        "mph_model_path": str(saved_path or self.last_saved_mph_path or ""),
                        "mph_save_records": list(self.saved_mph_records[-5:]),
                        "structural_runtime": dict(structural_runtime),
                        "power_runtime": dict(power_runtime),
                        "coupled_runtime": dict(coupled_runtime),
                        "metric_sources": metric_sources,
                        "comsol_feature_domain_audit": feature_domain_audit,
                    },
                    error_message="COMSOL求解发散",
                )

            metrics = self._extract_dynamic_results(
                design_state=request.design_state,
                structural_runtime=structural_runtime,
                power_runtime=power_runtime,
                coupled_runtime=coupled_runtime,
            )
            logger.info(f"  仿真完成: {metrics}")

            violations = self.check_constraints(metrics)
            metric_sources = {
                "thermal_source": (
                    THERMAL_SOURCE_ONLINE_COMSOL
                    if float(metrics.get("max_temp", 9999.0)) < 9000.0
                    else THERMAL_SOURCE_PENALTY
                ),
                "structural_source": "",
                "power_source": "",
                "structural_metric_keys": [],
                "power_metric_keys": [],
            }
            structural_keys = sorted(
                [
                    key
                    for key in ("max_stress", "max_displacement", "first_modal_freq", "safety_factor")
                    if key in metrics
                ]
            )
            power_keys = sorted(
                [
                    key
                    for key in ("total_power", "peak_power", "power_margin", "voltage_drop")
                    if key in metrics
                ]
            )
            metric_sources["structural_metric_keys"] = structural_keys
            metric_sources["power_metric_keys"] = power_keys
            if structural_keys:
                metric_sources["structural_source"] = (
                    STRUCTURAL_SOURCE_ONLINE_COMSOL
                    if len(structural_keys) == 4
                    else STRUCTURAL_SOURCE_ONLINE_COMSOL_PARTIAL
                )
            if power_keys:
                if bool(power_runtime.get("stat_solved", False)):
                    metric_sources["power_source"] = (
                        POWER_SOURCE_ONLINE_COMSOL
                        if len(power_keys) == 4
                        else POWER_SOURCE_ONLINE_COMSOL_PARTIAL
                    )
                else:
                    metric_sources["power_source"] = (
                        POWER_SOURCE_NETWORK_SOLVER
                        if len(power_keys) == 4
                        else POWER_SOURCE_NETWORK_SOLVER_PARTIAL
                    )

            feature_domain_audit = _collect_feature_domain_audit(
                heat_binding_report=heat_binding_report,
                structural_runtime=structural_runtime,
                power_runtime=power_runtime,
                coupled_runtime=coupled_runtime,
            )

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in violations],
                raw_data={
                    "heat_binding_report": heat_binding_report,
                    "mph_model_path": str(saved_path or self.last_saved_mph_path or ""),
                    "mph_save_records": list(self.saved_mph_records[-5:]),
                    "structural_runtime": dict(structural_runtime),
                    "power_runtime": dict(power_runtime),
                    "coupled_runtime": dict(coupled_runtime),
                    "metric_sources": metric_sources,
                    "comsol_feature_domain_audit": feature_domain_audit,
                },
            )

        except Exception as exc:
            logger.error(f"COMSOL动态仿真失败: {exc}", exc_info=True)
            fallback_structural_runtime = {
                "enabled": bool(self.enable_structural_real),
                "setup_ok": bool(self._structural_setup_ok),
                "stat_solved": False,
                "modal_solved": False,
                "error": str(exc),
            }
            fallback_power_runtime = {
                "enabled": bool(self.enable_power_comsol_real),
                "setup_ok": bool(self._power_setup_ok),
                "stat_solved": False,
                "error": str(exc),
            }
            fallback_coupled_runtime = {
                "enabled": bool(self.enable_coupled_multiphysics_real),
                "setup_ok": bool(self._coupled_setup_ok),
                "stat_solved": False,
                "error": str(exc),
            }
            feature_domain_audit = {}
            audit_builder = getattr(self, "_build_comsol_feature_domain_audit", None)
            if callable(audit_builder):
                try:
                    feature_domain_audit = dict(
                        audit_builder(
                            design_state=request.design_state,
                            heat_binding_report=dict(self._last_heat_binding_report or {}),
                            structural_runtime=fallback_structural_runtime,
                            power_runtime=fallback_power_runtime,
                            coupled_runtime=fallback_coupled_runtime,
                        )
                        or {}
                    )
                except Exception as audit_exc:
                    logger.warning("  ⚠ COMSOL feature/domain 审计构建失败: %s", audit_exc)
                    feature_domain_audit = {
                        "enabled": True,
                        "passed": False,
                        "failed_checks": ["audit_runtime_error"],
                        "error": str(audit_exc),
                    }
            return SimulationResult(
                success=False,
                metrics={
                    "max_temp": 9999.0,
                    "avg_temp": 9999.0,
                    "min_temp": 9999.0,
                },
                violations=[],
                raw_data={
                    "mph_model_path": str(self.last_saved_mph_path or ""),
                    "mph_save_records": list(self.saved_mph_records[-5:]),
                    "structural_runtime": fallback_structural_runtime,
                    "power_runtime": fallback_power_runtime,
                    "coupled_runtime": fallback_coupled_runtime,
                    "comsol_feature_domain_audit": feature_domain_audit,
                },
                error_message=str(exc),
            )
