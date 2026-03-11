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
from .catalog_geometry import (
    CatalogComponentSpec,
    GeometryProfileSpec,
    PrimitivePlacementSpec,
    extract_catalog_component_specs_from_layout_config,
    load_catalog_component_spec,
    resolve_catalog_component_spec_from_component_config,
    resolve_catalog_component_spec,
    resolve_catalog_component_specs,
)
from .geometry_proxy import (
    GeometryProxySpec,
    build_geometry_proxy_manifest,
    component_proxy_entries,
    shell_proxy_entries,
)
from .shell_spec import (
    ApertureSiteSpec,
    PanelSpec,
    ShellSpec,
    aperture_proxy_plans,
    build_box_panels,
    load_shell_spec,
    plan_box_panel_aperture,
    resolve_shell_spec_from_mapping,
    resolve_shell_spec,
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
    # Catalog geometry contracts
    "CatalogComponentSpec",
    "GeometryProfileSpec",
    "PrimitivePlacementSpec",
    "GeometryProxySpec",
    "build_geometry_proxy_manifest",
    "component_proxy_entries",
    "shell_proxy_entries",
    "extract_catalog_component_specs_from_layout_config",
    "load_catalog_component_spec",
    "resolve_catalog_component_spec_from_component_config",
    "resolve_catalog_component_spec",
    "resolve_catalog_component_specs",
    # Shell/panel/aperture contracts
    "ApertureSiteSpec",
    "PanelSpec",
    "ShellSpec",
    "aperture_proxy_plans",
    "build_box_panels",
    "load_shell_spec",
    "plan_box_panel_aperture",
    "resolve_shell_spec_from_mapping",
    "resolve_shell_spec",
]
