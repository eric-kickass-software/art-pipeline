"""Builds the modular character kit .blend from specs/character_kit.json.

    blender -b -P build_character_kit.py -- --spec specs/character_kit.json --out kit/character_kit.blend
                                           [--textures textures/materials]

Layout of the generated .blend (also the convention for hand-made parts):
  * one collection per body type: "body.<name>", holding an armature "rig.<name>" with the shared
    bone names (root, pelvis, spine, chest, neck, head, upperarm/forearm/hand/thigh/shin/foot .L/.R)
  * every part is one or more mesh objects inside that collection with custom properties
        hd_part = "<part id>"   hd_body = "<body name>"
    skinned with an Armature modifier, or bone-parented for rigid parts (hair, hats, weapons)
  * materials are named "hd.<channel>" (hd.skin, hd.cloth1, hd.metal...). The renderer rebuilds
    them, so an artist only has to assign a material with the right name.
  * animation clips are Actions named "clip.<name>", keyed on frames 0..n-1.
To add a part by hand: model it on top of a rig, give it hd_part/hd_body, assign hd.* materials,
add the part to the spec, then render.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402

HEAD_R = 0.30
VOX = 0.0  # voxel size from spec render.voxel; > 0 draws faces on the voxel grid


# ----------------------------------------------------------------------------- skeleton

def skeleton(b):
    """Joint positions for one body type. Character faces -Y; its left (.L) is +X."""
    h, t, l, s = b["height"], b["torso"], b["limbs"], b["shoulders"]
    J = {
        "P": (0, 0, 0.70 * h), "S": (0, 0.005, 0.86 * h), "C": (0, 0.01, 1.00 * h), "N": (0, 0.01, 1.12 * h),
        "H": (0, 0, 1.12 * h + HEAD_R * 0.92),
    }
    for side, sx in (("L", 1), ("R", -1)):
        J[f"SH.{side}"] = (sx * 0.175 * s, 0.01, 1.06 * h)
        J[f"EL.{side}"] = (sx * 0.255 * s, 0.03, 0.86 * h)
        J[f"WR.{side}"] = (sx * 0.30 * s, 0.0, 0.68 * h)
        J[f"HA.{side}"] = (sx * 0.315 * s, -0.01, 0.615 * h)
        J[f"HP.{side}"] = (sx * 0.09 * t, 0, 0.66 * h)
        J[f"KN.{side}"] = (sx * 0.095 * t, -0.01, 0.37 * h)
        J[f"AN.{side}"] = (sx * 0.10 * t, 0.01, 0.09 * h)
        J[f"TO.{side}"] = (sx * 0.10 * t, -0.13, 0.035)
    R = {"P": 0.15 * t, "S": 0.14 * t, "C": 0.16 * t, "N": 0.06}
    for side in ("L", "R"):
        R.update({f"SH.{side}": 0.07 * l, f"EL.{side}": 0.058 * l, f"WR.{side}": 0.05 * l,
                  f"HP.{side}": 0.09 * l, f"KN.{side}": 0.072 * l, f"AN.{side}": 0.06 * l, f"TO.{side}": 0.055 * l})
    return {k: Vector(v) for k, v in J.items()}, R


BONES = [  # name, head joint, tail joint, parent
    ("pelvis", "P", "S", "root"), ("spine", "S", "C", "pelvis"), ("chest", "C", "N", "spine"),
    ("neck", "N", "HB", "chest"), ("head", "HB", "HT", "neck"),
] + [b for side in ("L", "R") for b in [
    (f"upperarm.{side}", f"SH.{side}", f"EL.{side}", "chest"),
    (f"forearm.{side}", f"EL.{side}", f"WR.{side}", f"upperarm.{side}"),
    (f"hand.{side}", f"WR.{side}", f"HT.{side}", f"forearm.{side}"),
    (f"thigh.{side}", f"HP.{side}", f"KN.{side}", "pelvis"),
    (f"shin.{side}", f"KN.{side}", f"AN.{side}", f"thigh.{side}"),
    (f"foot.{side}", f"AN.{side}", f"TO.{side}", f"shin.{side}"),
]]


def build_armature(name, J, coll):
    J = dict(J)
    J["HB"] = J["N"] + Vector((0, 0, 0.06))
    J["HT"] = J["H"] + Vector((0, 0, HEAD_R))
    for side in ("L", "R"):
        J[f"HT.{side}"] = J[f"HA.{side}"] + (J[f"HA.{side}"] - J[f"WR.{side}"]) * 0.8
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    coll.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones
    root = eb.new("root"); root.head, root.tail = (0, 0, 0), (0, 0.25, 0)
    for bname, a, b, parent in BONES:
        e = eb.new(bname)
        e.head, e.tail = J[a], J[b]
        e.roll = 0
        e.parent = eb[parent]
        e.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return obj


# ----------------------------------------------------------------------------- mesh building

class PartBuilder:
    """Accumulates primitives (each with an hd.* material) into one mesh object."""

    def __init__(self, name):
        self.name = name
        self.bm = bmesh.new()
        self.mats = []

    def _mat_index(self, channel):
        name = f"hd.{channel}"
        if name not in self.mats:
            self.mats.append(name)
        return self.mats.index(name)

    def add(self, piece, channel, smooth=True, keep=None):
        """Merge a bmesh `piece`; `keep(co)` -> False deletes those vertices first."""
        if keep:
            bmesh.ops.delete(piece, geom=[v for v in piece.verts if not keep(v.co)], context="VERTS")
        mi = self._mat_index(channel)
        for f in piece.faces:
            f.material_index = mi
            f.smooth = smooth
        me = bpy.data.meshes.new("tmp")
        piece.to_mesh(me)
        piece.free()
        self.bm.from_mesh(me)
        bpy.data.meshes.remove(me)
        return self

    def sphere(self, center, radius, channel, scale=(1, 1, 1), seg=16, rings=10, keep=None, rot=None, smooth=True):
        p = bmesh.new()
        m = Matrix.Translation(center) @ (rot or Matrix()) @ Matrix.Diagonal((*scale, 1))
        bmesh.ops.create_uvsphere(p, u_segments=seg, v_segments=rings, radius=radius, matrix=m)
        return self.add(p, channel, smooth, keep)

    def cone(self, a, b, r1, r2, channel, seg=12, smooth=True, caps=True):
        a, b = Vector(a), Vector(b)
        d = b - a
        m = Matrix.Translation((a + b) / 2) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4()
        p = bmesh.new()
        bmesh.ops.create_cone(p, cap_ends=caps, cap_tris=False, segments=seg, radius1=r1, radius2=r2,
                              depth=d.length, matrix=m)
        return self.add(p, channel, smooth)

    def box(self, center, size, channel, rot=None, smooth=False):
        p = bmesh.new()
        m = Matrix.Translation(center) @ (rot or Matrix()) @ Matrix.Diagonal((*size, 1))
        bmesh.ops.create_cube(p, size=1.0, matrix=m)
        return self.add(p, channel, smooth)

    def torus(self, center, major, minor, channel, scale=(1, 1, 1), seg=16):
        p = bmesh.new()
        m = Matrix.Translation(center) @ Matrix.Diagonal((*scale, 1))
        for i in range(seg):
            a0 = 2 * math.pi * i / seg
            c = Vector((math.cos(a0) * major, math.sin(a0) * major, 0))
            bmesh.ops.create_uvsphere(p, u_segments=8, v_segments=5, radius=minor, matrix=m @ Matrix.Translation(c))
        return self.add(p, channel)

    def skin(self, joints, edges, radii, channel, subsurf=2):
        """Skin-modifier tube network (body and clothing shells)."""
        names = sorted({j for e in edges for j in e}, key=lambda k: list(joints).index(k))
        me = bpy.data.meshes.new("skin_src")
        me.from_pydata([joints[n] for n in names], [(names.index(a), names.index(b)) for a, b in edges], [])
        ob = bpy.data.objects.new("skin_src", me)
        bpy.context.scene.collection.objects.link(ob)
        mod = ob.modifiers.new("skin", "SKIN")
        mod.branch_smoothing = 0.6
        sk = me.skin_vertices[0].data
        for i, n in enumerate(names):
            r = radii[n]
            sk[i].radius = (r, r) if isinstance(r, (int, float)) else r
            sk[i].use_root = i == 0
        sub = ob.modifiers.new("sub", "SUBSURF")
        sub.levels = sub.render_levels = subsurf
        dg = bpy.context.evaluated_depsgraph_get()
        res = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
        bpy.data.objects.remove(ob)
        bpy.data.meshes.remove(me)
        p = bmesh.new()
        p.from_mesh(res)
        bpy.data.meshes.remove(res)
        return self.add(p, channel, True)

    def finish(self, coll, part_id, body, rig, mode, bones=None, bone=None):
        me = bpy.data.meshes.new(f"{part_id}|{body}")
        self.bm.to_mesh(me)
        self.bm.free()
        for mname in self.mats:
            me.materials.append(bpy.data.materials.get(mname) or bpy.data.materials.new(mname))
        ob = bpy.data.objects.new(f"{part_id}|{body}", me)
        coll.objects.link(ob)
        ob["hd_part"], ob["hd_body"] = part_id, body
        if mode == "skin":
            skin_weights(ob, rig, bones)
            ob.parent = rig
            ob.modifiers.new("rig", "ARMATURE").object = rig
        else:  # rigid: parent to one bone, keep world placement
            ob.parent, ob.parent_type, ob.parent_bone = rig, "BONE", bone
            pb = rig.pose.bones[bone]
            ob.matrix_parent_inverse = (rig.matrix_world @ Matrix.Translation(pb.tail - pb.head)
                                        @ pb.bone.matrix_local).inverted()
        return ob


def _seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
    return (p - (a + ab * t)).length


def skin_weights(ob, rig, allowed=None):
    """Distance-to-bone-segment weights: nearest bone plus its parent/child, smooth falloff."""
    bones = {b.name: b for b in rig.data.bones if b.name != "root" and (not allowed or b.name in allowed)}
    groups = {n: ob.vertex_groups.new(name=n) for n in bones}
    adj = {n: {c.name for c in b.children if c.name in bones} | ({b.parent.name} if b.parent and b.parent.name in bones else set())
           for n, b in bones.items()}
    for v in ob.data.vertices:
        d = {n: _seg_dist(v.co, b.head_local, b.tail_local) for n, b in bones.items()}
        best = min(d, key=d.get)
        cands = {best} | adj[best]
        w = {n: 1.0 / (d[n] + 0.01) ** 4 for n in cands}
        tot = sum(w.values())
        for n, x in w.items():
            if x / tot > 0.02:
                groups[n].add([v.index], x / tot, "REPLACE")


# ----------------------------------------------------------------------------- parts

def limbs(side):
    return [(f"SH.{side}", f"EL.{side}"), (f"EL.{side}", f"WR.{side}")]


def legs(side, to="AN"):
    e = [(f"HP.{side}", f"KN.{side}")]
    if to in ("AN", "TO"):
        e.append((f"KN.{side}", f"AN.{side}"))
    return e


def scaled(R, k, add=0.0):
    return {n: r * k + add for n, r in R.items()}


def build_parts(body, J, R, rig, coll, wanted):
    H = J["H"]
    out = []

    def want(pid):
        return wanted is None or pid in wanted

    def head_local(fn):  # keep-predicate in head-relative coordinates
        return lambda co: fn(co - H)

    skin_bones = None

    # ---------------- body
    if want("body.base"):
        pb = PartBuilder("body")
        edges = [("P", "S"), ("S", "C"), ("C", "N")] + [e for s in "LR" for e in
                 [("C", f"SH.{s}")] + limbs(s) + [("P", f"HP.{s}")] + legs(s) + [(f"AN.{s}", f"TO.{s}")]]
        rr = dict(R)
        pb.skin(J, edges, rr, "skin")
        for s in "LR":
            pb.sphere(J[f"HA.{s}"], 0.05 * (R[f"WR.{s}"] / 0.04), "skin", scale=(0.8, 1, 1.15))
        out.append(pb.finish(coll, "body.base", body, rig, "skin", skin_bones))
        hb = PartBuilder("head")
        hb.sphere(H, HEAD_R, "skin", scale=(1, 0.95, 1.0), seg=20, rings=14)
        for s, sx in (("L", 1), ("R", -1)):  # ears
            hb.sphere(H + Vector((sx * 0.29, 0.02, -0.02)), 0.055, "skin", scale=(0.5, 0.8, 1.1))
        out.append(hb.finish(coll, "body.base", body, rig, "rigid", bone="head"))

    # ---------------- faces (rigid on head)
    def face(pid, eye_r=0.034, eye_scale=(1, 0.45, 1.25), brow=None, mouth=(0.06, 0.012), smile=False):
        fb = PartBuilder(pid)
        for sx in (1, -1):
            x, z = sx * 0.105, -0.01
            y = -math.sqrt(max(HEAD_R ** 2 * 0.95 - x * x - z * z, 0)) - 0.005
            fb.sphere(H + Vector((x, y, z)), eye_r, "eyes", scale=eye_scale, seg=10, rings=6)
            if brow is not None:
                rot = Matrix.Rotation(math.radians(brow * sx), 4, "Y")
                fb.box(H + Vector((x, y + 0.01, 0.075)), (0.085, 0.03, 0.022), "hair", rot=rot)
        mw, mh = mouth
        y = -math.sqrt(HEAD_R ** 2 * 0.95 - 0.12 ** 2) + 0.0
        if smile:
            for sx in (-1, 0, 1):
                fb.box(H + Vector((sx * mw * 0.45, y, -0.115 + abs(sx) * 0.012)), (mw * 0.5, 0.03, mh), "dark")
        else:
            fb.box(H + Vector((0, y, -0.12)), (mw, 0.03, mh), "dark")
        out.append(fb.finish(coll, pid, body, rig, "rigid", bone="head"))

    def voxel_face(pid, eyes, brows=(), mouth=((0, -2),)):
        """Faces for voxel characters, drawn on the voxel grid like pixel art: each feature is one
        voxel, given as (column, row) offsets in voxels from the eye line. Small spheres sit at the
        centre of the head's front voxels, so they replace exactly those voxels when composited."""
        v = VOX
        fb = PartBuilder(pid)
        ez = round((H.z - 0.02) / v) * v - H.z  # eye row, on the grid

        def dot(col, row, channel):
            x, z = col * v, ez + row * v
            y = -math.sqrt(max(HEAD_R ** 2 * 0.93 - x * x - z * z, 0.0))
            p = Vector((round(x / v) * v, round((H.y + y) / v) * v - H.y + v * 0.15, z))
            fb.sphere(H + p, v * 0.3, channel, seg=6, rings=4)

        for c, r in eyes:
            dot(c, r, "eyes")
        for c, r in brows:
            dot(c, r, "hair")
        for c, r in mouth:
            dot(c, r, "dark")
        out.append(fb.finish(coll, pid, body, rig, "rigid", bone="head"))

    if VOX:
        eyes = ((-1, 0), (1, 0))
        if want("face.neutral"): voxel_face("face.neutral", eyes)
        if want("face.stern"): voxel_face("face.stern", eyes, brows=((-1, 1), (1, 1)), mouth=((0, -2), (1, -2)))
        if want("face.kind"): voxel_face("face.kind", eyes, mouth=((-1, -1), (0, -2), (1, -1)))
        if want("face.wide"): voxel_face("face.wide", eyes + ((-1, 1), (1, 1)), mouth=((0, -2),))
    else:
        if want("face.neutral"): face("face.neutral", brow=0)
        if want("face.stern"): face("face.stern", eye_scale=(1, 0.45, 0.85), brow=-22, mouth=(0.07, 0.012))
        if want("face.kind"): face("face.kind", eye_scale=(1, 0.45, 1.0), brow=12, smile=True)
        if want("face.wide"): face("face.wide", eye_r=0.045, eye_scale=(1, 0.45, 1.3), brow=6, mouth=(0.035, 0.03))

    # ---------------- hair (rigid on head)
    cap_r = HEAD_R + 0.025

    def cap(hb, front_z=0.07, back_z=-0.16):
        hb.sphere(H + Vector((0, 0.01, 0.01)), cap_r, "hair", scale=(1.02, 1.0, 1.0), seg=20, rings=14,
                  keep=head_local(lambda c: c.z > (front_z if c.y < -0.08 else back_z if c.y > 0.05 else (front_z + back_z) / 2)))

    def fringe(hb, depth=0.05):
        hb.sphere(H + Vector((0.02, -0.25, 0.14)), 0.2, "hair", scale=(1.15, 0.35, depth / 0.1 * 0.5))

    if want("hair.short"):
        hb = PartBuilder("hair.short"); cap(hb); fringe(hb)
        out.append(hb.finish(coll, "hair.short", body, rig, "rigid", bone="head"))
    if want("hair.long"):
        hb = PartBuilder("hair.long"); cap(hb, back_z=-0.3); fringe(hb)
        hb.sphere(H + Vector((0, 0.1, -0.28)), 0.3, "hair", scale=(1.1, 0.62, 1.15),
                  keep=head_local(lambda c: c.y > -0.02))
        for sx in (1, -1):
            hb.sphere(H + Vector((sx * 0.27, -0.04, -0.22)), 0.1, "hair", scale=(0.6, 0.8, 2.0))
        out.append(hb.finish(coll, "hair.long", body, rig, "rigid", bone="head"))
    if want("hair.ponytail"):
        hb = PartBuilder("hair.ponytail"); cap(hb, front_z=0.1)
        hb.sphere(H + Vector((0, 0.31, 0.06)), 0.07, "accent")
        hb.cone(H + Vector((0, 0.36, 0.05)), H + Vector((0, 0.42, -0.45)), 0.1, 0.03, "hair")
        out.append(hb.finish(coll, "hair.ponytail", body, rig, "rigid", bone="head"))
    if want("hair.spiky"):
        hb = PartBuilder("hair.spiky"); cap(hb, front_z=0.09)
        for az, el in [(0, 80), (40, 45), (-40, 45), (140, 40), (-140, 40), (180, 30), (90, 35), (-90, 35), (0, 35)]:
            d = Vector((math.cos(math.radians(el)) * math.sin(math.radians(az)),
                        -math.cos(math.radians(el)) * math.cos(math.radians(az)), math.sin(math.radians(el))))
            hb.cone(H + d * 0.22, H + d * 0.48, 0.09, 0.0, "hair", seg=6)
        out.append(hb.finish(coll, "hair.spiky", body, rig, "rigid", bone="head"))
    if want("hair.bun"):
        hb = PartBuilder("hair.bun"); cap(hb, front_z=0.1); fringe(hb, 0.03)
        hb.sphere(H + Vector((0, 0.16, 0.27)), 0.13, "hair")
        out.append(hb.finish(coll, "hair.bun", body, rig, "rigid", bone="head"))
    if want("hair.mohawk"):
        hb = PartBuilder("hair.mohawk")
        for i in range(7):
            a = math.radians(-60 + i * 25)
            d = Vector((0, -math.sin(a), math.cos(a)))
            hb.cone(H + d * 0.26, H + d * 0.44, 0.07, 0.01, "hair", seg=6)
        out.append(hb.finish(coll, "hair.mohawk", body, rig, "rigid", bone="head"))

    # ---------------- facial hair
    if want("facial.beard"):
        fb = PartBuilder("facial.beard")
        fb.sphere(H + Vector((0, -0.06, -0.12)), 0.26, "hair", scale=(1.0, 0.95, 0.9),
                  keep=head_local(lambda c: c.z < -0.1 and c.y < 0.05))
        out.append(fb.finish(coll, "facial.beard", body, rig, "rigid", bone="head"))
    if want("facial.mustache"):
        fb = PartBuilder("facial.mustache")
        for sx in (1, -1):
            fb.sphere(H + Vector((sx * 0.05, -0.29, -0.085)), 0.045, "hair", scale=(1.4, 0.6, 0.6),
                      rot=Matrix.Rotation(math.radians(-15 * sx), 4, "Y"))
        out.append(fb.finish(coll, "facial.mustache", body, rig, "rigid", bone="head"))

    # ---------------- head gear
    if want("head.hood"):
        b = PartBuilder("head.hood")
        b.sphere(H + Vector((0, 0.02, 0.02)), HEAD_R + 0.06, "cloth1", seg=20, rings=14,
                 keep=head_local(lambda c: not (c.y < -0.12 and abs(c.x) < 0.24 and -0.24 < c.z < 0.16)))
        b.cone(J["N"] + Vector((0, 0.03, 0.05)), J["C"] + Vector((0, 0.02, -0.08)), 0.2, 0.28, "cloth1", caps=False)
        out.append(b.finish(coll, "head.hood", body, rig, "rigid", bone="neck"))
    if want("head.helmet"):
        b = PartBuilder("head.helmet")
        b.sphere(H + Vector((0, 0.01, 0.02)), HEAD_R + 0.05, "metal", seg=20, rings=14,
                 keep=head_local(lambda c: c.z > (0.04 if c.y < -0.1 else -0.12)))
        b.torus(H + Vector((0, 0.01, 0.03)), HEAD_R + 0.045, 0.025, "accent")
        b.box(H + Vector((0, -0.34, -0.03)), (0.04, 0.03, 0.16), "metal")
        out.append(b.finish(coll, "head.helmet", body, rig, "rigid", bone="head"))
    if want("head.wizard_hat"):
        b = PartBuilder("head.wizard_hat")
        b.cone(H + Vector((0, 0, 0.17)), H + Vector((0, 0, 0.2)), 0.48, 0.46, "cloth1", seg=20)
        b.cone(H + Vector((0, 0, 0.19)), H + Vector((0, 0.12, 0.78)), 0.27, 0.01, "cloth1", seg=16)
        b.cone(H + Vector((0, 0, 0.2)), H + Vector((0, 0.01, 0.27)), 0.265, 0.25, "accent", seg=16)
        out.append(b.finish(coll, "head.wizard_hat", body, rig, "rigid", bone="head"))
    if want("head.cap"):
        b = PartBuilder("head.cap")
        b.sphere(H + Vector((0, 0.01, 0.03)), HEAD_R + 0.035, "leather", seg=18, rings=12,
                 keep=head_local(lambda c: c.z > 0.08))
        b.box(H + Vector((0, -0.3, 0.1)), (0.3, 0.16, 0.025), "leather",
              rot=Matrix.Rotation(math.radians(-12), 4, "X"))
        out.append(b.finish(coll, "head.cap", body, rig, "rigid", bone="head"))

    # ---------------- torso
    torso_edges = [("P", "S"), ("S", "C"), ("C", "N")] + [("C", f"SH.{s}") for s in "LR"]
    if want("torso.tunic"):
        b = PartBuilder("torso.tunic")
        Jx = dict(J); Jx["SK"] = J["P"] + Vector((0, 0.0, -0.17))
        rr = scaled(R, 1.18, 0.012); rr["SK"] = R["P"] * 1.5; rr["N"] = R["N"] * 1.3
        b.skin(Jx, [("SK", "P")] + torso_edges + [(f"SH.{s}", f"EL.{s}") for s in "LR"], rr, "cloth1")
        b.torus(J["P"] + Vector((0, 0, 0.05)), R["P"] * 1.2, 0.03, "leather", scale=(1, 0.9, 1))
        b.torus(J["N"] + Vector((0, 0, -0.02)), R["N"] * 1.5, 0.022, "cloth2")
        out.append(b.finish(coll, "torso.tunic", body, rig, "skin", skin_bones))
    if want("torso.robe"):
        b = PartBuilder("torso.robe")
        Jx = dict(J)
        Jx["SK1"] = J["P"] + Vector((0, 0.01, -0.3)); Jx["SK2"] = Vector((0, 0.02, 0.05))
        rr = scaled(R, 1.2, 0.015); rr["SK1"] = R["P"] * 1.75; rr["SK2"] = R["P"] * 2.2; rr["N"] = R["N"] * 1.4
        b.skin(Jx, [("SK2", "SK1"), ("SK1", "P")] + torso_edges + [e for s in "LR" for e in limbs(s)], rr, "cloth1")
        b.torus(J["P"] + Vector((0, 0, 0.06)), R["P"] * 1.25, 0.03, "cloth2", scale=(1, 0.9, 1))
        out.append(b.finish(coll, "torso.robe", body, rig, "skin", skin_bones))
    if want("torso.plate"):
        b = PartBuilder("torso.plate")
        rr = scaled(R, 1.3, 0.02)
        b.skin(J, [("P", "S"), ("S", "C"), ("C", "N")], rr, "metal")
        for s in "LR":
            b.sphere(J[f"SH.{s}"] + Vector((0, 0, 0.02)), 0.11, "metal", scale=(1.1, 1, 0.8),
                     keep=lambda c, z=J[f"SH.{s}"].z: c.z > z - 0.02)
        b.torus(J["P"] + Vector((0, 0, 0.04)), R["P"] * 1.35, 0.03, "leather", scale=(1, 0.9, 1))
        out.append(b.finish(coll, "torso.plate", body, rig, "skin", skin_bones))
    if want("torso.vest"):
        b = PartBuilder("torso.vest")
        rr = scaled(R, 1.24, 0.016)
        b.skin(J, [("P", "S"), ("S", "C"), ("C", "N")], rr, "leather")
        out.append(b.finish(coll, "torso.vest", body, rig, "skin", skin_bones))

    # ---------------- legs / feet / hands
    if want("legs.pants"):
        b = PartBuilder("legs.pants")
        b.skin(J, [("S", "P")] + [e for s in "LR" for e in [("P", f"HP.{s}")] + legs(s)], scaled(R, 1.14, 0.01), "cloth2")
        out.append(b.finish(coll, "legs.pants", body, rig, "skin", skin_bones))
    if want("legs.shorts"):
        b = PartBuilder("legs.shorts")
        Jx = dict(J)
        for s in "LR":
            Jx[f"MT.{s}"] = J[f"HP.{s}"].lerp(J[f"KN.{s}"], 0.55)
        rr = scaled(R, 1.16, 0.012)
        for s in "LR":
            rr[f"MT.{s}"] = R[f"HP.{s}"] * 1.2
        b.skin(Jx, [("S", "P")] + [e for s in "LR" for e in [("P", f"HP.{s}"), (f"HP.{s}", f"MT.{s}")]], rr, "cloth2")
        out.append(b.finish(coll, "legs.shorts", body, rig, "skin", skin_bones))
    if want("legs.greaves"):
        b = PartBuilder("legs.greaves")
        b.skin(J, [e for s in "LR" for e in [(f"KN.{s}", f"AN.{s}")]], scaled(R, 1.35, 0.02), "metal")
        for s in "LR":
            b.sphere(J[f"KN.{s}"] + Vector((0, -0.04, 0)), 0.07, "metal", scale=(1, 0.8, 1))
        out.append(b.finish(coll, "legs.greaves", body, rig, "skin", skin_bones))
    if want("feet.boots"):
        b = PartBuilder("feet.boots")
        Jx = dict(J)
        for s in "LR":
            Jx[f"BT.{s}"] = J[f"KN.{s}"].lerp(J[f"AN.{s}"], 0.45)
        rr = scaled(R, 1.3, 0.018)
        for s in "LR":
            rr[f"BT.{s}"] = R[f"KN.{s}"] * 1.3
        b.skin(Jx, [e for s in "LR" for e in [(f"BT.{s}", f"AN.{s}"), (f"AN.{s}", f"TO.{s}")]], rr, "leather")
        out.append(b.finish(coll, "feet.boots", body, rig, "skin", skin_bones))
    if want("feet.shoes"):
        b = PartBuilder("feet.shoes")
        b.skin(J, [e for s in "LR" for e in [(f"AN.{s}", f"TO.{s}")]], scaled(R, 1.25, 0.014), "leather")
        out.append(b.finish(coll, "feet.shoes", body, rig, "skin", skin_bones))
    if want("hands.gloves"):
        b = PartBuilder("hands.gloves")
        Jx = dict(J)
        for s in "LR":
            Jx[f"FA.{s}"] = J[f"EL.{s}"].lerp(J[f"WR.{s}"], 0.5)
        b.skin(Jx, [(f"FA.{s}", f"WR.{s}") for s in "LR"], scaled(R | {f"FA.{s}": R[f"WR.{s}"] * 1.2 for s in "LR"}, 1.35, 0.01), "leather")
        for s in "LR":
            b.sphere(J[f"HA.{s}"], 0.06, "leather", scale=(0.85, 1.05, 1.2))
        out.append(b.finish(coll, "hands.gloves", body, rig, "skin", skin_bones))
    if want("hands.gauntlets"):
        b = PartBuilder("hands.gauntlets")
        b.skin(J, [(f"EL.{s}", f"WR.{s}") for s in "LR"], scaled(R, 1.4, 0.015), "metal")
        for s in "LR":
            b.sphere(J[f"HA.{s}"], 0.065, "metal", scale=(0.85, 1.05, 1.2))
        out.append(b.finish(coll, "hands.gauntlets", body, rig, "skin", skin_bones))

    # ---------------- back
    if want("back.cape"):
        b = PartBuilder("back.cape")
        p = bmesh.new()
        top, bot = J["C"].z + 0.04, 0.32 * (J["P"].z / 0.70)
        rows, cols = 8, 10
        verts = []
        for i in range(rows + 1):
            t = i / rows
            z = top + (bot - top) * t
            half = 0.2 + 0.16 * t
            row = []
            for j in range(cols + 1):
                u = (j / cols - 0.5) * 2
                ang = u * 1.0
                x = math.sin(ang) * half
                y = 0.13 + R["C"] * 0.6 + (1 - math.cos(ang)) * -0.06 + t * 0.08
                row.append(p.verts.new((x, y, z)))
            verts.append(row)
        for i in range(rows):
            for j in range(cols):
                p.faces.new((verts[i][j], verts[i][j + 1], verts[i + 1][j + 1], verts[i + 1][j]))
        b.add(p, "cloth1")
        b.torus(J["C"] + Vector((0, -0.02, 0.06)), 0.17, 0.025, "accent", scale=(1, 0.9, 1))
        out.append(b.finish(coll, "back.cape", body, rig, "skin", {"chest", "spine", "pelvis"}))
    if want("back.quiver"):
        b = PartBuilder("back.quiver")
        a, c = J["P"] + Vector((-0.12, 0.2, 0.0)), J["C"] + Vector((0.14, 0.2, 0.25))
        b.cone(a, c, 0.065, 0.075, "leather")
        for k in range(3):
            off = Vector(((k - 1) * 0.03, 0.0, 0))
            b.cone(c + off, c + off + (c - a).normalized() * 0.16, 0.008, 0.008, "wood", seg=5)
            b.box(c + off + (c - a).normalized() * 0.18, (0.03, 0.01, 0.06), "accent")
        out.append(b.finish(coll, "back.quiver", body, rig, "rigid", bone="chest"))

    # ---------------- weapons (held in hand.R = mainhand, hand.L = offhand)
    hr, hl = J["HA.R"], J["HA.L"]
    fwd = Vector((0, -1, 0))
    if want("mainhand.sword"):
        b = PartBuilder("mainhand.sword")
        up = Vector((0, -0.55, 0.83)).normalized()
        b.cone(hr - up * 0.06, hr + up * 0.08, 0.022, 0.022, "leather", seg=6)
        b.box(hr + up * 0.1, (0.18, 0.04, 0.035), "accent", rot=up.to_track_quat("Z", "Y").to_matrix().to_4x4())
        b.box(hr + up * 0.42, (0.06, 0.018, 0.6), "metal", rot=up.to_track_quat("Z", "Y").to_matrix().to_4x4())
        b.sphere(hr - up * 0.08, 0.03, "accent", seg=8, rings=5)
        out.append(b.finish(coll, "mainhand.sword", body, rig, "rigid", bone="hand.R"))
    if want("mainhand.dagger"):
        b = PartBuilder("mainhand.dagger")
        up = Vector((0, -0.8, 0.6)).normalized()
        rot = up.to_track_quat("Z", "Y").to_matrix().to_4x4()
        b.cone(hr - up * 0.04, hr + up * 0.05, 0.02, 0.02, "leather", seg=6)
        b.box(hr + up * 0.065, (0.1, 0.03, 0.025), "accent", rot=rot)
        b.box(hr + up * 0.18, (0.045, 0.014, 0.22), "metal", rot=rot)
        out.append(b.finish(coll, "mainhand.dagger", body, rig, "rigid", bone="hand.R"))
    if want("mainhand.staff"):
        b = PartBuilder("mainhand.staff")
        lo, hi = Vector((hr.x - 0.02, hr.y - 0.04, 0.05)), Vector((hr.x + 0.02, hr.y - 0.1, 1.85 * J["P"].z / 0.70))
        b.cone(lo, hi, 0.028, 0.032, "wood", seg=8)
        b.sphere(hi + Vector((0, 0, 0.07)), 0.075, "magic", seg=10, rings=8)
        b.torus(hi, 0.06, 0.018, "accent", seg=10)
        out.append(b.finish(coll, "mainhand.staff", body, rig, "rigid", bone="hand.R"))
    if want("mainhand.mace"):
        b = PartBuilder("mainhand.mace")
        up = Vector((0, -0.55, 0.83)).normalized()
        b.cone(hr - up * 0.06, hr + up * 0.4, 0.022, 0.022, "wood", seg=6)
        b.sphere(hr + up * 0.45, 0.085, "metal", seg=10, rings=8)
        for d in (Vector((1, 0, 0)), Vector((-1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)), Vector((0, -1, 0))):
            c = hr + up * 0.45
            b.cone(c + d * 0.07, c + d * 0.14, 0.03, 0.0, "metal", seg=5, smooth=False)
        out.append(b.finish(coll, "mainhand.mace", body, rig, "rigid", bone="hand.R"))
    if want("offhand.shield"):
        b = PartBuilder("offhand.shield")
        c = hl + Vector((0.07, -0.04, 0.08))
        side = Vector((1, -0.35, 0)).normalized()
        b.cone(c, c + side * 0.045, 0.24, 0.24, "wood", seg=20)
        b.cone(c + side * 0.02, c + side * 0.055, 0.25, 0.25, "metal", seg=20, caps=False)
        b.sphere(c + side * 0.06, 0.06, "metal", seg=10, rings=6)
        b.box(c + side * 0.05, (0.03, 0.03, 0.4), "accent", rot=side.to_track_quat("X", "Z").to_matrix().to_4x4())
        out.append(b.finish(coll, "offhand.shield", body, rig, "rigid", bone="hand.L"))
    if want("offhand.tome"):
        b = PartBuilder("offhand.tome")
        c = hl + Vector((0.02, -0.08, 0.04))
        b.box(c, (0.06, 0.2, 0.25), "leather")
        b.box(c + Vector((0.0, 0.0, 0.0)), (0.07, 0.18, 0.23), "cloth2")
        b.box(c + Vector((-0.035, -0.0, 0)), (0.01, 0.06, 0.06), "accent")
        out.append(b.finish(coll, "offhand.tome", body, rig, "rigid", bone="hand.L"))
    if want("offhand.lantern"):
        b = PartBuilder("offhand.lantern")
        c = hl + Vector((0.0, -0.02, -0.17))
        b.cone(hl, c + Vector((0, 0, 0.1)), 0.008, 0.008, "metal", seg=4)
        b.cone(c + Vector((0, 0, -0.08)), c + Vector((0, 0, 0.08)), 0.065, 0.05, "metal", seg=6, caps=True)
        b.sphere(c, 0.05, "magic", seg=8, rings=6)
        out.append(b.finish(coll, "offhand.lantern", body, rig, "rigid", bone="hand.L"))
    return out


