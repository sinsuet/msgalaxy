"""
Workflow Orchestrator main entry.

Responsibilities:
1. Initialize geometry/simulation/optimization modules.
2. Route optimization runs by runtime mode.
3. Manage experiment lifecycle.
4. Materialize reports and artifacts.
"""

import os
import re
import json
import time
import importlib
from typing import Optional, Dict, Any, List
from pathlib import Path
import yaml
import numpy as np
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

from core.protocol import DesignState, EvaluationResult
from core.logger import ExperimentLogger
from core.exceptions import SatelliteDesignError

from geometry.layout_engine import LayoutEngine
from geometry.layout_seed_service import packing_result_to_design_state
from geometry.metrics import (
    calculate_boundary_violation as calculate_geometry_boundary_violation,
    calculate_packing_efficiency,
    calculate_pairwise_clearance as calculate_geometry_pairwise_clearance,
)
from simulation.base import SimulationDriver
from simulation.comsol_driver import ComsolDriver
from simulation.contracts import merge_metric_sources, normalize_runtime_constraints
from simulation.engineering_proxy import (
    estimate_power_proxy_metrics,
    estimate_structural_proxy_metrics,
)
from simulation.mission_proxy import evaluate_mission_fov_interface

try:
    from simulation.physics_engine import (
        SimplifiedPhysicsEngine,
    )
except ImportError:
    SimplifiedPhysicsEngine = None

from optimization.meta_reasoner import MetaReasoner
from optimization.llm import LLMGateway, LLMProfileResolver
from optimization.llm.controllers import IntentModeler, PolicyProgrammer, StrategicPlanner
from optimization.modes.agent_loop import (
    AgentCoordinator,
    GeometryAgent,
    PowerAgent,
    StructuralAgent,
    ThermalAgent,
)
from optimization.knowledge.mass import MassRAGSystem
from workflow.modes.agent_loop import AgentLoopService
from workflow.modes.agent_loop.runtime_support import AgentLoopRuntimeSupport
from workflow.modes.mass.pipeline_service import MaaSPipelineService
from workflow.modes.mass.runtime_support import MassRuntimeSupport
from workflow.modes.vop_maas import VOPPolicyProgramService
from workflow.runtime.contracts import RuntimeContext
from workflow.runtime.mode_router import resolve_mode_runner
from workflow.runtime.runtime_facade import RuntimeFacade
from optimization.protocol import (
    GlobalContextPack,
    GeometryMetrics,
    ThermalMetrics,
    StructuralMetrics,
    PowerMetrics,
    ViolationItem,
)


