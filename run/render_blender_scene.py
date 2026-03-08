#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Build Blender sidecar render artifacts from an MsGalaxy run and optionally render them directly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from visualization.blender_mcp import (  # noqa: E402
    build_render_brief,
    build_render_bundle_from_run,
    generate_blender_scene_script,
)
from visualization.review_package import audit_existing_review_package, write_scene_audit_artifacts  # noqa: E402


def _discover_blender_executable() -> Optional[Path]:
    candidates = [
        Path(r"D:\Program Files\Blender\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _run_direct_blender_render(blender_exe: Path, script_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(blender_exe), "--background", "--python", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _update_manifest_output_flags(
    manifest: dict,
    *,
    bundle_path: Path,
    review_payload_path: Path,
    scene_script_path: Path,
    brief_path: Path,
    scene_audit_path: Path | None = None,
    scene_readonly_checklist_path: Path | None = None,
    output_image_path: Path,
    output_blend_path: Path,
) -> None:
    metadata = dict(manifest.get("metadata", {}) or {})
    output_exists = dict(metadata.get("output_exists", {}) or {})
    output_exists.update(
        {
            "bundle": bundle_path.exists(),
            "review_payload": review_payload_path.exists(),
            "scene_script": scene_script_path.exists(),
            "brief": brief_path.exists(),
            "scene_audit": bool(scene_audit_path and scene_audit_path.exists()),
            "scene_readonly_checklist": bool(scene_readonly_checklist_path and scene_readonly_checklist_path.exists()),
            "output_image": output_image_path.exists(),
            "output_blend": output_blend_path.exists(),
        }
    )
    metadata["output_exists"] = output_exists
    manifest["metadata"] = metadata


def _print_scene_audit(audit: dict) -> None:
    summary = dict(audit.get("summary", {}) or {})
    print(
        "[INFO] Scene audit: "
        f"status={audit.get('status', '')}, "
        f"pass={summary.get('pass', 0)}, "
        f"warn={summary.get('warn', 0)}, "
        f"fail={summary.get('fail', 0)}"
    )
    for item in list(audit.get("checks", []) or []):
        status = str(item.get("status", "") or "").upper()
        if status not in {"WARN", "FAIL"}:
            continue
        print(f"[{status}] {item.get('name', '')}: {item.get('details', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Blender sidecar render artifacts from an MsGalaxy run")
    parser.add_argument("--run-dir", required=True, help="Path to an experiment run directory")
    parser.add_argument("--output-dir", default="", help="Optional output directory, default: <run-dir>/visualizations/blender")
    parser.add_argument("--profile", default="engineering", choices=["engineering", "showcase"], help="Render profile")
    parser.add_argument("--key-states", default="initial,best,final", help="Comma-separated key state set")
    parser.add_argument("--render-engine", default="BLENDER_EEVEE_NEXT", help="Blender render engine enum")
    parser.add_argument("--export-step", action="store_true", help="Export STEP via OpenCASCADE if available")
    parser.add_argument("--render-direct", action="store_true", help="Run Blender directly after generating artifacts")
    parser.add_argument("--blender-exe", default="", help="Path to blender.exe for direct render")
    parser.add_argument("--audit-only", action="store_true", help="Audit existing review-package artifacts without regenerating them")
    parser.add_argument("--skip-scene-audit", action="store_true", help="Skip read-only Phase 2 scene audit after build")
    parser.add_argument("--strict-scene-audit", action="store_true", help="Return non-zero when scene audit reports failure")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Run dir not found: {run_dir}")

    if args.audit_only:
        output_root = Path(args.output_dir).resolve() if args.output_dir else (run_dir / "visualizations" / "blender").resolve()
        audit = audit_existing_review_package(run_dir, output_dir=output_root)
        artifact_paths = write_scene_audit_artifacts(output_dir=output_root, audit=audit)
        audit["artifact_paths"] = artifact_paths
        manifest_path = output_root / "render_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.setdefault("metadata", {})
            manifest["metadata"]["scene_audit"] = audit
            manifest["scene_audit_path"] = artifact_paths["scene_audit_path"]
            manifest["scene_readonly_checklist_path"] = artifact_paths["scene_readonly_checklist_path"]
            _update_manifest_output_flags(
                manifest,
                bundle_path=output_root / "render_bundle.json",
                review_payload_path=output_root / "review_payload.json",
                scene_script_path=output_root / "blender_scene_builder.py",
                brief_path=output_root / "render_brief.md",
                scene_audit_path=output_root / "scene_audit.json",
                scene_readonly_checklist_path=output_root / "scene_readonly_mcp_checklist.md",
                output_image_path=output_root / "final_satellite_render.png",
                output_blend_path=output_root / "final_satellite_scene.blend",
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_scene_audit(audit)
        print(f"[OK] Scene audit report: {artifact_paths['scene_audit_path']}")
        print(f"[OK] Read-only MCP checklist: {artifact_paths['scene_readonly_checklist_path']}")
        if args.strict_scene_audit and str(audit.get("status", "") or "") == "fail":
            return 2
        return 0

    result = build_render_bundle_from_run(
        run_dir,
        output_dir=(args.output_dir or None),
        profile_name=args.profile,
        export_step=bool(args.export_step),
        key_states=args.key_states,
    )

    output_dir = Path(result["output_dir"]).resolve()
    bundle_path = Path(result["bundle_path"]).resolve()
    review_payload_path = Path(result["review_payload_path"]).resolve()
    manifest_path = Path(result["manifest_path"]).resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    output_image_path = output_dir / "final_satellite_render.png"
    output_blend_path = output_dir / "final_satellite_scene.blend"
    scene_script_path = output_dir / "blender_scene_builder.py"
    brief_path = output_dir / "render_brief.md"

    script = generate_blender_scene_script(
        bundle_path=bundle_path,
        output_image_path=output_image_path,
        output_blend_path=output_blend_path,
        profile_name=args.profile,
        render_engine=args.render_engine,
    )
    scene_script_path.write_text(script, encoding="utf-8")

    brief = build_render_brief(
        bundle=bundle,
        bundle_path=bundle_path,
        scene_script_path=scene_script_path,
        output_image_path=output_image_path,
        output_blend_path=output_blend_path,
        review_payload_path=review_payload_path,
        manifest_path=manifest_path,
    )
    brief_path.write_text(brief, encoding="utf-8")

    manifest["direct_render_status"] = "skipped"
    manifest["direct_render_stdout"] = ""
    manifest["direct_render_stderr"] = ""
    manifest.setdefault("metadata", {})
    manifest["metadata"]["direct_render_requested"] = bool(args.render_direct)
    manifest["metadata"]["scene_contract_status"] = "phase2_three_state_engineering_scene"
    manifest["metadata"]["scene_collection_names"] = [
        "MSGA_Envelope",
        "MSGA_Keepouts",
        "MSGA_State_Initial",
        "MSGA_State_Best",
        "MSGA_State_Final",
        "MSGA_Attachments",
        "MSGA_Annotations",
    ]
    manifest["metadata"]["default_visible_state_collection"] = "MSGA_State_Final"
    manifest["metadata"]["non_default_visible_collections"] = ["MSGA_Envelope", "MSGA_Keepouts", "MSGA_Attachments", "MSGA_Annotations"]
    manifest["scene_audit_path"] = str(bundle.get("artifact_links", {}).get("scene_audit_path", "") or "")
    manifest["scene_readonly_checklist_path"] = str(bundle.get("artifact_links", {}).get("scene_readonly_checklist_path", "") or "")

    if args.render_direct:
        blender_exe = Path(args.blender_exe).resolve() if args.blender_exe else _discover_blender_executable()
        if blender_exe is None or not blender_exe.exists():
            manifest["direct_render_status"] = "unavailable"
            manifest["direct_render_stderr"] = "Blender executable not found; direct render skipped."
        else:
            process = _run_direct_blender_render(blender_exe, scene_script_path)
            manifest["direct_render_status"] = "success" if process.returncode == 0 else "failed"
            manifest["direct_render_stdout"] = process.stdout[-8000:]
            manifest["direct_render_stderr"] = process.stderr[-8000:]
            manifest["metadata"]["blender_executable"] = str(blender_exe)

    _update_manifest_output_flags(
        manifest,
        bundle_path=bundle_path,
        review_payload_path=review_payload_path,
        scene_script_path=scene_script_path,
        brief_path=brief_path,
        scene_audit_path=None,
        scene_readonly_checklist_path=None,
        output_image_path=output_image_path,
        output_blend_path=output_blend_path,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.skip_scene_audit:
        audit = audit_existing_review_package(run_dir, output_dir=output_dir)
        artifact_paths = write_scene_audit_artifacts(output_dir=output_dir, audit=audit)
        audit["artifact_paths"] = artifact_paths
        manifest["metadata"]["scene_audit"] = audit
        manifest["scene_audit_path"] = artifact_paths["scene_audit_path"]
        manifest["scene_readonly_checklist_path"] = artifact_paths["scene_readonly_checklist_path"]
        _update_manifest_output_flags(
            manifest,
            bundle_path=bundle_path,
            review_payload_path=review_payload_path,
            scene_script_path=scene_script_path,
            brief_path=brief_path,
            scene_audit_path=output_dir / "scene_audit.json",
            scene_readonly_checklist_path=output_dir / "scene_readonly_mcp_checklist.md",
            output_image_path=output_image_path,
            output_blend_path=output_blend_path,
        )
        _print_scene_audit(audit)
        if args.strict_scene_audit and str(audit.get("status", "") or "") == "fail":
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return 2

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Render bundle: {bundle_path}")
    print(f"[OK] Review payload: {review_payload_path}")
    print(f"[OK] Scene script: {scene_script_path}")
    print(f"[OK] Render brief: {brief_path}")
    if manifest.get("scene_audit_path"):
        print(f"[OK] Scene audit report: {manifest['scene_audit_path']}")
    if manifest.get("scene_readonly_checklist_path"):
        print(f"[OK] Read-only MCP checklist: {manifest['scene_readonly_checklist_path']}")
    if bundle.get("source", {}).get("step_path"):
        print(f"[OK] STEP export: {bundle['source']['step_path']}")
    if manifest["direct_render_status"] == "success":
        print(f"[OK] Direct render image: {output_image_path}")
        print(f"[OK] Direct render blend: {output_blend_path}")
    elif args.render_direct:
        print(f"[WARN] Direct render status: {manifest['direct_render_status']}")
    else:
        print("[INFO] Direct render skipped; use Blender MCP or rerun with --render-direct")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
