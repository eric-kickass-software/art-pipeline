"""Limner: icons and hero portraits (Studio page /limner).

Categories: items (the game's base items without a slot: consumables, tools, keys), gear (every equippable base item,
plus fallbacks: cloth/leather/plate per armour slot, one per weapon type), classes, statuses, damageTypes, skills
(placeholders per damage type until the class waves), ui (menu screens and other UI icons) and portraits (archetype ×
look × gender, the static set players pick from). Items, gear and ui take Eric's own entries ("added" in the spec).

Everything is tunable after the fact, one layer at a time:
  style    (specs/limner.json "styles")      the look: prefix/suffix words, LoRA strength
  category ("categories")                    default style, extra words, generation size, the finish
  entry    ("entries"["<cat>/<id>"])         Eric's subject, seed, style, finish overrides (crop for portraits)
  candidate (sprites/limner/<cat>/<id>/<cid>/) master.png (kept), the finished icon.png / portrait.png + thumb.png,
                                             meta.json with the exact prompt and seed (the prompt history)
The finish (size, colours, outline, crop) is re-run on the kept masters without the GPU (refinish).
Approvals in sprites/limner/state.json; export writes <project>/Art/Icons/<cat>/ and Art/Portraits/ (+ index.json).
"""
import random
import re
from pathlib import Path

from PIL import Image

from . import config, contract, jobs, store

SPEC = config.specs_dir() / "limner.json"
ICON_CATS = ("items", "gear", "classes", "statuses", "damageTypes", "skills", "ui")
PORTRAIT_CATS = ("portraits", "monsters", "npcs")   # all export into Art/Portraits with one index.json
CATS = ICON_CATS + PORTRAIT_CATS + ("tokens",)
EQUIP_KINDS = ("weapon", "armor", "trinket")   # only for wanted rows without content: equippable unless told otherwise
GEAR_GROUPS = {"armor": "Armour", "weapon": "Weapons & held", "trinket": "Trinkets"}
ADD_PREFIX = {"items": "item.", "gear": "item.", "ui": "ui."}   # categories you can add entries to in the page


def spec():
    return store.read_json(SPEC)


def root():
    return config.data_dir() / "sprites" / "limner"


def state_store() -> store.State:
    """The Limner's state file (approvals per category and entry) as a store.State."""
    return store.State(root() / "state.json", {"approved": {}})


def _safe(s):
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", s or ""):
        raise ValueError(f"bad id {s!r}")
    return s


def entry_dir(cat, eid):
    if cat not in CATS:
        raise ValueError(f"unknown category {cat}")
    return root() / cat / _safe(eid)


# ----------------------------------------------------------------------------- the entry lists

def _req():
    try:
        return contract.load() or {}
    except Exception:
        return {}


