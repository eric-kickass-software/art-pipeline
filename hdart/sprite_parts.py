"""The drawings of the 2D JRPG sprite kit (v2): chibi anime proportions, cel shading, detailed parts.

All parts share one body model (`Pose`): a 64x64 cell, feet on row 61, a big 3/4 anime head (about half
the height), a small torso with round sloping shoulders, straight tapered arms and two skinny stub legs.
'front' faces the viewer three-quarters, looking screen-left; 'back' faces away. Mirrors give the rest.

Shading is cel style (no bevel): a lit base tone, a shadow band on the side away from the top-left
light, a 1 px highlight on the lit edge, plus hand-placed detail strokes (folds, seams, stitching,
trims) as shade offsets. Colours come later from the palette ramps.
"""
import math

import numpy as np

N = 64
GROUND = 61

# z order (higher = nearer the viewer). Depth in the data sheet is 255 - z*3.
Z = {"headgear_back": 3, "hair_back": 4, "cape_back": 6, "offhand_back": 8, "legs": 10, "feet": 12, "body": 20,
     "legwear": 22, "footwear": 24, "torso": 30, "torso_top": 32, "head": 40, "face": 44, "facial": 46, "hair": 50,
     "headgear": 54, "arm": 58, "back_front": 59, "sleeve": 60, "mantle": 61, "hand": 62, "handwear": 63, "mainhand": 66,
     "offhand": 68}

CH = {}  # channel name -> id (filled from the kit spec)

BODIES = {"slim": {"width": 0, "torso": 13}, "broad": {"width": 1.5, "torso": 13}, "short": {"width": 0.5, "torso": 11}}


# ----------------------------------------------------------------------------- drawing

class Layer:
    """One part's pixels for one pose: channel ids, shade offsets, and how deep its shadow band is."""

    def __init__(self, z, shadow=2, bottom=None, diag=True):
        self.z = z
        self.shadow = shadow
        self.bottom = bottom if bottom is not None else max(1, shadow - 1)  # bottom band depth
        self.diag = diag  # also shade the lower-right corner diagonally
        self.ch = np.zeros((N, N), np.int16)
        self.bias = np.zeros((N, N), np.int16)
        self.flat = np.zeros((N, N), bool)  # exact shade (eyes, painted pixels): no cel shading

    def _put(self, mask, channel, bias=0, flat=False, erase=False, keep_bias=False):
        if erase:
            self.ch[mask] = 0
            return
        self.ch[mask] = CH[channel]
        if not keep_bias:
            self.bias[mask] = bias
        self.flat[mask] = flat

    def mask_ellipse(self, cx, cy, rx, ry):
        y, x = np.mgrid[0:N, 0:N]
        return ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0

    def ellipse(self, cx, cy, rx, ry, channel, **kw):
        self._put(self.mask_ellipse(cx, cy, rx, ry), channel, **kw)

    def rect(self, x0, y0, x1, y1, channel, **kw):  # inclusive, rounded to pixels
        x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
        m = np.zeros((N, N), bool)
        m[max(y0, 0):min(y1, N - 1) + 1, max(x0, 0):min(x1, N - 1) + 1] = True
        self._put(m, channel, **kw)

    def mask_poly(self, pts):
        y, x = np.mgrid[0:N, 0:N]
        px, py = x + 0.5, y + 0.5
        inside = np.zeros((N, N), bool)
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            inside ^= ((y0 > py) != (y1 > py)) & (px < (x1 - x0) * (py - y0) / ((y1 - y0) + 1e-9) + x0)
        return inside

    def poly(self, pts, channel, **kw):
        self._put(self.mask_poly(pts), channel, **kw)

    def capsule(self, x0, y0, x1, y1, r0, r1, channel, **kw):
        """Tapered limb from (x0,y0) radius r0 to (x1,y1) radius r1."""
        y, x = np.mgrid[0:N, 0:N]
        px, py = x + 0.5, y + 0.5
        dx, dy = x1 - x0, y1 - y0
        t = np.clip(((px - x0) * dx + (py - y0) * dy) / max(dx * dx + dy * dy, 1e-9), 0, 1)
        d = np.hypot(px - (x0 + dx * t), py - (y0 + dy * t))
        self._put(d <= r0 + (r1 - r0) * t, channel, **kw)

    def line(self, x0, y0, x1, y1, channel, **kw):
        m = np.zeros((N, N), bool)
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for t in np.linspace(0, 1, steps + 1):
            xi, yi = int(math.floor(x0 + (x1 - x0) * t)), int(math.floor(y0 + (y1 - y0) * t))
            if 0 <= xi < N and 0 <= yi < N:
                m[yi, xi] = True
        self._put(m, channel, **kw)

    def px(self, pts, channel, **kw):
        m = np.zeros((N, N), bool)
        for x, y in pts:
            x, y = int(round(x)), int(round(y))
            if 0 <= x < N and 0 <= y < N:
                m[y, x] = True
        self._put(m, channel, **kw)

    def stroke(self, x0, y0, x1, y1, bias=-1, only=None):
        """Detail line that only changes shade of pixels already drawn (folds, seams, strands)."""
        m = np.zeros((N, N), bool)
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for t in np.linspace(0, 1, steps + 1):
            xi, yi = int(math.floor(x0 + (x1 - x0) * t)), int(math.floor(y0 + (y1 - y0) * t))
            if 0 <= xi < N and 0 <= yi < N:
                m[yi, xi] = True
        m &= self.ch > 0
        if only:
            m &= self.ch == CH[only]
        self.bias[m] = bias

    def dots(self, pts, bias=-1):
        for x, y in pts:
            x, y = int(round(x)), int(round(y))
            if 0 <= x < N and 0 <= y < N and self.ch[y, x] > 0:
                self.bias[y, x] = bias

    def clip(self, keep):
        y, x = np.mgrid[0:N, 0:N]
        self.ch[~keep(x + 0.5, y + 0.5)] = 0


