"""2D marionette puppet from an approved A-pose mannequin pair (front-3/4 + back-3/4), as worked out in the puppet tests
of 2026-10-21 (vault: Hearthdelve - Art Pipeline, "Proposed: 2D puppet"; scripts in work/puppet_test2/scripts).

From the art itself: skeleton (shoulder/elbow/wrist, hip/knee/ankle), limb-width shoulder and hip circles (wholly owned
by the limb), the torso complete underneath with a flesh armpit backfill, a curved leotard cut at the hips, and rigid
pieces per bone (child joint ends rounded and tucked under the parent). Thighs get a soft band from pelvis to thigh.
Motion for the preview: a foot-driven IK walk (marionette).

    template(build) -> writes sprites/ai/puppet/<build>/ (pieces per view, template.json, previews) and returns a summary
"""
import json, math, time

import heapq
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import imageops, jobs, rig, workshop as ws

PX = rig.PX               # the rig canvas (rest pose)
PAD = (32, 64)            # where the old 512 frame (and the 512 px mannequins) sits in it: headroom for hair and helmets
BG = (196, 198, 204)
yy, xx = np.mgrid[:PX, :PX]
FWD = {"front": None, "back": None}  # set below (needs unit())
SOFT = {}


# ----------------------------------------------------------------------------- masks and geometry helpers

def grow(m, n=1):
    for _ in range(n):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    return m


def thickness(mask):
    """Erosions until each pixel goes = distance to the edge (4-neighbour)."""
    t = np.zeros(mask.shape, np.int32); cur = mask.copy(); k = 0
    while cur.any():
        k += 1; t[cur] = k
        cur = cur & np.roll(cur, 1, 0) & np.roll(cur, -1, 0) & np.roll(cur, 1, 1) & np.roll(cur, -1, 1)
    return t


def component(mask, seed):
    """Connected region of `mask` containing pixel seed (y, x)."""
    if not mask[seed]:
        ys, xs = np.nonzero(mask); i = np.argmin((ys - seed[0]) ** 2 + (xs - seed[1]) ** 2); seed = (ys[i], xs[i])
    reg = np.zeros_like(mask); reg[seed] = True
    while True:
        nxt = grow(reg) & mask
        if (nxt == reg).all():
            return reg
        reg = nxt


def medial_path(mask, th, start, goal):
    """Cheapest 8-connected path start -> goal inside the mask, preferring thick (central) pixels."""
    H, W = mask.shape
    cost = 1.0 / (th.astype(float) + 0.5) ** 2
    dist = {start: 0.0}; prev = {}; pq = [(0.0, start)]
    nb = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    while pq:
        dcur, p = heapq.heappop(pq)
        if p == goal:
            break
        if dcur > dist.get(p, 1e18):
            continue
        for dy, dx in nb:
            q = (p[0] + dy, p[1] + dx)
            if 0 <= q[0] < H and 0 <= q[1] < W and mask[q]:
                nd = dcur + cost[q] * (1.414 if dy and dx else 1.0)
                if nd < dist.get(q, 1e18):
                    dist[q] = nd; prev[q] = p; heapq.heappush(pq, (nd, q))
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]  # start ... goal


def at_frac(path, f):
    L = np.cumsum([0] + [math.dist(path[i], path[i + 1]) for i in range(len(path) - 1)])
    i = int(np.searchsorted(L, f * L[-1])); i = min(i, len(path) - 1)
    return np.array(path[i][::-1], float)  # (x, y)


