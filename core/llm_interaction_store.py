"""
LLM interaction artifact writer with active-mode partitioning.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.mode_contract import normalize_observability_mode


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
        # Keep finite floats; non-finite values are converted to null-like None.
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json_value(v) for v in value]
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _sanitize_json_value(tolist_fn())
        except Exception:
            pass
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _sanitize_json_value(item_fn())
        except Exception:
            return value
    return value


class LLMInteractionStore:
    """Persist request/response artifacts under mode-partitioned directories."""

    _ACTIVE_DIRS = ("agent_loop", "mass", "shared")

    def __init__(self, run_dir: str):
        self.root_dir = os.path.join(str(run_dir), "llm_interactions")
        os.makedirs(self.root_dir, exist_ok=True)
        self.mode_dirs: Dict[str, str] = {}

    def _ensure_mode_dir(self, bucket: str) -> str:
        normalized = str(bucket or "").strip().lower()
        if normalized not in self._ACTIVE_DIRS:
            normalized = "shared"
        existing = self.mode_dirs.get(normalized)
        if existing:
            return str(existing)
        path = os.path.join(self.root_dir, normalized)
        os.makedirs(path, exist_ok=True)
        self.mode_dirs[normalized] = path
        return str(path)

    def infer_mode_from_role(self, role: str) -> str:
        normalized = str(role or "").strip().lower()
        if not normalized:
            return "shared"
        if normalized.startswith("model_agent_") or normalized.startswith("intent_modeler"):
            return "mass"
        if normalized.startswith("policy_program"):
            # Reserved policy-program traces are grouped with active mass flow.
            return "mass"
        if normalized in {
            "meta_reasoner",
            "geometry_agent",
            "thermal_agent",
            "structural_agent",
            "power_agent",
        }:
            return "agent_loop"
        return "shared"

    def normalize_mode(self, mode: Optional[str]) -> str:
        normalized = normalize_observability_mode(mode, default="shared")
        if normalized in {"agent_loop", "mass"}:
            return normalized
        return "shared"

    def resolve_dir(self, *, role: str = "", mode: Optional[str] = None) -> str:
        if str(mode or "").strip():
            bucket = self.normalize_mode(mode)
        else:
            bucket = self.infer_mode_from_role(role)
        return self._ensure_mode_dir(bucket)

    def get_active_buckets(self) -> list[str]:
        return sorted(self.mode_dirs.keys())

    def write(
        self,
        *,
        iteration: int,
        role: Optional[str],
        request: Optional[Dict[str, Any]],
        response: Optional[Dict[str, Any]],
        mode: Optional[str] = None,
    ) -> str:
        prefix = f"iter_{int(iteration):02d}"
        if role:
            prefix = f"{prefix}_{str(role)}"
        target_dir = self.resolve_dir(role=str(role or ""), mode=mode)

        if request is not None:
            req_path = os.path.join(target_dir, f"{prefix}_req.json")
            with open(req_path, "w", encoding="utf-8") as f:
                import json

                json.dump(_sanitize_json_value(request), f, indent=2, ensure_ascii=False, allow_nan=False)

        if response is not None:
            resp_path = os.path.join(target_dir, f"{prefix}_resp.json")
            with open(resp_path, "w", encoding="utf-8") as f:
                import json

                json.dump(_sanitize_json_value(response), f, indent=2, ensure_ascii=False, allow_nan=False)

        return prefix