def shade_layer(L, light=(-1, -1)):
    """Cel shading: base tone 2, shadow band 1 on the side away from the light, 1 px highlight 3 on the
    lit edge, plus the layer's explicit offsets. Returns (ramp index, normal)."""
    m = L.ch > 0

    def inside(dx, dy):  # value of mask at (x+dx, y+dy)
        out = np.zeros_like(m)
        ys, yd = (slice(dy, N), slice(0, N - dy)) if dy >= 0 else (slice(0, N + dy), slice(-dy, N))
        xs, xd = (slice(dx, N), slice(0, N - dx)) if dx >= 0 else (slice(0, N + dx), slice(-dx, N))
        out[yd, xd] = m[ys, xs]
        return out

    sd = max(1, L.shadow)
    shadow = m & (~inside(sd, 0) | ~inside(0, L.bottom) | (~inside(sd, sd) if L.diag else False))
    hi = m & (~inside(-1, 0) | ~inside(0, -1)) & ~shadow
    idx = np.full((N, N), 2, np.int16)
    idx[shadow] = 1
    idx[hi] = 3
    idx = np.where(L.flat, 2, idx) + L.bias
    idx = np.clip(idx, 0, 4)
    nrm = np.zeros((N, N, 3), np.float32)
    nrm[..., 2] = 1
    nrm[shadow] = (0.45, -0.35, 0.82)
    nrm[hi] = (-0.45, 0.45, 0.77)
    return idx, nrm


# ----------------------------------------------------------------------------- body model

class Pose:
    def __init__(self, body, facing="front", arm_main="down", arm_off="down"):
        self.body, self.facing = body, facing
        self.front = facing == "front"
        self.arm_main, self.arm_off = arm_main, arm_off
        w = body["width"]
        self.hip_y = GROUND - 7                      # bottom of the torso
        self.sh_y = self.hip_y - body["torso"]       # top of the shoulders
        self.cx = 31.5
        self.half = 6.5 + w                          # half shoulder width
        self.hc = 31.0                               # head centre x
        self.cy = self.sh_y - 15.5                   # cranium centre y
        self.main_side = -1 if self.front else 1     # screen side of the weapon hand
        self.shoulder = {s: (self.cx + s * (self.half - 1.5), self.sh_y + 2.5) for s in (-1, 1)}
        self.hand = {}
        for s, pose in ((self.main_side, arm_main), (-self.main_side, arm_off)):
            sx, sy = self.shoulder[s]
            if pose == "down":
                self.hand[s] = (sx + s * 0.8, self.hip_y - 2.5)
            elif pose == "raise":
                self.hand[s] = (sx - 1.5, self.sh_y - 8)
            else:  # forward: towards screen-left (the enemies)
                self.hand[s] = (sx - 9.5, sy + 1.5)
        self.lift = (0, 0)

    def legs(self):
        """Left / right stub x positions (2 px wide)."""
        return [self.cx - 4.5, self.cx + 1.5]


def arm_capsule(L, P, s, channel, r0=1.6, r1=1.3, upto=1.0, **kw):
    sx, sy = P.shoulder[s]
    hx, hy = P.hand[s]
    L.capsule(sx, sy, sx + (hx - sx) * upto, sy + (hy - sy) * upto, r0, r1, channel, **kw)


# ----------------------------------------------------------------------------- body

def part_body(P):
    hc, cy = P.hc, P.cy
    head = Layer(Z["head"], shadow=2, bottom=1, diag=False)
    if P.front:
        head.ellipse(hc, cy, 11.5, 10.5, "skin")
        head.clip(lambda x, y: y <= cy + 4)
        head.poly([(hc - 11.4, cy + 1.5), (hc + 11.4, cy + 1.5), (hc + 10.2, cy + 7), (hc + 5.5, cy + 12),
                   (hc - 2.0, cy + 15.6), (hc - 6.2, cy + 14.2), (hc - 10.2, cy + 9)], "skin")
        head.ellipse(hc + 10.6, cy + 4.5, 1.7, 2.6, "skin")  # far ear
        head.dots([(hc + 10.8, cy + 4.6), (hc + 10.8, cy + 5.6)], -1)
        head.stroke(hc + 4, cy + 12.5, hc - 1.5, cy + 15, -1)  # jaw shadow line
    else:
        head.ellipse(hc, cy, 11.5, 10.5, "skin")
        head.poly([(hc - 10.4, cy + 2), (hc + 10.4, cy + 2), (hc + 7.5, cy + 11), (hc - 7.5, cy + 11)], "skin")
        for ex in (hc - 11.6, hc + 11.6):
            head.ellipse(ex, cy + 4.5, 1.5, 2.4, "skin")
    body = Layer(Z["body"], shadow=2)
    body.rect(hc - 2.5, P.sh_y - 2, hc + 1.5, P.sh_y + 1, "skin", bias=-1)  # neck in shadow
    h, cx, sy, hy = P.half, P.cx, P.sh_y, P.hip_y
    for s in (-1, 1):
        body.ellipse(cx + s * (h - 2.5), sy + 2.8, 3.0, 2.8, "skin")  # round shoulders
    body.poly([(cx - h + 1, sy + 1), (cx + h - 1, sy + 1), (cx + h, sy + 4), (cx + h - 1.5, hy - 5), (cx + h - 1, hy + 1),
               (cx - h + 1, hy + 1), (cx - h + 1.5, hy - 5), (cx - h, sy + 4)], "skin")
    body.stroke(cx - 3, sy + 2, cx + 2, sy + 2, -1)  # collarbones
    arm, hands = Layer(Z["arm"], shadow=1), Layer(Z["hand"], shadow=1)
    for s in (-1, 1):
        arm_capsule(arm, P, s, "skin")
        hx, hy = P.hand[s]
        hands.ellipse(hx, hy + 0.3, 1.9, 1.9, "skin")
        hands.px([(hx - 1.5, hy - 0.5)], "skin")  # thumb
    legs, feet = Layer(Z["legs"], shadow=1), Layer(Z["feet"], shadow=1)
    for i, lx in enumerate(P.legs()):
        legs.rect(lx, hy + 1, lx + 1, GROUND - 2, "skin")
        fx = lx - 1 if P.front else lx
        feet.rect(fx, GROUND - 1, fx + 2, GROUND, "skin")
    return [legs, feet, body, arm, hands, head]


