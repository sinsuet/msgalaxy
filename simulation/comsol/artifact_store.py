from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.protocol import DesignState, SimulationRequest, SimulationType

logger = get_logger(__name__)


class ComsolArtifactStoreMixin:
    """Model save/load helpers for COMSOL runtime artifacts."""

    def _save_mph_model(self, request: SimulationRequest, reason: str = "regular") -> str:
        """
        Save COMSOL `.mph` model for reproducibility and debugging.

        Args:
            request: simulation request carrying state + runtime folder
        """
        try:
            if not self.model:
                logger.warning("  ⚠ COMSOL 模型对象不存在，跳过保存")
                return ""

            experiment_dir = request.parameters.get("experiment_dir")
            if experiment_dir:
                save_dir = Path(experiment_dir) / "mph_models"
            else:
                save_dir = Path("experiments/runtime/comsol_models")
            save_dir.mkdir(parents=True, exist_ok=True)

            state_id = (request.design_state.state_id or "").strip()
            if state_id:
                base_name = f"model_{state_id}"
            else:
                base_name = f"model_iter_{request.design_state.iteration:03d}"

            safe_base_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("_")
            if not safe_base_name:
                safe_base_name = f"model_iter_{request.design_state.iteration:03d}"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            unique_path = save_dir / f"{safe_base_name}_{timestamp}.mph"
            retry_path = save_dir / f"{safe_base_name}_{timestamp}_retry.mph"

            logger.info(f"  保存 COMSOL .mph 模型 (reason={reason})...")
            if self._try_save_mph_path(unique_path):
                return self._record_saved_mph(unique_path=unique_path, reason=reason)

            logger.warning("  唯一路径保存失败，进行一次回退重试...")
            if self._try_save_mph_path(retry_path):
                return self._record_saved_mph(unique_path=retry_path, reason=reason)

            logger.warning("  ⚠ 保存 .mph 模型失败: 唯一路径与回退路径均失败")
            return ""
        except Exception as exc:
            logger.warning(f"  ⚠ 保存 .mph 模型失败: {exc}")
            logger.warning(f"  异常类型: {type(exc).__name__}")
            logger.warning("  仿真结果仍然有效，继续执行...")
            return ""

    def _record_saved_mph(self, *, unique_path: Path, reason: str) -> str:
        saved_path = str(unique_path)
        save_only_latest = bool(getattr(self, "save_mph_only_latest", False))

        if save_only_latest:
            self._cleanup_previous_saved_mph(exclude_path=saved_path)
            self.saved_mph_records = []

        self.last_saved_mph_path = saved_path
        self.saved_mph_records.append(
            {
                "path": saved_path,
                "reason": str(reason),
                "timestamp": datetime.now().isoformat(),
            }
        )
        if save_only_latest:
            self.saved_mph_records = self.saved_mph_records[-1:]
        else:
            self.saved_mph_records = self.saved_mph_records[-20:]
        return saved_path

    def _cleanup_previous_saved_mph(self, *, exclude_path: str) -> None:
        exclude = str(exclude_path or "").strip()
        if not exclude:
            return

        for record in list(self.saved_mph_records or []):
            candidate = str((record or {}).get("path", "") or "").strip()
            if not candidate or candidate == exclude:
                continue
            try:
                candidate_path = Path(candidate)
                if candidate_path.exists() and candidate_path.is_file():
                    candidate_path.unlink()
            except Exception as cleanup_exc:
                logger.warning("  ⚠ 清理历史 .mph 文件失败: %s", cleanup_exc)

    def force_save_current_model(
        self,
        design_state: DesignState,
        experiment_dir: str,
        reason: str = "final_selected",
    ) -> str:
        """
        Force-save current COMSOL model for final-selected artifacts.
        """
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={"experiment_dir": str(experiment_dir)},
        )
        return self._save_mph_model(request, reason=reason)

    def _try_save_mph_path(self, save_path: Path) -> bool:
        """
        Try saving `.mph` to target path:
        1) MPh `save()`
        2) Java API `save()` fallback
        """
        save_path_safe = str(save_path).replace("\\", "/")
        try:
            self.model.save(save_path_safe)
            logger.info(f"  ✓ COMSOL .mph 模型已保存: {save_path_safe}")
            return True
        except Exception as save_error:
            logger.warning(f"  ⚠ MPh save() 调用失败: {save_error}")
            logger.warning("  尝试使用 Java API 保存...")
            try:
                self.model.java.save(save_path_safe)
                logger.info(f"  ✓ COMSOL .mph 模型已保存（Java API）: {save_path_safe}")
                return True
            except Exception as java_error:
                logger.warning(f"  ⚠ Java API 保存失败: {java_error}")
                return False
