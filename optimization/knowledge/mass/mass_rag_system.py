"""Mass-mode constraint-graph retrieval system."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from optimization.protocol import GlobalContextPack, KnowledgeItem

from .evidence_schema import MassEvidence, RetrievalCandidate
from .evidence_store import MassEvidenceStore
from .policy_router import PhaseAdaptivePolicyRouter
from .reranker import FeasibilityCalibratedReranker
from .retrievers import SemanticRetriever, SymbolicRetriever


class MassRAGSystem:
    """
    Constraint-Graph RAG for mass mode.

    This implementation is intentionally mass-only and does not reuse the legacy
    flat RAG module.
    """

    def __init__(
        self,
        api_key: str = "",
        knowledge_base_path: str = "data/knowledge_base",
        embedding_model: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_semantic: bool = True,
        filter_anomalous_cases: bool = True,
        anomaly_temp_tokens: Optional[List[str]] = None,
        anomaly_max_temp_delta_abs: float = 200.0,
        logger: Optional[Any] = None,
    ) -> None:
        _ = api_key
        _ = embedding_model
        _ = base_url
        self.enable_semantic = bool(enable_semantic)
        self.filter_anomalous_cases = bool(filter_anomalous_cases)
        self.anomaly_temp_tokens = [
            str(token).strip().lower()
            for token in list(
                anomaly_temp_tokens
                or ["999.0", "9999.0", "221,735,840", "2.217x10^8", "仿真失效", "未收敛"]
            )
            if str(token).strip()
        ]
        self.anomaly_max_temp_delta_abs = float(max(anomaly_max_temp_delta_abs, 0.0))
        self.logger = logger

        self.store = MassEvidenceStore(base_path=knowledge_base_path, logger=logger)
        self.symbolic_retriever = SymbolicRetriever()
        self.semantic_retriever = SemanticRetriever(dim=512)
        self.reranker = FeasibilityCalibratedReranker()
        self.policy_router = PhaseAdaptivePolicyRouter()

    def retrieve(
        self,
        context: GlobalContextPack,
        top_k: int = 5,
        use_semantic: bool = True,
        use_keyword: bool = True,
        phase: str = "A",
    ) -> List[KnowledgeItem]:
        evidences = self.store.list()
        if not evidences:
            return []

        policy = self.policy_router.route(context=context, phase=phase, top_k=top_k)
        candidates: List[RetrievalCandidate] = []
        if bool(use_keyword):
            candidates.extend(
                self.symbolic_retriever.retrieve(
                    evidences,
                    context=context,
                    phase=policy.phase,
                    top_k=policy.symbolic_k,
                )
            )
        if bool(use_semantic) and self.enable_semantic:
            candidates.extend(
                self.semantic_retriever.retrieve(
                    evidences,
                    context=context,
                    phase=policy.phase,
                    top_k=policy.semantic_k,
                )
            )
        if not candidates:
            candidates = self._build_fallback_candidates(
                evidences=evidences,
                phase=policy.phase,
                top_k=policy.final_k,
            )
            if not candidates:
                return []

        if policy.graph_expand:
            candidates.extend(
                self._graph_expand(
                    seed_candidates=candidates,
                    evidences=evidences,
                    hops=policy.graph_expand_hops,
                )
            )

        reranked = self.reranker.rerank(candidates, context=context, top_k=max(policy.final_k * 3, policy.final_k))
        filtered = [item for item in reranked if not self._is_anomalous_case_item(item.evidence)]
        if not filtered:
            filtered = [
                item
                for item in reranked
                if str(item.evidence.category).strip().lower() != "case"
            ]
        if not filtered:
            filtered = reranked

        return [
            self._to_knowledge_item(candidate)
            for candidate in filtered[: policy.final_k]
        ]

    def _build_fallback_candidates(
        self,
        *,
        evidences: Iterable[MassEvidence],
        phase: str,
        top_k: int,
    ) -> List[RetrievalCandidate]:
        phase_norm = str(phase or "A").strip().upper()
        scored: List[RetrievalCandidate] = []
        for evidence in list(evidences or []):
            phase_hint = str(evidence.phase_hint or "any").strip().upper()
            score = 0.05
            if phase_hint in {"ANY", ""}:
                score += 0.03
            elif phase_hint == phase_norm:
                score += 0.08
            scored.append(
                RetrievalCandidate(
                    evidence=evidence.copy(),
                    score=float(score),
                    channels=["fallback"],
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(int(top_k), 0)]

    def _graph_expand(
        self,
        *,
        seed_candidates: Iterable[RetrievalCandidate],
        evidences: Iterable[MassEvidence],
        hops: int,
    ) -> List[RetrievalCandidate]:
        hops = max(int(hops), 0)
        if hops == 0:
            return []

        evidence_map: Dict[str, MassEvidence] = {
            str(item.evidence_id): item.copy() for item in list(evidences or []) if str(item.evidence_id).strip()
        }
        if not evidence_map:
            return []

        tag_index: Dict[str, Set[str]] = {}
        violation_index: Dict[str, Set[str]] = {}
        for evidence in evidence_map.values():
            evidence_id = str(evidence.evidence_id)
            for tag in list(evidence.tags or []):
                tag_index.setdefault(str(tag), set()).add(evidence_id)
            for violation_type in list(evidence.query_signature.get("violation_types", []) or []):
                key = str(violation_type).strip().lower()
                if not key:
                    continue
                violation_index.setdefault(key, set()).add(evidence_id)

        visited: Set[str] = set()
        frontier: List[RetrievalCandidate] = [item for item in list(seed_candidates or [])]
        expanded: List[RetrievalCandidate] = []

        for _ in range(hops):
            next_frontier: List[RetrievalCandidate] = []
            for seed in frontier:
                seed_id = str(seed.evidence.evidence_id)
                if not seed_id:
                    continue
                visited.add(seed_id)

                neighbor_ids: Set[str] = set()
                for tag in list(seed.evidence.tags or []):
                    neighbor_ids.update(tag_index.get(str(tag), set()))
                for violation_type in list(seed.evidence.query_signature.get("violation_types", []) or []):
                    neighbor_ids.update(violation_index.get(str(violation_type).strip().lower(), set()))

                for neighbor_id in neighbor_ids:
                    if neighbor_id == seed_id or neighbor_id in visited:
                        continue
                    neighbor = evidence_map.get(neighbor_id)
                    if neighbor is None:
                        continue
                    shared_tags = len(set(seed.evidence.tags or []) & set(neighbor.tags or []))
                    shared_violations = len(
                        set(seed.evidence.query_signature.get("violation_types", []) or [])
                        & set(neighbor.query_signature.get("violation_types", []) or [])
                    )
                    boost = float(shared_tags) * 0.05 + float(shared_violations) * 0.10
                    score = max(0.0, float(seed.score) * 0.55 + boost)
                    if score <= 0.0:
                        continue
                    candidate = RetrievalCandidate(
                        evidence=neighbor.copy(),
                        score=score,
                        channels=["graph_expand"],
                    )
                    expanded.append(candidate)
                    next_frontier.append(candidate)
                    visited.add(neighbor_id)
            frontier = next_frontier
            if not frontier:
                break

        return expanded

    def _is_anomalous_case_item(self, evidence: MassEvidence) -> bool:
        if not self.filter_anomalous_cases:
            return False
        if str(evidence.category).strip().lower() != "case":
            return False

        text = evidence.as_retrieval_text().lower()
        if any(token in text for token in self.anomaly_temp_tokens):
            return True
        if re.search(r"(?:^|[^0-9])999(?:9)?(?:\\.0+)?\\s*(?:°\\s*)?(?:c|℃)", text):
            return True

        metrics = dict(evidence.metadata.get("metrics_improvement", {}) or {})
        max_temp_delta = metrics.get("max_temp", None)
        try:
            parsed_delta = float(max_temp_delta)
        except (TypeError, ValueError):
            parsed_delta = None
        if parsed_delta is not None and abs(parsed_delta) >= self.anomaly_max_temp_delta_abs:
            return True
        return False

    def _to_knowledge_item(self, candidate: RetrievalCandidate) -> KnowledgeItem:
        evidence = candidate.evidence
        metadata = dict(evidence.metadata or {})
        metadata["mode"] = "mass"
        metadata["phase_hint"] = str(evidence.phase_hint)
        metadata["query_signature"] = dict(evidence.query_signature or {})
        metadata["action_signature"] = dict(evidence.action_signature or {})
        metadata["outcome_signature"] = dict(evidence.outcome_signature or {})
        metadata["physics_provenance"] = dict(evidence.physics_provenance or {})
        metadata["tags"] = list(evidence.tags or [])
        metadata["retrieval_channels"] = list(candidate.channels or [])
        return KnowledgeItem(
            item_id=str(evidence.evidence_id),
            category=str(evidence.category),  # type: ignore[arg-type]
            title=str(evidence.title),
            content=str(evidence.content),
            relevance_score=float(candidate.score),
            metadata=metadata,
        )

    def add_knowledge(
        self,
        title: str,
        content: str,
        category: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeItem:
        metadata = dict(metadata or {})
        evidence = MassEvidence(
            evidence_id="",
            phase_hint=str(metadata.get("phase_hint", "any") or "any"),
            category=str(category or "case"),
            title=str(title or ""),
            content=str(content or ""),
            query_signature=dict(metadata.get("query_signature", {}) or {}),
            action_signature=dict(metadata.get("action_signature", {}) or {}),
            outcome_signature=dict(metadata.get("outcome_signature", {}) or {}),
            physics_provenance=dict(metadata.get("physics_provenance", {}) or {}),
            tags=list(metadata.get("tags", []) or []),
            metadata=metadata,
        )
        stored = self.store.add(evidence, deduplicate=True)
        return KnowledgeItem(
            item_id=str(stored.evidence_id),
            category=str(stored.category),  # type: ignore[arg-type]
            title=str(stored.title),
            content=str(stored.content),
            relevance_score=0.0,
            metadata=dict(stored.metadata or {}),
        )

    def add_case_from_iteration(
        self,
        iteration: int,
        problem: str,
        solution: str,
        success: bool,
        metrics_improvement: Dict[str, float],
    ) -> None:
        title = f"iter_{int(iteration):03d}_case"
        content = (
            f"Problem: {str(problem or '').strip()}\n"
            f"Solution: {str(solution or '').strip()}\n"
            f"Outcome: {'success' if bool(success) else 'failure'}\n"
            f"Metrics: {dict(metrics_improvement or {})}"
        )
        outcome_signature = {
            "diagnosis_status": "feasible" if bool(success) else "no_feasible",
            "strict_proxy_feasible": bool(success),
            "relaxed_only": False,
        }
        metadata: Dict[str, Any] = {
            "iteration": int(iteration),
            "success": bool(success),
            "metrics_improvement": dict(metrics_improvement or {}),
            "outcome_signature": outcome_signature,
            "phase_hint": "D",
            "tags": ["runtime_case", "iteration_case"],
        }
        self.add_knowledge(title=title, content=content, category="case", metadata=metadata)

    def ingest_evidence(self, evidence_items: Iterable[MassEvidence]) -> int:
        return self.store.add_many(evidence_items, deduplicate=True)

    def stats(self) -> Dict[str, Any]:
        return dict(self.store.stats() or {})