# ----------------------------------------------------------------------------- face

def face(P, kind="neutral"):
    L = Layer(Z["face"])
    if not P.front:
        return [L]
    hc, ey = P.hc, int(P.cy + 3)
    wide = kind == "wide"
    tall = 4 if wide else 3
    if kind == "kind":
        tall = 2
    # near eye (left, full) and far eye (narrower): lash line, white, iris, highlight
    for (lx0, lx1, ix0, ix1, white_x, hl) in ((hc - 9, hc - 5, hc - 7, hc - 5, hc - 8, hc - 6),
                                              (hc - 1, hc + 2, hc, hc + 1, hc + 2, hc + 1)):
        L.rect(lx0, ey, lx1, ey, "dark", flat=True)                   # lash line
        if kind == "stern":
            L.px([(lx0, ey + 1)] if lx0 < hc - 3 else [(lx1, ey + 1)], "dark", flat=True)
        L.rect(white_x, ey + 1, white_x, ey + tall - 1, "white", flat=True)
        L.rect(ix0, ey + 1, ix1, ey + tall, "eyes", flat=True)
        L.rect(ix0, ey + 1, ix1, ey + 1, "eyes", flat=True, bias=-1)  # darker top of the iris
        L.rect(ix0, ey + tall, ix1, ey + tall, "eyes", flat=True, bias=1)
        L.px([(hl, ey + 1)], "white", flat=True, bias=1)              # catch light
    if kind == "stern":
        brows = [((hc - 9, ey - 3), (hc - 5, ey - 2)), ((hc - 1, ey - 2), (hc + 2, ey - 3))]
    elif kind == "kind":
        brows = [((hc - 9, ey - 2), (hc - 5, ey - 3)), ((hc - 1, ey - 3), (hc + 2, ey - 2))]
    else:
        brows = [((hc - 9, ey - 2), (hc - 5, ey - 3)), ((hc - 1, ey - 3), (hc + 2, ey - 3))]
    for (x0, y0), (x1, y1) in brows:
        L.line(x0, y0, x1, y1, "hair", flat=True, bias=-1)
    L.px([(hc - 3, ey + 5)], "skin", flat=True, bias=-1)  # nose
    my = ey + 8
    if kind == "kind":
        L.px([(hc - 5, my - 1), (hc - 4, my), (hc - 3, my), (hc - 2, my - 1)], "dark", flat=True, bias=1)
    elif kind == "wide":
        L.rect(hc - 4, my - 1, hc - 3, my, "dark", flat=True, bias=1)
    else:
        L.rect(hc - 4, my, hc - 3, my, "dark", flat=True, bias=2)
    return [L]


# ----------------------------------------------------------------------------- hair

def hair_mass(P, L, back=None, fringe_len=(3, 4, 3, 3, 2), top=0.0):
    """Shared hair cap: cranium cover, fringe clumps, near cheek lock, strands and highlight band."""
    hc, cy = P.hc, P.cy
    L.ellipse(hc + 0.5, cy - 1.5 - top, 12.9, 11.6 + top, "hair")
    if P.front:
        L.clip(lambda x, y: y <= cy + 1.5)
        xs = [hc - 11, hc - 7, hc - 3, hc + 1, hc + 5]
        for x0, ln in zip(xs, fringe_len):
            L.poly([(x0, cy - 2), (x0 + 5, cy - 2), (x0 + 2.0, cy + ln)], "hair")
            L.stroke(x0 + 0.5, cy - 1, x0 + 1.5, cy - 6, -1)  # strand gap between clumps
        L.poly([(hc - 13, cy - 3), (hc - 9.5, cy - 1), (hc - 9.8, cy + 9), (hc - 11, cy + 13), (hc - 12.8, cy + 7)], "hair")
        L.stroke(hc - 11.5, cy, hc - 11.2, cy + 9, -1)
        if back is not None:  # far-side hair behind the ear
            back.poly([(hc + 8, cy - 4), (hc + 13, cy - 2), (hc + 13, cy + 8), (hc + 11, cy + 11), (hc + 9, cy + 4)], "hair")
        # anime highlight band
        for i, x in enumerate(range(int(hc - 8), int(hc + 4))):
            if i % 4 != 3:
                yy = cy - 7 - 0.12 * (x - hc) ** 2 / 4
                L.dots([(x, yy)], 1)
        L.dots([(hc - 9, cy - 4), (hc - 8, cy - 5)], 1)
    else:
        L.poly([(hc - 12.5, cy), (hc + 12.5, cy), (hc + 12, cy + 7), (hc + 8, cy + 10), (hc + 4, cy + 8), (hc, cy + 11),
                (hc - 4, cy + 8), (hc - 8, cy + 10), (hc - 12, cy + 7)], "hair")
        for x in (hc - 8, hc - 3, hc + 2, hc + 7):
            L.stroke(x, cy + 8, x + 1, cy - 4, -1)
        for x in range(int(hc - 6), int(hc + 6)):
            if x % 4:
                L.dots([(x, cy - 8 - 0.1 * (x - hc) ** 2 / 4)], 1)


