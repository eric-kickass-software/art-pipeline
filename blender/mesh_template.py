"""Dungeon mesh pieces (archways, ...) -> GLB + an iso normal pass on the exact dungeon cell canvas.

    blender -b -P mesh_template.py -- --mesh archway --tile 64 --height 2 --length 1 --scale 8
        --thickness 0.25 --out <dir>   [--opening 0.56 --spring 1.0]

Writes <out>/<name>.glb and <out>/<name>.normal.png (world normal * 0.5 + 0.5, alpha = coverage), where
<name> = <mesh>_t<tile>_h<height>_l<length>. hdart/dungeon.py turns the normal pass into the img2img
template, the silhouette and the normal map. Piece space (see hdart/iso.py) -> Blender world:
(x, y, z_units) -> (x - 0.5, -(y - 0.5), z_units * KZ); one world unit is one floor cell.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

ELEV, AZIM = 30.0, 45.0
KZ = (math.sqrt(2) / 2) / math.cos(math.radians(ELEV))  # one height unit (tile/2 px) in world units


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {}
    for i in range(0, len(a), 2):
        out[a[i].lstrip("-")] = a[i + 1]
    return out


def W(x, y, z):
    return Vector((x - 0.5, -(y - 0.5), z * KZ))


def box(x0, x1, y0, y1, z0, z1):
    bm = bmesh.new()
    c = (W(x0, y0, z0) + W(x1, y1, z1)) / 2
    size = Vector((x1 - x0, y1 - y0, (z1 - z0) * KZ))
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(c) @ Matrix.Diagonal((*size, 1)))
    return obj(bm)


def cyl_y(cx, cz, r_world, y0, y1, seg=48):
    """Cylinder along piece y (Blender -y), centre (cx, cz units), radius in world units."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r_world, radius2=r_world, depth=y1 - y0,
                          matrix=Matrix.Translation(W(cx, (y0 + y1) / 2, cz)) @ Matrix.Rotation(math.radians(90), 4, "X"))
    return obj(bm)


def obj(bm):
    me = bpy.data.meshes.new("m")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("o", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def boolean(a, b, op):
    m = a.modifiers.new("b", "BOOLEAN")
    m.operation, m.object, m.solver = op, b, "EXACT"
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(b)
    return a


def archway(h, t, L, opening, spring):
    """A thin wall slab with an arched opening and a slightly thicker arch frame round it."""
    cx, ow = L / 2, opening
    r = ow / 2  # world units (x is 1:1)
    frame = 0.07
    slab = box(0, L, 0.5 - t / 2, 0.5 + t / 2, 0, h)
    ft = t + 0.08
    fr = boolean(box(cx - r - frame, cx + r + frame, 0.5 - ft / 2, 0.5 + ft / 2, 0, spring),
                 cyl_y(cx, spring, r + frame, 0.5 - ft / 2, 0.5 + ft / 2), "UNION")
    piece = boolean(slab, fr, "UNION")
    cut = boolean(box(cx - r, cx + r, 0.5 - ft, 0.5 + ft, -0.1, spring),
                  cyl_y(cx, spring, r, 0.5 - ft, 0.5 + ft), "UNION")
    return boolean(piece, cut, "DIFFERENCE")


def normal_material():
    mat = bpy.data.materials.new("normals")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mul = nt.nodes.new("ShaderNodeVectorMath")
    mul.operation = "MULTIPLY_ADD"
    mul.inputs[1].default_value = (0.5, 0.5, 0.5)
    mul.inputs[2].default_value = (0.5, 0.5, 0.5)
    em = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(geo.outputs["Normal"], mul.inputs[0])
    nt.links.new(mul.outputs[0], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return mat


def main():
    a = args()
    tile, h, L, S = int(a["tile"]), float(a["height"]), int(a.get("length", 1)), int(a.get("scale", 8))
    t = float(a.get("thickness", 0.25))
    name = f"{a['mesh']}_t{tile}_h{a['height']}_l{L}"
    out = os.path.abspath(a["out"])
    os.makedirs(out, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    if a["mesh"] == "archway":
        ob = archway(h, t, L, float(a.get("opening", 0.56)), float(a.get("spring", 1.0)))
    else:
        raise SystemExit(f"unknown mesh {a['mesh']}")
    for f in ob.data.polygons:
        f.use_smooth = False
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(out, name + ".glb"), use_selection=True)
    ob.data.materials.clear()  # booleans leave an empty slot behind
    ob.data.materials.append(normal_material())
    for f in ob.data.polygons:
        f.material_index = 0

    # canvas = the run canvas of hdart/iso.py at scale S
    Wc_cell = tile * S
    H_cell = round((tile / 2 + h * tile / 2) * S)
    Wc, Hc = Wc_cell + (L - 1) * Wc_cell // 2, H_cell + (L - 1) * Wc_cell // 4
    ppu = Wc_cell / math.sqrt(2)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(Wc, Hc) / ppu
    el, az = math.radians(ELEV), math.radians(AZIM)
    d = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-d).to_track_quat("-Z", "Y")
    right = cam.rotation_quaternion @ Vector((1, 0, 0))
    up = cam.rotation_quaternion @ Vector((0, 1, 0))
    # piece origin (0, 0, 0) must land on pixel (Wc_cell/2, H_cell - Wc_cell/2) of the canvas
    dx = Wc_cell / 2 - Wc / 2
    dy_down = (H_cell - Wc_cell / 2) - Hc / 2
    origin = W(0, 0, 0)
    target = origin - right * (dx / ppu) + up * (dy_down / ppu)
    cam.location = target + d * 20
    cam.data.clip_start, cam.data.clip_end = 0.1, 60

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.pixel_filter_type = "BOX"
    scene.cycles.filter_width = 0.01
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y = Wc, Hc
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Raw"  # normals must stay linear
    scene.view_settings.look = "None"
    s = scene.render.image_settings
    s.file_format, s.color_mode, s.color_depth = "PNG", "RGBA", "8"
    scene.render.filepath = os.path.join(out, name + ".normal.png")
    bpy.ops.render.render(write_still=True)
    print(f"[mesh] {name} {Wc}x{Hc}", flush=True)


main()
