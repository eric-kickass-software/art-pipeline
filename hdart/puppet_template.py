"""Clean puppet template: the generic unisex body as a set of whole, separately drawn parts (Klei symbols / Live2D
template style), so nothing is ever cut from a figure. Vault: Hearthdelve - 2D Puppet Rig Research, "Building the
template itself" (2026-10-23).

The template is numbers, not pixels (specs/puppet_template.json), per view (front-3/4, back-3/4), in 512 px rig space:
  joints  shoulder/elbow/wrist/fingertip, hip/knee/ankle/toe (+ R = the shoulder/hip circle), neck, pelvis, crotch
  limbs   per segment (upper, lower, hand/foot): a bone between two joints + an outline of points in the bone's own
          frame ([t, n]: t = 0 at the start joint, 1 at the end joint; n = px to the side), joined by a smooth closed
          curve; a joint move carries its outlines along. Measured with a round cap around each joint (the overlap
          under the parent) and both sides of the limb at five stations (calf, bicep, knee)
  shapes  torso and head: smooth closed outlines (radial polygons); the torso is complete under the arms
  seams   guide lines shared by neighbouring parts (waistband; leg holes follow the torso's lower edge)
  face    front head guides (Live2D-style): the two eye centres, the mouth and the face's centre line

    measure(build)          -> spec measured from an approved build's puppet rig (hand-fixed joints included)
    render(spec, view, s)   -> {part: (rgba, guides rgba, pivot)} at 512*s px, clay shaded, outlined
    preview(spec)           -> writes sprites/ai/puppet_template/ (parts, guides, sheets, walk previews)
"""
import json, math, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ai_kit, jobs, workshop as ws, puppet as pp

SPEC = ai_kit.ROOT / "specs" / "puppet_template.json"
PX = pp.PX
LIMBS = ("armA", "armB", "legA", "legB")
SEGS = (("upper", "pivot", "mid"), ("lower", "mid", "end"), ("hand", "end", "tip"))
NW = 7                                    # samples per segment when measuring the joint caps
STATIONS = (0.2, 0.35, 0.5, 0.65, 0.8)    # where both sides of a limb are measured along its bone
LIGHT = np.array([-0.45, -0.65, 0.62]); LIGHT = LIGHT / np.linalg.norm(LIGHT)   # from the top left, toward the viewer
CLAY = (206, 204, 200)
LINE = (58, 52, 50)
GUIDE_BONE, GUIDE_SEAM, GUIDE_FACE = (40, 150, 230, 255), (225, 40, 150, 255), (255, 140, 0, 255)


def out_dir():
    return ai_kit.AI_DIR / "puppet_template"


# ----------------------------------------------------------------------------- measuring (from an approved build)

def _r(v):
    return [round(float(x), 1) for x in v]


def _smooth(w, keep0=False):
    w = np.asarray(w, float)
    for _ in range(2):
        p = np.concatenate([[w[0]], w, [w[-1]]])
        s = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
        if keep0:
            s[0] = w[0]
        w = s
    return w


def _radial(mask, n=96):
    """A star-shaped outline of a mask around its centroid (the farthest inside point on each ray)."""
    ys, xs = np.nonzero(mask); c = np.array([xs.mean(), ys.mean()])
    pts = []
    for a in np.linspace(0, 2 * math.pi, n, endpoint=False):
        d = np.array([math.cos(a), math.sin(a)]); last = c
        for s in np.arange(0, 300, 0.5):
            q = c + d * s; xi, yi = int(q[0]), int(q[1])
            if not (0 <= xi < PX and 0 <= yi < PX):
                break
            if mask[yi, xi]:
                last = q
        pts.append(_r(last))
    return pts


def _smooth_mask(mask, r):
    im = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r))
    return np.asarray(im) > 127


def _smooth_ring(pts, win=7):
    """Smooth a closed radial outline by averaging each point's radius with its neighbours."""
    P = np.asarray(pts, float); c = P.mean(0); d = P - c
    r = np.hypot(d[:, 0], d[:, 1]); a = np.arctan2(d[:, 1], d[:, 0])
    k = np.ones(win) / win; rr = np.convolve(np.concatenate([r[-win:], r, r[:win]]), k, "same")[win:-win]
    return [_r(c + q * np.array([math.cos(t), math.sin(t)])) for q, t in zip(rr, a)]


