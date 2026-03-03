"""
Pymoo execution wrapper with robustness and AOCC tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import traceback
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from pymoo.algorithms.moo.moead import MOEAD
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.algorithms.moo.nsga3 import NSGA3
    from pymoo.core.callback import Callback
    from pymoo.core.problem import Problem
    from pymoo.operators.sampling.rnd import FloatRandomSampling
    from pymoo.optimize import minimize
    from pymoo.termination import get_termination
    from pymoo.util.ref_dirs import get_reference_directions

    _PYMOO_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - only raised when pymoo is absent
    MOEAD = None  # type: ignore[assignment]
    NSGA2 = None  # type: ignore[assignment]
    NSGA3 = None  # type: ignore[assignment]
    Callback = object  # type: ignore[misc, assignment]
    Problem = object  # type: ignore[misc, assignment]
    FloatRandomSampling = object  # type: ignore[misc, assignment]
    get_reference_directions = None  # type: ignore[assignment]
    minimize = None  # type: ignore[assignment]
    get_termination = None  # type: ignore[assignment]
    _PYMOO_IMPORT_ERROR = exc

try:
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
except Exception:  # pragma: no cover - optional fallback for pymoo variants
    SBX = None  # type: ignore[assignment]
    PM = None  # type: ignore[assignment]


def _require_pymoo() -> None:
    if _PYMOO_IMPORT_ERROR is not None:
        raise ImportError(
            "pymoo is required for optimization/pymoo runners. "
            "Install dependencies with `pip install -r requirements.txt`."
        ) from _PYMOO_IMPORT_ERROR


_SUPPORTED_ALGORITHMS = ("nsga2", "nsga3", "moead")
_ALGORITHM_LABELS = {
    "nsga2": "NSGA2",
    "nsga3": "NSGA3",
    "moead": "MOEAD",
}


def calculate_aocc(series: List[float], lower_is_better: bool = True) -> float:
    """
    Calculate Area Over Convergence Curve (AOCC) in [0, 1] style scale.

    Higher value means faster convergence.
    """

    if not series:
        return 0.0

    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return 0.0

    lo = float(np.min(arr))
    hi = float(np.max(arr))
    span = hi - lo
    if span <= 1e-12:
        normalized = np.ones_like(arr)
    else:
        if lower_is_better:
            normalized = (hi - arr) / span
        else:
            normalized = (arr - lo) / span

    x = np.arange(normalized.size, dtype=float)
    area = float(np.trapezoid(normalized, x))
    return area / max(float(normalized.size - 1), 1.0)


class _ConvergenceTracker(Callback):
    def __init__(self):
        super().__init__()
        self.best_cv: List[float] = []
        self.best_feasible_sum_f: List[float] = []
        self.generation_records: List[Dict[str, Any]] = []

    @staticmethod
    def _extract_cv(pop, n_rows: int) -> np.ndarray:
        cv_raw = pop.get("raw_cv")
        if cv_raw is None:
            cv_raw = pop.get("CV")
        if cv_raw is not None:
            cv = np.asarray(cv_raw, dtype=float).reshape(-1)
            if cv.size == int(n_rows):
                finite_mask = np.isfinite(cv)
                if np.any(finite_mask):
                    fixed = cv.copy()
                    fixed[~finite_mask] = float("inf")
                    return fixed

        g_raw = pop.get("G")
        if g_raw is not None:
            g = np.asarray(g_raw, dtype=float)
            if g.ndim == 1:
                g = g.reshape(-1, 1)
            if g.shape[0] == int(n_rows):
                cv_from_g = np.sum(np.maximum(g, 0.0), axis=1).reshape(-1)
                finite_mask = np.isfinite(cv_from_g)
                fixed = cv_from_g.copy()
                fixed[~finite_mask] = float("inf")
                return fixed

        return np.full(int(n_rows), float("inf"), dtype=float)

    def notify(self, algorithm):
        pop = algorithm.pop
        f = np.asarray(pop.get("F"), dtype=float)
        rows = int(f.shape[0]) if f.ndim >= 1 else 0
        cv = self._extract_cv(pop, n_rows=rows)

        best_cv = float(np.min(cv)) if cv.size else float("inf")
        self.best_cv.append(best_cv)

        feasible = cv <= 1e-12
        feasible_count = int(np.sum(feasible)) if cv.size else 0
        feasible_ratio = (
            float(feasible_count) / float(rows)
            if rows > 0 else None
        )
        finite_cv = cv[np.isfinite(cv)] if cv.size else np.asarray([], dtype=float)
        mean_cv = float(np.mean(finite_cv)) if finite_cv.size > 0 else None
        best_feasible_sum_f = float("inf")
        if np.any(feasible):
            feasible_sum_f = np.sum(f[feasible], axis=1)
            best_feasible_sum_f = float(np.min(feasible_sum_f))
            self.best_feasible_sum_f.append(best_feasible_sum_f)
        else:
            self.best_feasible_sum_f.append(float("inf"))

        self.generation_records.append(
            {
                "generation": int(len(self.best_cv)),
                "population_size": int(rows),
                "feasible_count": int(feasible_count),
                "feasible_ratio": feasible_ratio,
                "best_cv": best_cv if np.isfinite(best_cv) else None,
                "mean_cv": mean_cv,
                "best_feasible_sum_f": (
                    best_feasible_sum_f if np.isfinite(best_feasible_sum_f) else None
                ),
            }
        )


class _SeededFloatSampling(FloatRandomSampling):
    """
    Random sampling with deterministic seed vectors injected at population head.
    """

    def __init__(self, seed_vectors: np.ndarray):
        super().__init__()
        vectors = np.asarray(seed_vectors, dtype=float)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        self.seed_vectors = vectors

    def _do(self, problem, n_samples, **kwargs):
        samples = super()._do(problem, n_samples, **kwargs)
        if self.seed_vectors.size == 0:
            return samples
        n_inject = min(samples.shape[0], self.seed_vectors.shape[0])
        for idx in range(n_inject):
            samples[idx, :] = np.clip(self.seed_vectors[idx], problem.xl, problem.xu)
        return samples


class _BiasedSeededFloatSampling(_SeededFloatSampling):
    """
    Seeded sampling with additional local jitter around injected vectors.
    """

    def __init__(
        self,
        seed_vectors: np.ndarray,
        *,
        sigma_ratio: float = 0.02,
        jitter_count: int = 0,
        rng_seed: int = 42,
    ) -> None:
        super().__init__(seed_vectors)
        self.sigma_ratio = max(float(sigma_ratio), 0.0)
        self.jitter_count = max(int(jitter_count), 0)
        self.rng_seed = int(rng_seed)
        self._rng = np.random.default_rng(self.rng_seed)

    def _do(self, problem, n_samples, **kwargs):
        samples = super()._do(problem, n_samples, **kwargs)
        if self.jitter_count <= 0 or self.sigma_ratio <= 0.0:
            return samples
        if self.seed_vectors.size == 0:
            return samples

        n_seed = min(samples.shape[0], self.seed_vectors.shape[0])
        n_jitter = min(max(samples.shape[0] - n_seed, 0), self.jitter_count)
        if n_jitter <= 0:
            return samples

        span = np.asarray(problem.xu, dtype=float) - np.asarray(problem.xl, dtype=float)
        sigma = np.maximum(np.abs(span) * self.sigma_ratio, 1e-9)
        for idx in range(n_jitter):
            src = self.seed_vectors[idx % n_seed]
            jittered = np.asarray(src, dtype=float) + self._rng.normal(0.0, sigma, size=src.shape)
            samples[n_seed + idx, :] = np.clip(jittered, problem.xl, problem.xu)
        return samples


class _ConstraintPenaltyProblem(Problem):
    """
    Constraint adapter for MOEA/D.

    pymoo's MOEA/D implementation in current dependency baseline does not
    support constrained problems. This wrapper converts constraints into
    additive objective penalties and preserves raw CV for diagnostics.
    """

    def __init__(self, base_problem, *, penalty_scale: float = 1000.0):
        self.base_problem = base_problem
        self.penalty_scale = max(float(penalty_scale), 1.0)
        n_var = int(getattr(base_problem, "n_var", 0) or 0)
        n_obj = int(getattr(base_problem, "n_obj", 1) or 1)
        xl = np.asarray(getattr(base_problem, "xl", np.zeros(n_var)), dtype=float)
        xu = np.asarray(getattr(base_problem, "xu", np.ones(n_var)), dtype=float)
        super().__init__(
            n_var=n_var,
            n_obj=n_obj,
            n_ieq_constr=0,
            xl=xl,
            xu=xu,
        )

    @staticmethod
    def _raw_cv_from_g(g_values: Optional[np.ndarray], n_rows: int) -> np.ndarray:
        if g_values is None:
            return np.zeros(int(n_rows), dtype=float)
        g = np.asarray(g_values, dtype=float)
        if g.ndim == 1:
            g = g.reshape(-1, 1)
        if g.size <= 0:
            return np.zeros(int(n_rows), dtype=float)
        return np.sum(np.maximum(g, 0.0), axis=1).reshape(-1)

    def evaluate_raw_cv(self, x_values: np.ndarray) -> np.ndarray:
        x = np.asarray(x_values, dtype=float)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        _, g = self.base_problem.evaluate(
            x,
            return_values_of=["F", "G"],
        )
        return self._raw_cv_from_g(g, n_rows=x.shape[0])

    def _evaluate(self, X, out, *args, **kwargs):
        x = np.asarray(X, dtype=float)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        f_raw, g_raw = self.base_problem.evaluate(
            x,
            return_values_of=["F", "G"],
        )
        f = np.asarray(f_raw, dtype=float)
        if f.ndim == 1:
            f = f.reshape(-1, self.n_obj)
        cv = self._raw_cv_from_g(g_raw, n_rows=f.shape[0])
        out["F"] = f + self.penalty_scale * cv.reshape(-1, 1)
        out["raw_cv"] = cv.reshape(-1, 1)


@dataclass
class PymooExecutionResult:
    success: bool
    message: str
    traceback_text: str = ""
    n_gen_completed: int = 0
    best_cv_curve: List[float] = field(default_factory=list)
    best_feasible_objective_curve: List[float] = field(default_factory=list)
    aocc_cv: float = 0.0
    aocc_objective: float = 0.0
    pareto_X: Optional[np.ndarray] = None
    pareto_F: Optional[np.ndarray] = None
    pareto_CV: Optional[np.ndarray] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class PymooNSGA2Runner:
    """
    Unified pymoo evolutionary runner with feasibility-first diagnostics.

    Note:
      By default this runner executes NSGA-II to preserve historical behavior.
      Set `algorithm` to `nsga3` or `moead` to enable additional MOEA solvers.
    """

    def __init__(
        self,
        pop_size: int = 96,
        n_generations: int = 80,
        seed: int = 42,
        repair: Optional[Any] = None,
        verbose: bool = False,
        return_least_infeasible: bool = True,
        initial_population: Optional[np.ndarray] = None,
        operator_bias: Optional[Dict[str, Any]] = None,
        algorithm: str = "nsga2",
        nsga3_ref_dirs_partitions: int = 0,
        moead_n_neighbors: int = 20,
        moead_prob_neighbor_mating: float = 0.9,
        moead_constraint_penalty: float = 1000.0,
    ) -> None:
        self.pop_size = int(pop_size)
        self.n_generations = int(n_generations)
        self.seed = int(seed)
        self.repair = repair
        self.verbose = bool(verbose)
        self.return_least_infeasible = bool(return_least_infeasible)
        self.initial_population = (
            np.asarray(initial_population, dtype=float) if initial_population is not None else None
        )
        self.operator_bias = dict(operator_bias or {})
        self.algorithm = str(algorithm or "nsga2").strip().lower()
        self.nsga3_ref_dirs_partitions = int(nsga3_ref_dirs_partitions)
        self.moead_n_neighbors = int(moead_n_neighbors)
        self.moead_prob_neighbor_mating = float(moead_prob_neighbor_mating)
        self.moead_constraint_penalty = float(moead_constraint_penalty)

    @staticmethod
    def _clip_float(
        value: Any,
        *,
        default: float,
        low: float,
        high: float,
    ) -> float:
        try:
            parsed = float(value)
        except Exception:
            return float(default)
        if not np.isfinite(parsed):
            return float(default)
        return float(min(max(parsed, low), high))

    @staticmethod
    def _clip_int(
        value: Any,
        *,
        default: int,
        low: int,
        high: int,
    ) -> int:
        try:
            parsed = int(value)
        except Exception:
            return int(default)
        return int(min(max(parsed, low), high))

    def _resolve_operator_bias(self) -> Dict[str, Any]:
        requested = dict(self.operator_bias or {})
        enabled = bool(requested.get("enabled", bool(requested)))
        if not enabled:
            return {
                "enabled": False,
                "strategy": "",
                "source": "",
                "program_id": "",
                "action_sequence": [],
                "sampling_sigma_ratio": 0.0,
                "sampling_jitter_count": 0,
                "crossover_prob": None,
                "crossover_eta": None,
                "mutation_prob": None,
                "mutation_eta": None,
                "repair_push_ratio": None,
                "repair_cg_nudge_ratio": None,
                "repair_max_passes": None,
            }

        applied: Dict[str, Any] = {
            "enabled": True,
            "strategy": str(requested.get("strategy", "operator_program")),
            "source": str(requested.get("source", "")),
            "program_id": str(requested.get("program_id", "")),
            "action_sequence": list(requested.get("action_sequence", []) or []),
            "sampling_sigma_ratio": self._clip_float(
                requested.get("sampling_sigma_ratio", 0.02),
                default=0.02,
                low=0.0,
                high=0.25,
            ),
            "sampling_jitter_count": self._clip_int(
                requested.get("sampling_jitter_count", 4),
                default=4,
                low=0,
                high=max(self.pop_size - 1, 0),
            ),
            "crossover_prob": self._clip_float(
                requested.get("crossover_prob", 0.9),
                default=0.9,
                low=0.5,
                high=1.0,
            ),
            "crossover_eta": self._clip_float(
                requested.get("crossover_eta", 15.0),
                default=15.0,
                low=2.0,
                high=80.0,
            ),
            "mutation_eta": self._clip_float(
                requested.get("mutation_eta", 20.0),
                default=20.0,
                low=2.0,
                high=80.0,
            ),
            "repair_push_ratio": None,
            "repair_cg_nudge_ratio": None,
            "repair_max_passes": None,
        }

        mutation_prob_raw = requested.get("mutation_prob", None)
        if mutation_prob_raw is None:
            applied["mutation_prob"] = None
        else:
            applied["mutation_prob"] = self._clip_float(
                mutation_prob_raw,
                default=0.25,
                low=1e-6,
                high=1.0,
            )

        if requested.get("repair_push_ratio", None) is not None:
            applied["repair_push_ratio"] = self._clip_float(
                requested.get("repair_push_ratio"),
                default=0.55,
                low=0.1,
                high=2.0,
            )
        if requested.get("repair_cg_nudge_ratio", None) is not None:
            applied["repair_cg_nudge_ratio"] = self._clip_float(
                requested.get("repair_cg_nudge_ratio"),
                default=0.9,
                low=0.0,
                high=3.0,
            )
        if requested.get("repair_max_passes", None) is not None:
            applied["repair_max_passes"] = self._clip_int(
                requested.get("repair_max_passes"),
                default=2,
                low=1,
                high=8,
            )
        return applied

    @staticmethod
    def _normalize_algorithm_name(value: Any) -> str:
        normalized = str(value or "nsga2").strip().lower()
        if normalized not in _SUPPORTED_ALGORITHMS:
            return "nsga2"
        return normalized

    def _resolve_algorithm(self, problem) -> Tuple[str, str, str]:
        requested = str(self.algorithm or "nsga2").strip().lower()
        used = self._normalize_algorithm_name(requested)
        reasons: List[str] = []

        if requested not in _SUPPORTED_ALGORITHMS:
            reasons.append(f"unknown_algorithm:{requested}")

        n_obj = int(max(int(getattr(problem, "n_obj", 1) or 1), 1))
        if used in {"nsga3", "moead"} and n_obj < 2:
            reasons.append(f"n_obj_{n_obj}_unsupported_for_{used}")
            used = "nsga2"

        if used == "nsga3" and NSGA3 is None:
            reasons.append("nsga3_unavailable")
            used = "nsga2"
        if used == "moead" and MOEAD is None:
            reasons.append("moead_unavailable")
            used = "nsga2"
        if used == "nsga2" and NSGA2 is None:
            raise ImportError("NSGA2 is unavailable in current pymoo installation.")

        return requested, used, ";".join(reasons)

    def _build_reference_directions(
        self,
        *,
        n_obj: int,
        target_count: int,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        n_obj = max(int(n_obj), 2)
        target_count = max(int(target_count), 2)

        if n_obj == 2:
            t = np.linspace(0.0, 1.0, num=target_count, dtype=float)
            dirs = np.column_stack([t, 1.0 - t])
            return dirs, {
                "ref_dirs_method": "uniform_line",
                "ref_dirs_count": int(dirs.shape[0]),
                "ref_dirs_partitions": None,
            }

        partitions = int(self.nsga3_ref_dirs_partitions)
        if partitions <= 0:
            partitions = 1
            while partitions < 128:
                count = math.comb(partitions + n_obj - 1, n_obj - 1)
                if count >= target_count:
                    break
                partitions += 1

        dirs = np.empty((0, n_obj), dtype=float)
        if get_reference_directions is not None:
            try:
                dirs = np.asarray(
                    get_reference_directions(
                        "das-dennis",
                        n_obj,
                        n_partitions=partitions,
                    ),
                    dtype=float,
                )
            except Exception:
                dirs = np.empty((0, n_obj), dtype=float)

        if dirs.size <= 0 or dirs.ndim != 2 or dirs.shape[1] != n_obj:
            rng = np.random.default_rng(self.seed + n_obj + target_count)
            raw = rng.random((target_count, n_obj), dtype=float)
            denom = np.sum(raw, axis=1, keepdims=True)
            denom = np.where(denom <= 1e-12, 1.0, denom)
            dirs = raw / denom
            return dirs, {
                "ref_dirs_method": "random_simplex_fallback",
                "ref_dirs_count": int(dirs.shape[0]),
                "ref_dirs_partitions": None,
            }

        if dirs.shape[0] > target_count:
            picked = np.linspace(0, dirs.shape[0] - 1, num=target_count, dtype=int)
            dirs = dirs[picked]
        elif dirs.shape[0] < target_count:
            repeat = int(math.ceil(float(target_count) / float(max(dirs.shape[0], 1))))
            dirs = np.vstack([dirs for _ in range(max(repeat, 1))])[:target_count]

        return np.asarray(dirs, dtype=float), {
            "ref_dirs_method": "das-dennis",
            "ref_dirs_count": int(dirs.shape[0]),
            "ref_dirs_partitions": int(partitions),
        }

    def run(self, problem) -> PymooExecutionResult:
        _require_pymoo()

        tracker = _ConvergenceTracker()
        sampling = None
        injected_count = 0
        bias_applied = self._resolve_operator_bias()
        repair_overrides: Dict[str, Any] = {}
        if self.initial_population is not None and self.initial_population.size > 0:
            if (
                bool(bias_applied.get("enabled", False)) and
                float(bias_applied.get("sampling_sigma_ratio", 0.0) or 0.0) > 0.0 and
                int(bias_applied.get("sampling_jitter_count", 0) or 0) > 0
            ):
                sampling = _BiasedSeededFloatSampling(
                    self.initial_population,
                    sigma_ratio=float(bias_applied.get("sampling_sigma_ratio", 0.0)),
                    jitter_count=int(bias_applied.get("sampling_jitter_count", 0)),
                    rng_seed=self.seed,
                )
            else:
                sampling = _SeededFloatSampling(self.initial_population)
            injected_count = int(
                min(self.pop_size, np.atleast_2d(self.initial_population).shape[0])
            )

        if self.repair is not None and bool(bias_applied.get("enabled", False)):
            if (
                bias_applied.get("repair_push_ratio") is not None and
                hasattr(self.repair, "push_ratio")
            ):
                self.repair.push_ratio = float(bias_applied["repair_push_ratio"])
                repair_overrides["push_ratio"] = float(self.repair.push_ratio)
            if (
                bias_applied.get("repair_cg_nudge_ratio") is not None and
                hasattr(self.repair, "cg_nudge_ratio")
            ):
                self.repair.cg_nudge_ratio = float(bias_applied["repair_cg_nudge_ratio"])
                repair_overrides["cg_nudge_ratio"] = float(self.repair.cg_nudge_ratio)
            if (
                bias_applied.get("repair_max_passes") is not None and
                hasattr(self.repair, "max_passes")
            ):
                self.repair.max_passes = int(bias_applied["repair_max_passes"])
                repair_overrides["max_passes"] = int(self.repair.max_passes)

        algorithm_kwargs: Dict[str, Any] = {
            "pop_size": self.pop_size,
            "eliminate_duplicates": True,
        }
        if sampling is not None:
            algorithm_kwargs["sampling"] = sampling
        if self.repair is not None:
            algorithm_kwargs["repair"] = self.repair

        operator_config_applied: Dict[str, Any] = {}
        if (
            bool(bias_applied.get("enabled", False)) and
            SBX is not None and
            PM is not None
        ):
            crossover_prob = float(bias_applied.get("crossover_prob", 0.9))
            crossover_eta = float(bias_applied.get("crossover_eta", 15.0))
            mutation_eta = float(bias_applied.get("mutation_eta", 20.0))
            mutation_prob = bias_applied.get("mutation_prob", None)

            algorithm_kwargs["crossover"] = SBX(prob=crossover_prob, eta=crossover_eta)
            mutation_kwargs: Dict[str, Any] = {"eta": mutation_eta}
            if mutation_prob is not None:
                mutation_kwargs["prob"] = float(mutation_prob)
            algorithm_kwargs["mutation"] = PM(**mutation_kwargs)
            operator_config_applied = {
                "crossover_prob": crossover_prob,
                "crossover_eta": crossover_eta,
                "mutation_prob": (
                    float(mutation_prob) if mutation_prob is not None else None
                ),
                "mutation_eta": mutation_eta,
            }

        requested_algo, used_algo, fallback_reason = self._resolve_algorithm(problem)
        requested_label = _ALGORITHM_LABELS.get(
            self._normalize_algorithm_name(requested_algo),
            "NSGA2",
        )
        used_label = _ALGORITHM_LABELS.get(used_algo, "NSGA2")

        algorithm_meta: Dict[str, Any] = {}
        problem_for_minimize = problem
        moead_constraint_adapter: Optional[_ConstraintPenaltyProblem] = None
        if used_algo == "nsga3":
            ref_dirs, ref_meta = self._build_reference_directions(
                n_obj=int(getattr(problem, "n_obj", 2)),
                target_count=self.pop_size,
            )
            algorithm_meta.update(ref_meta)
            nsga3_kwargs = dict(algorithm_kwargs)
            nsga3_kwargs["ref_dirs"] = ref_dirs
            nsga3_kwargs["pop_size"] = int(ref_dirs.shape[0])
            algorithm = NSGA3(**nsga3_kwargs)
        elif used_algo == "moead":
            ref_dirs, ref_meta = self._build_reference_directions(
                n_obj=int(getattr(problem, "n_obj", 2)),
                target_count=self.pop_size,
            )
            n_ieq = int(getattr(problem, "n_ieq_constr", 0) or 0)
            if n_ieq > 0:
                moead_constraint_adapter = _ConstraintPenaltyProblem(
                    problem,
                    penalty_scale=self.moead_constraint_penalty,
                )
                problem_for_minimize = moead_constraint_adapter
                algorithm_meta.update({
                    "constraint_handling": "penalty_objective",
                    "moead_constraint_penalty": float(max(self.moead_constraint_penalty, 1.0)),
                    "n_ieq_constr_original": int(n_ieq),
                })
            algorithm_meta.update(ref_meta)
            n_dirs = max(int(ref_dirs.shape[0]), 1)
            low = 1 if n_dirs <= 2 else 2
            high = max(n_dirs - 1, low)
            n_neighbors = self._clip_int(
                self.moead_n_neighbors,
                default=min(20, high),
                low=low,
                high=high,
            )
            prob_neighbor_mating = self._clip_float(
                self.moead_prob_neighbor_mating,
                default=0.9,
                low=0.0,
                high=1.0,
            )
            moead_kwargs = dict(algorithm_kwargs)
            moead_kwargs.pop("eliminate_duplicates", None)
            moead_kwargs.pop("pop_size", None)
            moead_kwargs["ref_dirs"] = ref_dirs
            moead_kwargs["n_neighbors"] = n_neighbors
            moead_kwargs["prob_neighbor_mating"] = prob_neighbor_mating
            algorithm_meta.update({
                "moead_n_neighbors": int(n_neighbors),
                "moead_prob_neighbor_mating": float(prob_neighbor_mating),
            })
            algorithm = MOEAD(**moead_kwargs)
        else:
            algorithm = NSGA2(**algorithm_kwargs)

        try:
            result = minimize(
                problem=problem_for_minimize,
                algorithm=algorithm,
                termination=get_termination("n_gen", self.n_generations),
                seed=self.seed,
                save_history=True,
                verbose=self.verbose,
                callback=tracker,
                return_least_infeasible=self.return_least_infeasible,
            )

            pareto_x = (
                np.asarray(result.X, dtype=float)
                if result.X is not None else
                np.empty((0, int(getattr(problem, "n_var", 0) or 0)))
            )
            pareto_f = (
                np.asarray(result.F, dtype=float)
                if result.F is not None else
                np.empty((0, int(getattr(problem, "n_obj", 1) or 1)))
            )
            if moead_constraint_adapter is not None and pareto_x.size > 0:
                pareto_cv = moead_constraint_adapter.evaluate_raw_cv(pareto_x)
            elif getattr(result, "CV", None) is not None:
                pareto_cv = np.asarray(result.CV, dtype=float).reshape(-1)
            else:
                pareto_cv = np.full(pareto_x.shape[0], np.nan, dtype=float)
            if pareto_x.ndim == 2 and pareto_cv.size != pareto_x.shape[0]:
                pareto_cv = np.full(pareto_x.shape[0], np.nan, dtype=float)
            aocc_cv = calculate_aocc(tracker.best_cv, lower_is_better=True)
            aocc_obj = calculate_aocc(tracker.best_feasible_sum_f, lower_is_better=True)

            return PymooExecutionResult(
                success=True,
                message=f"{used_label} completed",
                n_gen_completed=len(tracker.best_cv),
                best_cv_curve=[float(v) for v in tracker.best_cv],
                best_feasible_objective_curve=[float(v) for v in tracker.best_feasible_sum_f],
                aocc_cv=float(aocc_cv),
                aocc_objective=float(aocc_obj),
                pareto_X=pareto_x,
                pareto_F=pareto_f,
                pareto_CV=pareto_cv,
                metadata={
                    "constraint_strategy": "feasibility_first",
                    "algorithm_requested": requested_label,
                    "algorithm": used_label,
                    "algorithm_fallback_reason": fallback_reason,
                    "pop_size": self.pop_size,
                    "n_generations": self.n_generations,
                    "seed": self.seed,
                    "return_least_infeasible": self.return_least_infeasible,
                    "initial_population_injected": injected_count,
                    "algorithm_parameters": algorithm_meta,
                    "operator_bias_requested": dict(self.operator_bias or {}),
                    "operator_bias_applied": bias_applied,
                    "operator_config_applied": operator_config_applied,
                    "repair_overrides": repair_overrides,
                    "generation_records": list(tracker.generation_records),
                },
            )
        except (MemoryError, OverflowError, FloatingPointError, RuntimeError, Exception) as exc:
            return PymooExecutionResult(
                success=False,
                message=f"{used_label} failed: {exc}",
                traceback_text=traceback.format_exc(),
                n_gen_completed=len(tracker.best_cv),
                best_cv_curve=[float(v) for v in tracker.best_cv],
                best_feasible_objective_curve=[float(v) for v in tracker.best_feasible_sum_f],
                aocc_cv=float(calculate_aocc(tracker.best_cv, lower_is_better=True)),
                aocc_objective=float(
                    calculate_aocc(tracker.best_feasible_sum_f, lower_is_better=True)
                ),
                metadata={
                    "constraint_strategy": "feasibility_first",
                    "algorithm_requested": requested_label,
                    "algorithm": used_label,
                    "algorithm_fallback_reason": fallback_reason,
                    "algorithm_parameters": algorithm_meta,
                    "operator_bias_requested": dict(self.operator_bias or {}),
                    "operator_bias_applied": bias_applied,
                    "operator_config_applied": operator_config_applied,
                    "repair_overrides": repair_overrides,
                    "generation_records": list(tracker.generation_records),
                },
            )


class PymooNSGA3Runner(PymooNSGA2Runner):
    """Compatibility wrapper for NSGA-III execution."""

    def __init__(self, *args, **kwargs):
        kwargs["algorithm"] = "nsga3"
        super().__init__(*args, **kwargs)


class PymooMOEADRunner(PymooNSGA2Runner):
    """Compatibility wrapper for MOEA/D execution."""

    def __init__(self, *args, **kwargs):
        kwargs["algorithm"] = "moead"
        super().__init__(*args, **kwargs)
