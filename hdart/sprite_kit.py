"""Hand-designed 2D pixel-art character kit (JRPG / Octopath-inspired), drawn in code.

Every part is drawn once per facing on a 64x64 native grid with simple primitives, at fixed anchor
points shared by all parts, so any combination lines up. Animation is puppet-style: per frame the
upper body bobs, the stubby legs hobble and the arms swap between a few poses; nothing is redrawn.

Each part bakes to the same indexed layer sheets the 3D kit produced (data: shade / channel / depth /
coverage, plus a normal map), so `hdart.compose`, the palettes and Unity's CharacterComposer all work
unchanged. Shading is automatic: a rounded height field per part, lit from the top left.

    uv run hdart sprite-kit [--out Assets/Art/Characters] [--preview]
"""
import json
import math
from pathlib import Path

import numpy as np

N = 64  # native cell size
SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "sprite_kit.json"
from . import config, sprite_parts  # noqa: E402
from .sprite_parts import BODIES, CH, GROUND, PARTS, Z, Layer, Pose, shade_layer  # noqa: E402,F401

DESIGN = 64  # resolution the code-drawn parts are designed at; other resolutions use templates only


def configure(res):
    """Switch the whole kit to a res x res cell (power of two). Offsets scale with res / 64."""
    global N, GROUND, LEG_LINE
    N = res
    GROUND = res - max(1, round(3 * res / 64))
    LEG_LINE = GROUND - round(6 * res / 64)
    sprite_parts.N, sprite_parts.GROUND = N, GROUND


def px(v):
    """Scale a pixel offset authored at 64 px to the current resolution (non-zero stays non-zero)."""
    if not v:
        return 0
    r = int(round(v * N / DESIGN))
    return r if r else (1 if v > 0 else -1)


# ----------------------------------------------------------------------------- animation (puppet)

def frame_spec(view, clip, f, n):
    """-> dict(facing, arms, upper dy/dx, leg lift, whole-sprite offset, rot)."""
    s = {"arm_main": "down", "arm_off": "down", "dy": 0, "dx": 0, "lift": (0, 0), "rot": 0}
    if clip == "idle":
        s["dy"] = [0, 0, 1, 1][f % 4]
    elif clip == "walk":  # hobble: bob, sway, alternate one stubby leg
        s["dy"] = [0, -1, 0, -1][f % 4]
        s["dx"] = [0, -1, 0, 1][f % 4]
        s["lift"] = [(0, 0), (2, 0), (0, 0), (0, 2)][f % 4]
        s["tilt"] = [0, -1, 0, 1][f % 4]
    elif clip == "attack":
        s["arm_main"] = ["down", "raise", "raise", "forward", "forward", "down"][f]
        s["dx"] = [0, 1, 1, -3, -2, 0][f]
    elif clip == "cast":
        s["arm_main"] = s["arm_off"] = ["down", "forward", "forward", "raise", "raise", "down"][f]
        s["dy"] = [0, 0, -1, -1, 0, 0][f]
    elif clip == "hurt":
        s["dx"] = [3, 2, 1][f]
        s["dy"] = [0, 1, 0][f]
    elif clip == "dead":
        s["dy"] = [2, 4, 0, 0][f]
        s["rot"] = [0, 0, 1, 1][f]
        s["dx"] = [0, 0, 2, 2][f]
    return s


UPPER = {"head", "face", "facial", "hair", "hair_back", "headgear", "torso", "torso_top", "body", "arm", "sleeve",
         "hand", "handwear", "mainhand", "offhand", "offhand_back", "cape_back"}


def shift(a, dx, dy):
    out = np.zeros_like(a)
    ys, yd = (slice(0, N - dy), slice(dy, N)) if dy >= 0 else (slice(-dy, N), slice(0, N + dy))
    xs, xd = (slice(0, N - dx), slice(dx, N)) if dx >= 0 else (slice(-dx, N), slice(0, N + dx))
    out[yd, xd] = a[ys, xs]
    return out


# ----------------------------------------------------------------------------- shading / baking