def part_hair(style):
    def fn(P):
        back, front = Layer(Z["hair_back"], shadow=3), Layer(Z["hair"], shadow=3)
        hc, cy = P.hc, P.cy
        if style == "short":
            hair_mass(P, front, back)
        elif style == "long":
            hair_mass(P, front, back, fringe_len=(4, 4, 3, 3, 3))
            tgt = back if P.front else front
            tgt.poly([(hc - 13, cy - 3), (hc + 13, cy - 3), (hc + 14, cy + 14), (hc + 12, cy + 22), (hc + 8, cy + 19),
                      (hc + 4, cy + 24), (hc, cy + 20), (hc - 4, cy + 24), (hc - 8, cy + 19), (hc - 12, cy + 22),
                      (hc - 14, cy + 14)], "hair")
            for x in (hc - 9, hc - 4, hc + 1, hc + 6, hc + 10):
                tgt.stroke(x, cy + 6, x + 0.5, cy + 19, -1)
            if P.front:  # near lock reaching the chest
                front.poly([(hc - 13, cy + 2), (hc - 9.5, cy + 4), (hc - 9.5, cy + 18), (hc - 11, cy + 22), (hc - 13, cy + 16)], "hair")
                front.stroke(hc - 11.4, cy + 5, hc - 11.4, cy + 19, -1)
        elif style == "ponytail":
            hair_mass(P, front, back, fringe_len=(3, 4, 3, 2, 2))
            tail = back if P.front else front
            tx = hc + 10 if P.front else hc + 1
            tail.poly([(tx - 2, cy - 4), (tx + 3, cy - 5), (tx + 6, cy + 6), (tx + 5, cy + 15), (tx + 2, cy + 20), (tx, cy + 13),
                       (tx - 1, cy + 4)], "hair")
            tail.stroke(tx + 2, cy - 2, tx + 3, cy + 16, -1)
            front.ellipse(tx, cy - 4.5, 2.2, 2.2, "accent")
        elif style == "spiky":
            hair_mass(P, front, back, fringe_len=(4, 3, 4, 3, 3), top=1)
            for ang in (-80, -50, -20, 10, 40, 70, 100):
                a = math.radians(ang - 90)
                bx, by = hc + math.cos(a) * 8, cy - 2 + math.sin(a) * 8
                tx, ty = hc + math.cos(a) * 16, cy - 2 + math.sin(a) * 15
                nx, ny = -math.sin(a) * 3.2, math.cos(a) * 3.2
                front.poly([(bx - nx, by - ny), (bx + nx, by + ny), (tx, ty)], "hair")
                front.stroke(bx, by, (bx + tx) / 2, (by + ty) / 2, 1)
        elif style == "bun":
            hair_mass(P, front, back, fringe_len=(3, 3, 3, 2, 2))
            bx = hc + 3 if P.front else hc
            front.ellipse(bx, cy - 13, 5, 4.3, "hair")
            front.stroke(bx - 3, cy - 14, bx + 2, cy - 11, -1)
            front.dots([(bx - 2, cy - 15), (bx - 1, cy - 16)], 1)
            front.rect(bx - 3, cy - 10, bx + 3, cy - 10, "accent")
        elif style == "mohawk":
            front.poly([(hc - 3, cy - 13), (hc + 2, cy - 15), (hc + 5, cy - 12), (hc + 4, cy - 2), (hc - 3, cy - 3)], "hair")
            for y in range(int(cy - 12), int(cy - 3), 3):
                front.stroke(hc - 2, y, hc + 3, y - 1, -1)
            front.stroke(hc - 1, cy - 12, hc + 1, cy - 4, 1)
        return [back, front]
    return fn


def part_facial(kind):
    def fn(P):
        L = Layer(Z["facial"], shadow=2)
        if not P.front:
            return [L]
        hc, cy = P.hc, P.cy
        if kind == "beard":
            L.poly([(hc - 10.4, cy + 6), (hc - 7, cy + 9), (hc + 6, cy + 9), (hc + 9.5, cy + 6), (hc + 7, cy + 12),
                    (hc + 1, cy + 17.5), (hc - 3, cy + 19), (hc - 7, cy + 16), (hc - 10, cy + 11)], "hair")
            L.rect(hc - 5, cy + 11, hc - 2, cy + 11, "dark", flat=True, bias=1)  # mouth gap
            for x in (hc - 7, hc - 4, hc - 1, hc + 3):
                L.stroke(x, cy + 13, x + 0.5, cy + 17, -1)
        else:
            L.poly([(hc - 8, cy + 10), (hc - 4, cy + 9), (hc - 1, cy + 9.5), (hc + 1, cy + 11), (hc - 2, cy + 10.6),
                    (hc - 4, cy + 11), (hc - 7, cy + 12)], "hair")
        return [L]
    return fn


# ----------------------------------------------------------------------------- headgear