def entries(sp=None, req=None):
    """{category: [{id, name, sub, draft}]}: what each category should hold, from the game's contract and the spec."""
    sp, req = sp or spec(), req if req is not None else _req()
    wanted = (req.get("wanted") or {}).get("icons") or {}
    subj = sp.get("subjects", {})
    out = {c: [] for c in CATS}

    # Every base item of the game (its content files), plus wanted ids the content doesn't have. Not the wanted list
    # alone: the game drops ids from it once they have an icon. Equippables (a slot) go to Gear, the rest to Items.
    try:
        rows = {k: dict(v) for k, v in contract.game_items().items()}
    except Exception:
        rows = {}
    for w in wanted.get("items", []):
        rows.setdefault(w["id"], {**w, "slot": w.get("slot") or ("?" if w.get("kind") in EQUIP_KINDS else None)})
    for iid, r in sorted(rows.items()):
        name, text = r.get("name") or iid, r.get("text")
        draft = f"{name}, {text.rstrip('.').lower()}" if text else name.lower()
        if r.get("slot"):
            sub = r.get("weaponType") or (r.get("slot") if r.get("slot") != "?" else r.get("kind", ""))
            out["gear"].append({"id": iid, "name": name, "sub": sub, "group": GEAR_GROUPS.get(r.get("kind"), "Gear"),
                                "text": text or "", "draft": draft})
        else:
            out["items"].append({"id": iid, "name": name, "sub": r.get("kind", ""), "group": "Game items",
                                 "text": text or "", "draft": draft})

    for gid, s in subj.get("gear", {}).items():
        parts = gid.split(".")
        name = f"{parts[2].title()} {parts[1]}" if parts[0] == "slot" else parts[1].title()
        armour = parts[0] == "slot"
        out["gear"].append({"id": gid, "name": name, "sub": "armour slot" if armour else "weapon type",
                            "group": "Fallbacks: armour slot × material" if armour else "Fallbacks: weapon type", "draft": s})

    for uid, s in subj.get("ui", {}).items():
        parts = uid.split(".")   # ui.menu.end_turn → "End turn", label "menu"
        out["ui"].append({"id": uid, "name": parts[-1].replace("_", " ").capitalize(), "sub": " · ".join(parts[1:-1]),
                          "group": "Menu & UI", "draft": s})

    # wanted lists shrink as icons are delivered, so the spec's subjects keep the rest listed
    def _union(rows, keys):
        have = {r["id"] for r in rows}
        return list(rows) + [{"id": k, "name": k.split(".")[1].replace("_", " ").title()} for k in keys if k not in have]

    classes = _union(wanted.get("classes") or [], subj.get("classes", {}))
    for c in classes:
        out["classes"].append({"id": c["id"], "name": c["name"], "sub": c.get("role", ""), "text": c.get("text", ""),
                               "draft": subj.get("classes", {}).get(c["id"]) or f"an emblem of the {c['name']}"})

    statuses = _union(wanted.get("statuses") or [], subj.get("statuses", {}))
    for s in statuses:
        out["statuses"].append({"id": s["id"], "name": s["name"], "sub": "",
                                "draft": subj.get("statuses", {}).get(s["id"]) or s["name"].lower()})

    dts = req.get("damageTypes") or list(subj.get("damageTypes", {}))
    for dt in dts:
        motif = subj.get("damageTypes", {}).get(dt) or f"{dt} magic"
        out["damageTypes"].append({"id": dt, "name": dt.title(), "sub": "", "draft": motif})
        out["skills"].append({"id": f"placeholder.{dt}.active", "name": f"{dt.title()} skill", "sub": "placeholder · active",
                              "draft": f"{motif} unleashed as an attack spell, swirling energy"})
        out["skills"].append({"id": f"placeholder.{dt}.passive", "name": f"{dt.title()} passive", "sub": "placeholder · passive",
                              "draft": f"{motif} engraved on a round stone medallion, soft aura"})

    # Player portraits per race (Eric, 2026-11-07): humans keep their ids portrait.<archetype>.<look>.<g>, the other
    # races are portrait.<race>.<archetype>.<look>.<g>. The race's traits go after the look.
    races = sp.get("races") or {"human": {"name": "Human", "noun": "human"}}
    for rk, r in races.items():
        for ak, a in sp.get("archetypes", {}).items():
            for lk, look in a.get("looks", {}).items():
                for gk, g in sp.get("genders", {"m": "male", "f": "female"}).items():
                    pid = f"portrait.{ak}.{lk}.{gk}" if rk == "human" else f"portrait.{rk}.{ak}.{lk}.{gk}"
                    traits = f", {r['traits']}" if r.get("traits") else ""
                    out["portraits"].append({"id": pid, "name": f"{a['name']} · {lk} · {gk.upper()}", "sub": a["name"],
                                             "race": rk, "archetype": ak, "look": lk, "gender": gk,
                                             "group": f"{r['name']} · {a['name']}",
                                             "draft": f"a {g} {r.get('noun', rk)} {look}{traits}"})

    # Monsters and friendly NPCs (R-011, R-013, R-014): a portrait each, and a board token (one ¾ standee) each.
    # Rows come from the game's content; the look from subjects.monsters / subjects.npcs, else the name and text.
    looks = {**subj.get("monsters", {}), **subj.get("npcs", {})}
    for kind, cat, pre in (("monsters", "monsters", "monster."), ("npcs", "npcs", "npc.")):
        try:
            rows = contract.game_content(kind, pre)
        except Exception:
            rows = {}
        for mid, r in rows.items():
            ph = (r.get("visual") or {}).get("placeholder") or {}
            if ph.get("shape") == "block":   # not a creature (the restoring pond): Dungeon Forge makes it
                continue
            fam = (r.get("family") or "").replace("family.", "")
            name = r.get("name") or _title(mid)
            draft = looks.get(mid) or (f"{name}, {r['text'].rstrip('.').lower()}" if r.get("text") else name.lower())
            tags = r.get("tags") or []
            rank = "boss" if "boss" in tags or ph.get("scale", 1) >= 1.5 else "miniboss" if "miniboss" in tags else ""
            group = {"boss": "Bosses", "miniboss": "Mini-bosses"}.get(rank) or (fam.title() if fam else "NPCs")
            sub = " · ".join(x for x in (r.get("role"), rank.replace("miniboss", "mini-boss")) if x)
            shape = (sp.get("shape_overrides") or {}).get(mid) or ph.get("shape", "humanoid")   # e.g. mass: swarms, blobs
            row = {"id": mid, "name": name, "sub": sub, "group": group, "shape": shape,
                   "family": fam, "role": r.get("role", ""), "scale": ph.get("scale", 1), "text": r.get("text", ""),
                   "draft": draft, "archetype": "monster" if kind == "monsters" else "npc"}
            out[cat].append(row)
            out["tokens"].append({**row, "group": f"Monsters · {group}" if kind == "monsters" else "NPCs"})
    for cat in ("monsters", "tokens"):   # one block per group: families as the content lists them, then mini-bosses, bosses, NPCs
        seen = list(dict.fromkeys(x["group"] for x in out[cat]))
        last = [g for k in ("Mini-bosses", "Bosses", "NPCs") for g in seen if g.endswith(k)]
        rank = {g: i for i, g in enumerate([g for g in seen if g not in last] + last)}
        out[cat].sort(key=lambda x: rank[x["group"]])

    # Eric's own entries (added in the page): items/gear the game doesn't have yet, extra UI icons, …
    for cat, es in (sp.get("added") or {}).items():
        have = {x["id"] for x in out.get(cat, [])}
        for eid, a in es.items():
            if cat in out and eid not in have:
                out[cat].append({"id": eid, "name": a.get("name") or _title(eid), "sub": a.get("sub", ""),
                                 "group": "Added here", "added": True, "draft": a.get("draft") or (a.get("name") or eid).lower()})
    order = {"gear": list(GEAR_GROUPS.values()) + ["Gear", "Fallbacks: armour slot × material", "Fallbacks: weapon type"]}
    for cat, es in out.items():   # stable: groups in a fixed order, entries keep their order inside a group
        if cat in order:
            es.sort(key=lambda x: order[cat].index(x["group"]) if x.get("group") in order[cat] else len(order[cat]))
    return out