def _run(m, y, cx):
    """The run of mask pixels on row y that contains (or is nearest to) x = cx: (left, right)."""
    xs = np.nonzero(m[int(y)])[0]
    if not len(xs):
        return cx, cx
    x = int(xs[np.argmin(np.abs(xs - cx))]); l = r = x
    while l > 0 and m[int(y), l - 1]:
        l -= 1
    while r < PX - 1 and m[int(y), r + 1]:
        r += 1
    return float(l), float(r)


def _torso_points(B):
    """The torso as ~15 landmarks (clockwise from the neck), each measured on the body silhouette: a clean leotard
    shape that is complete under the arms (shoulder caps, armpits) and ends in the leg-hole curve over the thighs."""
    m, J, jt = B["mask"], B["J"], B["joints"]
    nk, pv = np.array(J["neck"]), np.array(J["pelvis"])
    cr = jt.get("crotch"); cr = np.array(cr if cr is not None else pv, float)
    cx = lambda y: float(np.interp(y, [nk[1], cr[1]], [nk[0], cr[0]]))
    body = m  # below the armpits the A-pose arms are apart from the body, so the centre run is the body itself
    aL, aR = (jt["armA"], jt["armB"]) if jt["armA"]["pivot"][0] < jt["armB"]["pivot"][0] else (jt["armB"], jt["armA"])
    Ra = (aL["R"] + aR["R"]) / 2; Rl = float(np.mean([jt[k]["R"] for k in ("legA", "legB")]))
    nl, nr = _run(B["torso"], nk[1] + 0.4 * Ra, nk[0])                 # the neck just below the chin
    y_ap = (aL["pivot"][1] + aR["pivot"][1]) / 2 + 1.6 * Ra
    y_hip = cr[1] - 1.0 * Rl
    ys = np.arange(int(y_ap), int(y_hip))
    wid = [np.subtract(*_run(body, y, cx(y))[::-1]) for y in ys]
    y_w = float(ys[int(np.argmin(wid))]) if len(ys) else (y_ap + y_hip) / 2
    apl, apr = _run(body, y_ap, cx(y_ap)); wl, wr = _run(body, y_w, cx(y_w)); hl, hr = _run(body, y_hip, cx(y_hip))
    pL, pR = np.array(aL["pivot"]), np.array(aR["pivot"])
    # an arm still touching the body at armpit height makes the run reach out along it: then the armpit sits
    # between the shoulder and the waist instead
    if apr > pR[0] + 0.3 * aR["R"]:
        apr = wr + 0.45 * (pR[0] + 0.6 * aR["R"] - wr)
    if apl < pL[0] - 0.3 * aL["R"]:
        apl = wl + 0.45 * (pL[0] - 0.6 * aL["R"] - wl)
    pts = [(nl, nk[1] - 0.2 * Ra), (nr, nk[1] - 0.2 * Ra),
           (pR[0] + 0.9 * aR["R"], pR[1] + 0.2 * aR["R"]),
           (apr, y_ap), (wr, y_w), (hr, y_hip), (hr - 0.15 * Rl, y_hip + 0.7 * Rl),
           (cr[0], cr[1] + 0.35 * Rl),
           (hl + 0.15 * Rl, y_hip + 0.7 * Rl), (hl, y_hip), (wl, y_w), (apl, y_ap),
           (pL[0] - 0.9 * aL["R"], pL[1] + 0.2 * aL["R"])]
    return [_r(p) for p in pts], y_w, y_hip