# ----------------------------------------------------------------------------- paintable templates
# sprites/parts/<body or any>/<part>/<facing>.<arm_main>-<arm_off>.<layer>.png, painted with the index
# palette (sprites/index_palette.*): each colour = one channel + one shade step. A painted file replaces
# the code drawing for that layer; a missing arm pose falls back to the down-down drawing.

TEMPLATES = config.data_dir() / "sprites" / "parts"
POSES = [("down", "down"), ("raise", "down"), ("forward", "down"), ("forward", "forward"), ("raise", "raise")]
LEG_LAYERS = ("legs", "feet", "legwear", "footwear")
_INDEX = {}


def index_palette():
    """(channel id, shade idx) -> RGB, unique per entry, from each channel's default ramp."""
    if _INDEX:
        return _INDEX
    from .palette import Palettes
    pal = Palettes(Path(__file__).resolve().parent.parent / "specs" / "palettes.json")
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    used = set()
    for c in spec["channels"]:
        if c["id"] == 0:
            continue
        ramp = pal.ramps[c["default"]]
        for i in range(5):
            rgb = tuple(int(v) for v in ramp[min(i, len(ramp) - 1)])
            while rgb in used:  # keep every entry distinct so a painted colour decodes unambiguously
                rgb = (rgb[0], rgb[1], (rgb[2] + 3) % 256)
            used.add(rgb)
            _INDEX[(c["id"], i)] = rgb
    return _INDEX


def template_dir(body, part):
    for d in (TEMPLATES / f"r{N}" / body / part, TEMPLATES / f"r{N}" / "any" / part):
        if d.exists():
            return d
    return None


def load_template_layers(body, part, facing, arm_main, arm_off):
    d = template_dir(body, part)
    if d is None:
        return None
    from PIL import Image
    pal = index_palette()
    keys = np.array(list(pal.keys()))
    cols = np.array(list(pal.values()), np.float32)
    files = {}
    for f in d.glob(f"{facing}.*.png"):
        _, pose, zname, _ = f.name.split(".")
        files.setdefault(zname, {})[pose] = f
    if not files:
        return None
    out = []
    for zname, poses in files.items():
        f = poses.get(f"{arm_main}-{arm_off}") or poses.get("down-down")
        if f is None:
            continue
        a = np.asarray(Image.open(f).convert("RGBA")).astype(np.float32)
        L = Layer(Z[zname])
        m = a[..., 3] > 127
        if m.any():
            dist = ((a[m][:, None, :3] - cols[None]) ** 2).sum(-1)
            k = keys[dist.argmin(1)]
            L.ch[m] = k[:, 0]
            L.bias[m] = k[:, 1] - 2
            L.flat[m] = True  # painted shading is used exactly as drawn
        out.append(L)
    return out


def part_layers(part, body_name, facing, arm_main, arm_off):
    """Layers for one pose at rest: painted templates if present, else the code drawing."""
    layers = load_template_layers(body_name, part, facing, arm_main, arm_off)
    if layers is None and (part not in PARTS or N != DESIGN):
        return []
    if layers is None:
        P = Pose(BODIES[body_name], facing, arm_main, arm_off)
        P.lift = (0, 0)
        layers = PARTS[part](P)
    return layers


LEG_LINE = GROUND - 6  # rows at or below this belong to the legs (hobble per side)


def render_layers(part, body_name, facing, spec_frame):
    layers = part_layers(part, body_name, facing, spec_frame["arm_main"], spec_frame["arm_off"])
    out = []
    rows = np.arange(N)[:, None]
    cols = np.arange(N)[None, :]
    for L in layers:
        if not (L.ch > 0).any():
            continue
        idx, nrm = shade_layer(L)
        z = L.z
        zname = next((k for k, v in Z.items() if v == z), "body")
        moves = zname in UPPER or z >= Z["body"]
        dx, dy = (px(spec_frame["dx"]), px(spec_frame["dy"])) if moves else (0, 0)
        if spec_frame.get("tilt", 0) and zname in ("head", "face", "facial", "hair", "hair_back", "headgear"):
            dx += px(spec_frame["tilt"])
        lift = (px(spec_frame["lift"][0]), px(spec_frame["lift"][1]))
        mid = N // 2 - 1
        regions = [(rows < LEG_LINE, dx, dy),                                   # upper body bobs / sways
                   ((rows >= LEG_LINE) & (cols < mid), 0, -lift[0]),            # left stub
                   ((rows >= LEG_LINE) & (cols >= mid), 0, -lift[1])]           # right stub
        ch_o = np.zeros_like(L.ch)
        idx_o = np.zeros_like(idx)
        nrm_o = np.zeros_like(nrm)
        for region, rx, ry in regions:
            reg = np.broadcast_to(region, L.ch.shape)
            c = shift(np.where(reg, L.ch, 0), rx, ry)
            m = c > 0
            ch_o[m] = c[m]
            idx_o[m] = shift(np.where(reg, idx, 0), rx, ry)[m]
            for k in range(3):
                nrm_o[..., k][m] = shift(np.where(reg, nrm[..., k], 0), rx, ry)[m]
        out.append((z, ch_o, idx_o, nrm_o))
    return out