def part_headgear(kind):
    def fn(P):
        L, back = Layer(Z["headgear"], shadow=3), Layer(Z["headgear_back"], shadow=3)
        hc, cy, sy = P.hc, P.cy, P.sh_y
        if kind == "hood":
            L.ellipse(hc + 0.5, cy - 0.5, 13.6, 12.6, "cloth1")
            L.poly([(hc + 8, cy - 9), (hc + 15, cy - 10), (hc + 12, cy - 3)], "cloth1")  # pointed tip
            if P.front:
                L.ellipse(hc - 2.5, cy + 4.5, 8.6, 9.6, "cloth1", erase=True)
                L.stroke(hc - 11.5, cy - 2, hc - 10.5, cy + 9, -1)
                L.stroke(hc - 6, cy - 9, hc + 3, cy - 11, 1)
                rim = L.mask_ellipse(hc - 2.5, cy + 4.5, 9.6, 10.6) & (L.ch > 0)
                L.bias[rim] = -1  # shadowed inner rim of the face opening
            else:
                L.stroke(hc, cy - 10, hc + 1, cy + 8, -1)
            mant = Layer(Z["mantle"], shadow=2)  # mantle over the shoulders
            mant.poly([(hc - 7, sy - 1), (hc + 7, sy - 1), (P.cx + P.half + 2.5, sy + 5), (P.cx + P.half + 1, sy + 7),
                       (P.cx - P.half - 1, sy + 7), (P.cx - P.half - 2.5, sy + 5)], "cloth1")
            mant.rect(P.cx - P.half - 1, sy + 6, P.cx + P.half + 1, sy + 7, "cloth2")
            if P.front:
                mant.px([(hc - 1, sy + 1), (hc, sy + 1)], "accent")
            return [back, L, mant]
        if kind == "helmet":
            L.ellipse(hc + 0.5, cy - 2, 12.6, 10.6, "metal")
            L.clip(lambda x, y: y <= cy + 2.5)
            L.rect(hc - 12, cy + 1, hc + 12, cy + 2, "accent")
            L.stroke(hc - 7, cy - 10, hc + 1, cy - 12, 1)
            L.stroke(hc - 9, cy - 5, hc - 8, cy - 9, 1)
            L.dots([(hc - 9, cy + 1), (hc - 3, cy + 1), (hc + 3, cy + 1), (hc + 9, cy + 1)], -2)
            if P.front:
                L.rect(hc - 4, cy + 2, hc - 3, cy + 7, "metal")            # nose guard
                L.poly([(hc - 12, cy + 1), (hc - 9.5, cy + 1), (hc - 9.5, cy + 8), (hc - 11.5, cy + 6)], "metal")  # cheek guard
            L.poly([(hc - 2, cy - 12.5), (hc + 3, cy - 13.5), (hc + 3, cy - 10), (hc - 2, cy - 10)], "accent")  # crest base
        elif kind == "wizard_hat":
            L.ellipse(hc + 0.5, cy - 5, 16.5, 3.4, "cloth1")
            L.stroke(hc - 15, cy - 4, hc + 15, cy - 4, -1)
            L.poly([(hc - 8.5, cy - 6), (hc + 9.5, cy - 6), (hc + 7, cy - 14), (hc + 3, cy - 20), (hc + 12, cy - 27),
                    (hc + 0, cy - 22), (hc - 4, cy - 14)], "cloth1")
            L.rect(hc - 8, cy - 9, hc + 8, cy - 7, "accent")
            L.rect(hc - 2, cy - 9, hc, cy - 7, "metal")
            L.stroke(hc - 2, cy - 11, hc + 1, cy - 19, -1)
            L.stroke(hc + 4, cy - 11, hc + 4, cy - 16, -1)
            L.stroke(hc - 5, cy - 11, hc - 3, cy - 15, 1)
        elif kind == "cap":
            L.ellipse(hc + 0.5, cy - 4, 12.2, 8, "leather")
            L.clip(lambda x, y: y <= cy - 1)
            if P.front:
                L.poly([(hc - 15, cy - 3), (hc - 5, cy - 3), (hc - 5, cy - 1), (hc - 14, cy - 1)], "leather", bias=-1)
            L.stroke(hc - 9, cy - 3, hc + 10, cy - 3, -1)
            L.dots([(x, cy - 7) for x in range(int(hc - 7), int(hc + 8), 2)], -1)
            L.poly([(hc + 6, cy - 7), (hc + 12, cy - 15), (hc + 9, cy - 6)], "accent")  # feather
        return [back, L]
    return fn


# ----------------------------------------------------------------------------- torso

def torso_shape(P, flare=0.0, hem=None, w_add=0.0):
    h, cx, sy, hy = P.half + w_add, P.cx, P.sh_y, P.hip_y
    hem = hem if hem is not None else hy + 1
    return [(cx - h + 0.5, sy + 0.8), (cx + h - 0.5, sy + 0.8), (cx + h + 0.8, sy + 3.5), (cx + h, hy - 5),
            (cx + h + flare, hem), (cx - h - flare, hem), (cx - h, hy - 5), (cx - h - 0.8, sy + 3.5)]


def sleeve(L, P, s, channel, upto, cuff=None, flare=0.0):
    sx, sy = P.shoulder[s]
    hx, hy = P.hand[s]
    ex, ey = sx + (hx - sx) * upto, sy + (hy - sy) * upto
    L.capsule(sx, sy, ex, ey, 2.4, 2.0 + flare, channel)
    L.ellipse(sx, sy, 3.2, 3.0, channel)  # covered shoulder cap
    if cuff:
        L.capsule(ex - (hx - sx) * 0.06, ey - (hy - sy) * 0.06, ex, ey, 2.1 + flare, 2.1 + flare, cuff)


