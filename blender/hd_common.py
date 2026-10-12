"""Shared helpers for the Hearthdelve Blender scripts (run inside Blender 5.2+).

The core trick: every material ends in the HD_Output node group, which emits *data* instead of
lit colour. One global MODE value switches all materials at once between:

    MODE 0  "data"    R = detail (texture luminance), G = channel id (raw float), B = depth 0..1
    MODE 1  "normal"  RGB = camera-space normal * 0.5 + 0.5 (x right, y up, z towards camera)
    MODE 2  "albedo"  RGB = the material's colour input (tiles, props, VFX)

Renders are Cycles, 1 sample, no pixel filter, emission only, saved as float EXR, so the values
come back exactly. Each frame is rendered at `supersample` x the target size and reduced here
with coverage/majority rules, which gives clean pixel-art silhouettes.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

MODE_DATA, MODE_NORMAL, MODE_ALBEDO = 0, 1, 2
GROUP = "HD_Output"


# ----------------------------------------------------------------------------- args / spec

def script_args():
    """Arguments after `--` on the blender command line, as a dict of --key value pairs."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out, key = {}, None
    for a in argv:
        if a.startswith("--"):
            key = a[2:].replace("-", "_")
            out[key] = True
        elif key:
            out[key] = a if out[key] is True else f"{out[key]} {a}"
    return out


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def channel_ids(spec):
    return {c["name"]: c["id"] for c in spec["channels"]}


# ----------------------------------------------------------------------------- scene

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(scene, width, height, supersample=1):
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type != "CPU"]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type != "CPU"
                scene.cycles.device = "GPU"
                break
        except TypeError:
            continue
    c = scene.cycles
    c.samples = 1
    c.use_adaptive_sampling = False
    c.use_denoising = False
    c.max_bounces = 0
    c.diffuse_bounces = c.glossy_bounces = c.transmission_bounces = c.volume_bounces = 0
    c.transparent_max_bounces = 8
    c.pixel_filter_type = "BOX"
    c.filter_width = 0.01
    c.seed = 0
    scene.render.film_transparent = True
    scene.render.resolution_x = width * supersample
    scene.render.resolution_y = height * supersample
    scene.render.resolution_percentage = 100
    scene.render.use_persistent_data = True
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    s = scene.render.image_settings
    s.file_format = "OPEN_EXR"
    s.color_mode = "RGBA"
    s.color_depth = "32"
    s.exr_codec = "ZIP"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Strength"].default_value = 0.0


def ortho_camera(scene, ortho_scale, elevation_deg, azimuth_deg, target, distance=10.0):
    cam = scene.camera
    if cam is None:
        cam = bpy.data.objects.new("HD_Camera", bpy.data.cameras.new("HD_Camera"))
        scene.collection.objects.link(cam)
        scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = ortho_scale
    cam.data.clip_start = 0.05
    cam.data.clip_end = distance * 4
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    # azimuth 0 = camera on the -Y side looking +Y (front view); positive azimuth swings it towards +X
    d = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    t = Vector(target)
    cam.location = t + d * distance
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-d).to_track_quat("-Z", "Y")
    set_group_value("CAM_DIST", distance)
    return cam


# ----------------------------------------------------------------------------- materials

_fresh = False