def _title(eid):
    return eid.split(".", 1)[-1].replace(".", " ").replace("_", " ").capitalize()


def _entry(cat, eid, sp=None):
    sp = sp or spec()
    e = next((x for x in entries(sp)[cat] if x["id"] == eid), None)
    if e is None:
        raise ValueError(f"no {cat} entry {eid}")
    return e


def override(sp, cat, eid):
    return sp.get("entries", {}).get(f"{cat}/{eid}", {})


def subject(cat, eid, sp=None):
    sp = sp or spec()
    return override(sp, cat, eid).get("subject") or _entry(cat, eid, sp)["draft"]


def style_of(cat, eid, sp=None):
    sp = sp or spec()
    c = sp["categories"][cat]
    shaped = c.get("shapes", {}).get(_entry(cat, eid, sp).get("shape"), {}).get("style") if c.get("shapes") else None
    return override(sp, cat, eid).get("style") or shaped or c["style"]


def prompt(cat, eid, style=None, sp=None, extra=None):
    """style words + category words + subject (+ a batch's variation words) + category words + style words."""
    sp = sp or spec()
    c = sp["categories"][cat]
    st = sp["styles"][style or style_of(cat, eid, sp)]
    subj = subject(cat, eid, sp) + (f", {extra.strip()}" if extra and extra.strip() else "")
    if c.get("shapes"):   # monsters: beasts get their own framing ("bust from the chest up" makes anything a person)
        c = {**c, **c["shapes"].get(_entry(cat, eid, sp).get("shape"), {})}
    return st["prefix"] + c.get("prefix", "") + subj + c.get("suffix", "") + st["suffix"]


def finish_params(cat, eid, sp=None):
    sp = sp or spec()
    f = dict(sp["categories"][cat]["finish"])
    f.update(override(sp, cat, eid).get("finish", {}))
    return f


# ----------------------------------------------------------------------------- the finish (no GPU)

def _hex(c):
    c = (c or "").lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else None


def finish_icon(master, f):
    from .pixel import add_outline, alpha_bbox, pixelize
    img = master.convert("RGBA")
    size, margin, outline = int(f.get("size", 64)), int(f.get("margin", 4)), _hex(f.get("outline"))
    if int(f.get("colors", 0)) > 0:
        return pixelize(img, size, int(f["colors"]), outline=outline, margin=margin)
    img = img.crop(alpha_bbox(img))
    inner = size - 2 * margin
    s = inner / max(img.width, img.height)
    img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    out = Image.new("RGBA", (size, size))
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return add_outline(out, outline) if outline else out


def finish_portrait(master, f):
    img = master.convert("RGB")
    w = int(f.get("width", 512))
    big = img.resize((w, round(img.height * w / img.width)), Image.LANCZOS)
    x, y, s = f.get("crop", [0.25, 0.06, 0.5])
    side = s * img.width
    box = (round(x * img.width), round(y * img.width), round(x * img.width + side), round(y * img.width + side))
    t = int(f.get("thumb", 128))
    return big, img.crop(box).resize((t, t), Image.LANCZOS)