def part_torso(kind):
    def fn(P):
        L, top, sl = Layer(Z["torso"], shadow=3), Layer(Z["torso_top"], shadow=1), Layer(Z["sleeve"], shadow=1)
        h, cx, sy, hy = P.half, P.cx, P.sh_y, P.hip_y
        if kind == "tunic":
            L.poly(torso_shape(P, flare=2.5, hem=hy + 3.5, w_add=0.6), "cloth1")
            L.rect(cx - h - 2, hy + 2, cx + h + 2, hy + 3, "cloth2")
            L.dots([(x, hy + 2) for x in range(int(cx - h - 1), int(cx + h + 2), 3)], 2)
            if P.front:
                L.poly([(cx - 1.2, hy + 4), (cx - 0.4, hy - 0.5), (cx + 0.6, hy + 4)], "cloth1", erase=True)  # slit
                L.poly([(cx - 3.8, sy + 0.4), (cx + 3.0, sy + 0.4), (cx - 0.6, sy + 5.2)], "cloth1", erase=True)  # V neck
                L.line(cx - 3.8, sy + 0.8, cx - 0.6, sy + 5.0, "cloth2")
                L.line(cx + 3.0, sy + 0.8, cx - 0.6, sy + 5.0, "cloth2")
                L.dots([(cx - 0.6, sy + 7), (cx - 0.6, sy + 9)], -1)  # lacing points
            else:
                L.stroke(cx, sy + 2, cx, hy + 2, -1)  # back seam
            L.stroke(cx - h + 0.5, sy + 4, cx - h + 2.5, hy - 6, -1)
            L.stroke(cx + h - 0.5, sy + 4, cx + h - 2.5, hy - 6, -1)
            L.stroke(cx - 4, hy - 3, cx - 5, hy + 1, -1)
            L.stroke(cx + 3, hy - 3, cx + 4, hy + 1, -1)
            top.rect(cx - h - 0.5, hy - 5, cx + h + 0.5, hy - 4, "leather")
            if P.front:
                top.rect(cx - 2, hy - 5, cx - 1, hy - 4, "accent")
                top.rect(cx + 3, hy - 4, cx + 5, hy - 1, "leather")          # pouch
                top.stroke(cx + 3, hy - 3, cx + 5, hy - 3, -1)
                top.px([(cx + 4, hy - 3)], "accent")
            for s in (-1, 1):
                sleeve(sl, P, s, "cloth1", 0.5, cuff="cloth2")
        elif kind == "robe":
            L.poly(torso_shape(P, flare=4.2, hem=GROUND - 0.5, w_add=0.8), "cloth1")
            L.rect(cx - h - 4, GROUND - 2, cx + h + 4, GROUND - 1, "cloth2")
            L.dots([(x, GROUND - 2) for x in range(int(cx - h - 3), int(cx + h + 4), 2)], 2)
            if P.front:
                L.rect(cx - 1.5, sy + 3, cx - 0.5, GROUND - 2, "cloth2")
                L.dots([(cx - 1, y) for y in range(int(sy + 5), GROUND - 3, 3)], 2)
                L.ellipse(cx - 0.5, sy + 0.8, 5.2, 2.4, "cloth2")             # high collar
                L.stroke(cx - 5, sy + 1, cx + 4, sy + 1, 1)
            for x in (cx - h + 1, cx - 4, cx + 3, cx + h - 1):
                L.stroke(x, hy - 2, x - 0.5 if x < cx else x + 0.5, GROUND - 3, -1)
            top.rect(cx - h - 0.8, hy - 5, cx + h + 0.8, hy - 4, "cloth2")      # sash
            if P.front:
                top.rect(cx - 4, hy - 5, cx - 3, hy - 3, "cloth2", bias=1)
                top.line(cx - 4, hy - 3, cx - 5, hy + 3, "cloth2")
                top.line(cx - 3, hy - 3, cx - 3, hy + 2, "cloth2", bias=-1)
            for s in (-1, 1):  # bell sleeves
                sxp, syp = P.shoulder[s]
                hx, hy_ = P.hand[s]
                ex, ey = sxp + (hx - sxp) * 0.82, syp + (hy_ - syp) * 0.82
                sl.capsule(sxp, syp, ex, ey, 2.4, 3.2, "cloth1")
                sl.ellipse(sxp, syp, 3.2, 3.0, "cloth1")
                sl.capsule(ex - (hx - sxp) * 0.08, ey - (hy_ - syp) * 0.08, ex, ey, 3.3, 3.3, "cloth2")
        elif kind == "plate":
            L.poly(torso_shape(P, hem=hy - 5, w_add=0.4), "metal")
            L.stroke(cx - 1, sy + 2, cx - 1, hy - 7, 1)                       # ridge highlight
            L.stroke(cx, sy + 2, cx, hy - 7, -1)
            L.stroke(cx - h + 1, sy + 7, cx - 2, sy + 9, -1)
            for i, yy in enumerate((hy - 5, hy - 3)):                           # abdomen lames
                L.rect(cx - h + 0.5, yy, cx + h - 0.5, yy + 1, "metal", bias=-1 if i else 0)
            L.dots([(cx - h + 1.5, hy - 4), (cx + h - 1.5, hy - 4)], 2)
            L.ellipse(cx - 0.5, sy + 0.5, 4.6, 1.9, "metal", bias=1)           # gorget
            top.rect(cx - h - 0.5, hy - 1, cx + h + 0.5, hy, "leather")
            top.rect(cx - 3, hy + 1, cx + 2, hy + 4, "cloth1")                  # tabard flap
            top.rect(cx - 3, hy + 4, cx + 2, hy + 4, "cloth2")
            if P.front:
                top.px([(cx - 1, hy + 2), (cx, hy + 2), (cx - 0.5, hy + 3)], "accent")
            for s in (-1, 1):  # layered pauldrons
                sxp, syp = P.shoulder[s]
                sl.ellipse(sxp, syp + 2.6, 3.6, 1.8, "metal", bias=-1)
                sl.ellipse(sxp, syp - 0.2, 4.0, 3.3, "metal")
                sl.stroke(sxp - 3, syp - 1.5, sxp + 1, syp - 3, 1)
                sl.dots([(sxp, syp + 1)], 2)
        elif kind == "vest":
            L.poly(torso_shape(P, hem=hy + 0.5, w_add=0.3), "cloth2")          # shirt
            L.stroke(cx - h + 1, sy + 4, cx - h + 3, hy - 5, -1)
            L.stroke(cx + h - 1, sy + 4, cx + h - 3, hy - 5, -1)
            for s in (-1, 1):
                sleeve(sl, P, s, "cloth2", 0.55, cuff="cloth2")
                sl.stroke(*P.shoulder[s], P.shoulder[s][0], P.shoulder[s][1] + 3, -1)
            if P.front:
                top.poly([(cx - h - 0.4, sy + 1), (cx - 1.8, sy + 1), (cx - 1.2, hy - 1), (cx - h + 0.6, hy - 1)], "leather")
                top.poly([(cx + 0.8, sy + 1), (cx + h + 0.4, sy + 1), (cx + h - 0.6, hy - 1), (cx + 1.6, hy - 1)], "leather")
                for i, yy in enumerate(range(int(sy + 3), int(hy - 1), 2)):     # lacing
                    top.px([(cx - 1 + (i % 2), yy)], "accent")
                top.dots([(cx - h + 0.5, y) for y in range(int(sy + 3), int(hy - 1), 2)], -1)  # stitching
                top.stroke(cx - h, sy + 1.5, cx - 2, sy + 1.5, 1)
            else:
                top.poly(torso_shape(P, hem=hy - 1, w_add=0.6), "leather")
                top.stroke(cx, sy + 2, cx, hy - 2, -1)
        return [L, top, sl]
    return fn


