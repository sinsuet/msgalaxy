"""Ingest run summaries into mass evidence store."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

from .evidence_schema import MassEvidence
from .mass_rag_system import MassRAGSystem


def _is_run_dir(path: Path) -> bool:
    name = str(path.name or "")
    if name.startswith("run_"):
        return True
    if re.fullmatch(r"\d{4}_.+", name):
        return True
    if re.fullmatch(r"\d{6}_.+", name):
        return True
    return False


def _iter_summary_files(runs_root: Path) -> Iterable[Path]:
    for path in sorted(runs_root.rglob("summary.json")):
        if not _is_run_dir(path.parent):
            continue
        if path.is_file():
            yield path


def _build_evidence_from_summary(path: Path, payload: Dict[str, object]) -> MassEvidence:
    diagnosis = str(payload.get("diagnosis_status", "") or "").strip().lower()
    strict_proxy_feasible = bool(payload.get("strict_proxy_feasible", False))
    dominant_violation = str(payload.get("dominant_violation", "") or "").strip().lower()
    violation_types: List[str] = []
    if dominant_violation:
        violation_types.append(dominant_violation)

    title = f"run_summary_{path.parent.name}"
    content = (
        f"Run: {path.parent.name}\n"
        f"Diagnosis: {diagnosis}\n"
        f"Dominant violation: {dominant_violation}\n"
        f"first_feasible_eval: {payload.get('first_feasible_eval', None)}\n"
        f"best_cv_min: {payload.get('best_cv_min', None)}"
    )
    metadata = {
        "run_dir": str(path.parent.as_posix()),
        "summary_path": str(path.as_posix()),
        "maas_attempt_count": payload.get("maas_attempt_count", None),
        "source_gate_passed": payload.get("source_gate_passed", None),
    }
    return MassEvidence(
        evidence_id="",
        phase_hint="D",
        category="case",
        title=title,
        content=content,
        query_signature={
            "violation_types": violation_types,
            "dominant_violations": [dominant_violation] if dominant_violation else [],
        },
        action_signature={
            "operator_family": str(payload.get("search_space", "") or ""),
        },
        outcome_signature={
            "diagnosis_status": diagnosis,
            "strict_proxy_feasible": strict_proxy_feasible,
            "relaxed_only": bool(payload.get("relaxed_only_feasible", False)),
        },
        physics_provenance={
            "source_gate_passed": bool(payload.get("source_gate_passed", False)),
        },
        tags=["run_summary_ingest", str(payload.get("search_space", "") or "").strip().lower()],
        metadata=metadata,
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest run summaries into mass evidence store")
    parser.add_argument("--runs-root", default="experiments", help="Root directory containing run artifacts")
    parser.add_argument("--kb-path", default="data/knowledge_base", help="Knowledge base directory")
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root).resolve()
    if not runs_root.exists():
        print(f"runs root not found: {runs_root}")
        return 1

    rag = MassRAGSystem(
        api_key="",
        knowledge_base_path=str(Path(args.kb_path).resolve().as_posix()),
        enable_semantic=False,
    )

    added = 0
    scanned = 0
    for summary_path in _iter_summary_files(runs_root):
        scanned += 1
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        evidence = _build_evidence_from_summary(summary_path, payload)
        before = rag.stats().get("total", 0)
        rag.ingest_evidence([evidence])
        after = rag.stats().get("total", 0)
        if int(after) > int(before):
            added += 1

    print(f"scanned={scanned}, added={added}, total={rag.stats().get('total', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
