"""Voxel sheet: turns composed 2D pixel sprites into a lit sheet of thin voxels (HD-2D style depth).

Every opaque sprite pixel becomes a box 1 x 1 (front face) x `thickness` deep (default 0.25). With
`relief` > 0, layers nearer the viewer (hair, face, hands, weapons) step forward by `relief` per depth
tier, so the sprite reads as stacked cut-outs. Rendered in Cycles with a warm key light from the top
left and a cool rim light, from a slight angle so edges and steps catch the light.

    blender -b -P render_voxel_sheet.py -- --jobs jobs.json
jobs.json: [{"color": "a.png", "depth": "a_depth.png", "out": "a_vox.png", "camera": "battle"|"board"|"front"}]
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402

CAMERAS = {  # (yaw degrees around the sprite, pitch degrees down)
    "front": (0, 4),
    "battle": (-28, 12),
    "board": (-20, 32),
}


def load_rgba(path, srgb=True):
    img = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(h, w, 4)[::-1]  # top row first


def build(color, depth, thickness, relief, tiers):
    h, w = color.shape[:2]
    bm = bmesh.new()
    col_layer = bm.faces.layers.float_color.new("col")
    lin = np.where(color[..., :3] <= 0.04045, color[..., :3] / 12.92, ((color[..., :3] + 0.055) / 1.055) ** 2.4)
    d = depth[..., 0] * 255
    opaque = color[..., 3] > 0.5
    # depth tiers: nearest layers come forward (data depth: 0 near .. 255 far)
    levels = np.unique(np.round(d[opaque] / 255 * tiers)) if opaque.any() else []
    for y in range(h):
        for x in range(w):
            if not opaque[y, x]:
                continue
            tier = tiers - round(d[y, x] / 255 * tiers)
            z0 = tier * relief
            c = (*lin[y, x], 1.0)
            x0, x1 = x - w / 2, x + 1 - w / 2
            y0, y1 = (h - y - 1), (h - y)
            # front face at -Y (towards camera), depth goes +Y
            vs = [bm.verts.new(v) for v in (
                (x0, -z0, y0), (x1, -z0, y0), (x1, -z0, y1), (x0, -z0, y1),
                (x0, -z0 + thickness, y0), (x1, -z0 + thickness, y0), (x1, -z0 + thickness, y1), (x0, -z0 + thickness, y1))]
            for f in ((0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0)):
                face = bm.faces.new([vs[i] for i in f])
                face[col_layer] = c
    me = bpy.data.meshes.new("sheet")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("sheet", me)
    bpy.context.scene.collection.objects.link(ob)
    mat = bpy.data.materials.new("voxel")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = "GEOMETRY", "col"
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.55
    bsdf.inputs["Specular IOR Level"].default_value = 0.35
    # a little emission keeps the pixel colours true where the key light doesn't reach
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 0.35
    me.materials.append(mat)
    return ob, w, h


def lights(scene, w, h):
    def area(name, loc, energy, color, size):
        L = bpy.data.lights.new(name, "AREA")
        L.energy, L.color, L.size = energy, color, size
        o = bpy.data.objects.new(name, L)
        o.location = loc
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = (Vector((0, 0, h / 2)) - Vector(loc)).to_track_quat("-Z", "Y")
        scene.collection.objects.link(o)
    area("key", (-w * 0.9, -w * 1.2, h * 1.4), 9.0e4, (1.0, 0.86, 0.7), w * 0.6)
    area("rim", (w * 1.0, w * 0.9, h * 1.0), 5.0e4, (0.55, 0.7, 1.0), w * 0.4)
    area("fill", (w * 0.6, -w * 1.4, h * 0.3), 1.6e4, (0.85, 0.85, 1.0), w * 0.8)
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.045, 0.07, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6


def camera(scene, cam_name, w, h, res):
    yaw, pitch = CAMERAS[cam_name]
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(w, h) * 1.02
    y, p = math.radians(yaw), math.radians(pitch)
    d = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
    target = Vector((0, 0, h / 2))
    cam.location = target + d * (w * 3)
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (-d).to_track_quat("-Z", "Y")
    cam.data.clip_end = w * 10
    scene.render.resolution_x = scene.render.resolution_y = res


def main():
    a = hd.script_args()
    jobs = json.load(open(a["jobs"], encoding="utf-8"))
    for job in jobs:
        hd.clear_scene()
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "OPTIX"
        prefs.get_devices()
        for dv in prefs.devices:
            dv.use = dv.type != "CPU"
        scene.cycles.device = "GPU"
        scene.cycles.samples = int(job.get("samples", 48))
        scene.cycles.use_denoising = True
        scene.render.film_transparent = job.get("transparent", False)
        scene.view_settings.view_transform = "Standard"
        scene.world = bpy.data.worlds.new("w")
        scene.world.use_nodes = True
        color = load_rgba(job["color"])
        depth = load_rgba(job["depth"]) if job.get("depth") else np.zeros_like(color)
        ob, w, h = build(color, depth, float(job.get("thickness", 0.25)), float(job.get("relief", 0.0)),
                         int(job.get("tiers", 3)))
        lights(scene, w, h)
        camera(scene, job.get("camera", "battle"), w, h, int(job.get("res", 768)))
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.filepath = os.path.abspath(job["out"])
        bpy.ops.render.render(write_still=True)
        print(f"[voxsheet] {job['out']}", flush=True)


if __name__ == "__main__":
    main()