def _catmull(pts, n=12):
    """Closed Catmull-Rom curve through the landmarks."""
    P = np.asarray(pts, float); out = []
    for i in range(len(P)):
        p0, p1, p2, p3 = P[i - 1], P[i], P[(i + 1) % len(P)], P[(i + 2) % len(P)]
        for t in np.linspace(0, 1, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    return [tuple(q) for q in out]


def _blur(a, r):
    """Float Gaussian-ish blur (three box passes), no 8-bit banding."""
    r = max(1, int(round(r * 0.8)))
    for _ in range(3):
        for ax in (0, 1):
            p = np.pad(a, [(r + 1, r) if i == ax else (0, 0) for i in (0, 1)], mode="edge")
            c = np.cumsum(p, axis=ax)
            a = (np.take(c, range(2 * r + 1, c.shape[ax]), axis=ax) - np.take(c, range(0, c.shape[ax] - 2 * r - 1), axis=ax)) / (2 * r + 1)
    return a


def _side(reg, p, n):
    """How far the limb reaches from the bone point p along the unit direction n (px)."""
    k = 0
    while k < 150:
        q = (p + n * (k + 1)).astype(int)
        if not (0 <= q[0] < PX and 0 <= q[1] < PX and reg[q[1], q[0]]):
            break
        k += 1
    return float(k)


def _limb_points(reg, p0, p1, r0, r1):
    """A limb segment's outline in its bone's frame: a round cap around each joint (3 points each) + both sides
    measured at the stations (the calf can bulge on one side only). Points run: side +n from the start to the end,
    around the end cap, side -n back, around the start cap."""
    L = float(np.linalg.norm(p1 - p0)) or 1.0; u = (p1 - p0) / L; nv = np.array([-u[1], u[0]])
    sp = [_side(reg, p0 + (p1 - p0) * t, nv) for t in STATIONS]
    sn = [_side(reg, p0 + (p1 - p0) * t, -nv) for t in STATIONS]
    avg = (r0 + r1) / 2
    sp = np.clip(_smooth(sp), 0.4 * avg, 2.5 * avg); sn = np.clip(_smooth(sn), 0.4 * avg, 2.5 * avg)
    def cap(t0, r, degs):   # points on the circle of radius r around the joint at t0
        return [[round(t0 + r * math.cos(math.radians(a)) / L, 3), round(r * math.sin(math.radians(a)), 1)] for a in degs]
    pts = [[round(t, 3), round(float(x), 1)] for t, x in zip(STATIONS, sp)]
    pts += cap(1.0, r1, (60, 0, -60))
    pts += [[round(t, 3), round(-float(x), 1)] for t, x in reversed(list(zip(STATIONS, sn)))]
    pts += cap(0.0, r0, (240, 180, 120))
    return pts


def _from_widths(Lin, name, V, disk):
    """An old editor page (two circles per segment, schemaVersion 1) saved: keep the saved outline's contour, scaled
    to the two circle sizes, with round caps around the new joints. Without a saved outline: a plain tapered shape."""
    r0, r1 = Lin["widths"][0] / 2, Lin["widths"][-1] / 2
    a, b = np.array(V["joints"][name.split(".")[0]][Lin["from"].split(".")[1]], float), np.array(V["joints"][name.split(".")[0]][Lin["to"].split(".")[1]], float)
    Ln = float(np.linalg.norm(b - a)) or 1.0
    def cap(t0, r, degs):
        return [[round(t0 + r * math.cos(math.radians(d)) / Ln, 3), round(r * math.sin(math.radians(d)), 1)] for d in degs]
    D = (disk or {}).get("limbs", {}).get(name, {})
    old_pts = D.get("points")
    if old_pts and len(old_pts) == 16 and disk:
        da = np.array(disk["joints"][name.split(".")[0]][D["from"].split(".")[1]], float)
        db = np.array(disk["joints"][name.split(".")[0]][D["to"].split(".")[1]], float)
        Lo = float(np.linalg.norm(db - da)) or 1.0
        r0o, r1o = max(1.0, -old_pts[14][0] * Lo), max(1.0, (old_pts[6][0] - 1.0) * Lo)
        f = lambda t: r0 / r0o + (r1 / r1o - r0 / r0o) * t
        side = lambda seq: [[t, round(q * f(t), 1)] for t, q in seq]
        return side(old_pts[0:5]) + cap(1.0, r1, (60, 0, -60)) + side(old_pts[8:13]) + cap(0.0, r0, (240, 180, 120))
    ts = STATIONS
    return ([[t, round(r0 + (r1 - r0) * t, 1)] for t in ts] + cap(1.0, r1, (60, 0, -60))
            + [[t, round(-(r0 + (r1 - r0) * t), 1)] for t in reversed(ts)] + cap(0.0, r0, (240, 180, 120)))


def limb_world(V, L):
    """A limb outline's points in rig space (from its bone's frame)."""
    a, b = _joint(V, L["from"]), _joint(V, L["to"])
    d = b - a; n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) or 1.0)
    return [tuple(a + d * t + n * q) for t, q in L["points"]]