# ----------------------------------------------------------------------------- animation

def _q(axis, deg):
    from mathutils import Quaternion
    return Quaternion(Vector(axis), math.radians(deg))


def pose(rig, rots, loc=(0, 0, 0), root_rot=None):
    """rots: bone -> list of (axis, degrees) in armature space. Converted to each bone's local frame."""
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
    for name, items in rots.items():
        pb = rig.pose.bones.get(name)
        if not pb:
            continue
        R = pb.bone.matrix_local.to_quaternion()
        q = _q((1, 0, 0), 0)
        for axis, deg in items:
            q = _q(axis, deg) @ q
        pb.rotation_quaternion = R.inverted() @ q @ R
    root = rig.pose.bones["root"]
    Rr = root.bone.matrix_local.to_quaternion()
    root.location = Rr.inverted() @ Vector(loc)
    if root_rot:
        root.rotation_quaternion = Rr.inverted() @ _q(*root_rot) @ Rr


X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)


def clip_pose(rig, clip, i, n):
    """Procedural clips. Forward is -Y, so a negative X rotation swings a limb forward."""
    t = i / n
    p = 2 * math.pi * t
    s = math.sin
    arms_down = {"upperarm.L": [(Y, -8)], "upperarm.R": [(Y, 8)]}
    if clip == "idle":
        b = 0.5 - 0.5 * math.cos(p)
        r = dict(arms_down)
        r["chest"] = [(X, 1.5 * b)]
        r["head"] = [(X, -1.5 * b)]
        r["upperarm.L"] = r["upperarm.L"] + [(X, 3 * b)]
        r["upperarm.R"] = r["upperarm.R"] + [(X, 3 * b)]
        pose(rig, r, loc=(0, 0, -0.012 * b))
    elif clip == "walk":
        r = {
            "thigh.L": [(X, -28 * s(p))], "thigh.R": [(X, 28 * s(p))],
            "shin.L": [(X, 38 * max(0, s(p + math.pi * 0.5)) ** 1.5)], "shin.R": [(X, 38 * max(0, s(p - math.pi * 0.5)) ** 1.5)],
            "upperarm.L": [(Y, -8), (X, 24 * s(p))], "upperarm.R": [(Y, 8), (X, -24 * s(p))],
            "forearm.L": [(X, -12)], "forearm.R": [(X, -12)],
            "chest": [(Z, 5 * s(p))], "pelvis": [(Z, -4 * s(p))],
        }
        pose(rig, r, loc=(0, 0, -0.03 + 0.03 * abs(math.cos(p))))
    else:
        ready = {
            "thigh.L": [(X, -10)], "thigh.R": [(X, 12)], "shin.L": [(X, 10)], "shin.R": [(X, 10)],
            "upperarm.R": [(X, -35), (Y, 10)], "forearm.R": [(X, -45)],
            "upperarm.L": [(X, -25), (Y, -12)], "forearm.L": [(X, -35)],
            "hand.R": [(X, 75)], "hand.L": [(X, 55)],
        }
        if clip == "idle_battle":
            pose(rig, ready)
        elif clip in ("attack",):
            swing = [-35, -130, -175, -70, -25, -35][i % 6]
            twist = [0, 12, 18, -20, -14, -4][i % 6]
            lunge = [0, 0.03, 0.04, -0.18, -0.12, -0.03][i % 6]
            r = dict(ready)
            r["upperarm.R"] = [(X, swing), (Y, 10)]
            r["forearm.R"] = [(X, [-45, -30, -20, -10, -20, -40][i % 6])]
            r["hand.R"] = [(X, [75, 70, 60, 10, 30, 70][i % 6])]
            r["chest"] = [(Z, twist), (X, [0, 6, 8, -10, -6, 0][i % 6])]
            pose(rig, r, loc=(0, lunge, -0.03 if 2 < i < 5 else 0))
        elif clip == "cast":
            lift = [-25, -60, -95, -110, -110, -60][i % 6]
            r = dict(ready)
            r["upperarm.R"] = [(X, lift), (Y, 14)]
            r["upperarm.L"] = [(X, lift), (Y, -14)]
            r["forearm.R"] = r["forearm.L"] = [(X, -15)]
            r["head"] = [(X, -8 if i > 1 else 0)]
            r["chest"] = [(X, 6 if i > 2 else 0)]
            pose(rig, r, loc=(0, 0, 0.02 if i in (3, 4) else 0))
        elif clip == "hurt":
            k = [1.0, 0.7, 0.3][i % 3]
            r = dict(ready)
            r["chest"] = [(X, 18 * k)]
            r["head"] = [(X, 14 * k)]
            r["upperarm.R"] = [(X, -20 + 30 * k), (Y, 30 * k)]
            r["upperarm.L"] = [(X, -10 + 30 * k), (Y, -30 * k)]
            pose(rig, r, loc=(0, 0.12 * k, -0.02 * k))
        elif clip == "dead":
            k = min(1.0, (i + 1) / n)
            r = {"chest": [(X, 10 * k)], "head": [(X, 15 * k), (Z, 20 * k)],
                 "upperarm.L": [(Y, -40 * k)], "upperarm.R": [(Y, 40 * k)],
                 "thigh.L": [(X, -20 * k)], "shin.L": [(X, 35 * k)]}
            pose(rig, r, loc=(0, 0.25 * k, 0.12 * k - 0.0), root_rot=(X, 82 * k))
        else:
            pose(rig, ready)


