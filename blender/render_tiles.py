"""Renders an isometric biome tile set (2:1 dimetric) from generated textures.

    blender -b -P render_tiles.py -- --spec specs/tiles_crypt.json --out P:/Projects/Unity/Hearthdelve/Assets/Art/Tiles --tile 64

For every tile/variant/state it writes <out>/<biome>/<name>.png (albedo with soft baked light,
reduced to one shared biome palette) and <name>.normal.png (for URP 2D lights), plus
<out>/<biome>/tiles.json with sizes and pivots. One world unit is one tile; the floor diamond is
`tile` px wide and tile/2 px tall, and its centre is the sprite pivot.
"""
import math
import os
import random
import sys
import tempfile
import zlib

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402

ELEV, AZIM = 30.0, 45.0


def mesh_obj(name, bm, mat):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def box(name, size, center, mat, bevel=0.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(center) @ Matrix.Diagonal((*size, 1)))
    if bevel:
        bmesh.ops.bevel(bm, geom=bm.edges[:], offset=bevel, segments=1, affect="EDGES")
    return mesh_obj(name, bm, mat)


def cyl(name, r, z0, z1, mat, seg=16, center=(0, 0)):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=z1 - z0,
                          matrix=Matrix.Translation((center[0], center[1], (z0 + z1) / 2)))
    for f in bm.faces:
        f.smooth = True
    return mesh_obj(name, bm, mat)


def rocks(name, n, mat, rng, area=0.42, size=(0.05, 0.16), z=0.0):
    bm = bmesh.new()
    for _ in range(n):
        r = rng.uniform(*size)
        c = Vector((rng.uniform(-area, area), rng.uniform(-area, area), z + r * 0.35))
        m = Matrix.Translation(c) @ Matrix.Rotation(rng.uniform(0, 6.3), 4, "Z") @ Matrix.Diagonal((1, rng.uniform(0.6, 1.2), rng.uniform(0.5, 0.9), 1))
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r, matrix=m)
    return mesh_obj(name, bm, mat)


def tex_material(name, tex_path, offset, tint=(1, 1, 1, 1), scale=1.0, bump=0.6, channel=0):
    mat = hd.hd_material(name, channel, texture=tex_path, tex_scale=scale, bump=bump, detail_range=(0.9, 1.0))
    nt = mat.node_tree
    mp = next(n for n in nt.nodes if n.type == "MAPPING")
    mp.inputs["Location"].default_value = offset
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
    if tex is not None and tint != (1, 1, 1, 1):
        mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        mix.inputs["B"].default_value = tint
        grp = next(n for n in nt.nodes if n.type == "GROUP")
        nt.links.new(tex.outputs["Color"], mix.inputs["A"])
        nt.links.new(mix.outputs["Result"], grp.inputs["Color"])
    return mat


def clear_objects():
    for o in list(bpy.data.objects):
        if o.type == "MESH":
            bpy.data.objects.remove(o)