# ----------------------------------------------------------------------------- legs, feet, hands

def part_legwear(kind):
    def fn(P):
        L = Layer(Z["legwear"], shadow=1)
        hy = P.hip_y
        for lx in P.legs():
            if kind == "pants":
                L.rect(lx, hy, lx + 1, GROUND - 2, "cloth2")
                L.dots([(lx, hy + 3)], -1)
            elif kind == "shorts":
                L.rect(lx - 0.5, hy, lx + 1.5, hy + 2, "cloth2")
                L.stroke(lx - 0.5, hy + 2, lx + 1.5, hy + 2, -1)
            elif kind == "greaves":
                L.rect(lx - 0.5, hy + 2, lx + 1.5, GROUND - 2, "metal")
                L.dots([(lx - 0.5, hy + 2)], 1)
        if kind in ("pants", "shorts"):
            L.rect(P.cx - P.half + 1, hy - 1, P.cx + P.half - 1, hy + 1, "cloth2")
        return [L]
    return fn


def part_footwear(kind):
    def fn(P):
        L = Layer(Z["footwear"], shadow=1)
        for lx in P.legs():
            fx = lx - 1 if P.front else lx
            if kind == "boots":
                L.rect(lx - 0.5, GROUND - 4, lx + 1.5, GROUND - 1, "leather")
                L.rect(fx - 0.5, GROUND - 1, fx + 2.5, GROUND, "leather")
                L.stroke(lx - 0.5, GROUND - 4, lx + 1.5, GROUND - 4, 1)    # folded cuff
                L.stroke(fx - 0.5, GROUND, fx + 2.5, GROUND, -2)           # sole
            else:
                L.rect(fx, GROUND - 1, fx + 2, GROUND, "leather")
                L.stroke(fx, GROUND, fx + 2, GROUND, -1)
        return [L]
    return fn


def part_handwear(kind):
    def fn(P):
        L = Layer(Z["handwear"], shadow=1)
        for s in (-1, 1):
            hx, hy = P.hand[s]
            sx, sy = P.shoulder[s]
            if kind == "gauntlets":
                L.capsule(sx + (hx - sx) * 0.55, sy + (hy - sy) * 0.55, hx, hy, 1.9, 1.9, "metal")
                L.ellipse(hx, hy + 0.3, 2.2, 2.2, "metal")
                L.stroke(sx + (hx - sx) * 0.6, sy + (hy - sy) * 0.6, hx - 0.5, hy - 1.2, 1)
            else:
                L.ellipse(hx, hy + 0.3, 2.1, 2.1, "leather")
                L.capsule(sx + (hx - sx) * 0.82, sy + (hy - sy) * 0.82, sx + (hx - sx) * 0.88, sy + (hy - sy) * 0.88,
                          1.8, 1.8, "leather", bias=1)
        return [L]
    return fn


def part_back(kind):
    def fn(P):
        back, front = Layer(Z["cape_back"], shadow=3), Layer(Z["back_front"], shadow=1)
        h, cx, sy = P.half, P.cx, P.sh_y
        if kind == "cape":
            cape = back if P.front else front
            cape.poly([(cx - h - 0.5, sy), (cx + h + 0.5, sy), (cx + h + 4.5, GROUND - 2), (cx + 2, GROUND - 1),
                       (cx - 2, GROUND - 2), (cx - h - 4.5, GROUND - 1)], "cloth1")
            for x in (cx - 5, cx - 1, cx + 3):
                cape.stroke(x, sy + 4, x + (x - cx) * 0.4, GROUND - 3, -1)
            cape.rect(cx - h - 4, GROUND - 2, cx + h + 4, GROUND - 2, "cloth2")
            if P.front:
                front.rect(cx - h, sy, cx - h + 1, sy + 3, "cloth1")
                front.rect(cx + h - 1, sy, cx + h, sy + 3, "cloth1")
                front.ellipse(cx - 0.5, sy + 1.2, 1.3, 1.3, "accent")
        else:  # quiver + strap
            q = back if P.front else front
            q.capsule(cx + 7, sy - 5, cx - 1, sy + 11, 2.2, 2.0, "leather")
            q.stroke(cx + 6, sy - 3, cx, sy + 9, -1)
            for k in range(3):
                q.line(cx + 6 + k, sy - 6, cx + 8 + k, sy - 10, "wood")
                q.px([(cx + 8 + k, sy - 11)], "accent")
            if P.front:
                front.line(cx - h + 1, sy + 1, cx + h - 1, sy + 9, "leather")
        return [back, front]
    return fn


# ----------------------------------------------------------------------------- weapons

