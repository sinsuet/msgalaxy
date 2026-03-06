"""
瀹為獙鏃ュ織绯荤粺

鎻愪緵瀹屾暣鐨勫彲杩芥函鎬ф敮鎸侊紝璁板綍姣忔杩唬鐨勮緭鍏ヨ緭鍑恒€佹寚鏍囧彉鍖栧拰LLM浜や簰銆?
"""

import os
import json
import logging
import math
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from core.event_logger import EventLogger
from core.llm_interaction_store import LLMInteractionStore
from core.mode_contract import normalize_observability_mode
from core.path_policy import serialize_artifact_path, serialize_run_path
from core.modes.agent_loop.trace_store import (
    append_agent_loop_trace_row,
    init_agent_loop_trace_csv,
    materialize_metrics_payload,
)
from core.modes.mass.trace_store import (
    append_mass_trace_row,
    init_mass_trace_csv,
    materialize_trace_payload,
)

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
    """Convert non-JSON-safe numeric values (NaN/Inf) into JSON-safe nulls."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json_value(v) for v in value]

    # Handle numpy arrays or similar containers with .tolist()
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _sanitize_json_value(tolist_fn())
        except Exception:
            pass

    # Handle numpy scalars or similar objects with .item()
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


def _sanitize_run_label(raw_label: Any) -> str:
    """Normalize run label for directory/file-system safety."""
    text = str(raw_label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "_")
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    if not text:
        return ""
    return text[:64]


def _normalize_run_algorithm(raw_algorithm: Any) -> str:
    """Normalize algorithm tag for run naming."""
    text = str(raw_algorithm or "").strip().lower()
    if not text:
        return ""

    compact = re.sub(r"[^a-z0-9]+", "", text)
    aliases = {
        "nsga2": "nsga2",
        "nsgaii": "nsga2",
        "nsga3": "nsga3",
        "nsgaiii": "nsga3",
        "moead": "moead",
    }
    mapped = aliases.get(compact, "")
    if mapped:
        return mapped

    token = _sanitize_run_label(text)
    return token[:16]


def _contains_algorithm_token(label: str, algorithm: str) -> bool:
    normalized_label = _sanitize_run_label(label)
    normalized_algorithm = _normalize_run_algorithm(algorithm)
    if not normalized_label or not normalized_algorithm:
        return False
    tokens = [item for item in normalized_label.split("_") if item]
    for token in tokens:
        if _normalize_run_algorithm(token) == normalized_algorithm:
            return True
    return False


def _build_compact_run_label(raw_label: Any, *, run_mode: str, run_algorithm: str) -> str:
    label = _sanitize_run_label(raw_label)
    algorithm = _normalize_run_algorithm(run_algorithm)
    mode_tag = "agent" if str(run_mode or "").strip().lower() == "agent_loop" else _sanitize_run_label(run_mode)

    if not label:
        label = mode_tag or "run"

    replacements = (
        ("operator_program", "op"),
        ("meta_policy", "mp"),
        ("baseline", "base"),
        ("deterministic", "det"),
        ("strict_replay", "replay"),
        ("agent_loop", "agent"),
        ("online_comsol", "ocomsol"),
        ("real_only", "real"),
        ("intermediate", "mid"),
        ("complex", "cx"),
        ("extreme", "x"),
    )
    for source, target in replacements:
        label = label.replace(source, target)

    raw_tokens = [item for item in label.split("_") if item]
    compacted_tokens: List[str] = []
    seen = set()
    for token in raw_tokens:
        if token in {"bm", "run"}:
            continue
        if re.fullmatch(r"\d{4}", token) or re.fullmatch(r"\d{6}", token):
            continue
        if token == "simple" and compacted_tokens and re.fullmatch(r"l\d+", compacted_tokens[-1]):
            continue
        if token in seen:
            continue
        seen.add(token)
        compacted_tokens.append(token)

    if not compacted_tokens:
        compacted_tokens = [mode_tag or "run"]

    compacted = "_".join(compacted_tokens)
    if algorithm and algorithm != "na" and not _contains_algorithm_token(compacted, algorithm):
        compacted = f"{compacted}_{algorithm}"
    compacted = _sanitize_run_label(compacted)

    if len(compacted) <= 40:
        return compacted

    preferred: List[str] = []
    for token in compacted.split("_"):
        if token.startswith("l") and token[1:].isdigit():
            preferred.append(token)
        elif token in {"op", "mp", "base", "agent", "mass"}:
            preferred.append(token)
        elif token.startswith("s") and token[1:].isdigit():
            preferred.append(token)
        elif _normalize_run_algorithm(token) in {"nsga2", "nsga3", "moead"}:
            preferred.append(_normalize_run_algorithm(token))
        elif token.startswith("t") and any(ch.isdigit() for ch in token):
            preferred.append(token)
        elif len(preferred) < 4:
            preferred.append(token)
    compacted = _sanitize_run_label("_".join(preferred) or compacted)
    return compacted[:40]


def _next_run_sequence(parent_dir: str, name_prefix: str) -> int:
    """Resolve next collision index for a run leaf name."""
    root = Path(str(parent_dir)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(name_prefix)}(?:_(\d{{2}}))?$")
    max_seq = 0
    try:
        for item in root.iterdir():
            if not item.is_dir():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            try:
                suffix = match.group(1)
                seq = int(suffix) if suffix is not None else 1
                max_seq = max(max_seq, seq)
            except Exception:
                continue
    except Exception:
        return 1
    return max_seq + 1


class ExperimentLogger:
    """Experiment-scoped artifact and observability logger."""
    def __init__(
        self,
        base_dir: str = "experiments",
        run_mode: Optional[str] = None,
        run_label: Optional[str] = None,
        run_algorithm: Optional[str] = None,
        run_naming_strategy: Optional[str] = None,
    ):
        """
        鍒濆鍖栨棩蹇楃鐞嗗櫒

        Args:
            base_dir: 瀹為獙杈撳嚭鏍圭洰褰?            run_mode: 杩愯妯″紡鏍囩锛坅gent_loop/mass锛?            run_label: 杩愯鏍囩锛堥€氬父鏉ヨ嚜 BOM/娴嬭瘯鍚嶏級
            run_algorithm: 绠楁硶鏍囩锛堝 NSGA-II/MOEAD锛?            run_naming_strategy: 鍛藉悕绛栫暐锛坈ompact/verbose锛?        """
        self.base_dir = base_dir
        self.base_dir_path = Path(self.base_dir).resolve()
        resolved_mode = str(run_mode or "").strip().lower()
        if resolved_mode not in {"agent_loop", "mass"}:
            resolved_mode = "unknown"
        self.run_mode = resolved_mode
        self.run_mode_bucket = (
            normalize_observability_mode(self.run_mode, default="shared")
            if self.run_mode in {"agent_loop", "mass"}
            else "shared"
        )
        self.run_label = _sanitize_run_label(run_label)
        self.run_algorithm = _normalize_run_algorithm(run_algorithm) or "na"
        strategy = str(run_naming_strategy or "compact").strip().lower()
        if strategy not in {"compact", "verbose"}:
            strategy = "compact"
        self.run_naming_strategy = strategy
        mode_tag = "agent" if self.run_mode == "agent_loop" else self.run_mode

        started_at = datetime.now()
        self.run_started_at = started_at.isoformat()
        self.run_date = started_at.strftime("%m%d")
        self.run_time = started_at.strftime("%H%M")
        self.run_time_precise = started_at.strftime("%H%M%S")
        self.run_timestamp = f"{self.run_date}_{self.run_time_precise}"
        date_root = self.base_dir_path / self.run_date
        if self.run_naming_strategy == "verbose":
            run_prefix = f"{self.run_time_precise}_{mode_tag}"
            if self.run_label:
                run_prefix = f"{run_prefix}_{self.run_label}"
            if self.run_algorithm != "na" and not _contains_algorithm_token(run_prefix, self.run_algorithm):
                run_prefix = f"{run_prefix}_{self.run_algorithm}"
        else:
            short_tag = _build_compact_run_label(
                self.run_label,
                run_mode=self.run_mode,
                run_algorithm=self.run_algorithm,
            )
            run_prefix = f"{self.run_time}_{short_tag}"

        self.run_sequence = int(max(1, _next_run_sequence(parent_dir=str(date_root), name_prefix=run_prefix)))
        run_stem = run_prefix if self.run_sequence == 1 else f"{run_prefix}_{self.run_sequence:02d}"
        run_dir = date_root / run_stem
        while run_dir.exists():
            self.run_sequence += 1
            run_stem = f"{run_prefix}_{self.run_sequence:02d}"
            run_dir = date_root / run_stem
        self.run_dir = str(run_dir)
        self.exp_dir = self.run_dir  # 娣诲姞exp_dir鍒悕
        self.run_id = f"run_{self.run_date}_{run_stem}"
        self.latest_index_path = str(self.base_dir_path / "_latest.json")
        os.makedirs(self.run_dir, exist_ok=True)
        self.event_logger = EventLogger(
            self.run_dir,
            persisted_run_dir=serialize_artifact_path(self.base_dir_path, self.run_dir),
        )
        self.event_logger.run_id = self.run_id
        self.save_run_manifest(
            {
                "run_mode": self.run_mode,
                "run_mode_bucket": self.run_mode_bucket,
                "run_label": self.run_label,
                "run_algorithm": self.run_algorithm,
                "run_naming_strategy": self.run_naming_strategy,
                "run_date": self.run_date,
                "run_time": self.run_time,
                "run_timestamp": self.run_timestamp,
                "run_started_at": self.run_started_at,
                "run_sequence": int(self.run_sequence),
            }
        )

        # 鍒涘缓瀛愭枃浠跺す
        self.llm_store = LLMInteractionStore(self.run_dir)
        self.llm_log_dir = self.llm_store.root_dir

        self.viz_dir = os.path.join(self.run_dir, "visualizations")
        os.makedirs(self.viz_dir, exist_ok=True)

        # 鍒濆鍖朇SV缁熻鏂囦欢
        self.csv_path = os.path.join(self.run_dir, "evolution_trace.csv")
        self._init_csv()
        self.mass_csv_path = os.path.join(self.run_dir, "mass_trace.csv")
        self._init_mass_csv()

        # 鍘嗗彶璁板綍
        self.history: List[str] = []

        # 鍒涘缓Python logger
        self.logger = get_logger(f"experiment_{self.run_id}", persist_global=False)

        # 娣诲姞鏂囦欢澶勭悊鍣紝灏嗘棩蹇楄緭鍑哄埌瀹為獙鐩綍鐨?run_log.txt
        self._add_run_log_handler(self.run_timestamp)

        print(f"Experiment logs: {self.run_dir}")

    def serialize_artifact_path(self, path_value: Any) -> str:
        return serialize_artifact_path(self.base_dir_path, path_value)

    def serialize_run_path(self, path_value: Any) -> str:
        return serialize_run_path(self.run_dir, path_value)

    def _add_run_log_handler(self, timestamp: str):
        """
        娣诲姞鏂囦欢澶勭悊鍣紝灏嗘棩蹇楄緭鍑哄埌瀹為獙鐩綍鐨?run_log.txt

        Args:
            timestamp: 鏃堕棿鎴冲瓧绗︿覆
        """
        # 鍒涘缓 run_log.txt 鏂囦欢璺緞
        run_log_path = os.path.join(self.run_dir, "run_log.txt")
        run_debug_path = os.path.join(self.run_dir, "run_log_debug.txt")

        class _RunLogCompactFilter(logging.Filter):
            """
            绮剧畝 run_log.txt 鐨勯珮閲嶅浣庝俊鎭瘑搴︽棩蹇椼€?
            璁捐鍘熷垯锛?            - WARNING/ERROR 涓€寰嬩繚鐣欙紱
            - 楂橀閲嶅鐨勭粨鏋勬寚鏍囨槑缁嗚浆绉诲埌 debug 鏃ュ織锛?            - 鍏抽敭娴佺▼纰戯紙COMSOL 璋冪敤銆侀绠楄€楀敖銆佸璁＄粨璁猴級淇濈暀銆?            """

            _structural_prefixes = (
                "璐ㄥ績:",
                "鍑犱綍涓績:",
                "璐ㄥ績鍋忕Щ閲?",
                "杞姩鎯噺:",
            )

            def filter(self, record: logging.LogRecord) -> bool:
                if record.levelno >= logging.WARNING:
                    return True

                if record.name == "simulation.structural_physics":
                    message = str(record.getMessage() or "")
                    if message.startswith(self._structural_prefixes):
                        return False

                return True

        # Configure the per-run log formatter.
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        root_logger = logging.getLogger()

        # 閬垮厤鍚岃繘绋嬪娆″垵濮嬪寲鏃堕噸澶嶆寕杞芥湰绯荤粺 handler 瀵艰嚧鏃ュ織鍊嶅
        stale_handlers = [
            h for h in list(root_logger.handlers)
            if bool(getattr(h, "_msgalaxy_run_handler", False))
        ]
        for handler in stale_handlers:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        # run_log.txt keeps a compact view for quick diagnosis.
        compact_handler = logging.FileHandler(run_log_path, encoding='utf-8')
        compact_handler.setLevel(logging.INFO)
        compact_handler.setFormatter(formatter)
        compact_handler.addFilter(_RunLogCompactFilter())
        compact_handler._msgalaxy_run_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(compact_handler)

        # run_log_debug.txt: 瀹屾暣鐗堬紝淇濈暀鍏ㄩ儴 INFO 缁嗚妭鐢ㄤ簬娣辨寲
        debug_handler = logging.FileHandler(run_debug_path, encoding='utf-8')
        debug_handler.setLevel(logging.INFO)
        debug_handler.setFormatter(formatter)
        debug_handler._msgalaxy_run_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(debug_handler)

        # 纭繚鏍?logger 鐨勭骇鍒笉浼氳繃婊ゆ帀 INFO 绾у埆鏃ュ織
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)

        self.logger.info(
            "Run log initialized: %s (compact) | %s (full)",
            run_log_path,
            run_debug_path,
        )

    def _init_csv(self):
        """Initialize the agent-loop trace CSV."""
        init_agent_loop_trace_csv(self.csv_path)

    def _init_mass_csv(self):
        """Initialize the mass trace CSV."""
        init_mass_trace_csv(self.mass_csv_path)

    def log_llm_interaction(self, iteration: int, role: str = None, request: Dict[str, Any] = None,
                           response: Dict[str, Any] = None, context_dict: Dict[str, Any] = None,
                           response_dict: Dict[str, Any] = None, mode: Optional[str] = None):
        """
        璁板綍LLM浜や簰

        鏀寔涓ょ璋冪敤鏂瑰紡锛?
        1. 鏂版柟寮? log_llm_interaction(iteration, role, request, response)
        2. 鏃ф柟寮? log_llm_interaction(iteration, context_dict, response_dict)

        Args:
            iteration: 杩唬娆℃暟
            role: 瑙掕壊鍚嶇О锛坢eta_reasoner, thermal_agent绛夛級
            request: 璇锋眰鏁版嵁
            response: 鍝嶅簲鏁版嵁
            context_dict: 杈撳叆涓婁笅鏂囷紙鏃ф柟寮忥級
            response_dict: LLM鍝嶅簲锛堟棫鏂瑰紡锛?            mode: 鍙€夋ā寮忔爣绛撅紙agent_loop/mass锛?        """
        # 鍏煎鏃ф柟寮?
        if context_dict is not None:
            request = context_dict
        if response_dict is not None:
            response = response_dict

        # 濡傛灉娌℃湁鏁版嵁锛岃烦杩?        if request is None and response is None:
            return

        prefix = self.llm_store.write(
            iteration=int(iteration),
            role=role,
            request=request,
            response=response,
            mode=mode if mode is not None else self.run_mode_bucket,
        )

        if request is not None or response is not None:
            print(f"  馃捑 LLM interaction saved: {prefix}")

    def log_metrics(self, data: Dict[str, Any]):
        """
        璁板綍杩唬鎸囨爣

        Args:
            data: 鎸囨爣鏁版嵁瀛楀吀
        """
        row = materialize_metrics_payload(data)
        append_agent_loop_trace_row(self.csv_path, row)

    def add_history(self, message: str):
        """
        娣诲姞鍘嗗彶璁板綍

        Args:
            message: 鍘嗗彶娑堟伅
        """
        self.history.append(message)

    def get_recent_history(self, n: int = 3) -> List[str]:
        """
        鑾峰彇鏈€杩戠殑鍘嗗彶璁板綍

        Args:
            n: 杩斿洖鏈€杩憂鏉¤褰?

        Returns:
            鍘嗗彶璁板綍鍒楄〃
        """
        return self.history[-n:] if len(self.history) >= n else self.history

    def save_design_state(self, iteration: int, design_state: Dict[str, Any]):
        """
        淇濆瓨璁捐鐘舵€?
        Args:
            iteration: 杩唬娆℃暟
            design_state: 璁捐鐘舵€佸瓧鍏?
        """
        state_path = os.path.join(self.run_dir, f"design_state_iter_{iteration:02d}.json")
        with open(state_path, 'w', encoding='utf-8') as f:
            _json_dump_safe(design_state, f)

    @staticmethod
    def _component_diff_lists(
        previous_state: Optional[Dict[str, Any]],
        current_state: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """Summarize component-level deltas between two layout states."""
        prev_components = {
            str(item.get("id", "")): item
            for item in list((previous_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        curr_components = {
            str(item.get("id", "")): item
            for item in list((current_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }

        moved_components: List[str] = []
        added_heatsinks: List[str] = []
        added_brackets: List[str] = []
        changed_contacts: List[str] = []
        changed_coatings: List[str] = []

        for comp_id, curr in curr_components.items():
            prev = prev_components.get(comp_id, {})
            curr_pos = dict(curr.get("position", {}) or {})
            prev_pos = dict(prev.get("position", {}) or {})
            try:
                dx = float(curr_pos.get("x", 0.0)) - float(prev_pos.get("x", 0.0))
                dy = float(curr_pos.get("y", 0.0)) - float(prev_pos.get("y", 0.0))
                dz = float(curr_pos.get("z", 0.0)) - float(prev_pos.get("z", 0.0))
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist > 1e-6:
                    moved_components.append(comp_id)
            except Exception:
                pass

            prev_heatsink = prev.get("heatsink")
            curr_heatsink = curr.get("heatsink")
            if curr_heatsink and curr_heatsink != prev_heatsink:
                added_heatsinks.append(comp_id)

            prev_bracket = prev.get("bracket")
            curr_bracket = curr.get("bracket")
            if curr_bracket and curr_bracket != prev_bracket:
                added_brackets.append(comp_id)

            prev_contacts = dict(prev.get("thermal_contacts", {}) or {})
            curr_contacts = dict(curr.get("thermal_contacts", {}) or {})
            if curr_contacts != prev_contacts:
                changed_contacts.append(comp_id)

            prev_coating = (
                prev.get("coating_type", "default"),
                float(prev.get("emissivity", 0.8) or 0.8),
                float(prev.get("absorptivity", 0.3) or 0.3),
            )
            curr_coating = (
                curr.get("coating_type", "default"),
                float(curr.get("emissivity", 0.8) or 0.8),
                float(curr.get("absorptivity", 0.3) or 0.3),
            )
            if curr_coating != prev_coating:
                changed_coatings.append(comp_id)

        return {
            "moved_components": sorted(moved_components),
            "added_heatsinks": sorted(added_heatsinks),
            "added_brackets": sorted(added_brackets),
            "changed_contacts": sorted(changed_contacts),
            "changed_coatings": sorted(changed_coatings),
        }

    def save_layout_snapshot(
        self,
        *,
        iteration: int,
        attempt: int,
        sequence: int,
        stage: str,
        design_state: Any,
        thermal_source: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        branch_action: str = "",
        branch_source: str = "",
        diagnosis_status: str = "",
        diagnosis_reason: str = "",
        operator_program_id: str = "",
        operator_actions: Optional[List[str]] = None,
        previous_design_state: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a layout snapshot and emit the paired layout event."""
        snapshots_dir = os.path.join(self.run_dir, "snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)

        stage_token = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_"
            for ch in str(stage or "snapshot")
        ).strip("_") or "snapshot"
        snapshot_name = (
            f"seq_{int(sequence):04d}_iter_{int(iteration):02d}_"
            f"attempt_{int(attempt):02d}_{stage_token}.json"
        )
        snapshot_path = os.path.join(snapshots_dir, snapshot_name)

        if hasattr(design_state, "model_dump"):
            state_payload = design_state.model_dump()
        elif isinstance(design_state, dict):
            state_payload = dict(design_state)
        else:
            state_payload = {}

        prev_payload: Optional[Dict[str, Any]] = None
        if previous_design_state is not None:
            if hasattr(previous_design_state, "model_dump"):
                prev_payload = previous_design_state.model_dump()
            elif isinstance(previous_design_state, dict):
                prev_payload = dict(previous_design_state)
            else:
                prev_payload = None

        delta = self._component_diff_lists(prev_payload, state_payload)
        payload = {
            "run_id": self.run_id,
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "timestamp": datetime.now().isoformat(),
            "sequence": int(sequence),
            "iteration": int(iteration),
            "attempt": int(attempt),
            "stage": str(stage or ""),
            "thermal_source": str(thermal_source or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "metrics": dict(metrics or {}),
            "delta": dict(delta),
            "metadata": dict(metadata or {}),
            "design_state": state_payload,
        }

        with open(snapshot_path, "w", encoding="utf-8") as f:
            _json_dump_safe(payload, f)

        event_payload = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "snapshot_path": self.serialize_run_path(snapshot_path),
            "thermal_source": str(thermal_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "moved_components": list(delta.get("moved_components", [])),
            "added_heatsinks": list(delta.get("added_heatsinks", [])),
            "added_brackets": list(delta.get("added_brackets", [])),
            "changed_contacts": list(delta.get("changed_contacts", [])),
            "changed_coatings": list(delta.get("changed_coatings", [])),
            "metrics": dict(metrics or {}),
            "metadata": dict(metadata or {}),
        }
        self.event_logger.append_layout_event(event_payload)
        return {"snapshot_path": snapshot_path, "event": event_payload, "delta": delta}

    def save_visualization(self, iteration: int, fig_name: str, fig):
        """Persist a generated visualization figure."""
        viz_path = os.path.join(self.viz_dir, f"iter_{iteration:02d}_{fig_name}.png")
        fig.savefig(viz_path, dpi=150, bbox_inches='tight')
        print(f"  馃搳 Visualization saved: {fig_name}")

    def save_summary(
        self,
        status: str,
        final_iteration: int,
        notes: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ):
        """Persist the run summary and refresh the run manifest."""
        summary = {
            "status": status,
            "final_iteration": final_iteration,
            "timestamp": datetime.now().isoformat(),
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "run_label": self.run_label,
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "notes": notes
        }
        if extra:
            summary.update(extra)

        summary_path = os.path.join(self.run_dir, "summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            _json_dump_safe(summary, f)

        # 鐢熸垚Markdown鎶ュ憡
        self._generate_markdown_report(summary)

        # 鍚屾鏇存柊浜嬩欢灞?run manifest
        self.save_run_manifest(
            {
                "status": str(status),
                "final_iteration": int(final_iteration),
                "extra": dict(extra or {}),
            }
        )

    def _write_latest_index(self, manifest_payload: Dict[str, Any]) -> None:
        latest_path = Path(self.latest_index_path)
        latest_path.parent.mkdir(parents=True, exist_ok=True)

        existing: Dict[str, Any] = {}
        if latest_path.exists():
            try:
                existing = json.loads(latest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}

        existing_started_at = str(existing.get("run_started_at", "") or "").strip()
        existing_run_id = str(existing.get("run_id", "") or "").strip()
        if (
            existing_started_at
            and existing_run_id
            and existing_run_id != self.run_id
            and existing_started_at > self.run_started_at
        ):
            return

        extra = dict(manifest_payload.get("extra", {}) or {})
        latest_payload = {
            "run_id": self.run_id,
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_leaf_dir": Path(self.run_dir).name,
            "run_date_dir": Path(self.run_dir).parent.name,
            "run_label": self.run_label,
            "run_mode": self.run_mode,
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "optimization_mode": str(manifest_payload.get("optimization_mode", "") or ""),
            "pymoo_algorithm": str(manifest_payload.get("pymoo_algorithm", "") or ""),
            "thermal_evaluator_mode": str(manifest_payload.get("thermal_evaluator_mode", "") or ""),
            "search_space_mode": str(manifest_payload.get("search_space_mode", "") or ""),
            "profile": str(manifest_payload.get("profile", "") or ""),
            "level": str(manifest_payload.get("level", "") or ""),
            "seed": manifest_payload.get("seed"),
            "status": str(manifest_payload.get("status", "") or ""),
            "diagnosis_status": str(extra.get("diagnosis_status", "") or ""),
            "diagnosis_reason": str(extra.get("diagnosis_reason", "") or ""),
            "summary_path": self.serialize_artifact_path(Path(self.run_dir) / "summary.json"),
            "manifest_path": self.serialize_artifact_path(
                Path(self.run_dir) / "events" / "run_manifest.json"
            ),
            "updated_at": datetime.now().isoformat(),
        }
        with latest_path.open("w", encoding="utf-8") as f:
            _json_dump_safe(latest_payload, f)

    def save_run_manifest(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Update the per-run manifest event payload."""
        data = dict(payload or {})
        data.setdefault("run_id", self.run_id)
        data["run_dir"] = self.serialize_artifact_path(data.get("run_dir", self.run_dir))
        data.setdefault("run_mode", self.run_mode)
        data.setdefault("run_mode_bucket", self.run_mode_bucket)
        data.setdefault("run_label", self.run_label)
        data.setdefault("run_algorithm", self.run_algorithm)
        data.setdefault("run_naming_strategy", self.run_naming_strategy)
        data.setdefault("run_date", self.run_date)
        data.setdefault("run_time", self.run_time)
        data.setdefault("run_timestamp", self.run_timestamp)
        data.setdefault("run_started_at", self.run_started_at)
        data.setdefault("run_sequence", int(self.run_sequence))
        manifest = self.event_logger.write_run_manifest(data)
        self._write_latest_index(manifest)
        return manifest

    def log_maas_phase_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS phase event."""
        try:
            self.event_logger.append_phase_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas phase event write failed: %s", exc)

    def log_maas_policy_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS policy event."""
        try:
            self.event_logger.append_policy_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas policy event write failed: %s", exc)

    def log_maas_generation_events(self, data: Dict[str, Any]) -> None:
        """Append MaaS generation-level events."""
        payload = dict(data or {})
        records = list(payload.get("records", []) or [])
        if not records:
            return

        iteration = int(payload.get("iteration", 0) or 0)
        attempt = int(payload.get("attempt", 0) or 0)
        branch_action = str(payload.get("branch_action", ""))
        branch_source = str(payload.get("branch_source", ""))
        search_space_mode = str(payload.get("search_space_mode", ""))
        pymoo_algorithm = str(payload.get("pymoo_algorithm", ""))

        for item in records:
            try:
                record = dict(item or {})
                self.event_logger.append_generation_event(
                    {
                        "iteration": iteration,
                        "attempt": attempt,
                        "generation": int(record.get("generation", 0) or 0),
                        "pymoo_algorithm": pymoo_algorithm,
                        "branch_action": branch_action,
                        "branch_source": branch_source,
                        "search_space_mode": search_space_mode,
                        "population_size": int(record.get("population_size", 0) or 0),
                        "feasible_count": int(record.get("feasible_count", 0) or 0),
                        "feasible_ratio": record.get("feasible_ratio"),
                        "best_cv": record.get("best_cv"),
                        "mean_cv": record.get("mean_cv"),
                        "best_feasible_sum_f": record.get("best_feasible_sum_f"),
                    }
                )
            except Exception as exc:
                self.logger.debug("maas generation event write failed: %s", exc)

    def log_maas_physics_event(self, data: Dict[str, Any]) -> None:
        """Append a MaaS physics event."""
        try:
            self.event_logger.append_physics_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas physics event write failed: %s", exc)

    def log_mass_trace(self, data: Dict[str, Any]):
        """Record a mass attempt-level trace row."""
        materialized = materialize_trace_payload(dict(data or {}))
        append_mass_trace_row(
            self.mass_csv_path,
            list(materialized.get("row", []) or []),
        )

        attempt_event_payload = dict(materialized.get("attempt_event_payload", {}) or {})
        candidate_event_payload = materialized.get("candidate_event_payload", None)
        is_best_attempt = bool(materialized.get("is_best_attempt", False))
        try:
            if not is_best_attempt:
                self.event_logger.append_attempt_event(attempt_event_payload)
            elif isinstance(candidate_event_payload, dict):
                self.event_logger.append_candidate_event(dict(candidate_event_payload))
        except Exception as exc:
            self.logger.debug("maas attempt/candidate event write failed: %s", exc)

    def _generate_markdown_report(self, summary: Dict[str, Any]):
        """Generate the markdown summary report."""
        report_path = os.path.join(self.run_dir, "report.md")

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# Satellite Design Optimization Report\n\n")
            f.write(f"**Status**: {summary['status']}\n\n")
            f.write(f"**Final Iteration**: {summary['final_iteration']}\n\n")
            f.write(f"**Timestamp**: {summary['timestamp']}\n\n")

            if summary.get('notes'):
                f.write(f"## Notes\n\n{summary['notes']}\n\n")

            f.write(f"## Files\n\n")
            f.write(f"- Evolution trace: `evolution_trace.csv`\n")
            f.write(f"- mass trace: `mass_trace.csv`\n")
            f.write(f"- Events: `events/`\n")
            f.write(f"  - generation events: `events/generation_events.jsonl`\n")
            f.write(f"- Materialized tables: `tables/`\n")
            f.write(f"- LLM interactions: `llm_interactions/`\n")
            active_buckets = self.llm_store.get_active_buckets()
            if active_buckets:
                f.write(
                    "- mode buckets: "
                    + ", ".join([f"`{name}/`" for name in active_buckets])
                    + "\n"
                )
            else:
                f.write("- mode buckets: (none)\n")
            f.write(f"- Visualizations: `visualizations/`\n")

        print(f"  馃摑 Report generated: report.md")

    # ============ Phase 4: Trace 瀹¤鏃ュ織 ============

    def save_trace_data(
        self,
        iteration: int,
        context_pack: Optional[Dict[str, Any]] = None,
        strategic_plan: Optional[Dict[str, Any]] = None,
        eval_result: Optional[Dict[str, Any]] = None
    ):
        """
        淇濆瓨瀹屾暣鐨?Trace 瀹¤鏁版嵁锛圥hase 4锛?

        Args:
            iteration: 杩唬娆℃暟
            context_pack: 杈撳叆缁?LLM 鐨勪笂涓嬫枃鍖?
            strategic_plan: LLM 鐨勬垬鐣ヨ鍒掕緭鍑?
            eval_result: 鐗╃悊浠跨湡鐨勮瘎浼扮粨鏋?
        """
        # 鍒涘缓 trace 瀛愮洰褰?
        trace_dir = os.path.join(self.run_dir, "trace")
        os.makedirs(trace_dir, exist_ok=True)

        prefix = f"iter_{iteration:02d}"

        # 淇濆瓨 ContextPack
        if context_pack is not None:
            context_path = os.path.join(trace_dir, f"{prefix}_context.json")
            with open(context_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(context_pack, f)

        # 淇濆瓨 StrategicPlan
        if strategic_plan is not None:
            plan_path = os.path.join(trace_dir, f"{prefix}_plan.json")
            with open(plan_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(strategic_plan, f)

        # 淇濆瓨 EvalResult
        if eval_result is not None:
            eval_path = os.path.join(trace_dir, f"{prefix}_eval.json")
            with open(eval_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(eval_result, f)

        self.logger.info(f"  馃捑 Trace data saved: {prefix}")

    def save_maas_diagnostic_event(
        self,
        iteration: int,
        attempt: int,
        payload: Dict[str, Any],
    ) -> None:
        """
        璁板綍 MaaS 闂幆姣忔姹傝В灏濊瘯鐨勮瘖鏂簨浠讹紙JSONL锛夈€?
        Args:
            iteration: 澶栧眰浼樺寲杩唬缂栧彿
            attempt: MaaS 鍐呴儴绗嚑娆″缓妯?姹傝В灏濊瘯锛堜粠1寮€濮嬶級
            payload: 浠绘剰鍙簭鍒楀寲璇婃柇淇℃伅
        """
        log_path = os.path.join(self.run_dir, "maas_diagnostics.jsonl")
        event = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "timestamp": datetime.now().isoformat(),
            "payload": payload,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json_dumps_safe(event) + "\n")
        self.logger.info(f"  馃捑 MaaS diagnostics saved: iter={iteration}, attempt={attempt}")

    def save_rollback_event(
        self,
        iteration: int,
        rollback_reason: str,
        from_state_id: str,
        to_state_id: str,
        penalty_before: float,
        penalty_after: float
    ):
        """
        璁板綍鍥為€€浜嬩欢锛圥hase 4锛?

        Args:
            iteration: 瑙﹀彂鍥為€€鐨勮凯浠ｆ鏁?
            rollback_reason: 鍥為€€鍘熷洜
            from_state_id: 鍥為€€鍓嶇殑鐘舵€両D
            to_state_id: 鍥為€€鍚庣殑鐘舵€両D
            penalty_before: 鍥為€€鍓嶇殑鎯╃綒鍒?
            penalty_after: 鍥為€€鍚庣殑鎯╃綒鍒?
        """
        rollback_log_path = os.path.join(self.run_dir, "rollback_events.jsonl")

        event = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "reason": rollback_reason,
            "from_state": from_state_id,
            "to_state": to_state_id,
            "penalty_before": penalty_before,
            "penalty_after": penalty_after
        }

        # 杩藉姞鍒?JSONL 鏂囦欢
        with open(rollback_log_path, 'a', encoding='utf-8') as f:
            f.write(_json_dumps_safe(event) + '\n')

        self.logger.warning(f"  鈿狅笍 Rollback event logged: {from_state_id} 鈫?{to_state_id}")


def get_logger(name: str, *, persist_global: Optional[bool] = None) -> Any:
    """
    Get a configured Python logger.

    Args:
        name: logger name
        persist_global: whether to also write `logs/<name>.log`

    Returns:
        logging.Logger object
    """
    import logging

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
    """
    璁板綍寮傚父璇︽儏

    Args:
        logger: 鏃ュ織璁板綍鍣?
        exception: 寮傚父瀵硅薄
        context: 涓婁笅鏂囦俊鎭?
    """
    import traceback

    error_msg = f"Exception in {context}: {type(exception).__name__}: {str(exception)}"
    logger.error(error_msg)
    logger.debug(traceback.format_exc())