def bake_clips(rig, spec):
    rig.animation_data_create()
    for name, c in spec["clips"].items():
        act = bpy.data.actions.new(f"clip.{name}")
        act.use_fake_user = True
        rig.animation_data.action = act
        key = "idle_battle" if name == "idle" else name
        for f in range(c["frames"]):
            clip_pose(rig, name, f, c["frames"])
            for pb in rig.pose.bones:
                pb.keyframe_insert("location", frame=f)
                pb.keyframe_insert("rotation_quaternion", frame=f)
        if name == "idle":  # battle idle stance lives in its own action
            act2 = bpy.data.actions.new("clip.idle_battle")
            act2.use_fake_user = True
            rig.animation_data.action = act2
            for f in range(c["frames"]):
                clip_pose(rig, "idle_battle", f, c["frames"])
                b = 0.5 - 0.5 * math.cos(2 * math.pi * f / c["frames"])
                rig.pose.bones["root"].location = rig.pose.bones["root"].bone.matrix_local.to_quaternion().inverted() @ Vector((0, 0, -0.015 * b))
                for pb in rig.pose.bones:
                    pb.keyframe_insert("location", frame=f)
                    pb.keyframe_insert("rotation_quaternion", frame=f)
    rig.animation_data.action = None
    pose(rig, {})