def _dir(P, side):
    pose = P.arm_main if side == P.main_side else P.arm_off
    dx, dy = {"down": (-0.35, -1.0), "raise": (0.45, -1.0), "forward": (-1.0, -0.4)}[pose]
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def part_mainhand(kind):
    def fn(P):
        L = Layer(Z["mainhand"], shadow=1)
        s = P.main_side
        hx, hy = P.hand[s]
        dx, dy = _dir(P, s)
        nx, ny = -dy, dx
        if kind == "sword":
            L.capsule(hx - dx * 2, hy - dy * 2, hx + dx * 1.5, hy + dy * 1.5, 0.9, 0.9, "leather")
            L.px([(hx - dx * 3, hy - dy * 3)], "accent")                       # pommel
            L.capsule(hx + dx * 2 - nx * 3, hy + dy * 2 - ny * 3, hx + dx * 2 + nx * 3, hy + dy * 2 + ny * 3, 0.8, 0.8, "accent")
            L.capsule(hx + dx * 3, hy + dy * 3, hx + dx * 16, hy + dy * 16, 1.2, 0.7, "metal")
            L.stroke(hx + dx * 4, hy + dy * 4, hx + dx * 14, hy + dy * 14, 2)    # edge glint
        elif kind == "dagger":
            L.capsule(hx - dx, hy - dy, hx + dx * 1.5, hy + dy * 1.5, 0.8, 0.8, "leather")
            L.capsule(hx + dx * 2 - nx * 2, hy + dy * 2 - ny * 2, hx + dx * 2 + nx * 2, hy + dy * 2 + ny * 2, 0.6, 0.6, "accent")
            L.capsule(hx + dx * 2.5, hy + dy * 2.5, hx + dx * 8.5, hy + dy * 8.5, 1.0, 0.5, "metal")
            L.stroke(hx + dx * 3, hy + dy * 3, hx + dx * 7, hy + dy * 7, 2)
        elif kind == "staff":
            L.capsule(hx - dx * 13, hy - dy * 13, hx + dx * 18, hy + dy * 18, 0.9, 0.9, "wood")
            for t in (-8, -2, 6, 12):
                L.dots([(hx + dx * t, hy + dy * t)], -1)
            tip = (hx + dx * 21, hy + dy * 21)
            L.capsule(tip[0] - nx * 2.4, tip[1] - ny * 2.4, tip[0] + nx * 2.4, tip[1] + ny * 2.4, 0.8, 0.8, "metal")
            L.ellipse(tip[0] + dx * 1.2, tip[1] + dy * 1.2, 2.4, 2.4, "magic")
            L.px([(tip[0] + dx * 1.2 - 1, tip[1] + dy * 1.2 - 1)], "white", flat=True, bias=1)
        elif kind == "mace":
            L.capsule(hx - dx * 2, hy - dy * 2, hx + dx * 9, hy + dy * 9, 0.9, 0.9, "wood")
            hx2, hy2 = hx + dx * 11.5, hy + dy * 11.5
            L.ellipse(hx2, hy2, 3.0, 3.0, "metal")
            for a in range(0, 360, 60):
                r = math.radians(a)
                L.px([(hx2 + math.cos(r) * 3.6, hy2 + math.sin(r) * 3.6)], "metal", bias=-1)
            L.dots([(hx2 - 1, hy2 - 1)], 2)
        return [L]
    return fn


def part_offhand(kind):
    def fn(P):
        s = -P.main_side
        hx, hy = P.hand[s]
        L = Layer(Z["offhand"] if P.front else Z["offhand_back"], shadow=2)
        if kind == "shield":
            cx, cy = hx + 1.5 * s, hy - 2.5
            L.ellipse(cx, cy, 6.4, 7.4, "metal")
            L.ellipse(cx, cy, 5.2, 6.2, "wood")
            for x in (cx - 2.5, cx, cx + 2.5):
                L.stroke(x, cy - 6, x, cy + 6, -1, only="wood")
            L.ellipse(cx, cy, 1.9, 1.9, "metal", bias=1)
            L.dots([(cx - 0.5, cy - 0.5)], 2)
            L.dots([(cx - 4.5, cy - 4), (cx + 4, cy - 4), (cx - 4.5, cy + 4), (cx + 4, cy + 4)], 2)
        elif kind == "tome":
            L.rect(hx - 3.5, hy - 5, hx + 3.5, hy + 2, "leather")
            L.rect(hx - 2.5, hy - 4, hx + 2.5, hy + 1, "leather", bias=1)
            L.rect(hx + 3, hy - 4, hx + 3, hy + 1, "white")                       # page edges
            L.px([(hx - 0.5, hy - 2), (hx + 0.5, hy - 2), (hx, hy - 1)], "accent")
            L.rect(hx - 3.5, hy - 1.5, hx - 3, hy - 0.5, "metal")                 # clasp
        elif kind == "lantern":
            L.line(hx, hy, hx, hy + 3, "metal")
            L.rect(hx - 2.5, hy + 3, hx + 2.5, hy + 3, "metal")
            L.rect(hx - 2, hy + 4, hx + 2, hy + 9, "metal")
            L.rect(hx - 1, hy + 5, hx + 1, hy + 8, "magic", bias=1)
            L.px([(hx, hy + 6)], "white", flat=True, bias=1)
            L.rect(hx - 2.5, hy + 10, hx + 2.5, hy + 10, "metal", bias=-1)
        return [L]
    return fn


PARTS = {
    "body.base": part_body,
    **{f"face.{k}": (lambda k: lambda P: face(P, k))(k) for k in ("neutral", "stern", "kind", "wide")},
    **{f"hair.{s}": part_hair(s) for s in ("short", "long", "ponytail", "spiky", "bun", "mohawk")},
    "facial.beard": part_facial("beard"), "facial.mustache": part_facial("mustache"),
    **{f"head.{k}": part_headgear(k) for k in ("hood", "helmet", "wizard_hat", "cap")},
    **{f"torso.{k}": part_torso(k) for k in ("tunic", "robe", "plate", "vest")},
    **{f"legs.{k}": part_legwear(k) for k in ("pants", "shorts", "greaves")},
    **{f"feet.{k}": part_footwear(k) for k in ("boots", "shoes")},
    **{f"hands.{k}": part_handwear(k) for k in ("gloves", "gauntlets")},
    **{f"back.{k}": part_back(k) for k in ("cape", "quiver")},
    **{f"mainhand.{k}": part_mainhand(k) for k in ("sword", "staff", "dagger", "mace")},
    **{f"offhand.{k}": part_offhand(k) for k in ("shield", "tome", "lantern")},
}
