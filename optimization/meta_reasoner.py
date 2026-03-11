"""
Meta-Reasoner: 战略层元推理器

负责顶层决策：
1. 多学科协调 - 平衡几何、热控、结构、电源等约束
2. 探索策略制定 - 决定优化方向（局部搜索 vs 全局重构）
3. 冲突解决 - 提供多约束权衡方案
"""

import os
import re
# 强制清空可能导致 10061 错误的本地代理环境变量
for proxy_env in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
    if proxy_env in os.environ:
        del os.environ[proxy_env]

import dashscope
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import json
import math
import yaml
from pathlib import Path

from .protocol import (
    GlobalContextPack,
    StrategicPlan,
    AgentTask,
    ViolationItem,
    ModelingIntent,
)
from core.logger import ExperimentLogger
from core.exceptions import LLMError
from optimization.llm.gateway import LLMGateway, build_legacy_gateway
from optimization.modes.mass.operator_program import SUPPORTED_ACTIONS
from optimization.modes.mass.operator_program_v4 import (
    SUPPORTED_ACTIONS_V4,
    normalize_operator_program_v4_payload,
    validate_operator_program_v4,
)
from optimization.modes.mass.metric_registry import get_metric_status, normalize_metric_key
from optimization.modes.mass.maas_compiler import _build_component_aliases, _resolve_component_id


VOP_ACTION_ALIAS_MAP: Dict[str, str] = {
    "keepout_clear": "fov_keepout_push",
    "clear_keepout": "fov_keepout_push",
    "fov_clear": "fov_keepout_push",
    "mission_keepout_push": "fov_keepout_push",
    "move_group": "group_move",
    "move_cluster": "group_move",
    "recenter_cg": "cg_recenter",
    "spread_heat": "hot_spread",
    "thermal_spread": "hot_spread",
    "thermal_contact": "set_thermal_contact",
    "brace_add": "add_bracket",
    "add_stiffener": "stiffener_insert",
    "bus_proximity": "bus_proximity_opt",
}

VOP_DIRECTION_ALIAS_MAP: Dict[str, str] = {
    "+": "positive",
    "plus": "positive",
    "positive": "positive",
    "-": "negative",
    "minus": "negative",
    "negative": "negative",
    "auto": "auto",
    "either": "auto",
    "both": "auto",
}

STRATEGIC_PLAN_MIN_MAX_TOKENS = 4096
MODELING_INTENT_MIN_MAX_TOKENS = 8192
POLICY_PROGRAM_MIN_MAX_TOKENS = 8192