def measure_face(B):
    """Eye centres, mouth and centre line from the approved front art: the dark eye blobs inside the head (2-means on
    x), the dark mouth below them. Falls back to chibi proportions."""
    hm = B["pieces"]["head"].rgba[..., 3] > 0; ys, xs = np.nonzero(hm)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max(); H, W = y1 - y0, x1 - x0
    inner = hm.copy()
    for _ in range(8):
        inner &= np.roll(inner, 1, 0) & np.roll(inner, -1, 0) & np.roll(inner, 1, 1) & np.roll(inner, -1, 1)
    lum = np.asarray(B["img"].convert("RGB")).astype(float).mean(-1)
    yy = np.arange(PX)[:, None]
    dy, dx = np.nonzero(inner & (lum < 70) & (yy > y0 + 0.52 * H) & (yy < y0 + 0.8 * H))
    eyes = [[x0 + 0.3 * W, y0 + 0.64 * H], [x0 + 0.62 * W, y0 + 0.64 * H]]
    if len(dx) >= 30:
        c = np.percentile(dx, [20, 80]).astype(float)
        for _ in range(12):
            lab = np.abs(dx[:, None] - c[None]).argmin(1)
            c = np.array([dx[lab == i].mean() if (lab == i).any() else c[i] for i in (0, 1)])
        if (lab == 0).sum() > 8 and (lab == 1).sum() > 8 and abs(c[1] - c[0]) > 0.12 * W:
            eyes = sorted([[float(np.median(dx[lab == i])), float(np.median(dy[lab == i]))] for i in (0, 1)])
    ey = (eyes[0][1] + eyes[1][1]) / 2; mx = (eyes[0][0] + eyes[1][0]) / 2
    my, mxs = np.nonzero(inner & (lum < 90) & (yy > ey + 0.1 * H) & (yy < ey + 0.3 * H)
                         & (np.abs(np.arange(PX)[None, :] - mx) < 0.25 * W))
    mouth = [float(np.median(mxs)), float(np.median(my))] if len(my) >= 4 else [mx, ey + 0.18 * H]
    mid = [mx, ey]
    up = np.array(mid) - np.array(mouth); up = up / (np.linalg.norm(up) or 1.0)
    top = np.array(mid) + up * 0.42 * H
    chin = np.array(mouth) - up * 0.12 * H
    return {"eyes": [_r(e) for e in eyes], "mouth": _r(mouth), "center": [_r(top), _r(mid), _r(chin)]}


def add_face(spec, build="human.unisex"):
    """Give a spec (e.g. one already edited in the Workshop) front face guides measured from the build's art."""
    views, _ = _rig(build)
    spec["views"]["front"]["shapes"]["head"]["face"] = measure_face(views["front"])
    return spec


def measure_view(B):
    m, J, C, jt = B["mask"], B["J"], B["C"], B["joints"]
    joints = {k: {"pivot": _r(jt[k]["pivot"]), "R": round(float(jt[k]["R"]), 1), "mid": _r(C[k]["mid"]),
                  "end": _r(C[k]["end"]), "tip": _r(C[k]["tip"])} for k in LIMBS}
    joints["neck"], joints["pelvis"] = _r(J["neck"]), _r(J["pelvis"])
    cr = jt.get("crotch")
    joints["crotch"] = _r(cr if cr is not None else J["pelvis"])
    limbs = {}
    for k in LIMBS:
        reg = B["regions"][k] & m
        prev_end = None
        for seg, a, b in SEGS:
            p0, p1 = np.array(joints[k][a]), np.array(joints[k][b]); d = pp.unit(p1 - p0)
            w = [float(pp.width_at(reg, p0 + (p1 - p0) * t, d)) for t in np.linspace(0, 1, NW)]
            if seg == "upper":
                w[0] = 2.0 * jt[k]["R"]                       # the shoulder / hip circle
            elif prev_end is not None:
                w[0] = prev_end                               # continuous with the parent at the joint
            # the end circle comes from the limb just inside the joint: right at the joint the next part (a foot,
            # a hand, the other leg) can widen the run
            inner = [float(pp.width_at(reg, p0 + (p1 - p0) * t, d)) for t in (0.75, 0.8, 0.85, 0.9)]
            end = float(np.median(inner))
            if seg == "hand":
                end = 0.75 * end                               # a round fingertip / toe, a little narrower
            elif seg == "lower":                               # shins and forearms taper (the back view's heel
                end = min(end, (0.85 if k.startswith("leg") else 1.0) * w[0])   # sits right behind the ankle)
            prev_end = end
            limbs[f"{k}.{seg}"] = {"from": f"{k}.{a}", "to": f"{k}.{b}",
                                   "points": _limb_points(reg, p0, p1, float(w[0]) / 2, end / 2)}
    torso, y_w, y_hip = _torso_points(B)
    head = _smooth_ring(_radial(_smooth_mask(B["pieces"]["head"].rgba[..., 3] > 0, 3)), 7)[::6]   # 16 landmarks
    # waistband (briefs): between the waist and the hips; bowed for the view from above
    hd = {"points": head}
    if B.get("view") == "front":
        hd["face"] = measure_face(B)
    return {"joints": joints, "limbs": limbs,
            "shapes": {"torso": {"points": torso}, "head": hd},
            "seams": {"waist": {"y": round(float(y_w + 0.6 * (y_hip - y_w)), 1), "bow": 0.18}},
            "order": _order(B)}