def finish_token(master, f):
    """A board token: the figure cut to its outline, `size` px tall (feet `margin` px above the bottom, centred),
    mirrored when the art faces left (finish.mirror), and a normal map from its shading. Returns (token, normal, pivot)."""
    import numpy as np
    from .pixel import alpha_bbox, height_to_normal
    img = master.convert("RGBA")
    a = np.asarray(img)[..., 3]
    img.putalpha(Image.fromarray(np.where(a < 24, 0, a).astype(np.uint8)))   # BiRefNet haze around the figure
    img = img.crop(alpha_bbox(img))
    if f.get("mirror"):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    size, margin = int(f.get("size", 512)), int(f.get("margin", 8))
    s = (size - 2 * margin) / max(img.width, img.height)   # fits a size × size box: tall figures and wide blobs alike
    img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    tok = Image.new("RGBA", (img.width + 2 * margin, img.height + 2 * margin))
    tok.alpha_composite(img, (margin, margin))
    flat = Image.new("RGB", tok.size, (128, 128, 128))
    flat.paste(tok, mask=tok.getchannel("A"))
    nm = height_to_normal(flat, float(f.get("normal", 2.0))).convert("RGBA")
    nm.putalpha(tok.getchannel("A"))
    return tok, nm, [0.5, round(margin / tok.height, 4)]


def finish(cat, eid, d, sp=None):
    """(Re)make a candidate's finished image(s) from its kept master."""
    d = Path(d)
    f = finish_params(cat, eid, sp)
    with Image.open(d / "master.png") as m:
        m.load()
        if f["kind"] == "token":
            tok, nm, _ = finish_token(m, f)
            tok.save(d / "token.png")
            nm.save(d / "token.n.png")
        elif f["kind"] == "portrait":
            big, thumb = finish_portrait(m, f)
            big.save(d / "portrait.png")
            thumb.save(d / "thumb.png")
        else:
            finish_icon(m, f).save(d / "icon.png")


def refinish(cat, ids=None):
    sp, n = spec(), 0
    for e in entries(sp)[cat]:
        if ids and e["id"] not in ids:
            continue
        for d in store.folders(entry_dir(cat, e["id"]), finished=True):
            finish(cat, e["id"], d, sp)
            n += 1
    return {"refinished": n}


# ----------------------------------------------------------------------------- generation (GPU, via the job queue)

def _remove_bg(img):
    from . import comfy

    def build(n):
        g = comfy.Graph()
        load = g.add("LoadImage", image=n["img"])
        bgm = g.add("LoadBackgroundRemovalModel", bg_removal_name=config.model("birefnet"))
        mask = g.add("RemoveBackground", bg_removal_model=bgm[0], image=load[0])
        inv = g.add("InvertMask", mask=mask[0])
        rgba = g.add("JoinImageWithAlpha", image=load[0], alpha=inv[0])
        g.add("SaveImage", images=rgba[0], filename_prefix="hdart/limner_bg")
        return g.nodes
    return comfy.run_images({"img": img.convert("RGB")}, build, mode="RGBA")


def _save(cat, eid, img, seed, p, style, kind, sp, **extra):
    cid, d = store.new(entry_dir(cat, eid), seed=seed)
    img.save(d / "master.png")
    finish(cat, eid, d, sp)
    c = sp["categories"][cat]
    store.write_meta(d, "limner", seed=seed, prompts={"prompt": p}, params={"style": style, "gen": c["gen"],
                     "lora": sp["styles"][style].get("lora", 0.0)}, category=cat, entry=eid, kind=kind, **extra)
    return cid


def generate(cat, eid, n=3, seed=None, style=None, extra=None, batch=None, timeout=None):
    """n candidates. seed: fixed start (seed, seed+1, …), else the entry's seed, else random. extra: variation words
    after the subject. batch: the overnight batch id (in meta, for review). timeout: seconds per image (batches)."""
    from . import comfy
    sp = spec()
    style = style or style_of(cat, eid, sp)
    p, g = prompt(cat, eid, style, sp, extra), sp["categories"][cat]["gen"]
    lora = sp["styles"][style].get("lora", 0.0)
    if seed is None:
        seed = override(sp, cat, eid).get("seed")
    made = []
    for i in range(int(n)):
        s = int(seed) + i if seed not in (None, "") else random.randrange(1, 2 ** 31)
        nodes = comfy.build_txt2img(p, g["width"], g["height"], seed=s, steps=g.get("steps", 4), lora_strength=lora,
                                    remove_bg=bool(g.get("remove_bg")), prefix="hdart/limner")
        tags = {k: v for k, v in (("batch", batch), ("extra", extra)) if v}
        made.append(_save(cat, eid, comfy.image(nodes, timeout=timeout), s, p, style, "txt2img", sp, **tags))
    return made


