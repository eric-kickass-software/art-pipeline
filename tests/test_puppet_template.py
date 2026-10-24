"""Clean puppet template (hdart.puppet_template): renders every part whole from the spec alone (no art), pivots sit
inside their parts, joints stay covered by the round ends, the torso is one closed shape, and the checked-in spec has
both views. Run: uv run python tests/test_puppet_template.py"""
import numpy as np
import hdart.puppet_template as pt

spec = pt.load()
assert spec["schemaVersion"] == 2 and set(spec["views"]) == {"front", "back"}
for v in ("front", "back"):
    V = spec["views"][v]
    assert len(V["limbs"]) == 12 and len(V["order"]) == 14 and len(V["shapes"]["torso"]["points"]) >= 10
    parts = pt.render(spec, v, s=1)
    assert set(parts) == set(V["order"])
    for name, (rgba, guides, pivot) in parts.items():
        a = rgba[..., 3] > 127
        assert a.sum() > 50, (v, name)
        x, y = int(round(pivot[0])), int(round(pivot[1]))
        if name not in ("head", "torso"):   # limbs rotate about their pivot (head and torso only bob in the walk)
            assert a[y, x], (v, name, "pivot outside its part")      # rotating about it never shows a gap
        assert not (guides[..., 3] > 0)[~(rgba[..., 3] > 0)].any()   # guides stay on the part
    for name, L in V["limbs"].items():   # the child's round end covers its start joint
        if not name.endswith(".upper"):
            j = pt._joint(V, L["from"]); a = parts[name][0][..., 3] > 127
            assert a[int(j[1]), int(j[0])], (v, name)
    t = parts["torso"][0][..., 3] > 127
    ys = np.nonzero(t.any(1))[0]
    assert all(t[y].any() for y in range(ys.min(), ys.max() + 1))    # one piece top to bottom
    for k in ("armA", "armB"):                                       # complete under the shoulder caps
        p = V["joints"][k]["pivot"]; assert t[int(p[1]), int(p[0])], (v, k)

# synthetic spec: rendering works from numbers alone
syn = {"views": {"front": {
    "joints": {"armA": {"pivot": [200, 200], "mid": [170, 240], "end": [150, 280], "tip": [140, 300], "R": 14},
               "armB": {"pivot": [300, 200], "mid": [330, 240], "end": [350, 280], "tip": [360, 300], "R": 14},
               "legA": {"pivot": [230, 330], "mid": [225, 390], "end": [222, 440], "tip": [205, 455], "R": 22},
               "legB": {"pivot": [270, 330], "mid": [275, 390], "end": [278, 440], "tip": [295, 455], "R": 22},
               "neck": [250, 180], "pelvis": [250, 320], "crotch": [250, 340]},
    "limbs": {f"{k}.{s}": {"from": f"{k}.{a}", "to": f"{k}.{b}", "points": [[0.2, 12], [0.5, 13], [0.8, 12], [1.15, 0], [0.8, -12], [0.5, -13], [0.2, -12], [-0.15, 0]]}
              for k in pt.LIMBS for s, a, b in pt.SEGS},
    "shapes": {"torso": {"points": [[230, 176], [270, 176], [312, 204], [300, 260], [298, 320], [262, 350],
                                    [238, 350], [202, 320], [200, 260], [188, 204]]},
               "head": {"poly": [[250 + 70 * np.cos(a), 110 + 70 * np.sin(a)] for a in np.linspace(0, 6.28, 40)]}},
    "seams": {"waist": {"y": 300, "bow": 0.18}},
    "order": spec["views"]["front"]["order"]}}}
p = pt.render(syn, "front", s=1)
assert all((p[n][0][..., 3] > 127).sum() > 50 for n in p)
assert (p["torso"][1][..., 3] > 0).any()       # waistband guide drawn

# the template is the rig: the walk poses straight from the spec's joints (no art)
import hdart.puppet as pp
for v in ("front", "back"):
    assert len(spec["views"][v]["shapes"]["head"]["points"]) >= 8
    B = pt.rig_from_spec(spec, v)
    T, bones = pp.pose(B, v, 0.25, "walk")
    assert set(T) >= set(spec["views"][v]["order"])
    for k in pt.LIMBS:   # bone lengths are kept by the walk
        L = np.linalg.norm(np.subtract(spec["views"][v]["joints"][k]["mid"], spec["views"][v]["joints"][k]["pivot"]))
        assert abs(np.linalg.norm(bones[k][1] - bones[k][0]) - L) < 1.0, (v, k)

# editor saves: validated, the upper cap width sets the joint radius, bad input refused
import copy, json, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp()) / "puppet_template.json"; tmp.write_text(json.dumps(spec)); pt.SPEC = tmp
e = copy.deepcopy(spec); e["views"]["front"]["limbs"]["armB.upper"]["points"][2] = [0.5, 30]
out = pt.save(e)
assert out["views"]["front"]["limbs"]["armB.upper"]["points"][2] == [0.5, 30] and out["edited"] and out["from"] == spec["from"]
u = copy.deepcopy(out); u["views"]["back"]["limbs"]["legA.lower"] = {"from": "legA.mid", "to": "legA.end", "widths": [44, 30]}
up = pt.save(u)["views"]["back"]["limbs"]["legA.lower"]   # an old editor page (two circles) still saves: upgraded
assert len(up["points"]) == 16 and "widths" not in up
assert abs(up["points"][6][1]) < 0.5 and up["points"][6][0] > 1.0      # the round end cap around the ankle
e = out


f = copy.deepcopy(out); fc = {"eyes": [[200, 130], [240, 130]], "mouth": [210, 170], "center": [[230, 60], [220, 130], [205, 190]]}
f["views"]["front"]["shapes"]["head"]["face"] = fc
kept = pt.save(f)["views"]["front"]["shapes"]["head"]["face"]
assert kept["eyes"] == fc["eyes"] and kept["mouth"] == fc["mouth"]       # face guides survive a save
g = pt.render(pt.load(), "front", s=1)["head"][1]
assert ((g[..., 0] > 200) & (g[..., 1] > 100) & (g[..., 1] < 180) & (g[..., 2] < 60) & (g[..., 3] > 0)).any()   # drawn (orange)


def refused(mut):
    b = copy.deepcopy(spec); mut(b)
    try: pt.save(b)
    except ValueError: return True
    return False
assert refused(lambda b: b["views"].pop("back"))
assert refused(lambda b: b["views"]["front"]["limbs"]["legA.lower"].__setitem__("points", [[0, 1]] * 3))
assert refused(lambda b: b["views"]["front"]["limbs"]["legA.lower"]["points"].__setitem__(0, [9, 0]))
assert refused(lambda b: b["views"]["back"]["shapes"]["torso"].__setitem__("points", [[1, 1]] * 3))
assert refused(lambda b: b["views"]["front"]["joints"]["neck"].__setitem__(0, "x"))
assert json.loads(tmp.read_text())["views"]["front"]["limbs"]["armB.upper"]["points"][2] == [0.5, 30]   # refused saves wrote nothing

# limb outlines live in their bone's frame: moving a joint carries the outline along
V = copy.deepcopy(spec["views"]["front"]); L = V["limbs"]["armB.lower"]
before = np.array(pt.limb_world(V, L)); V["joints"]["armB"]["mid"] = [c + 10 for c in V["joints"]["armB"]["mid"]]
V["joints"]["armB"]["end"] = [c + 10 for c in V["joints"]["armB"]["end"]]
assert np.allclose(np.array(pt.limb_world(V, L)) - before, 10)
print("ok")
