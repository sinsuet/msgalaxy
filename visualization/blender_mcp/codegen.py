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


def mm_to_m(value):
    return float(value) * 0.001


def vec_mm_to_m(values):
    return (mm_to_m(values[0]), mm_to_m(values[1]), mm_to_m(values[2]))


def radians(values):
    return tuple(math.radians(float(v)) for v in values)


def ensure_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_object_to_collection(obj, collection):
    for old_collection in list(obj.users_collection):
        old_collection.objects.unlink(obj)
    collection.objects.link(obj)


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in list(bpy.data.materials):
        if block.users == 0:
            bpy.data.materials.remove(block)
    for block in list(bpy.data.cameras):
        if block.users == 0:
            bpy.data.cameras.remove(block)
    for block in list(bpy.data.lights):
        if block.users == 0:
            bpy.data.lights.remove(block)


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
    if emission_strength > 0.0 and 'Emission Strength' in shader.inputs:
        shader.inputs['Emission Strength'].default_value = emission_strength
    links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    if hasattr(material, 'blend_method'):
        material.blend_method = 'BLEND' if alpha < 1.0 else 'OPAQUE'
    if hasattr(material, 'shadow_method'):
        material.shadow_method = 'HASHED'
    return material


def material_for_hint(hint):
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
        'bus_shell': ((0.56, 0.60, 0.66, 1.0), 0.48, 0.32, 0.58, 0.0),
        'ground_plane': ((0.07, 0.08, 0.10, 1.0), 0.0, 0.96, 1.0, 0.0),
    }
    color, metallic, roughness, alpha, emission_strength = mapping.get(key, mapping['spacecraft_gray'])
    return ensure_material(key or 'spacecraft_gray', color, metallic, roughness, alpha, emission_strength)


def create_cube(name, dimensions_m, location_m, rotation_rad=(0.0, 0.0, 0.0), material_hint='spacecraft_gray'):
    bpy.ops.mesh.primitive_cube_add(location=location_m, rotation=rotation_rad)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dimensions_m[0] * 0.5, dimensions_m[1] * 0.5, dimensions_m[2] * 0.5)
    obj.data.materials.clear()
    obj.data.materials.append(material_for_hint(material_hint))
    bpy.ops.object.shade_smooth()
    return obj


def create_cylinder(name, radius_m, depth_m, location_m, rotation_rad=(0.0, 0.0, 0.0), material_hint='spacecraft_gray', vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius_m, depth=depth_m, location=location_m, rotation=rotation_rad)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.clear()
    obj.data.materials.append(material_for_hint(material_hint))
    bpy.ops.object.shade_smooth()
    return obj


