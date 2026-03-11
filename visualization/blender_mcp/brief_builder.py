"""
Build Codex + Blender MCP execution briefs from render bundles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _final_components(bundle: Dict[str, Any]) -> list[Dict[str, Any]]:
    key_states = dict(bundle.get("key_states", {}) or {})
    final_state = dict(key_states.get("final", {}) or {})
    components = list(final_state.get("components", []) or [])
    if components:
        return components
    return list(bundle.get("components", []) or [])


def build_render_brief(
    *,
    bundle: Dict[str, Any],
    bundle_path: str | Path,
    scene_script_path: str | Path,
    output_image_path: str | Path,
    output_blend_path: str | Path,
    review_payload_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> str:
    bundle_file = Path(bundle_path).resolve()
    script_file = Path(scene_script_path).resolve()
    image_file = Path(output_image_path).resolve()
    blend_file = Path(output_blend_path).resolve()
    payload_file = Path(review_payload_path).resolve() if review_payload_path else None
    manifest_file = Path(manifest_path).resolve() if manifest_path else None

    components = _final_components(bundle)
    component_roles = {}
    for component in components:
        role = str(component.get("render_role", "generic_box") or "generic_box")
        component_roles[role] = int(component_roles.get(role, 0) or 0) + 1

    heuristics = dict(bundle.get("heuristics", {}) or {})
    metrics = dict(bundle.get("metrics", {}) or {})
    key_states = dict(bundle.get("key_states", {}) or {})
    final_state = dict(key_states.get("final", {}) or {})
    best_state = dict(key_states.get("best", {}) or {})
    artifact_links = dict(bundle.get("artifact_links", {}) or {})
    metadata = dict(bundle.get("metadata", {}) or {})
    final_state_metadata = dict(final_state.get("metadata", {}) or {})
    best_state_metadata = dict(best_state.get("metadata", {}) or {})

    payload = {}
    if payload_file is not None and payload_file.exists():
        try:
            payload = json.loads(payload_file.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload_metadata = dict(payload.get("metadata", {}) or {})
    operator_family_audit = dict(payload_metadata.get("operator_family_audit", {}) or {})

    prompt_lines = [
        "Use the `blender` MCP server for this task.",
        "First call `get_scene_info` to verify the Blender connection is alive.",
        f"Then read and execute the Python code from `{script_file}` using the `execute_blender_code` tool.",
        "This Phase 1 scene script keeps direct-render compatibility by reconstructing the final state only.",
        "Do not redesign the layout coordinates; use the bundle as the geometric truth source.",
        "It is acceptable to keep solar wings / antenna / lens as visual heuristics only when they are marked as heuristics in the bundle.",
        "Do not treat heuristics, labels, or proxy attachments as physics truth.",
        f"The final render should be saved to `{image_file}` and the Blender scene to `{blend_file}`.",
    ]

    lines = [
        "# Blender MCP Render Brief",
        "",
        "## Input Artifacts",
        f"- Render bundle: `{bundle_file}`",
    ]
    if payload_file is not None:
        lines.append(f"- Review payload: `{payload_file}`")
    if manifest_file is not None:
        lines.append(f"- Render manifest: `{manifest_file}`")
    if str(artifact_links.get("runtime_feature_fingerprint_path", "") or "").strip():
        lines.append(
            f"- Runtime feature fingerprint: `{artifact_links.get('runtime_feature_fingerprint_path', '')}`"
        )
    if str(artifact_links.get("mass_final_summary_zh_path", "") or "").strip():
        lines.append(
            f"- MASS 中文总结: `{artifact_links.get('mass_final_summary_zh_path', '')}`"
        )
    if str(artifact_links.get("mass_final_summary_digest_path", "") or "").strip():
        lines.append(
            f"- MASS 中文总结 digest: `{artifact_links.get('mass_final_summary_digest_path', '')}`"
        )
    if str(artifact_links.get("llm_final_summary_zh_path", "") or "").strip():
        lines.append(
            f"- 中文最终总结: `{artifact_links.get('llm_final_summary_zh_path', '')}`"
        )
    if str(artifact_links.get("llm_final_summary_digest_path", "") or "").strip():
        lines.append(
            f"- 中文总结 digest: `{artifact_links.get('llm_final_summary_digest_path', '')}`"
        )
    lines.extend(
        [
            f"- Generated Blender scene code: `{script_file}`",
            f"- Expected still render: `{image_file}`",
            f"- Expected `.blend` scene: `{blend_file}`",
            "",
            "## Bundle Summary",
            f"- Run ID: `{bundle.get('run_id', '')}`",
            f"- Profile: `{dict(bundle.get('render_profile', {}) or {}).get('profile_name', '')}`",
            f"- Run mode: `{metadata.get('run_mode', '')}`",
            f"- Execution mode: `{metadata.get('execution_mode', '')}`",
            f"- Key states: `{','.join(key_states.keys())}`",
            f"- Direct-render state: `{final_state.get('name', 'final')}`",
            f"- Final snapshot: `{final_state.get('snapshot_path', '')}`",
            f"- Best primary operator family: `{best_state_metadata.get('primary_action_family_label', '')}`",
            f"- Final primary operator family: `{final_state_metadata.get('primary_action_family_label', '')}`",
            f"- Best operator caption: `{best_state_metadata.get('semantic_caption_short', '')}`",
            f"- Final operator caption: `{final_state_metadata.get('semantic_caption_short', '')}`",
            f"- Final operator rules: `{final_state_metadata.get('rule_summary', '')}`",
            f"- Final expected effects: `{final_state_metadata.get('expected_effect_summary', '')}`",
            f"- Operator-family unmapped actions: `{json.dumps(operator_family_audit.get('unmapped_actions', []), ensure_ascii=False)}`",
            "- Scene collections: `MSGA_Envelope`, `MSGA_Keepouts`, `MSGA_State_Initial`, `MSGA_State_Best`, `MSGA_State_Final`, `MSGA_Attachments`, `MSGA_Annotations`",
            "- Default visible state collection: `MSGA_State_Final`",
            f"- Component count: `{len(components)}`",
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
            "- Treat solar wings / antenna / lens / radiator fins / proxy attachments as visualization-only heuristics.",
            "- `initial / best / final` are now emitted as separate engineering-review collections for manual switching.",
            "- Do not use the render as a physics or constraint-evaluation artifact.",
            "",
        ]
    )
    return "\n".join(lines)
