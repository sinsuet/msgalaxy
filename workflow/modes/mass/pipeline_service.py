from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from workflow.scenario_runtime import ScenarioExecutionResult, ScenarioRuntime


@dataclass
class MaaSPipelineService:
    """Scenario-driven mass executor on the rebuilt catalog-first core."""

    host: Any | None = None
    config: Optional[Mapping[str, Any]] = None
    run_label: str = ""

    def __post_init__(self) -> None:
        if self.config is None and self.host is not None:
            self.config = dict(getattr(self.host, "config", {}) or {})
        elif self.config is None:
            self.config = {}

    def run_scenario(
        self,
        *,
        scenario_path: str,
    ) -> ScenarioExecutionResult:
        runtime = ScenarioRuntime(
            stack="mass",
            config=dict(self.config or {}),
            scenario_path=scenario_path,
            run_label=str(self.run_label or ""),
        )
        return runtime.execute()

    def run_pipeline(self, *args, **kwargs):
        raise RuntimeError(
            "Legacy mass pipeline has been removed. "
            "Use run/run_scenario.py --stack mass --scenario <id>."
        )
