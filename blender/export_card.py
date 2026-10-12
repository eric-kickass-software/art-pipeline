"""Exports a double-sided voxel card (front sprite + back sprite) as an FBX for Unity.

    blender -b -P export_card.py -- --front f.png --back b.png --tiers-front tf.png --tiers-back tb.png
        --thickness 3 --relief 0.7 --ppu 32 --out witch_card.fbx --atlas witch_card_atlas.png

Each opaque pixel is a solid column: the front half reaches thickness/2 + tierF * relief towards the
viewer, the back half the same backwards with the back sprite (stored as the reverse side, so its column
for x is at 63 - x). One unit per pixel in Blender, scaled by 1/ppu on export. UVs point at the pixel
centres of a 2N x N atlas (front | back), so a point-filtered texture with URP Lit shows exact pixels.
Feet sit on y = 0, the card is centred on x = 0; the front faces Blender -Y.
"""
import os
import sys

import bmesh
import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402


def load(path):
    img = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(h, w, 4)[::-1]  # top row first


def main():
    a = hd.script_args()
    thick, relief, ppu = float(a.get("thickness", 3)), float(a.get("relief", 0.7)), float(a.get("ppu", 32))
    F, Bk = load(a["front"]), load(a["back"])
    TF = np.rint(load(a["tiers_front"])[..., 0] * 255).astype(int)
    TB = np.rint(load(a["tiers_back"])[..., 0] * 255).astype(int)
    n = F.shape[0]
    hd.clear_scene()
    # atlas: front | back
    atlas = np.zeros((n, 2 * n, 4), np.float32)
    atlas[:, :n] = F
    atlas[:, n:] = Bk
    img = bpy.data.images.new("atlas", 2 * n, n, alpha=True)
    img.pixels.foreach_set(atlas[::-1].ravel())
    img.filepath_raw = os.path.abspath(a["atlas"])
    img.file_format = "PNG"
    img.save()

    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    ys = np.nonzero(F[..., 3] > 0.5)[0]
    ground = ys.max() + 1 if len(ys) else n
    half = thick / 2

    def column(x, y, z0, z1, u, v):
        X0, X1 = x - n / 2, x + 1 - n / 2
        Y0, Y1 = ground - y - 1, ground - y
        # Blender: x right, z up, -y towards the viewer (front)
        vs = [bm.verts.new(p) for p in ((X0, -z1, Y0), (X1, -z1, Y0), (X1, -z1, Y1), (X0, -z1, Y1),
                                       (X0, -z0, Y0), (X1, -z0, Y0), (X1, -z0, Y1), (X0, -z0, Y1))]
        for f in ((0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7), (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0)):
            face = bm.faces.new([vs[i] for i in f])
            for loop in face.loops:
                loop[uvl].uv = (u, v)

    for y in range(n):
        for x in range(n):
            v = 1 - (y + 0.5) / n
            if F[y, x, 3] > 0.5:
                column(x, y, 0.0, half + TF[y, x] * relief, (x + 0.5) / (2 * n), v)
            xb = n - 1 - x  # back art is stored as seen from behind
            if Bk[y, xb, 3] > 0.5:
                column(x, y, -(half + TB[y, xb] * relief), 0.0, (n + xb + 0.5) / (2 * n), v)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    me = bpy.data.meshes.new("card")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(a.get("name", "VoxelCard"), me)
    bpy.context.scene.collection.objects.link(ob)
    ob.scale = (1 / ppu,) * 3
    mat = bpy.data.materials.new("VoxelCard")
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    me.materials.append(mat)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    ob.rotation_euler = (0, 0, 3.141592653589793)  # front ends up facing -Z in Unity (towards a default camera)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.ops.export_scene.fbx(filepath=os.path.abspath(a["out"]), use_selection=True, axis_forward="-Z", axis_up="Y",
                             apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True, mesh_smooth_type="FACE",
                             path_mode="STRIP")
    print(f"[card] {len(me.polygons)} faces -> {a['out']}")


if __name__ == "__main__":
    main()
