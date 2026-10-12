"""hdart: Hearthdelve art pipeline CLI. Run `uv run hdart --help` from P:\Tools\art-pipeline."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .paths import REPO, ROOT                            # this tool / Unity project root (HD_PROJECT)
BLENDER_DEFAULT = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
SPEC = ROOT / "specs" / "character_kit.json"
PALETTES = ROOT / "specs" / "palettes.json"
KIT = ROOT / "kit" / "character_kit.blend"
CHAR_OUT = REPO / "Assets" / "Art" / "Characters"


def blender():
    return os.environ.get("HD_BLENDER", BLENDER_DEFAULT)


def run_blender(script, blend=None, **kw):
    cmd = [blender(), "-b"]
    if blend:
        cmd.append(str(blend))
    cmd += ["--factory-startup", "-P", str(ROOT / "blender" / script), "--"]
    for k, v in kw.items():
        if v is None or v is False:
            continue
        cmd.append("--" + k.replace("_", "-"))
        if v is not True:
            cmd.append(str(v))
    print(">", " ".join(cmd), flush=True)
    p = subprocess.run(cmd, text=True, capture_output=True)
    for line in p.stdout.splitlines():
        if line.startswith("[") or "Error" in line or "Traceback" in line or line.startswith("  File"):
            print(line)
    if p.returncode != 0 or "Traceback" in p.stdout or "Traceback" in p.stderr:
        print(p.stderr[-4000:], file=sys.stderr)
        sys.exit(1)


# ----------------------------------------------------------------------------- commands

def cmd_kit_build(a):
    if Path(a.out).exists() and not a.force:
        sys.exit(f"{a.out} exists (it may have hand edits). Pass --force to regenerate it.")
    run_blender("build_character_kit.py", spec=a.spec, out=a.out, bodies=a.bodies, parts=a.parts, textures=a.textures)


def cmd_kit_render(a):
    run_blender("render_character.py", blend=a.kit, spec=a.spec, out=a.out, bodies=a.bodies, parts=a.parts,
                views=a.views, textures=a.textures, supersample=a.supersample)
    from .palette import Palettes
    print(Palettes(PALETTES).export(Path(a.out) / "palettes.json"))


def _kit(a):
    from .compose import Kit
    return Kit(a.renders, a.spec, a.palettes)


def cmd_compose(a):
    from PIL import Image
    kit = _kit(a)
    app = json.loads(Path(a.appearance).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for view in kit.manifest["views"]:
        color, normal = kit.compose(app, view)
        Image.fromarray(color).save(out / f"{Path(a.appearance).stem}.{view}.png")
        Image.fromarray(normal).save(out / f"{Path(a.appearance).stem}.{view}.normal.png")
    print(f"composed -> {out}")


def cmd_preview(a):
    from .preview import crowd, gif, lineup, random_apps
    from .palette import Palettes
    kit = _kit(a)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    apps = random_apps(kit, a.count, a.seed)
    (out / "appearances.json").write_text(json.dumps(apps, indent=2), encoding="utf-8")
    made = []
    for view in kit.manifest["views"]:
        made.append(lineup(kit, apps[:min(8, len(apps))], view, out / f"lineup_{view}.png"))
        made.append(crowd(kit, apps, view, out / f"crowd_{view}.png"))
        made.append(gif(kit, apps[:12], view, out / f"anim_{view}.gif"))
    Palettes(a.palettes).swatch(out / "palettes.png")
    made.append(out / "palettes.png")
    for m in made:
        print(m)
    if a.open:
        for m in made:
            os.startfile(m)


TEXTURES = ROOT / "specs" / "textures.json"
ICONS = ROOT / "specs" / "icons.json"


def cmd_textures(a):
    from PIL import Image
    from . import comfy
    from .pixel import contact_sheet, height_to_normal, make_tileable
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    d, s = spec["defaults"], spec["sets"][a.set]
    out = Path(a.out) / a.set
    raw = ROOT / "work" / "textures_raw" / a.set
    only = set(a.only.split(",")) if a.only else None
    made = []
    for tid, t in s["items"].items():
        if only and tid not in only:
            continue
        cfg = {**d, **t}
        prompt = cfg["prompt"] + s.get("suffix", "")
        nodes = comfy.build_txt2img(prompt, cfg["size"], cfg["size"], seed=cfg["seed"] + a.seed, steps=cfg["steps"],
                                    lora_strength=cfg["lora"], prefix=f"hdart/tex_{a.set}_{tid}")
        p = comfy.run(nodes, raw)[0]
        img = Image.open(p).convert("RGB")
        if cfg["tileable"]:
            img = make_tileable(img)
        out.mkdir(parents=True, exist_ok=True)
        img.save(out / f"{tid}.png")
        height_to_normal(img).save(out / f"{tid}_n.png")
        made.append(img.resize((256, 256)))
        print(f"[tex] {a.set}/{tid}", flush=True)
    if made:
        sheet = out / "_contact.png"
        contact_sheet(made, cols=4, scale=1).save(sheet)
        print(sheet)


def cmd_icon(a):
    from PIL import Image
    from . import comfy
    from .pixel import contact_sheet, pixelize
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    d = spec["defaults"]
    entries = {k: v for k, v in spec["icons"].items() if not a.only or k in a.only.split(",")}
    out, raw = Path(a.out), ROOT / "work" / "icons_raw"
    sheet = []
    for iid, e in entries.items():
        cfg = {**d, **e}
        prompt = d["prefix"] + cfg["prompt"] + d["suffix"]
        for v in range(a.variants):
            nodes = comfy.build_txt2img(prompt, 512, 512, seed=cfg.get("seed", 1) + v + a.seed, steps=cfg["steps"],
                                        lora_strength=cfg["lora"], remove_bg=True, prefix=f"hdart/icon_{iid}")
            p = comfy.run(nodes, raw)[0]
            px = pixelize(Image.open(p), cfg["size"], cfg["colors"])
            name = iid if a.variants == 1 else f"{iid}.v{v + 1}"
            target = out / cfg.get("folder", "Items") / f"{name}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            px.save(target)
            sheet.append(px)
            print(f"[icon] {target}", flush=True)
    if sheet:
        p = ROOT / "work" / "icons_contact.png"
        contact_sheet(sheet, cols=8, scale=4).save(p)
        print(p)


def cmd_mesh(a):
    from . import comfy
    from .pixel import contact_sheet
    if a.prompt:  # concept first: FLUX.2 image of the object on a plain background
        nodes = comfy.build_txt2img(a.prompt + ", single object, centered, plain light grey background, "
                                    "three-quarter view, full object visible, game asset, soft even lighting",
                                    1024, 1024, seed=a.seed, lora_strength=0.0, prefix=f"hdart/concept_{a.name}")
        img_path = comfy.run(nodes, ROOT / "work" / "concepts")[0]
    else:
        img_path = Path(a.image)
    name = comfy.upload(img_path)
    paths = comfy.run(comfy.build_img2mesh(name, seed=a.seed, prefix=f"hdart/mesh_{a.name}"),
                      Path(a.out), timeout=3600)
    for p in paths:
        print(p)


def cmd_vfx(a):
    from .vfx import preview_gif, render_all
    sheets = render_all(a.spec, a.palettes, a.out, a.size)
    print(preview_gif(sheets, ROOT / "work" / "vfx_preview.gif", a.size))


def cmd_tiles(a):
    run_blender("render_tiles.py", spec=a.spec, out=a.out, tile=a.tile, pixel=not a.smooth)


def cmd_pixel_textures(a):
    from PIL import Image
    from .pixel import contact_sheet, pixel_texture
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    src = ROOT / spec["textureDir"]
    dst = ROOT / spec["pixelTextureDir"]
    dst.mkdir(parents=True, exist_ok=True)
    shown = []
    for name, size in spec["texturePixels"].items():
        img = pixel_texture(Image.open(src / f"{name}.png"), size, spec.get("texColors", 6))
        img.save(dst / f"{name}.png")
        shown.append(img)
        print(f"[pxtex] {name} {size}px")
    contact_sheet(shown, cols=6, scale=4).save(dst / "_contact.png")


def cmd_mesh_render(a):
    run_blender("render_mesh.py", glb=a.glb, out=a.out, name=a.name, height=a.height, views=a.views,
                cell=a.cell, yaw=a.yaw, colors=a.colors, voxel=a.voxel, saturation=a.saturation,
                outline=not a.no_outline)


def cmd_room(a):
    from .compose import Kit
    from .preview import random_apps
    from .room_preview import render_room
    kit = Kit(a.renders, SPEC, PALETTES)
    apps = random_apps(kit, a.heroes, a.seed)
    for app, d in zip(apps, ["sw", "se", "sw", "ne", "se", "sw", "nw", "se"]):
        app["_dir"] = d
    render_room(a.tiles, kit, apps, out=a.out, scale=a.scale, seed=a.seed)
    print(a.out)


SPRITE_SPEC = ROOT / "specs" / "sprite_kit.json"


def cmd_sprite_kit(a):
    from .sprite_kit import bake
    print(bake(a.spec, a.out, parts=a.parts.split(",") if a.parts else None, res=a.res))


def cmd_sprite_export(a):
    from .sprite_kit import TEMPLATES, export_templates
    n = export_templates(tuple(a.bodies.split(",")), a.parts.split(",") if a.parts else None, a.overwrite)
    print(f"{n} template layers -> {TEMPLATES}  (palette: {TEMPLATES.parent / 'index_palette.gpl'})")


AI_PARTS = ROOT / "specs" / "ai_parts.json"


def cmd_ai_base(a):
    from .ai_kit import build_base_part, make_base
    for body in a.bodies.split(","):
        for facing in ("front", "back"):
            print(make_base(body, facing, a.seed))
            print(build_base_part(body, facing))


def cmd_ai_part(a):
    from .ai_kit import make_part
    print(make_part(a.part, a.prompt, a.body, seed=a.seed, lora=a.lora))


def cmd_ai_kit(a):
    from .ai_kit import make_part
    lib = json.loads(Path(a.spec).read_text(encoding="utf-8"))["parts"]
    only = set(a.only.split(",")) if a.only else None
    for pid, e in lib.items():
        if only and pid not in only:
            continue
        for body in a.bodies.split(","):
            make_part(pid, e["prompt"], body, seed=e.get("seed", 5) + a.seed, lora=e.get("lora", 0.6))
            print(f"[ai] {body} {pid}", flush=True)


def cmd_studio(a):
    from .studio import serve
    if not (Path(a.renders) / "manifest.json").exists():
        from .sprite_kit import bake
        bake(a.spec, a.renders)
    serve(a.renders, a.spec, a.palettes, a.port, not a.no_browser)


def cmd_dungeon_export(a):
    from . import dungeon
    print(dungeon.export(a.set))


def cmd_dungeon_capture(a):
    from . import dungeon
    for f in dungeon.capture(a.set):
        print(dungeon.CAPTURES / f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="hdart", description=__doc__)
    sub = p.add_subparsers(required=True)

    k = sub.add_parser("kit-build", help="generate the modular character kit .blend")
    k.add_argument("--spec", default=str(SPEC)); k.add_argument("--out", default=str(KIT))
    k.add_argument("--bodies"); k.add_argument("--parts"); k.add_argument("--textures")
    k.add_argument("--force", action="store_true")
    k.set_defaults(fn=cmd_kit_build)

    r = sub.add_parser("kit-render", help="render every kit part into indexed layer sheets")
    r.add_argument("--kit", default=str(KIT)); r.add_argument("--spec", default=str(SPEC))
    r.add_argument("--out", default=str(CHAR_OUT)); r.add_argument("--bodies"); r.add_argument("--parts")
    r.add_argument("--views"); r.add_argument("--textures"); r.add_argument("--supersample")
    r.set_defaults(fn=cmd_kit_render)

    for name, fn, hlp in (("compose", cmd_compose, "compose one appearance JSON into sprite sheets"),
                          ("preview", cmd_preview, "random characters: lineups, crowd grid, GIFs")):
        c = sub.add_parser(name, help=hlp)
        c.add_argument("--renders", default=str(CHAR_OUT)); c.add_argument("--spec", default=str(SPEC))
        c.add_argument("--palettes", default=str(PALETTES)); c.add_argument("--out", default=str(ROOT / "work" / "preview"))
        if name == "compose":
            c.add_argument("appearance")
        else:
            c.add_argument("--count", type=int, default=24); c.add_argument("--seed", type=int, default=1)
            c.add_argument("--open", action="store_true")
        c.set_defaults(fn=fn)

    t = sub.add_parser("textures", help="generate a tileable texture set with FLUX.2 (ComfyUI)")
    t.add_argument("set"); t.add_argument("--spec", default=str(TEXTURES))
    t.add_argument("--out", default=str(ROOT / "textures")); t.add_argument("--only")
    t.add_argument("--seed", type=int, default=0)
    t.set_defaults(fn=cmd_textures)

    i = sub.add_parser("icons", help="pixel-art icons for items/spells (FLUX.2 + pixel LoRA + BiRefNet)")
    i.add_argument("--spec", default=str(ICONS)); i.add_argument("--out", default=str(REPO / "Assets" / "Art" / "Icons"))
    i.add_argument("--only"); i.add_argument("--variants", type=int, default=1); i.add_argument("--seed", type=int, default=0)
    i.set_defaults(fn=cmd_icon)

    m = sub.add_parser("mesh", help="image (or prompt -> concept image) to textured GLB with TRELLIS.2")
    m.add_argument("name"); g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--image"); g.add_argument("--prompt")
    m.add_argument("--seed", type=int, default=42); m.add_argument("--out", default=str(ROOT / "work" / "meshes"))
    m.set_defaults(fn=cmd_mesh)

    v = sub.add_parser("vfx", help="procedural pixel spell effects (battle flipbooks)")
    v.add_argument("--spec", default=str(ROOT / "specs" / "spells.json")); v.add_argument("--palettes", default=str(PALETTES))
    v.add_argument("--out", default=str(REPO / "Assets" / "Art" / "VFX")); v.add_argument("--size", type=int, default=64)
    v.set_defaults(fn=cmd_vfx)

    tl = sub.add_parser("tiles", help="render an isometric biome tile set from its textures (Blender)")
    tl.add_argument("--spec", default=str(ROOT / "specs" / "tiles_crypt.json"))
    tl.add_argument("--out", default=str(REPO / "Assets" / "Art" / "Tiles")); tl.add_argument("--tile", type=int, default=32)
    tl.add_argument("--smooth", action="store_true", help="use the full-res textures instead of the pixel textures")
    tl.set_defaults(fn=cmd_tiles)

    px = sub.add_parser("pixel-textures", help="shrink + posterize a biome's textures for pixel-art tiles")
    px.add_argument("--spec", default=str(ROOT / "specs" / "tiles_crypt.json"))
    px.set_defaults(fn=cmd_pixel_textures)

    mr = sub.add_parser("mesh-render", help="render a GLB into battle / iso / icon sprites (Blender)")
    mr.add_argument("glb"); mr.add_argument("--name"); mr.add_argument("--out", default=str(REPO / "Assets" / "Art" / "Monsters"))
    mr.add_argument("--height", default="1.8"); mr.add_argument("--views", default="battle,iso4,icon")
    mr.add_argument("--cell", default="96"); mr.add_argument("--yaw", default="0"); mr.add_argument("--colors", default="16")
    mr.add_argument("--voxel", default="0.075", help="voxel size in world units (0 = smooth)")
    mr.add_argument("--saturation", default="1.3"); mr.add_argument("--no-outline", action="store_true")
    mr.set_defaults(fn=cmd_mesh_render)

    rm = sub.add_parser("room", help="mock crypt room with random heroes and torch light (review image)")
    rm.add_argument("--renders", default=str(CHAR_OUT)); rm.add_argument("--tiles", default=str(REPO / "Assets" / "Art" / "Tiles" / "crypt"))
    rm.add_argument("--out", default=str(ROOT / "work" / "room_preview.png")); rm.add_argument("--heroes", type=int, default=6)
    rm.add_argument("--seed", type=int, default=8); rm.add_argument("--scale", type=int, default=2)
    rm.set_defaults(fn=cmd_room)

    sk = sub.add_parser("sprite-kit", help="bake the 2D JRPG sprite kit (hdart/sprite_kit.py) into layer sheets")
    sk.add_argument("--spec", default=str(SPRITE_SPEC)); sk.add_argument("--out", default=str(CHAR_OUT))
    sk.add_argument("--parts"); sk.add_argument("--res", type=int, default=64, help="grid size: 32, 64, 128 or 256")
    sk.set_defaults(fn=cmd_sprite_kit)

    se = sub.add_parser("sprite-export", help="write parts as paintable PNG templates (index palette)")
    se.add_argument("--bodies", default="slim"); se.add_argument("--parts")
    se.add_argument("--overwrite", action="store_true", help="replace templates you may have painted")
    se.set_defaults(fn=cmd_sprite_export)

    ab = sub.add_parser("ai-base", help="generate the AI base mannequin (front + back) per build")
    ab.add_argument("--bodies", default="slim"); ab.add_argument("--seed", type=int, default=21)
    ab.set_defaults(fn=cmd_ai_base)

    ap = sub.add_parser("ai-part", help="generate one part as an edit of the base mannequin")
    ap.add_argument("part"); ap.add_argument("--prompt", required=True); ap.add_argument("--body", default="slim")
    ap.add_argument("--seed", type=int, default=5); ap.add_argument("--lora", type=float, default=0.6)
    ap.set_defaults(fn=cmd_ai_part)

    ak = sub.add_parser("ai-kit", help="generate every part in specs/ai_parts.json")
    ak.add_argument("--spec", default=str(AI_PARTS)); ak.add_argument("--bodies", default="slim")
    ak.add_argument("--only"); ak.add_argument("--seed", type=int, default=0)
    ak.set_defaults(fn=cmd_ai_kit)

    so = sub.add_parser("studio", help="Sprite Studio: mix parts/colours, play animations, live voxel-sheet view")
    so.add_argument("--renders", default=str(ROOT / "work" / "sprite_render")); so.add_argument("--spec", default=str(SPRITE_SPEC))
    so.add_argument("--palettes", default=str(PALETTES)); so.add_argument("--port", type=int, default=4321)
    so.add_argument("--no-browser", action="store_true")
    so.set_defaults(fn=cmd_studio)

    de = sub.add_parser("dungeon-export", help="export a dungeon set's approved pieces to Unity (StreamingAssets)")
    de.add_argument("set", nargs="?", default="crypt")
    de.set_defaults(fn=cmd_dungeon_export)

    dc = sub.add_parser("dungeon-capture", help="export a dungeon set and screenshot Prototype V with it (Unity batch)")
    dc.add_argument("set", nargs="?", default="crypt")
    dc.set_defaults(fn=cmd_dungeon_capture)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