def vary(cat, eid, cid, n=3, denoise=0.45):
    """Variations of a candidate: img2img from its master with the entry's current prompt (so a prompt tweak plus a
    low denoise nudges a near-miss)."""
    from . import comfy
    sp = spec()
    src = entry_dir(cat, eid) / _safe(cid)
    meta = store.read_meta(src)
    style = meta.get("params", {}).get("style") or style_of(cat, eid, sp)
    p, g = prompt(cat, eid, style, sp), sp["categories"][cat]["gen"]
    with Image.open(src / "master.png") as m:
        base = m.convert("RGBA")
    init = Image.new("RGBA", base.size, (255, 255, 255, 255))
    init.alpha_composite(base)
    made = []
    for _ in range(int(n)):
        s = random.randrange(1, 2 ** 31)
        out = comfy.img2img(init.convert("RGB"), p, seed=s, denoise=float(denoise), lora=sp["styles"][style].get("lora", 0.0),
                            steps=8, prefix="hdart/limner_vary").resize(base.size, Image.LANCZOS)
        if g.get("remove_bg"):
            out = _remove_bg(out)
        made.append(_save(cat, eid, out, s, p, style, "vary", sp, inputs={"from": cid}, denoise=float(denoise)))
    return made


@jobs.kind("limner_gen", reload=("limner",))
def _job_gen(a):
    return generate(a["cat"], a["id"], a.get("n", 3), a.get("seed"), a.get("style"), a.get("extra"), a.get("batch"),
                    a.get("timeout"))


@jobs.kind("limner_vary", reload=("limner",))
def _job_vary(a):
    return vary(a["cat"], a["id"], a["cid"], a.get("n", 3), a.get("denoise", 0.45))


# ----------------------------------------------------------------------------- overnight batches

BATCH = {"retries": 2, "timeout": {"icon": 240, "portrait": 420, "token": 300}, "secs": {"icon": 4, "portrait": 6, "token": 6}}


def plan_batch(cat, ids, styles=None, variants=None, n=2, seeds="random", seed=None, sp=None):
    """The jobs of one batch: every entry × style × variation line, n candidates each. seeds: "random", or "sweep"
    (each job starts at `seed` and the next job continues where it stopped, so a sweep never repeats a seed).
    Returns (batch id, [job args], estimate {jobs, images, minutes})."""
    import time
    sp = sp or spec()
    kind = sp["categories"][cat]["finish"]["kind"]
    variants = [v.strip() for v in (variants or [""])] or [""]
    styles = [x for x in (styles or [None]) if x is None or x in sp["styles"]] or [None]
    bid = time.strftime("b%m%d_%H%M%S")
    jobs_, k = [], int(seed or 1)
    for eid in ids:
        _entry(cat, eid, sp)
        for st in styles:
            for v in variants:
                a = {"cat": cat, "id": eid, "n": int(n), "style": st, "extra": v or None, "batch": bid,
                     "retries": BATCH["retries"], "timeout": BATCH["timeout"][kind]}
                if seeds == "sweep":
                    a["seed"], k = k, k + int(n)
                jobs_.append(a)
    images = len(jobs_) * int(n)
    return bid, jobs_, {"jobs": len(jobs_), "images": images, "minutes": round(images * BATCH["secs"][kind] / 60 + len(jobs_) * 0.05, 1)}