def build(t, variant, state, orient, texdir, rng, ramp_names=()):
    def mat(key, scale=1.0, tint=(1, 1, 1, 1)):
        p = os.path.join(texdir, f"{t.get(key, key)}.png")
        off = (rng.uniform(0, 5), rng.uniform(0, 5), rng.uniform(0, 5))
        ramp = t.get("ramps", {}).get(key, "stone")
        ch = ramp_names.index(ramp) + 1 if ramp in ramp_names else 0
        return tex_material(f"m_{key}_{variant}_{rng.random():.5f}", p, off, tint, scale, channel=ch)

    kind = t["kind"]
    h = t.get("height", 1.0)
    if kind == "floor" or "floor" in t:
        fk = "texture" if kind == "floor" else "floor"
        box("floor", (1, 1, 0.12), (0, 0, -0.06), mat(fk, 1.0), bevel=0.012)
    if kind == "wall":
        box("wall", (1, 1, h), (0, 0, h / 2), mat("texture", 0.5), bevel=0.02)
    elif kind == "pillar":
        m = mat("texture", 0.8)
        box("base", (0.62, 0.62, 0.14), (0, 0, 0.07), m, bevel=0.015)
        cyl("shaft", 0.22, 0.14, h - 0.14, m, seg=12)
        box("cap", (0.6, 0.6, 0.14), (0, 0, h - 0.07), m, bevel=0.015)
    elif kind == "rubble":
        rocks("rocks", rng.randint(9, 15), mat("texture", 2.0), rng)
    elif kind == "decal":
        bm = bmesh.new()
        for _ in range(rng.randint(3, 6)):
            c = Vector((rng.uniform(-0.35, 0.35), rng.uniform(-0.35, 0.35), 0.02))
            m = Matrix.Translation(c) @ Matrix.Rotation(rng.uniform(0, 6.3), 4, "Z")
            if rng.random() < 0.3:
                bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=6, radius=0.07, matrix=m @ Matrix.Translation((0, 0, 0.05)))
            else:
                bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=0.018, radius2=0.018, depth=0.22,
                                      matrix=m @ Matrix.Rotation(math.pi / 2, 4, "Y"))
        mesh_obj("bones", bm, mat("texture", 3.0, (1.25, 1.2, 1.05, 1)))
    elif kind == "door":
        fm = mat("frame", 0.5)
        dm = mat("texture", 1.2)
        # wall plane runs along X ("ne" edge) or Y ("nw" edge) through the tile centre
        along = Vector((1, 0, 0)) if orient == "ne" else Vector((0, 1, 0))
        across = Vector((0, 1, 0)) if orient == "ne" else Vector((1, 0, 0))
        th = 0.34
        for s in (-1, 1):
            c = along * (s * 0.4) + Vector((0, 0, h / 2))
            sz = along * 0.2 + across * th + Vector((0, 0, h))
            box(f"post{s}", tuple(abs(v) for v in sz), c, fm, bevel=0.015)
        sz = along * 1.0 + across * th + Vector((0, 0, h - 1.25))
        box("lintel", tuple(abs(v) for v in sz), Vector((0, 0, 1.25 + (h - 1.25) / 2)), fm, bevel=0.015)
        if state == "closed":
            sz = along * 0.6 + across * 0.06 + Vector((0, 0, 1.25))
            box("door", tuple(abs(v) for v in sz), Vector((0, 0, 0.625)), dm, bevel=0.01)
        else:  # swung open into the room, hinged on one post
            ob = box("door", (0.6, 0.06, 1.25), (0.3, 0, 0.625), dm, bevel=0.01)
            ob.location = along * -0.3
            ob.rotation_euler = (0, 0, math.radians(-100 if orient == "ne" else 10))