def skeleton(img):
    m = imageops.clean(imageops.figure_mask(img))
    th = thickness(m)
    ys, xs = np.nonzero(m); y0, y1 = ys.min(), ys.max(); H = y1 - y0
    widths = m.sum(1)
    lo, hi = int(y0 + 0.28 * H), int(y0 + 0.6 * H)
    chin = lo + int(np.argmin(widths[lo:hi]))
    neck = np.array([xs[ys == chin].mean(), chin], float)
    # torso core: the thick middle below the chin
    body_rows = (ys > chin) & (ys < y0 + 0.75 * H)
    cy = int(np.median(ys[body_rows])); cx = int(np.median(xs[body_rows]))
    J = {"neck": neck, "top": np.array([xs[ys == y0].mean(), y0], float)}
    # hands: farthest left / right points between the chin and the crotch region
    arm_zone = m & (np.arange(PX)[:, None] > chin) & (np.arange(PX)[:, None] < y0 + 0.75 * H)
    ay, ax = np.nonzero(arm_zone)
    tips = {"armA": (ay[np.argmin(ax)], ax[np.argmin(ax)]), "armB": (ay[np.argmax(ax)], ax[np.argmax(ax)])}
    # feet: lowest point on each side of the body centre
    low = m & (np.arange(PX)[:, None] > y0 + 0.8 * H)
    ly, lx = np.nonzero(low)
    for k, side in (("legA", lx < cx), ("legB", lx >= cx)):
        i = np.argmax(np.where(side, ly, -1)); tips[k] = (ly[i], lx[i])
    torso_th = th[cy, cx]
    chains = {}
    for k, tip in tips.items():
        path = medial_path(m, th, tip, (cy, cx))
        prof = np.array([th[p] for p in path])
        limb_th = np.median(prof[: max(3, len(prof) // 3)])
        enter = next((i for i, t in enumerate(prof) if t > max(1.7 * limb_th, 0.55 * torso_th)), len(prof) - 1)
        if k.startswith("leg"):  # hips: where the leg path reaches the crotch row (top of the gap between the legs)
            gap_rows = [y for y in range(int(y0 + 0.5 * H), y1) if _has_gap(m[y], cx)]
            crotch = min(gap_rows) if gap_rows else int(y0 + 0.7 * H)
            enter = next((i for i, p in enumerate(path) if p[0] <= crotch), enter)
        limb = path[:enter + 1][::-1]  # root (shoulder/hip) ... tip
        root = np.array(limb[0][::-1], float)
        # wrist / ankle: the narrowest point of the limb before the hand or foot widens out
        lp = np.array([th[q] for q in limb], float)
        i0, i1 = int(0.55 * len(limb)), max(int(0.55 * len(limb)) + 1, int(0.8 * len(limb)))  # before the fingers / toes
        iw = i0 + int(np.argmin(lp[i0:i1]))
        end = np.array(limb[iw][::-1], float)
        if k.startswith("leg"):
            # ankle = the top of the foot: scanning up from the sole, the first row where the leg is no wider than
            # ~1.3x the shin (the foot rows are much wider); centred on that row's run
            pts_ = np.array(limb, int)                           # (y, x), root ... tip
            def run_at(y, x):
                if not (0 <= y < PX and 0 <= x < PX and m[y, x]):
                    return None
                x0_ = x
                while x0_ > 0 and m[y, x0_ - 1]: x0_ -= 1
                x1_ = x
                while x1_ < PX - 1 and m[y, x1_ + 1]: x1_ += 1
                return x0_, x1_
            def path_x(y):
                return int(pts_[np.argmin(np.abs(pts_[:, 0] - y)), 1])
            ys_ = pts_[:, 0]; ylo, yhi = int(ys_[int(0.45 * len(pts_))]), int(ys_.max())
            mids = [run_at(y, path_x(y)) for y in range(ylo, ylo + max(3, (yhi - ylo) // 3))]
            shin_w = float(np.median([r[1] - r[0] + 1 for r in mids if r])) if any(mids) else 20.0
            ank_y, in_foot = None, False
            for y in range(yhi, ylo, -1):                         # from the sole up: through the wide foot rows ...
                r = run_at(y, path_x(y))
                if not r:
                    continue
                w_ = r[1] - r[0] + 1
                if w_ > 1.5 * shin_w:
                    in_foot = True
                elif in_foot and w_ <= 1.3 * shin_w:              # ... to where the leg is shin-wide again
                    ank_y = y; break
            sole = int(ys_.max())
            ank_y = int(round(sole - 0.2 * (sole - float(ys_.min()))))  # the ankle: ~20% of the leg height above the sole
            if ank_y is not None:
                r = run_at(ank_y, path_x(ank_y))
                end = np.array([(r[0] + r[1]) / 2.0, float(ank_y)])
                iw = int(np.argmin(np.hypot(pts_[:, 1] - end[0], pts_[:, 0] - end[1])))
        mid = at_frac(limb[:iw + 1], 0.5)  # elbow / knee: halfway to the wrist / ankle (redone from the pivot later)
        chains[k] = {"root": root, "mid": mid, "end": end, "tip": np.array(tip[::-1], float), "path": limb, "iw": iw}
    hips = [chains[k]["root"] for k in ("legA", "legB")]
    J["pelvis"] = (hips[0] + hips[1]) / 2
    return m, th, J, chains, chin


def _has_gap(row, cx):
    xs = np.nonzero(row)[0]
    if len(xs) < 2:
        return False
    inside = row[xs.min():xs.max() + 1]
    return (~inside).any()


def halfplane(shape, p, direction):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return ((xx - p[0]) * direction[0] + (yy - p[1]) * direction[1]) > 0


def unit(v):
    return v / max(np.linalg.norm(v), 1e-6)


def segment_parts(m, J, chains, chin, th):
    parts = {}
    taken = np.zeros_like(m)
    yy, xx = np.mgrid[:PX, :PX]

    def dseg(a, b):
        """Squared distance of every pixel to the segment a-b."""
        ab = b - a; t = np.clip(((xx - a[0]) * ab[0] + (yy - a[1]) * ab[1]) / max(ab @ ab, 1e-6), 0, 1)
        return (xx - (a[0] + t * ab[0])) ** 2 + (yy - (a[1] + t * ab[1])) ** 2

    def dchain(c):
        return np.minimum.reduce([dseg(c["root"], c["mid"]), dseg(c["mid"], c["end"]), dseg(c["end"], c["tip"])])

    torso_d = dseg(J["pelvis"], J["neck"])
    D = {k: dchain(chains[k]) for k in chains}
    for k, c in chains.items():
        reach = (2.6 * max(6, th[int(c["mid"][1]), int(c["mid"][0])]) + 10) ** 2
        others = [D[o] for o in chains if o != k]
        mine = (D[k] < torso_d) & (D[k] < np.minimum.reduce(others)) & (D[k] < reach)
        parts[k] = m & mine & halfplane(m.shape, c["root"], unit(c["mid"] - c["root"]))
        taken |= parts[k]
    head = m & ~taken & (np.arange(PX)[:, None] <= chin)
    parts["head"] = head
    parts["torso"] = m & ~taken & ~head
    return parts


# ----------------------------------------------------------------------------- skinned pieces

class Piece:
    """A part image on a triangle grid; each vertex has weights to named bones (2D rigid transforms)."""
    def __init__(self, rgba, region, weights_fn, cell=6):
        self.rgba = rgba
        ys, xs = np.nonzero(region)
        x0, x1, y0, y1 = xs.min() - cell, xs.max() + cell, ys.min() - cell, ys.max() + cell
        gx = np.arange(x0, x1 + cell, cell); gy = np.arange(y0, y1 + cell, cell)
        V = np.array([(x, y) for y in gy for x in gx], float); nx = len(gx)
        reg = grow(region, cell)
        tris = []
        for j in range(len(gy) - 1):
            for i in range(nx - 1):
                a, b, c, e = j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1
                cxp, cyp = int(np.clip(gx[i] + cell / 2, 0, PX - 1)), int(np.clip(gy[j] + cell / 2, 0, PX - 1))
                if reg[cyp, cxp]:
                    tris += [(a, b, c), (b, e, c)]
        self.V, self.T = V, np.array(tris)
        self.W = [weights_fn(v) for v in V]  # list of {bone: w}

    def deform(self, X):
        out = np.zeros_like(self.V)
        for i, (v, w) in enumerate(zip(self.V, self.W)):
            out[i] = sum(wt * X[b](v) for b, wt in w.items())
        return out

    def draw(self, canvas, X):
        D = self.deform(X)
        src = self.rgba
        for t in self.T:
            s, q = self.V[t], D[t]
            x0, x1 = int(max(0, np.floor(q[:, 0].min()))), int(min(PX - 1, np.ceil(q[:, 0].max())))
            y0, y1 = int(max(0, np.floor(q[:, 1].min()))), int(min(PX - 1, np.ceil(q[:, 1].max())))
            if x1 < x0 or y1 < y0:
                continue
            M = np.array([[q[1, 0] - q[0, 0], q[2, 0] - q[0, 0]], [q[1, 1] - q[0, 1], q[2, 1] - q[0, 1]]])
            S = np.array([[s[1, 0] - s[0, 0], s[2, 0] - s[0, 0]], [s[1, 1] - s[0, 1], s[2, 1] - s[0, 1]]])
            if abs(np.linalg.det(M)) < 1e-6 or abs(np.linalg.det(M)) > 3.0 * abs(np.linalg.det(S)):
                continue  # degenerate or over-stretched triangle: skip rather than smear a spike
            Mi = np.linalg.inv(M)
            yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
            rel = np.stack([xx - q[0, 0] + 0.5, yy - q[0, 1] + 0.5], -1) @ Mi.T
            u, v = rel[..., 0], rel[..., 1]
            inside = (u >= -0.02) & (v >= -0.02) & (u + v <= 1.02)
            if not inside.any():
                continue
            sx = s[0, 0] + u * (s[1, 0] - s[0, 0]) + v * (s[2, 0] - s[0, 0])
            sy = s[0, 1] + u * (s[1, 1] - s[0, 1]) + v * (s[2, 1] - s[0, 1])
            sxi = np.clip(np.floor(sx).astype(int), 0, PX - 1); syi = np.clip(np.floor(sy).astype(int), 0, PX - 1)
            px = src[syi, sxi]
            ok = inside & (px[..., 3] > 0)
            reg = canvas[y0:y1 + 1, x0:x1 + 1]
            reg[ok] = px[ok]


def rot(theta, pivot, new_pivot):
    c, s = math.cos(theta), math.sin(theta); R = np.array([[c, -s], [s, c]])
    return lambda v: new_pivot + R @ (v - pivot)


def ang(v):
    return math.atan2(v[1], v[0])


# ----------------------------------------------------------------------------- build one view

def framed(img):
    """A 512 px mannequin on the PX rig canvas at PAD (padded with its own corner colour)."""
    if img.size == (PX, PX):
        return img
    out = Image.new(img.mode, (PX, PX), img.getpixel((0, 0))); out.paste(img, PAD)
    return out


def build_view(img, view, hip_frac, overrides=None):
    """One view of the detection puppet from mannequin art (the Template measures its rig from it). Test 3: rounded joints with pivots inside them, torso complete underneath, chest never on an arm,
    shorts stay on the torso (legs start inside them, drawn under the torso)."""
    img = framed(img.convert("RGB"))
    a = np.asarray(img); ai = a.astype(int)
    m, th, J, C, chin = skeleton(img)
    P = segment_parts(m, J, C, chin, th)
    rgba = np.dstack([a, np.where(m, 255, 0).astype(np.uint8)])
    yy, xx = np.mgrid[:PX, :PX]
    mx, mn = ai.max(-1), ai.min(-1)
    grey = m & ((mx - mn) < 0.2 * np.maximum(mx, 1)) & (mx > 60) & (mx < 235)  # the grey underclothes (test only)
    core = grey.copy()
    for _ in range(3):  # drop the thin anti-aliased edges of limbs, which are grey-ish too
        core &= np.roll(core, 1, 0) & np.roll(core, -1, 0) & np.roll(core, 1, 1) & np.roll(core, -1, 1)
    core = imageops.clean(grow(core, 3) & grey, min_area=300)
    skin = m & (ai[..., 0] > ai[..., 2] + 25) & ((mx - mn) > 0.2 * np.maximum(mx, 1))
    cloth = core | (grow(core, 3) & m & ~skin)  # the garment, with its own dark outline (the hem keeps its line)
    disc = lambda p, r: ((xx - p[0]) ** 2 + (yy - p[1]) ** 2) <= r * r
    # limbs A = screen-left, B = screen-right; in both 3/4 views (front facing left, back facing away right) the near
    # limbs are the screen-right ones
    near = {"arm": "armB", "leg": "legB"}
    joints, regions = {}, {}
    torso = m & ~P["head"]
    inner = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    ey, ex = np.nonzero(m & ~inner)                     # the silhouette outline
    E = np.stack([ex, ey], 1).astype(float)
    cx_body = float(np.median(np.nonzero(m[int(J["pelvis"][1])])[0]))

    def fit_circle(pts):
        """Least-squares circle through outline points (Kasa)."""
        A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))]); b = -(pts ** 2).sum(1)
        D, Ee, F = np.linalg.lstsq(A, b, rcond=None)[0]
        c = np.array([-D / 2, -Ee / 2]); return c, float(np.sqrt(max(c @ c - F, 1.0)))

    # crotch: top of the gap between the legs
    crotch = None
    for y in range(int(J["pelvis"][1]) - 30, PX):
        xs_ = np.nonzero(m[y])[0]
        if len(xs_) < 2:
            continue
        gap = np.nonzero(~m[y, xs_.min():xs_.max() + 1])[0]
        near_c = gap[np.abs(gap + xs_.min() - cx_body) < 40]
        if len(near_c):
            crotch = np.array([float(near_c.mean() + xs_.min()), float(y)]); break
    joints["crotch"] = crotch
    fitpts = {}
    lum = ai.mean(-1)

    def width_in(region, q0, n):
        """The limb's own width at q0, measured across it (along n) inside its own region."""
        def run(sgn_):
            n_ = 0
            while n_ < 120:
                q = (q0 + n * sgn_ * (n_ + 1)).astype(int)
                if not (0 <= q[0] < PX and 0 <= q[1] < PX and region[q[1], q[0]]):
                    break
                n_ += 1
            return n_
        return run(1) + run(-1) + 1

    def path_at(c, pv, f):
        """Point a fraction f of the way from the pivot to the wrist/ankle, along the limb's centreline."""
        path = np.array([q[::-1] for q in c["path"]], float)
        i0 = int(np.argmin(np.hypot(*(path - pv).T))); iw = c["iw"]
        seg = path[min(i0, iw):iw + 1]
        if len(seg) < 2:
            return pv + f * (c["end"] - pv)
        Lc = np.cumsum([0] + [np.linalg.norm(seg[i + 1] - seg[i]) for i in range(len(seg) - 1)])
        return seg[int(np.clip(np.searchsorted(Lc, f * Lc[-1]), 0, len(seg) - 1))]

    def dist_poly(pts_):
        d2 = np.full((PX, PX), 1e12)
        for i in range(len(pts_) - 1):
            a_, b_ = pts_[i], pts_[i + 1]; ab = b_ - a_
            t_ = np.clip(((xx - a_[0]) * ab[0] + (yy - a_[1]) * ab[1]) / max(ab @ ab, 1e-6), 0, 1)
            d2 = np.minimum(d2, (xx - (a_[0] + t_ * ab[0])) ** 2 + (yy - (a_[1] + t_ * ab[1])) ** 2)
        return np.sqrt(d2)

    def livewire(a_pt, b_pt, allowed):
        """Cheapest 8-connected path a -> b inside `allowed`; dark pixels (lines drawn in the art) are cheap."""
        cost = 0.04 + (lum / 255.0) ** 2
        st, goal = (int(a_pt[1]), int(a_pt[0])), (int(b_pt[1]), int(b_pt[0]))
        dist = {st: 0.0}; prev = {}; pq = [(0.0, st)]
        while pq:
            dc, q = heapq.heappop(pq)
            if q == goal:
                break
            if dc > dist.get(q, 1e18):
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                r_ = (q[0] + dy, q[1] + dx)
                if 0 <= r_[0] < PX and 0 <= r_[1] < PX and allowed[r_]:
                    nd = dc + cost[r_] * (1.414 if dy and dx else 1.0)
                    if nd < dist.get(r_, 1e18):
                        dist[r_] = nd; prev[r_] = q; heapq.heappush(pq, (nd, r_))
        if goal != st and goal not in prev:
            return None
        out_ = [goal]
        while out_[-1] != st:
            out_.append(prev[out_[-1]])
        return [np.array([q[1], q[0]], float) for q in out_[::-1]]

    torso_arms_guard = np.zeros_like(m)
    for k in ("armA", "armB", "legA", "legB"):
        c = C[k]; root, mid0 = c["root"], c["mid"]; u = unit(mid0 - root)
        n = np.array([-u[1], u[0]])
        R = max(6.0, min(width_in(P[k], root + f * (mid0 - root), n) for f in (0.35, 0.5, 0.65, 0.8)) / 2.0)  # half the limb's own (smallest) width
        if k.startswith("arm"):
            nup = n if n[1] < 0 else -n
            rel = E - root; along, perp = rel @ u, rel @ nup
            head_zone = grow(P["head"], 6)
            sel = ((along > -1.8 * R) & (along < 0.9 * R) & (perp > 0.2 * R) & (perp < 2.4 * R)
                   & ~head_zone[E[:, 1].astype(int), E[:, 0].astype(int)])
            pts = E[sel]
            cands = [root + nup * sv + u * tv for sv in np.linspace(0, 0.6 * R, 7) for tv in np.linspace(-0.6 * R, 0.3 * R, 10)]  # pinned to where the arm meets the body
            other = np.zeros_like(m)
        else:
            cpx = crotch[0] if crotch is not None else cx_body
            sgn = 1.0 if root[0] > cpx else -1.0
            nout = np.array([sgn, 0.0])
            rel = E - root
            sel = ((rel[:, 0] * sgn > 0.2 * R) & (rel[:, 0] * sgn < 3.0 * R)
                   & (E[:, 1] > root[1] - 2.5 * R) & (E[:, 1] < root[1] + 1.0 * R))
            pts = E[sel]
            # hip joint height: the outer outline's bulge (the hip from the front, the buttock from behind)
            fy0, fy1 = np.nonzero(m)[0].min(), np.nonzero(m)[0].max()
            if view == "front" and hip_frac.get(k) is not None:  # same body: the back view's buttock height
                cy_ = fy0 + hip_frac[k] * (fy1 - fy0)
            else:
                cy_ = float(pts[np.argmax((pts[:, 0] - root[0]) * sgn), 1]) if len(pts) else root[1] - R
            cands = [np.array([cpx + sgn * tv, cy_ + dv]) for tv in np.linspace(0.3 * R, 4.0 * R, 38) for dv in (-0.3 * R, 0.0, 0.3 * R)]
            other = (xx - cpx) * sgn < 0                                    # the other leg's side
            if crotch is not None:  # hip circle at the crotch's height, diameter = the leg's width there (overlaps the V)
                cyc = int(crotch[1])
                for yy_ in range(cyc, min(PX - 1, cyc + 40)):  # first row below the crotch where the legs are apart
                    row = m[yy_]; x_ = int(cpx + sgn * 2)
                    while 0 <= x_ < PX and not row[x_]:
                        x_ += int(sgn)
                    x0_ = x_
                    while 0 <= x_ < PX and row[x_]:
                        x_ += int(sgn)
                    if abs(x_ - x0_) > 8:
                        break
                R = max(6.0, abs(x_ - x0_) / 2.0)
                cands = [np.array([(x0_ + x_ - sgn) / 2.0, float(cyc)])]
        pivot, best = root.copy(), None
        for cpt in cands:  # inscribed circle touching the outline, off the top's fabric / the other leg
            if not (0 <= cpt[0] < PX and 0 <= cpt[1] < PX) or not m[int(cpt[1]), int(cpt[0])]:
                continue
            dk_ = disc(cpt, R); area = max(float(dk_.sum()), 1.0)
            touch = abs(float(np.min(np.hypot(*(pts - cpt).T))) - R) if len(pts) else 0.0
            cost = (touch + 4.0 * R * float((dk_ & ~m).sum()) / area + 2.0 * R * float((dk_ & other).sum()) / area
                    + 6.0 * R * float((dk_ & P["head"]).sum()) / area
                    + (0.5 * R * float((dk_ & cloth).sum()) / area if k.startswith("arm") else 0.0)
                    + 0.05 * float(np.linalg.norm(cpt - root)))
            if best is None or cost < best[0]:
                best = (cost, cpt)
        if best is not None:
            pivot = best[1]
        mid = path_at(c, pivot, 0.5)                                         # elbow / knee
        if k.startswith("leg"):  # knee: the thinnest point between the thigh and the calf bulge
            d_ = unit(c["end"] - pivot); n_ = np.array([-d_[1], d_[0]])
            frs = np.linspace(0.2, 0.85, 66)
            pts_ = [path_at(c, pivot, fr_) for fr_ in frs]
            ws_ = np.array([width_in(m, q, n_) for q in pts_], float)
            ws_ = np.convolve(ws_, np.ones(5) / 5, mode="same")             # smooth out outline noise
            lo_ = (frs >= 0.55) & (frs <= 0.85)
            calf = int(np.argmax(np.where(lo_, ws_, -1)))                   # the calf's widest point (lower half)
            span = np.nonzero((frs >= 0.38) & (frs <= frs[calf] - 0.06))[0]
            mid = pts_[int(span[np.argmin(ws_[span])])] if len(span) else path_at(c, pivot, 0.45)
        ov = (overrides or {}).get(k)  # hand-placed joints from the Workshop's joint editor win
        if ov:
            pivot = np.array(ov.get("pivot", pivot), float); mid = np.array(ov.get("mid", mid), float)
            c["end"] = np.array(ov.get("end", c["end"]), float)
        c["mid"] = mid
        cap = disc(pivot, R)
        past = halfplane(m.shape, pivot, unit(mid - pivot))
        limbline = dist_poly([pivot, mid, c["end"], c["tip"]])
        if k.startswith("arm"):
            keep_top = grow(cloth, 2)                                        # strap and armpit crease stay on the torso
            beyond_el = halfplane(m.shape, mid, unit(c["end"] - mid))
            corridor = (limbline < 1.1 * R) | (P[k] & beyond_el)           # the upper arm is only as wide as the arm
            regions[k] = component((m & past & ~keep_top & corridor) | (cap & m), (int(mid[1]), int(mid[0]))) | cap
            torso &= ~(m & past & ~cloth & ~cap & corridor)                  # the whole bicep leaves the torso
            ua = unit(mid - pivot); na = np.array([-ua[1], ua[0]]); na = na if na[1] < 0 else -na
            top_side = ((xx - pivot[0]) * na[0] + (yy - pivot[1]) * na[1]) > 0
            torso &= ~(m & past & ~cap & top_side & (limbline < 1.6 * R) & ~grow(cloth, 2))  # the raised arm's top outline (no stub)
            torso_arms_guard |= regions[k]
        else:
            # lower-torso cut traced along lines drawn in the art: crotch -> outer hip near the top of the hip circle
            hip_target = pivot + nout * R * 1.3 - np.array([0, 1.0]) * R * 1.3  # high-cut: up the side of the hip
            on_out = E[(E[:, 0] - cpx) * sgn > 0]
            hip_pt = on_out[int(np.argmin(np.hypot(*(on_out - hip_target).T)))]
            cp = crotch.copy() if crotch is not None else root.copy()
            cp[1] = min(cp[1], pivot[1])              # the real crotch: just below the hip joints, not the shorts' hem
            side = ((xx - cp[0]) * sgn) >= -2
            interior = m.copy()
            for _ in range(5):  # the cut may follow lines inside the shorts, not the silhouette outline itself
                interior &= np.roll(interior, 1, 0) & np.roll(interior, -1, 0) & np.roll(interior, 1, 1) & np.roll(interior, -1, 1)
            ends = disc(cp, 7) | disc(hip_pt, 7)
            # the cut: a smooth curve from the crotch, arching over the hip circle, to the outer hip (high-cut)
            ctrl = pivot - np.array([0, 1.0]) * R * 1.9
            ts_ = np.linspace(0, 1, 60)[:, None]
            wire = list((1 - ts_) ** 2 * cp + 2 * (1 - ts_) * ts_ * ctrl + ts_ ** 2 * hip_pt)
            wmask = np.zeros_like(m)
            for q in wire:
                wmask[int(q[1]), int(q[0])] = True
            wmask = grow(wmask, 1)
            # watertight: the leg is what lies under the curve (polygon closed down past the feet), on its own side
            poly = [tuple(q) for q in wire] + [(hip_pt[0] + sgn * 200, hip_pt[1]), (hip_pt[0] + sgn * 200, PX + 50),
                                              (cp[0], PX + 50)]
            pim = Image.new("L", (PX, PX), 0); ImageDraw.Draw(pim).polygon(poly, fill=255)
            below = m & (np.asarray(pim) > 0) & ~wmask & ~P["head"] & ~torso_arms_guard
            regions[k] = below | (wmask & m & side) | (cap & m)
            torso &= ~(below | (wmask & m & side & ~cap))
            joints[k + ".hip"] = hip_pt
            joints[k + ".wire"] = np.array(wire)
            joints[k + ".blend"] = (dist_poly(np.array(wire)) if len(wire) > 1 else np.full((PX, PX), 99.0), below)
        fitpts[k] = pts
        joints[k] = {"pivot": pivot, "R": R}
        if view == "back" and k.startswith("leg"):
            fy0, fy1 = np.nonzero(m)[0].min(), np.nonzero(m)[0].max()
            hip_frac["legA" if k == "legB" else "legB"] = None  # placeholder, set below by side
            hip_frac[k] = (pivot[1] - fy0) / (fy1 - fy0)
    joints["fitpts"] = fitpts
    chest = (J["neck"] + J["pelvis"]) / 2
    torso = component(torso, (int(chest[1]), int(chest[0])))  # one body: drop stray bits of the old arm outline
    pieces = {"torso": Piece(rgba * torso[..., None].astype(np.uint8), torso, lambda v: {"torso": 1.0})}
    head_reg = P["head"] | (grow(P["head"], 8) & m & (yy <= chin + 10) & ~cloth)
    pieces["head"] = Piece(rgba * head_reg[..., None].astype(np.uint8), head_reg,
                           lambda v: {"head": 1.0} if v[1] < chin - 4 else {"torso": 1.0} if v[1] > chin + 8 else
                           {"head": (chin + 8 - v[1]) / 12, "torso": 1 - (chin + 8 - v[1]) / 12})
    def solid_cap(k):
        """Fill the whole cap disc (rotation-safe): art pixels where the limb has them, else the nearest limb colour;
        outline the rim where it sticks out of the original silhouette."""
        jt = joints[k]; dk = disc(jt["pivot"], jt["R"])
        if k.startswith("leg"):  # legs sit under the torso: fill their caps only inside the silhouette
            want = regions[k] | (dk & m)
        else:
            want = regions[k] | dk  # shoulders: the full circle is the arm's
        solid = m.copy()
        for _ in range(5):  # spread colour only from the limb's interior, not its light anti-aliased edge or outline
            solid &= np.roll(solid, 1, 0) & np.roll(solid, -1, 0) & np.roll(solid, 1, 1) & np.roll(solid, -1, 1)
        known = regions[k] & solid
        col = a.copy()
        for _ in range(400):
            todo = want & ~known
            if not todo.any():
                break
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                src = np.roll(known, (dy, dx), (0, 1)); take = todo & src & ~known
                col[take] = np.roll(col, (dy, dx), (0, 1))[take]; known |= take
        keep = regions[k] & m & ~dk  # outside the cap the limb keeps its own art, outline included
        col[keep] = a[keep]
        inner = dk.copy()
        for _ in range(3):
            inner &= np.roll(inner, 1, 0) & np.roll(inner, -1, 0) & np.roll(inner, 1, 1) & np.roll(inner, -1, 1)
        rim = dk & ~inner & ~solid & ~(regions[k] & m & ~dk)
        col[rim] = (40, 32, 30)
        regions[k] = want
        return np.dstack([col, np.where(want, 255, 0).astype(np.uint8)])
    limb_rgba = {k: solid_cap(k) for k in ("armA", "armB", "legA", "legB")}
    for k in ("armA", "armB", "legA", "legB"):
        c = C[k]; pv = joints[k]["pivot"]; mid, end = c["mid"], c["end"]
        u_up = unit(mid - pv); u_lo = unit(end - mid)
        L = np.linalg.norm(mid - pv) + np.linalg.norm(end - mid); Rk = joints[k]["R"]
        blend = joints.get(k + ".blend")
        def wfn(v, mid=mid, end=end, u_up=u_up, u_lo=u_lo, L=L, k=k, blend=blend, Rk=Rk):
            a_ = float(np.clip(0.5 + ((v - mid) @ u_up) / (0.18 * L), 0, 1))     # elbow / knee
            b_ = float(np.clip(0.5 + ((v - end) @ u_lo) / (10.0 if k.startswith("arm") else max(16.0, 1.0 * Rk)), 0, 1))  # wrist / ankle
            w = {f"{k}.upper": 1 - a_, f"{k}.lower": a_ * (1 - b_), f"{k}.hand": a_ * b_}
            if blend is not None:  # the shorts stretch from the pelvis (at the traced cut) to the thigh
                wd, below = blend
                q = (int(np.clip(v[1], 0, PX - 1)), int(np.clip(v[0], 0, PX - 1)))
                wt = 1.0 if not below[q] else float(np.clip(1 - wd[q] / Rk, 0, 1))
                w = {bn: x * (1 - wt) for bn, x in w.items()}; w["torso"] = wt
            return {bn: x for bn, x in w.items() if x > 1e-4}
        pieces[k] = Piece(limb_rgba[k] * regions[k][..., None].astype(np.uint8), regions[k], wfn)
    far = {"arm": ({"armA", "armB"} - {near["arm"]}).pop(), "leg": ({"legA", "legB"} - {near["leg"]}).pop()}
    order = [far["leg"], near["leg"], far["arm"], "torso", "head", near["arm"]]
    return {"img": img, "mask": m, "J": J, "C": C, "P": P, "regions": regions, "torso": torso, "joints": joints,
            "pieces": pieces, "order": order}


def on_bg(rgba):
    im = Image.fromarray(rgba); bg = Image.new("RGBA", im.size, BG + (255,)); bg.alpha_composite(im); return bg.convert("RGB")


def token(rgba, fig_h):
    """Same on-screen size as the game's token: ~35 px tall figure in a 64 px cell, area-averaged, 1 px outline."""
    k = 35.0 / fig_h; n = max(1, round(PX * k))
    a = np.asarray(Image.fromarray(rgba).resize((n, n), Image.BOX)).copy()
    al = a[..., 3] > 110; a[..., 3] = np.where(al, 255, 0)
    edge = grow(al) & ~al; a[edge] = (27, 20, 33, 255)
    cell = np.zeros((64, 64, 4), np.uint8)
    ys, xs = np.nonzero(al)
    oy = 60 - ys.max() if len(ys) else 0; ox = 32 - int(xs.mean()) if len(xs) else 0
    for y, x in zip(*np.nonzero(a[..., 3])):
        if 0 <= y + oy < 64 and 0 <= x + ox < 64:
            cell[y + oy, x + ox] = a[y, x]
    return cell




# ----------------------------------------------------------------------------- rigid pieces + marionette walk

def disc(p, r):
    return ((xx - p[0]) ** 2 + (yy - p[1]) ** 2) <= r * r


def width_at(region, p, d):
    n = np.array([-d[1], d[0]])
    def run(s):
        k = 0
        while k < 120:
            q = (p + n * s * (k + 1)).astype(int)
            if not (0 <= q[0] < PX and 0 <= q[1] < PX and region[q[1], q[0]]):
                break
            k += 1
        return k
    return run(1) + run(-1) + 1


def fill_piece(art, m, own, want, outline=True):
    """RGBA for a piece: its own art inside `own`, the rest of `want` filled from its interior; outline where the
    piece sticks out of the body silhouette."""
    solid = own.copy()
    for _ in range(3):
        solid &= np.roll(solid, 1, 0) & np.roll(solid, -1, 0) & np.roll(solid, 1, 1) & np.roll(solid, -1, 1)
    if not solid.any():
        solid = own.copy()
    col = art.copy(); known = solid.copy()
    for _ in range(300):
        todo = want & ~known
        if not todo.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_ = np.roll(known, (dy, dx), (0, 1)); take = todo & src_ & ~known
            col[take] = np.roll(col, (dy, dx), (0, 1))[take]; known |= take
    keep = own & ~(want & ~own)
    col[keep] = art[keep]
    inner = want.copy()
    for _ in range(2):
        inner &= np.roll(inner, 1, 0) & np.roll(inner, -1, 0) & np.roll(inner, 1, 1) & np.roll(inner, -1, 1)
    if outline:
        rim = want & ~inner & ~own
        col[rim] = (40, 32, 30)
    return np.dstack([col, np.where(want, 255, 0).astype(np.uint8)])


def make_pieces(B):
    art = np.asarray(B["img"]); m = B["mask"]; C, jt = B["C"], B["joints"]
    pieces = {}  # name -> (rgba, pivot (rest), bone key)
    ai_ = art.astype(int)
    skin = m & (ai_[..., 0] > ai_[..., 2] + 25) & ((ai_.max(-1) - ai_.min(-1)) > 0.2 * np.maximum(ai_.max(-1), 1))
    # torso backfill: where the arms were cut away near the shoulders, the torso's own side is flesh (= the armpit)
    torso_want = B["torso"].copy()
    for k in ("armA", "armB"):
        pv_, R_ = jt[k]["pivot"], jt[k]["R"]
        u_ = unit(C[k]["mid"] - pv_)
        side = grow(B["torso"], max(2, int(0.7 * R_)))            # only the strip right beside the torso's side
        not_out = ((xx - (pv_[0] + u_[0] * 0.4 * R_)) * u_[0] + (yy - (pv_[1] + u_[1] * 0.4 * R_)) * u_[1]) <= 0
        torso_want |= B["regions"][k] & m & disc(pv_, 2.4 * R_) & side & not_out
    torso_col = art.copy(); known = B["torso"] & skin
    for _ in range(300):
        todo = torso_want & ~B["torso"] & ~known
        if not todo.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_ = np.roll(known, (dy, dx), (0, 1)); take = todo & src_ & ~known
            torso_col[take] = np.roll(torso_col, (dy, dx), (0, 1))[take]; known |= take
    delt = np.zeros_like(m)
    for k in ("armA", "armB"):
        delt |= disc(jt[k]["pivot"], 1.12 * jt[k]["R"])
    delt &= ~B["torso"]
    torso_want |= delt
    for _ in range(300):  # flesh from the nearest skin
        todo = torso_want & ~B["torso"] & ~known
        if not todo.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_ = np.roll(known, (dy, dx), (0, 1)); take = todo & src_ & ~known
            torso_col[take] = np.roll(torso_col, (dy, dx), (0, 1))[take]; known |= take
    torso_col[B["torso"]] = art[B["torso"]]
    inner_t = torso_want.copy()
    for _ in range(2):
        inner_t &= np.roll(inner_t, 1, 0) & np.roll(inner_t, -1, 0) & np.roll(inner_t, 1, 1) & np.roll(inner_t, -1, 1)
    torso_col[torso_want & ~inner_t & ~m] = (40, 32, 30)      # the rounded shoulder's outline where it leaves the body
    pieces["torso"] = (np.dstack([torso_col, np.where(torso_want, 255, 0).astype(np.uint8)]), B["J"]["pelvis"], "torso")
    hp = B["pieces"]["head"].rgba
    pieces["head"] = (hp, B["J"]["neck"], "head")
    for k in ("armA", "armB", "legA", "legB"):
        c = C[k]; pv, mid, end = jt[k]["pivot"], c["mid"], c["end"]
        reg = B["regions"][k] & m
        u1, u2 = unit(mid - pv), unit(end - mid)
        past_mid = ((xx - mid[0]) * u1[0] + (yy - mid[1]) * u1[1]) > 0
        past_end = ((xx - end[0]) * u2[0] + (yy - end[1]) * u2[1]) > 0
        r_mid = max(5.0, 0.42 * width_at(reg, mid, u1))  # just inside the limb: the joint never shows past its edges
        r_end = max(4.0, 0.42 * width_at(reg, end, u2))
        own_up, own_lo, own_hd = reg & ~past_mid, reg & past_mid & ~past_end, reg & past_mid & past_end
        if k.startswith("arm"):  # the upper arm is a capsule as wide as the shoulder circle: cut along its outline
            ab = mid - pv; t_ = np.clip(((xx - pv[0]) * ab[0] + (yy - pv[1]) * ab[1]) / max(ab @ ab, 1e-6), 0, 1)
            dseg = np.hypot(xx - (pv[0] + t_ * ab[0]), yy - (pv[1] + t_ * ab[1]))
            nup_ = np.array([-(ab[1]), ab[0]]) / max(np.linalg.norm(ab), 1e-6)
            nup_ = nup_ if nup_[1] < 0 else -nup_                    # the shoulder-top side of the arm
            top_side = ((xx - pv[0]) * nup_[0] + (yy - pv[1]) * nup_[1]) > 0
            own_up = own_up & (top_side | (dseg <= jt[k]["R"] * 1.05))  # trim only the armpit side; keep the deltoid
        cap_sh = B["regions"][k] & disc(pv, jt[k]["R"])  # the shoulder/hip circle (test 6)
        want_up = own_up | cap_sh                      # the parent just ends at the joint ...
        want_lo = own_lo | disc(mid, r_mid)            # ... the child's round end tucks under it
        want_hd = own_hd | disc(end, r_end)
        if k.startswith("leg"):  # legs sit under the torso: a full hip disc, so a turning thigh never opens a gap
            want_up = own_up | disc(pv, 1.1 * jt[k]["R"])
            wd, _below = jt[k + ".blend"]
            cr = jt["crotch"] if jt["crotch"] is not None else B["J"]["pelvis"]
            sgn_ = 1.0 if pv[0] > cr[0] else -1.0
            R_ = jt[k]["R"]
            above_cr = yy <= cr[1] + 0.25 * R_                     # backfill only in the hip area, never down between the legs
            want_up |= m & (wd < 1.0 * R_) & (((xx - cr[0]) * sgn_) > -2) & above_cr   # a little under the torso, own side only
            rgba_l = fill_piece(art, m, own_up | (cap_sh & m), want_up, outline=False)
            ai2 = art.astype(int); mx2, mn2 = ai2.max(-1), ai2.min(-1)
            shorts = own_up & ((mx2 - mn2) < 0.2 * np.maximum(mx2, 1)) & (mx2 > 70) & (mx2 < 200)  # the thigh's own shorts cloth
            if shorts.any():
                back = want_up & ~own_up & ~disc(mid, r_mid + 3) & (yy <= pv[1] + 1.5 * jt[k]["R"])  # hip backfill only
                base = np.median(art[shorts], 0)
                shade = np.clip((yy - pv[1]) / (3.0 * jt[k]["R"]), -1, 1)[..., None]   # a gentle top-to-bottom shade
                rgba_l[back, :3] = np.clip(base * (1.0 - 0.12 * shade[back]), 0, 255).astype(np.uint8)
            pieces[f"{k}.upper"] = (rgba_l, pv, f"{k}.upper")
            # soft shorts band: weights pelvis -> thigh across ~1 hip radius below the leotard cut; rigid thigh below
            wd_, below_ = jt[k + ".blend"]
            band = 1.0 * jt[k]["R"]
            def wfn(v, wd_=wd_, below_=below_, band=band, k=k):
                q = (int(np.clip(v[1], 0, PX - 1)), int(np.clip(v[0], 0, PX - 1)))
                w = float(np.clip(wd_[q] / band, 0, 1)) if below_[q] else 0.0
                return {f"{k}.upper": w, "torso": 1 - w} if 0 < w < 1 else ({f"{k}.upper": 1.0} if w >= 1 else {"torso": 1.0})
            SOFT[f"{k}.upper"] = Piece(rgba_l, rgba_l[..., 3] > 0, wfn, cell=4)
        else:
            rgba_up = fill_piece(art, m, own_up | (cap_sh & m), want_up)
            inner = want_up.copy()
            for _ in range(2):
                inner &= np.roll(inner, 1, 0) & np.roll(inner, -1, 0) & np.roll(inner, 1, 1) & np.roll(inner, -1, 1)
            cut = want_up & ~inner & m & ~disc(mid, r_mid + 2)       # the arm's outline along its cut edge (armpit line)
            rgba_up[cut, :3] = (40, 32, 30)
            pieces[f"{k}.upper"] = (rgba_up, pv, f"{k}.upper")
        pieces[f"{k}.lower"] = (fill_piece(art, m, own_lo, want_lo, outline=False), mid, f"{k}.lower")
        pieces[f"{k}.hand"] = (fill_piece(art, m, own_hd, want_hd, outline=False), end, f"{k}.hand")
    near_leg, far_leg = B["order"][1], B["order"][0]
    far_arm, near_arm = B["order"][2], B["order"][5]
    limb = lambda k: [f"{k}.hand", f"{k}.lower", f"{k}.upper"]  # child under parent
    order = limb(far_arm) + limb(far_leg) + limb(near_leg) + ["torso", "head"] + limb(near_arm)
    return pieces, order


def two_bone(hip, target, l1, l2, fwd):
    d = target - hip; dist = float(np.clip(np.linalg.norm(d), abs(l1 - l2) + 1e-3, l1 + l2 - 1e-3))
    a = math.acos(np.clip((l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist), -1, 1))
    base = ang(d)
    cands = [hip + l1 * np.array([math.cos(base + s * a), math.sin(base + s * a)]) for s in (1, -1)]
    return max(cands, key=lambda k: (k - (hip + target) / 2) @ fwd)


STANCE = 0.3  # how far the standing foot stays toward its drawn A-pose spot (0 = right under the hip, 1 = as drawn)


def pose(B, view, phase, clip):
    """For every bone: (rest pivot, new pivot, rotation). Feet drive the legs; arms hang and swing gently."""
    J, C, jt = B["J"], B["C"], B["joints"]; f = FWD[view]
    L = {k: (np.linalg.norm(C[k]["mid"] - jt[k]["pivot"]), np.linalg.norm(C[k]["end"] - C[k]["mid"])) for k in ("legA", "legB")}
    leg_len = float(np.mean([sum(v) for v in L.values()]))
    bob = 0.035 * leg_len * math.cos(4 * math.pi * phase) if clip == "walk" else 0.015 * leg_len * (1 - math.cos(2 * math.pi * phase)) / 2
    off = np.array([0.0, bob])
    T = {"torso": (J["pelvis"], J["pelvis"] + off, 0.0), "head": (J["neck"], J["neck"] + off, 0.0)}
    bones = {}
    for i, k in enumerate(("legA", "legB")):
        hip0, knee0, ank0 = jt[k]["pivot"], C[k]["mid"], C[k]["end"]
        l1, l2 = np.linalg.norm(knee0 - hip0), L[k][1]; hip = hip0 + off
        # stance: the art is an A-pose (feet wide apart); stand the foot nearly under the hip on an almost straight leg,
        # otherwise the splayed legs bend sideways on screen (bowlegged)
        under = np.array([hip0[0] + STANCE * (ank0[0] - hip0[0]), ank0[1]])
        base = hip0 + unit(under - hip0) * 0.97 * (l1 + l2)
        if clip == "walk":
            t = (phase + 0.5 * i) % 1.0; S = 0.24 * leg_len; H = 0.13 * leg_len
            if t < 0.5:
                s = S / 2 - S * (t / 0.5); lift = 0.0
            else:
                u_ = (t - 0.5) / 0.5; s = -S / 2 + S * u_; lift = H * math.sin(math.pi * u_)
            # foot roll: heel lifts at toe-off, toes trail early in the swing, toes up for the heel strike
            foot_len = float(np.linalg.norm(C[k]["tip"] - ank0))
            if t < 0.5:
                roll = math.radians(28) * max(0.0, (t - 0.3) / 0.2)                       # toe-off (heel up)
                heel = 0.45 * foot_len * math.sin(roll)                                     # the ankle rises with the heel
            else:
                u2 = (t - 0.5) / 0.5
                roll = math.radians(28) * (1 - u2 / 0.35) if u2 < 0.35 else                        -math.radians(14) * math.sin(math.pi * min(1.0, (u2 - 0.6) / 0.4)) if u2 > 0.6 else 0.0
                heel = 0.0
            foot_ang = math.copysign(1.0, f[0]) * roll                                       # + = toes down
            target = base + f * s + np.array([0.0, -lift - heel])
        else:
            target = base.copy(); foot_ang = 0.0
        knee = two_bone(hip, target, l1, l2, f)
        lim = math.radians(20)  # the hip can't swing the thigh more than 20 degrees from rest
        d_th = (ang(knee - hip) - ang(knee0 - hip0) + math.pi) % (2 * math.pi) - math.pi
        T[f"{k}.upper"] = (hip0, hip, ang(knee - hip) - ang(knee0 - hip0))
        T[f"{k}.lower"] = (knee0, knee, ang(target - knee) - ang(ank0 - knee0))
        T[f"{k}.hand"] = (ank0, target, foot_ang)                  # foot rolls through the step
        bones[k] = (hip, knee, target)
    for k in ("armA", "armB"):
        sh0, el0, wr0 = jt[k]["pivot"], C[k]["mid"], C[k]["end"]
        out_ = 1.0 if sh0[0] > J["pelvis"][0] else -1.0
        sh = sh0 + off + np.array([out_ * 0.3 * jt[k]["R"], 0.0])   # shoulders set slightly outward
        l1, l2 = np.linalg.norm(el0 - sh0), np.linalg.norm(wr0 - el0)
        outward = 1.0 if sh0[0] > J["pelvis"][0] else -1.0
        hang = math.atan2(1.0, 0.16 * outward)                       # upper arm down, a little out from the body
        legk = "legA" if (sh0[0] < J["pelvis"][0]) == (jt["legA"]["pivot"][0] < J["pelvis"][0]) else "legB"
        legt = (phase + (0.0 if legk == "legA" else 0.5)) % 1.0
        swing = 0.12 * math.cos(2 * math.pi * legt) if clip == "walk" else 0.0  # a little sway
        back = math.copysign(0.14, f[0])                              # rotation that moves a hanging limb backward
        th_up = hang + swing * (1 if f[0] > 0 else -1) + back        # upper arm leans back a little...
        el = sh + l1 * np.array([math.cos(th_up), math.sin(th_up)])
        th_lo = th_up - 2 * back                                      # ...forearm forward: the elbow sits just behind the shoulder-wrist line
        wr = el + l2 * np.array([math.cos(th_lo), math.sin(th_lo)])
        T[f"{k}.upper"] = (sh0, sh, th_up - ang(el0 - sh0))
        T[f"{k}.lower"] = (el0, el, th_lo - ang(wr0 - el0))
        T[f"{k}.hand"] = (wr0, wr, th_lo - ang(wr0 - el0))
        bones[k] = (sh, el, wr)
    return T, bones


def draw_rigid(canvas, rgba, p0, p1, theta):
    """Rotate a piece by theta about its rest pivot p0 and move that pivot to p1 (rigid, nearest-neighbour)."""
    c, s = math.cos(theta), math.sin(theta)
    # output -> input: v_in = p0 + R^-1 (v_out - p1)
    a, b = c, s; d_, e = -s, c
    tx = p0[0] - (a * p1[0] + b * p1[1]); ty = p0[1] - (d_ * p1[0] + e * p1[1])
    im = Image.fromarray(rgba).transform((PX, PX), Image.AFFINE, (a, b, tx, d_, e, ty), resample=Image.NEAREST)
    arr = np.asarray(im)
    sel = arr[..., 3] > 0
    canvas[sel] = arr[sel]




FWD = {"front": unit(np.array([-2.0, 1.0])), "back": unit(np.array([2.0, -1.0]))}


def _exploded(pieces, order):
    tiles = []
    for name in order:
        rgba = pieces[name][0]; al = rgba[..., 3] > 0
        if not al.any():
            continue
        ys, xs = np.nonzero(al)
        crop = Image.fromarray(rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
        t = Image.new("RGB", (crop.width + 8, crop.height + 30), (90, 90, 90))
        bgc = Image.new("RGBA", crop.size, (215, 215, 220, 255)); bgc.alpha_composite(crop)
        t.paste(bgc.convert("RGB"), (4, 26))
        ImageDraw.Draw(t).text((4, 4), name, fill=(255, 255, 255), font=ImageFont.load_default(size=14))
        tiles.append(t)
    W = sum(t.width for t in tiles) + 4 * len(tiles); H = max(t.height for t in tiles)
    sh = Image.new("RGB", (W, H), (60, 60, 60)); x = 0
    for t in tiles:
        sh.paste(t, (x, 0)); x += t.width + 4
    return sh


def _skeleton_img(B):
    im = B["img"].copy(); dr = ImageDraw.Draw(im)
    for k in ("armA", "armB", "legA", "legB"):
        c = B["C"][k]; jt = B["joints"][k]; pv, R = jt["pivot"], jt["R"]
        dr.ellipse([pv[0] - R, pv[1] - R, pv[0] + R, pv[1] + R], outline=(0, 0, 255), width=2)
        dr.line([tuple(pv), tuple(c["mid"]), tuple(c["end"]), tuple(c["tip"])], fill=(255, 0, 0), width=3)
        for q, col in ((pv, (0, 0, 255)), (c["mid"], (255, 0, 0)), (c["end"], (255, 160, 0))):
            dr.ellipse([q[0] - 5, q[1] - 5, q[0] + 5, q[1] + 5], fill=col)
        w = B["joints"].get(k + ".wire")
        if w is not None and len(w) > 1:
            dr.line([tuple(q) for q in w], fill=(0, 200, 255), width=2)
    dr.line([tuple(B["J"]["pelvis"]), tuple(B["J"]["neck"])], fill=(0, 160, 0), width=3)
    return im


def render(B, view, phase, clip="walk"):
    pieces, order = B["rigid"]
    T, bones = pose(B, view, phase, clip)
    canvas = np.zeros((PX, PX, 4), np.uint8)
    Xf = {b: (lambda v, p0=p0, p1=p1, th=th: p1 + np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]]) @ (v - p0))
          for b, (p0, p1, th) in T.items()}
    for name in order:
        if name in B["soft"]:
            B["soft"][name].draw(canvas, Xf); continue
        rgba, _, bone = pieces[name]
        p0, p1, th = T[bone]
        draw_rigid(canvas, rgba, p0, p1, th)
    return canvas