def kit_parts(bodies, templates_only=False):
    """Code parts plus any template-only parts (AI or hand-made) found in sprites/parts/."""
    found = set()
    for b in bodies:
        d = TEMPLATES / f"r{N}" / b
        if d.exists():
            found |= {p.name for p in d.iterdir() if p.is_dir() and any(p.glob("*.png"))}
    if templates_only or N != DESIGN:
        return sorted(found)
    return sorted(set(PARTS) | found)


def export_templates(bodies=("slim",), parts=None, overwrite=False):
    """Write every part as paintable PNG layers (index palette) plus the palette files."""
    from PIL import Image
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    for c in spec["channels"]:
        CH[c["name"]] = c["id"]
    pal = index_palette()
    names = {c["id"]: c["name"] for c in spec["channels"]}
    root = TEMPLATES.parent
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "index_palette.gpl", "w", encoding="utf-8") as f:
        f.write("GIMP Palette\nName: Hearthdelve sprite index\nColumns: 5\n#\n")
        for (cid, i), rgb in pal.items():
            f.write(f"{rgb[0]:3d} {rgb[1]:3d} {rgb[2]:3d}\t{names[cid]} {i}\n")
    sw = np.zeros((len(names) - 1, 5, 3), np.uint8)
    for (cid, i), rgb in pal.items():
        sw[cid - 1, i] = rgb
    Image.fromarray(np.repeat(np.repeat(sw, 16, 0), 16, 1)).save(root / "index_palette.png")
    written = 0
    for body in bodies:
        for part in (parts or PARTS):
            d = TEMPLATES / f"r{DESIGN}" / body / part
            base = {}
            for facing in ("front", "back"):
                for am, ao in POSES:
                    P = Pose(BODIES[body], facing, am, ao)
                    P.lift = (0, 0)
                    for L in PARTS[part](P):
                        if not (L.ch > 0).any():
                            continue
                        idx, _ = shade_layer(L)
                        img = np.zeros((N, N, 4), np.uint8)
                        m = L.ch > 0
                        for (cid, i), rgb in pal.items():
                            sel = m & (L.ch == cid) & (idx == i)
                            img[sel, :3] = rgb
                        img[m, 3] = 255
                        zname = next(k for k, v in Z.items() if v == L.z)
                        key = (facing, zname)
                        if (am, ao) != ("down", "down") and key in base and np.array_equal(base[key], img):
                            continue  # pose doesn't change this layer
                        if (am, ao) == ("down", "down"):
                            base[key] = img
                        p = d / f"{facing}.{am}-{ao}.{zname}.png"
                        if p.exists() and not overwrite:
                            continue
                        d.mkdir(parents=True, exist_ok=True)
                        Image.fromarray(img).save(p)
                        written += 1
    return written


def layers_to_cell(layers, rot=0):
    """Flatten one part's layers into a data cell + normal cell (nearest layer wins)."""
    data = np.zeros((N, N, 4), np.uint8)
    nrmc = np.zeros((N, N, 4), np.uint8)
    for z, ch, idx, nrm in sorted(layers, key=lambda t: t[0]):
        m = ch > 0
        shade = 0.36 + (idx.astype(np.float32) + 0.5) / 5 * 0.62  # inverse of compose.colorize's mapping
        data[m, 0] = np.clip(np.rint(shade[m] * 255), 0, 255)
        data[m, 1] = ch[m]
        data[m, 2] = 255 - z * 3
        data[m, 3] = 255
        nrmc[m, :3] = np.clip(np.rint((nrm[m] * 0.5 + 0.5) * 255), 0, 255)
        nrmc[m, 3] = 255
    if rot:
        data, nrmc = lie_down(data), lie_down(nrmc)
    return data, nrmc


