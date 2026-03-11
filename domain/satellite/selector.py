from __future__ import annotations

from typing import Iterable, Optional

from .baseline import load_default_satellite_reference_baseline
from .contracts import SatelliteArchetype, SatelliteReferenceBaseline


def _normalize_task_type(task_type: str) -> str:
    return " ".join(str(task_type or "").strip().lower().replace("-", " ").replace("_", " ").split())


class TaskTypeArchetypeSelector:
    _RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("navigation_satellite", ("navigation", "gnss", "pnt", "beidou", "gps")),
        (
            "optical_remote_sensing_microsat",
            ("optical", "imaging", "camera", "remote sensing", "earth observation", "eo"),
        ),
        (
            "radar_or_comm_payload_microsat",
            ("radar", "sar", "communication", "comm", "relay", "antenna", "broadband"),
        ),
        (
            "cubesat_modular_bus",
            ("cubesat", "1u", "3u", "6u", "12u", "rideshare", "modular bus"),
        ),
        (
            "science_experiment_smallsat",
            ("science", "experiment", "microgravity", "spectrometer", "technology demo"),
        ),
    )

    def __init__(
        self,
        baseline: Optional[SatelliteReferenceBaseline] = None,
    ) -> None:
        self.baseline = baseline or load_default_satellite_reference_baseline()

    def select(self, task_type: str) -> SatelliteArchetype:
        normalized = _normalize_task_type(task_type)
        if not normalized:
            raise ValueError("task_type_is_required")

        for archetype_id, keywords in self._RULES:
            if self._matches_any_keyword(normalized, keywords):
                archetype = self.baseline.get_archetype(archetype_id)
                if archetype is None:
                    raise ValueError(f"baseline_missing_archetype:{archetype_id}")
                return archetype

        raise ValueError(f"unsupported_task_type:{task_type}")

    @staticmethod
    def _matches_any_keyword(normalized: str, keywords: Iterable[str]) -> bool:
        return any(str(keyword) in normalized for keyword in keywords)


def select_archetype_for_task(
    task_type: str,
    *,
    baseline: Optional[SatelliteReferenceBaseline] = None,
) -> SatelliteArchetype:
    selector = TaskTypeArchetypeSelector(baseline=baseline)
    return selector.select(task_type)
