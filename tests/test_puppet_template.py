"""Clean puppet template (hdart.puppet_template): renders every part whole from the spec alone (no art), pivots sit
inside their parts, joints stay covered by the round ends, the torso is one closed shape, and the checked-in spec has
both views. Run: uv run python tests/test_puppet_template.py"""
import numpy as np
import hdart.puppet_template as pt

spec = pt.load()
assert spec["schemaVersion"] == 1 and set(spec["views"]) == {"front", "back"}
for v in ("front", "back"):
    V = spec["views"][v]
    assert len(V["limbs"]) == 12 and len(V["order"]) == 14 and len(V["shapes"]["torso"]["points"]) >= 10
    parts = pt.render(spec, v, s=1)
    assert set(parts) == set(V["order"])
    for name, (rgba, guides, pivot) in parts.items():
        a = rgba[..., 3] > 127
        assert a.sum() > 50, (v, name)
        x, y = int(round(pivot[0])), int(round(pivot[1]))
        assert a[y, x], (v, name, "pivot outside its part")          # rotating about it never shows a gap
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
    "limbs": {f"{k}.{s}": {"from": f"{k}.{a}", "to": f"{k}.{b}", "widths": [24] * 7}
              for k in pt.LIMBS for s, a, b in pt.SEGS},
    "shapes": {"torso": {"points": [[230, 176], [270, 176], [312, 204], [300, 260], [298, 320], [262, 350],
                                    [238, 350], [202, 320], [200, 260], [188, 204]]},
               "head": {"poly": [[250 + 70 * np.cos(a), 110 + 70 * np.sin(a)] for a in np.linspace(0, 6.28, 40)]}},
    "seams": {"waist": {"y": 300, "bow": 0.18}},
    "order": spec["views"]["front"]["order"]}}}
p = pt.render(syn, "front", s=1)
assert all((p[n][0][..., 3] > 127).sum() > 50 for n in p)
assert (p["torso"][1][..., 3] > 0).any()       # waistband guide drawn
print("ok")
