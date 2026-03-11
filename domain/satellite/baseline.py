from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .contracts import SatelliteReferenceBaseline


DEFAULT_BASELINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "satellite_archetypes"
    / "public_reference_baseline.json"
)


def load_satellite_reference_baseline(
    path: Optional[Path | str] = None,
) -> SatelliteReferenceBaseline:
    baseline_path = Path(path) if path is not None else DEFAULT_BASELINE_PATH
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return SatelliteReferenceBaseline.model_validate(payload)


def load_default_satellite_reference_baseline() -> SatelliteReferenceBaseline:
    return load_satellite_reference_baseline(DEFAULT_BASELINE_PATH)