class WorkflowOrchestrator(AgentLoopRuntimeSupport, MassRuntimeSupport):
    """Main workflow orchestrator."""

    def __init__(self, config_path: str = "config/system/mass/base.yaml"):
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
        if self.optimization_mode not in {"agent_loop", "mass", "vop_maas"}:
            raise SatelliteDesignError(
                f"Unsupported optimization.mode: {self.optimization_mode}. "
                "Use 'agent_loop', 'mass', or 'vop_maas'."
            )

        # 初始化日志
        self.logger = ExperimentLogger(
            base_dir=self.config.get("logging", {}).get("base_dir", "experiments"),
            run_mode=self.optimization_mode,
            run_label=self.config.get("logging", {}).get("run_label", ""),
            run_algorithm=self.config.get("optimization", {}).get("pymoo_algorithm", ""),
            run_naming_strategy=self.config.get("logging", {}).get("run_naming_strategy", "compact"),
        )

        # 初始化各模块
        self._initialize_modules()
        self._initialize_llm_controllers()
        self.maas_pipeline_service = MaaSPipelineService(host=self)
        self.vop_policy_program_service = VOPPolicyProgramService(host=self)
        self.agent_loop_service = AgentLoopService(host=self)
        self.runtime_facade = RuntimeFacade(host=self)
        self._mode_runner = resolve_mode_runner(self.optimization_mode)
        # Runtime operator credits for adaptive operator-bias tuning.
        self._maas_operator_credit_stats: Dict[str, Dict[str, Any]] = {}
        self._mission_fov_evaluator_ref: str = ""
        self._mission_fov_evaluator: Any = None
        self._mission_fov_evaluator_error: str = ""

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

    def _normalize_constraints(self, raw_constraints: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """标准化约束配置（统一键名与默认值）"""
        return normalize_runtime_constraints(raw_constraints)

    def _to_bool(self, value: Any, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(int(value))
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        return bool(default)

    def _resolve_mission_fov_evaluator(self):
        opt_cfg = dict(self.config.get("optimization", {}) or {})
        raw_ref = str(opt_cfg.get("mass_mission_fov_evaluator", "") or "").strip()
        if not raw_ref:
            self._mission_fov_evaluator_ref = ""
            self._mission_fov_evaluator = None
            self._mission_fov_evaluator_error = ""
            return None

        if (
            raw_ref == str(self._mission_fov_evaluator_ref or "")
            and callable(self._mission_fov_evaluator)
        ):
            return self._mission_fov_evaluator

        module_name = ""
        attr_name = ""
        if ":" in raw_ref:
            module_name, attr_name = raw_ref.split(":", 1)
        elif "." in raw_ref:
            module_name, attr_name = raw_ref.rsplit(".", 1)

        module_name = str(module_name or "").strip()
        attr_name = str(attr_name or "").strip()
        if not module_name or not attr_name:
            self._mission_fov_evaluator_ref = raw_ref
            self._mission_fov_evaluator = None
            self._mission_fov_evaluator_error = (
                "invalid_evaluator_path: use 'module.submodule:function_name'"
            )
            self.logger.logger.warning(
                "Invalid mission evaluator reference '%s': %s",
                raw_ref,
                self._mission_fov_evaluator_error,
            )
            return None

        try:
            module = importlib.import_module(module_name)
            evaluator = getattr(module, attr_name)
            if not callable(evaluator):
                raise TypeError(f"attribute '{attr_name}' is not callable")
            self._mission_fov_evaluator_ref = raw_ref
            self._mission_fov_evaluator = evaluator
            self._mission_fov_evaluator_error = ""
            return evaluator
        except Exception as exc:
            self._mission_fov_evaluator_ref = raw_ref
            self._mission_fov_evaluator = None
            self._mission_fov_evaluator_error = str(exc)
            self.logger.logger.warning(
                "Failed to resolve mission evaluator '%s': %s",
                raw_ref,
                exc,
            )
            return None

    def _extract_bom_overrides(self, bom_file: str) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
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
            def _assign_float(out_key: str, candidate_keys: List[str]) -> None:
                for candidate in candidate_keys:
                    if candidate not in raw_constraints:
                        continue
                    try:
                        constraint_override[out_key] = float(raw_constraints[candidate])
                        return
                    except Exception:
                        continue

            _assign_float("max_temp_c", ["max_temp_c", "max_temperature"])
            _assign_float("min_clearance_mm", ["min_clearance_mm", "min_clearance"])
            _assign_float("max_cg_offset_mm", ["max_cg_offset_mm", "max_cg_offset"])
            _assign_float("min_safety_factor", ["min_safety_factor"])
            _assign_float("min_modal_freq_hz", ["min_modal_freq_hz", "min_modal_freq"])
            _assign_float("max_voltage_drop_v", ["max_voltage_drop_v", "max_voltage_drop"])
            _assign_float("min_power_margin_pct", ["min_power_margin_pct", "min_power_margin"])
            _assign_float("max_power_w", ["max_power_w", "max_power"])
            _assign_float("bus_voltage_v", ["bus_voltage_v", "bus_voltage"])
            if "enforce_power_budget" in raw_constraints:
                constraint_override["enforce_power_budget"] = self._to_bool(
                    raw_constraints.get("enforce_power_budget"),
                    default=False,
                )

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

    def _extract_bom_envelope_override(self, bom_file: str) -> Optional[Dict[str, Any]]:
        """Extract explicit envelope size from BOM when present."""
        path = Path(bom_file)
        if not path.exists():
            return None

        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                if path.suffix.lower() == ".json":
                    raw = json.load(f)
                else:
                    raw = yaml.safe_load(f)
        except Exception as exc:
            self.logger.logger.warning(
                "Failed to parse BOM envelope override from %s: %s",
                bom_file,
                exc,
            )
            return None

        if not isinstance(raw, dict):
            return None

        envelope = raw.get("envelope", {})
        if not isinstance(envelope, dict):
            return None

        try:
            size_mm = [
                float(envelope["x"]),
                float(envelope["y"]),
                float(envelope["z"]),
            ]
        except Exception:
            return None

        if any(value <= 0.0 for value in size_mm):
            return None

        return {"size_mm": size_mm}

    def _recenter_initial_layout_to_cg(self, design_state: DesignState) -> DesignState:
        """Apply a rigid-body shift to reduce initial CG bias without changing topology."""
        geom_cfg = dict(self.config.get("geometry", {}) or {})
        enabled = self._to_bool(geom_cfg.get("center_initial_layout_to_cg"), default=True)
        if not enabled:
            return design_state

        components = list(getattr(design_state, "components", []) or [])
        if not components:
            return design_state

        masses: List[float] = []
        centers: List[np.ndarray] = []
        half_sizes: List[np.ndarray] = []
        for comp in components:
            dim = getattr(comp, "dimensions", None)
            pos = getattr(comp, "position", None)
            if dim is None or pos is None:
                continue
            masses.append(max(float(getattr(comp, "mass", 0.0)), 0.0))
            centers.append(
                np.asarray(
                    [float(pos.x), float(pos.y), float(pos.z)],
                    dtype=float,
                )
            )
            half_sizes.append(
                0.5
                * np.asarray(
                    [float(dim.x), float(dim.y), float(dim.z)],
                    dtype=float,
                )
            )

        if not centers:
            return design_state

        weights = np.asarray(masses, dtype=float)
        total_mass = float(np.sum(weights))
        if total_mass <= 1e-9:
            return design_state

        center_array = np.vstack(centers)
        half_array = np.vstack(half_sizes)

        envelope = design_state.envelope
        if getattr(envelope, "inner_size", None) is not None:
            env_size = np.asarray(
                [
                    float(envelope.inner_size.x),
                    float(envelope.inner_size.y),
                    float(envelope.inner_size.z),
                ],
                dtype=float,
            )
        else:
            env_size = np.asarray(
                [
                    float(envelope.outer_size.x),
                    float(envelope.outer_size.y),
                    float(envelope.outer_size.z),
                ],
                dtype=float,
            )

        if str(getattr(envelope, "origin", "center")).strip().lower() == "center":
            env_min = -0.5 * env_size
            env_max = 0.5 * env_size
            target_center = np.zeros(3, dtype=float)
        else:
            env_min = np.zeros(3, dtype=float)
            env_max = env_size
            target_center = 0.5 * env_size

        cg_center = np.sum(center_array * weights[:, None], axis=0) / total_mass
        desired_shift = target_center - cg_center
        lower_shift = np.max(env_min[None, :] + half_array - center_array, axis=0)
        upper_shift = np.min(env_max[None, :] - half_array - center_array, axis=0)
        shift = np.minimum(np.maximum(desired_shift, lower_shift), upper_shift)
        if not np.all(np.isfinite(shift)):
            return design_state

        if float(np.linalg.norm(shift)) <= 1e-9:
            return design_state

        recentered_state = design_state.model_copy(deep=True)
        for comp in list(recentered_state.components or []):
            comp.position.x = float(comp.position.x + shift[0])
            comp.position.y = float(comp.position.y + shift[1])
            comp.position.z = float(comp.position.z + shift[2])

        shifted_cg = cg_center + shift
        self.logger.logger.info(
            "Initial layout CG recentered: shift=(%.2f, %.2f, %.2f) mm, cg_before=(%.2f, %.2f, %.2f), cg_after=(%.2f, %.2f, %.2f)",
            float(shift[0]),
            float(shift[1]),
            float(shift[2]),
            float(cg_center[0]),
            float(cg_center[1]),
            float(cg_center[2]),
            float(shifted_cg[0]),
            float(shifted_cg[1]),
            float(shifted_cg[2]),
        )
        return recentered_state

    def _sync_layout_clearance_constraint(
        self,
        geom_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        synced = dict(geom_config or {})
        required_clearance = float(self.runtime_constraints.get("min_clearance_mm", 5.0) or 5.0)
        current_clearance = float(synced.get("clearance_mm", 5.0) or 5.0)
        synced["clearance_mm"] = max(current_clearance, required_clearance)
        return synced

    def _repair_initial_mission_keepout(self, design_state: DesignState) -> DesignState:
        opt_cfg = dict(self.config.get("optimization", {}) or {})
        mission_evaluator_ref = str(opt_cfg.get("mass_mission_fov_evaluator", "") or "").strip()
        require_mission_real = bool(
            opt_cfg.get("mass_source_gate_require_mission_real", False)
            or opt_cfg.get("mass_physics_real_only", False)
        )
        if not mission_evaluator_ref and not require_mission_real:
            return design_state

        axis = str(opt_cfg.get("mission_keepout_axis", "z") or "z").strip().lower()
        if axis not in {"x", "y", "z"}:
            axis = "z"
        axis_idx = self._mission_axis_index(axis)
        keepout_center = float(opt_cfg.get("mission_keepout_center_mm", 0.0) or 0.0)
        min_sep = max(float(opt_cfg.get("mission_min_separation_mm", 0.0) or 0.0), 0.0)

        critical_components = [
            comp for comp in list(getattr(design_state, "components", []) or [])
            if self._is_mission_critical_component(comp)
        ]
        if not critical_components:
            return design_state

        repaired_state = design_state.model_copy(deep=True)
        env_min, env_max = self._envelope_bounds_for_state(repaired_state)
        moved_components: List[str] = []

        for comp in list(repaired_state.components or []):
            if not self._is_mission_critical_component(comp):
                continue

            half = np.asarray(
                [
                    float(comp.dimensions.x) / 2.0,
                    float(comp.dimensions.y) / 2.0,
                    float(comp.dimensions.z) / 2.0,
                ],
                dtype=float,
            )
            pos = np.asarray(
                [float(comp.position.x), float(comp.position.y), float(comp.position.z)],
                dtype=float,
            )
            current_sep = abs(float(pos[axis_idx]) - keepout_center) - float(half[axis_idx])
            if current_sep >= min_sep - 1e-9:
                continue

            lower = float(env_min[axis_idx] + half[axis_idx])
            upper = float(env_max[axis_idx] - half[axis_idx])
            if lower > upper:
                continue

            required_center_offset = float(min_sep + max(float(half[axis_idx]), 0.0))
            positive_target = float(keepout_center + required_center_offset)
            negative_target = float(keepout_center - required_center_offset)
            positive_feasible = bool(positive_target <= upper + 1e-9)
            negative_feasible = bool(negative_target >= lower - 1e-9)

            if positive_feasible and negative_feasible:
                target = positive_target if float(pos[axis_idx]) >= keepout_center else negative_target
            elif positive_feasible:
                target = positive_target
            elif negative_feasible:
                target = negative_target
            else:
                positive_sep = abs(upper - keepout_center) - float(half[axis_idx])
                negative_sep = abs(lower - keepout_center) - float(half[axis_idx])
                target = upper if positive_sep >= negative_sep else lower

            clipped = float(np.clip(target, lower, upper))
            if abs(clipped - float(pos[axis_idx])) <= 1e-9:
                continue
            if axis_idx == 0:
                comp.position.x = clipped
            elif axis_idx == 1:
                comp.position.y = clipped
            else:
                comp.position.z = clipped
            moved_components.append(str(getattr(comp, "id", "") or ""))

        if moved_components:
            self.logger.logger.info(
                "Initial mission keepout repair applied: axis=%s, center=%.2f, min_sep=%.2f, moved=%s",
                axis,
                keepout_center,
                min_sep,
                moved_components,
            )
        return repaired_state

    def _initialize_modules(self):
        """初始化所有模块。"""
        # 1. 几何模块
        geom_config = self._sync_layout_clearance_constraint(
            dict(self.config.get("geometry", {}) or {})
        )
        self.layout_engine = LayoutEngine(config=geom_config)

        # 2. 仿真模块
        sim_config = self.config.get("simulation", {})
        sim_backend = sim_config.get("backend", "simplified")

        if sim_backend == "comsol":
            self.sim_driver = ComsolDriver(config=sim_config)
        else:
            if SimplifiedPhysicsEngine is None:
                raise SatelliteDesignError("simulation.physics_engine 不可用，无法使用 simplified backend")
            self.sim_driver = SimplifiedPhysicsEngine(config=sim_config)

        # 3. LLM模块
        openai_config = self.config.get("openai", {})
        self.llm_profile_resolver = LLMProfileResolver(openai_config)
        self.llm_gateway = LLMGateway(profile_resolver=self.llm_profile_resolver)
        self.active_text_llm_profile = self.llm_profile_resolver.resolve_text_profile(
            str(openai_config.get("default_text_profile", "") or "")
        )
        try:
            self.active_embedding_llm_profile = self.llm_profile_resolver.resolve_embedding_profile(
                str(openai_config.get("default_embedding_profile", "") or "")
            )
        except Exception:
            self.active_embedding_llm_profile = None
        api_key = self.active_text_llm_profile.api_key
        base_url = self.active_text_llm_profile.base_url

        # Meta-Reasoner
        self.meta_reasoner = MetaReasoner(
            api_key=api_key,
            model=self.active_text_llm_profile.model,
            temperature=self.active_text_llm_profile.temperature,
            base_url=base_url,
            logger=self.logger,
            llm_gateway=self.llm_gateway,
            llm_profile=self.active_text_llm_profile.name,
        )

        # Agents
        agent_model = self.active_text_llm_profile.model
        agent_temperature = self.active_text_llm_profile.temperature

        self.geometry_agent = GeometryAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger,
            llm_gateway=self.llm_gateway,
            llm_profile=self.active_text_llm_profile.name,
        )
        self.thermal_agent = ThermalAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger,
            llm_gateway=self.llm_gateway,
            llm_profile=self.active_text_llm_profile.name,
        )
        self.structural_agent = StructuralAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger,
            llm_gateway=self.llm_gateway,
            llm_profile=self.active_text_llm_profile.name,
        )
        self.power_agent = PowerAgent(
            api_key=api_key,
            model=agent_model,
            temperature=agent_temperature,
            base_url=base_url,
            logger=self.logger,
            llm_gateway=self.llm_gateway,
            llm_profile=self.active_text_llm_profile.name,
        )

        # Coordinator
        self.coordinator = AgentCoordinator(
            geometry_agent=self.geometry_agent,
            thermal_agent=self.thermal_agent,
            structural_agent=self.structural_agent,
            power_agent=self.power_agent,
            logger=self.logger
        )

        # Mass RAG System
        knowledge_config = self.config.get("knowledge", {})
        self.rag_system = MassRAGSystem(
            api_key=api_key,
            knowledge_base_path=knowledge_config.get("base_path", "data/knowledge_base"),
            embedding_model=knowledge_config.get("embedding_model"),
            base_url=base_url,
            llm_gateway=self.llm_gateway,
            embedding_profile_name=getattr(self.active_embedding_llm_profile, "name", ""),
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

    def _initialize_llm_controllers(self) -> None:
        """初始化按职责分拆的 LLM 控制器层。"""
        self.intent_modeler = IntentModeler(delegate=self.meta_reasoner)
        self.strategic_planner = StrategicPlanner(delegate=self.meta_reasoner)
        self.policy_programmer = PolicyProgrammer(delegate=self.meta_reasoner)

    def run_optimization(
        self,
        bom_file: Optional[str] = None,
        max_iterations: int = 20,
        convergence_threshold: float = 0.01
    ) -> DesignState:
        """
        运行完整的优化流程。

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
            f"CG<= {self.runtime_constraints['max_cg_offset_mm']:.2f}mm, "
            f"SF>= {self.runtime_constraints['min_safety_factor']:.2f}, "
            f"f1>= {self.runtime_constraints['min_modal_freq_hz']:.2f}Hz, "
            f"Vdrop<= {self.runtime_constraints['max_voltage_drop_v']:.3f}V, "
            f"margin>= {self.runtime_constraints['min_power_margin_pct']:.2f}%"
        )

        # 1. 初始化设计状态
        current_state = self._initialize_design_state(bom_file)

        runtime_context = RuntimeContext(
            bom_file=bom_file,
            max_iterations=int(max_iterations),
            convergence_threshold=float(convergence_threshold),
        )
        if self._mode_runner is not None:
            return self._mode_runner.run(
                host=self,
                current_state=current_state,
                context=runtime_context,
            )

        return self.agent_loop_service.run(
            current_state=current_state,
            max_iterations=int(max_iterations),
            convergence_threshold=float(convergence_threshold),
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
                        if isinstance(components, list):
                            component_ids = [
                                str(item.get("id", "")).strip()
                                for item in components
                                if isinstance(item, dict) and str(item.get("id", "")).strip()
                            ]
                            if component_ids:
                                lines.append(
                                    "BOM组件ID(必须原样复用): "
                                    + ", ".join(component_ids)
                                )
                except Exception as exc:
                    lines.append(f"BOM解析失败: {exc}")

        lines.append(
            "Canonical metric keys: "
            "cg_offset, min_clearance, num_collisions, boundary_violation, "
            "max_temp, safety_factor, first_modal_freq, voltage_drop, "
            "power_margin, peak_power, mission_keepout_violation"
        )
        lines.append(
            "Do not use runtime limit names as metric keys: "
            "max_temp_c, min_clearance_mm, max_cg_offset_mm, min_safety_factor, "
            "min_modal_freq_hz, max_voltage_drop_v, min_power_margin_pct, "
            "max_power_w, task_fov_violation"
        )

        return "\n".join(lines)

    def _initialize_design_state(self, bom_file: Optional[str]) -> DesignState:
        """初始化设计状态。"""
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
                    f"CG<= {self.runtime_constraints['max_cg_offset_mm']:.2f}mm, "
                    f"SF>= {self.runtime_constraints['min_safety_factor']:.2f}, "
                    f"f1>= {self.runtime_constraints['min_modal_freq_hz']:.2f}Hz, "
                    f"Vdrop<= {self.runtime_constraints['max_voltage_drop_v']:.3f}V, "
                    f"margin>= {self.runtime_constraints['min_power_margin_pct']:.2f}%"
                )

            bom_components = BOMParser.parse(bom_file)

            # 验证BOM
            errors = BOMParser.validate(bom_components)
            if errors:
                raise ValueError(f"BOM验证失败: {errors}")

            self.logger.logger.info(f"BOM loaded: {len(bom_components)} components")

            # 更新 layout_engine 的配置
            # 将BOM组件转换为layout_engine需要的格式
            geom_config = self._sync_layout_clearance_constraint(
                dict(self.config.get('geometry', {}) or {})
            )
            envelope_cfg = dict(geom_config.get('envelope', {}) or {})
            prefer_bom_envelope = self._to_bool(
                envelope_cfg.get("prefer_bom_envelope"),
                default=True,
            )
            if prefer_bom_envelope:
                bom_envelope_override = self._extract_bom_envelope_override(bom_file)
                if bom_envelope_override is not None:
                    envelope_cfg["auto_envelope"] = False
                    envelope_cfg["size_mm"] = list(bom_envelope_override["size_mm"])
                    self.logger.logger.info(
                        "BOM envelope override applied: %.2f x %.2f x %.2f mm",
                        float(envelope_cfg["size_mm"][0]),
                        float(envelope_cfg["size_mm"][1]),
                        float(envelope_cfg["size_mm"][2]),
                    )
            geom_config['envelope'] = envelope_cfg
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

        # 设置随机种子以确保布局可重现
        import random
        import numpy as np
        random.seed(42)
        np.random.seed(42)

        # 使用默认布局
        packing_result = self.layout_engine.generate_layout()

        design_state = packing_result_to_design_state(
            packing_result=packing_result,
            envelope_geom=self.layout_engine.envelope,
            clearance_mm=float(self.runtime_constraints.get("min_clearance_mm", 5.0) or 5.0),
            component_props_by_id=component_props_by_id,
            iteration=0,
            state_id="state_iter_00_init",
            parent_id=None,
        )

        design_state = self._recenter_initial_layout_to_cg(design_state)
        return self._repair_initial_mission_keepout(design_state)

    def _evaluate_design(
        self,
        design_state: DesignState,
        iteration: int
    ) -> tuple[Dict[str, Any], list[ViolationItem]]:
        """评估设计状态。"""
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
            self.logger.logger.info(f"  导出 STEP 文件用于动态仿真: {step_file}")

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
        sim_metric_sources = dict(sim_raw_data.get("metric_sources", {}) or {})
        comsol_feature_domain_audit = dict(
            sim_raw_data.get("comsol_feature_domain_audit", {}) or {}
        )
        mph_model_path = str(sim_raw_data.get("mph_model_path", "") or "")
        sim_metrics = dict(sim_result.metrics or {})

        thermal_metrics = ThermalMetrics(
            max_temp=sim_result.metrics.get("max_temp", 0),
            min_temp=sim_result.metrics.get("min_temp", 0),
            avg_temp=sim_result.metrics.get("avg_temp", 0),
            temp_gradient=sim_result.metrics.get("temp_gradient", 0)
        )

        # 3. 结构/电源评估（优先真实链路，失败回退 proxy）。
        boundary_violation = self._calculate_boundary_violation(design_state)
        structural_proxy = estimate_structural_proxy_metrics(
            design_state,
            cg_offset_mm=float(geometry_metrics.cg_offset_magnitude),
            min_clearance_mm=float(geometry_metrics.min_clearance),
            num_collisions=int(geometry_metrics.num_collisions),
            boundary_violation_mm=float(boundary_violation),
        )

        power_proxy = estimate_power_proxy_metrics(
            design_state,
            max_power_w=float(self.runtime_constraints.get("max_power_w", 500.0)),
            bus_voltage_v=float(self.runtime_constraints.get("bus_voltage_v", 28.0)),
        )

        def _sim_float(key: str) -> Optional[float]:
            try:
                value = float(sim_metrics.get(key))
                if np.isfinite(value):
                    return float(value)
            except Exception:
                return None
            return None

        sim_structural_values = {
            "max_stress": _sim_float("max_stress"),
            "max_displacement": _sim_float("max_displacement"),
            "first_modal_freq": _sim_float("first_modal_freq"),
            "safety_factor": _sim_float("safety_factor"),
        }
        sim_structural_source = str(sim_metric_sources.get("structural_source", "") or "simulation_result")
        merged_structural_values, structural_metric_sources, structural_source = merge_metric_sources(
            simulation_values=sim_structural_values,
            proxy_values=structural_proxy,
            metric_keys=("max_stress", "max_displacement", "first_modal_freq", "safety_factor"),
            simulation_source_label=sim_structural_source,
        )
        structural_metrics = StructuralMetrics(
            max_stress=float(merged_structural_values["max_stress"]),
            max_displacement=float(merged_structural_values["max_displacement"]),
            first_modal_freq=float(merged_structural_values["first_modal_freq"]),
            safety_factor=float(merged_structural_values["safety_factor"]),
        )

        sim_power_values = {
            "total_power": _sim_float("total_power"),
            "peak_power": _sim_float("peak_power"),
            "power_margin": _sim_float("power_margin"),
            "voltage_drop": _sim_float("voltage_drop"),
        }
        sim_power_source = str(sim_metric_sources.get("power_source", "") or "simulation_result")
        merged_power_values, power_metric_sources, power_source = merge_metric_sources(
            simulation_values=sim_power_values,
            proxy_values=power_proxy,
            metric_keys=("total_power", "peak_power", "power_margin", "voltage_drop"),
            simulation_source_label=sim_power_source,
        )
        power_metrics = PowerMetrics(
            total_power=float(merged_power_values["total_power"]),
            peak_power=float(merged_power_values["peak_power"]),
            power_margin=float(merged_power_values["power_margin"]),
            voltage_drop=float(merged_power_values["voltage_drop"]),
        )
        opt_cfg = dict(self.config.get("optimization", {}) or {})
        mission_axis = str(opt_cfg.get("mission_keepout_axis", "z")).strip().lower() or "z"
        mission_center = float(opt_cfg.get("mission_keepout_center_mm", 0.0) or 0.0)
        mission_min_sep = float(opt_cfg.get("mission_min_separation_mm", 0.0) or 0.0)
        mission_require_real = bool(opt_cfg.get("mass_source_gate_require_mission_real", False))
        mission_require_real = bool(mission_require_real or opt_cfg.get("mass_physics_real_only", False))
        mission_evaluator = self._resolve_mission_fov_evaluator()
        mission_payload = evaluate_mission_fov_interface(
            design_state,
            evaluator=mission_evaluator if callable(mission_evaluator) else None,
            axis=mission_axis,
            keepout_center_mm=mission_center,
            min_separation_mm=mission_min_sep,
            require_real=mission_require_real,
        )
        mission_source = str(mission_payload.get("mission_source", ""))

        # 5. 检查约束违反
        violations = self._check_violations(
            geometry_metrics,
            thermal_metrics,
            structural_metrics,
            power_metrics,
            mission_metrics=dict(mission_payload or {}),
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
                "metric_sources": sim_metric_sources,
                "comsol_feature_domain_audit": comsol_feature_domain_audit,
                "structural_source": str(structural_source),
                "power_source": str(power_source),
                "sim_structural_metrics": {
                    key: value for key, value in sim_structural_values.items() if value is not None
                },
                "sim_power_metrics": {
                    key: value for key, value in sim_power_values.items() if value is not None
                },
                "structural_metric_sources": structural_metric_sources,
                "power_metric_sources": power_metric_sources,
                "proxy_structural": structural_proxy,
                "proxy_power": power_proxy,
                "mission_source": mission_source,
                "mission_interface_status": str(
                    mission_payload.get("interface_status", "")
                ),
                "mission_evaluator_ref": str(self._mission_fov_evaluator_ref or ""),
                "mission_evaluator_error": str(self._mission_fov_evaluator_error or ""),
                "mission_metrics": {
                    key: value
                    for key, value in dict(mission_payload or {}).items()
                    if key
                },
            }
        }

        return metrics, violations

    def _export_design_to_step(self, design_state: DesignState, iteration: int) -> Path:
        """
        导出设计状态为 STEP 文件（用于动态 COMSOL 仿真）。
        使用 OpenCASCADE 生成真实 BREP 实体。

        Args:
            design_state: 设计状态
            iteration: 当前迭代次数

        Returns:
            STEP文件路径
        """
        from geometry.cad_export_occ import export_design_occ
        from pathlib import Path

        # 创建临时目录
        temp_dir = Path(self.logger.get_step_files_dir())
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
            packing_efficiency=calculate_packing_efficiency(design_state),
            num_collisions=num_collisions
        )

    def _calculate_pairwise_clearance(self, design_state: DesignState) -> tuple[float, int]:
        """
        计算组件两两间最小净间隙与碰撞对数（基于中心点坐标 + 轴对齐包围盒）。

        Returns:
            (min_clearance_mm, num_collisions)
        """
        return calculate_geometry_pairwise_clearance(design_state)

    def _calculate_boundary_violation(self, design_state: DesignState) -> float:
        """计算设计在包络约束下的最大越界量（mm）。"""

        return calculate_geometry_boundary_violation(design_state)

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
        """Generate state fingerprint for strict no-op detection."""
        return self._state_fingerprint_with_options(design_state, position_quantization_mm=0.0)

    def _state_fingerprint_with_options(
        self,
        design_state: DesignState,
        position_quantization_mm: float = 0.0,
    ) -> tuple:
        """
        Generate state fingerprint with optional position quantization.

        Args:
            design_state: current design state.
            position_quantization_mm: when >0, quantize positions by this step.
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

if __name__ == "__main__":
    print("Workflow Orchestrator module created")





