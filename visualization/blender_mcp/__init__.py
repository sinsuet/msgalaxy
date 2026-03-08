"""
Blender MCP sidecar utilities for MsGalaxy visualization.
"""

from .brief_builder import build_render_brief
from .bundle_builder import build_render_bundle_from_run

try:
    from .codegen import generate_blender_scene_script
except ModuleNotFoundError:  # pragma: no cover - optional helper for partial installs
    generate_blender_scene_script = None

__all__ = [
    "build_render_brief",
    "build_render_bundle_from_run",
    "generate_blender_scene_script",
]