class MetaReasoner:
    """元推理器 - 战略决策层"""

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3-max",
        temperature: float = 0.7,
        base_url: Optional[str] = None,
        logger: Optional[ExperimentLogger] = None,
        api_mode: str = "dashscope_generation",
        responses_url: Optional[str] = None,
        timeout_s: float = 120.0,
        llm_gateway: Optional[LLMGateway] = None,
        llm_profile: str = "",
    ):
        """
        初始化Meta-Reasoner

        Args:
            api_key: API密钥（DashScope API Key）
            model: 使用的模型（qwen-plus, qwen-max等）
            temperature: 温度参数（0.0-1.0），控制创造性
            base_url: 保留兼容性参数（不使用）
            logger: 实验日志记录器
        """
        # 设置 DashScope API Key
        dashscope.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.logger = logger
        self.llm_profile = str(llm_profile or "").strip()
        self.llm_client = llm_gateway or build_legacy_gateway(
            api_key=api_key,
            model=model,
            temperature=temperature,
            base_url=base_url,
            api_mode=api_mode,
            timeout_s=timeout_s,
        )

        # 加载系统提示词
        self.system_prompt = self._load_system_prompt()
        self.modeling_system_prompt = self._load_modeling_system_prompt()
        self.vop_policy_system_prompt = self._load_vop_policy_system_prompt()

        # Few-shot示例
        self.few_shot_examples = self._load_few_shot_examples()
        self._modeling_intent_autofill_used = False
        self._reset_modeling_intent_diagnostics()

    def _current_llm_log_metadata(self) -> Dict[str, Any]:
        try:
            profile = self.llm_client.resolve_text_profile(self.llm_profile)
            return {
                "profile": profile.name,
                "provider": profile.provider,
                "model": profile.model,
                "api_style": profile.api_style,
                "fallback_used": False,
                "fallback_reason": "",
                "key_source": profile.api_key_source,
                "key_source_masked": profile.key_source_masked,
            }
        except Exception:
            return {
                "profile": str(self.llm_profile or ""),
                "provider": "",
                "model": str(self.model or ""),
                "api_style": "",
                "fallback_used": False,
                "fallback_reason": "",
                "key_source": "",
                "key_source_masked": "",
            }

    def _resolve_preferred_max_tokens(self, *, minimum_tokens: int) -> int:
        preferred = max(int(minimum_tokens or 0), 0)
        try:
            profile = self.llm_client.resolve_text_profile(self.llm_profile)
            preferred = max(preferred, int(getattr(profile, "max_tokens", 0) or 0))
        except Exception:
            pass
        return preferred

    @staticmethod
    def _attach_llm_log_metadata(payload: Any, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(payload, dict):
            result = dict(payload)
        else:
            result = {"payload": payload}
        result["_llm"] = dict(metadata or {})
        return result

    def _reset_modeling_intent_diagnostics(self) -> None:
        """重置最近一次 ModelingIntent 调用诊断。"""
        self._modeling_intent_diagnostics: Dict[str, Any] = {
            "called": False,
            "api_call_attempted": False,
            "api_call_succeeded": False,
            "response_status_code": None,
            "used_fallback": False,
            "fallback_reason": "",
            "error": "",
            "source": "not_called",
            "autofill_used": False,
            "model": str(self.model),
            "timestamp": "",
        }

    def get_modeling_intent_diagnostics(self) -> Dict[str, Any]:
        """获取最近一次 ModelingIntent 调用诊断快照。"""
        return dict(self._modeling_intent_diagnostics or {})

    def _load_system_prompt(self) -> str:
        """加载系统提示词"""
        return """你是卫星设计优化系统的首席架构师（Meta-Reasoner）。

【角色定位】
- 你不直接修改设计参数，而是制定优化策略并协调专业Agent
- 你需要平衡多个学科的约束，做出权衡决策
- 你的决策必须有明确的工程依据

【核心能力】
1. 多学科协调：理解几何、热控、结构、电源之间的耦合关系
2. 战略规划：选择合适的优化策略（局部搜索/全局重构/混合）
3. 任务分解：将复杂问题分解为可执行的Agent任务
4. 风险评估：预测优化方案的潜在风险

【输入信息】
1. 当前设计状态（几何布局、仿真结果）
2. 约束违反情况（几何干涉、热超标、结构应力等）
3. 历史优化轨迹（避免重复失败的尝试）
4. 检索到的工程知识（相关规范、案例）

【输出要求】
你必须输出一个JSON格式的StrategicPlan，包含以下字段：

```json
{
  "plan_id": "PLAN_YYYYMMDD_NNN",
  "reasoning": "详细的Chain-of-Thought推理过程：\\n1. 问题诊断：当前问题的根本原因是什么？\\n2. 策略选择：为什么选择这个策略而不是其他策略？\\n3. 预期效果：预期会产生什么连锁反应？",
  "strategy_type": "local_search | global_reconfig | hybrid",
  "strategy_description": "策略的简要描述",
  "tasks": [
    {
      "task_id": "TASK_001",
      "agent_type": "geometry | thermal | structural | power",
      "objective": "任务目标的自然语言描述",
      "constraints": ["约束条件1", "约束条件2"],
      "priority": 1-5,
      "context": {"额外上下文信息"}
    }
  ],
  "expected_improvements": {
    "max_temp": -5.0,
    "min_clearance": 2.0
  },
  "risks": ["风险1", "风险2"]
}
```

【策略类型说明】
1. **local_search**: 局部微调
   - 适用场景：接近可行解，只有少量约束违反
   - 特点：小步迭代，风险低，收敛快
   - 示例：微调组件位置以消除干涉

2. **global_reconfig**: 全局重构
   - 适用场景：严重违反约束，局部调整无效
   - 特点：大幅改动，风险高，可能突破局部最优
   - 示例：重新规划整体布局

3. **hybrid**: 混合策略
   - 适用场景：部分区域需要重构，部分区域可微调
   - 特点：平衡风险与收益
   - 示例：重构热点区域，保持其他区域不变

【约束规则】
1. 不得违反物理定律（如质心必须在支撑范围内）
2. 优先保证安全裕度（不仅满足约束，还要留有余量）
3. 考虑制造可行性（避免过于复杂的结构）
4. 尊重历史经验（避免重复已失败的尝试）

【决策原则】
1. **安全第一**: 结构安全 > 热控 > 性能优化
2. **渐进式**: 优先尝试风险低的方案
3. **可追溯**: 每个决策都要有明确的工程依据
4. **可回滚**: 考虑失败后的回退方案
"""

    def _load_modeling_system_prompt(self) -> str:
        """加载 MaaS 建模系统提示词（Phase A: Understanding）。"""
        return """Role: You are the MsGalaxy Meta-Reasoner, a Senior Aerospace Software Engineer and Optimization Specialist.

Task:
- You are the Modeling-as-a-Service layer.
- You DO NOT perform numerical optimization directly.
- You MUST transform requirements into a rigorous modeling intent JSON.

Operational rules:
1. Output MUST be valid JSON object only.
2. You MUST include:
   - problem_type
   - variables
   - objectives
   - hard_constraints
   - soft_constraints
3. Hard constraints represent physical laws or mandatory engineering limits.
4. Soft constraints represent preferences.
5. Do not output code at this stage.
6. Use canonical executable metric keys only, such as:
   - cg_offset
   - min_clearance
   - num_collisions
   - boundary_violation
   - max_temp
   - safety_factor
   - first_modal_freq
   - voltage_drop
   - power_margin
   - peak_power
   - mission_keepout_violation
7. Do NOT use runtime limit names as metric keys, such as:
   - max_temp_c
   - min_clearance_mm
   - max_cg_offset_mm
   - min_safety_factor
   - min_modal_freq_hz
   - max_voltage_drop_v
   - min_power_margin_pct
   - max_power_w
   - task_fov_violation
8. If requirement text provides BOM component IDs, you MUST reuse those exact IDs in variables.

JSON schema guideline:
{
  "intent_id": "INTENT_YYYYMMDD_NNN",
  "iteration": 1,
  "problem_type": "continuous | discrete | mixed | multi_objective",
  "variables": [
    {
      "name": "Battery_01_x",
      "variable_type": "continuous",
      "lower_bound": -120.0,
      "upper_bound": 120.0,
      "unit": "mm",
      "component_id": "Battery_01",
      "description": "x-position"
    }
  ],
  "objectives": [
    {
      "name": "min_cg_offset",
      "metric_key": "cg_offset",
      "direction": "minimize",
      "weight": 1.0,
      "description": "keep centroid close to reference"
    }
  ],
  "hard_constraints": [
    {
      "name": "clearance_limit",
      "metric_key": "min_clearance",
      "category": "geometry",
      "relation": ">=",
      "target_value": 5.0,
      "unit": "mm",
      "expression": "min_clearance >= 5.0",
      "latex": "g_{clear}=5.0-min\\_clearance\\le 0",
      "physical_meaning": "minimum mechanical clearance"
    }
  ],
  "soft_constraints": [],
  "assumptions": [],
  "notes": ""
}
"""

    def _load_vop_policy_system_prompt(self) -> str:
        """Load the VOP-MaaS policy-program system prompt."""
        allowed_actions = ", ".join(sorted(SUPPORTED_ACTIONS))
        semantic_actions = ", ".join(sorted(SUPPORTED_ACTIONS_V4))
        return (
            "Role: You are the Verified Operator-Policy programmer for MsGalaxy VOP-MaaS.\n\n"
            "Task:\n"
            "- Read the structured multiphysics evidence and produce a bounded policy pack.\n"
            "- You DO NOT output final component coordinates.\n"
            "- You DO NOT replace pymoo or MaaS numeric optimization.\n"
            "- You MAY bias the search using operator programs, search-space prior, runtime knob priors, and fidelity hints.\n\n"
            "Hard rules:\n"
            "1. Output MUST be one valid JSON object only.\n"
            "2. Search-space prior MUST be one of: coordinate, operator_program, hybrid.\n"
            "3. operator_candidates MUST be executable OP-MaaS DSL programs only.\n"
            "4. Runtime knobs MUST stay bounded and advisory.\n"
            "5. If evidence is weak, reduce confidence instead of inventing aggressive actions.\n"
            f"6. Prefer semantic DSL v4 action names from this allowlist: {semantic_actions}.\n"
            f"7. Legacy DSL v3 actions are still accepted for compatibility: {allowed_actions}.\n"
            "8. For semantic DSL v4, each action should include explicit `targets`, `hard_rules`, and `soft_preferences` when applicable.\n"
            "9. If the dominant violation is mission/FOV/keepout, prefer `protect_fov_keepout` in v4 or `fov_keepout_push` in legacy v3; do not invent unrelated aliases.\n"
            "10. Do NOT weaken runtime fidelity already requested by the system/profile; if `VOP Graph.metadata.fidelity_floor_hint` requires online COMSOL, keep or strengthen it.\n"
            "11. If `dominant_violation_family` is empty, prefer `VOP Graph.metadata.level_focus_hint` and bounded fidelity hints before falling back to mission/geometry.\n\n"
            "Required JSON shape:\n"
            "{\n"
            '  "policy_id": "VOP_POLICY_YYYYMMDD_NNN",\n'
            '  "constraint_focus": ["thermal", "power"],\n'
            '  "search_space_prior": "hybrid",\n'
            '  "operator_candidates": [\n'
            "    {\n"
            '      "candidate_id": "cand_01",\n'
            '      "priority": 1.0,\n'
            '      "note": "why this branch is useful",\n'
            '      "program_v4": {\n'
            '        "program_id": "op_prog_01",\n'
            '        "version": "opmaas-r4",\n'
            '        "rationale": "structured explanation",\n'
            '        "actions": [\n'
            "          {\n"
            '            "action": "move_heat_source_to_radiator_zone",\n'
            '            "targets": [\n'
            '              {\n'
            '                "object_type": "component_group",\n'
            '                "object_id": "hot_cluster",\n'
            '                "role": "subject",\n'
            '                "attributes": {"component_ids": ["battery_main", "payload_camera"]}\n'
            '              },\n'
            '              {\n'
            '                "object_type": "zone",\n'
            '                "object_id": "radiator_zone_nadir",\n'
            '                "role": "target"\n'
            '              }\n'
            '            ],\n'
            '            "hard_rules": ["thermal_boundary"],\n'
            '            "soft_preferences": ["heat_source_to_radiator"],\n'
            '            "params": {"axis": "y", "delta_mm": 8.0, "focus_ratio": 0.6}\n'
            "          }\n"
            "        ]\n"
            "      }\n"
            "    }\n"
            "  ],\n"
            '  "runtime_knob_priors": {\n'
            '    "maas_relax_ratio": 0.08,\n'
            '    "mcts_action_prior_weight": 0.10,\n'
            '    "online_comsol_eval_budget": 6\n'
            "  },\n"
            '  "fidelity_plan": {\n'
            '    "physics_audit_top_k": 2\n'
            "  },\n"
            '  "confidence": 0.55,\n'
            '  "rationale": "concise explanation grounded in evidence",\n'
            '  "expected_effects": {\n'
            '    "max_temp": -2.0,\n'
            '    "power_margin": 2.0\n'
            "  },\n"
            '  "policy_source": "llm_api"\n'
            "}\n"
        )

    def _load_few_shot_examples(self) -> List[Dict[str, str]]:
        """加载 strategic planning 的 few-shot 示例。"""
        return []

    def _normalize_vop_action_payload(
        self,
        action_payload: Any,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if not isinstance(action_payload, dict):
            return None, True

        autofill_used = False
        action_name = str(action_payload.get("action", "") or "").strip().lower()
        if not action_name:
            return None, True

        canonical_action = VOP_ACTION_ALIAS_MAP.get(action_name, action_name)
        if canonical_action != action_name:
            autofill_used = True
        if canonical_action not in SUPPORTED_ACTIONS:
            return None, True

        raw_params = action_payload.get("params", {})
        if not isinstance(raw_params, dict):
            raw_params = {}
            autofill_used = True

        normalized_params = dict(raw_params)
        if canonical_action == "fov_keepout_push":
            normalized_params, params_repaired = self._normalize_fov_keepout_push_params(
                normalized_params
            )
            autofill_used = autofill_used or params_repaired

        normalized_action: Dict[str, Any] = {
            "action": canonical_action,
            "params": normalized_params,
        }
        note = str(action_payload.get("note", "") or "").strip()
        if note:
            normalized_action["note"] = note
        return normalized_action, autofill_used

    def _looks_like_v4_program_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        version = str(
            payload.get("version")
            or payload.get("dsl_version")
            or payload.get("semantic_version")
            or ""
        ).strip().lower()
        if version.endswith("r4") or version.endswith("v4"):
            return True
        for action in list(payload.get("actions", []) or []):
            if not isinstance(action, dict):
                continue
            if "targets" in action or "hard_rules" in action or "soft_preferences" in action:
                return True
            if any(
                key in action
                for key in (
                    "panel_id",
                    "aperture_id",
                    "zone_id",
                    "mount_site_id",
                    "component_group_id",
                )
            ):
                return True
        return False

    def _normalize_vop_program_v4_payload(
        self,
        program_payload: Any,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if not isinstance(program_payload, dict):
            return None, True

        normalized = normalize_operator_program_v4_payload(program_payload)
        validation = validate_operator_program_v4(normalized)
        autofill_used = normalized != program_payload
        if not validation.get("is_valid", False):
            return None, True
        normalized_payload = validation.get("normalized_payload")
        if not isinstance(normalized_payload, dict):
            return None, True
        return dict(normalized_payload), bool(autofill_used or validation.get("warnings"))

    def _normalize_fov_keepout_push_params(
        self,
        params: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        normalized = dict(params or {})
        autofill_used = False

        def _alias_into(target_key: str, alias_keys: List[str]) -> None:
            nonlocal autofill_used
            if target_key in normalized:
                return
            for alias_key in alias_keys:
                if alias_key not in normalized:
                    continue
                normalized[target_key] = normalized.pop(alias_key)
                autofill_used = True
                return

        _alias_into("component_ids", ["components", "targets", "target_component_ids"])
        _alias_into("axis", ["keepout_axis", "push_axis"])
        _alias_into("keepout_center_mm", ["keepout_center", "center_mm", "center"])
        _alias_into(
            "min_separation_mm",
            ["min_offset_mm", "clearance_mm", "separation_mm", "min_distance_mm"],
        )

        if "preferred_side" not in normalized and "direction_hint" in normalized:
            raw_direction = str(normalized.pop("direction_hint") or "").strip().lower()
            mapped_direction = VOP_DIRECTION_ALIAS_MAP.get(raw_direction, "")
            if mapped_direction:
                normalized["preferred_side"] = mapped_direction
            autofill_used = True

        preferred_side = str(normalized.get("preferred_side", "") or "").strip().lower()
        if preferred_side in VOP_DIRECTION_ALIAS_MAP:
            normalized["preferred_side"] = VOP_DIRECTION_ALIAS_MAP[preferred_side]
            autofill_used = True

        return normalized, autofill_used

    def _extract_component_catalog_from_requirement_text(
        self,
        requirement_text: str,
    ) -> List[Dict[str, str]]:
        raw = str(requirement_text or "").strip()
        if not raw:
            return []

        catalog: List[Dict[str, str]] = []
        seen_ids: set[str] = set()

        path_match = re.search(r"BOM路径:\s*(.+)", raw)
        if path_match:
            bom_path = Path(path_match.group(1).strip())
            try:
                if bom_path.exists():
                    with bom_path.open("r", encoding="utf-8") as handle:
                        if bom_path.suffix.lower() == ".json":
                            payload = json.load(handle)
                        else:
                            payload = yaml.safe_load(handle)
                    if isinstance(payload, dict):
                        components = payload.get("components", [])
                        if isinstance(components, list):
                            for item in components:
                                if not isinstance(item, dict):
                                    continue
                                component_id = str(item.get("id", "") or "").strip()
                                if not component_id or component_id in seen_ids:
                                    continue
                                seen_ids.add(component_id)
                                catalog.append(
                                    {
                                        "id": component_id,
                                        "category": str(item.get("category", "") or "").strip(),
                                    }
                                )
            except Exception:
                pass

        if not catalog:
            ids_match = re.search(r"BOM组件ID(?:\(必须原样复用\))?:\s*(.+)", raw)
            if ids_match:
                for token in ids_match.group(1).split(","):
                    component_id = str(token or "").strip()
                    if not component_id or component_id in seen_ids:
                        continue
                    seen_ids.add(component_id)
                    catalog.append({"id": component_id, "category": ""})

        return catalog

    def _normalize_modeling_metric_key(self, metric_key: Any) -> Tuple[str, bool]:
        raw_metric_key = str(metric_key or "").strip()
        normalized_metric_key = normalize_metric_key(raw_metric_key)
        return normalized_metric_key, normalized_metric_key != raw_metric_key

    def _normalize_modeling_unit(self, unit: Any) -> Tuple[str, bool]:
        raw_unit = str(unit or "").strip()
        normalized = raw_unit
        if raw_unit in {"°C", "℃", "degC", "deg_c", "celsius"}:
            normalized = "C"
        elif raw_unit.lower() in {"percent", "pct"}:
            normalized = "%"
        elif raw_unit.lower() in {"dimensionless", "unitless", "none", "n/a"}:
            normalized = "dimensionless" if raw_unit else raw_unit
        return normalized, normalized != raw_unit

    def _repair_modeling_variables(
        self,
        variables: List[Dict[str, Any]],
        *,
        requirement_text: str,
    ) -> bool:
        component_catalog = self._extract_component_catalog_from_requirement_text(requirement_text)
        if not component_catalog or not variables:
            return False

        component_ids = [str(item.get("id", "") or "").strip() for item in component_catalog if str(item.get("id", "") or "").strip()]
        if not component_ids:
            return False

        alias_components = [
            type(
                "ComponentAliasSeed",
                (),
                {
                    "id": str(item.get("id", "") or "").strip(),
                    "category": str(item.get("category", "") or "").strip(),
                },
            )()
            for item in component_catalog
            if str(item.get("id", "") or "").strip()
        ]
        component_aliases = _build_component_aliases(alias_components)
        component_id_set = set(component_ids)

        repaired = False
        resolved_by_raw: Dict[str, str] = {}
        unresolved_in_order: List[str] = []
        used_targets: set[str] = set()

        for var in variables:
            raw_component_id = str(var.get("component_id", "") or "").strip()
            if not raw_component_id:
                continue
            if raw_component_id in resolved_by_raw:
                used_targets.add(resolved_by_raw[raw_component_id])
                continue
            resolved = _resolve_component_id(
                raw_component_id=raw_component_id,
                variable_name=str(var.get("name", "") or ""),
                component_ids=component_id_set,
                component_aliases=component_aliases,
            )
            if resolved:
                resolved_by_raw[raw_component_id] = resolved
                used_targets.add(resolved)
            elif raw_component_id not in unresolved_in_order:
                unresolved_in_order.append(raw_component_id)

        remaining_targets = [item for item in component_ids if item not in used_targets]
        if unresolved_in_order and len(unresolved_in_order) == len(remaining_targets):
            for raw_component_id, resolved in zip(unresolved_in_order, remaining_targets):
                resolved_by_raw[raw_component_id] = resolved

        for var in variables:
            raw_component_id = str(var.get("component_id", "") or "").strip()
            if not raw_component_id:
                continue
            repaired_component_id = str(resolved_by_raw.get(raw_component_id, "") or "").strip()
            if repaired_component_id and repaired_component_id != raw_component_id:
                var["component_id"] = repaired_component_id
                repaired = True

        return repaired

    def _build_runtime_hard_constraint_payloads(
        self,
        runtime_constraints: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        max_temp = float(runtime_constraints.get("max_temp_c", 60.0))
        min_clearance = float(runtime_constraints.get("min_clearance_mm", 3.0))
        max_cg = float(runtime_constraints.get("max_cg_offset_mm", 20.0))
        min_safety_factor = float(runtime_constraints.get("min_safety_factor", 2.0))
        min_modal_freq_hz = float(runtime_constraints.get("min_modal_freq_hz", 55.0))
        max_voltage_drop_v = float(runtime_constraints.get("max_voltage_drop_v", 0.5))
        min_power_margin_pct = float(runtime_constraints.get("min_power_margin_pct", 10.0))
        max_power_w = float(runtime_constraints.get("max_power_w", 500.0))

        return [
            {
                "name": "g_collision",
                "metric_key": "collision_violation",
                "category": "geometry",
                "relation": "<=",
                "target_value": 0.0,
                "unit": "count",
                "expression": "collision_violation <= 0",
                "latex": "g_{collision}=collision\\_violation\\le 0",
                "physical_meaning": "no component collisions",
            },
            {
                "name": "g_clearance",
                "metric_key": "min_clearance",
                "category": "geometry",
                "relation": ">=",
                "target_value": min_clearance,
                "unit": "mm",
                "expression": f"min_clearance >= {min_clearance}",
                "latex": f"g_{{clear}}={min_clearance}-min\\_clearance\\le 0",
                "physical_meaning": "minimum mechanical clearance",
            },
            {
                "name": "g_boundary",
                "metric_key": "boundary_violation",
                "category": "geometry",
                "relation": "<=",
                "target_value": 0.0,
                "unit": "mm",
                "expression": "boundary_violation <= 0",
                "latex": "g_{boundary}=boundary\\_violation\\le 0",
                "physical_meaning": "all components remain within envelope",
            },
            {
                "name": "g_thermal",
                "metric_key": "max_temp",
                "category": "thermal",
                "relation": "<=",
                "target_value": max_temp,
                "unit": "C",
                "expression": f"max_temp <= {max_temp}",
                "latex": f"g_{{thermal}}=max\\_temp-{max_temp}\\le 0",
                "physical_meaning": "thermal upper bound",
            },
            {
                "name": "g_cg",
                "metric_key": "cg_offset",
                "category": "geometry",
                "relation": "<=",
                "target_value": max_cg,
                "unit": "mm",
                "expression": f"cg_offset <= {max_cg}",
                "latex": f"g_{{cg}}=cg\\_offset-{max_cg}\\le 0",
                "physical_meaning": "center-of-gravity offset limit",
            },
            {
                "name": "g_struct_sf",
                "metric_key": "safety_factor",
                "category": "structural",
                "relation": ">=",
                "target_value": min_safety_factor,
                "unit": "dimensionless",
                "expression": f"safety_factor >= {min_safety_factor}",
                "latex": f"g_{{sf}}={min_safety_factor}-safety\\_factor\\le 0",
                "physical_meaning": "structural safety factor lower bound",
            },
            {
                "name": "g_struct_modal",
                "metric_key": "first_modal_freq",
                "category": "structural",
                "relation": ">=",
                "target_value": min_modal_freq_hz,
                "unit": "Hz",
                "expression": f"first_modal_freq >= {min_modal_freq_hz}",
                "latex": f"g_{{modal}}={min_modal_freq_hz}-f_1\\le 0",
                "physical_meaning": "first modal frequency lower bound",
            },
            {
                "name": "g_power_vdrop",
                "metric_key": "voltage_drop",
                "category": "power",
                "relation": "<=",
                "target_value": max_voltage_drop_v,
                "unit": "V",
                "expression": f"voltage_drop <= {max_voltage_drop_v}",
                "latex": f"g_{{vdrop}}=voltage\\_drop-{max_voltage_drop_v}\\le 0",
                "physical_meaning": "bus voltage drop upper bound",
            },
            {
                "name": "g_power_margin",
                "metric_key": "power_margin",
                "category": "power",
                "relation": ">=",
                "target_value": min_power_margin_pct,
                "unit": "%",
                "expression": f"power_margin >= {min_power_margin_pct}",
                "latex": f"g_{{margin}}={min_power_margin_pct}-power\\_margin\\le 0",
                "physical_meaning": "power reserve lower bound",
            },
            {
                "name": "g_power_peak",
                "metric_key": "peak_power",
                "category": "power",
                "relation": "<=",
                "target_value": max_power_w,
                "unit": "W",
                "expression": f"peak_power <= {max_power_w}",
                "latex": f"g_{{peak}}=peak\\_power-{max_power_w}\\le 0",
                "physical_meaning": "peak power budget cap",
            },
            {
                "name": "g_mission_keepout",
                "metric_key": "mission_keepout_violation",
                "category": "mission",
                "relation": "<=",
                "target_value": 0.0,
                "unit": "dimensionless",
                "expression": "mission_keepout_violation <= 0",
                "latex": "g_{mission}=mission\\_keepout\\_violation\\le 0",
                "physical_meaning": "mission keepout / FOV must be satisfied",
            },
        ]

    def _inject_missing_runtime_hard_constraints(
        self,
        hard_constraints: List[Dict[str, Any]],
        *,
        runtime_constraints: Dict[str, float],
    ) -> bool:
        if not isinstance(hard_constraints, list):
            return False

        existing_metric_keys = {
            normalize_metric_key(item.get("metric_key"))
            for item in hard_constraints
            if isinstance(item, dict) and str(item.get("metric_key", "") or "").strip()
        }

        injected = False
        for constraint_payload in self._build_runtime_hard_constraint_payloads(runtime_constraints):
            metric_key = normalize_metric_key(constraint_payload.get("metric_key"))
            if metric_key in existing_metric_keys:
                continue
            hard_constraints.append(dict(constraint_payload))
            existing_metric_keys.add(metric_key)
            injected = True

        return injected

    def _extract_dashscope_message_content(self, response: Any) -> str:
        content = getattr(
            getattr(response, "output", None),
            "choices",
            [type("Choice", (), {"message": type("Message", (), {"content": ""})()})()],
        )[0].message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("value") or ""
                    if text:
                        parts.append(str(text))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or content.get("value") or "")
        return str(content or "")

    def _extract_json_object_text(self, raw_text: str) -> str:
        text = str(raw_text or "").strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced_match:
            return fenced_match.group(1)

        start = text.find("{")
        if start < 0:
            raise ValueError("JSON object start not found")

        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        raise ValueError("JSON object end not found")

    def _sanitize_vop_policy_payload(
        self,
        payload: Any,
        *,
        max_candidates: int,
        replan_round: int,
    ) -> Dict[str, Any]:
        raw = payload if isinstance(payload, dict) else {}
        normalized_wrapper = dict(raw or {})
        if "policy_pack" not in normalized_wrapper:
            normalized_wrapper = {
                "status": str(raw.get("status", "") or "ok"),
                "source": str(raw.get("source", "") or "llm_api"),
                "reason": str(
                    raw.get("reason")
                    or raw.get("message")
                    or raw.get("error")
                    or ""
                ),
                "policy_pack": dict(raw or {}),
            }

        policy_pack = normalized_wrapper.get("policy_pack", {})
        if not isinstance(policy_pack, dict):
            policy_pack = {}
        policy_pack = dict(policy_pack)

        if not policy_pack:
            policy_pack = {
                str(key): value
                for key, value in normalized_wrapper.items()
                if str(key) not in {"status", "source", "reason", "message", "error"}
            }

        autofill_used = False
        policy_id = str(policy_pack.get("policy_id", "") or "").strip()
        if not policy_id:
            policy_id = (
                f"VOP_POLICY_{datetime.now().strftime('%Y%m%d')}_"
                f"{int(max(1, replan_round + 1)):03d}"
            )
            policy_pack["policy_id"] = policy_id
            autofill_used = True

        raw_focus = policy_pack.get("constraint_focus", [])
        if isinstance(raw_focus, str):
            constraint_focus = [
                token.strip()
                for token in re.split(r"[,\|;/\n]+", raw_focus)
                if token and token.strip()
            ]
            autofill_used = True
        elif isinstance(raw_focus, (list, tuple, set)):
            constraint_focus = [str(token).strip() for token in raw_focus if str(token).strip()]
        else:
            constraint_focus = []
            if raw_focus not in (None, "", []):
                autofill_used = True
        policy_pack["constraint_focus"] = constraint_focus

        raw_candidates = policy_pack.get("operator_candidates", [])
        if isinstance(raw_candidates, dict):
            raw_candidates = [raw_candidates]
            autofill_used = True
        elif not isinstance(raw_candidates, list):
            raw_candidates = []
            if policy_pack.get("operator_candidates") not in (None, "", []):
                autofill_used = True

        legacy_programs: List[Any] = []
        for key in ("program", "operator_program", "program_v4", "operator_program_v4"):
            candidate_program = policy_pack.get(key)
            if isinstance(candidate_program, dict):
                legacy_programs.append(candidate_program)
        for key in ("programs", "operator_programs", "programs_v4", "operator_programs_v4"):
            candidate_programs = policy_pack.get(key)
            if isinstance(candidate_programs, list):
                legacy_programs.extend(candidate_programs)
        if not raw_candidates and legacy_programs:
            raw_candidates = list(legacy_programs)
            autofill_used = True
        if not raw_candidates and isinstance(policy_pack.get("actions"), list):
            inline_program = {
                "program_id": str(policy_pack.get("program_id", "") or "").strip(),
                "actions": list(policy_pack.get("actions", []) or []),
                "rationale": str(policy_pack.get("rationale", "") or "").strip(),
            }
            if self._looks_like_v4_program_payload(inline_program):
                raw_candidates = [{"program_v4": inline_program}]
            else:
                raw_candidates = [inline_program]
            autofill_used = True

        normalized_candidates: List[Dict[str, Any]] = []
        for index, candidate in enumerate(list(raw_candidates or []), start=1):
            if not isinstance(candidate, dict):
                autofill_used = True
                continue
            candidate_id = str(candidate.get("candidate_id", "") or "").strip()
            if not candidate_id:
                candidate_id = f"cand_{index:02d}"
                autofill_used = True
            candidate_priority = float(
                self._to_finite_float(candidate.get("priority"), 1.0) or 1.0
            )
            candidate_note = str(candidate.get("note", "") or "").strip()

            program_payload_v4 = candidate.get("program_v4")
            if not isinstance(program_payload_v4, dict):
                program_payload_v4 = None
            if program_payload_v4 is None and self._looks_like_v4_program_payload(candidate):
                program_payload_v4 = dict(candidate)
                autofill_used = True
            if program_payload_v4 is None:
                candidate_program = candidate.get("program")
                if self._looks_like_v4_program_payload(candidate_program):
                    program_payload_v4 = dict(candidate_program or {})
                    autofill_used = True
            if program_payload_v4 is None and any(
                key in candidate
                for key in (
                    "targets",
                    "hard_rules",
                    "soft_preferences",
                    "panel_id",
                    "aperture_id",
                    "zone_id",
                    "mount_site_id",
                    "component_group_id",
                )
            ):
                program_payload_v4 = {
                    "program_id": candidate.get("program_id"),
                    "version": candidate.get("version") or candidate.get("dsl_version") or "opmaas-r4",
                    "rationale": candidate.get("rationale"),
                    "actions": [dict(candidate)],
                }
                autofill_used = True

            if isinstance(program_payload_v4, dict):
                normalized_program_v4, v4_autofill_used = self._normalize_vop_program_v4_payload(
                    program_payload_v4
                )
                autofill_used = autofill_used or v4_autofill_used
                if normalized_program_v4 is None:
                    continue
                program_id = str(normalized_program_v4.get("program_id", "") or "").strip()
                if not program_id:
                    normalized_program_v4 = dict(normalized_program_v4)
                    normalized_program_v4["program_id"] = (
                        f"op_prog_r{int(max(0, replan_round))}_{index:02d}"
                    )
                    autofill_used = True
                normalized_candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "priority": candidate_priority,
                        "note": candidate_note,
                        "program_v4": dict(normalized_program_v4),
                        "dsl_version": "v4",
                    }
                )
                continue

            program_payload = candidate.get("program")
            if not isinstance(program_payload, dict):
                if any(key in candidate for key in ("actions", "program_id", "rationale")):
                    program_payload = {
                        "program_id": candidate.get("program_id"),
                        "rationale": candidate.get("rationale"),
                        "actions": candidate.get("actions", []),
                    }
                    autofill_used = True
                else:
                    continue
            else:
                program_payload = dict(program_payload)
            program_payload["rationale"] = str(program_payload.get("rationale", "") or "")
            if not isinstance(program_payload.get("actions"), list):
                program_payload["actions"] = list(program_payload.get("actions", []) or [])
                autofill_used = True
            sanitized_actions: List[Dict[str, Any]] = []
            dropped_actions: List[str] = []
            for action_payload in list(program_payload.get("actions", []) or []):
                normalized_action, action_autofill_used = self._normalize_vop_action_payload(
                    action_payload
                )
                autofill_used = autofill_used or action_autofill_used
                if normalized_action is None:
                    action_name = ""
                    if isinstance(action_payload, dict):
                        action_name = str(action_payload.get("action", "") or "").strip()
                    dropped_actions.append(action_name or "invalid_action")
                    continue
                sanitized_actions.append(normalized_action)
            if not sanitized_actions:
                autofill_used = True
                continue
            program_payload["actions"] = sanitized_actions
            if dropped_actions:
                program_metadata = program_payload.get("metadata", {})
                if not isinstance(program_metadata, dict):
                    program_metadata = {}
                program_metadata = dict(program_metadata)
                program_metadata["llm_dropped_actions"] = list(dropped_actions)
                program_payload["metadata"] = program_metadata

            program_id = str(program_payload.get("program_id", "") or "").strip()
            if not program_id:
                program_payload = dict(program_payload)
                program_payload["program_id"] = f"op_prog_r{int(max(0, replan_round))}_{index:02d}"
                autofill_used = True
            normalized_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "priority": candidate_priority,
                    "note": candidate_note,
                    "program": dict(program_payload),
                    "dsl_version": "v3",
                }
            )
        policy_pack["operator_candidates"] = normalized_candidates[: max(1, int(max_candidates))]

        search_space_prior = str(policy_pack.get("search_space_prior", "") or "").strip().lower()
        if search_space_prior not in {"coordinate", "operator_program", "hybrid"}:
            search_space_prior = "operator_program" if policy_pack["operator_candidates"] else "hybrid"
            autofill_used = True
        policy_pack["search_space_prior"] = search_space_prior

        runtime_knob_priors = policy_pack.get("runtime_knob_priors", {})
        if not isinstance(runtime_knob_priors, dict):
            runtime_knob_priors = {}
            autofill_used = True
        policy_pack["runtime_knob_priors"] = dict(runtime_knob_priors)

        fidelity_plan = policy_pack.get("fidelity_plan", {})
        if not isinstance(fidelity_plan, dict):
            fidelity_plan = {}
            autofill_used = True
        policy_pack["fidelity_plan"] = dict(fidelity_plan)

        expected_effects = policy_pack.get("expected_effects", {})
        if not isinstance(expected_effects, dict):
            expected_effects = {}
            autofill_used = True
        policy_pack["expected_effects"] = dict(expected_effects)

        confidence = self._to_finite_float(policy_pack.get("confidence"), 0.0)
        policy_pack["confidence"] = max(0.0, min(float(confidence or 0.0), 1.0))
        policy_pack["rationale"] = str(
            policy_pack.get("rationale")
            or policy_pack.get("reasoning")
            or normalized_wrapper.get("reason")
            or ""
        ).strip()

        metadata = policy_pack.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            autofill_used = True
        metadata = dict(metadata)
        metadata["real_llm_primary_round"] = True
        metadata["policy_round_index"] = int(max(0, replan_round))
        metadata["autofill_used"] = bool(autofill_used)
        policy_pack["metadata"] = metadata

        policy_source = str(
            policy_pack.get("policy_source")
            or normalized_wrapper.get("source")
            or "llm_api"
        ).strip().lower()
        if not policy_source:
            policy_source = "llm_api"
        if autofill_used and policy_source == "llm_api":
            policy_source = "llm_api_autofill"
        policy_pack["policy_source"] = policy_source

        normalized_wrapper["status"] = str(normalized_wrapper.get("status", "") or "ok")
        normalized_wrapper["source"] = policy_source
        normalized_wrapper["policy_pack"] = policy_pack
        return normalized_wrapper

    def generate_strategic_plan(
        self,
        context: GlobalContextPack
    ) -> StrategicPlan:
        """
        生成战略计划。

        Args:
            context: 全局上下文包

        Returns:
            StrategicPlan: 战略计划
        """
        try:
            messages = [{"role": "system", "content": self.system_prompt}]
            for example in self.few_shot_examples:
                if not isinstance(example, dict):
                    continue
                user_content = str(example.get("user", "") or "").strip()
                assistant_content = str(example.get("assistant", "") or "").strip()
                if user_content:
                    messages.append({"role": "user", "content": user_content})
                if assistant_content:
                    messages.append({"role": "assistant", "content": assistant_content})

            messages.append({"role": "user", "content": context.to_markdown_prompt()})

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="meta_reasoner",
                    request=self._attach_llm_log_metadata(
                        {"messages": messages, "model": self.model},
                        self._current_llm_log_metadata(),
                    ),
                    response=None,
                )

            response = self.llm_client.generate_text(
                messages,
                profile_name=self.llm_profile,
                expects_json=True,
                max_tokens=self._resolve_preferred_max_tokens(
                    minimum_tokens=STRATEGIC_PLAN_MIN_MAX_TOKENS
                ),
            )
            response_text = response.content
            response_json = json.loads(self._extract_json_object_text(response_text))

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="meta_reasoner",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        response_json,
                        response.as_log_metadata(),
                    ),
                )

            if "iteration" not in response_json:
                response_json["iteration"] = context.iteration

            response_json = self._sanitize_plan_payload(response_json, context)

            try:
                plan = StrategicPlan(**response_json)
            except Exception as validation_error:
                if self.logger:
                    self.logger.logger.warning(
                        f"Meta-Reasoner 输出校验失败，回退到启发式计划: {validation_error}"
                    )
                fallback_json = self._build_fallback_plan_payload(
                    context=context,
                    reason=f"validation_error: {validation_error}",
                    raw_response=response_json,
                )
                plan = StrategicPlan(**fallback_json)

            if not plan.plan_id or plan.plan_id.startswith("PLAN_YYYYMMDD"):
                plan.plan_id = f"PLAN_{datetime.now().strftime('%Y%m%d')}_{context.iteration:03d}"

            return plan

        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.logger.warning(f"Meta-Reasoner JSON 解析失败，启用回退计划: {exc}")
            fallback_json = self._build_fallback_plan_payload(
                context=context,
                reason=f"json_decode_error: {exc}",
                raw_response=None,
            )
            return StrategicPlan(**fallback_json)
        except Exception as exc:
            if self.logger:
                self.logger.logger.warning(f"Meta-Reasoner 调用异常，启用回退计划: {exc}")
            fallback_json = self._build_fallback_plan_payload(
                context=context,
                reason=f"meta_reasoner_error: {exc}",
                raw_response=None,
            )
            return StrategicPlan(**fallback_json)

    def _to_finite_float(self, value: Any, default: Optional[float] = None) -> Optional[float]:
        """将任意输入安全转换为有限浮点数，失败返回 default。"""
        if value is None or isinstance(value, bool):
            return default
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(numeric):
            return default
        return numeric

    def generate_modeling_intent(
        self,
        context: GlobalContextPack,
        runtime_constraints: Optional[Dict[str, float]] = None,
        requirement_text: str = "",
    ) -> ModelingIntent:
        runtime_constraints = runtime_constraints or {}
        self._modeling_intent_autofill_used = False
        self._reset_modeling_intent_diagnostics()
        self._modeling_intent_diagnostics["called"] = True
        self._modeling_intent_diagnostics["timestamp"] = datetime.now().isoformat()
        self._modeling_intent_diagnostics["source"] = "pre_call"

        try:
            component_catalog = self._extract_component_catalog_from_requirement_text(
                requirement_text
            )
            component_ids = [
                str(item.get("id", "") or "").strip()
                for item in component_catalog
                if str(item.get("id", "") or "").strip()
            ]
            component_section = ""
            if component_ids:
                component_section = (
                    "## BOM Component IDs (reuse exactly)\n"
                    + ", ".join(component_ids)
                    + "\n\n"
                )

            user_prompt = (
                f"{context.to_markdown_prompt()}\n\n"
                f"## Runtime Hard Limits\n"
                f"{json.dumps(runtime_constraints, ensure_ascii=False, indent=2)}\n\n"
                f"{component_section}"
                "## Executable Metric Registry\n"
                "- use only canonical metric keys: cg_offset, min_clearance, num_collisions, boundary_violation, max_temp, safety_factor, first_modal_freq, voltage_drop, power_margin, peak_power, mission_keepout_violation\n"
                "- do not use runtime limit names as metric keys: max_temp_c, min_clearance_mm, max_cg_offset_mm, min_safety_factor, min_modal_freq_hz, max_voltage_drop_v, min_power_margin_pct, max_power_w, task_fov_violation\n"
                "- include explicit hard constraints for collision, clearance, boundary, thermal, cg_limit, structural, power, and mission keepout when corresponding limits/metrics are available\n\n"
                f"## Requirement Text\n"
                f"{requirement_text or '未提供额外需求描述，请基于当前上下文生成建模要素。'}\n\n"
                "请严格输出 ModelingIntent JSON。"
            )

            messages = [
                {"role": "system", "content": self.modeling_system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="model_agent",
                    request=self._attach_llm_log_metadata(
                        {"messages": messages, "model": self.model},
                        self._current_llm_log_metadata(),
                    ),
                    response=None,
                )

            self._modeling_intent_diagnostics["api_call_attempted"] = True
            response = self.llm_client.generate_text(
                messages,
                profile_name=self.llm_profile,
                expects_json=True,
                temperature=min(self.temperature, 0.5),
                max_tokens=self._resolve_preferred_max_tokens(
                    minimum_tokens=MODELING_INTENT_MIN_MAX_TOKENS
                ),
            )
            status_code = getattr(response, "status_code", None)
            if status_code is not None:
                try:
                    self._modeling_intent_diagnostics["response_status_code"] = int(status_code)
                except Exception:
                    self._modeling_intent_diagnostics["response_status_code"] = status_code

            self._modeling_intent_diagnostics["api_call_succeeded"] = True
            response_text = response.content
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError:
                payload = json.loads(self._extract_json_object_text(response_text))
            payload = self._sanitize_modeling_intent_payload(
                payload=payload,
                context=context,
                runtime_constraints=runtime_constraints,
                requirement_text=requirement_text,
            )
            intent = ModelingIntent(**payload)
            self._modeling_intent_diagnostics["source"] = (
                "llm_api_autofill"
                if bool(self._modeling_intent_autofill_used)
                else "llm_api"
            )
            self._modeling_intent_diagnostics["autofill_used"] = bool(
                self._modeling_intent_autofill_used
            )
            self._modeling_intent_diagnostics["used_fallback"] = False
            self._modeling_intent_diagnostics["fallback_reason"] = ""
            self._modeling_intent_diagnostics["error"] = ""

            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=context.iteration,
                    role="model_agent",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        intent.model_dump(),
                        response.as_log_metadata(),
                    ),
                )

            return intent

        except Exception as exc:
            self._modeling_intent_diagnostics["used_fallback"] = True
            self._modeling_intent_diagnostics["fallback_reason"] = str(exc)
            self._modeling_intent_diagnostics["error"] = str(exc)
            self._modeling_intent_diagnostics["source"] = "fallback_modeling_intent"
            self._modeling_intent_diagnostics["autofill_used"] = bool(
                self._modeling_intent_autofill_used
            )
            if self.logger:
                self.logger.logger.warning(
                    f"Modeling intent generation failed, fallback enabled: {exc}"
                )

            fallback_payload = self._build_fallback_modeling_intent_payload(
                context=context,
                runtime_constraints=runtime_constraints,
                reason=str(exc),
            )
            return ModelingIntent(**fallback_payload)

    def generate_policy_program(
        self,
        *,
        context: Any,
        runtime_constraints: Optional[Dict[str, Any]] = None,
        requirement_text: str = "",
        mode: str = "vop_maas",
        vop_graph: Optional[Dict[str, Any]] = None,
        max_candidates: int = 3,
        previous_policy_pack: Optional[Dict[str, Any]] = None,
        policy_effect_summary: Optional[Dict[str, Any]] = None,
        feedback_aware_fidelity_plan: Optional[Dict[str, Any]] = None,
        feedback_aware_fidelity_reason: str = "",
        replan_reason: str = "",
        replan_round: int = 0,
    ) -> Dict[str, Any]:
        runtime_constraints = runtime_constraints or {}
        graph_payload = dict(vop_graph or {})
        previous_policy_payload = dict(previous_policy_pack or {})
        policy_effect_payload = dict(policy_effect_summary or {})
        feedback_aware_fidelity_payload = dict(feedback_aware_fidelity_plan or {})
        try:
            if hasattr(context, "to_markdown_prompt"):
                context_prompt = context.to_markdown_prompt()
            else:
                context_prompt = json.dumps(context, ensure_ascii=False, indent=2)

            graph_metadata = dict(graph_payload.get("metadata", {}) or {})
            level_focus_hint = [
                str(item).strip()
                for item in list(graph_metadata.get("level_focus_hint", []) or [])
                if str(item).strip()
            ]
            fidelity_floor_hint = dict(graph_metadata.get("fidelity_floor_hint", {}) or {})

            reflective_replan_prompt = ""
            if previous_policy_payload or policy_effect_payload or replan_reason:
                reflective_replan_prompt = (
                    f"\n\n## Reflective Replanning\n"
                    f"- replan_round: {int(max(0, replan_round))}\n"
                    f"- replan_reason: {replan_reason or 'not_provided'}\n"
                    "- revise the previous policy only when the observed effect warrants it\n"
                    "- keep pymoo/mass as the sole numeric executor"
                )
            previous_policy_section = ""
            if previous_policy_payload:
                previous_policy_section = (
                    f"\n\n## Previous Policy Pack\n"
                    f"{json.dumps(previous_policy_payload, ensure_ascii=False, indent=2)}"
                )
            policy_effect_section = ""
            if policy_effect_payload:
                policy_effect_section = (
                    f"\n\n## Policy Effect Summary\n"
                    f"{json.dumps(policy_effect_payload, ensure_ascii=False, indent=2)}"
                )
            feedback_aware_fidelity_section = ""
            if feedback_aware_fidelity_payload:
                feedback_aware_fidelity_section = (
                    f"\n\n## Feedback-Aware Fidelity Recommendation\n"
                    f"{json.dumps(feedback_aware_fidelity_payload, ensure_ascii=False, indent=2)}\n"
                    f"- reason: {feedback_aware_fidelity_reason or 'not_provided'}\n"
                    "- if you set fidelity_plan, keep it at least as strong as the bounded recommendation"
                )
            scenario_focus_section = ""
            if level_focus_hint or fidelity_floor_hint:
                scenario_focus_section = (
                    f"\n\n## Scenario Focus Hints\n"
                    f"- level_focus_hint: {json.dumps(level_focus_hint, ensure_ascii=False)}\n"
                    f"- fidelity_floor_hint: {json.dumps(fidelity_floor_hint, ensure_ascii=False)}\n"
                    "- if dominant_violation_family is empty, prefer level_focus_hint before mission/geometry defaults\n"
                    "- do not weaken fidelity below fidelity_floor_hint"
                )

            user_prompt = (
                f"{context_prompt}\n\n"
                f"## Runtime Hard Limits\n"
                f"{json.dumps(runtime_constraints, ensure_ascii=False, indent=2)}\n\n"
                f"## VOP Graph\n"
                f"{json.dumps(graph_payload, ensure_ascii=False, indent=2)}\n\n"
                f"## Requirement Text\n"
                f"{requirement_text or 'No extra requirement text provided.'}\n\n"
                f"## Executable Operator DSL\n"
                f"- preferred_semantic_actions_v4: {', '.join(sorted(SUPPORTED_ACTIONS_V4))}\n"
                f"- legacy_actions_v3: {', '.join(sorted(SUPPORTED_ACTIONS))}\n"
                "- prefer semantic DSL v4 with explicit targets / hard_rules / soft_preferences\n"
                "- use exact action names only\n"
                "- for mission/FOV/keepout violations, prefer protect_fov_keepout in v4 or fov_keepout_push in v3\n"
                "- do not invent aliases such as keepout_clear\n\n"
                f"## Policy Bounds\n"
                f"- max_candidates: {int(max(1, max_candidates))}\n"
                f"- mode: {str(mode or 'vop_maas')}\n"
                "- do not output coordinates\n"
                "- do not output code\n"
                "- emit a bounded policy pack JSON only"
                f"{scenario_focus_section}"
                f"{previous_policy_section}"
                f"{policy_effect_section}"
                f"{feedback_aware_fidelity_section}"
                f"{reflective_replan_prompt}"
            )

            messages = [
                {"role": "system", "content": self.vop_policy_system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=int(getattr(context, "iteration", 1) or 1),
                    role="vop_policy_programmer",
                    request=self._attach_llm_log_metadata(
                        {"messages": messages, "model": self.model},
                        self._current_llm_log_metadata(),
                    ),
                    response=None,
                )

            response = self.llm_client.generate_text(
                messages,
                profile_name=self.llm_profile,
                expects_json=True,
                temperature=min(self.temperature, 0.5),
                max_tokens=self._resolve_preferred_max_tokens(
                    minimum_tokens=POLICY_PROGRAM_MIN_MAX_TOKENS
                ),
            )
            response_text = response.content
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError:
                payload = json.loads(self._extract_json_object_text(response_text))
            if not isinstance(payload, dict):
                raise LLMError("policy payload is not a JSON object")

            payload = self._sanitize_vop_policy_payload(
                payload,
                max_candidates=max_candidates,
                replan_round=replan_round,
            )
            if self.logger:
                self.logger.log_llm_interaction(
                    iteration=int(getattr(context, "iteration", 1) or 1),
                    role="vop_policy_programmer",
                    request=None,
                    response=self._attach_llm_log_metadata(
                        payload,
                        response.as_log_metadata(),
                    ),
                )
            return payload
        except Exception as exc:
            if self.logger:
                self.logger.logger.warning(
                    "VOP policy-program generation failed: %s", exc
                )
            return {
                "status": "error",
                "reason": str(exc),
                "source": "error",
            }

    def _sanitize_modeling_intent_payload(
        self,
        payload: Any,
        context: GlobalContextPack,
        runtime_constraints: Dict[str, float],
        requirement_text: str = "",
    ) -> Dict[str, Any]:
        """清洗 ModelingIntent 输出，兼容大小写字段与弱类型字段。"""
        raw = payload if isinstance(payload, dict) else {}

        clean: Dict[str, Any] = {
            "intent_id": str(raw.get("intent_id") or f"INTENT_{datetime.now().strftime('%Y%m%d')}_{context.iteration:03d}"),
            "iteration": int(context.iteration),
            "problem_type": str(raw.get("problem_type") or "multi_objective"),
            "variables": [],
            "objectives": [],
            "hard_constraints": [],
            "soft_constraints": [],
            "assumptions": [],
            "notes": str(raw.get("notes") or ""),
        }

        if clean["problem_type"] not in {"continuous", "discrete", "mixed", "multi_objective"}:
            clean["problem_type"] = "multi_objective"

        raw_variables = raw.get("variables", raw.get("Variables", []))
        if isinstance(raw_variables, list):
            for idx, item in enumerate(raw_variables, start=1):
                if not isinstance(item, dict):
                    continue
                lb = self._to_finite_float(item.get("lower_bound"), None)  # type: ignore[arg-type]
                ub = self._to_finite_float(item.get("upper_bound"), None)  # type: ignore[arg-type]
                unit, unit_repaired = self._normalize_modeling_unit(item.get("unit") or "mm")
                if unit_repaired:
                    self._modeling_intent_autofill_used = True
                var = {
                    "name": str(item.get("name") or f"var_{idx}"),
                    "variable_type": str(item.get("variable_type") or "continuous"),
                    "lower_bound": lb,
                    "upper_bound": ub,
                    "unit": unit,
                    "component_id": (str(item.get("component_id")) if item.get("component_id") is not None else None),
                    "description": str(item.get("description") or ""),
                }
                if var["variable_type"] not in {"continuous", "integer", "binary", "categorical"}:
                    var["variable_type"] = "continuous"
                    self._modeling_intent_autofill_used = True
                clean["variables"].append(var)

        raw_objectives = raw.get("objectives", raw.get("Objectives", []))
        if isinstance(raw_objectives, list):
            for idx, item in enumerate(raw_objectives, start=1):
                if not isinstance(item, dict):
                    continue
                direction = str(item.get("direction") or "minimize")
                if direction not in {"minimize", "maximize"}:
                    direction = "minimize"
                    self._modeling_intent_autofill_used = True
                weight = self._to_finite_float(item.get("weight"), 1.0) or 1.0
                metric_key, metric_repaired = self._normalize_modeling_metric_key(
                    item.get("metric_key") or f"metric_{idx}"
                )
                if metric_repaired:
                    self._modeling_intent_autofill_used = True
                metric_status = get_metric_status(metric_key)
                if not bool(metric_status.get("is_known", False)) or not bool(
                    metric_status.get("is_implemented", False)
                ):
                    self._modeling_intent_autofill_used = True
                    continue
                clean["objectives"].append({
                    "name": str(item.get("name") or f"obj_{idx}"),
                    "metric_key": metric_key,
                    "direction": direction,
                    "weight": float(weight),
                    "description": str(item.get("description") or ""),
                })

        clean["hard_constraints"] = self._sanitize_modeling_constraints(
            raw.get("hard_constraints", raw.get("Hard Constraints", [])),
            default_hard=True,
        )
        clean["soft_constraints"] = self._sanitize_modeling_constraints(
            raw.get("soft_constraints", raw.get("Soft Constraints", [])),
            default_hard=False,
        )

        assumptions = raw.get("assumptions", [])
        if isinstance(assumptions, list):
            clean["assumptions"] = [str(item) for item in assumptions if str(item).strip()]
        if self._repair_modeling_variables(clean["variables"], requirement_text=requirement_text):
            self._modeling_intent_autofill_used = True
        if self._inject_missing_runtime_hard_constraints(
            clean["hard_constraints"],
            runtime_constraints=runtime_constraints,
        ):
            self._modeling_intent_autofill_used = True

        # 最低保障：缺失关键字段时补齐基础模板
        if not clean["variables"] or not clean["objectives"] or not clean["hard_constraints"]:
            self._modeling_intent_autofill_used = True
            fallback = self._build_fallback_modeling_intent_payload(
                context=context,
                runtime_constraints=runtime_constraints,
                reason="incomplete_payload",
            )
            # 用 fallback 补齐缺失字段，但保留已有输出
            clean["variables"] = clean["variables"] or fallback["variables"]
            clean["objectives"] = clean["objectives"] or fallback["objectives"]
            clean["hard_constraints"] = clean["hard_constraints"] or fallback["hard_constraints"]
            clean["soft_constraints"] = clean["soft_constraints"] or fallback["soft_constraints"]
            clean["assumptions"] = clean["assumptions"] or fallback["assumptions"]

        return clean

    def _sanitize_modeling_constraints(
        self,
        constraints_raw: Any,
        default_hard: bool,
    ) -> List[Dict[str, Any]]:
        """清洗约束列表。"""
        clean_constraints: List[Dict[str, Any]] = []
        if not isinstance(constraints_raw, list):
            return clean_constraints

        for idx, item in enumerate(constraints_raw, start=1):
            if not isinstance(item, dict):
                continue

            relation = str(item.get("relation") or "<=")
            if relation not in {"<=", ">=", "=="}:
                relation = "<="
                self._modeling_intent_autofill_used = True

            target_value = self._to_finite_float(item.get("target_value"), 0.0)
            if target_value is None:
                target_value = 0.0
                self._modeling_intent_autofill_used = True

            category = str(item.get("category") or "mission")
            if category not in {"geometry", "thermal", "structural", "power", "mission", "emc"}:
                category = "mission"
                self._modeling_intent_autofill_used = True
            metric_key, metric_repaired = self._normalize_modeling_metric_key(
                item.get("metric_key") or f"metric_{idx}"
            )
            unit, unit_repaired = self._normalize_modeling_unit(item.get("unit") or "")
            if metric_repaired or unit_repaired:
                self._modeling_intent_autofill_used = True

            clean_constraints.append({
                "name": str(item.get("name") or f"{'hard' if default_hard else 'soft'}_constraint_{idx}"),
                "metric_key": metric_key,
                "category": category,
                "relation": relation,
                "target_value": float(target_value),
                "unit": unit,
                "expression": str(item.get("expression") or ""),
                "latex": str(item.get("latex") or ""),
                "physical_meaning": str(item.get("physical_meaning") or ""),
            })

        return clean_constraints

    def _build_fallback_modeling_intent_payload(
        self,
        context: GlobalContextPack,
        runtime_constraints: Dict[str, float],
        reason: str,
    ) -> Dict[str, Any]:
        """建模意图失败时的兜底模板。"""
        hard_constraints = self._build_runtime_hard_constraint_payloads(runtime_constraints)

        return {
            "intent_id": f"INTENT_FALLBACK_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "iteration": context.iteration,
            "problem_type": "multi_objective",
            "variables": [
                {
                    "name": "delta_x",
                    "variable_type": "continuous",
                    "lower_bound": -30.0,
                    "upper_bound": 30.0,
                    "unit": "mm",
                    "component_id": None,
                    "description": "global x perturbation",
                },
                {
                    "name": "delta_y",
                    "variable_type": "continuous",
                    "lower_bound": -30.0,
                    "upper_bound": 30.0,
                    "unit": "mm",
                    "component_id": None,
                    "description": "global y perturbation",
                },
                {
                    "name": "delta_z",
                    "variable_type": "continuous",
                    "lower_bound": -30.0,
                    "upper_bound": 30.0,
                    "unit": "mm",
                    "component_id": None,
                    "description": "global z perturbation",
                },
            ],
            "objectives": [
                {
                    "name": "min_cg_offset",
                    "metric_key": "cg_offset",
                    "direction": "minimize",
                    "weight": 1.0,
                    "description": "minimize centroid offset",
                },
                {
                    "name": "min_max_temp",
                    "metric_key": "max_temp",
                    "direction": "minimize",
                    "weight": 1.0,
                    "description": "minimize peak temperature",
                },
            ],
            "hard_constraints": hard_constraints,
            "soft_constraints": [
                {
                    "name": "soft_moi_balance",
                    "metric_key": "moi_imbalance",
                    "category": "structural",
                    "relation": "<=",
                    "target_value": 1.0,
                    "unit": "dimensionless",
                    "expression": "moi_imbalance <= 1.0",
                    "latex": "f_{moi}=\\sigma(I_{xx},I_{yy},I_{zz})",
                    "physical_meaning": "prefer inertial balance",
                }
            ],
            "assumptions": [
                f"fallback generated because: {reason}",
                "all mandatory constraints are transformed to inequality-compatible form",
            ],
            "notes": "fallback_modeling_intent",
        }

    def _sanitize_plan_payload(
        self,
        payload: Any,
        context: GlobalContextPack
    ) -> Dict[str, Any]:
        """
        对 LLM 返回的 StrategicPlan JSON 做鲁棒清洗。

        重点修复：
        - expected_improvements 中出现 null / 非数字，导致 pydantic float 校验失败
        - tasks/risks/context 字段类型异常
        """
        clean: Dict[str, Any] = {}
        raw = payload if isinstance(payload, dict) else {}

        clean["plan_id"] = str(raw.get("plan_id") or "")
        clean["iteration"] = context.iteration
        clean["timestamp"] = str(raw.get("timestamp") or datetime.now().isoformat())
        clean["reasoning"] = str(raw.get("reasoning") or "基于当前违反项执行稳健修复。")

        strategy = raw.get("strategy_type")
        if strategy not in {"local_search", "global_reconfig", "hybrid"}:
            strategy = "local_search"
        clean["strategy_type"] = strategy
        clean["strategy_description"] = str(
            raw.get("strategy_description") or "围绕当前主要违规执行局部修复。"
        )

        # 清洗 tasks
        clean_tasks: List[Dict[str, Any]] = []
        raw_tasks = raw.get("tasks", [])
        if isinstance(raw_tasks, list):
            for idx, task in enumerate(raw_tasks, start=1):
                if not isinstance(task, dict):
                    continue
                agent_type = task.get("agent_type")
                if agent_type not in {"geometry", "thermal", "structural", "power"}:
                    continue

                priority_val = self._to_finite_float(task.get("priority"))
                priority = int(priority_val) if priority_val is not None else 3
                priority = min(5, max(1, priority))

                constraints = task.get("constraints")
                if not isinstance(constraints, list):
                    constraints = []
                constraints = [str(item) for item in constraints if str(item).strip()]

                context_obj = task.get("context")
                if not isinstance(context_obj, dict):
                    context_obj = {}

                clean_tasks.append({
                    "task_id": str(task.get("task_id") or f"TASK_{context.iteration:03d}_{idx:03d}"),
                    "agent_type": agent_type,
                    "objective": str(task.get("objective") or "执行约束修复任务"),
                    "constraints": constraints,
                    "priority": priority,
                    "context": context_obj
                })
        clean["tasks"] = clean_tasks

        # 清洗 expected_improvements
        clean_improvements: Dict[str, float] = {}
        raw_improvements = raw.get("expected_improvements", {})
        if isinstance(raw_improvements, dict):
            for key, value in raw_improvements.items():
                numeric = self._to_finite_float(value)
                if numeric is None:
                    if self.logger:
                        self.logger.logger.warning(
                            f"Meta-Reasoner expected_improvements.{key} 非法值已忽略: {value}"
                        )
                    continue
                clean_improvements[str(key)] = numeric
        clean["expected_improvements"] = clean_improvements

        raw_risks = raw.get("risks", [])
        if isinstance(raw_risks, list):
            clean["risks"] = [str(item) for item in raw_risks if str(item).strip()]
        else:
            clean["risks"] = []

        return clean

    def _build_fallback_plan_payload(
        self,
        context: GlobalContextPack,
        reason: str,
        raw_response: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        当 LLM 输出不可用或校验失败时，生成可执行的兜底 StrategicPlan。
        目标是“不中断优化流程”，而非替代 LLM 长期决策能力。
        """
        primary_violation = context.violations[0] if context.violations else None
        task_agent = "geometry"
        task_objective = "执行局部几何修复并复核约束。"
        expected_improvements: Dict[str, float] = {}
        task_constraints: List[str] = [
            "不得引入新碰撞",
            "优先降低总惩罚分"
        ]
        task_context: Dict[str, Any] = {
            "fallback_reason": reason
        }

        if primary_violation is not None:
            task_constraints.insert(0, primary_violation.to_natural_language())
            task_context["primary_violation"] = primary_violation.description
            task_context["violation_metric"] = primary_violation.metric_value
            task_context["violation_threshold"] = primary_violation.threshold

            if primary_violation.violation_type == "thermal":
                task_agent = "thermal"
                task_objective = "优先降低峰值温度并保持几何可行。"
                expected_improvements["max_temp"] = -max(
                    1.0,
                    float(primary_violation.metric_value - primary_violation.threshold)
                )
            elif primary_violation.violation_type == "geometry":
                desc = primary_violation.description
                if "间隙" in desc or "重叠" in desc:
                    task_agent = "geometry"
                    task_objective = "优先提升最小间隙并消除潜在重叠风险。"
                    expected_improvements["min_clearance"] = max(
                        1.0,
                        float(primary_violation.threshold - primary_violation.metric_value) + 0.5
                    )
                elif "质心" in desc:
                    task_agent = "geometry"
                    task_objective = "优先降低质心偏移并保持布局可制造性。"
                    expected_improvements["cg_offset_magnitude"] = -max(
                        1.0,
                        float(primary_violation.metric_value - primary_violation.threshold)
                    )
            elif primary_violation.violation_type == "structural":
                task_agent = "structural"
                task_objective = "优先提升结构安全系数。"
                expected_improvements["safety_factor"] = max(
                    0.1,
                    float(primary_violation.threshold - primary_violation.metric_value)
                )
            elif primary_violation.violation_type == "power":
                task_agent = "power"
                task_objective = "优先降低功率约束违反风险。"

        if not expected_improvements:
            expected_improvements["penalty_score"] = -10.0

        return {
            "plan_id": f"PLAN_FALLBACK_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "iteration": context.iteration,
            "timestamp": datetime.now().isoformat(),
            "reasoning": (
                "Meta-Reasoner 输出不可用，启用鲁棒兜底策略。"
                f" 触发原因: {reason}。优先修复当前主要违反项并保持总惩罚分下降。"
            ),
            "strategy_type": "local_search",
            "strategy_description": "回退到可执行的局部修复策略，保障优化流程连续性",
            "tasks": [
                {
                    "task_id": f"TASK_FALLBACK_{context.iteration:03d}_001",
                    "agent_type": task_agent,
                    "objective": task_objective,
                    "constraints": task_constraints,
                    "priority": 1,
                    "context": {
                        **task_context,
                        "raw_response": raw_response if isinstance(raw_response, dict) else {}
                    }
                }
            ],
            "expected_improvements": expected_improvements,
            "risks": [
                "兜底计划保守，可能降低单轮探索幅度",
                "建议后续继续监控 LLM 输出稳定性"
            ]
        }

    def evaluate_plan_quality(self, plan: StrategicPlan) -> Dict[str, Any]:
        """
        评估战略计划的质量

        Args:
            plan: 战略计划

        Returns:
            评估结果字典
        """
        quality_score = 0.0
        issues = []

        # 检查推理过程的完整性
        if len(plan.reasoning) < 100:
            issues.append("推理过程过于简短，缺乏详细分析")
        else:
            quality_score += 0.3

        # 检查任务分配的合理性
        if len(plan.tasks) == 0:
            issues.append("未分配任何任务")
        elif len(plan.tasks) > 5:
            issues.append("任务过多，可能导致协调困难")
        else:
            quality_score += 0.3

        # 检查预期改进的具体性
        if len(plan.expected_improvements) == 0:
            issues.append("未明确预期改进指标")
        else:
            quality_score += 0.2

        # 检查风险评估
        if len(plan.risks) == 0:
            issues.append("未进行风险评估")
        else:
            quality_score += 0.2

        return {
            "quality_score": quality_score,
            "issues": issues,
            "is_acceptable": quality_score >= 0.6
        }

    def refine_plan(
        self,
        plan: StrategicPlan,
        feedback: str
    ) -> StrategicPlan:
        """
        根据反馈优化战略计划

        Args:
            plan: 原始计划
            feedback: 反馈信息

        Returns:
            优化后的计划
        """
        # 构建优化提示
        refinement_prompt = f"""
原始计划：
{json.dumps(plan.model_dump(), indent=2, ensure_ascii=False)}

反馈意见：
{feedback}

请根据反馈优化计划，输出新的JSON格式的StrategicPlan。
"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": refinement_prompt}
        ]

        try:
            response = self.llm_client.generate_text(
                messages,
                profile_name=self.llm_profile,
                expects_json=True,
            )
            response_json = json.loads(self._extract_json_object_text(response.content))
            refined_plan = StrategicPlan(**response_json)
            refined_plan.plan_id = f"{plan.plan_id}_refined"

            return refined_plan

        except Exception as e:
            raise LLMError(f"Plan refinement failed: {e}")