def ensure_watchdog():
    """Start the watchdog (hdart/watchdog.py) as its own detached process unless one is alive. It keeps the PC awake
    while jobs remain, restarts a dead Studio or ComfyUI, unsticks hung renders, and exits when the queue is done."""
    import os, subprocess, sys
    from . import watchdog
    if watchdog.alive():
        return False
    flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    subprocess.Popen([sys.executable, "-m", "hdart.watchdog"], cwd=config.ROOT, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def mark_reviewed(cat):
    import time
    with state_store().edit() as s:
        s.setdefault("reviewed", {})[cat] = int(time.time())
    return {"ok": True}


# ----------------------------------------------------------------------------- state, edits, page data

def approve(cat, eid, cid, on=True):
    with state_store().edit() as s:
        store.pick(s, ("approved", cat, eid), cid, on)
    return {"ok": True}


def archive(cat, eid, cid):
    src = entry_dir(cat, eid) / _safe(cid)
    approve(cat, eid, cid, False)
    store.archive(src, config.data_dir() / "archive" / "limner" / cat / eid / cid)
    return {"ok": True}


def _edit_spec(fn):
    with store.lock(SPEC):
        sp = spec()
        fn(sp)
        store.write_json(SPEC, sp)
    return sp


def save_entry(cat, eid, fields):
    """Per-entry overrides: subject, style, seed, finish ({} keys merged; a value of None or "" removes it)."""
    def fn(sp):
        e = sp.setdefault("entries", {}).setdefault(f"{cat}/{eid}", {})
        for k, v in fields.items():
            if k == "finish":
                fin = e.setdefault("finish", {})
                for fk, fv in (v or {}).items():
                    if fv in (None, ""):
                        fin.pop(fk, None)
                    else:
                        fin[fk] = fv
                if not fin:
                    e.pop("finish")
            elif v in (None, ""):
                e.pop(k, None)
            else:
                e[k] = v
        if not e:
            sp["entries"].pop(f"{cat}/{eid}")
    _edit_spec(fn)
    if "finish" in fields:
        refinish(cat, [eid])
    return {"ok": True}


def save_category(cat, fields):
    def fn(sp):
        c = sp["categories"][cat]
        for k in ("style", "prefix", "suffix"):
            if k in fields:
                c[k] = fields[k]
        for k in ("gen", "finish"):
            if k in fields:
                c[k].update(fields[k])
    _edit_spec(fn)
    if "finish" in fields:
        refinish(cat)
    return {"ok": True}


def save_style(key, fields):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise ValueError("style keys are lowercase words")

    def fn(sp):
        st = sp["styles"].setdefault(key, {"name": key, "prefix": "", "suffix": "", "lora": 0.0})
        for k in ("name", "prefix", "suffix"):
            if k in fields:
                st[k] = fields[k]
        if "lora" in fields:
            st["lora"] = float(fields["lora"])
    _edit_spec(fn)
    return {"ok": True}


def save_look(archetype, look, text):
    """Add, change (text) or remove (text "") one look of an archetype: each look makes a male and a female portrait."""
    if not re.fullmatch(r"[a-z][a-z0-9]*", look):
        raise ValueError("look keys are one lowercase word")

    def fn(sp):
        looks = sp["archetypes"][archetype].setdefault("looks", {})
        if text:
            looks[look] = text
        else:
            looks.pop(look, None)
    _edit_spec(fn)
    return {"ok": True}


def save_race(key, fields):
    """Add or change a race of the player portraits (name, noun, traits); every archetype × look × gender gets one."""
    if not re.fullmatch(r"[a-z][a-z0-9]*", key or ""):
        raise ValueError("race keys are one lowercase word (e.g. halforc)")

    def fn(sp):
        races = sp.setdefault("races", {"human": {"name": "Human", "noun": "human"}})
        r = races.setdefault(key, {"name": key.title(), "noun": key})
        for k in ("name", "noun", "traits"):
            if k in fields:
                if fields[k]:
                    r[k] = fields[k].strip()
                elif k == "traits":
                    r.pop(k, None)
    _edit_spec(fn)
    return {"ok": True}


def add_entry(cat, eid, name, subject="", sub=""):
    """Add (or rename) one of Eric's own entries: an item or gear the game doesn't have yet, a UI icon, …
    The id gets the category's prefix (item., ui.) when it's missing; the subject is the entry's usual override."""
    if cat not in ADD_PREFIX:
        raise ValueError(f"entries can't be added to {cat}")
    eid = (eid or "").strip().lower()
    if not eid:
        eid = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    if not eid.startswith(ADD_PREFIX[cat]):
        eid = ADD_PREFIX[cat] + eid
    if not re.fullmatch(re.escape(ADD_PREFIX[cat]) + r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)*", eid):
        raise ValueError(f"{eid!r}: ids are {ADD_PREFIX[cat]}lowercase_words")
    sp = spec()
    for c, es in entries(sp).items():
        hit = next((x for x in es if x["id"] == eid and not x.get("added")), None)
        if hit:
            raise ValueError(f"{eid} is already in {c} (from the game or the spec)")

    def fn(sp):
        for c in ADD_PREFIX:   # one category per id
            if c != cat and eid in sp.get("added", {}).get(c, {}):
                raise ValueError(f"{eid} was already added to {c}")
        a = {"name": (name or "").strip() or _title(eid)}
        if sub:
            a["sub"] = sub.strip()
        sp.setdefault("added", {}).setdefault(cat, {})[eid] = a
        if subject and subject.strip():
            sp.setdefault("entries", {}).setdefault(f"{cat}/{eid}", {})["subject"] = subject.strip()
    _edit_spec(fn)
    return {"ok": True, "id": eid}


def remove_entry(cat, eid):
    """Take one of Eric's added entries off the list. Its candidates, approval and overrides stay (re-adding the id
    brings them back); export skips approvals without an entry."""
    def fn(sp):
        if eid not in sp.get("added", {}).get(cat, {}):
            raise ValueError(f"{eid} wasn't added in the page (game and spec entries can't be removed here)")
        sp["added"][cat].pop(eid)
        if not sp["added"][cat]:
            sp["added"].pop(cat)
    _edit_spec(fn)
    return {"ok": True}


def move_entries(src, dst, ids=None, dry_run=False):
    """Move entries between categories, keeping everything: candidate folders (and archived ones), the approval,
    the per-entry overrides and an added entry. Default: every entry stored under `src` that now lists in `dst`
    (e.g. items → gear for the equippables). Never overwrites: an id with candidates in both is reported, not moved."""
    import shutil
    listed = {x["id"] for x in entries()[dst]}
    have = {p.name for p in (root() / src).iterdir() if p.is_dir()} if (root() / src).exists() else set()
    sp = spec()
    have |= {k.split("/", 1)[1] for k in sp.get("entries", {}) if k.startswith(f"{src}/")}
    have |= set(state_store().load().get("approved", {}).get(src, {}))
    ids = sorted(i for i in (ids or have) if i in listed and i in have)
    clash = [i for i in ids if (root() / dst / i).exists() and (root() / src / i).exists()]
    ids = [i for i in ids if i not in clash]
    if dry_run:
        return {"would_move": ids, "clash": clash}
    for i in ids:
        for base in (root(), config.data_dir() / "archive" / "limner"):
            s, d = base / src / _safe(i), base / dst / i
            if s.exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(s), str(d))
        for c in store.folders(root() / dst / i):   # candidate meta says which category it was made in
            m = store.read_meta(c)
            if m.get("category") == src:
                store.write_json(c / "meta.json", {**m, "category": dst, "moved_from": src})
    with state_store().edit() as s:
        ap = s.setdefault("approved", {})
        for i in ids:
            if i in ap.get(src, {}):
                ap.setdefault(dst, {})[i] = ap[src].pop(i)

    def fn(sp):
        es = sp.setdefault("entries", {})
        for i in ids:
            if f"{src}/{i}" in es:
                es[f"{dst}/{i}"] = es.pop(f"{src}/{i}")
            if i in sp.get("added", {}).get(src, {}):
                sp["added"].setdefault(dst, {})[i] = sp["added"][src].pop(i)
    _edit_spec(fn)
    return {"moved": ids, "clash": clash}