# ----------------------------------------------------------------------------- main

def main():
    args = hd.script_args()
    spec = hd.load_json(args["spec"])
    global VOX
    VOX = float(spec.get("render", {}).get("voxel", 0.0))
    out = os.path.abspath(args["out"])
    wanted = set(args["parts"].split(",")) if "parts" in args else None
    bodies = args["bodies"].split(",") if "bodies" in args else list(spec["bodies"])
    hd.clear_scene()
    ch = hd.channel_ids(spec)
    tex_dir = args.get("textures")
    for c in spec["channels"]:
        if c["name"] != "none":
            tex = os.path.join(tex_dir, f"{c['name']}.png") if tex_dir else None
            hd.hd_material(f"hd.{c['name']}", ch[c["name"]], texture=tex)
    for body in bodies:
        coll = bpy.data.collections.new(f"body.{body}")
        bpy.context.scene.collection.children.link(coll)
        J, R = skeleton(spec["bodies"][body])
        rig = build_armature(f"rig.{body}", J, coll)
        rig["hd_body"] = body
        objs = build_parts(body, J, R, rig, coll, wanted)
        bake_clips(rig, spec)
        print(f"[kit] body {body}: {len(objs)} part objects")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
    print(f"[kit] saved {out}")


if __name__ == "__main__":
    main()