def create_bus_shell(envelope_mm, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    shell_thickness = max(min(sx, sy, sz) * 0.018, 0.004)
    panels = [
        ('BusPanel_PosX', (shell_thickness, sy, sz), (sx * 0.5 + shell_thickness * 0.5, 0.0, 0.0)),
        ('BusPanel_NegX', (shell_thickness, sy, sz), (-sx * 0.5 - shell_thickness * 0.5, 0.0, 0.0)),
        ('BusPanel_PosY', (sx, shell_thickness, sz), (0.0, sy * 0.5 + shell_thickness * 0.5, 0.0)),
        ('BusPanel_NegY', (sx, shell_thickness, sz), (0.0, -sy * 0.5 - shell_thickness * 0.5, 0.0)),
        ('BusPanel_PosZ', (sx, sy, shell_thickness), (0.0, 0.0, sz * 0.5 + shell_thickness * 0.5)),
        ('BusPanel_NegZ', (sx, sy, shell_thickness), (0.0, 0.0, -sz * 0.5 - shell_thickness * 0.5)),
    ]
    for name, dims, loc in panels:
        obj = create_cube(name, dims, loc, material_hint='bus_shell')
        move_object_to_collection(obj, collection)


def nearest_face(position_mm, envelope_mm):
    px, py, pz = [float(v) for v in position_mm]
    hx, hy, hz = [float(v) * 0.5 for v in envelope_mm]
    scores = {
        '+X': hx - px,
        '-X': hx + px,
        '+Y': hy - py,
        '-Y': hy + py,
        '+Z': hz - pz,
        '-Z': hz + pz,
    }
    return min(scores.items(), key=lambda item: item[1])[0]


def face_vector(face):
    return {
        '+X': (1.0, 0.0, 0.0),
        '-X': (-1.0, 0.0, 0.0),
        '+Y': (0.0, 1.0, 0.0),
        '-Y': (0.0, -1.0, 0.0),
        '+Z': (0.0, 0.0, 1.0),
        '-Z': (0.0, 0.0, -1.0),
    }[face]


def create_payload_lens(component, envelope_mm, collection):
    dims = [mm_to_m(v) for v in component['dimensions_mm']]
    pos = vec_mm_to_m(component['position_mm'])
    face = nearest_face(component['position_mm'], envelope_mm)
    direction = face_vector(face)
    radius = max(min(dims[0], dims[1]) * 0.18, 0.015)
    depth = max(min(dims[2] * 0.28, 0.08), 0.03)
    offset = (
        direction[0] * (dims[0] * 0.5 + depth * 0.35),
        direction[1] * (dims[1] * 0.5 + depth * 0.35),
        direction[2] * (dims[2] * 0.5 + depth * 0.35),
    )
    location = (pos[0] + offset[0], pos[1] + offset[1], pos[2] + offset[2])
    rotation = {
        '+X': (0.0, math.radians(90.0), 0.0),
        '-X': (0.0, math.radians(90.0), 0.0),
        '+Y': (math.radians(90.0), 0.0, 0.0),
        '-Y': (math.radians(90.0), 0.0, 0.0),
        '+Z': (0.0, 0.0, 0.0),
        '-Z': (0.0, 0.0, 0.0),
    }[face]
    obj = create_cylinder(component['id'] + '_lens', radius, depth, location, rotation, 'glass_lens')
    move_object_to_collection(obj, collection)


def create_radiator_fins(component, envelope_mm, collection):
    dims = [mm_to_m(v) for v in component['dimensions_mm']]
    pos = vec_mm_to_m(component['position_mm'])
    face = nearest_face(component['position_mm'], envelope_mm)
    direction = face_vector(face)
    fin_count = 6
    span_a = max(dims[0], dims[1], dims[2]) * 0.75
    fin_depth = max(min(dims[2] * 0.8, 0.025), 0.008)
    fin_thickness = max(min(min(dims[0], dims[1]) * 0.08, 0.004), 0.002)
    for index in range(fin_count):
        offset_axis = (-span_a * 0.5) + (index * span_a / max(fin_count - 1, 1))
        if abs(direction[0]) == 1.0:
            location = (pos[0] + direction[0] * (dims[0] * 0.5 + fin_depth * 0.5), pos[1] + offset_axis, pos[2])
            size = (fin_depth, fin_thickness, dims[2] * 0.9)
        elif abs(direction[1]) == 1.0:
            location = (pos[0] + offset_axis, pos[1] + direction[1] * (dims[1] * 0.5 + fin_depth * 0.5), pos[2])
            size = (fin_thickness, fin_depth, dims[2] * 0.9)
        else:
            location = (pos[0] + offset_axis, pos[1], pos[2] + direction[2] * (dims[2] * 0.5 + fin_depth * 0.5))
            size = (fin_thickness, dims[1] * 0.9, fin_depth)
        obj = create_cube(component['id'] + '_fin_' + str(index + 1), size, location, material_hint='brushed_aluminum')
        move_object_to_collection(obj, collection)


def create_component(component, envelope_mm, internal_collection, external_collection):
    dims = [mm_to_m(v) for v in component['dimensions_mm']]
    pos = vec_mm_to_m(component['position_mm'])
    rotation = radians(component.get('rotation_deg') or (0.0, 0.0, 0.0))
    material_hint = component.get('material_hint') or 'spacecraft_gray'
    role = component.get('render_role') or 'generic_box'
    collection = external_collection if component.get('is_external') else internal_collection

    if component.get('envelope_type') == 'cylinder':
        radius = max(dims[0], dims[1]) * 0.5
        obj = create_cylinder(component['id'], radius, dims[2], pos, rotation, material_hint)
    else:
        obj = create_cube(component['id'], dims, pos, rotation, material_hint)
    move_object_to_collection(obj, collection)

    if role == 'payload_optics':
        create_payload_lens(component, envelope_mm, external_collection)
    if role == 'radiator_panel':
        create_radiator_fins(component, envelope_mm, external_collection)


def create_solar_wings(envelope_mm, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    wing_span = max(sx, sy) * 0.95
    wing_length = max(sz * 0.85, 0.24)
    wing_thickness = max(min(sz * 0.04, 0.01), 0.003)
    offset = sx * 0.5 + wing_span * 0.5 + 0.04
    boom_length = max(wing_span * 0.18, 0.05)

    for side, sign in (('Port', -1.0), ('Starboard', 1.0)):
        panel = create_cube(side + '_SolarWing', (wing_span, wing_thickness, wing_length), (sign * offset, 0.0, 0.0), material_hint='solar_panel_blue')
        move_object_to_collection(panel, collection)
        boom_center_x = sign * (sx * 0.5 + boom_length * 0.5 + 0.018)
        boom = create_cube(side + '_SolarWing_Boom', (boom_length, wing_thickness * 0.8, wing_thickness * 1.35), (boom_center_x, 0.0, 0.0), material_hint='brushed_aluminum')
        move_object_to_collection(boom, collection)


def create_antenna(envelope_mm, payload_face, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    direction = face_vector(payload_face)
    base_offset = (
        direction[0] * (sx * 0.5 + 0.035),
        direction[1] * (sy * 0.5 + 0.035),
        direction[2] * (sz * 0.5 + 0.035),
    )
    rotation = (math.radians(90.0), 0.0, 0.0) if abs(direction[1]) == 1.0 else (0.0, math.radians(90.0), 0.0) if abs(direction[0]) == 1.0 else (0.0, 0.0, 0.0)
    stem = create_cylinder('Payload_Antenna_Stem', 0.008, 0.06, base_offset, rotation, 'gold_foil', 32)
    move_object_to_collection(stem, collection)
    dish_location = (
        base_offset[0] + direction[0] * 0.045,
        base_offset[1] + direction[1] * 0.045,
        base_offset[2] + direction[2] * 0.045,
    )
    dish = create_cylinder('Payload_Antenna_Dish', 0.028, 0.01, dish_location, rotation, 'gold_foil', 48)
    move_object_to_collection(dish, collection)


def setup_world():
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1440
    scene.render.resolution_percentage = 100
    try:
        scene.render.engine = RENDER_ENGINE
    except Exception:
        try:
            scene.render.engine = 'BLENDER_EEVEE'
        except Exception:
            scene.render.engine = 'CYCLES'
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new('World')
        bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get('Background')
    if background is not None:
        background.inputs[0].default_value = (0.016, 0.020, 0.026, 1.0)
        background.inputs[1].default_value = 0.35
    if hasattr(scene, 'view_settings'):
        scene.view_settings.exposure = -1.15


def setup_lights_and_camera(envelope_mm):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    max_dim = max(sx, sy, sz)
    dist = max_dim * 3.0

    bpy.ops.object.light_add(type='AREA', location=(dist, -dist, dist * 1.2))
    key_light = bpy.context.active_object
    key_light.data.energy = 480.0
    key_light.data.shape = 'RECTANGLE'
    key_light.data.size = max_dim * 2.2
    key_light.data.size_y = max_dim * 1.4

    bpy.ops.object.light_add(type='AREA', location=(-dist * 0.7, dist * 0.6, dist * 0.8))
    fill_light = bpy.context.active_object
    fill_light.data.energy = 160.0
    fill_light.data.size = max_dim * 1.8

    bpy.ops.object.light_add(type='SUN', location=(0.0, 0.0, dist * 2.0))
    sun = bpy.context.active_object
    sun.data.energy = 0.75
    sun.rotation_euler = (math.radians(45.0), 0.0, math.radians(-35.0))

    bpy.ops.object.camera_add(location=(dist * 1.45, -dist * 1.28, dist * 0.92))
    camera = bpy.context.active_object
    camera.name = 'MsGalaxyCamera'
    camera.data.lens = 55
    constraint = camera.constraints.new(type='TRACK_TO')
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0.0, 0.0, 0.0))
    target = bpy.context.active_object
    target.name = 'MsGalaxyCameraTarget'
    constraint.target = target
    constraint.track_axis = 'TRACK_NEGATIVE_Z'
    constraint.up_axis = 'UP_Y'
    bpy.context.scene.camera = camera


def create_ground(envelope_mm, collection):
    sx, sy, sz = [mm_to_m(v) for v in envelope_mm]
    size = max(sx, sy, sz) * 5.5
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0.0, 0.0, -sz * 0.75))
    plane = bpy.context.active_object
    plane.name = 'GroundPlane'
    plane.data.materials.clear()
    plane.data.materials.append(material_for_hint('ground_plane'))
    move_object_to_collection(plane, collection)


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