def _order(B):
    near_leg, far_leg = B["order"][1], B["order"][0]
    far_arm, near_arm = B["order"][2], B["order"][5]
    limb = lambda k: [f"{k}.hand", f"{k}.lower", f"{k}.upper"]  # child under parent
    return limb(far_arm) + limb(far_leg) + limb(near_leg) + ["torso", "head"] + limb(near_arm)


def _rig(build):
    s = ws.load_state(); b = s["builds"][build]
    src = {f: b[f]["approved"] for f in ("front", "back")}
    hip, views = {}, {}
    for v in ("back", "front"):
        ovr = (b.get("puppet") or {}).get("joints", {}).get(v)
        views[v] = pp._build(Image.open(ai_kit.AI_DIR / src[v]), v, hip, ovr); views[v]["view"] = v
    return views, src


def measure(build="human.unisex", save=True):
    views, src = _rig(build)
    spec = {"schemaVersion": 2, "_doc": __doc__.split("\n\n")[1], "px": PX, "from": {"build": build, **src},
            "made": int(time.time()), "views": {v: measure_view(views[v]) for v in ("front", "back")}}
    if save:
        SPEC.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    return spec


def load():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _pt(v, what):
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and -PX <= x <= 2 * PX for x in v)):
        raise ValueError(f"{what}: expected [x, y] in rig space, got {v!r}")
    return [round(float(x), 1) for x in v]


def save(spec):
    """Validate and write an edited spec (from the Workshop template editor). Keeps `from`; stamps `edited`."""
    old = load() if SPEC.exists() else {}
    out = {"schemaVersion": 2, "_doc": old.get("_doc", __doc__.split("\n\n")[1]), "px": PX,
           "from": old.get("from", spec.get("from")), "made": old.get("made"), "edited": int(time.time()), "views": {}}
    for v in ("front", "back"):
        V = (spec.get("views") or {}).get(v)
        if not V:
            raise ValueError(f"missing view {v}")
        j = {}
        for k in LIMBS:
            jk = V["joints"][k]
            j[k] = {n: _pt(jk[n], f"{v} {k}.{n}") for n in ("pivot", "mid", "end", "tip")}
            j[k]["R"] = round(float(jk["R"]), 1)
            if not 2 <= j[k]["R"] <= 120:
                raise ValueError(f"{v} {k}: joint radius {j[k]['R']} out of range")
        for n in ("neck", "pelvis", "crotch"):
            j[n] = _pt(V["joints"][n], f"{v} {n}")
        limbs = {}
        for k in LIMBS:
            for seg, a, b in SEGS:
                Lin = V["limbs"][f"{k}.{seg}"]
                pts = Lin.get("points") or (_from_widths(Lin, f"{k}.{seg}", V, old.get("views", {}).get(v)) if "widths" in Lin else [])
                ok = len(pts) >= 6 and all(isinstance(q, (list, tuple)) and len(q) == 2 and all(isinstance(c, (int, float)) for c in q)
                                           and -3 <= q[0] <= 4 and abs(q[1]) <= 250 for q in pts)
                if not ok:
                    raise ValueError(f"{v} {k}.{seg}: needs 6+ outline points [t, n] (t along the bone, n px to the side)")
                limbs[f"{k}.{seg}"] = {"from": f"{k}.{a}", "to": f"{k}.{b}",
                                       "points": [[round(float(t), 3), round(float(q), 1)] for t, q in pts]}
        shapes = {}
        for n in ("torso", "head"):
            pts = V["shapes"][n].get("points")
            if not pts or len(pts) < 6:
                raise ValueError(f"{v} {n}: needs at least 6 outline points")
            shapes[n] = {"points": [_pt(p, f"{v} {n} point") for p in pts]}
            fc = V["shapes"][n].get("face")
            if n == "head" and fc:
                shapes[n]["face"] = {"eyes": [_pt(p, f"{v} eye") for p in fc["eyes"]][:2], "mouth": _pt(fc["mouth"], f"{v} mouth"),
                                     "center": [_pt(p, f"{v} face centre") for p in fc["center"]]}
                if len(shapes[n]["face"]["eyes"]) != 2 or len(shapes[n]["face"]["center"]) < 2:
                    raise ValueError(f"{v} face: needs 2 eyes and a centre line")
        w = V["seams"]["waist"]
        seams = {"waist": {"y": round(float(w["y"]), 1), "bow": round(float(w.get("bow", 0.18)), 3)}}
        order = V.get("order") or (old.get("views", {}).get(v, {}).get("order"))
        if sorted(order) != sorted(list(limbs) + ["torso", "head"]):
            raise ValueError(f"{v}: draw order must list every part once")
        out["views"][v] = {"joints": j, "limbs": limbs, "shapes": shapes, "seams": seams, "order": order}
    SPEC.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def state():
    """What the Workshop template editor needs: the spec, the art it was measured from, the preview files."""
    spec = load(); out = out_dir()
    frm = spec.get("from") or {}
    art = {v: frm.get(v) for v in ("front", "back") if frm.get(v) and (ai_kit.AI_DIR / frm[v]).exists()}
    files = sorted(p.name for p in out.glob("*.*")) if out.exists() else []
    made = int(max((p.stat().st_mtime for p in out.glob("*.*")), default=0)) if out.exists() else 0
    return {"spec": spec, "art": art, "dir": ws.rel(out), "files": files, "preview_made": made}


