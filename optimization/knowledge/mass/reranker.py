"""Feasibility-calibrated reranking for mass evidence."""

from __future__ import annotations

from typing import Dict, Iterable, List

from optimization.protocol import GlobalContextPack

from .evidence_schema import RetrievalCandidate


class FeasibilityCalibratedReranker:
    """Rerank retrieval candidates with feasibility and provenance priors."""

    def rerank(
        self,
        candidates: Iterable[RetrievalCandidate],
        *,
        context: GlobalContextPack,
        top_k: int,
    ) -> List[RetrievalCandidate]:
        merged: Dict[str, RetrievalCandidate] = {}
        for candidate in list(candidates or []):
            evidence_id = str(candidate.evidence.evidence_id)
            if not evidence_id:
                continue
            existing = merged.get(evidence_id)
            if existing is None or float(candidate.score) > float(existing.score):
                merged[evidence_id] = candidate
            else:
                existing.channels = list(set(existing.channels + list(candidate.channels or [])))

        reranked: List[RetrievalCandidate] = []
        for candidate in merged.values():
            score = float(candidate.score)
            score += self._feasibility_bonus(candidate)
            score += self._provenance_bonus(candidate)
            score += self._dominant_violation_bonus(candidate, context=context)
            candidate.score = float(score)
            reranked.append(candidate)

        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked[: max(int(top_k), 0)]

    def _feasibility_bonus(self, candidate: RetrievalCandidate) -> float:
        outcome = dict(candidate.evidence.outcome_signature or {})
        score = 0.0
        if bool(outcome.get("strict_proxy_feasible", False)):
            score += 0.80
        diagnosis = str(outcome.get("diagnosis_status", "")).strip().lower()
        if diagnosis in {"feasible", "feasible_but_stalled"}:
            score += 0.20
        if bool(outcome.get("relaxed_only", False)):
            score -= 0.50
        return score

    def _provenance_bonus(self, candidate: RetrievalCandidate) -> float:
        provenance = dict(candidate.evidence.physics_provenance or {})
        if bool(provenance.get("source_gate_passed", False)):
            return 0.25
        source = str(provenance.get("thermal_source", "") or "").strip().lower()
        if source and source != "proxy":
            return 0.10
        return 0.0

    def _dominant_violation_bonus(
        self,
        candidate: RetrievalCandidate,
        *,
        context: GlobalContextPack,
    ) -> float:
        violations = list(context.violations or [])
        if not violations:
            return 0.0
        dominant = str(violations[0].violation_type or "").strip().lower()
        hints = {
            str(item).strip().lower()
            for item in list(candidate.evidence.query_signature.get("dominant_violations", []) or [])
        }
        if dominant and dominant in hints:
            return 0.35

        support = {
            str(item).strip().lower()
            for item in list(candidate.evidence.query_signature.get("violation_types", []) or [])
        }
        if dominant and dominant in support:
            return 0.20
        return 0.0

