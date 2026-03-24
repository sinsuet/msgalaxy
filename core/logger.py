"""
Minimal logging and report helpers for the rebuilt scenario runtime.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.artifact_index import (
    ARTIFACT_LAYOUT_VERSION,
    build_artifact_index_payload,
    write_artifact_index,
)
from core.llm_interaction_store import LLMInteractionStore
from core.mode_contract import (
    normalize_runtime_mode,
    resolve_execution_mode,
    resolve_lifecycle_state,
)
from core.path_policy import serialize_run_path


GLOBAL_FILE_LOGGER_NAMES = {
    "api_server",
    "websocket_client",
}


def _should_persist_global_log(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("experiment_"):
        return False
    return normalized in GLOBAL_FILE_LOGGER_NAMES


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json_value(v) for v in value]
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _sanitize_json_value(tolist_fn())
        except Exception:
            pass
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _sanitize_json_value(item_fn())
        except Exception:
            return value
    return value


def _json_dump_safe(payload: Any, fp) -> None:
    json.dump(
        _sanitize_json_value(payload),
        fp,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_dumps_safe(payload: Any) -> str:
    return json.dumps(
        _sanitize_json_value(payload),
        ensure_ascii=False,
        allow_nan=False,
    )


def discover_active_llm_buckets(run_dir: str) -> List[str]:
    run_path = Path(run_dir)
    buckets: List[str] = []
    for candidate in (
        run_path / "artifacts" / "mass" / "llm_interactions",
        run_path / "artifacts" / "legacy" / "llm_interactions",
    ):
        if candidate.is_dir():
            buckets.append(candidate.parent.name if candidate.parent.name != "artifacts" else "legacy")
    return sorted(set(buckets))


def write_markdown_report(
    run_dir: str,
    summary: Dict[str, Any],
    *,
    active_buckets: Optional[List[str]] = None,
) -> None:
    run_path = Path(run_dir)
    report_path = run_path / "report.md"
    artifacts = dict(summary.get("artifacts", {}) or {})
    field_exports = dict(summary.get("field_exports", {}) or {})

    lines = [
        "# Scenario Run Report",
        "",
        f"- status: `{str(summary.get('status', '') or 'UNKNOWN')}`",
        f"- run_mode: `{str(summary.get('run_mode') or summary.get('stack') or '')}`",
        f"- execution_mode: `{str(summary.get('execution_mode', '') or '')}`",
        f"- lifecycle_state: `{str(summary.get('lifecycle_state', '') or '')}`",
        f"- scenario_id: `{str(summary.get('scenario_id', '') or '')}`",
        f"- archetype_id: `{str(summary.get('archetype_id', '') or '')}`",
        f"- requested_profile: `{str(summary.get('requested_physics_profile', '') or '')}`",
        f"- effective_profile: `{str(summary.get('effective_physics_profile', '') or '')}`",
        "",
        "## Metrics",
    ]

    for key, value in sorted(dict(summary.get("final_metrics", {}) or {}).items()):
        lines.append(f"- {key}: `{value}`")

    proxy_metrics = dict(summary.get("proxy_metrics", {}) or {})
    if proxy_metrics:
        lines.extend(["", "## Proxy Metrics"])
        for key, value in sorted(proxy_metrics.items()):
            lines.append(f"- {key}: `{value}`")

    if artifacts:
        lines.extend(["", "## Artifacts"])
        for key, value in sorted(artifacts.items()):
            if value:
                lines.append(f"- {key}: `{value}`")

    if field_exports:
        lines.extend(["", "## Field Exports"])
        for key, payload in sorted(field_exports.items()):
            figure_path = str(dict(payload or {}).get("figure_path", "") or "")
            grid_path = str(dict(payload or {}).get("grid_path", "") or "")
            if figure_path or grid_path:
                lines.append(f"- {key}: figure=`{figure_path or 'n/a'}`, grid=`{grid_path or 'n/a'}`")

    buckets = [str(item).strip() for item in list(active_buckets or []) if str(item).strip()]
    if buckets:
        lines.extend(["", "## LLM Buckets", f"- active: `{', '.join(buckets)}`"])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ExperimentLogger:
    """Small compatibility logger used by remaining utility code."""

    def __init__(
        self,
        *,
        run_mode: str = "mass",
        run_dir: str = "",
        run_id: str = "",
        run_label: str = "",
        base_dir: str = "experiments",
        **_: Any,
    ) -> None:
        self.run_mode = normalize_runtime_mode(run_mode, default="mass")
        self.execution_mode = resolve_execution_mode(self.run_mode)
        self.lifecycle_state = resolve_lifecycle_state(self.run_mode)
        self.run_started_at = datetime.now().isoformat()
        self.run_label = str(run_label or "").strip()
        self.run_id = str(run_id or f"{self.run_mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}").strip()
        self.run_dir = str(Path(run_dir).resolve()) if str(run_dir or "").strip() else self._build_run_dir(base_dir)
        self.exp_dir = self.run_dir
        Path(self.run_dir).mkdir(parents=True, exist_ok=True)
        self.logger = get_logger(f"experiment_{self.run_id}", persist_global=False)
        self.llm_store = LLMInteractionStore(self.run_dir, run_mode=self.run_mode)
        self.artifact_layout_version = int(ARTIFACT_LAYOUT_VERSION)
        self.artifact_index = build_artifact_index_payload(
            run_dir=self.run_dir,
            run_mode=self.run_mode,
            execution_mode=self.execution_mode,
            lifecycle_state=self.lifecycle_state,
        )
        write_artifact_index(self.run_dir, self.artifact_index)

    def _build_run_dir(self, base_dir: str) -> str:
        root = Path(str(base_dir or "experiments")).resolve()
        date_dir = root / datetime.now().strftime("%Y%m%d")
        run_name = f"{datetime.now().strftime('%H%M%S')}_{self.run_mode}_{self.run_label or 'legacy'}"
        return str((date_dir / run_name).resolve())

    def serialize_artifact_path(self, value: Any) -> str:
        raw = Path(str(value or ""))
        if not raw.is_absolute():
            raw = (Path(self.run_dir) / raw).resolve()
        return serialize_run_path(self.run_dir, raw)

    def get_step_files_dir(self) -> str:
        path = Path(self.run_dir) / "step"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def log_llm_interaction(
        self,
        *,
        iteration: int,
        role: Optional[str],
        request: Optional[Dict[str, Any]],
        response: Optional[Dict[str, Any]],
        mode: Optional[str] = None,
    ) -> str:
        return self.llm_store.write(
            iteration=iteration,
            role=role,
            request=request,
            response=response,
            mode=mode,
        )

    def save_summary(self, summary: Dict[str, Any]) -> None:
        payload = dict(summary or {})
        payload.setdefault("run_mode", self.run_mode)
        payload.setdefault("execution_mode", self.execution_mode)
        payload.setdefault("lifecycle_state", self.lifecycle_state)
        summary_path = Path(self.run_dir) / "summary.json"
        with summary_path.open("w", encoding="utf-8") as f:
            _json_dump_safe(payload, f)
        write_markdown_report(
            run_dir=self.run_dir,
            summary=payload,
            active_buckets=self.llm_store.get_active_buckets(),
        )

    def __getattr__(self, name: str):
        if name.startswith(("log_", "save_", "append_")):
            def _noop(*args: Any, **kwargs: Any) -> Any:
                return None

            return _noop
        raise AttributeError(name)


def get_logger(name: str, *, persist_global: Optional[bool] = None) -> Any:
    if persist_global is None:
        persist_global = _should_persist_global_log(name)

    logger = logging.getLogger(name)
    console_handlers = [
        handler for handler in logger.handlers
        if bool(getattr(handler, "_msgalaxy_console_handler", False))
    ]
    global_file_handlers = [
        handler for handler in logger.handlers
        if bool(getattr(handler, "_msgalaxy_global_file_handler", False))
    ]

    if not console_handlers:
        import sys

        console_handler = logging.StreamHandler(sys.stdout)
        if hasattr(console_handler.stream, "reconfigure"):
            console_handler.stream.reconfigure(encoding="utf-8")
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        console_handler._msgalaxy_console_handler = True  # type: ignore[attr-defined]
        logger.addHandler(console_handler)

    if persist_global and not global_file_handlers:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        file_handler._msgalaxy_global_file_handler = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)
    elif not persist_global and global_file_handlers:
        for handler in global_file_handlers:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    logger.setLevel(logging.DEBUG)
    return logger


def log_exception(logger, exception: Exception, context: str = ""):
    import traceback

    error_msg = f"Exception in {context}: {type(exception).__name__}: {str(exception)}"
    logger.error(error_msg)
    logger.debug(traceback.format_exc())
