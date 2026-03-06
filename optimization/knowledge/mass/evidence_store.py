"""Persistence layer for mass-mode retrieval evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .evidence_schema import MassEvidence


def _default_mass_evidence() -> List[MassEvidence]:
    defaults: List[Dict[str, Any]] = [
        {
            "evidence_id": "MASS_STD_001",
            "phase_hint": "A",
            "category": "standard",
            "title": "Hard constraints must be explicit and executable",
            "content": (
                "For mass mode, encode hard constraints as executable g(x)<=0 metrics. "
                "Avoid vague natural-language-only constraints."
            ),
            "query_signature": {"violation_types": ["geometry", "thermal", "structural", "power"]},
            "action_signature": {"operator_family": "modeling_intent"},
            "outcome_signature": {"strict_proxy_feasible": True, "diagnosis_status": "feasible"},
            "physics_provenance": {"source_gate_passed": True},
            "tags": ["constraints", "modeling", "g(x)<=0"],
        },
        {
            "evidence_id": "MASS_STD_002",
            "phase_hint": "A",
            "category": "heuristic",
            "title": "Thermal and clearance violations should be handled jointly",
            "content": (
                "When thermal and clearance are both violated, prioritize moves that reduce hotspot "
                "concentration without introducing overlap. Avoid one-metric-only fixes."
            ),
            "query_signature": {"violation_types": ["thermal", "geometry"]},
            "action_signature": {"operator_family": "hot_spread"},
            "outcome_signature": {"strict_proxy_feasible": True, "diagnosis_status": "feasible"},
            "physics_provenance": {"source_gate_passed": True},
            "tags": ["thermal", "clearance", "joint_fix"],
        },
        {
            "evidence_id": "MASS_STD_003",
            "phase_hint": "D",
            "category": "heuristic",
            "title": "Reflection should separate strict-feasible and relaxed-feasible",
            "content": (
                "A relaxed-feasible run is not equivalent to strict-feasible. Reflection should mark "
                "strict replay result and avoid over-claiming convergence."
            ),
            "query_signature": {"violation_types": ["geometry", "thermal", "structural", "power"]},
            "action_signature": {"operator_family": "reflection"},
            "outcome_signature": {"strict_proxy_feasible": True, "diagnosis_status": "feasible"},
            "physics_provenance": {"source_gate_passed": True},
            "tags": ["reflection", "strict", "relaxed"],
        },
        {
            "evidence_id": "MASS_STD_004",
            "phase_hint": "C",
            "category": "heuristic",
            "title": "Operator seeds should bias search, not bypass pymoo",
            "content": (
                "Use operator-program seeds and bounds priors to improve first-feasible efficiency. "
                "Do not bypass multi-objective search with direct coordinates."
            ),
            "query_signature": {"violation_types": ["geometry", "thermal", "structural", "power"]},
            "action_signature": {"operator_family": "operator_program_seed"},
            "outcome_signature": {"strict_proxy_feasible": True, "diagnosis_status": "feasible"},
            "physics_provenance": {"source_gate_passed": True},
            "tags": ["operator_program", "seed_population", "pymoo"],
        },
        {
            "evidence_id": "MASS_STD_005",
            "phase_hint": "D",
            "category": "heuristic",
            "title": "Source provenance gates improve reliability of reflection",
            "content": (
                "Prefer evidence with explicit metric sources and source-gate pass records. "
                "De-prioritize mixed or unknown physics provenance."
            ),
            "query_signature": {"violation_types": ["structural", "power"]},
            "action_signature": {"operator_family": "source_gate"},
            "outcome_signature": {"strict_proxy_feasible": True, "diagnosis_status": "feasible"},
            "physics_provenance": {"source_gate_passed": True},
            "tags": ["provenance", "source_gate", "reliability"],
        },
    ]
    return [MassEvidence.from_dict(item) for item in defaults]


class MassEvidenceStore:
    """Durable storage for structured mass evidence."""

    def __init__(self, base_path: str, *, logger: Optional[Any] = None) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.evidence_file = self.base_path / "mass_evidence.jsonl"
        self.logger = logger
        self._items: List[MassEvidence] = []
        self.reload()

    def reload(self) -> None:
        if not self.evidence_file.exists():
            self._items = _default_mass_evidence()
            self.save()
            return

        loaded: List[MassEvidence] = []
        for raw in self.evidence_file.read_text(encoding="utf-8").splitlines():
            line = str(raw).strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                evidence = MassEvidence.from_dict(dict(payload or {}))
            except Exception:
                continue
            if not evidence.evidence_id:
                continue
            loaded.append(evidence)

        if not loaded:
            loaded = _default_mass_evidence()
            self._items = loaded
            self.save()
            return

        self._items = loaded

    def save(self) -> None:
        lines = [json.dumps(item.to_dict(), ensure_ascii=False) for item in self._items]
        content = "\n".join(lines).strip()
        if content:
            content += "\n"
        self.evidence_file.write_text(content, encoding="utf-8")

    def list(self) -> List[MassEvidence]:
        return [item.copy() for item in list(self._items or [])]

    def _fingerprint(self, evidence: MassEvidence) -> str:
        raw = f"{evidence.category}|{evidence.title}|{evidence.content}".strip().lower()
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _next_id(self) -> str:
        next_index = len(self._items) + 1
        return f"MASS_CASE_{next_index:05d}"

    def add(self, evidence: MassEvidence, *, deduplicate: bool = True) -> MassEvidence:
        if not evidence.evidence_id:
            evidence.evidence_id = self._next_id()

        if deduplicate:
            fp = self._fingerprint(evidence)
            for item in self._items:
                if self._fingerprint(item) == fp:
                    return item.copy()

        self._items.append(evidence.copy())
        self.save()
        return evidence.copy()

    def add_many(self, evidence_items: Iterable[MassEvidence], *, deduplicate: bool = True) -> int:
        added = 0
        for evidence in list(evidence_items or []):
            existing_count = len(self._items)
            self.add(evidence, deduplicate=deduplicate)
            if len(self._items) > existing_count:
                added += 1
        return int(added)

    def stats(self) -> Dict[str, Any]:
        categories: Dict[str, int] = {}
        phase_hints: Dict[str, int] = {}
        for item in self._items:
            categories[item.category] = int(categories.get(item.category, 0) + 1)
            phase_hints[item.phase_hint] = int(phase_hints.get(item.phase_hint, 0) + 1)
        return {
            "total": int(len(self._items)),
            "categories": categories,
            "phase_hints": phase_hints,
            "path": self.evidence_file.as_posix(),
        }