if __name__ == "__main__":
    # 测试Meta-Reasoner
    print("Testing Meta-Reasoner...")

    # 创建示例上下文
    from optimization.protocol import GeometryMetrics, ThermalMetrics, StructuralMetrics, PowerMetrics

    context = GlobalContextPack(
        iteration=1,
        design_state_summary="电池组位于X=13.0mm，与肋板间隙3.0mm",
        geometry_metrics=GeometryMetrics(
            min_clearance=3.0,
            com_offset=[0.5, -0.2, 0.1],
            moment_of_inertia=[1.2, 1.3, 1.1],
            packing_efficiency=75.0,
            num_collisions=0
        ),
        thermal_metrics=ThermalMetrics(
            max_temp=58.2,
            min_temp=18.5,
            avg_temp=35.6,
            temp_gradient=2.5
        ),
        structural_metrics=StructuralMetrics(
            max_stress=45.0,
            max_displacement=0.12,
            first_modal_freq=85.0,
            safety_factor=2.1
        ),
        power_metrics=PowerMetrics(
            total_power=120.0,
            peak_power=150.0,
            power_margin=25.0,
            voltage_drop=0.3
        ),
        violations=[
            ViolationItem(
                violation_id="V001",
                violation_type="geometry",
                severity="major",
                description="电池与肋板间隙不足",
                affected_components=["Battery_01", "Rib_01"],
                metric_value=3.0,
                threshold=3.0
            )
        ],
        history_summary="Iter 1: 尝试向-X移动，失败"
    )

    print("\n✓ Context created successfully!")
    print(f"Violations: {len(context.violations)}")
    print(f"Markdown prompt length: {len(context.to_markdown_prompt())} chars")
