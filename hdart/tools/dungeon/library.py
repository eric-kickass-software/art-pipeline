"""Materials library: reference materials with tags, descriptors, a palette and a seamless texture at true scale.

Imports come from two CC0 libraries with free JSON APIs (no keys): ambientCG (ambientcg.com/api/v2) and Poly Haven
(api.polyhaven.com). An import keeps the 1K colour and height maps (library/cache/, git-ignored, re-downloadable),
and writes library/materials/<id>.json (in git): name, source and licence, tags, descriptors for prompts, physical
size, a palette (stone tones from the raised parts, the joint colour from the low parts of the height map) and an
image template. Families use a material three ways: its texture as the layout template (scaled so a whole number of
repeats fits a tile, at the material's real size), its palette on a procedural template, its words in the prompt.
"""
import io
import json
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from hdart.core import config, jobs



def materials_dir() -> Path:
    """library/materials: one JSON per material (in git)."""
    return config.library_dir() / "materials"


def cache_dir() -> Path:
    """library/cache: downloaded maps (git-ignored)."""
    return config.library_dir() / "cache"

TILE_METERS = 1.5  # one board tile: a hero is ~1.5 units (~1.8 m), 1 unit = iso.KZ tiles -> 1 tile ~ 1.47 m
HOSTS = ("ambientcg.com", "acg-media.struffelproductions.com", "api.polyhaven.com", "dl.polyhaven.org", "cdn.polyhaven.com")
UA = {"User-Agent": "hearthdelve-art-pipeline (local tool)"}


def _get(url, **kw):
    host = re.match(r"https://([^/]+)/", url)
    if not host or host.group(1) not in HOSTS:
        raise ValueError(f"not a library host: {url}")
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 60), **kw)
    r.raise_for_status()
    return r


# ----------------------------------------------------------------------------- search

def search(q, source="all", limit=24):
    """[{source, asset, name, tags, thumb, size_m}] from ambientCG and/or Poly Haven (textures only)."""
    q = q.strip()
    out = []
    if source in ("all", "ambientcg"):
        r = _get("https://ambientcg.com/api/v2/full_json", params={"type": "Material", "q": q, "limit": limit,
                                                                   "include": "tagData,previewData,dimensionsData"}).json()
        for a in r.get("foundAssets", []):
            dx, dy = a.get("dimensionX"), a.get("dimensionY")
            out.append({"source": "ambientcg", "asset": a["assetId"], "name": a.get("displayName") or a["assetId"],
                        "tags": a.get("tags", []), "thumb": (a.get("previewImage") or {}).get("256-PNG"),
                        "size_m": [dx / 100, dy / 100] if dx and dy else None})
    if source in ("all", "polyhaven"):
        allt = _get("https://api.polyhaven.com/assets", params={"t": "textures"}).json()
        words = [w for w in q.lower().split() if w]
        hits = []
        for aid, a in allt.items():
            hay = " ".join([aid, a.get("name", "")] + a.get("tags", []) + a.get("categories", [])).lower()
            if all(w in hay for w in words):
                hits.append((a.get("download_count", 0), aid, a))
        for _, aid, a in sorted(hits, key=lambda t: -t[0])[:limit]:
            d = a.get("dimensions")
            out.append({"source": "polyhaven", "asset": aid, "name": a.get("name", aid),
                        "tags": a.get("tags", []) + a.get("categories", []),
                        "thumb": f"https://cdn.polyhaven.com/asset_img/thumbs/{aid}.png?width=256",
                        "size_m": [d[0] / 1000, d[1] / 1000] if d else None})
    return out


# ----------------------------------------------------------------------------- import

def _slug(s):
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")


def _fetch_maps(source, asset, folder):
    """Download the 1K colour and height maps into `folder` as color.jpg / height.jpg."""
    folder.mkdir(parents=True, exist_ok=True)
    if source == "ambientcg":
        z = zipfile.ZipFile(io.BytesIO(_get(f"https://ambientcg.com/get?file={asset}_1K-JPG.zip", timeout=180).content))
        for member in z.namelist():  # only these two maps, written under fixed names (nothing else is extracted)
            low = member.lower()
            if low.endswith("_color.jpg"):
                (folder / "color.jpg").write_bytes(z.read(member))
            elif low.endswith("_displacement.jpg"):
                (folder / "height.jpg").write_bytes(z.read(member))
    else:
        files = _get(f"https://api.polyhaven.com/files/{asset}").json()
        (folder / "color.jpg").write_bytes(_get(files["Diffuse"]["1k"]["jpg"]["url"], timeout=180).content)
        disp = files.get("Displacement", {}).get("1k", {}).get("jpg") or files.get("Displacement", {}).get("1k", {}).get("png")
        if disp:
            Image.open(io.BytesIO(_get(disp["url"], timeout=180).content)).convert("L").save(folder / "height.jpg")
    if not (folder / "color.jpg").exists():
        raise ValueError(f"{source} {asset}: no colour map found")


def _kmeans(px, k, iters=12, seed=0):
    rng = np.random.default_rng(seed)
    c = px[rng.choice(len(px), k, replace=False)].astype(np.float64)
    for _ in range(iters):
        lab = np.argmin(((px[:, None, :] - c[None]) ** 2).sum(-1), 1)
        for j in range(k):
            if (lab == j).any():
                c[j] = px[lab == j].mean(0)
    return c, np.bincount(lab, minlength=k)


