"""Retrievers for structured mass-mode evidence."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Dict, Iterable, List, Set

import numpy as np

from optimization.protocol import GlobalContextPack

from .evidence_schema import MassEvidence, RetrievalCandidate


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
def _text_tokens(text: str) -> List[str]:
    return [tok.lower() for tok in _TOKEN_PATTERN.findall(str(text or ""))]


def _hash_feature_vector(text: str, *, dim: int = 512) -> np.ndarray:
    vec = np.zeros(dim, dtype=float)
    tokens = _text_tokens(text)
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % dim
        vec[idx] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


def _context_violations(context: GlobalContextPack) -> Set[str]:
    return {str(v.violation_type).strip().lower() for v in list(context.violations or [])}


def _dominant_violation(context: GlobalContextPack) -> str:
    if not context.violations:
        return ""
    return str(context.violations[0].violation_type or "").strip().lower()


def _query_keywords(context: GlobalContextPack) -> Set[str]:
    pieces = [str(context.design_state_summary or ""), str(context.history_summary or "")]
    for item in list(context.violations or []):
        pieces.append(str(item.description or ""))
    for comp in list(getattr(context.thermal_metrics, "hotspot_components", []) or []):
        pieces.append(str(comp))
    joined = "\n".join(pieces)
    return set(_text_tokens(joined))


class SymbolicRetriever:
    """Rule-based evidence filtering and scoring."""

    def retrieve(
        self,
        evidences: Iterable[MassEvidence],
        *,
        context: GlobalContextPack,
        phase: str,
        top_k: int,
    ) -> List[RetrievalCandidate]:
        phase_norm = str(phase or "A").strip().upper()
        violations = _context_violations(context)
        dominant = _dominant_violation(context)
        keywords = _query_keywords(context)

        candidates: List[RetrievalCandidate] = []
        for evidence in list(evidences or []):
            score = 0.0

            phase_hint = str(evidence.phase_hint or "any").strip().upper()
            if phase_hint in {"ANY", ""}:
                score += 0.10
            elif phase_hint == phase_norm:
                score += 0.50
            else:
                score -= 0.20

            evidence_violation_types = {
                str(item).strip().lower()
                for item in list(evidence.query_signature.get("violation_types", []) or [])
            }
            if violations and evidence_violation_types:
                intersection = len(violations & evidence_violation_types)
                score += float(intersection) * 1.10
                if intersection == 0:
                    score -= 0.40

            dominant_hints = {
                str(item).strip().lower()
                for item in list(evidence.query_signature.get("dominant_violations", []) or [])
            }
            if dominant and dominant_hints and dominant in dominant_hints:
                score += 0.60

            if bool(evidence.outcome_signature.get("strict_proxy_feasible", False)):
                score += 0.65
            diagnosis_status = str(evidence.outcome_signature.get("diagnosis_status", "")).strip().lower()
            if diagnosis_status in {"feasible", "feasible_but_stalled"}:
                score += 0.20
            if bool(evidence.outcome_signature.get("relaxed_only", False)):
                score -= 0.35

            if bool(evidence.physics_provenance.get("source_gate_passed", False)):
                score += 0.20

            evidence_text_tokens = set(_text_tokens(evidence.as_retrieval_text()))
            if keywords and evidence_text_tokens:
                overlap = len(keywords & evidence_text_tokens)
                if overlap > 0:
                    score += min(0.45, float(overlap) * 0.05)

            if score > 0.0:
                candidates.append(
                    RetrievalCandidate(
                        evidence=evidence.copy(),
                        score=float(score),
                        channels=["symbolic"],
                    )
                )

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[: max(int(top_k), 0)]


class SemanticRetriever:
    """Feature-hashing semantic retriever (local, no external API dependency)."""

    def __init__(self, *, dim: int = 512) -> None:
        self.dim = max(int(dim), 64)

    def retrieve(
        self,
        evidences: Iterable[MassEvidence],
        *,
        context: GlobalContextPack,
        phase: str,
        top_k: int,
    ) -> List[RetrievalCandidate]:
        evidence_list = list(evidences or [])
        if not evidence_list:
            return []

        query_text = self._build_query_text(context=context, phase=phase)
        query_vec = _hash_feature_vector(query_text, dim=self.dim)
        query_norm = float(np.linalg.norm(query_vec))
        if query_norm <= 0.0:
            return []

        matrix = np.vstack(
            [_hash_feature_vector(item.as_retrieval_text(), dim=self.dim) for item in evidence_list]
        )
        similarities = np.matmul(matrix, query_vec.reshape(-1, 1)).reshape(-1)

        candidates: List[RetrievalCandidate] = []
        for idx, similarity in enumerate(similarities):
            sim_value = float(similarity)
            if not math.isfinite(sim_value):
                continue
            if sim_value <= 0.0:
                continue
            candidates.append(
                RetrievalCandidate(
                    evidence=evidence_list[idx].copy(),
                    score=sim_value,
                    channels=["semantic"],
                )
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[: max(int(top_k), 0)]

    def _build_query_text(self, *, context: GlobalContextPack, phase: str) -> str:
        parts: List[str] = [f"phase:{str(phase or 'A').strip().upper()}"]
        parts.append(str(context.design_state_summary or ""))
        parts.append(str(context.history_summary or ""))
        for violation in list(context.violations or []):
            parts.append(str(violation.violation_type or ""))
            parts.append(str(violation.description or ""))
        for comp in list(getattr(context.thermal_metrics, "hotspot_components", []) or []):
            parts.append(str(comp))
        return "\n".join([part for part in parts if str(part).strip()])