def doc():
    """Everything the page shows."""
    sp = spec()
    stt = state_store().load()
    approved, reviewed = stt.get("approved", {}), stt.get("reviewed", {})
    cats = {}
    for cat, es in entries(sp).items():
        rows = []
        for e in es:
            ov = override(sp, cat, e["id"])
            cands = []
            for d in store.folders(entry_dir(cat, e["id"]), finished=True):
                m = store.read_meta(d)
                cands.append({"cid": d.name, "seed": m.get("seed"), "made": m.get("made"), "kind": m.get("kind"),
                              "style": m.get("params", {}).get("style"), "prompt": m.get("prompts", {}).get("prompt"),
                              "from": (m.get("inputs") or {}).get("from"), "batch": m.get("batch"), "extra": m.get("extra"),
                              "new": (m.get("made") or 0) > reviewed.get(cat, 0)})
            cands.sort(key=lambda c: c["made"] or 0, reverse=True)
            rows.append({**e, "subject": ov.get("subject") or "", "style": ov.get("style") or "", "seed": ov.get("seed"),
                         "finish": ov.get("finish") or {}, "approved": approved.get(cat, {}).get(e["id"]),
                         "candidates": cands, "prompt": prompt(cat, e["id"], sp=sp)})
            rows[-1]["new"] = sum(1 for c in cands if c["new"] and c["cid"] != rows[-1]["approved"])
        cats[cat] = {**sp["categories"][cat], "entries": rows, "reviewed": reviewed.get(cat, 0),
                     "new": sum(r["new"] for r in rows), "add_prefix": ADD_PREFIX.get(cat)}
    from . import watchdog
    return {"styles": sp["styles"], "categories": cats, "order": list(CATS), "archetypes": sp["archetypes"],
            "races": sp.get("races") or {},
            "export": str(_export_root()), "batch": BATCH, "watchdog": watchdog.status()}