def main():
    args = hd.script_args()
    spec = hd.load_json(args["spec"])
    tile = int(args.get("tile", 64))
    ss = int(args.get("supersample", 4))
    out = os.path.join(os.path.abspath(args["out"]), spec["biome"])
    base = os.path.dirname(os.path.abspath(args["spec"]))
    tex_key = "pixelTextureDir" if args.get("pixel") and "pixelTextureDir" in spec else "textureDir"
    texdir = os.path.join(os.path.dirname(base), spec[tex_key])
    hd.clear_scene()
    scene = bpy.context.scene
    tmp = os.path.join(tempfile.mkdtemp(prefix="hdtiles_"), "f.exr")

    ppu = tile / math.sqrt(2)  # pixels per world unit, horizontally
    max_h = max(t.get("height", 1.0) for t in spec["tiles"])
    canvas_h = int(math.ceil((tile / 2 + max_h * math.cos(math.radians(ELEV)) * ppu + 4) / 8) * 8)
    W, H = tile, canvas_h
    pivot_from_bottom = tile / 4 + 1  # diamond centre sits tile/4 px above the bottom edge (+1px slack)
    hd.setup_render(scene, W, H, ss)
    cam = hd.ortho_camera(scene, max(W, H) / ppu, ELEV, AZIM, (0, 0, 0))
    up = cam.rotation_quaternion @ Vector((0, 1, 0))
    shift_px = H / 2 - pivot_from_bottom
    hd.ortho_camera(scene, max(W, H) / ppu, ELEV, AZIM, tuple(up * (shift_px / ppu)))

    results, albedos = [], []
    for t in spec["tiles"]:
        combos = []
        if t["kind"] == "door":
            for st in t["states"]:
                for o in t["orientations"]:
                    combos.append((0, st, o, f"{t['id']}.{st}.{o}"))
        else:
            for v in range(t.get("variants", 1)):
                combos.append((v, None, None, f"{t['id']}.{v + 1}"))
        for v, st, o, name in combos:
            clear_objects()
            rng = random.Random(zlib.crc32(name.encode()))
            build(t, v, st, o, texdir, rng, list(spec.get("ramps", {})))
            hd.set_mode(hd.MODE_ALBEDO)
            a_hi = hd.render_exr(scene, tmp)
            hd.set_mode(hd.MODE_DATA)
            d_hi = hd.render_exr(scene, tmp)
            hd.set_mode(hd.MODE_NORMAL)
            n_hi = hd.render_exr(scene, tmp)
            alpha, _, chan, _, nrm = hd.reduce_frame(d_hi, n_hi, ss)
            blocks = hd._blocks(a_hi, ss)
            cov = blocks[..., 3] > 0.5
            col = (blocks[..., :3] * cov[..., None]).sum(axis=2) / np.maximum(cov.sum(axis=2), 1)[..., None]
            shade = hd.form_shade(nrm, spec["light"]["dir"], spec["light"]["ambient"])
            col = np.clip(col * shade[..., None], 0, 1) ** (1 / 2.2)  # linear -> sRGB-ish
            albedos.append((name, col, alpha, nrm, chan))
            print(f"[tiles] {name}", flush=True)

    ramp_names = list(spec.get("ramps", {}))
    meta = {"biome": spec["biome"], "tile": tile, "canvas": [W, H], "pivot": [W / 2 / W, pivot_from_bottom / H],
            "sprites": []}
    if ramp_names:  # gradient map: shaded luminance -> the material's designed ramp
        ramps = [np.array([[int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)] for h in spec["ramps"][r]]) for r in ramp_names]
        lum_all = np.concatenate([(c[a] @ [0.3, 0.59, 0.11]) for _, c, a, _, _ in albedos])
        lo, hi = np.percentile(lum_all, 2), np.percentile(lum_all, 98)
        meta["ramps"] = spec["ramps"]
    else:  # one k-means palette for the whole biome
        pal = kmeans_palette(np.concatenate([c[a] for _, c, a, _, _ in albedos]), spec.get("colors", 24))
        meta["palette"] = ["#%02x%02x%02x" % tuple(int(v * 255) for v in c) for c in pal]
    for name, col, alpha, nrm, chan in albedos:
        if ramp_names:
            lum = np.clip((col @ [0.3, 0.59, 0.11] - lo) / max(hi - lo, 1e-6), 0, 1)
            mat = np.clip(chan - 1, 0, len(ramps) - 1)  # material channel 1.. -> ramp index
            top = nrm[..., 1] > 0.55  # faces pointing up (camera-space y)
            edge = np.zeros_like(alpha)  # silhouette pixels: inner outline
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                edge |= ~np.roll(alpha, (dy, dx), axis=(0, 1))
            rim = top & ~np.roll(top, -1, axis=0) & np.roll(alpha, -1, axis=0)  # top face meets a side below
            q = np.zeros_like(col)
            for k, ramp in enumerate(ramps):
                n = len(ramp)
                idx = np.clip(np.floor(lum * n), 0, n - 1).astype(int)
                idx = np.where(rim, np.minimum(idx + 2, n - 1), idx)
                idx = np.where(edge, np.maximum(idx - 3, 0), idx)
                m = alpha & (mat == k)
                q[m] = ramp[idx[m]]
        else:
            d = ((col[:, :, None, :] - pal[None, None]) ** 2).sum(-1)
            q = pal[d.argmin(-1)]
        img = np.zeros((H, W, 4), np.uint8)
        img[..., :3] = np.clip(np.rint(q * 255), 0, 255)
        img[..., 3] = alpha * 255
        hd.write_png(os.path.join(out, f"{name}.png"), img * (alpha[..., None] > 0))
        hd.write_png(os.path.join(out, f"{name}.normal.png"), hd.encode_normal(nrm, alpha))
        meta["sprites"].append(name)
    hd.write_json(os.path.join(out, "tiles.json"), meta)
    print(f"[tiles] done -> {out}")


def kmeans_palette(px, k, iters=12, seed=0):
    rng = np.random.default_rng(seed)
    sample = px[rng.choice(len(px), size=min(len(px), 20000), replace=False)]
    centers = sample[rng.choice(len(sample), size=k, replace=False)]
    for _ in range(iters):
        d = ((sample[:, None] - centers[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                centers[j] = sample[m].mean(0)
    return centers[np.argsort(centers.sum(1))]


if __name__ == "__main__":
    main()
