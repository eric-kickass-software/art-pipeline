"""Renders a GLB (TRELLIS.2 output or any model) into pixel-art sprites: monsters, props, items.

    blender -b -P render_mesh.py -- --glb work/meshes/x.glb --out P:/Projects/Unity/Hearthdelve/Assets/Art/Monsters/x
        [--height 1.8] [--views battle,iso4,icon] [--cell 64] [--yaw 0] [--colors 24] [--faces 30000]

Views:  battle  side view, facing RIGHT (enemies stand on the left of the FF3-style screen)
        iso4    four isometric directions (board tokens for bosses/lairs, props)
        icon    3/4 view for inventory icons
Writes <name>.<view>.png (albedo with soft baked light, palette-reduced), <name>.<view>.normal.png and
<name>.json (cell size, pivots, frame list). The model's front is assumed to face -Y after import;
pass --yaw to turn it.
"""
import math
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402
from render_tiles import kmeans_palette  # noqa: E402

VIEWS = {
    "battle": {"elev": 8, "az": 0, "yaws": [50]},
    "iso4": {"elev": 30, "az": 45, "yaws": [0, 90, 180, 270]},
    "icon": {"elev": 25, "az": 30, "yaws": [0]},
}


def import_glb(path, height, faces):
    hd.clear_scene()
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(ob.data.polygons) > faces:
        dec = ob.modifiers.new("dec", "DECIMATE")
        dec.ratio = faces / len(ob.data.polygons)
        bpy.ops.object.modifier_apply(modifier=dec.name)
    # normalise: feet on z=0, centred, given height
    pts = [ob.matrix_world @ v.co for v in ob.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    s = height / max(hi.z - lo.z, 1e-6)
    for v in ob.data.vertices:
        p = ob.matrix_world @ v.co
        v.co = Vector(((p.x - (lo.x + hi.x) / 2) * s, (p.y - (lo.y + hi.y) / 2) * s, (p.z - lo.z) * s))
    ob.matrix_world.identity()
    ob.rotation_mode = "XYZ"  # the glTF importer leaves objects in quaternion mode
    for poly in ob.data.polygons:
        poly.use_smooth = True
    return ob, max(hi.x - lo.x, hi.y - lo.y) * s


def vertex_color_material(ob):
    mat = hd.hd_material("hd.mesh", 0)
    nt = mat.node_tree
    grp = next(n for n in nt.nodes if n.type == "GROUP")
    attrs = ob.data.color_attributes
    if len(attrs):
        ca = nt.nodes.new("ShaderNodeAttribute")  # generic lookup also works after voxelizing
        ca.attribute_type, ca.attribute_name = "GEOMETRY", attrs[0].name
        nt.links.new(ca.outputs["Color"], grp.inputs["Color"])
    else:  # textured GLB: take the base colour texture from the imported material
        src = ob.data.materials[0] if ob.data.materials else None
        tex = next((n for n in src.node_tree.nodes if n.type == "TEX_IMAGE"), None) if src and src.node_tree else None
        if tex is not None:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = tex.image
            uv = nt.nodes.new("ShaderNodeUVMap")
            nt.links.new(uv.outputs["UV"], t.inputs["Vector"])
            nt.links.new(t.outputs["Color"], grp.inputs["Color"])
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    nt.links.new(geo.outputs["Normal"], grp.inputs["Normal"])
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def main():
    a = hd.script_args()
    name = a.get("name") or os.path.splitext(os.path.basename(a["glb"]))[0]
    out = os.path.abspath(a["out"])
    height = float(a.get("height", 1.8))
    cell = int(a.get("cell", 64))
    ss = int(a.get("supersample", 4))
    yaw0 = float(a.get("yaw", 0))
    views = a.get("views", "battle,iso4,icon").split(",")
    ob, width = import_glb(a["glb"], height, int(a.get("faces", 30000)))
    vertex_color_material(ob)
    voxel = float(a.get("voxel", 0))
    if voxel > 0:  # same "pixels come to life" cubes as the characters
        hd.voxelize_objects([ob], voxel)
    sat = float(a.get("saturation", 1.0))
    scene = bpy.context.scene
    hd.setup_render(scene, cell, cell, ss)
    hd.set_group_value("DEPTH_RANGE", 4.0)
    tmp = os.path.join(tempfile.mkdtemp(prefix="hdmesh_"), "f.exr")
    ortho = max(height, width) * 1.25
    frames = []
    for vname in views:
        v = VIEWS[vname]
        hd.ortho_camera(scene, ortho, v["elev"], v["az"], (0, 0, height * 0.5))
        for yaw in v["yaws"]:
            ob.rotation_euler = (0, 0, math.radians(yaw + yaw0))
            hd.set_mode(hd.MODE_ALBEDO)
            a_hi = hd.render_exr(scene, tmp)
            hd.set_mode(hd.MODE_NORMAL)
            n_hi = hd.render_exr(scene, tmp)
            alpha, _, _, _, nrm = hd.reduce_frame(np.dstack([np.zeros_like(a_hi[..., :3]), a_hi[..., 3:]]), n_hi, ss)
            blocks = hd._blocks(a_hi, ss)
            cov = blocks[..., 3] > 0.5
            col = (blocks[..., :3] * cov[..., None]).sum(axis=2) / np.maximum(cov.sum(axis=2), 1)[..., None]
            shade = hd.form_shade(nrm, [-0.45, 0.5, 0.74], 0.55)
            col = np.clip(col * shade[..., None] * 1.15, 0, 1) ** (1 / 2.2)
            if sat != 1.0:  # punchier, icon-style colour (reference: 32x32 monster icons)
                grey = col.mean(axis=-1, keepdims=True)
                col = np.clip(grey + (col - grey) * sat, 0, 1)
            frames.append((vname, yaw, col, alpha, nrm))
            print(f"[mesh] {name} {vname} yaw {yaw}", flush=True)
    pal = kmeans_palette(np.concatenate([c[al] for _, _, c, al, _ in frames]), int(a.get("colors", 24)))
    meta = {"name": name, "cell": [cell, cell], "height": height, "views": {},
            "pivot": [0.5, round(0.5 - height * 0.5 * math.cos(math.radians(8)) / ortho, 4)]}  # feet, battle view
    for vname in views:
        fr = [f for f in frames if f[0] == vname]
        sheet = np.zeros((cell, cell * len(fr), 4), np.uint8)
        nsheet = np.zeros_like(sheet)
        for i, (_, yaw, col, alpha, nrm) in enumerate(fr):
            d = ((col[:, :, None, :] - pal[None, None]) ** 2).sum(-1)
            q = pal[d.argmin(-1)]
            sheet[:, i * cell:(i + 1) * cell, :3] = np.clip(np.rint(q * 255), 0, 255) * alpha[..., None]
            sheet[:, i * cell:(i + 1) * cell, 3] = alpha * 255
            if a.get("outline"):  # 1 px dark outline around the silhouette
                edge = np.zeros_like(alpha)
                for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    edge |= np.roll(alpha, (dy, dx), axis=(0, 1))
                edge &= ~alpha
                sheet[:, i * cell:(i + 1) * cell][edge] = (27, 20, 33, 255)
            nsheet[:, i * cell:(i + 1) * cell] = hd.encode_normal(nrm, alpha)
        hd.write_png(os.path.join(out, f"{name}.{vname}.png"), sheet)
        hd.write_png(os.path.join(out, f"{name}.{vname}.normal.png"), nsheet)
        meta["views"][vname] = {"frames": len(fr), "yaws": [f[1] for f in fr]}
    hd.write_json(os.path.join(out, f"{name}.json"), meta)
    print(f"[mesh] done -> {out}")


if __name__ == "__main__":
    main()
