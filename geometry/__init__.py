"""
Geometry module for satellite design optimization.

Provides 3D layout, packing algorithms, and CAD export functionality.
"""

from .schema import (
    AABB,
    Part,
    EnvelopeGeometry,
    PackingResult,
    generate_category_color,
)

from .keepout import (
    boxes_overlap,
    intersect_box,
    subtract_box,
    build_bins,
    build_envelope,
    create_keepout_aabbs,
)

from .packing import (
    multistart_pack,
    BinFaceMapper,
)

from .layout_engine import (
    LayoutEngine,
    generate_bom_from_config,
    generate_synthetic_bom,
)

from .layout_seed_service import (
    LayoutSeedService,
    apply_packing_result_to_reference_state,
    packing_result_to_design_state,
)

from .metrics import (
    calculate_boundary_violation,
    calculate_component_volume_sum,
    calculate_packing_efficiency,
    calculate_pairwise_clearance,
    summarize_geometry_state,
)

__all__ = [
    # Schema
    "AABB",
    "Part",
    "EnvelopeGeometry",
    "PackingResult",
    "generate_category_color",
    # Keepout
    "boxes_overlap",
    "intersect_box",
    "subtract_box",
    "build_bins",
    "build_envelope",
    "create_keepout_aabbs",
    # Packing
    "multistart_pack",
    "BinFaceMapper",
    # Layout Engine
    "LayoutEngine",
    "generate_bom_from_config",
    "generate_synthetic_bom",
    # Layout seed service
    "LayoutSeedService",
    "apply_packing_result_to_reference_state",
    "packing_result_to_design_state",
    # Metrics
    "calculate_boundary_violation",
    "calculate_component_volume_sum",
    "calculate_packing_efficiency",
    "calculate_pairwise_clearance",
    "summarize_geometry_state",
]
