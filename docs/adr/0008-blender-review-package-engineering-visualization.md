# 0008-blender-review-package-engineering-visualization

- status: accepted
- date: 2026-03-08
- deciders: msgalaxy-core

## Context

Current repository-owned Blender support is limited to the sidecar P0 slice:
- `run/render_blender_scene.py` builds `render_bundle.json`, a Blender scene script, a Codex brief, and can optionally invoke direct Blender render,
- `visualization/blender_mcp/*` defines the bundle contract, bundle builder, script generator, and brief builder,
- `core/visualization.py` remains the canonical analytical visualization entry and renders static PNG/GIF artifacts such as `final_layout_3d.png`, `layout_evolution.png`, `thermal_heatmap.png`, `mass_storyboard.png`, `mass_trace.png`, and layout timeline frames.

The current pain point is not missing geometry data. The repository already persists enough run artifacts to support a richer review surface:
- `summary.json`
- `snapshots/*.json`
- `events/layout_events.jsonl`
- `tables/*.csv`
- `visualizations/*.png|gif`

The gap is that 3D spatial review and constraint/audit evidence are split:
- Blender P0 can reconstruct and render a final scene, but it does not carry structured diagnosis context,
- analytical PNG/GIF artifacts expose trends and audit clues, but they are disconnected from spatial inspection and state switching.

At the same time, optimization truth must remain in the existing `LLM -> pymoo -> physics -> diagnosis` pipeline. Any Blender-facing upgrade must stay a one-way consumer of run artifacts and must not rewrite solver results, constraints, or gate outcomes.

## Decision

1. Adopt a dual-asset review flow: **Blender as the primary 3D review surface** plus an **offline review dashboard as the analysis companion**.
2. Keep Blender focused on spatial inspection, state switching, and engineering review; do not use Blender as the container for dense analytical dashboards.
3. Upgrade `render_bundle.json` to a repository-owned `v2` contract that supports `initial / best / final` key states instead of only a final-state scene payload.
4. Define **review package** as the new standard delivery unit, with at least:
   - `render_bundle.json`
   - scene script
   - brief
   - `review_payload.json`
   - `review_dashboard.html`
   - `render_manifest.json`
5. Make `engineering` the default review profile; keep `showcase` as an optional enhancement profile and not the default decision surface.
6. Trigger the review package flow via explicit CLI invocation, with `run/render_blender_scene.py` as the canonical entry; do not attach it to strict benchmark or default post-run hooks by default.
7. Treat Blender/MCP/render failures as non-fatal sidecar failures that do not change solver/run success semantics unless a caller explicitly requests render-blocking behavior.

## Alternatives Considered

### Rejected: Blender-only review surface
- Rejected because Blender is suitable for spatial scene inspection, but it is a poor container for high-density constraint, audit, and observability analytics.

### Rejected: analytical Matplotlib artifacts only
- Rejected because static PNG/GIF artifacts keep spatial inspection and diagnosis evidence split across disconnected assets.

### Deferred: PyVista / vtk.js first
- Deferred because the dependency footprint and viewer stack are heavier than needed for the current repository, while the existing run artifacts already fit a lighter offline HTML companion model.

### Deferred: Three.js / glTF first
- Deferred to a later phase because it is better suited for browser sharing and distribution after the repository first stabilizes the Blender-centered review workflow and artifact contracts.

### Rejected: integrate Blender into the optimization loop
- Rejected because it violates the sidecar boundary and would blur optimization truth with rendering-side behavior.

## Consequences

### Positive
- Unifies 3D review and audit evidence into a single review package.
- Reuses existing run artifacts instead of inventing a second truth source.
- Supports both engineering review and future presentation-grade delivery.
- Preserves a future migration path to glTF/web viewers without replacing the current Blender sidecar baseline.

### Negative
- Increases contract and manifest complexity.
- Requires maintenance of Blender scene templates and the offline HTML dashboard generator.
- Requires documentation and artifact naming to stay synchronized across `HANDOFF`, `README`, ADRs, and reports.

### Constraints
- The repository must not over-claim the review package as implemented today; the current truth is still Blender sidecar P0 only.
- Visualization-only heuristic objects must never be presented as physics truth or solver truth.
- The approved path must not introduce a mandatory Node-based frontend build pipeline into the repository mainline.

## Follow-up Required

- Update `HANDOFF.md` with the newly approved Blender Review Package direction as a planned target.
- Update `README.md` with the new user-facing direction and sidecar boundary.
- Maintain `docs/reports/R32_blender_review_package_plan_20260308.md` as the implementation master report for this decision.
- Do not update `AGENTS.md` unless future agent workflow actually changes.