def main():
    bundle = json.loads(Path(BUNDLE_PATH).read_text(encoding='utf-8'))
    envelope_mm = bundle['envelope']['outer_size_mm']
    clear_scene()
    setup_world()

    shell_collection = ensure_collection('MsGalaxy_Shell')
    internal_collection = ensure_collection('MsGalaxy_Internal')
    external_collection = ensure_collection('MsGalaxy_External')
    support_collection = ensure_collection('MsGalaxy_Support')

    create_ground(envelope_mm, support_collection)
    create_bus_shell(envelope_mm, shell_collection)

    for component in bundle.get('components', []):
        create_component(component, envelope_mm, internal_collection, external_collection)

    heuristics = bundle.get('heuristics', {})
    if heuristics.get('enable_solar_wings'):
        create_solar_wings(envelope_mm, external_collection)
    if heuristics.get('enable_payload_lens'):
        create_antenna(envelope_mm, heuristics.get('payload_face', '+Z'), external_collection)

    setup_lights_and_camera(envelope_mm)
    save_outputs()
    print(json.dumps({
        'status': 'success',
        'bundle_path': BUNDLE_PATH,
        'output_image': OUTPUT_IMAGE,
        'output_blend': OUTPUT_BLEND,
        'profile_name': PROFILE_NAME,
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
