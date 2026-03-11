from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from core.logger import get_logger
from simulation.comsol.field_registry import get_field_spec

logger = get_logger(__name__)


class ComsolDatasetEvaluatorMixin:
    """Dataset probing and expression evaluation fallbacks."""

    def _ensure_dataset_probe_cache(self) -> set[str]:
        """
        Keep dataset probe cache scoped to current COMSOL model instance.

        `_dataset_probe_failed` stores dataset tags already confirmed invalid for
        evaluate(..., dataset=tag) within the current model to suppress repeated probes.
        """
        model_identity = id(self.model) if self.model is not None else None
        cached_identity = getattr(self, "_dataset_cache_model_identity", None)
        if cached_identity != model_identity:
            setattr(self, "_dataset_cache_model_identity", model_identity)
            setattr(self, "_dataset_probe_failed", set())
            setattr(self, "_dataset_tags_cache", [])

        failed = getattr(self, "_dataset_probe_failed", None)
        if not isinstance(failed, set):
            failed = set()
            setattr(self, "_dataset_probe_failed", failed)
        return failed

    @staticmethod
    def _is_dataset_missing_error(exc: Exception) -> bool:
        message = str(exc or "").strip().lower()
        if "dataset" not in message:
            return False
        missing_tokens = (
            "does not exist",
            "not exist",
            "unknown dataset",
            "could not be found",
            "cannot be found",
            "not found",
        )
        return any(token in message for token in missing_tokens)

    def _dataset_tag_exists(self, tag: str) -> bool:
        """
        Check whether a dataset tag is resolvable in current COMSOL model.

        Prefer direct API checks (`hasTag` / `result().dataset(tag)`) to avoid
        triggering mph evaluate errors for stale/non-existent tags.
        """
        if self.model is None:
            return False
        normalized = str(tag or "").strip()
        if not normalized:
            return False
        try:
            result_node = self.model.java.result()
            dataset_list = result_node.dataset()
        except Exception:
            return False

        has_tag = getattr(dataset_list, "hasTag", None)
        if callable(has_tag):
            try:
                return bool(has_tag(normalized))
            except Exception:
                pass

        if callable(dataset_list):
            try:
                dataset_list(normalized)
                return True
            except Exception:
                pass

        try:
            result_node.dataset(normalized)
            return True
        except Exception:
            pass

        tags_getter = getattr(dataset_list, "tags", None)
        if callable(tags_getter):
            try:
                tags = {str(item or "").strip() for item in list(tags_getter())}
                return normalized in tags
            except Exception:
                pass

        return False

    def _extract_numeric_values(self, raw: Any) -> list[float]:
        if raw is None:
            return []
        try:
            arr = np.asarray(raw)
        except Exception:
            try:
                arr = np.asarray(list(raw))
            except Exception:
                return []
        if arr.size == 0:
            return []
        if np.iscomplexobj(arr):
            arr = np.real(arr.astype(np.complex128))
        try:
            arr = np.asarray(arr, dtype=float).reshape(-1)
        except Exception:
            return []
        if arr.size == 0:
            return []
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return []
        return [float(item) for item in arr.tolist()]

    def _extract_numeric_magnitudes(self, raw: Any) -> list[float]:
        """Return magnitudes when COMSOL expression yields complex values."""
        if raw is None:
            return []
        try:
            arr = np.asarray(raw)
        except Exception:
            try:
                arr = np.asarray(list(raw))
            except Exception:
                return []
        if arr.size == 0:
            return []
        if np.iscomplexobj(arr):
            arr = np.abs(arr.astype(np.complex128))
        try:
            arr = np.asarray(arr, dtype=float).reshape(-1)
        except Exception:
            return []
        if arr.size == 0:
            return []
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return []
        return [float(item) for item in arr.tolist()]

    def _collect_dataset_tags(self) -> list[str]:
        """Collect COMSOL dataset tags as normalized strings."""
        failed = self._ensure_dataset_probe_cache()
        if self.model is None:
            return []
        try:
            raw_tags = list(self.model.java.result().dataset().tags())
        except Exception:
            raw_tags = []

        tags: list[str] = []
        seen: set[str] = set()
        for raw in raw_tags:
            tag = str(raw or "").strip()
            if not tag or tag in seen:
                continue
            if not self._dataset_tag_exists(tag):
                failed.add(tag)
                continue
            seen.add(tag)
            tags.append(tag)
        failed.difference_update(set(tags))
        setattr(self, "_dataset_tags_cache", list(tags))
        return tags

    def _evaluate_dataset_derived_value(
        self,
        *,
        expression: str,
        dataset: str,
        unit: Optional[str] = None,
        reducer: str = "max",
    ) -> Optional[float]:
        """
        Evaluate an explicit COMSOL solution dataset through Derived Values.

        This follows COMSOL's result-tree pattern (`result().numerical()`
        with `data=<dataset>`), which is more reliable than
        `mph.Model.evaluate(..., dataset=...)` for explicit solution datasets.
        """
        if self.model is None:
            return None

        dataset_tag = str(dataset or "").strip()
        if not dataset_tag:
            return None

        numerical_type = {
            "max": "MaxVolume",
            "min": "MinVolume",
        }.get(str(reducer or "").strip().lower())
        if not numerical_type:
            return None

        numeric_tag = f"drv_{abs(hash((dataset_tag, expression, reducer, datetime.now().timestamp()))) % 10_000_000}"
        node = None
        numerical_root = None
        try:
            numerical_root = self.model.java.result().numerical()
            node = numerical_root.create(numeric_tag, numerical_type)
            node.set("data", dataset_tag)
            try:
                node.selection().all()
            except Exception:
                pass
            node.setIndex("expr", str(expression), 0)
            if unit:
                try:
                    node.setIndex("unit", str(unit), 0)
                except Exception:
                    pass

            values = self._extract_numeric_values(node.getReal())
            if not values:
                return None
            if reducer == "min":
                return float(min(values))
            return float(max(values))
        except Exception:
            return None
        finally:
            if node is not None:
                try:
                    numerical_root.remove(numeric_tag)
                except Exception:
                    pass

    def _probe_modal_dataset_frequency(self, dataset: str) -> Optional[float]:
        if self.model is None:
            return None

        dataset_tag = str(dataset or "").strip()
        if not dataset_tag:
            return None

        result_node = self.model.java.result()
        eg_tag = f"eg_modal_tmp_{abs(hash((dataset_tag, datetime.now().timestamp()))) % 10_000_000}"
        try:
            eg = result_node.evaluationGroup().create(eg_tag, "EvaluationGroup")
            eg.set("data", dataset_tag)
            eg.create("gev1", "EvalGlobal")
            eg.feature("gev1").setIndex("expr", "1", 0)
            try:
                eg.setIndex("looplevelinput", "manual", 1)
                eg.setIndex("looplevel", "1 2 3 4 5 6", 1)
            except Exception:
                pass
            eg.run()
            values = self._extract_numeric_values(eg.getReal())
            positives = [
                float(v)
                for v in values
                if float(v) > 1e-9 and abs(float(v) - 1.0) > 1e-9
            ]
            if positives:
                return float(min(positives))
        except Exception:
            return None
        finally:
            try:
                result_node.evaluationGroup().remove(eg_tag)
            except Exception:
                pass
        return None

    def _supports_explicit_dataset_derived_values(self) -> bool:
        if self.model is None:
            return False
        try:
            result_node = self.model.java.result()
        except Exception:
            return False
        numerical = getattr(result_node, "numerical", None)
        return callable(numerical)

    def _select_modal_result_dataset(
        self,
        *,
        dataset_candidates: Optional[list[Optional[str]]] = None,
    ) -> Optional[str]:
        if self.model is None:
            return None

        model_identity = id(self.model)
        cached_identity = getattr(self, "_modal_dataset_cache_model_identity", None)
        cached_dataset = getattr(self, "_modal_dataset_cache_value", None)
        if cached_identity == model_identity and cached_dataset:
            return str(cached_dataset)

        candidates = [str(tag) for tag in list(dataset_candidates or self._build_dataset_candidates(prefer_modal=True)) if tag]
        for dataset_tag in candidates:
            freq = self._probe_modal_dataset_frequency(dataset_tag)
            if freq is None:
                continue
            setattr(self, "_modal_dataset_cache_model_identity", model_identity)
            setattr(self, "_modal_dataset_cache_value", dataset_tag)
            return dataset_tag
        return None

    def _select_structural_stationary_dataset(
        self,
        *,
        dataset_candidates: Optional[list[Optional[str]]] = None,
    ) -> Optional[str]:
        if self.model is None:
            return None

        model_identity = id(self.model)
        cached_identity = getattr(self, "_structural_dataset_cache_model_identity", None)
        cached_dataset = getattr(self, "_structural_dataset_cache_value", None)
        if cached_identity == model_identity and cached_dataset:
            return str(cached_dataset)

        candidates = [str(tag) for tag in list(dataset_candidates or self._build_dataset_candidates(prefer_modal=False)) if tag]
        if not candidates:
            return None

        modal_dataset = self._select_modal_result_dataset(dataset_candidates=candidates)
        best_dataset: Optional[str] = None
        best_stress = -1.0
        best_disp = -1.0
        stress_field = get_field_spec("stress")
        displacement_field = get_field_spec("displacement")

        for dataset_tag in candidates:
            if dataset_tag == modal_dataset:
                continue

            stress = self._evaluate_expression_candidates(
                expressions=list(stress_field.expression_candidates),
                unit=stress_field.unit,
                datasets=[dataset_tag],
                reducer="max",
            )
            disp = self._evaluate_expression_candidates(
                expressions=list(displacement_field.expression_candidates),
                unit=displacement_field.unit,
                datasets=[dataset_tag],
                reducer="max",
            )
            stress_value = float(stress or 0.0)
            disp_value = float(disp or 0.0)
            if stress_value <= 0.0 and disp_value <= 0.0:
                continue
            if stress_value > best_stress or (
                abs(stress_value - best_stress) <= 1e-18 and disp_value > best_disp
            ):
                best_dataset = dataset_tag
                best_stress = stress_value
                best_disp = disp_value

        if best_dataset:
            setattr(self, "_structural_dataset_cache_model_identity", model_identity)
            setattr(self, "_structural_dataset_cache_value", best_dataset)
        return best_dataset

    @staticmethod
    def _dataset_priority(tag: str, *, prefer_modal: bool = False) -> int:
        name = str(tag or "").strip().lower()
        if not name:
            return -100

        score = 0
        if "dset" in name:
            score += 2
        if "sol" in name:
            score += 1
        if "struct" in name or "solid" in name or "stat" in name:
            score += 1

        modal_tokens = ("modal", "eig", "freq", "mode")
        has_modal_token = any(token in name for token in modal_tokens)
        if has_modal_token:
            score += 6 if prefer_modal else -1
        elif prefer_modal:
            score -= 2

        return int(score)

    def _build_dataset_candidates(
        self,
        *,
        prefer_modal: bool = False,
        max_candidates: int = 12,
    ) -> list[Optional[str]]:
        """
        Build robust dataset candidates for expression evaluation.

        - prefer_modal=True: prioritize modal/eigenfrequency datasets.
        - prefer_modal=False: keep no-dataset first for stationary expressions.
        """
        failed = self._ensure_dataset_probe_cache()
        tags = [tag for tag in self._collect_dataset_tags() if tag not in failed]
        ranked = sorted(
            list(enumerate(tags)),
            key=lambda item: (
                self._dataset_priority(item[1], prefer_modal=prefer_modal),
                item[0],
            ),
            reverse=True,
        )
        ordered_tags = [tag for _, tag in ranked[: max(1, int(max_candidates))]]

        if prefer_modal:
            candidates: list[Optional[str]] = list(ordered_tags)
            candidates.append(None)
            return candidates

        candidates = [None]
        candidates.extend(ordered_tags)
        return candidates

    def _evaluate_expression_candidates(
        self,
        *,
        expressions: list[str],
        unit: Optional[str] = None,
        datasets: Optional[list[str]] = None,
        reducer: str = "max",
    ) -> Optional[float]:
        if not self.model:
            return None
        failed = self._ensure_dataset_probe_cache()
        cached_tags = [str(item).strip() for item in list(getattr(self, "_dataset_tags_cache", []) or []) if str(item).strip()]
        available_tags = set(cached_tags) if cached_tags else set(self._collect_dataset_tags())
        dataset_candidates: list[Optional[str]] = []
        seen_tags: set[str] = set()
        seen_none = False
        for item in list(datasets or [None]):
            if item is None:
                if not seen_none:
                    dataset_candidates.append(None)
                    seen_none = True
                continue
            tag = str(item).strip()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            if tag in failed:
                continue
            if available_tags and tag not in available_tags:
                failed.add(tag)
                continue
            dataset_candidates.append(tag)
        if not dataset_candidates:
            dataset_candidates = [None]

        for expr in expressions:
            for dset in dataset_candidates:
                if dset is not None and dset in failed:
                    continue
                if dset is not None and reducer in {"max", "min"}:
                    derived_value = self._evaluate_dataset_derived_value(
                        expression=str(expr),
                        dataset=str(dset),
                        unit=unit,
                        reducer=reducer,
                    )
                    if derived_value is not None:
                        return float(derived_value)
                try:
                    if dset is None:
                        raw = self.model.evaluate(expr, unit=unit) if unit else self.model.evaluate(expr)
                    else:
                        raw = (
                            self.model.evaluate(expr, unit=unit, dataset=dset)
                            if unit
                            else self.model.evaluate(expr, dataset=dset)
                        )
                except Exception as eval_error:
                    if dset is not None and self._is_dataset_missing_error(eval_error):
                        if (
                            not self._dataset_tag_exists(str(dset))
                            or not self._supports_explicit_dataset_derived_values()
                        ):
                            failed.add(str(dset))
                        continue
                    if not unit:
                        continue
                    try:
                        if dset is None:
                            raw = self.model.evaluate(expr)
                        else:
                            raw = self.model.evaluate(expr, dataset=dset)
                    except Exception as fallback_error:
                        if dset is not None and self._is_dataset_missing_error(fallback_error):
                            if (
                                not self._dataset_tag_exists(str(dset))
                                or not self._supports_explicit_dataset_derived_values()
                            ):
                                failed.add(str(dset))
                        continue

                values = self._extract_numeric_values(raw)
                if not values and reducer != "min_positive":
                    continue
                if reducer == "min":
                    if values:
                        return float(min(values))
                    continue
                if reducer == "mean":
                    if values:
                        return float(sum(values) / len(values))
                    continue
                if reducer == "first":
                    if values:
                        return float(values[0])
                    continue
                if reducer == "min_positive":
                    positives = [float(v) for v in values if float(v) > 1e-9]
                    if not positives:
                        magnitudes = self._extract_numeric_magnitudes(raw)
                        positives = [float(v) for v in magnitudes if float(v) > 1e-9]
                    if positives:
                        return float(min(positives))
                    continue
                if values:
                    return float(max(values))
        return None

    def _extract_modal_frequency_via_eval_group(
        self,
        *,
        dataset_candidates: list[Optional[str]],
    ) -> Optional[float]:
        """
        COMSOL official fallback for eigenfrequency extraction:
        EvaluationGroup + EvalGlobal.
        """
        if self.model is None:
            return None

        for dset in dataset_candidates:
            if dset is None:
                continue
            freq = self._probe_modal_dataset_frequency(str(dset))
            if freq is not None:
                logger.info(
                    "  ✓ 通过 EvaluationGroup 回退提取到模态频率: %.6f Hz (dataset=%s)",
                    float(freq),
                    str(dset),
                )
                return float(freq)
        return None

    def _extract_modal_frequency_from_solver_sequence(self) -> Optional[float]:
        """Last-resort extraction from eigenvalue solver sequence parameters."""
        if self.model is None:
            return None

        try:
            sol_tags = list(self.model.java.sol().tags())
        except Exception:
            sol_tags = []

        best: Optional[float] = None
        for tag in sol_tags:
            try:
                sol = self.model.java.sol(str(tag))
            except Exception:
                continue

            sol_type = ""
            try:
                sol_type = str(sol.getType() or "").strip().lower()
            except Exception:
                sol_type = ""
            if "eigen" not in sol_type:
                continue

            values: list[float] = []
            for getter_name in ("getParamVals", "getPVals"):
                getter = getattr(sol, getter_name, None)
                if not callable(getter):
                    continue
                try:
                    values.extend(self._extract_numeric_values(getter()))
                except Exception:
                    continue
            positives = [float(v) for v in values if float(v) > 1e-9]
            if positives:
                candidate = float(min(positives))
                if best is None or candidate < best:
                    best = candidate

        if best is not None:
            logger.info("  ✓ 通过 SolverSequence 回退提取到模态频率: %.6f", float(best))
        return best