def extract_palette(color, height=None, k=4):
    """Stone tones = k-means of the raised parts, joint = the mean of the lowest 15% (or the darkest tone)."""
    a = np.asarray(color.convert("RGB").resize((96, 96), Image.LANCZOS)).reshape(-1, 3).astype(np.float64)
    if height is not None:
        h = np.asarray(height.convert("L").resize((96, 96), Image.LANCZOS)).reshape(-1).astype(np.float64)
        lo = h <= np.percentile(h, 15)
        joint, stones_px = a[lo].mean(0), a[~lo]
    else:
        joint, stones_px = None, a
    cent, counts = _kmeans(stones_px, k)
    cent = cent[np.argsort(-counts)]
    if joint is None:
        joint = cent[np.argmin(cent.sum(1))] * 0.6
    hx = lambda c: "#%02x%02x%02x" % tuple(int(np.clip(v, 0, 255)) for v in c)
    return {"stones": [hx(c) for c in cent], "mortar": hx(joint), "variation": 0.06}


def repeats_per_tile(size_m):
    """Whole repeats of the texture per tile, from its real size (at least 1: a texture bigger than a tile is scaled
    down to one tile, since it only repeats seamlessly at its own edges)."""
    if not size_m:
        return 1
    return max(1, int(round(TILE_METERS / max(min(size_m), 0.05))))


def import_material(source, asset, meta=None):
    """Download, analyse and save a material. `meta` = the search hit (name, tags, size_m) when known."""
    if source not in ("ambientcg", "polyhaven"):
        raise ValueError("source: ambientcg or polyhaven")
    hit = meta or next((h for h in search(asset, source, 50) if h["asset"] == asset), None) or {"name": asset, "tags": [], "size_m": None}
    mid = _slug(f"{source}_{asset}")
    folder = cache_dir() / mid
    _fetch_maps(source, asset, folder)
    color = Image.open(folder / "color.jpg")
    height = Image.open(folder / "height.jpg") if (folder / "height.jpg").exists() else None
    tags = sorted({t.lower() for t in hit.get("tags", []) if t and not t.isdigit() and t.lower() != asset.lower()})
    mat = {
        "id": mid, "name": hit.get("name", asset), "source": source, "asset": asset, "license": "CC0",
        "url": f"https://ambientcg.com/view?id={asset}" if source == "ambientcg" else f"https://polyhaven.com/a/{asset}",
        "tags": tags, "descriptors": ", ".join(tags[:8]), "size_m": hit.get("size_m"),
        "palette": extract_palette(color, height), "repeats": repeats_per_tile(hit.get("size_m")),
        "imported": time.strftime("%Y-%m-%d"),
    }
    save(mat)
    return mat


# ----------------------------------------------------------------------------- store

def list_materials():
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(materials_dir().glob("*.json"))]


def load(mid):
    return json.loads((materials_dir() / f"{_slug(mid)}.json").read_text(encoding="utf-8"))


def save(mat):
    materials_dir().mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in mat.items() if k in ("id", "name", "source", "asset", "license", "url", "tags", "descriptors",
                                                   "size_m", "palette", "repeats", "imported", "notes")}
    (materials_dir() / f"{_slug(mat['id'])}.json").write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
    return keep


def image_path(mid, kind="color"):
    """A cached map (downloaded again when the cache was cleared)."""
    mat = load(mid)
    f = cache_dir() / mat["id"] / f"{'height' if kind == 'height' else 'color'}.jpg"
    if not f.exists():
        _fetch_maps(mat["source"], mat["asset"], cache_dir() / mat["id"])
    return f


# ----------------------------------------------------------------------------- as a template

def image_template(tpl, kind, per, height_tiles):
    """A material's seamless colour map as a family template: `repeats` copies per tile across (and down, for
    floors), so it repeats exactly once per tile like the procedural patterns. Walls: counted up from the base."""
    mat = load(tpl["material"])
    n = int(tpl.get("repeats") or mat.get("repeats") or 1)
    cell = max(8, per // n)
    color = Image.open(image_path(mat["id"])).convert("RGB").resize((cell, cell), Image.LANCZOS)
    hp = image_path(mat["id"], "height")
    height = Image.open(hp).convert("L").resize((cell, cell), Image.LANCZOS) if hp.exists() else None
    if kind == "wall":
        rows = int(np.ceil(height_tiles * n)) + 1
        H = int(round(height_tiles * per))
        a = np.tile(np.asarray(color), (rows, n, 1))[-H:]  # bottom rows: the texture starts at the wall's base
        h = np.tile(np.asarray(height), (rows, n))[-H:] if height is not None else None
    else:
        a = np.tile(np.asarray(color), (n, n, 1))
        h = np.tile(np.asarray(height), (n, n)) if height is not None else None
    W = a.shape[1]
    img = Image.fromarray(a).resize((per, int(round(a.shape[0] * per / W))), Image.LANCZOS)
    hm = (np.asarray(Image.fromarray(h).resize(img.size, Image.LANCZOS)).astype(np.float64) / 255) if h is not None else np.ones(img.size[::-1])
    return {"rgb": img, "height": hm, "region": (hm > np.percentile(hm, 15)).astype(np.uint8)}


# ----------------------------------------------------------------------------- Studio job kinds (hdart.core.jobs)

@jobs.kind("lib_import", gpu=False)   # downloads, no ComfyUI
def _job_import(a):
    return import_material(a["source"], a["asset"], a.get("hit"))["id"]