def rig_from_spec(spec, view):
    """The rig the walk needs (neck, pelvis, per limb: pivot, R, mid, end, tip), straight from the template: the
    template is the rig, no detection on the art."""
    V = spec["views"][view]; a = lambda p: np.array(p, float)
    jt = {k: {"pivot": a(V["joints"][k]["pivot"]), "R": float(V["joints"][k]["R"])} for k in LIMBS}
    C = {k: {n: a(V["joints"][k][n]) for n in ("mid", "end", "tip")} for k in LIMBS}
    jt["crotch"] = a(V["joints"]["crotch"])
    J = {"neck": a(V["joints"]["neck"]), "pelvis": a(V["joints"]["pelvis"])}
    return {"J": J, "C": C, "joints": jt, "order": ["legA", "legB", "armA", "torso", "head", "armB"], "soft": {}}


# ----------------------------------------------------------------------------- rendering (from the spec only)

def _joint(V, name):
    if "." in name:
        k, j = name.split(".")
        return np.array(V["joints"][k][j], float)
    return np.array(V["joints"][name], float)




def _poly_cov(N, s, poly, soft=1.5):
    big = Image.new("L", (2 * N, 2 * N), 0)
    ImageDraw.Draw(big).polygon([(x * 2 * s, y * 2 * s) for x, y in poly], fill=255)
    big = big.filter(ImageFilter.GaussianBlur(soft * s))
    big = big.point(lambda v: 255 if v > 127 else 0).resize((N, N), Image.BOX)
    return np.asarray(big, float) / 255


def _shade(cov, thick):
    """Generic round clay shading: a soft dome from the blurred shape, lit from the top left."""
    h = _blur(cov, max(2, 0.55 * thick))
    gy, gx = np.gradient(h)
    k = 1.6 * thick
    n = np.dstack([-gx * k, -gy * k, np.ones_like(h)]); n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lam = np.clip(n @ LIGHT, 0, 1)
    v = 0.62 + 0.42 * lam
    rgb = np.clip(np.array(CLAY, float)[None, None] * v[..., None], 0, 255)
    return rgb


def _outline(rgb, cov, w):
    inner = np.asarray(Image.fromarray((cov * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * int(w) + 1)), float) / 255
    rim = np.clip(cov - inner, 0, 1)[..., None]
    return rgb * (1 - rim) + np.array(LINE, float)[None, None] * rim


def _waist(V, s, torso_cov):
    """The waistband as a bowed line across the torso's width at the seam height."""
    sm = V["seams"]["waist"]; y = sm["y"]
    row = np.nonzero(torso_cov[int(y * s)] > 0.5)[0]
    if not len(row):
        return []
    x0, x1 = row.min() / s, row.max() / s; cx, hw = (x0 + x1) / 2, (x1 - x0) / 2
    return [(cx + hw * u, y + sm["bow"] * hw * (1 - u * u)) for u in np.linspace(-1, 1, 25)]


