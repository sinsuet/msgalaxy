from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulation.comsol.field_registry import build_field_registry_manifest, get_field_spec
from simulation.comsol.physics_profiles import (
    materialize_contract_payload,
)
from tools.comsol_field_demo.common import iter_case_dirs, load_config, load_design_state, read_json, write_json


FIELD_REGISTRY_KEY_BY_TOOL_FIELD: Dict[str, str] = {
    "temperature": get_field_spec("temperature").key,
    "stress": get_field_spec("stress").key,
    "displacement": get_field_spec("displacement").key,
}


def _split_numeric_line(line: str) -> List[str]:
    if "," in line:
        return [token.strip() for token in line.split(",") if token.strip()]
    if ";" in line:
        return [token.strip() for token in line.split(";") if token.strip()]
    return [token.strip() for token in line.split() if token.strip()]


def _is_numeric_tokens(tokens: Sequence[str]) -> bool:
    try:
        for token in tokens:
            float(token)
    except Exception:
        return False
    return bool(tokens)


def parse_regular_grid_text(path: str | Path) -> np.ndarray:
    rows: List[List[float]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("%"):
                continue
            tokens = _split_numeric_line(line)
            if not _is_numeric_tokens(tokens):
                continue
            rows.append([float(token) for token in tokens])
    if not rows:
        raise ValueError(f"regular_grid_empty:{path}")
    return np.asarray(rows, dtype=float)


def _round_key(value: float) -> float:
    return round(float(value), 9)


def _build_scalar_grid(rows: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if rows.ndim != 2 or rows.shape[1] < 4:
        raise ValueError(f"regular_grid_invalid_shape:{rows.shape}")
    x_values = sorted({_round_key(value) for value in rows[:, 0].tolist()})
    y_values = sorted({_round_key(value) for value in rows[:, 1].tolist()})
    z_values = sorted({_round_key(value) for value in rows[:, 2].tolist()})
    field = np.full((len(x_values), len(y_values), len(z_values)), np.nan, dtype=float)
    x_index = {value: idx for idx, value in enumerate(x_values)}
    y_index = {value: idx for idx, value in enumerate(y_values)}
    z_index = {value: idx for idx, value in enumerate(z_values)}
    for row in rows:
        field[
            x_index[_round_key(row[0])],
            y_index[_round_key(row[1])],
            z_index[_round_key(row[2])],
        ] = float(row[3])
    return (
        np.asarray(x_values, dtype=float),
        np.asarray(y_values, dtype=float),
        np.asarray(z_values, dtype=float),
        field,
    )


def _maybe_convert_coordinate_scale(
    x_coords: np.ndarray,
    y_coords: np.ndarray,
    z_coords: np.ndarray,
    *,
    expected_span_mm: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    span = float(
        max(
            float(np.max(x_coords) - np.min(x_coords)),
            float(np.max(y_coords) - np.min(y_coords)),
            float(np.max(z_coords) - np.min(z_coords)),
        )
    )
    if span <= 0.0 or expected_span_mm <= 0.0:
        return x_coords, y_coords, z_coords, 1.0
    if span < expected_span_mm * 0.1:
        return x_coords * 1000.0, y_coords * 1000.0, z_coords * 1000.0, 1000.0
    if span > expected_span_mm * 10.0:
        return x_coords * 0.001, y_coords * 0.001, z_coords * 0.001, 0.001
    return x_coords, y_coords, z_coords, 1.0


def _storage_unit(field_name: str, field_manifest: Dict[str, Any]) -> str:
    manifest_unit = str(
        field_manifest.get("grid_unit", field_manifest.get("unit", field_manifest.get("raw_unit", ""))) or ""
    ).strip()
    if manifest_unit:
        return manifest_unit
    return str(get_field_spec(FIELD_REGISTRY_KEY_BY_TOOL_FIELD.get(field_name, field_name)).unit)


def export_case_tensors(case_dir: str | Path) -> Dict[str, Any]:
    case_path = Path(case_dir)
    manifest = read_json(case_path / "field_exports" / "manifest.json")
    design_state = load_design_state(case_path / "design_state.json")
    tensor_dir = case_path / "tensor"
    tensor_dir.mkdir(parents=True, exist_ok=True)
    source_claim = dict(manifest.get("source_claim", {}) or {})

    expected_span_mm = float(design_state.envelope.outer_size.x)
    outputs: Dict[str, Any] = {
        "case_id": manifest.get("case_id", case_path.name),
        "case_dir": str(case_path),
        "source_claim": dict(source_claim),
        "tensors": {},
        "errors": [],
    }
    outputs = materialize_contract_payload(
        outputs,
        claim=source_claim,
        contract_bundle=dict(manifest.get("contract_bundle", {}) or {}),
        source_payload=manifest,
        field_export_registry=dict(
            manifest.get("field_export_registry", {}) or build_field_registry_manifest()
        ),
    )

    for field_name in ("temperature", "stress", "displacement"):
        field_manifest = dict(dict(manifest.get("exports", {}) or {}).get(field_name, {}) or {})
        grid_path = str(field_manifest.get("grid_path", "") or "").strip()
        if not grid_path:
            outputs["errors"].append(f"{field_name}:missing_grid_path")
            continue
        try:
            rows = parse_regular_grid_text(grid_path)
            x_coords, y_coords, z_coords, field = _build_scalar_grid(rows)
            x_coords, y_coords, z_coords, _ = _maybe_convert_coordinate_scale(
                x_coords,
                y_coords,
                z_coords,
                expected_span_mm=expected_span_mm,
            )
            vectors = None
            if field_name == "displacement":
                vector_grids = []
                for axis_name in ("u", "v", "w"):
                    vector_path = str(
                        dict(field_manifest.get("vector_grid_paths", {}) or {}).get(axis_name, "")
                    ).strip()
                    if not vector_path:
                        raise ValueError(f"displacement_vector_missing:{axis_name}")
                    vector_rows = parse_regular_grid_text(vector_path)
                    vx, vy, vz, component_field = _build_scalar_grid(vector_rows)
                    vx, vy, vz, component_scale = _maybe_convert_coordinate_scale(
                        vx,
                        vy,
                        vz,
                        expected_span_mm=expected_span_mm,
                    )
                    if not (
                        np.allclose(vx, x_coords)
                        and np.allclose(vy, y_coords)
                        and np.allclose(vz, z_coords)
                    ):
                        raise ValueError(f"displacement_vector_grid_mismatch:{axis_name}")
                    _ = component_scale
                    vector_grids.append(component_field.astype(float))
                vectors = np.stack(vector_grids, axis=-1)

            stored_field = field.astype(float)
            stored_vectors = vectors.astype(float) if vectors is not None else None
            stored_unit = _storage_unit(field_name, field_manifest)
            registry_key = str(FIELD_REGISTRY_KEY_BY_TOOL_FIELD.get(field_name, field_name))
            vector_component_registry_keys: Dict[str, str] = {}
            if field_name == "displacement" and stored_vectors is not None:
                manifest_component_keys = dict(
                    field_manifest.get("vector_component_registry_keys", {}) or {}
                )
                vector_component_registry_keys = (
                    {
                        str(axis_name): str(registry_key)
                        for axis_name, registry_key in manifest_component_keys.items()
                        if str(axis_name).strip() and str(registry_key).strip()
                    }
                    or {
                        axis_name: get_field_spec(f"displacement_{axis_name}").key
                        for axis_name in ("u", "v", "w")
                    }
                )
            tensor_path = tensor_dir / f"{field_name}_tensor.npz"
            np.savez_compressed(
                tensor_path,
                field=stored_field.astype(np.float32),
                vectors=stored_vectors.astype(np.float32) if stored_vectors is not None else np.asarray([], dtype=np.float32),
                x_coords=x_coords.astype(np.float32),
                y_coords=y_coords.astype(np.float32),
                z_coords=z_coords.astype(np.float32),
                unit=np.asarray(stored_unit),
                field_name=np.asarray(field_name),
                registry_key=np.asarray(registry_key),
            )
            outputs["tensors"][field_name] = {
                "path": str(tensor_path),
                "shape": list(stored_field.shape),
                "registry_key": registry_key,
                "unit": stored_unit,
            }
            if vector_component_registry_keys:
                outputs["tensors"][field_name]["vector_component_registry_keys"] = dict(
                    vector_component_registry_keys
                )
        except Exception as exc:
            outputs["errors"].append(f"{field_name}:{exc}")

    write_json(tensor_dir / "manifest.json", outputs)
    return outputs


def export_dataset_tensors(dataset_root: str | Path) -> Dict[str, Any]:
    dataset_path = Path(dataset_root)
    cases = [export_case_tensors(case_dir) for case_dir in iter_case_dirs(dataset_path)]
    summary = {
        "dataset_root": str(dataset_path),
        "case_count": len(cases),
        "cases": cases,
    }
    write_json(dataset_path / "tensor_export_summary.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert COMSOL grid exports to tensor npz payloads.")
    parser.add_argument("--dataset-root", type=str, required=True, help="Root created by tool_generate_cases.py")
    parser.add_argument("--config", type=str, default=None, help="Reserved for future use; kept for CLI symmetry.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    _ = load_config(args.config)
    summary = export_dataset_tensors(args.dataset_root)
    print(f"Exported tensors for {summary['case_count']} cases under {args.dataset_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
