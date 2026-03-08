"""
Generate standalone Blender Python scripts from render bundles.
"""

from __future__ import annotations

from pathlib import Path


SCENE_TEMPLATE = r'''
import bpy
import json
import math
from pathlib import Path

BUNDLE_PATH = __BUNDLE_PATH__
OUTPUT_IMAGE = __OUTPUT_IMAGE__
OUTPUT_BLEND = __OUTPUT_BLEND__
PROFILE_NAME = __PROFILE_NAME__
RENDER_ENGINE = __RENDER_ENGINE__

STATE_ORDER = ('initial', 'best', 'final')
STATE_COLLECTION_NAMES = {
    'initial': 'MSGA_State_Initial',
    'best': 'MSGA_State_Best',
    'final': 'MSGA_State_Final',
}
STATE_VISIBILITY = {
    'initial': False,
    'best': False,
    'final': True,
}
MAX_MOVED_LABELS = 6


def mm_to_m(value):
    return float(value) * 0.001


def vec_mm_to_m(values):
    return (mm_to_m(values[0]), mm_to_m(values[1]), mm_to_m(values[2]))


def radians(values):
    return tuple(math.radians(float(v)) for v in values)


def ensure_collection(name, parent=None):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    parent_collection = parent or bpy.context.scene.collection
    if parent_collection.children.get(name) is None:
        parent_collection.children.link(collection)
    return collection


def set_collection_visibility(collection, visible):
    collection.hide_viewport = not bool(visible)
    collection.hide_render = not bool(visible)


def move_object_to_collection(obj, collection):
    for old_collection in list(obj.users_collection):
        old_collection.objects.unlink(obj)
    collection.objects.link(obj)


def clear_scene():
    root = bpy.context.scene.collection
    for child in list(root.children):
        if str(child.name).startswith('MSGA_'):
            root.children.unlink(child)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block_group in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.curves,
        bpy.data.fonts,
    ):
        for block in list(block_group):
            if block.users == 0:
                block_group.remove(block)
    for collection in list(bpy.data.collections):
        if str(collection.name).startswith('MSGA_') and collection.users == 0:
            bpy.data.collections.remove(collection)


def ensure_material(name, base_color, metallic=0.0, roughness=0.45, alpha=1.0, emission_strength=0.0):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (300, 0)
    shader = nodes.new(type='ShaderNodeBsdfPrincipled')
    shader.location = (0, 0)
    shader.inputs['Base Color'].default_value = base_color
    shader.inputs['Metallic'].default_value = metallic
    shader.inputs['Roughness'].default_value = roughness
    shader.inputs['Alpha'].default_value = alpha
    if 'Emission Strength' in shader.inputs:
        shader.inputs['Emission Strength'].default_value = emission_strength
    links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    if hasattr(material, 'blend_method'):
        material.blend_method = 'BLEND' if alpha < 1.0 else 'OPAQUE'
    if hasattr(material, 'shadow_method'):
        material.shadow_method = 'HASHED'
    return material


def _material_spec(hint):
    key = str(hint or '').strip().lower()
    mapping = {
        'spacecraft_gray': ((0.56, 0.58, 0.62, 1.0), 0.55, 0.32, 1.0, 0.0),
        'black_anodized_aluminum': ((0.06, 0.06, 0.07, 1.0), 0.45, 0.42, 1.0, 0.0),
        'battery_dark_gray': ((0.14, 0.15, 0.16, 1.0), 0.20, 0.55, 1.0, 0.0),
        'power_blue_gray': ((0.24, 0.33, 0.42, 1.0), 0.22, 0.40, 1.0, 0.0),
        'gunmetal_space': ((0.20, 0.22, 0.24, 1.0), 0.35, 0.38, 1.0, 0.0),
        'white_thermal_paint': ((0.86, 0.88, 0.92, 1.0), 0.05, 0.28, 1.0, 0.0),
        'brushed_aluminum': ((0.75, 0.77, 0.79, 1.0), 0.72, 0.24, 1.0, 0.0),
        'mli_silver': ((0.74, 0.75, 0.76, 1.0), 0.80, 0.18, 1.0, 0.0),
        'solar_panel_blue': ((0.06, 0.11, 0.19, 1.0), 0.05, 0.20, 1.0, 0.0),
        'gold_foil': ((0.76, 0.61, 0.17, 1.0), 0.80, 0.25, 1.0, 0.0),
        'glass_lens': ((0.07, 0.12, 0.18, 1.0), 0.00, 0.02, 0.55, 0.0),
        'bus_shell': ((0.56, 0.60, 0.66, 1.0), 0.48, 0.32, 0.36, 0.0),
        'keepout_zone': ((0.92, 0.18, 0.16, 1.0), 0.02, 0.18, 0.18, 1.2),
        'attachment_proxy': ((0.98, 0.66, 0.16, 1.0), 0.12, 0.34, 0.48, 0.3),
        'annotation_text': ((0.96, 0.97, 0.98, 1.0), 0.0, 0.42, 1.0, 0.0),
    }
    return mapping.get(key, mapping['spacecraft_gray'])


def _mix_color(color, tint, factor):
    inv = 1.0 - float(factor)
    return (
        color[0] * inv + tint[0] * factor,
        color[1] * inv + tint[1] * factor,
        color[2] * inv + tint[2] * factor,
        color[3],
    )


def material_for_component(hint, state_name='final', variant='component'):
    color, metallic, roughness, alpha, emission = _material_spec(hint)
    if variant == 'proxy':
        color = _mix_color(color, (0.98, 0.64, 0.18, 1.0), 0.42)
        alpha = min(alpha, 0.48)
        roughness = max(roughness, 0.36)
        emission = max(emission, 0.2)
    elif variant == 'annotation':
        color, metallic, roughness, alpha, emission = _material_spec('annotation_text')
    elif variant == 'keepout':
        color, metallic, roughness, alpha, emission = _material_spec('keepout_zone')
    elif variant == 'envelope':
        color, metallic, roughness, alpha, emission = _material_spec('bus_shell')

    if state_name == 'initial':
        color = _mix_color(color, (0.24, 0.54, 0.90, 1.0), 0.35)
        alpha = min(alpha, 0.34)
    elif state_name == 'best':
        color = _mix_color(color, (0.96, 0.73, 0.24, 1.0), 0.28)
        alpha = min(alpha, 0.42)

    material_name = f"{str(hint or 'spacecraft_gray').lower()}__{state_name}__{variant}"
    return ensure_material(material_name, color, metallic, roughness, alpha, emission)


def assign_material(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def tag_visualization_object(obj, *, state_name='', kind='', visualization_only=False):
    obj['msgalaxy_state'] = str(state_name or '')
    obj['msgalaxy_kind'] = str(kind or '')
    obj['msgalaxy_visualization_only'] = bool(visualization_only)


def create_cube(name, dimensions_m, location_m, rotation_rad=(0.0, 0.0, 0.0), material=None):
    bpy.ops.mesh.primitive_cube_add(location=location_m, rotation=rotation_rad)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dimensions_m[0] * 0.5, dimensions_m[1] * 0.5, dimensions_m[2] * 0.5)
    if material is not None:
        assign_material(obj, material)
    bpy.ops.object.shade_smooth()
    return obj


def create_cylinder(name, radius_m, depth_m, location_m, rotation_rad=(0.0, 0.0, 0.0), material=None, vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius_m,
        depth=depth_m,
        location=location_m,
        rotation=rotation_rad,
    )
    obj = bpy.context.active_object
    obj.name = name
    if material is not None:
        assign_material(obj, material)
    bpy.ops.object.shade_smooth()
    return obj


def create_text_label(name, text, location_m, collection, size_m=0.018):
    bpy.ops.object.text_add(location=location_m)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.body = text
    obj.data.align_x = 'CENTER'
    obj.data.align_y = 'CENTER'
    obj.data.size = size_m
    assign_material(obj, material_for_component('annotation_text', 'final', 'annotation'))
    tag_visualization_object(obj, kind='annotation', visualization_only=True)
    move_object_to_collection(obj, collection)
    return obj


def create_bus_shell(envelope_mm, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    obj = create_cube(
        'MSGA_EnvelopeShell',
        (sx, sy, sz),
        (0.0, 0.0, 0.0),
        material=material_for_component('bus_shell', 'final', 'envelope'),
    )
    obj.display_type = 'SOLID'
    tag_visualization_object(obj, kind='envelope', visualization_only=True)
    move_object_to_collection(obj, collection)
    return obj


def create_keepout_zone(keepout, collection):
    min_point = keepout.get('min_point_mm', [0.0, 0.0, 0.0])
    max_point = keepout.get('max_point_mm', [0.0, 0.0, 0.0])
    center_mm = [0.5 * (float(min_point[idx]) + float(max_point[idx])) for idx in range(3)]
    size_mm = [max(float(max_point[idx]) - float(min_point[idx]), 1.0) for idx in range(3)]
    obj = create_cube(
        f"KEEP__{keepout.get('tag', 'zone')}",
        vec_mm_to_m(size_mm),
        vec_mm_to_m(center_mm),
        material=material_for_component('keepout_zone', 'final', 'keepout'),
    )
    obj.display_type = 'WIRE'
    obj.show_wire = True
    tag_visualization_object(obj, kind='keepout', visualization_only=True)
    move_object_to_collection(obj, collection)
    return obj


def nearest_face(position_mm, envelope_mm):
    hx, hy, hz = [float(v) * 0.5 for v in envelope_mm]
    distances = {
        '+X': hx - float(position_mm[0]),
        '-X': hx + float(position_mm[0]),
        '+Y': hy - float(position_mm[1]),
        '-Y': hy + float(position_mm[1]),
        '+Z': hz - float(position_mm[2]),
        '-Z': hz + float(position_mm[2]),
    }
    return min(distances, key=distances.get)


def face_vector(face):
    mapping = {
        '+X': (1.0, 0.0, 0.0),
        '-X': (-1.0, 0.0, 0.0),
        '+Y': (0.0, 1.0, 0.0),
        '-Y': (0.0, -1.0, 0.0),
        '+Z': (0.0, 0.0, 1.0),
        '-Z': (0.0, 0.0, -1.0),
    }
    return mapping.get(face, (0.0, 0.0, 1.0))


def create_component(component, state_name, collection):
    location_m = vec_mm_to_m(component['position_mm'])
    dimensions_m = vec_mm_to_m(component['dimensions_mm'])
    rotation_rad = radians(component.get('rotation_deg', (0.0, 0.0, 0.0)))
    material = material_for_component(component.get('material_hint', 'spacecraft_gray'), state_name, 'component')
    if component.get('envelope_type') == 'cylinder':
        radius_m = max(dimensions_m[0], dimensions_m[1]) * 0.5
        obj = create_cylinder(
            f"{state_name.upper()}__{component['id']}",
            radius_m=radius_m,
            depth_m=dimensions_m[2],
            location_m=location_m,
            rotation_rad=rotation_rad,
            material=material,
        )
    else:
        obj = create_cube(
            f"{state_name.upper()}__{component['id']}",
            dimensions_m=dimensions_m,
            location_m=location_m,
            rotation_rad=rotation_rad,
            material=material,
        )
    tag_visualization_object(obj, state_name=state_name, kind='component', visualization_only=False)
    obj['msgalaxy_component_id'] = str(component.get('id', ''))
    move_object_to_collection(obj, collection)
    return obj


def create_payload_lens_proxy(component, envelope_mm, state_name, collection):
    face = nearest_face(component['position_mm'], envelope_mm)
    normal = face_vector(face)
    dims_m = vec_mm_to_m(component['dimensions_mm'])
    pos_m = vec_mm_to_m(component['position_mm'])
    offset = max(dims_m) * 0.35
    center = (
        pos_m[0] + normal[0] * offset,
        pos_m[1] + normal[1] * offset,
        pos_m[2] + normal[2] * offset,
    )
    obj = create_cylinder(
        f"{state_name.upper()}__LENS__{component['id']}",
        radius_m=max(min(dims_m[0], dims_m[1]) * 0.22, 0.004),
        depth_m=max(min(dims_m) * 0.28, 0.004),
        location_m=center,
        rotation_rad=(math.radians(90.0), 0.0, 0.0) if face in {'+Y', '-Y'} else (0.0, math.radians(90.0), 0.0),
        material=material_for_component('glass_lens', state_name, 'proxy'),
    )
    tag_visualization_object(obj, state_name=state_name, kind='attachment_proxy', visualization_only=True)
    move_object_to_collection(obj, collection)


def create_radiator_fin_proxy(component, envelope_mm, state_name, collection):
    face = nearest_face(component['position_mm'], envelope_mm)
    normal = face_vector(face)
    dims_m = vec_mm_to_m(component['dimensions_mm'])
    pos_m = vec_mm_to_m(component['position_mm'])
    fin_count = 4
    for index in range(fin_count):
        lateral = (index - (fin_count - 1) * 0.5) * max(min(dims_m[0], dims_m[1]) * 0.14, 0.003)
        center = (
            pos_m[0] + normal[0] * (max(dims_m) * 0.3),
            pos_m[1] + normal[1] * (max(dims_m) * 0.3),
            pos_m[2] + normal[2] * (max(dims_m) * 0.3) + lateral,
        )
        obj = create_cube(
            f"{state_name.upper()}__FIN_{index:02d}__{component['id']}",
            (
                max(dims_m[0] * 0.08, 0.002),
                max(dims_m[1] * 0.7, 0.004),
                max(dims_m[2] * 0.08, 0.002),
            ),
            center,
            material=material_for_component('brushed_aluminum', state_name, 'proxy'),
        )
        tag_visualization_object(obj, state_name=state_name, kind='attachment_proxy', visualization_only=True)
        move_object_to_collection(obj, collection)


def create_component_attachment_proxies(component, state_name, collection):
    attachments = component.get('attachments', {}) or {}
    dims_m = vec_mm_to_m(component['dimensions_mm'])
    pos_m = vec_mm_to_m(component['position_mm'])
    proxy_material = material_for_component('attachment_proxy', state_name, 'proxy')

    if attachments.get('heatsink') is not None:
        obj = create_cube(
            f"{state_name.upper()}__HEATSINK__{component['id']}",
            (
                max(dims_m[0] * 0.58, 0.004),
                max(dims_m[1] * 0.58, 0.004),
                max(dims_m[2] * 0.22, 0.003),
            ),
            (pos_m[0], pos_m[1], pos_m[2] + dims_m[2] * 0.62),
            material=proxy_material,
        )
        tag_visualization_object(obj, state_name=state_name, kind='attachment_proxy', visualization_only=True)
        move_object_to_collection(obj, collection)

    if attachments.get('bracket') is not None:
        obj = create_cube(
            f"{state_name.upper()}__BRACKET__{component['id']}",
            (
                max(dims_m[0] * 0.72, 0.005),
                max(dims_m[1] * 0.14, 0.003),
                max(dims_m[2] * 0.72, 0.005),
            ),
            (pos_m[0], pos_m[1] - dims_m[1] * 0.62, pos_m[2]),
            material=proxy_material,
        )
        tag_visualization_object(obj, state_name=state_name, kind='attachment_proxy', visualization_only=True)
        move_object_to_collection(obj, collection)


def create_solar_wings(envelope_mm, state_name, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    wing_dims = (sx * 0.10, sy * 1.45, sz * 0.05)
    offset_x = sx * 0.62
    for side, sign in (('L', -1.0), ('R', 1.0)):
        obj = create_cube(
            f"{state_name.upper()}__SOLAR_{side}",
            wing_dims,
            (sign * offset_x, 0.0, 0.0),
            material=material_for_component('solar_panel_blue', state_name, 'proxy'),
        )
        tag_visualization_object(obj, state_name=state_name, kind='attachment_proxy', visualization_only=True)
        move_object_to_collection(obj, collection)


def create_payload_face_marker(envelope_mm, payload_face, state_name, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    vec = face_vector(payload_face)
    offset = max(sx, sy, sz) * 0.66
    location = (vec[0] * offset, vec[1] * offset, vec[2] * offset)
    marker = create_cylinder(
        f"{state_name.upper()}__PAYLOAD_FACE_MARKER",
        radius_m=max(min(sx, sy, sz) * 0.04, 0.004),
        depth_m=max(min(sx, sy, sz) * 0.12, 0.008),
        location_m=location,
        rotation_rad=(math.radians(90.0), 0.0, 0.0) if abs(vec[1]) > 0.5 else (0.0, math.radians(90.0), 0.0),
        material=material_for_component('gold_foil', state_name, 'proxy'),
    )
    tag_visualization_object(marker, state_name=state_name, kind='attachment_proxy', visualization_only=True)
    move_object_to_collection(marker, collection)


def component_map(components):
    mapping = {}
    for component in list(components or []):
        comp_id = str(component.get('id', '') or '').strip()
        if comp_id:
            mapping[comp_id] = component
    return mapping


def compute_displacements(reference_components, target_components):
    ref_map = component_map(reference_components)
    target_map = component_map(target_components)
    rows = []
    for comp_id in sorted(set(ref_map.keys()) & set(target_map.keys())):
        ref = ref_map[comp_id]
        target = target_map[comp_id]
        delta = [float(target['position_mm'][idx]) - float(ref['position_mm'][idx]) for idx in range(3)]
        dist = math.sqrt(sum(value * value for value in delta))
        rows.append({'component_id': comp_id, 'dist': dist, 'target': target})
    rows.sort(key=lambda item: item['dist'], reverse=True)
    return rows


def create_state_annotations(state_name, reference_components, target_components, collection):
    if not reference_components or not target_components:
        return
    rows = compute_displacements(reference_components, target_components)
    created = 0
    for row in rows:
        if row['dist'] <= 1e-6:
            continue
        target = row['target']
        pos_m = vec_mm_to_m(target['position_mm'])
        dims_m = vec_mm_to_m(target['dimensions_mm'])
        label_location = (
            pos_m[0],
            pos_m[1],
            pos_m[2] + max(dims_m[2] * 0.75, 0.016),
        )
        text = f"{target['id']} Δ{row['dist']:.1f} mm"
        label = create_text_label(
            f"{state_name.upper()}__LABEL__{target['id']}",
            text,
            label_location,
            collection,
        )
        tag_visualization_object(label, state_name=state_name, kind='annotation', visualization_only=True)
        created += 1
        if created >= MAX_MOVED_LABELS:
            break


def create_scene_legend(envelope_mm, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    create_text_label(
        'MSGA_Annotation_Legend',
        'MSGA_Attachments = visualization-only proxies',
        (0.0, -sy * 1.12, sz * 0.72),
        collection,
        size_m=max(min(sx, sy, sz) * 0.08, 0.016),
    )


def setup_world():
    scene = bpy.context.scene
    scene.render.engine = RENDER_ENGINE if RENDER_ENGINE in {'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'CYCLES'} else 'BLENDER_EEVEE_NEXT'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False
    if scene.render.engine == 'CYCLES':
        scene.cycles.samples = 64
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new('World')
        bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get('Background')
    if background is not None:
        background.inputs[0].default_value = (0.018, 0.021, 0.028, 1.0)
        background.inputs[1].default_value = 0.28
    if hasattr(scene, 'view_settings'):
        scene.view_settings.exposure = -1.05


def setup_lights_and_camera(envelope_mm):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    max_dim = max(sx, sy, sz)
    dist = max_dim * 3.2

    bpy.ops.object.light_add(type='AREA', location=(dist, -dist * 0.9, dist * 1.1))
    key_light = bpy.context.active_object
    key_light.data.energy = 360.0
    key_light.data.shape = 'RECTANGLE'
    key_light.data.size = max_dim * 1.8
    key_light.data.size_y = max_dim * 1.2

    bpy.ops.object.light_add(type='AREA', location=(-dist * 0.8, dist * 0.7, dist * 0.9))
    fill_light = bpy.context.active_object
    fill_light.data.energy = 110.0
    fill_light.data.size = max_dim * 1.5

    bpy.ops.object.light_add(type='SUN', location=(0.0, 0.0, dist * 2.0))
    sun = bpy.context.active_object
    sun.data.energy = 0.55
    sun.rotation_euler = (math.radians(42.0), 0.0, math.radians(-32.0))

    bpy.ops.object.camera_add(location=(dist * 1.42, -dist * 1.25, dist * 0.95))
    camera = bpy.context.active_object
    camera.name = 'MsGalaxyCamera'
    camera.data.lens = 58
    constraint = camera.constraints.new(type='TRACK_TO')
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0.0, 0.0, 0.0))
    target = bpy.context.active_object
    target.name = 'MsGalaxyCameraTarget'
    constraint.target = target
    constraint.track_axis = 'TRACK_NEGATIVE_Z'
    constraint.up_axis = 'UP_Y'
    bpy.context.scene.camera = camera


def save_outputs():
    if OUTPUT_IMAGE:
        image_path = Path(OUTPUT_IMAGE)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(image_path)
        bpy.ops.render.render(write_still=True)
    if OUTPUT_BLEND:
        blend_path = Path(OUTPUT_BLEND)
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_mainfile(filepath=str(blend_path))


def build_scene_collections():
    envelope_collection = ensure_collection('MSGA_Envelope')
    keepout_collection = ensure_collection('MSGA_Keepouts')
    attachments_root = ensure_collection('MSGA_Attachments')
    annotations_root = ensure_collection('MSGA_Annotations')
    set_collection_visibility(envelope_collection, True)
    set_collection_visibility(keepout_collection, True)
    set_collection_visibility(attachments_root, True)
    set_collection_visibility(annotations_root, True)

    state_collections = {}
    attachment_collections = {}
    annotation_collections = {}
    for state_name in STATE_ORDER:
        state_collection = ensure_collection(STATE_COLLECTION_NAMES[state_name])
        state_collections[state_name] = state_collection
        set_collection_visibility(state_collection, STATE_VISIBILITY[state_name])

        attachment_collection = ensure_collection(f"MSGA_Attachments_{state_name.title()}", attachments_root)
        annotation_collection = ensure_collection(f"MSGA_Annotations_{state_name.title()}", annotations_root)
        attachment_collections[state_name] = attachment_collection
        annotation_collections[state_name] = annotation_collection
        set_collection_visibility(attachment_collection, STATE_VISIBILITY[state_name])
        set_collection_visibility(annotation_collection, STATE_VISIBILITY[state_name])

    return {
        'envelope': envelope_collection,
        'keepouts': keepout_collection,
        'state_collections': state_collections,
        'attachment_collections': attachment_collections,
        'annotation_collections': annotation_collections,
        'annotations_root': annotations_root,
    }


def main():
    bundle = json.loads(Path(BUNDLE_PATH).read_text(encoding='utf-8'))
    envelope_mm = bundle['envelope']['outer_size_mm']
    key_states = dict(bundle.get('key_states', {}) or {})
    heuristics = dict(bundle.get('heuristics', {}) or {})

    clear_scene()
    setup_world()
    collections = build_scene_collections()

    create_bus_shell(envelope_mm, collections['envelope'])
    for keepout in list(bundle.get('keepouts', []) or []):
        create_keepout_zone(keepout, collections['keepouts'])

    state_components = {}
    for state_name in STATE_ORDER:
        state_payload = dict(key_states.get(state_name, {}) or {})
        components = list(state_payload.get('components', []) or [])
        if state_name == 'final' and not components:
            components = list(bundle.get('components', []) or [])
        state_components[state_name] = components
        state_collection = collections['state_collections'][state_name]
        attachment_collection = collections['attachment_collections'][state_name]
        annotation_collection = collections['annotation_collections'][state_name]
        for component in components:
            create_component(component, state_name, state_collection)
            if component.get('render_role') == 'payload_optics':
                create_payload_lens_proxy(component, envelope_mm, state_name, attachment_collection)
            if component.get('render_role') == 'radiator_panel':
                create_radiator_fin_proxy(component, envelope_mm, state_name, attachment_collection)
            create_component_attachment_proxies(component, state_name, attachment_collection)

        if state_name == 'best':
            create_state_annotations(state_name, state_components.get('initial', []), components, annotation_collection)
        elif state_name == 'final':
            reference_components = state_components.get('best', []) or state_components.get('initial', [])
            create_state_annotations(state_name, reference_components, components, annotation_collection)

    if heuristics.get('enable_solar_wings'):
        create_solar_wings(envelope_mm, 'final', collections['attachment_collections']['final'])
    if heuristics.get('enable_payload_lens'):
        create_payload_face_marker(
            envelope_mm,
            heuristics.get('payload_face', '+Z'),
            'final',
            collections['attachment_collections']['final'],
        )

    create_scene_legend(envelope_mm, collections['annotations_root'])
    setup_lights_and_camera(envelope_mm)
    save_outputs()
    print(json.dumps({
        'status': 'success',
        'bundle_path': BUNDLE_PATH,
        'output_image': OUTPUT_IMAGE,
        'output_blend': OUTPUT_BLEND,
        'profile_name': PROFILE_NAME,
        'scene_mode': 'phase2_three_state_engineering_scene',
        'state_collections': STATE_COLLECTION_NAMES,
    }))


main()
'''


def generate_blender_scene_script(
    *,
    bundle_path: str | Path,
    output_image_path: str | Path | None = None,
    output_blend_path: str | Path | None = None,
    profile_name: str = "showcase",
    render_engine: str = "BLENDER_EEVEE_NEXT",
) -> str:
    script = SCENE_TEMPLATE
    script = script.replace("__BUNDLE_PATH__", repr(str(Path(bundle_path).resolve())))
    script = script.replace("__OUTPUT_IMAGE__", repr(str(Path(output_image_path).resolve())) if output_image_path else "''")
    script = script.replace("__OUTPUT_BLEND__", repr(str(Path(output_blend_path).resolve())) if output_blend_path else "''")
    script = script.replace("__PROFILE_NAME__", repr(str(profile_name)))
    script = script.replace("__RENDER_ENGINE__", repr(str(render_engine)))
    return script.lstrip()