def render(spec, view, s=2):
    """Every part as its own whole image on the rig canvas (512*s px): (rgba, guides rgba, pivot in 512 space)."""
    V = spec["views"][view]; N = PX * s
    covs, thick, bones = {}, {}, {}
    for name, L in V["limbs"].items():
        p0, p1 = _joint(V, L["from"]), _joint(V, L["to"])
        covs[name] = _poly_cov(N, s, _catmull(limb_world(V, L)), soft=0.6)
        thick[name] = float(np.mean([abs(q) for _, q in L["points"]])) * s
        bones[name] = (p0, p1)
    for name in ("torso", "head"):
        sh = V["shapes"][name]
        covs[name] = _poly_cov(N, s, _catmull(sh["points"]) if "points" in sh else sh["poly"])
        thick[name] = math.sqrt(covs[name].sum() / math.pi) * 0.75
    piv = {n: _joint(V, L["from"]) for n, L in V["limbs"].items()}
    piv["torso"], piv["head"] = _joint(V, "pelvis"), _joint(V, "neck")
    out = {}
    for name, cov in covs.items():
        rgb = _outline(_shade(cov, thick[name]), cov, 1.6 * s)
        rgba = np.dstack([rgb, cov * 255]).astype(np.uint8)
        g = Image.new("RGBA", (N, N)); d = ImageDraw.Draw(g)
        if name in bones:
            a, b = bones[name] * s if False else (bones[name][0] * s, bones[name][1] * s)
            d.line([tuple(a), tuple(b)], fill=GUIDE_BONE, width=max(1, s))
            for q in (a, b):
                d.ellipse([q[0] - 3 * s, q[1] - 3 * s, q[0] + 3 * s, q[1] + 3 * s], outline=GUIDE_BONE, width=max(1, s))
        if name == "torso":
            nk, pv = _joint(V, "neck") * s, _joint(V, "pelvis") * s
            d.line([tuple(nk), tuple(pv)], fill=GUIDE_BONE, width=max(1, s))
            w = _waist(V, s, cov)
            if w:
                d.line([(x * s, y * s) for x, y in w], fill=GUIDE_SEAM, width=2 * s)
        fc = V["shapes"]["head"].get("face") if name == "head" else None
        if fc:
            e0, e1 = np.array(fc["eyes"][0]) * s, np.array(fc["eyes"][1]) * s; ext = (e1 - e0) * 0.35
            d.line([tuple(e0 - ext), tuple(e1 + ext)], fill=GUIDE_FACE, width=max(1, s))          # eye line
            r = 0.09 * np.linalg.norm(e1 - e0)
            for e in (e0, e1):
                d.ellipse([e[0] - r, e[1] - 1.3 * r, e[0] + r, e[1] + 1.3 * r], outline=GUIDE_FACE, width=max(1, s))
            m = np.array(fc["mouth"]) * s
            d.line([(m[0] - r, m[1]), (m[0] + r, m[1])], fill=GUIDE_FACE, width=2 * s)              # mouth
            d.line([(x * s, y * s) for x, y in fc["center"]], fill=GUIDE_FACE, width=max(1, s))     # centre line
        if name.startswith("leg") and name.endswith(".upper"):
            # leg hole: the torso's lower edge where it crosses this thigh (shared with the torso's own edge)
            tc = covs["torso"] > 0.5; edge = tc & ~np.roll(tc, -1, 0)
            ys, xs = np.nonzero(edge & (cov > 0.5) & (np.arange(N)[:, None] > _joint(V, "neck")[1] * s + 0.5 * (_joint(V, "crotch")[1] - _joint(V, "neck")[1]) * s))
            for x, y in zip(xs, ys):
                d.ellipse([x - s, y - s, x + s, y + s], fill=GUIDE_SEAM)
        ga = np.asarray(g).copy(); ga[..., 3] = (ga[..., 3] * (cov > 0.02)).astype(np.uint8)
        out[name] = (rgba, ga, piv[name])
    return out


# ----------------------------------------------------------------------------- previews (rest pose, parts, walk)

def _save(im, path, **kw):
    """Save, retrying briefly: a viewer or virus scan can hold a fresh preview file for a moment (Errno 22 on Windows)."""
    for i in range(10):
        try:
            return im.save(path, **kw)
        except OSError:
            if i == 9:
                raise
            time.sleep(0.4)


def _compose(parts, order, guides=False):
    N = next(iter(parts.values()))[0].shape[0]
    cv = Image.new("RGBA", (N, N))
    for n in order:
        cv.alpha_composite(Image.fromarray(parts[n][0]))
        if guides:
            cv.alpha_composite(Image.fromarray(parts[n][1]))
    return cv