def lie_down(cell):
    """Dead pose: rotate the whole sprite onto its back and drop it to the ground."""
    r = np.rot90(cell, k=-1)  # head to the right, feet to the left
    ys = np.nonzero(r[..., 3])[0]
    if len(ys):
        r = np.roll(r, GROUND - ys.max(), axis=0)
    return r


def mirror(cell):
    return cell[:, ::-1].copy()


def template_bodies():
    d = TEMPLATES / f"r{N}"
    return sorted(p.name for p in d.iterdir() if p.is_dir() and p.name != "any") if d.exists() else []


def bake(spec_path, out_dir, bodies=None, parts=None, res=64):
    configure(res)
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if not bodies:
        bodies = template_bodies() if (spec.get("templatesOnly") or res != DESIGN) else list(BODIES)
    for c in spec["channels"]:
        CH[c["name"]] = c["id"]
    out = Path(out_dir)
    views = spec["views"]
    manifest = {"schemaVersion": 1, "channels": spec["channels"], "slots": spec["slots"],
                "bodies": sorted(bodies), "views": {}, "parts": {}, "style": "sprite2d"}
    for vname, v in views.items():
        rows = []
        for d in v["directions"]:
            for clip in v["clips"]:
                rows.append({"dir": d, "yaw": 0, "clip": clip, "frames": spec["clips"][clip]["frames"],
                             "fps": spec["clips"][clip]["fps"], "loop": spec["clips"][clip]["loop"]})
        for i, r in enumerate(rows):
            r["row"] = i
        manifest["views"][vname] = {"cell": [N, N], "columns": max(r["frames"] for r in rows), "rows": rows}
    spec_parts = {p["id"]: p for p in spec["parts"]}
    for body in bodies:
        plist = parts.get(body, []) if isinstance(parts, dict) else (parts or kit_parts([body], spec.get("templatesOnly", False)))
        for part in plist:
            for vname, vm in manifest["views"].items():
                rows, cols = vm["rows"], vm["columns"]
                data = np.zeros((len(rows) * N, cols * N, 4), np.uint8)
                nrm = np.zeros_like(data)
                for r in rows:
                    facing = "front" if r["dir"] in ("sw", "se", "left") else "back"
                    # card model: the stored back art is the reverse side of the front card, i.e. facing up-right (ne)
                    flip = r["dir"] in (("se", "nw") if spec.get("cardBacks") else ("se", "ne"))
                    for f in range(r["frames"]):
                        fs = frame_spec(vname, r["clip"], f, r["frames"])
                        if facing == "back" and spec.get("cardBacks"):  # the reverse side moves mirrored
                            fs = {**fs, "dx": -fs["dx"], "tilt": -fs.get("tilt", 0)}
                        dc, nc = layers_to_cell(render_layers(part, body, facing, fs), fs["rot"])
                        if flip:
                            dc, nc = mirror(dc), mirror(nc)
                            nc[..., 0] = np.where(nc[..., 3] > 0, 255 - nc[..., 0], 0)
                        data[r["row"] * N:(r["row"] + 1) * N, f * N:(f + 1) * N] = dc
                        nrm[r["row"] * N:(r["row"] + 1) * N, f * N:(f + 1) * N] = nc
                (out / body).mkdir(parents=True, exist_ok=True)
                from PIL import Image
                Image.fromarray(data).save(out / body / f"{part}.{vname}.data.png")
                Image.fromarray(nrm).save(out / body / f"{part}.{vname}.normal.png")
            e = manifest["parts"].setdefault(part, {"slot": part.split(".")[0], "bodies": [], "views": list(views),
                                                    "name": spec_parts.get(part, {}).get("name", part),
                                                    "hides": spec_parts.get(part, {}).get("hides", [])})
            e["bodies"] = sorted(set(e["bodies"]) | {body})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out
