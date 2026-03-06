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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Blender sidecar render artifacts from an MsGalaxy run")
    parser.add_argument("--run-dir", required=True, help="Path to an experiment run directory")
    parser.add_argument("--output-dir", default="", help="Optional output directory, default: <run-dir>/visualizations/blender")
    parser.add_argument("--profile", default="showcase", choices=["engineering", "showcase"], help="Render profile")
    parser.add_argument("--render-engine", default="BLENDER_EEVEE_NEXT", help="Blender render engine enum")
    parser.add_argument("--export-step", action="store_true", help="Export STEP via OpenCASCADE if available")
    parser.add_argument("--render-direct", action="store_true", help="Run Blender directly after generating artifacts")
    parser.add_argument("--blender-exe", default="", help="Path to blender.exe for direct render")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Run dir not found: {run_dir}")

    result = build_render_bundle_from_run(
        run_dir,
        output_dir=(args.output_dir or None),
        profile_name=args.profile,
        export_step=bool(args.export_step),
    )

    output_dir = Path(result["output_dir"]).resolve()
    bundle_path = Path(result["bundle_path"]).resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

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
    )
    brief_path.write_text(brief, encoding="utf-8")

    manifest_path = output_dir / "render_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scene_script_path"] = str(scene_script_path)
    manifest["brief_path"] = str(brief_path)
    manifest["output_image_path"] = str(output_image_path)
    manifest["output_blend_path"] = str(output_blend_path)
    manifest["direct_render_status"] = "skipped"
    manifest["direct_render_stdout"] = ""
    manifest["direct_render_stderr"] = ""

    if args.render_direct:
        blender_exe = Path(args.blender_exe).resolve() if args.blender_exe else _discover_blender_executable()
        if blender_exe is None or not blender_exe.exists():
            raise FileNotFoundError("Blender executable not found; provide --blender-exe")

        process = _run_direct_blender_render(blender_exe, scene_script_path)
        manifest["direct_render_status"] = "success" if process.returncode == 0 else "failed"
        manifest["direct_render_stdout"] = process.stdout[-8000:]
        manifest["direct_render_stderr"] = process.stderr[-8000:]
        manifest["blender_executable"] = str(blender_exe)
        if process.returncode != 0:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                f"Direct Blender render failed with code {process.returncode}\n"
                f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
            )

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Render bundle: {bundle_path}")
    print(f"[OK] Scene script: {scene_script_path}")
    print(f"[OK] Render brief: {brief_path}")
    if bundle.get("source", {}).get("step_path"):
        print(f"[OK] STEP export: {bundle['source']['step_path']}")
    if args.render_direct:
        print(f"[OK] Direct render image: {output_image_path}")
        print(f"[OK] Direct render blend: {output_blend_path}")
    else:
        print("[INFO] Direct render skipped; use Blender MCP or rerun with --render-direct")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
