# 0006-blender-mcp-visualization-sidecar

- status: accepted
- date: 2026-03-06
- deciders: msgalaxy-core

## Context

Current MsGalaxy outputs already contain enough information to reconstruct the final layout:
- `workflow/modes/mass/pipeline_service.py` writes `final_selected` snapshots and keeps `final_mph_path` when available,
- `core/logger.py` persists `snapshots/*.json` with full `design_state`,
- `core/protocol.py` defines stable layout geometry in `mm`,
- `geometry/cad_export_occ.py` can export real STEP for the current dynamic geometry subset,
- `core/visualization.py` renders analytical plots and 3D layout PNG/GIF artifacts.

However, the repository does not yet produce presentation-grade realistic renders. At the same time, optimization truth must remain in the existing `LLM -> pymoo -> physics -> diagnosis` pipeline, not in a rendering stack.

## Decision

1. Introduce Blender MCP as a **visualization sidecar**, not as part of the optimization/constraint loop.
2. Use `final_selected` snapshot / `DesignState` as the canonical rendering source.
3. Add a repository-owned `render_bundle.json` contract as the stable handoff artifact to Blender MCP.
4. Prefer a staged rendering strategy:
   - Stage 1: direct primitive reconstruction from `DesignState`,
   - Stage 2: asset replacement by category/BOM mapping,
   - Stage 3: optional STEP-to-mesh bridge for selected components.
5. Persist outputs under `visualizations/blender/`, including scene file, stills, animations, and render manifest.
6. Treat Blender/MCP failures as non-fatal warnings unless the user explicitly requests render-blocking behavior.

## Consequences

### Positive
- Keeps optimization semantics unchanged and auditable.
- Reuses existing snapshot/CAD infrastructure.
- Enables realistic figures, turntables, and storytelling assets for reports and papers.
- Makes MCP implementation replaceable through a thin adapter layer.

### Negative
- Adds a new external dependency surface around Blender/MCP.
- Requires asset library curation and scene-template maintenance.
- STEP reuse inside Blender remains a secondary path due to bridge complexity.

### Constraints
- Only the P0 sidecar slice is implemented today: bundle generation, scene script generation, Codex brief generation, and optional direct Blender render.
- Must preserve current `mass`/`agent_loop` runtime contracts.
- Must keep render side effects out of strict gate and solver success criteria by default.