def out_dir(build):
    return ws.AI_DIR / "puppet" / build


def template(build, frames=8):
    """Build the puppet template of a race build from its approved front and back. Writes pieces + previews."""
    s = ws.load_state()
    b = s["builds"].get(build)
    if not b or not b.get("front", {}).get("approved") or not b.get("back", {}).get("approved"):
        raise ValueError(f"Approve both the front and the back of {build} first.")
    src = {f: b[f]["approved"] for f in ("front", "back")}
    out = out_dir(build); out.mkdir(parents=True, exist_ok=True)
    hip_frac, views = {}, {}
    for v in ("back", "front"):  # the back first: the front takes its hip height from the back view's buttocks
        ovr = (b.get("puppet") or {}).get("joints", {}).get(v)
        views[v] = build_view(Image.open(ws.AI_DIR / src[v]), v, hip_frac, ovr)
    meta = {"build": build, "from": src, "made": int(time.time()), "views": {}}
    for v in ("front", "back"):
        B = views[v]
        pieces, order = make_pieces(B)
        B["rigid"] = (pieces, order); B["soft"] = dict(SOFT); SOFT.clear()
        (out / v).mkdir(exist_ok=True)
        for name in order:
            Image.fromarray(pieces[name][0]).save(out / v / f"{name}.png")
        _exploded(pieces, order).save(out / f"pieces_{v}.png")
        _skeleton_img(B).save(out / f"skeleton_{v}.png")
        meta["views"][v] = {"order": order, "pivots": {n: [round(float(x), 1) for x in pieces[n][1]] for n in order},
                            "bones": {n: pieces[n][2] for n in order},
                            "joints": {k: {"pivot": [round(float(x), 1) for x in B["joints"][k]["pivot"]], "R": round(float(B["joints"][k]["R"]), 1),
                                           "mid": [round(float(x), 1) for x in B["C"][k]["mid"]],
                                           "end": [round(float(x), 1) for x in B["C"][k]["end"]]}
                                       for k in ("armA", "armB", "legA", "legB")}}
        imgs = [on_bg(render(B, v, i / frames)) for i in range(frames)]
        imgs[0].save(out / f"walk_{v}.gif", save_all=True, append_images=imgs[1:], duration=120, loop=0)
        imgs[min(2, len(imgs) - 1)].save(out / f"walk_{v}_frame.png")
    # 64 px, four directions (front, front mirrored, back, back mirrored), shown 4x
    grid = []
    for i in range(frames):
        g = Image.new("RGB", (4 * 256, 256), BG)
        for j, (v, mir) in enumerate((("front", False), ("front", True), ("back", False), ("back", True))):
            a = render(views[v], v, i / frames)
            im = on_bg(a[:, ::-1].copy() if mir else a).resize((64, 64), Image.BOX).resize((256, 256), Image.NEAREST)
            g.paste(im, (j * 256, 0))
        grid.append(g)
    grid[0].save(out / "walk_4dir_64.gif", save_all=True, append_images=grid[1:], duration=120, loop=0)
    (out / "template.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    with ws.edit_state() as s:
        keep = (s["builds"][build].get("puppet") or {}).get("joints")
        s["builds"][build]["puppet"] = {"from": src, "made": meta["made"], "approved": False}
        if keep:
            s["builds"][build]["puppet"]["joints"] = keep
    return {"build": build, "dir": ws.rel(out), "files": sorted(p.name for p in out.glob("*.*"))}


def set_joints(build, view, joints):
    """Hand-placed joints for one view: {limb: {pivot, mid, end}} in rig px (puppet.PX canvas); None clears them. The next
    template build uses them instead of the detected ones."""
    with ws.edit_state() as s:
        p = s["builds"].get(build, {}).setdefault("puppet", {})
        allj = p.setdefault("joints", {})
        if joints:
            allj[view] = {k: {n: [float(x) for x in v[n]] for n in ("pivot", "mid", "end") if n in v} for k, v in joints.items()}
        else:
            allj.pop(view, None)
    return p


def approve(build, on=True):
    with ws.edit_state() as s:
        p = s["builds"].get(build, {}).get("puppet")
        if not p:
            raise ValueError(f"Build the puppet template of {build} first.")
        p["approved"] = bool(on)
    return p


# ----------------------------------------------------------------------------- Studio job kinds (hdart.jobs)

@jobs.kind("puppet", gpu=False)
def _job_template(a):
    return template(a["build"])
