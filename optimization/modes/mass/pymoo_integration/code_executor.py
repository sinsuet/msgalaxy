"""
Safe execution helper for LLM-generated optimization scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import traceback
from typing import Any, Dict, Optional


@dataclass
class ScriptExecutionResult:
    success: bool
    message: str
    traceback_text: str = ""
    namespace: Dict[str, Any] = field(default_factory=dict)
    return_value: Any = None


def safe_exec_generated_script(
    script: str,
    injected_globals: Optional[Dict[str, Any]] = None,
    return_symbol: str = "",
) -> ScriptExecutionResult:
    """
    Execute generated Python safely and return structured traceback on failure.

    This wrapper is designed for Meta-Reasoner feedback loops:
    - Runtime errors are captured in `traceback_text`.
    - No exception is raised to caller.
    """

    namespace: Dict[str, Any] = {}
    if injected_globals:
        namespace.update(injected_globals)

    try:
        exec(script, namespace, namespace)  # noqa: S102 - expected for code synthesis loop
        return_value = namespace.get(return_symbol) if return_symbol else None
        return ScriptExecutionResult(
            success=True,
            message="script executed",
            namespace=namespace,
            return_value=return_value,
        )
    except (MemoryError, OverflowError, FloatingPointError, RuntimeError, Exception) as exc:
        return ScriptExecutionResult(
            success=False,
            message=f"script failed: {exc}",
            traceback_text=traceback.format_exc(),
            namespace=namespace,
            return_value=None,
        )

