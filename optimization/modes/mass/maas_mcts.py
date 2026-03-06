"""
Lightweight MCTS planner for MaaS modeling path selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Callable, Dict, List, Optional

from .protocol import ModelingIntent


@dataclass
class MCTSVariant:
    """One expandable modeling branch variant."""

    action: str
    intent: ModelingIntent
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCTSEvaluation:
    """Evaluation result attached to one searched node."""

    score: float
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCTSNode:
    """Node state for MCTS traversal."""

    node_id: int
    intent: ModelingIntent
    depth: int
    action_from_parent: str = "root"
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent: Optional["MCTSNode"] = None
    children: List["MCTSNode"] = field(default_factory=list)
    visits: int = 0
    total_value: float = 0.0
    best_value: float = float("-inf")
    evaluation: Optional[MCTSEvaluation] = None

    @property
    def mean_value(self) -> float:
        if self.visits <= 0:
            return 0.0
        return float(self.total_value / float(self.visits))

    def uct_score(self, parent_visits: int, c_uct: float) -> float:
        if self.visits <= 0:
            return float("inf")
        explore = c_uct * math.sqrt(math.log(max(parent_visits, 1)) / float(self.visits))
        return float(self.mean_value + explore)


@dataclass
class MCTSSearchResult:
    """Final output from one MCTS search."""

    root: MCTSNode
    best_node: Optional[MCTSNode]
    iterations: int
    stop_reason: str = "budget_exhausted"
    best_score: Optional[float] = None
    best_cv: Optional[float] = None
    pruning_events: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)
    branch_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    action_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class MaaSMCTSPlanner:
    """
    Lightweight MCTS implementation for modeling-path selection.

    - Selection: UCT
    - Expansion: callback-provided variants
    - Simulation: callback-provided evaluator
    - Backpropagation: cumulative score
    """

    def __init__(
        self,
        max_depth: int = 2,
        budget: int = 4,
        c_uct: float = 1.2,
        stagnation_rounds: int = 0,
        min_score_improvement: float = 1e-6,
        min_cv_improvement: float = 1e-4,
        prune_margin: float = 0.0,
        action_prior_weight: float = 0.02,
        cv_penalty_weight: float = 0.2,
    ) -> None:
        self.max_depth = max(1, int(max_depth))
        self.budget = max(1, int(budget))
        self.c_uct = float(c_uct)
        self.stagnation_rounds = max(0, int(stagnation_rounds))
        self.min_score_improvement = max(0.0, float(min_score_improvement))
        self.min_cv_improvement = max(0.0, float(min_cv_improvement))
        self.prune_margin = max(0.0, float(prune_margin))
        self.action_prior_weight = float(action_prior_weight)
        self.cv_penalty_weight = max(0.0, float(cv_penalty_weight))
        self._next_node_id = 1
        self._action_stats: Dict[str, Dict[str, Any]] = {}

    def update_policy_weights(
        self,
        *,
        action_prior_weight: Optional[float] = None,
        cv_penalty_weight: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Runtime update for policy-related weights.
        """
        if action_prior_weight is not None:
            self.action_prior_weight = float(action_prior_weight)
        if cv_penalty_weight is not None:
            self.cv_penalty_weight = max(0.0, float(cv_penalty_weight))
        return {
            "action_prior_weight": float(self.action_prior_weight),
            "cv_penalty_weight": float(self.cv_penalty_weight),
        }

    def get_policy_weights(self) -> Dict[str, float]:
        return {
            "action_prior_weight": float(self.action_prior_weight),
            "cv_penalty_weight": float(self.cv_penalty_weight),
        }

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except Exception:
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    @staticmethod
    def _normalize_action(action: str) -> str:
        raw = str(action or "").strip().lower()
        if not raw:
            return "unknown"
        return re.sub(r"_d\d+$", "", raw)

    def _record_action_feedback(
        self,
        action: str,
        score: float,
        best_cv: Optional[float],
    ) -> None:
        key = self._normalize_action(action)
        stat = self._action_stats.get(key)
        if stat is None:
            stat = {
                "action_key": key,
                "count": 0,
                "sum_score": 0.0,
                "mean_score": 0.0,
                "best_score": float("-inf"),
                "best_cv": None,
            }
            self._action_stats[key] = stat

        stat["count"] = int(stat["count"]) + 1
        stat["sum_score"] = float(stat["sum_score"]) + float(score)
        stat["mean_score"] = float(stat["sum_score"]) / float(stat["count"])
        stat["best_score"] = max(float(stat["best_score"]), float(score))
        if best_cv is not None and math.isfinite(best_cv):
            prev_cv = stat.get("best_cv")
            if prev_cv is None or float(best_cv) < float(prev_cv):
                stat["best_cv"] = float(best_cv)

    def _action_prior(self, action: str) -> float:
        key = self._normalize_action(action)
        stat = self._action_stats.get(key)
        if not stat:
            return 0.0
        mean_score = self._safe_float(stat.get("mean_score"))
        best_cv = self._safe_float(stat.get("best_cv"))
        prior = 0.0
        if mean_score is not None:
            prior += self.action_prior_weight * mean_score
        if best_cv is not None:
            prior -= self.cv_penalty_weight * max(best_cv, 0.0)
        return float(prior)

    def _export_action_stats(self) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}
        for key, stat in self._action_stats.items():
            payload[key] = {
                "action_key": key,
                "count": int(stat.get("count", 0)),
                "mean_score": self._safe_float(stat.get("mean_score")),
                "best_score": self._safe_float(stat.get("best_score")),
                "best_cv": self._safe_float(stat.get("best_cv")),
                "prior_score": float(self._action_prior(key)),
            }
        return payload

    def _collect_branch_stats(self, root: MCTSNode) -> Dict[str, Dict[str, Any]]:
        """
        Collect branch-level search statistics rooted at depth-1 children.
        """
        stats: Dict[str, Dict[str, Any]] = {}
        for branch in root.children:
            stack = [branch]
            node_count = 0
            eval_count = 0
            branch_best_score = float("-inf")
            branch_best_cv = float("inf")
            while stack:
                node = stack.pop()
                node_count += 1
                if node.best_value > branch_best_score:
                    branch_best_score = node.best_value
                if node.evaluation is not None:
                    eval_count += 1
                    payload = node.evaluation.payload or {}
                    best_cv = self._safe_float(payload.get("best_cv"))
                    if best_cv is not None:
                        branch_best_cv = min(branch_best_cv, best_cv)
                if node.children:
                    stack.extend(node.children)

            stats[branch.action_from_parent] = {
                "action": branch.action_from_parent,
                "visits": int(branch.visits),
                "mean_value": float(branch.mean_value),
                "best_value": (
                    float(branch_best_score)
                    if branch_best_score > float("-inf")
                    else None
                ),
                "best_cv": (
                    float(branch_best_cv)
                    if math.isfinite(branch_best_cv)
                    else None
                ),
                "node_count": int(node_count),
                "evaluation_count": int(eval_count),
            }
        return stats

    def search(
        self,
        root_intent: ModelingIntent,
        propose_variants: Callable[[MCTSNode], List[MCTSVariant]],
        evaluate_node: Callable[[MCTSNode, int], MCTSEvaluation],
    ) -> MCTSSearchResult:
        root = MCTSNode(node_id=0, intent=root_intent, depth=0, action_from_parent="root")
        self._action_stats = {}
        best_node: Optional[MCTSNode] = None
        best_score = float("-inf")
        best_cv = float("inf")
        records: List[Dict[str, Any]] = []
        stop_reason = "budget_exhausted"
        last_score_improve_rollout = 0
        last_cv_improve_rollout = 0
        iterations_done = 0
        pruning_events = 0

        for rollout in range(1, self.budget + 1):
            iterations_done = rollout
            path = [root]
            node = root
            rollout_pruned = 0

            # Selection
            while node.children and node.depth < self.max_depth:
                parent_visits = max(node.visits, 1)
                selectable: List[MCTSNode] = []
                for child in node.children:
                    if child.visits <= 0:
                        selectable.append(child)
                        continue
                    upper = child.uct_score(parent_visits, self.c_uct) + self._action_prior(
                        child.action_from_parent
                    )
                    if upper >= (best_score - self.prune_margin):
                        selectable.append(child)
                    else:
                        rollout_pruned += 1
                        pruning_events += 1
                if not selectable:
                    selectable = list(node.children)
                node = max(
                    selectable,
                    key=lambda child: (
                        child.uct_score(parent_visits, self.c_uct) +
                        self._action_prior(child.action_from_parent)
                    ),
                )
                path.append(node)

            # Expansion
            # Unvisited leaves are evaluated once before expansion so deeper
            # proposals can consume fresh diagnostics (e.g., dominant_violation)
            # instead of empty/default payload.
            evaluate_before_expand = (
                node.visits == 0 and
                node.evaluation is None
            )
            if (not evaluate_before_expand) and node.depth < self.max_depth and not node.children:
                variants = propose_variants(node)
                variants = sorted(
                    variants,
                    key=lambda variant: self._action_prior(variant.action),
                    reverse=True,
                )
                for variant in variants:
                    child = MCTSNode(
                        node_id=self._next_node_id,
                        intent=variant.intent,
                        depth=node.depth + 1,
                        action_from_parent=variant.action,
                        metadata=dict(variant.metadata),
                        parent=node,
                    )
                    self._next_node_id += 1
                    node.children.append(child)

                if node.children:
                    unvisited = [child for child in node.children if child.visits == 0]
                    node = unvisited[0] if unvisited else node.children[0]
                    path.append(node)

            # Simulation / Evaluation
            evaluation = evaluate_node(node, rollout)
            node.evaluation = evaluation
            score = float(evaluation.score)
            payload = evaluation.payload or {}
            current_best_cv = self._safe_float(payload.get("best_cv"))
            action_prior = self._action_prior(node.action_from_parent)

            # Post-eval expansion for fresh leaves: rollout-1 can evaluate root
            # then expand root with diagnostics; rollout-2 can do the same for
            # depth-1 nodes without requiring an extra rollout budget.
            if evaluate_before_expand and node.depth < self.max_depth and not node.children:
                variants = propose_variants(node)
                variants = sorted(
                    variants,
                    key=lambda variant: self._action_prior(variant.action),
                    reverse=True,
                )
                for variant in variants:
                    child = MCTSNode(
                        node_id=self._next_node_id,
                        intent=variant.intent,
                        depth=node.depth + 1,
                        action_from_parent=variant.action,
                        metadata=dict(variant.metadata),
                        parent=node,
                    )
                    self._next_node_id += 1
                    node.children.append(child)

            # Backpropagation
            for item in path:
                item.visits += 1
                item.total_value += score
                item.best_value = max(item.best_value, score)
                if payload:
                    item.metadata["latest_rollout_payload"] = dict(payload)
            self._record_action_feedback(node.action_from_parent, score, current_best_cv)

            if score > best_score + self.min_score_improvement:
                best_score = score
                best_node = node
                last_score_improve_rollout = rollout

            if (
                current_best_cv is not None and
                math.isfinite(current_best_cv) and
                current_best_cv < (best_cv - self.min_cv_improvement)
            ):
                best_cv = current_best_cv
                last_cv_improve_rollout = rollout

            records.append({
                "rollout": int(rollout),
                "selected_node_id": int(node.node_id),
                "selected_action": node.action_from_parent,
                "score": score,
                "best_score_so_far": float(best_score),
                "best_cv_so_far": float(best_cv) if math.isfinite(best_cv) else None,
                "depth": int(node.depth),
                "root_visits": int(root.visits),
                "pruned_children": int(rollout_pruned),
                "selected_action_prior": float(action_prior),
                "action_prior_weight": float(self.action_prior_weight),
                "cv_penalty_weight": float(self.cv_penalty_weight),
            })

            if self.stagnation_rounds > 0:
                latest_improve = max(last_score_improve_rollout, last_cv_improve_rollout)
                if latest_improve <= 0:
                    latest_improve = 1
                if rollout - latest_improve >= self.stagnation_rounds:
                    # Keep root-level branch coverage before early-stopping.
                    # This avoids terminating before key first-depth variants
                    # (e.g. uniform_relax) are evaluated at least once.
                    unvisited_root_children = sum(
                        1 for child in root.children if int(child.visits) <= 0
                    )
                    if unvisited_root_children > 0:
                        continue
                    stop_reason = f"stagnation_{self.stagnation_rounds}"
                    break

        branch_stats = self._collect_branch_stats(root)
        return MCTSSearchResult(
            root=root,
            best_node=best_node,
            iterations=iterations_done,
            stop_reason=stop_reason,
            best_score=float(best_score) if best_score > float("-inf") else None,
            best_cv=float(best_cv) if math.isfinite(best_cv) else None,
            pruning_events=int(pruning_events),
            records=records,
            branch_stats=branch_stats,
            action_stats=self._export_action_stats(),
        )
