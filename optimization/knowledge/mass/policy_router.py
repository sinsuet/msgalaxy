"""Phase-adaptive retrieval routing policy for mass mode."""

from __future__ import annotations

from dataclasses import dataclass

from optimization.protocol import GlobalContextPack


@dataclass
class RetrievalPolicy:
    phase: str
    symbolic_k: int
    semantic_k: int
    graph_expand: bool
    graph_expand_hops: int
    final_k: int


class PhaseAdaptivePolicyRouter:
    """Dynamic policy selection by phase and problem complexity."""

    def route(self, *, context: GlobalContextPack, phase: str, top_k: int) -> RetrievalPolicy:
        phase_norm = str(phase or "A").strip().upper()
        final_k = max(int(top_k), 1)
        complexity = self._estimate_complexity(context)

        if phase_norm == "A":
            symbolic_k = final_k * (3 if complexity >= 2 else 2)
            semantic_k = final_k * (2 if complexity >= 1 else 1)
            return RetrievalPolicy(
                phase=phase_norm,
                symbolic_k=symbolic_k,
                semantic_k=semantic_k,
                graph_expand=bool(complexity >= 1),
                graph_expand_hops=1,
                final_k=final_k,
            )
        if phase_norm == "D":
            symbolic_k = final_k * 3
            semantic_k = final_k
            return RetrievalPolicy(
                phase=phase_norm,
                symbolic_k=symbolic_k,
                semantic_k=semantic_k,
                graph_expand=True,
                graph_expand_hops=2 if complexity >= 2 else 1,
                final_k=final_k,
            )

        symbolic_k = final_k * 2
        semantic_k = final_k
        return RetrievalPolicy(
            phase=phase_norm,
            symbolic_k=symbolic_k,
            semantic_k=semantic_k,
            graph_expand=bool(complexity >= 2),
            graph_expand_hops=1,
            final_k=final_k,
        )

    def _estimate_complexity(self, context: GlobalContextPack) -> int:
        violations = list(context.violations or [])
        severity_weight = 0
        for item in violations:
            sev = str(getattr(item, "severity", "")).strip().lower()
            if sev == "critical":
                severity_weight += 2
            elif sev == "major":
                severity_weight += 1

        violation_types = {str(item.violation_type).strip().lower() for item in violations}
        mixed_domains = len(violation_types)
        score = 0
        if len(violations) >= 2:
            score += 1
        if mixed_domains >= 2:
            score += 1
        if severity_weight >= 3:
            score += 1
        return int(score)

