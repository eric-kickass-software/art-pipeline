"""Renders every part of the character kit into indexed layer sheets.

    blender -b kit/character_kit.blend -P render_character.py -- --spec specs/character_kit.json
            --out P:/Projects/Unity/Hearthdelve/Assets/Art/Characters [--bodies slim,broad] [--parts hair.short,face.kind]
            [--views token,battle] [--textures textures/materials]

Per (body, part, view) it writes two sheets plus one manifest for the whole run:
    <out>/<body>/<part>.<view>.data.png     R = shade (form light x material detail), G = channel id,
                                           B = depth (0 near .. 255 far), A = coverage
    <out>/<body>/<part>.<view>.normal.png  camera-space normal (Unity sprite convention), A = coverage
    <out>/manifest.json                    cell size, clip rows, part list, channels
Rows are (direction, clip); columns are frames. The compositor (hdart compose, or the Unity
CharacterComposer) stacks any set of parts by per-pixel depth and colours them from palette ramps.
"""
import math
import os
import sys
import tempfile
import time

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hd_common as hd  # noqa: E402


def direction_name(view, deg, cam):
    if len(view["directions"]) == 1:
        return "left" if view["azimuth"] == 0 else "default"
    a = math.radians(deg)
    face = Vector((math.sin(a), -math.cos(a), 0))  # rest facing is -Y, rotated about Z
    q = cam.rotation_quaternion
    right, fwd = q @ Vector((1, 0, 0)), q @ Vector((0, 0, -1))
    fwd.z = 0
    return ("s" if face.dot(-fwd) > 0 else "n") + ("e" if face.dot(right) > 0 else "w")


def set_action(rig, name):
    act = bpy.data.actions.get(name)
    if act is None:
        raise RuntimeError(f"missing action {name}")
    rig.animation_data.action = act
    if hasattr(rig.animation_data, "action_slot") and act.slots and rig.animation_data.action_slot is None:
        rig.animation_data.action_slot = act.slots[0]


def main():
    args = hd.script_args()
    spec = hd.load_json(args["spec"])
    out = os.path.abspath(args["out"])
    rcfg = spec["render"]
    ss = int(args.get("supersample", rcfg["supersample"]))
    bodies = args["bodies"].split(",") if "bodies" in args else list(spec["bodies"])
    views = args["views"].split(",") if "views" in args else list(spec["views"])
    only = set(args["parts"].split(",")) if "parts" in args else None
    ch = hd.channel_ids(spec)
    tex_dir = args.get("textures")
    for c in spec["channels"]:
        if c["name"] != "none":
            tex = os.path.join(tex_dir, f"{c['name']}.png") if tex_dir else None
            hd.hd_material(f"hd.{c['name']}", ch[c["name"]], texture=tex,
                           detail_range=tuple(rcfg.get("detailRange", (0.84, 1.0))))
    coverage = rcfg.get("coverage", {})
    hd.set_group_value("DEPTH_RANGE", rcfg["depthRange"])
    scene = bpy.context.scene
    tmp = tempfile.mkdtemp(prefix="hdart_")
    exr = os.path.join(tmp, "f.exr")

    meshes = [o for o in scene.objects if o.type == "MESH" and "hd_part" in o]
    voxel = float(args.get("voxel", rcfg.get("voxel", 0)))
    if voxel > 0:
        hd.voxelize_objects(meshes, voxel)
        print(f"[render] voxelized at {voxel} units", flush=True)
    manifest_path = os.path.join(out, "manifest.json")
    manifest = hd.load_json(manifest_path) if os.path.exists(manifest_path) else {"parts": {}}
    manifest.update({"schemaVersion": 1, "channels": spec["channels"], "slots": spec["slots"],
                     "bodies": sorted(set(manifest.get("bodies", [])) | set(bodies))})
    manifest.setdefault("views", {})
    t0, renders = time.time(), 0

    for vname in views:
        v = spec["views"][vname]
        w, h = v["cell"]
        hd.setup_render(scene, w, h, ss)
        cam = hd.ortho_camera(scene, v["orthoScale"], v["elevation"], v["azimuth"], v["target"])
        rows = []
        for deg in v["directions"]:
            for clip in v["clips"]:
                rows.append({"dir": direction_name(v, deg, cam), "yaw": deg, "clip": clip,
                             "frames": spec["clips"][clip]["frames"], "fps": spec["clips"][clip]["fps"],
                             "loop": spec["clips"][clip]["loop"]})
        for i, r in enumerate(rows):
            r["row"] = i
        cols = max(r["frames"] for r in rows)
        manifest["views"][vname] = {"cell": [w, h], "columns": cols, "rows": rows}

        for body in bodies:
            rig = bpy.data.objects[f"rig.{body}"]
            parts = sorted({o["hd_part"] for o in meshes if o["hd_body"] == body})
            parts = [p for p in parts if only is None or p in only]
            for part in parts:
                objs = [o for o in meshes if o["hd_body"] == body and o["hd_part"] == part]
                for o in meshes:
                    o.hide_render = o not in objs
                data_sheet = np.zeros((len(rows) * h, cols * w, 4), np.uint8)
                norm_sheet = np.zeros_like(data_sheet)
                for r in rows:
                    rig.rotation_euler = (0, 0, math.radians(r["yaw"]))
                    act = "clip.idle_battle" if (vname == "battle" and r["clip"] == "idle") else f"clip.{r['clip']}"
                    set_action(rig, act)
                    for f in range(r["frames"]):
                        scene.frame_set(f)
                        hd.set_mode(hd.MODE_DATA)
                        d_hi = hd.render_exr(scene, exr)
                        hd.set_mode(hd.MODE_NORMAL)
                        n_hi = hd.render_exr(scene, exr)
                        renders += 2
                        cov = coverage.get(part.split(".")[0], coverage.get("default", 0.45))
                        alpha, detail, chan, depth, nrm = hd.reduce_frame(d_hi, n_hi, ss, coverage=cov)
                        shade = hd.form_shade(nrm, rcfg["lightDir"], rcfg["ambient"]) * detail
                        cell = np.zeros((h, w, 4), np.uint8)
                        cell[..., 0] = np.clip(np.rint(shade * 255), 0, 255)
                        cell[..., 1] = chan
                        cell[..., 2] = np.clip(np.rint(depth * 255), 0, 255)
                        cell[..., 3] = alpha * 255
                        y0, x0 = r["row"] * h, f * w
                        data_sheet[y0:y0 + h, x0:x0 + w] = cell * alpha[..., None]
                        norm_sheet[y0:y0 + h, x0:x0 + w] = hd.encode_normal(nrm, alpha)
                hd.write_png(os.path.join(out, body, f"{part}.{vname}.data.png"), data_sheet)
                hd.write_png(os.path.join(out, body, f"{part}.{vname}.normal.png"), norm_sheet)
                entry = manifest["parts"].setdefault(part, {"slot": part.split(".")[0], "bodies": [], "views": []})
                entry["bodies"] = sorted(set(entry["bodies"]) | {body})
                entry["views"] = sorted(set(entry["views"]) | {vname})
                print(f"[render] {body} {part} {vname}  ({renders} renders, {time.time() - t0:.0f}s)", flush=True)
            rig.rotation_euler = (0, 0, 0)
    spec_parts = {p["id"]: p for p in spec["parts"]}
    for pid, e in manifest["parts"].items():
        if pid in spec_parts:
            e["name"] = spec_parts[pid].get("name", pid)
            e["hides"] = spec_parts[pid].get("hides", [])
    hd.write_json(manifest_path, manifest)
    print(f"[render] done: {renders} renders in {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
