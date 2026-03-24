"""
LLM interaction artifact writer for the rebuilt scenario mainline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from core.artifact_index import (
    default_raw_scope_for_run_mode,
    normalize_artifact_scope,
    scope_relative_root,
)
from core.mode_contract import normalize_observability_mode, normalize_runtime_mode


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
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
    """Persist request/response artifacts under mode-scoped directories."""

    _ACTIVE_DIRS = ("mass", "legacy")

    def __init__(self, run_dir: str, *, run_mode: str):
        self.run_dir = str(run_dir)
        self.run_mode = normalize_runtime_mode(run_mode, default="mass")
        self.default_scope = default_raw_scope_for_run_mode(self.run_mode)
        self.mode_dirs: Dict[str, str] = {}

    def _ensure_scope_dir(self, scope: str) -> str:
        normalized = normalize_artifact_scope(scope, default="legacy")
        if normalized not in self._ACTIVE_DIRS:
            normalized = "legacy"
        existing = self.mode_dirs.get(normalized)
        if existing:
            return str(existing)
        relative_root = Path(scope_relative_root(normalized)) / "llm_interactions"
        path = Path(self.run_dir) / relative_root
        path.mkdir(parents=True, exist_ok=True)
        self.mode_dirs[normalized] = str(path)
        return str(path)

    def infer_mode_from_role(self, role: str) -> str:
        normalized = str(role or "").strip().lower()
        if not normalized:
            return self.default_scope
        if normalized.startswith("model_agent_") or normalized.startswith("intent_modeler"):
            return "mass"
        if normalized.startswith("policy_program") or normalized.startswith("vop_"):
            return "mass"
        return self.default_scope

    def normalize_mode(self, mode: Optional[str]) -> str:
        normalized = str(mode or "").strip().lower()
        normalized = normalize_observability_mode(normalized, default=self.default_scope)
        if normalized in self._ACTIVE_DIRS:
            return normalized
        return self.default_scope

    def resolve_dir(self, *, role: str = "", mode: Optional[str] = None) -> str:
        scope = self.normalize_mode(mode) if str(mode or "").strip() else self.infer_mode_from_role(role)
        return self._ensure_scope_dir(scope)

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

                json.dump(
                    _sanitize_json_value(request),
                    f,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )

        if response is not None:
            resp_path = os.path.join(target_dir, f"{prefix}_resp.json")
            with open(resp_path, "w", encoding="utf-8") as f:
                import json

                json.dump(
                    _sanitize_json_value(response),
                    f,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )

        return prefix