def image(rel):
    p = (root() / rel).resolve()
    if not p.is_relative_to(root().resolve()) or p.suffix != ".png":
        raise ValueError("bad path")
    return p.read_bytes()


# ----------------------------------------------------------------------------- export

def _export_root():
    try:
        return config.project().export("art")
    except Exception:
        return None


def _clear(folder, keep):
    for f in folder.glob("*.png"):
        if f.name not in keep:
            f.unlink()
            Path(f"{f}.meta").unlink(missing_ok=True)


def export(cats=None):
    """Approved icons → Art/Icons/<cat>/<id>.png + index.json (id → file); portraits → Art/Portraits/ (+ thumbs).
    Each folder is rebuilt whole (the PNGs Limner doesn't approve any more are removed)."""
    import shutil
    art = _export_root()
    if art is None:
        raise ValueError("the project has no 'art' export path")
    approved, sp, done = state_store().load().get("approved", {}), spec(), {}
    es_all = entries(sp)
    listed = {c: {e["id"] for e in es} for c, es in es_all.items()}

    def ok_of(cat):
        return {eid: cid for eid, cid in approved.get(cat, {}).items()
                if (eid in listed[cat] or not listed[cat]) and (entry_dir(cat, eid) / cid / "meta.json").exists()}
    cats = list(cats or CATS)
    if any(c in PORTRAIT_CATS for c in cats):   # one folder, one index: always rebuilt from all three together
        cats = [c for c in cats if c not in PORTRAIT_CATS] + ["portraits"]
    for cat in cats:
        ok = ok_of(cat)
        if cat == "portraits":
            folder, index = art / "Portraits", {}
            folder.mkdir(parents=True, exist_ok=True)
            for pc in PORTRAIT_CATS:
                byid = {e["id"]: e for e in es_all[pc]}
                for eid, cid in sorted(ok_of(pc).items()):
                    if eid not in byid:
                        continue
                    d, e = entry_dir(pc, eid) / cid, byid[eid]
                    shutil.copyfile(d / "portrait.png", folder / f"{eid}.png")
                    shutil.copyfile(d / "thumb.png", folder / f"{eid}.thumb.png")
                    row = {"file": f"{eid}.png", "thumb": f"{eid}.thumb.png", "archetype": e["archetype"]}
                    if pc == "portraits":   # a hero portrait: the creator offers these (archetype: one of the five)
                        row.update(race=e.get("race", "human"), gender=e["gender"], look=e["look"])
                    else:                   # archetype: monster | npc (the turn-order bar, hover cards, the shop)
                        row.update(name=e["name"], family=e.get("family", ""), role=e.get("role", ""))
                    index[eid] = row
                    done[pc] = done.get(pc, 0) + 1
            _clear(folder, {f for v in index.values() for f in (v["file"], v["thumb"])})
            store.write_json(folder / "index.json", index)
            continue
        if cat == "tokens":   # Art/Monsters/<id>/token.png + token.n.png (NPCs too), index: id → file, normal, pivot
            folder, index = art / "Monsters", {}
            folder.mkdir(parents=True, exist_ok=True)
            byid = {e["id"]: e for e in es_all["tokens"]}
            for eid, cid in sorted(ok.items()):
                d = entry_dir(cat, eid) / cid
                f = finish_params(cat, eid, sp)
                (folder / eid).mkdir(exist_ok=True)
                shutil.copyfile(d / "token.png", folder / eid / "token.png")
                shutil.copyfile(d / "token.n.png", folder / eid / "token.n.png")
                with Image.open(d / "token.png") as t:
                    w, h = t.size
                index[eid] = {"file": f"{eid}/token.png", "normal": f"{eid}/token.n.png", "size": [w, h],
                              "pivot": [0.5, round(int(f.get("margin", 8)) / h, 4)], "facing": "right",
                              "scale": byid.get(eid, {}).get("scale", 1)}
            for sub in folder.iterdir():   # tokens Limner doesn't approve any more
                if sub.is_dir() and sub.name not in index and (sub / "token.png").exists():
                    shutil.rmtree(sub)
            store.write_json(folder / "index.json", index)
            done[cat] = len(index)
            continue
        else:
            folder, index = art / "Icons" / cat, {}
            folder.mkdir(parents=True, exist_ok=True)
            for eid, cid in sorted(ok.items()):
                shutil.copyfile(entry_dir(cat, eid) / cid / "icon.png", folder / f"{eid}.png")
                index[eid] = f"{eid}.png"
            _clear(folder, set(index.values()))
        store.write_json(folder / "index.json", index)
        done[cat] = len(index)
    return {"exported": done, "to": str(art)}
