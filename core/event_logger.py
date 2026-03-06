"""
Event logger for MaaS observability artifacts.

Writes structured events under:
  <run_dir>/events/
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.observability_schema import (
    AttemptEvent,
    CandidateEvent,
    GenerationEvent,
    LayoutEvent,
    PhaseEvent,
    PhysicsEvent,
    PolicyEvent,
    RunManifestEvent,
)


class EventLogger:
    """Minimal typed event sink for MaaS pipeline observability."""

    def __init__(self, run_dir: str, *, persisted_run_dir: str | None = None):
        self.run_dir = str(run_dir)
        self.persisted_run_dir = str(persisted_run_dir or run_dir)
        self.run_id = Path(run_dir).name
        self.events_dir = Path(run_dir) / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)

        self.run_manifest_path = self.events_dir / "run_manifest.json"
        self.phase_events_path = self.events_dir / "phase_events.jsonl"
        self.attempt_events_path = self.events_dir / "attempt_events.jsonl"
        self.generation_events_path = self.events_dir / "generation_events.jsonl"
        self.policy_events_path = self.events_dir / "policy_events.jsonl"
        self.physics_events_path = self.events_dir / "physics_events.jsonl"
        self.candidate_events_path = self.events_dir / "candidate_events.jsonl"
        self.layout_events_path = self.events_dir / "layout_events.jsonl"

        # Seed manifest with minimal stable identity for downstream tooling.
        self.write_run_manifest({"run_id": self.run_id, "run_dir": self.persisted_run_dir})

    def _inject_defaults(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(payload or {})
        data.setdefault("run_id", self.run_id)
        data.setdefault("timestamp", datetime.now().isoformat())
        return data

    @staticmethod
    def _sanitize_json_value(value: Any) -> Any:
        if isinstance(value, float):
            return value if math.isfinite(value) else None

        if isinstance(value, dict):
            return {str(k): EventLogger._sanitize_json_value(v) for k, v in value.items()}

        if isinstance(value, (list, tuple, set)):
            return [EventLogger._sanitize_json_value(v) for v in value]

        tolist_fn = getattr(value, "tolist", None)
        if callable(tolist_fn):
            try:
                return EventLogger._sanitize_json_value(tolist_fn())
            except Exception:
                pass

        item_fn = getattr(value, "item", None)
        if callable(item_fn):
            try:
                return EventLogger._sanitize_json_value(item_fn())
            except Exception:
                return value

        return value

    @staticmethod
    def _json_dumps_safe(payload: Dict[str, Any], *, indent: int | None = None) -> str:
        return json.dumps(
            EventLogger._sanitize_json_value(payload),
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(EventLogger._json_dumps_safe(payload) + "\n")

    def write_run_manifest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        merged = {}
        if self.run_manifest_path.exists():
            try:
                merged = json.loads(self.run_manifest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                merged = {}

        merged.update(dict(payload or {}))
        merged.setdefault("run_id", self.run_id)
        merged.setdefault("run_dir", self.persisted_run_dir)
        merged.setdefault("created_at", datetime.now().isoformat())
        merged["updated_at"] = datetime.now().isoformat()
        parsed = RunManifestEvent(**merged)
        self.run_manifest_path.write_text(
            EventLogger._json_dumps_safe(parsed.model_dump(), indent=2),
            encoding="utf-8",
        )
        return parsed.model_dump()

    def append_phase_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = PhaseEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.phase_events_path, obj)
        return obj

    def append_attempt_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = AttemptEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.attempt_events_path, obj)
        return obj

    def append_generation_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = GenerationEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.generation_events_path, obj)
        return obj

    def append_policy_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = PolicyEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.policy_events_path, obj)
        return obj

    def append_physics_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = PhysicsEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.physics_events_path, obj)
        return obj

    def append_candidate_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = CandidateEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.candidate_events_path, obj)
        return obj

    def append_layout_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        parsed = LayoutEvent(**self._inject_defaults(payload))
        obj = parsed.model_dump()
        self._append_jsonl(self.layout_events_path, obj)
        return obj
