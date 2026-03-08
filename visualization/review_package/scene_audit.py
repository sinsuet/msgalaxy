"""
Read-only scene contract audit for Blender review packages.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence

from core.path_policy import serialize_repo_path

from .builders import planned_review_package_paths


EXPECTED_SCENE_COLLECTIONS: tuple[str, ...] = (
    "MSGA_Envelope",
    "MSGA_Keepouts",
    "MSGA_State_Initial",
    "MSGA_State_Best",
    "MSGA_State_Final",
    "MSGA_Attachments",
    "MSGA_Annotations",
)

READ_ONLY_MCP_CHECKLIST: tuple[str, ...] = (
    "Call `get_scene_info` and confirm the generated `.blend` or script-built scene is loaded.",
    "Verify `MSGA_State_Final` is visible by default and `MSGA_State_Initial` / `MSGA_State_Best` can be toggled manually.",
    "Verify `MSGA_Envelope` and `MSGA_Keepouts` are visible and distinct from state component collections.",
    "Verify `MSGA_Attachments*` objects read as visualization-only proxies rather than solver truth components.",
    "Verify `MSGA_Annotations*` exposes only top-N moved-component labels instead of dense full-scene text.",
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _vector_distance(a: Sequence[Any], b: Sequence[Any]) -> float:
    values = []
    for idx in range(3):
        left = float(a[idx] if idx < len(a) else 0.0)
        right = float(b[idx] if idx < len(b) else 0.0)
        values.append((right - left) ** 2)
    return math.sqrt(sum(values))


def _component_positions(components: Sequence[Dict[str, Any]]) -> Dict[str, Sequence[Any]]:
    mapping: Dict[str, Sequence[Any]] = {}
    for component in list(components or []):
        comp_id = str(component.get("id", "") or "").strip()
        if not comp_id:
            continue
        mapping[comp_id] = list(component.get("position_mm", []) or [])
    return mapping


def _moved_component_count(reference: Sequence[Dict[str, Any]], target: Sequence[Dict[str, Any]]) -> int:
    ref_map = _component_positions(reference)
    target_map = _component_positions(target)
    count = 0
    for comp_id in sorted(set(ref_map.keys()) & set(target_map.keys())):
        if _vector_distance(ref_map[comp_id], target_map[comp_id]) > 1e-6:
            count += 1
    return count


def _build_check(name: str, ok: bool, *, fail_details: str, pass_details: str = "", severity: str = "fail") -> Dict[str, Any]:
    if ok:
        return {
            "name": name,
            "status": "pass",
            "details": pass_details or "ok",
        }
    return {
        "name": name,
        "status": "warn" if severity == "warn" else "fail",
        "details": fail_details,
    }


def audit_review_package(
    *,
    bundle_path: str | Path,
    review_payload_path: str | Path,
    manifest_path: str | Path,
    scene_script_path: str | Path,
    brief_path: str | Path,
) -> Dict[str, Any]:
    bundle_file = Path(bundle_path)
    payload_file = Path(review_payload_path)
    manifest_file = Path(manifest_path)
    scene_file = Path(scene_script_path)
    brief_file = Path(brief_path)

    missing_files = [
        str(path)
        for path in (bundle_file, payload_file, manifest_file, scene_file, brief_file)
        if not path.exists()
    ]
    if missing_files:
        return {
            "status": "fail",
            "checks": [
                {
                    "name": "required_files",
                    "status": "fail",
                    "details": f"Missing required review-package files: {missing_files}",
                }
            ],
            "summary": {"pass": 0, "warn": 0, "fail": 1},
            "read_only_mcp_checklist": list(READ_ONLY_MCP_CHECKLIST),
        }

    bundle = _load_json(bundle_file)
    payload = _load_json(payload_file)
    manifest = _load_json(manifest_file)
    scene_script = scene_file.read_text(encoding="utf-8")
    brief = brief_file.read_text(encoding="utf-8")

    key_states = dict(bundle.get("key_states", {}) or {})
    payload_states = dict(payload.get("states", {}) or {})
    manifest_key_states = dict(manifest.get("key_states", {}) or {})
    manifest_metadata = dict(manifest.get("metadata", {}) or {})
    scene_collection_names = list(manifest_metadata.get("scene_collection_names", []) or [])

    initial_components = list(dict(key_states.get("initial", {}) or {}).get("components", []) or [])
    best_components = list(dict(key_states.get("best", {}) or {}).get("components", []) or [])
    final_components = list(dict(key_states.get("final", {}) or {}).get("components", []) or bundle.get("components", []) or [])
    moved_initial_to_best = _moved_component_count(initial_components, best_components)
    moved_best_to_final = _moved_component_count(best_components or initial_components, final_components)

    needs_keepout_support = bool(list(bundle.get("keepouts", []) or []))
    needs_proxy_support = bool(
        any(bool(dict(component.get("attachments", {}) or {})) for component in final_components)
        or any(str(component.get("render_role", "") or "") in {"payload_optics", "radiator_panel"} for component in final_components)
        or bool(dict(bundle.get("heuristics", {}) or {}).get("enable_solar_wings"))
        or bool(dict(bundle.get("heuristics", {}) or {}).get("enable_payload_lens"))
        or bool(dict(bundle.get("heuristics", {}) or {}).get("enable_radiator_fins"))
    )

    checks: List[Dict[str, Any]] = []
    checks.append(
        _build_check(
            "bundle_key_states",
            all(name in key_states for name in ("initial", "best", "final")),
            fail_details="Bundle does not expose all required key states: initial, best, final.",
            pass_details="Bundle exposes initial/best/final key states.",
        )
    )
    checks.append(
        _build_check(
            "payload_states",
            all(name in payload_states for name in ("initial", "best", "final")),
            fail_details="Review payload does not expose all required states.",
            pass_details="Review payload exposes initial/best/final state summaries.",
        )
    )
    checks.append(
        _build_check(
            "manifest_scene_mode",
            str(manifest_metadata.get("scene_contract_status", "") or "") == "phase2_three_state_engineering_scene",
            fail_details="Manifest scene contract status is not marked as Phase 2 three-state engineering scene.",
            pass_details="Manifest scene contract status is Phase 2 three-state engineering scene.",
        )
    )
    checks.append(
        _build_check(
            "manifest_collections",
            all(name in scene_collection_names for name in EXPECTED_SCENE_COLLECTIONS),
            fail_details=f"Manifest scene collection names are incomplete: expected {EXPECTED_SCENE_COLLECTIONS}.",
            pass_details="Manifest includes all expected engineering-scene collection names.",
        )
    )
    checks.append(
        _build_check(
            "manifest_default_visible_state",
            str(manifest_metadata.get("default_visible_state_collection", "") or "") == "MSGA_State_Final",
            fail_details="Manifest default visible state collection is not MSGA_State_Final.",
            pass_details="Manifest default visible state collection is MSGA_State_Final.",
        )
    )
    checks.append(
        _build_check(
            "snapshot_consistency",
            all(
                str(dict(manifest.get("source_snapshot_paths", {}) or {}).get(name, "") or "")
                == str(dict(key_states.get(name, {}) or {}).get("snapshot_path", "") or "")
                for name in ("initial", "best", "final")
            ),
            fail_details="Manifest source snapshot paths do not match bundle key-state snapshot paths.",
            pass_details="Manifest and bundle snapshot paths are aligned for initial/best/final.",
        )
    )
    checks.append(
        _build_check(
            "scene_script_collections",
            all(token in scene_script for token in EXPECTED_SCENE_COLLECTIONS),
            fail_details="Scene script does not contain all expected engineering-scene collections.",
            pass_details="Scene script contains all expected engineering-scene collections.",
        )
    )
    checks.append(
        _build_check(
            "scene_script_keepouts",
            (not needs_keepout_support) or ("create_keepout_zone" in scene_script and "MSGA_Keepouts" in scene_script),
            fail_details="Bundle contains keepouts but scene script lacks keepout construction support.",
            pass_details="Keepout support is present for the current bundle.",
        )
    )
    checks.append(
        _build_check(
            "scene_script_annotations",
            (moved_initial_to_best == 0 and moved_best_to_final == 0) or ("create_state_annotations" in scene_script),
            fail_details="State displacement exists but scene script lacks moved-component annotation support.",
            pass_details="Moved-component annotation support is present for state comparison.",
            severity="warn",
        )
    )
    checks.append(
        _build_check(
            "scene_script_proxy_support",
            (not needs_proxy_support)
            or all(
                token in scene_script
                for token in ("attachment_proxy", "create_component_attachment_proxies")
            ),
            fail_details="Current bundle needs proxy attachment support but scene script lacks proxy construction hooks.",
            pass_details="Visualization-only proxy support is present where needed.",
            severity="warn",
        )
    )
    checks.append(
        _build_check(
            "brief_visualization_boundary",
            ("visualization-only" in brief.lower()) and ("MSGA_State_Final" in brief) and ("MSGA_State_Best" in brief),
            fail_details="Render brief does not clearly describe collection structure and visualization-only boundary.",
            pass_details="Render brief documents collection structure and visualization-only boundary.",
        )
    )
    checks.append(
        _build_check(
            "manifest_output_presence",
            bool(dict(manifest_metadata.get("output_exists", {}) or {}).get("scene_script"))
            and bool(dict(manifest_metadata.get("output_exists", {}) or {}).get("brief"))
            and bool(dict(manifest_metadata.get("output_exists", {}) or {}).get("bundle"))
            and bool(dict(manifest_metadata.get("output_exists", {}) or {}).get("review_payload")),
            fail_details="Manifest output existence flags are incomplete for core review-package artifacts.",
            pass_details="Manifest output existence flags cover core review-package artifacts.",
        )
    )
    checks.append(
        _build_check(
            "payload_scene_support",
            all(name in manifest_key_states for name in ("initial", "best", "final")),
            fail_details="Manifest key-state summary is incomplete for scene-side review.",
            pass_details="Manifest key-state summary covers initial/best/final.",
        )
    )

    pass_count = sum(1 for item in checks if item["status"] == "pass")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    fail_count = sum(1 for item in checks if item["status"] == "fail")
    status = "fail" if fail_count > 0 else ("warn" if warn_count > 0 else "pass")

    return {
        "status": status,
        "checks": checks,
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "moved_initial_to_best": moved_initial_to_best,
            "moved_best_to_final": moved_best_to_final,
            "keepout_count": len(list(bundle.get("keepouts", []) or [])),
            "final_component_count": len(final_components),
        },
        "read_only_mcp_checklist": list(READ_ONLY_MCP_CHECKLIST),
    }


def audit_existing_review_package(run_dir: str | Path, *, output_dir: str | Path | None = None) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    output_root = Path(output_dir).resolve() if output_dir else (run_path / "visualizations" / "blender").resolve()
    paths = planned_review_package_paths(output_root)
    audit = audit_review_package(
        bundle_path=paths["bundle_path"],
        review_payload_path=paths["review_payload_path"],
        manifest_path=paths["manifest_path"],
        scene_script_path=paths["scene_script_path"],
        brief_path=paths["brief_path"],
    )
    audit["run_dir"] = str(run_path)
    audit["output_dir"] = str(output_root)
    return audit


def build_read_only_mcp_checklist_markdown(audit: Dict[str, Any]) -> str:
    summary = dict(audit.get("summary", {}) or {})
    checks = list(audit.get("checks", []) or [])
    checklist = list(audit.get("read_only_mcp_checklist", []) or READ_ONLY_MCP_CHECKLIST)
    lines = [
        "# Read-Only MCP Scene Validation Checklist",
        "",
        f"- Audit status: `{audit.get('status', '')}`",
        f"- Pass / Warn / Fail: `{summary.get('pass', 0)}` / `{summary.get('warn', 0)}` / `{summary.get('fail', 0)}`",
        f"- Moved components `initial -> best`: `{summary.get('moved_initial_to_best', 0)}`",
        f"- Moved components `best -> final`: `{summary.get('moved_best_to_final', 0)}`",
        f"- Keepout count: `{summary.get('keepout_count', 0)}`",
        f"- Final component count: `{summary.get('final_component_count', 0)}`",
        "",
        "## Read-Only MCP Steps",
    ]
    for index, item in enumerate(checklist, start=1):
        lines.append(f"{index}. {item}")
    lines.extend(
        [
            "",
            "## Static Audit Findings",
        ]
    )
    for item in checks:
        lines.append(f"- `{item.get('status', '')}` `{item.get('name', '')}`: {item.get('details', '')}")
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "- This checklist is read-only and must not change solver truth.",
            "- Proxy attachments and labels remain visualization-only artifacts.",
            "- Direct render is not required for this validation flow.",
            "",
        ]
    )
    return "\n".join(lines)


def write_scene_audit_artifacts(
    *,
    output_dir: str | Path,
    audit: Dict[str, Any],
) -> Dict[str, str]:
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    planned_paths = planned_review_package_paths(output_root)
    audit_path = planned_paths["scene_audit_path"]
    checklist_path = planned_paths["scene_readonly_checklist_path"]
    persisted_audit_path = serialize_repo_path(audit_path)
    persisted_checklist_path = serialize_repo_path(checklist_path)

    audit_payload = dict(audit or {})
    audit_payload["artifact_paths"] = {
        "scene_audit_path": persisted_audit_path,
        "scene_readonly_checklist_path": persisted_checklist_path,
    }
    audit_path.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    checklist_path.write_text(build_read_only_mcp_checklist_markdown(audit_payload), encoding="utf-8")
    return {
        "scene_audit_path": persisted_audit_path,
        "scene_readonly_checklist_path": persisted_checklist_path,
    }
