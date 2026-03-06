"""Schema for mass-mode retrieval evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


_ALLOWED_CATEGORIES = {"standard", "case", "formula", "heuristic"}
_ALLOWED_PHASE_HINTS = {"A", "B", "C", "D", "any"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_category(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _ALLOWED_CATEGORIES:
        return "case"
    return normalized


def _normalize_phase_hint(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == "":
        return "ANY"
    if normalized == "ANY":
        return "any"
    if normalized not in _ALLOWED_PHASE_HINTS:
        return "any"
    return normalized


@dataclass
class MassEvidence:
    """Structured evidence unit for mass-mode retrieval."""

    evidence_id: str
    mode: str = "mass"
    phase_hint: str = "any"
    category: str = "case"
    title: str = ""
    content: str = ""
    query_signature: Dict[str, Any] = field(default_factory=dict)
    action_signature: Dict[str, Any] = field(default_factory=dict)
    outcome_signature: Dict[str, Any] = field(default_factory=dict)
    physics_provenance: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.mode = "mass"
        self.category = _normalize_category(self.category)
        self.phase_hint = _normalize_phase_hint(self.phase_hint)
        self.title = str(self.title or "").strip()
        self.content = str(self.content or "").strip()
        self.tags = [str(tag).strip().lower() for tag in list(self.tags or []) if str(tag).strip()]
        self.query_signature = dict(self.query_signature or {})
        self.action_signature = dict(self.action_signature or {})
        self.outcome_signature = dict(self.outcome_signature or {})
        self.physics_provenance = dict(self.physics_provenance or {})
        self.metadata = dict(self.metadata or {})
        self.created_at = str(self.created_at or _utc_now_iso())

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MassEvidence":
        return cls(
            evidence_id=str(payload.get("evidence_id", "")).strip(),
            mode="mass",
            phase_hint=str(payload.get("phase_hint", "any") or "any"),
            category=str(payload.get("category", "case") or "case"),
            title=str(payload.get("title", "") or ""),
            content=str(payload.get("content", "") or ""),
            query_signature=dict(payload.get("query_signature", {}) or {}),
            action_signature=dict(payload.get("action_signature", {}) or {}),
            outcome_signature=dict(payload.get("outcome_signature", {}) or {}),
            physics_provenance=dict(payload.get("physics_provenance", {}) or {}),
            tags=list(payload.get("tags", []) or []),
            metadata=dict(payload.get("metadata", {}) or {}),
            created_at=str(payload.get("created_at", "") or _utc_now_iso()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": str(self.evidence_id),
            "mode": "mass",
            "phase_hint": str(self.phase_hint),
            "category": str(self.category),
            "title": str(self.title),
            "content": str(self.content),
            "query_signature": dict(self.query_signature or {}),
            "action_signature": dict(self.action_signature or {}),
            "outcome_signature": dict(self.outcome_signature or {}),
            "physics_provenance": dict(self.physics_provenance or {}),
            "tags": list(self.tags or []),
            "metadata": dict(self.metadata or {}),
            "created_at": str(self.created_at),
        }

    def as_retrieval_text(self) -> str:
        """Text used by semantic retrieval backends."""
        sections = [
            self.title,
            self.content,
            " ".join(list(self.tags or [])),
            " ".join(str(x) for x in list(self.query_signature.get("violation_types", []) or [])),
            str(self.action_signature.get("operator_family", "")),
        ]
        return "\n".join([str(x) for x in sections if str(x).strip()])

    def copy(self) -> "MassEvidence":
        return MassEvidence.from_dict(self.to_dict())


@dataclass
class RetrievalCandidate:
    """Intermediate candidate with score trace."""

    evidence: MassEvidence
    score: float
    channels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": str(self.evidence.evidence_id),
            "score": float(self.score),
            "channels": list(self.channels or []),
        }