def _exploded(parts, order, guides=True):
    tiles = []
    f = ImageFont.load_default(size=16)
    for n in order:
        rgba, g, _ = parts[n]; a = rgba[..., 3] > 8
        ys, xs = np.nonzero(a)
        im = Image.fromarray(rgba)
        if guides:
            im.alpha_composite(Image.fromarray(g))
        im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
        t = Image.new("RGB", (im.width + 12, im.height + 30), (90, 90, 90))
        bg = Image.new("RGBA", im.size, (225, 225, 230, 255)); bg.alpha_composite(im)
        t.paste(bg.convert("RGB"), (6, 26)); ImageDraw.Draw(t).text((6, 4), n, fill="white", font=f)
        tiles.append(t)
    W = sum(t.width for t in tiles) + 6 * len(tiles); H = max(t.height for t in tiles)
    sh = Image.new("RGB", (W, H), (60, 60, 60)); x = 0
    for t in tiles:
        sh.paste(t, (x, 0)); x += t.width + 6
    return sh


def rig_from(B, parts):
    """Swap a puppet rig's pieces for the template's parts (512 px), so the existing walk can drive them."""
    def at512(rgba):  # hard alpha: the rig copies every pixel with alpha > 0, so soft edges would leave halos
        a = np.asarray(Image.fromarray(rgba).resize((PX, PX), Image.LANCZOS)).copy()
        a[..., 3] = np.where(a[..., 3] > 127, 255, 0)
        return a
    pieces = {n: (at512(parts[n][0]), parts[n][2], n) for n in parts}
    # all rigid (Klei style): the thigh's round top sits under the torso, so no soft hip band is needed
    B2 = dict(B); B2["soft"] = {}
    return B2, pieces


def preview(spec=None, s=2, frames=8):
    """Render every part from the spec and walk them on the rig made from the same spec."""
    spec = spec or load()
    out = out_dir(); out.mkdir(parents=True, exist_ok=True)
    frm = spec.get("from") or {}
    res = {}
    for v in ("front", "back"):
        V = spec["views"][v]; parts = render(spec, v, s); order = V["order"]
        (out / v).mkdir(exist_ok=True)
        for n, (rgba, g, _) in parts.items():
            _save(Image.fromarray(rgba), out / v / f"{n}.png"); _save(Image.fromarray(g), out / v / f"{n}.guides.png")
        _save(_exploded(parts, order), out / f"parts_{v}.png")
        rest = _compose(parts, order); rest_g = _compose(parts, order, guides=True)
        B2, pieces = rig_from(rig_from_spec(spec, v), parts)
        B2["rigid"] = (pieces, order)
        imgs = [pp.on_bg(pp.render(B2, v, i / frames)) for i in range(frames)]
        _save(imgs[0], out / f"walk_{v}.gif", save_all=True, append_images=imgs[1:], duration=120, loop=0)
        art_p = ai_kit.AI_DIR / frm[v] if frm.get(v) else None
        art = Image.open(art_p).convert("RGB").resize((PX, PX)) if art_p and art_p.exists() else Image.new("RGB", (PX, PX), pp.BG)
        res[v] = {"rest": rest, "guides": rest_g, "walk": imgs[2], "art": art}
    T = 400; f = ImageFont.load_default(size=18)
    cols = (("art", "approved unisex art"), ("rest", "template: rest pose"), ("guides", "template + guides"), ("walk", "template on the walk"))
    sheet = Image.new("RGB", (len(cols) * T, 2 * T + 34), (60, 60, 60)); d = ImageDraw.Draw(sheet)
    for c, (key, label) in enumerate(cols):
        d.text((c * T + 8, 8), label, fill="white", font=f)
        for r, v in enumerate(("front", "back")):
            im = res[v][key]
            im = pp.on_bg(np.asarray(im.convert("RGBA").resize((PX, PX), Image.LANCZOS))) if key in ("rest", "guides") else im.convert("RGB")
            sheet.paste(im.resize((T, T), Image.LANCZOS), (c * T, 34 + r * T))
    _save(sheet, out / "template_sheet.png")
    return {"dir": ws.rel(out), "files": sorted(p.name for p in out.glob("*.*"))}


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("tpl", gpu=False, reload=("puppet", "puppet_template"))
def _job_preview(a):   # clean puppet template: preview
    return preview(None)


@jobs.kind("tpl_measure", gpu=False, reload=("puppet", "puppet_template"))
def _job_measure(a):   # re-measure from the art, then preview
    return preview(measure(a.get("build", "human.unisex")))