def _group():
    """The HD_Output group. Rebuilt once per process, so a .blend saved by an older script
    version always renders with the current encoding."""
    global _fresh
    g = bpy.data.node_groups.get(GROUP)
    if g and _fresh:
        return g
    if g:
        g.name = GROUP + "_old"
    _fresh = True
    g = bpy.data.node_groups.new(GROUP, "ShaderNodeTree")
    io = g.interface
    io.new_socket("Channel", in_out="INPUT", socket_type="NodeSocketFloat")
    io.new_socket("Detail", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 1.0
    io.new_socket("Normal", in_out="INPUT", socket_type="NodeSocketVector")
    io.new_socket("Color", in_out="INPUT", socket_type="NodeSocketColor").default_value = (0.5, 0.5, 0.5, 1)
    io.new_socket("Alpha", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 1.0
    io.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    n, l = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")

    def value(name, v):
        node = n.new("ShaderNodeValue")
        node.name = node.label = name
        node.outputs[0].default_value = v
        return node

    mode = value("MODE", 0.0)
    cam_dist = value("CAM_DIST", 10.0)
    depth_range = value("DEPTH_RANGE", 2.4)

    # depth: camera-space distance along the view axis, centred on the target, mapped to 0..1
    geo = n.new("ShaderNodeNewGeometry")
    vt_pos = n.new("ShaderNodeVectorTransform")
    vt_pos.vector_type, vt_pos.convert_from, vt_pos.convert_to = "POINT", "WORLD", "CAMERA"
    l.new(geo.outputs["Position"], vt_pos.inputs[0])
    sep = n.new("ShaderNodeSeparateXYZ")
    l.new(vt_pos.outputs[0], sep.inputs[0])
    # Cycles camera space: x right, y up, +z INTO the scene, so z is already the view distance
    neg = n.new("ShaderNodeMath"); neg.operation = "MULTIPLY"; neg.inputs[1].default_value = 1
    l.new(sep.outputs["Z"], neg.inputs[0])
    sub = n.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    l.new(neg.outputs[0], sub.inputs[0]); l.new(cam_dist.outputs[0], sub.inputs[1])
    div = n.new("ShaderNodeMath"); div.operation = "DIVIDE"
    l.new(sub.outputs[0], div.inputs[0]); l.new(depth_range.outputs[0], div.inputs[1])
    depth = n.new("ShaderNodeMath"); depth.operation = "ADD"; depth.inputs[1].default_value = 0.5
    depth.use_clamp = True
    l.new(div.outputs[0], depth.inputs[0])

    data = n.new("ShaderNodeCombineXYZ")
    l.new(gi.outputs["Detail"], data.inputs[0])
    l.new(gi.outputs["Channel"], data.inputs[1])
    l.new(depth.outputs[0], data.inputs[2])

    vt_n = n.new("ShaderNodeVectorTransform")
    vt_n.vector_type, vt_n.convert_from, vt_n.convert_to = "NORMAL", "WORLD", "CAMERA"
    l.new(gi.outputs["Normal"], vt_n.inputs[0])
    flip = n.new("ShaderNodeVectorMath"); flip.operation = "MULTIPLY"
    flip.inputs[1].default_value = (1, 1, -1)  # make +z point at the viewer (Unity sprite normals)
    l.new(vt_n.outputs[0], flip.inputs[0])
    norm = n.new("ShaderNodeVectorMath"); norm.operation = "NORMALIZE"
    l.new(flip.outputs[0], norm.inputs[0])
    enc = n.new("ShaderNodeVectorMath"); enc.operation = "MULTIPLY_ADD"
    enc.inputs[1].default_value = (0.5, 0.5, 0.5); enc.inputs[2].default_value = (0.5, 0.5, 0.5)
    l.new(norm.outputs[0], enc.inputs[0])

    def is_mode(k):
        c = n.new("ShaderNodeMath"); c.operation = "COMPARE"
        c.inputs[1].default_value = k; c.inputs[2].default_value = 0.1
        l.new(mode.outputs[0], c.inputs[0])
        return c

    mix1 = n.new("ShaderNodeMix"); mix1.data_type = "RGBA"
    l.new(is_mode(MODE_NORMAL).outputs[0], mix1.inputs["Factor"])
    l.new(data.outputs[0], mix1.inputs["A"]); l.new(enc.outputs[0], mix1.inputs["B"])
    mix2 = n.new("ShaderNodeMix"); mix2.data_type = "RGBA"
    l.new(is_mode(MODE_ALBEDO).outputs[0], mix2.inputs["Factor"])
    l.new(mix1.outputs["Result"], mix2.inputs["A"]); l.new(gi.outputs["Color"], mix2.inputs["B"])

    emit = n.new("ShaderNodeEmission")
    l.new(mix2.outputs["Result"], emit.inputs["Color"])
    transp = n.new("ShaderNodeBsdfTransparent")
    mixs = n.new("ShaderNodeMixShader")
    l.new(gi.outputs["Alpha"], mixs.inputs[0])
    l.new(transp.outputs[0], mixs.inputs[1]); l.new(emit.outputs[0], mixs.inputs[2])
    l.new(mixs.outputs[0], go.inputs["Shader"])
    old = bpy.data.node_groups.get(GROUP + "_old")
    if old:
        for m in bpy.data.materials:
            if m.node_tree:
                for node in m.node_tree.nodes:
                    if node.type == "GROUP" and node.node_tree == old:
                        node.node_tree = g
        bpy.data.node_groups.remove(old)
    return g


def set_group_value(name, v):
    _group().nodes[name].outputs[0].default_value = v


def set_mode(mode):
    set_group_value("MODE", float(mode))


def hd_material(name, channel_id, texture=None, tex_scale=3.0, bump=0.35, color=(0.5, 0.5, 0.5, 1),
                detail_range=(0.84, 1.0)):
    """A material ending in HD_Output. `texture` (path) feeds detail, bump and albedo through box
    projection, so parts need no UVs. Without a texture a procedural noise stands in."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    n, l = nt.nodes, nt.links
    out = n.new("ShaderNodeOutputMaterial")
    grp = n.new("ShaderNodeGroup"); grp.node_tree = _group()
    grp.inputs["Channel"].default_value = float(channel_id)
    grp.inputs["Color"].default_value = color
    l.new(grp.outputs[0], out.inputs["Surface"])
    tc = n.new("ShaderNodeTexCoord")
    mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (tex_scale,) * 3
    l.new(tc.outputs["Object"], mp.inputs["Vector"])
    if texture and os.path.exists(texture):
        tex = n.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(os.path.abspath(texture), check_existing=True)
        tex.projection, tex.projection_blend = "BOX", 0.25
        tex.interpolation = "Closest"
        l.new(mp.outputs[0], tex.inputs[0])
        src_col = tex.outputs["Color"]
        l.new(src_col, grp.inputs["Color"])
    else:
        tex = n.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = 4.0
        tex.inputs["Detail"].default_value = 6.0
        l.new(mp.outputs[0], tex.inputs["Vector"])
        src_col = tex.outputs["Color"]
    bw = n.new("ShaderNodeRGBToBW"); l.new(src_col, bw.inputs[0])
    rng = n.new("ShaderNodeMapRange")
    rng.inputs["To Min"].default_value, rng.inputs["To Max"].default_value = detail_range
    l.new(bw.outputs[0], rng.inputs["Value"]); l.new(rng.outputs[0], grp.inputs["Detail"])
    bmp = n.new("ShaderNodeBump"); bmp.inputs["Strength"].default_value = bump
    bmp.inputs["Distance"].default_value = 0.02
    l.new(bw.outputs[0], bmp.inputs["Height"]); l.new(bmp.outputs["Normal"], grp.inputs["Normal"])
    return mat


# ----------------------------------------------------------------------------- voxelize

VOXEL_GROUP = "HD_Voxelize"


def voxel_group(size):
    """Geometry Nodes: surface -> grid-snapped cubes ("pixels come to life"). Works on open meshes
    (hair caps, capes): points are scattered on the faces, snapped to a grid of `size`, de-duplicated,
    and each becomes a cube that keeps its face's material index (so the colour channel survives)."""
    g = bpy.data.node_groups.get(VOXEL_GROUP)
    if g:
        bpy.data.node_groups.remove(g)
    g = bpy.data.node_groups.new(VOXEL_GROUP, "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, l = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    mi = n.new("GeometryNodeInputNamedAttribute"); mi.data_type = "INT"; mi.inputs["Name"].default_value = "material_index"
    store = n.new("GeometryNodeStoreNamedAttribute"); store.data_type = "INT"; store.domain = "FACE"
    store.inputs["Name"].default_value = "hd_mat"
    l.new(gi.outputs[0], store.inputs["Geometry"]); l.new(mi.outputs["Attribute"], store.inputs["Value"])
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.inputs["Density"].default_value = 6.0 / (size * size)
    l.new(store.outputs["Geometry"], dist.inputs["Mesh"])
    pos = n.new("GeometryNodeInputPosition")
    half = n.new("ShaderNodeVectorMath"); half.operation = "ADD"; half.inputs[1].default_value = (size / 2,) * 3
    l.new(pos.outputs[0], half.inputs[0])
    snap = n.new("ShaderNodeVectorMath"); snap.operation = "SNAP"; snap.inputs[1].default_value = (size,) * 3
    l.new(half.outputs[0], snap.inputs[0])
    setp = n.new("GeometryNodeSetPosition")
    l.new(dist.outputs["Points"], setp.inputs["Geometry"]); l.new(snap.outputs[0], setp.inputs["Position"])
    merge = n.new("GeometryNodeMergeByDistance"); merge.inputs["Distance"].default_value = size * 0.3
    l.new(setp.outputs["Geometry"], merge.inputs["Geometry"])
    cube = n.new("GeometryNodeMeshCube"); cube.inputs["Size"].default_value = (size,) * 3
    inst = n.new("GeometryNodeInstanceOnPoints")
    l.new(merge.outputs["Geometry"], inst.inputs["Points"]); l.new(cube.outputs["Mesh"], inst.inputs["Instance"])
    real = n.new("GeometryNodeRealizeInstances")
    l.new(inst.outputs["Instances"], real.inputs["Geometry"])
    ma = n.new("GeometryNodeInputNamedAttribute"); ma.data_type = "INT"; ma.inputs["Name"].default_value = "hd_mat"
    setm = n.new("GeometryNodeSetMaterialIndex")
    l.new(real.outputs["Geometry"], setm.inputs["Geometry"]); l.new(ma.outputs["Attribute"], setm.inputs["Material Index"])
    l.new(setm.outputs["Geometry"], go.inputs[0])
    return g


def voxelize_objects(objects, size):
    g = voxel_group(size)
    for ob in objects:
        # realized cubes carry no material list, so bind the materials to the object's slots instead
        mats = list(ob.data.materials)
        for slot, mat in zip(ob.material_slots, mats):
            slot.link = "OBJECT"
            slot.material = mat
        if not any(m.type == "NODES" and m.node_group == g for m in ob.modifiers):
            ob.modifiers.new("voxelize", "NODES").node_group = g  # after the Armature modifier


# ----------------------------------------------------------------------------- render / read / reduce

def render_exr(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    bpy.data.images.remove(img)
    return buf.reshape(h, w, 4)[::-1].copy()  # top row first


def _blocks(a, ss):
    h, w = a.shape[0] // ss, a.shape[1] // ss
    return a.reshape(h, ss, w, ss, *a.shape[2:]).swapaxes(1, 2).reshape(h, w, ss * ss, *a.shape[2:])


def reduce_frame(data_hi, normal_hi, ss, max_channel=16, coverage=0.45):
    """Supersampled data+normal renders -> one pixel-art frame.
    Returns (alpha bool HxW, detail HxW, channel int HxW, depth HxW, normal HxWx3)."""
    a = _blocks(data_hi[..., 3] > 0.5, ss)
    cover = a.mean(axis=2)
    alpha = cover >= coverage
    cnt = np.maximum(a.sum(axis=2), 1)
    d = _blocks(data_hi[..., :3], ss)
    detail = (d[..., 0] * a).sum(axis=2) / cnt
    ch_hi = np.rint(d[..., 1]).astype(np.int32).clip(0, max_channel - 1)
    votes = np.stack([((ch_hi == k) & a).sum(axis=2) for k in range(max_channel)], axis=-1)
    channel = votes.argmax(axis=-1)
    depth = np.where(a, d[..., 2], 9.0).min(axis=2)
    nrm = _blocks(normal_hi[..., :3] * 2 - 1, ss)
    nrm = (nrm * a[..., None]).sum(axis=2)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-6)
    return alpha, detail, channel, np.where(alpha, depth, 1.0), nrm


def form_shade(normal, light_dir, ambient):
    l = np.array(light_dir, dtype=np.float32)
    l /= np.linalg.norm(l)
    lam = np.clip((normal * l).sum(axis=-1), 0, 1)
    return ambient + (1 - ambient) * lam


def write_png(path, rgba_u8):
    """rgba_u8: HxWx4 uint8, top row first."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h, w = rgba_u8.shape[:2]
    img = bpy.data.images.new("hd_out", w, h, alpha=True, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    img.pixels.foreach_set((rgba_u8[::-1].astype(np.float32) / 255.0).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def encode_normal(normal, alpha):
    out = np.zeros(normal.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.clip(np.rint((normal * 0.5 + 0.5) * 255), 0, 255)
    out[..., 3] = alpha * 255
    return out
