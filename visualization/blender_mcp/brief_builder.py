"""
Build Codex + Blender MCP execution briefs from render bundles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def build_render_brief(
    *,
    bundle: Dict[str, Any],
    bundle_path: str | Path,
    scene_script_path: str | Path,
    output_image_path: str | Path,
    output_blend_path: str | Path,
) -> str:
    bundle_file = Path(bundle_path).resolve()
    script_file = Path(scene_script_path).resolve()
    image_file = Path(output_image_path).resolve()
    blend_file = Path(output_blend_path).resolve()

    component_roles = {}
    for component in list(bundle.get("components", []) or []):
        role = str(component.get("render_role", "generic_box") or "generic_box")
        component_roles[role] = int(component_roles.get(role, 0) or 0) + 1

    heuristics = dict(bundle.get("heuristics", {}) or {})
    metrics = dict(bundle.get("metrics", {}) or {})

    prompt_lines = [
        "Use the `blender` MCP server for this task.",
        "First call `get_scene_info` to verify the Blender connection is alive.",
        f"Then read and execute the Python code from `{script_file}` using the `execute_blender_code` tool.",
        "After the scene is built, call `get_viewport_screenshot` once for inspection.",
        f"The final render should be saved to `{image_file}` and the Blender scene to `{blend_file}`.",
        "Do not redesign the layout coordinates; use the bundle as the geometric truth source.",
        "It is acceptable to keep solar wings / antenna / lens as visual heuristics only when they are marked as heuristics in the bundle.",
    ]

    return "\n".join(
        [
            "# Blender MCP Render Brief",
            "",
            "## Input Artifacts",
            f"- Render bundle: `{bundle_file}`",
            f"- Generated Blender scene code: `{script_file}`",
            f"- Expected still render: `{image_file}`",
            f"- Expected `.blend` scene: `{blend_file}`",
            "",
            "## Bundle Summary",
            f"- Run ID: `{bundle.get('run_id', '')}`",
            f"- Profile: `{dict(bundle.get('render_profile', {}) or {}).get('profile_name', '')}`",
            f"- Component count: `{len(list(bundle.get('components', []) or []))}`",
            f"- Component roles: `{json.dumps(component_roles, ensure_ascii=False)}`",
            f"- Payload face: `{heuristics.get('payload_face', '')}`",
            f"- Solar wings heuristic: `{bool(heuristics.get('enable_solar_wings'))}`",
            f"- Payload lens heuristic: `{bool(heuristics.get('enable_payload_lens'))}`",
            f"- Radiator fin heuristic: `{bool(heuristics.get('enable_radiator_fins'))}`",
            f"- best_cv_min: `{metrics.get('best_cv_min', '')}`",
            f"- diagnosis_status: `{metrics.get('diagnosis_status', '')}`",
            "",
            "## Codex Prompt",
            "```text",
            *prompt_lines,
            "```",
            "",
            "## Blender UI Reminder",
            "- In Blender, open the 3D viewport sidebar with `N`.",
            "- Open the `BlenderMCP` tab.",
            "- Click `Connect to MCP server` before asking Codex to use the MCP tools.",
            "",
            "## Safety Boundary",
            "- Keep `DesignState` coordinates as the source of truth.",
            "- Treat solar wings / antenna / lens / radiator fins as visualization-side heuristics only.",
            "- Do not use the render as a physics or constraint-evaluation artifact.",
            "",
        ]
    )
