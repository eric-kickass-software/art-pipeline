"""The art tools as an MCP server, for agents: `uv run hdart mcp` (stdio).

Task-level tools over the op registry (hdart/ops.py), the same verbs for every tool (`tool` = character, items,
dungeon or limner): state, candidates, view_candidate, generate, approve, archive, get_spec, patch_spec; the shared
jobs_list, jobs_wait, jobs_cancel, export, export_check; and list_ops / describe_op / call_op, which reach every op.

Every call goes to the running Studio over HTTP (hdart/studio_client.py): one job queue, one owner of the state locks.
Nothing here imports a tool to do work. The tools' descriptions are built from the op docstrings (the local registry,
read once at start, so they show even while the Studio is down).

Targets name what a verb works on, per tool:
  items      "<item>"                  a Workshop piece, e.g. "item.iron_helm" (race human)
  character  "<race>.<sex>"            a build's Paint, e.g. "human.male" (also "paint:human.male")
             "hair:<race>", "face:<race>"   a race's hair or face candidates
  dungeon    "<set>/<piece>"           e.g. "crypt/tile.crypt_floor"
  limner     "<cat>/<entry>"           e.g. "items/item.bandage"
Spec targets (get_spec / patch_spec) are listed in those tools' descriptions (SPECS below).
"""
import importlib
import io
import json
from typing import Any, Literal

from . import ops, studio_client as sc

Tool = Literal["character", "items", "dungeon", "limner"]
TOOLS = ("character", "items", "dungeon", "limner")
API_MODULES = ("items_api", "character_api", "dungeon_api", "limner_api", "jobs_api")
MAX_WAIT = 600.0   # seconds jobs_wait may hold one call open


class Refused(Exception):
    """A tool call that can't go ahead (bad target, the Studio refused): its message is shown to the agent."""


# ----------------------------------------------------------------------------- calls and results

def _call(name: str, args: dict | None = None, timeout: float = 120.0):
    try:
        return sc.call(name, {k: v for k, v in (args or {}).items() if v is not None}, timeout)
    except sc.StudioError as e:
        raise Refused(str(e)) from None


def _j(x: Any) -> str:
    """Compact JSON text for a tool result."""
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)


def _image(blob: ops.Blob, max_size: int, pixel: bool = False):
    """An op's image as MCP image content, downscaled to fit max_size px (pixel art: nearest neighbour)."""
    from PIL import Image as PILImage
    from mcp.server.mcpserver import Image
    im = PILImage.open(io.BytesIO(blob.data))
    im.seek(0)   # a GIF: its first frame
    im = im.convert("RGBA")
    w, h = im.size
    if max(w, h) > max_size:
        k = max_size / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))),
                       PILImage.NEAREST if pixel else PILImage.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return Image(data=buf.getvalue(), format="png"), f"{w}x{h} px" + (f", shown at {im.width}x{im.height}" if im.size != (w, h) else "")


def _jobs(result: dict) -> list:
    """The job ids an op that queues work returned ({"job": id} or {"jobs": [ids]})."""
    if not isinstance(result, dict):
        return []
    ids = list(result.get("jobs") or [])
    if result.get("job") is not None:
        ids.append(result["job"])
    return ids


# ----------------------------------------------------------------------------- targets

def _two(target: str, what: str) -> tuple[str, str]:
    a, sep, b = (target or "").partition("/")
    if not (a and sep and b):
        raise Refused(f"target must be {what}, got {target!r}")
    return a, b


def _char(target: str) -> tuple[str, str]:
    """("paint", build) or ("hair" | "face", race)."""
    kind, sep, rest = (target or "").partition(":")
    if not sep:
        kind, rest = "paint", target or ""
    if kind == "paint" and "." in rest:
        return kind, rest
    if kind in ("hair", "face") and rest:
        return kind, rest
    raise Refused(f'character target must be "<race>.<sex>" (Paint), "hair:<race>" or "face:<race>", got {target!r}')


def _item(target: str) -> str:
    if not target:
        raise Refused('items target must be a Workshop piece id, e.g. "item.iron_helm"')
    return target


def _dun_rel(piece: str, candidate: str) -> str:
    """A dungeon candidate as its path in the set folder; a bare id ("s1_123") is completed."""
    if "/" in candidate:
        return candidate
    return f"candidates/{piece}/{candidate if candidate.endswith('.png') else candidate + '.png'}"


def _stem(rel: str) -> str:
    return rel.rsplit("/", 1)[-1].removesuffix(".png")


def _limner_entry(cat: str, entry: str) -> dict:
    doc = _call("limner.doc")
    c = doc["categories"].get(cat)
    if c is None:
        raise Refused(f"no Limner category {cat!r}; categories: {', '.join(doc['categories'])}")
    e = next((e for e in c["entries"] if e["id"] == entry), None)
    if e is None:
        raise Refused(f"no entry {entry!r} in Limner category {cat!r}")
    return e


def _dun_piece_state(set_: str, piece: str) -> tuple[dict, dict]:
    st = _call("dungeon.state", {"set": set_})
    p = next((p for p in st["spec"]["pieces"] if p["id"] == piece), None)
    if p is None:
        raise Refused(f"no piece {piece!r} in set {set_!r}")
    return p, st["state"].get("pieces", {}).get(piece, {})


# ----------------------------------------------------------------------------- the verbs

def state(tool: Tool, target: str = "", full: bool = False) -> str:
    """A compact overview of a tool (full=true: the whole document the page shows, which can be large)."""
    if tool == "items":
        s = _call("items.state", {"race": target or None})
        if full:
            return _j(s)
        return _j({"builds": s["builds"], "pieces": {
            i: {"slot": d.get("slot"), "held_as": d.get("held_as"), "fit": d.get("fit"), "item": d.get("item"),
                "candidates": len(s["candidates"].get(i, [])), "approved": s["approved"].get(i) or {}}
            for i, d in s["spec"]["items"].items()}})
    if tool == "character":
        race = target or "human"
        r, f = _call("character.race", {"race": race}), _call("character.faces", {"race": race})
        paints = {}
        for sex in ("male", "female"):
            try:
                p = _call("character.paint_state", {"build": f"{race}.{sex}"})
                paints[f"{race}.{sex}"] = {"candidates": len(p["candidates"]), "approved": p["approved"]}
            except Refused as e:
                paints[f"{race}.{sex}"] = {"error": str(e)}
        out = {"race": race, "races": sorted(r["spec"]["races"]), "paint": paints,
               "mannequin": {sex: {fc: {"candidates": len(v.get("candidates", [])), "approved": v.get("approved")}
                                   for fc, v in row.items()} for sex, row in r["builds"].items()},
               "hair": {"candidates": len(f["kinds"].get("hair", [])), "set": f["sets"].get("hair", [])},
               "face": {"candidates": len(f["kinds"].get("face", [])), "set": f["sets"].get("face", [])},
               "skin_ramps": sorted(r.get("ramps", {}))}
        return _j({"race": r, "faces": f, "paint": paints} if full else out)
    if tool == "dungeon":
        if not target:
            return _j({"sets": _call("dungeon.sets")})
        st = _call("dungeon.state", {"set": target})
        if full:
            return _j(st)
        ps = st["state"].get("pieces", {})
        return _j({"set": target, "name": st["spec"].get("name"), "families": sorted(st.get("families", {})),
                   "pieces": [{"id": p["id"], "kind": p.get("kind"), "method": p.get("method"),
                               "family": p.get("family"), "candidates": len(ps.get(p["id"], {}).get("candidates", [])),
                               "approved": len(ps.get(p["id"], {}).get("approved", []))} for p in st["spec"]["pieces"]],
                   "orphans": st.get("orphans")})
    if tool == "limner":
        doc = _call("limner.doc")
        if full:
            return _j(doc if not target else doc["categories"].get(target))
        if not target:
            return _j({"styles": sorted(doc["styles"]), "categories": {
                c: {"entries": len(v["entries"]), "approved": sum(1 for e in v["entries"] if e.get("approved")),
                    "candidates": sum(len(e["candidates"]) for e in v["entries"]), "new": v.get("new", 0),
                    "style": v.get("style")} for c, v in doc["categories"].items()}})
        c = doc["categories"].get(target)
        if c is None:
            raise Refused(f"no Limner category {target!r}; categories: {', '.join(doc['categories'])}")
        return _j({"category": target, "style": c.get("style"), "entries": [
            {"id": e["id"], "name": e.get("name"), "approved": e.get("approved"), "candidates": len(e["candidates"]),
             "new": e.get("new", 0)} for e in c["entries"]]})
    raise Refused(f"unknown tool {tool!r}")


def candidates(tool: Tool, target: str) -> str:
    """A target's candidates (newest first), each with its id, whether it's approved, and the views view_candidate
    can show."""
    if tool == "items":
        item = _item(target)
        r = _call("items.candidates", {"item": item})
        keys = {}
        for k, cid in (r.get("approved") or {}).items():
            keys.setdefault(cid, []).append(k)
        return _j({"item": item, "approved": r.get("approved") or {}, "candidates": [
            {"id": c["id"], "seed": c.get("seed"), "made": c.get("made"), "approved_for": keys.get(c["id"], []),
             "edited": c.get("edited"), "views": _item_views(c)} for c in r["candidates"]]})
    if tool == "character":
        kind, key = _char(target)
        if kind == "paint":
            r = _call("character.paint_state", {"build": key})
            cs = sorted(r["candidates"], key=lambda c: -(c.get("meta", {}).get("made") or 0))
            return _j({"build": key, "approved": r["approved"], "candidates": [
                {"id": c["id"], "seed": c.get("meta", {}).get("seed"), "made": c.get("meta", {}).get("made"),
                 "approved": c["id"] == r["approved"], "in_progress": bool(c.get("progress")), "stale": c.get("stale"),
                 "views": _png_views(c)} for c in cs]})
        f = _call("character.faces", {"race": key})
        cs = sorted(f["kinds"].get(kind, []), key=lambda c: -(c.get("meta", {}).get("made") or 0))
        return _j({"race": key, "kind": kind, "set": f["sets"].get(kind, []), "candidates": [
            {"id": c["id"], "seed": c.get("meta", {}).get("seed"), "made": c.get("meta", {}).get("made"),
             "in_set": c.get("in_set"), "in_progress": bool(c.get("progress")), "views": _png_views(c)} for c in cs]})
    if tool == "dungeon":
        set_, piece = _two(target, '"<set>/<piece>"')
        p, ps = _dun_piece_state(set_, piece)
        appr, star = set(ps.get("approved", [])), set(ps.get("starred", []))
        made = lambda r: int(t) if (t := _stem(r).rsplit("_", 1)[-1]).isdigit() else 0   # "s<seed>_<time>"
        rels = sorted(ps.get("candidates", []), key=lambda r: -made(r))
        return _j({"set": set_, "piece": piece, "kind": p.get("kind"), "method": p.get("method"), "candidates": [
            {"id": _stem(r), "rel": r, "approved": r in appr, "starred": r in star} for r in rels],
            "views": ["raw", "native"]})
    if tool == "limner":
        cat, entry = _two(target, '"<cat>/<entry>"')
        e = _limner_entry(cat, entry)
        return _j({"category": cat, "entry": entry, "approved": e.get("approved"), "prompt": e.get("prompt"),
                   "candidates": [{"id": c["cid"], "seed": c.get("seed"), "made": c.get("made"),
                                   "style": c.get("style"), "new": c.get("new"),
                                   "approved": c["cid"] == e.get("approved")} for c in e["candidates"]],
                   "views": ["icon", "portrait", "master", "thumb"]})
    raise Refused(f"unknown tool {tool!r}")


def _item_views(c: dict) -> list:
    out = [k for k, v in (c.get("prev") or {}).items() if v]
    return out + [k for k in ("paint", "paint_back", "held") if c.get(k)]


def _png_views(c: dict) -> list:
    return [f[:-4] for f in c.get("files", []) if f.endswith(".png")]


def view_candidate(tool: Tool, target: str, candidate: str, view: str = "", max_size: int = 384):
    """Look at a candidate: one view as an image, downscaled to fit max_size px."""
    max_size = max(32, min(int(max_size), 1024))
    pixel = False
    if tool == "items":
        item = _item(target)
        c = next((c for c in _call("items.candidates", {"item": item})["candidates"] if c["id"] == candidate), None)
        if c is None:
            raise Refused(f"no candidate {candidate!r} of {item}")
        views = {**{k: v for k, v in (c.get("prev") or {}).items() if v},
                 **{k: c[k] for k in ("paint", "paint_back", "held") if c.get(k)}}
        if not views:
            raise Refused(f"{item} {candidate} has no images yet")
        view = view or next((v for v in ("male_front", "female_front", "held", "paint") if v in views), next(iter(views)))
        if view not in views:
            raise Refused(f"no view {view!r}; views: {', '.join(views)}")
        blob = _call("character.image", {"path": views[view]})
    elif tool == "character":
        kind, key = _char(target)
        if kind == "paint":
            cs = _call("character.paint_state", {"build": key})["candidates"]
        else:
            cs = _call("character.faces", {"race": key})["kinds"].get(kind, [])
        c = next((c for c in cs if c["id"] == candidate), None)
        if c is None:
            raise Refused(f"no {kind} candidate {candidate!r} for {key}")
        views = _png_views(c)
        default = ("rest_front", "figure_front") if kind == "paint" else ("prev_male_front", "head_male", "layer_front")
        view = view or next((v for v in default if v in views), views[0] if views else "")
        if view not in views:
            raise Refused(f"no view {view!r}; views: {', '.join(views) or 'none yet'}")
        blob = _call("character.image", {"path": f"{c['dir']}/{view}.png"})
    elif tool == "dungeon":
        set_, piece = _two(target, '"<set>/<piece>"')
        rel, pixel = _dun_rel(piece, candidate), True
        view = view or "raw"
        if view == "raw":
            blob = _call("dungeon.candidate_image", {"set": set_, "rel": rel})
        elif view == "native":
            blob = _call("dungeon.native_image", {"set": set_, "piece": piece, "rel": rel, "scale": 4})
        else:
            raise Refused('dungeon views: "raw" (as generated) or "native" (the game\'s pixel size, zoomed 4x)')
    elif tool == "limner":
        cat, entry = _two(target, '"<cat>/<entry>"')
        tries = [view] if view else ["icon", "portrait", "master"]   # icons have icon.png, portraits portrait.png
        blob = None
        for v in tries:
            try:
                blob, view = sc.call("limner.image", {"path": f"{cat}/{entry}/{candidate}/{v}.png"}), v
                break
            except sc.StudioError as e:
                if isinstance(e, sc.StudioDown) or e.status != 404:
                    raise Refused(str(e)) from None
        if blob is None:
            raise Refused(f"no {' / '.join(tries)} image for {cat}/{entry} {candidate}")
    else:
        raise Refused(f"unknown tool {tool!r}")
    if not isinstance(blob, ops.Blob):
        raise Refused("the Studio sent JSON where an image was expected")
    img, size = _image(blob, max_size, pixel)
    return [f"{tool} {target} {candidate} · {view} · {size}", img]


def generate(tool: Tool, target: str, n: int = 1, seed: int = 1, options: dict | None = None) -> str:
    """Queue n new candidates (GPU work, behind anything already queued). Returns the job ids; follow with
    jobs_wait, then candidates."""
    opt = dict(options or {})
    if tool == "items":
        name, args = "items.generate", {"item": _item(target), "n": n, "seed": seed}
    elif tool == "character":
        kind, key = _char(target)
        if kind == "paint":
            name, args = "character.generate_paint", {"build": key, "n": n, "seed": seed}
        else:
            name, args = "character.generate_face", {"race": key, "kind": kind, "n": n, "seed": seed}
    elif tool == "dungeon":
        set_, piece = _two(target, '"<set>/<piece>"')
        name, args = "dungeon.generate", {"set": set_, "piece": piece, "n": n, "seed": seed}
    elif tool == "limner":
        cat, entry = _two(target, '"<cat>/<entry>"')
        name, args = "limner.generate", {"cat": cat, "ids": [entry], "n": n, "seed": seed}
    else:
        raise Refused(f"unknown tool {tool!r}")
    r = _call(name, {**args, **opt})
    return _j({"op": name, "jobs": _jobs(r)})


def approve(tool: Tool, target: str, candidate: str, on: bool = True, key: str = "all") -> str:
    """Approve a candidate (on=false: take the approval back). Approved candidates are what export ships."""
    if tool == "items":
        name, args = "items.approve", {"item": _item(target), "candidate": candidate, "key": key, "on": on}
    elif tool == "character":
        kind, k = _char(target)
        if kind == "paint":
            name, args = "character.approve_paint", {"build": k, "candidate": candidate, "on": on}
        else:
            name, args = "character.set_face", {"race": k, "kind": kind, "candidate": candidate, "on": on}
    elif tool == "dungeon":
        set_, piece = _two(target, '"<set>/<piece>"')
        name, args = "dungeon.approve", {"set": set_, "piece": piece, "rel": _dun_rel(piece, candidate), "on": on}
    elif tool == "limner":
        cat, entry = _two(target, '"<cat>/<entry>"')
        name, args = "limner.approve", {"cat": cat, "entry": entry, "candidate": candidate, "on": on}
    else:
        raise Refused(f"unknown tool {tool!r}")
    _call(name, args)
    return _j({"op": name, "approved" if on else "unapproved": candidate})


def archive(tool: Tool, target: str, candidate: str) -> str:
    """Move a candidate to archive/ (nothing is deleted; it stops being approved)."""
    if tool == "items":
        name, args = "items.archive", {"item": _item(target), "candidate": candidate}
    elif tool == "character":
        kind, k = _char(target)
        if kind == "paint":
            name, args = "character.archive_paint", {"build": k, "candidate": candidate}
        else:
            name, args = "character.archive_face", {"race": k, "kind": kind, "candidate": candidate}
    elif tool == "dungeon":
        set_, piece = _two(target, '"<set>/<piece>"')
        name, args = "dungeon.discard", {"set": set_, "piece": piece, "rel": _dun_rel(piece, candidate)}
    elif tool == "limner":
        cat, entry = _two(target, '"<cat>/<entry>"')
        name, args = "limner.archive", {"cat": cat, "entry": entry, "candidate": candidate}
    else:
        raise Refused(f"unknown tool {tool!r}")
    _call(name, args)
    return _j({"op": name, "archived": candidate})


# ----------------------------------------------------------------------------- specs

def merge_patch(doc: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch: objects merge key by key, null removes a key, anything else replaces."""
    if not isinstance(patch, dict):
        return patch
    out = dict(doc) if isinstance(doc, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


PROMPT_KEYS = ("prompt", "prompt_back", "prompt_held", "prompt_held_shield", "prompt_held_focus", "prompt_complete",
               "material_text", "prompt_design", "prompt_held_design")

# Spec targets per tool: (prefix, what it is, read op, save op). How each reads and saves is in _spec().
SPECS = {
    "items": [("item:<item>", "a Workshop piece's definition (or just the item id)", "items.state", "items.save_item"),
              ("prompts", "the shared prompt wording", "items.state", "items.save_prompts")],
    "character": [("paint", "the paint prompts (specs/paint.json)", "character.paint_spec", "character.save_paint_spec"),
                  ("face", "the hair and face prompts (specs/face.json)", "character.faces", "character.save_face_spec"),
                  ("races", "races.json (every race's words, ramps, flags)", "character.race", "character.save_races"),
                  ("template", "the rig template (parts, joints, draw order)", "character.template",
                   "character.save_template")],
    "dungeon": [("set:<set>", "a set's own fields (name, biome, pixel, tile...); or just the set id", "dungeon.state",
                 "dungeon.update_set"),
                ("piece:<set>/<piece>", 'a piece\'s definition; or just "<set>/<piece>"', "dungeon.state",
                 "dungeon.save_piece")],
    "limner": [("entry:<cat>/<entry>", 'an entry\'s settings (subject, style, seed, finish); or "<cat>/<entry>"',
                "limner.doc", "limner.save_entry"),
               ("category:<cat>", "a category's settings (style, prefix, suffix, gen, finish)", "limner.doc",
                "limner.save_category"),
               ("style:<key>", "a style (name, prefix, suffix, lora)", "limner.doc", "limner.save_style")],
}


def _spec_kind(tool: str, target: str) -> tuple[str, str]:
    kind, sep, rest = (target or "").partition(":")
    if sep:
        return kind, rest
    if tool == "items":
        return ("prompts", "") if target == "prompts" else ("item", target)
    if tool == "character":
        return target, ""
    if tool == "dungeon":
        return ("piece" if "/" in target else "set"), target
    if tool == "limner":
        return "entry", target
    raise Refused(f"unknown tool {tool!r}")


def _spec(tool: str, target: str, patch: dict | None = None):
    """Read a spec target; with `patch`, merge it in and save it through the tool's op (which validates it).
    Returns (the spec, the save op's name or None)."""
    kind, key = _spec_kind(tool, target)
    bad = Refused(f"no {tool} spec target {target!r}; targets: " + ", ".join(t for t, *_ in SPECS.get(tool, [])))
    if tool == "items":
        s = _call("items.state")["spec"]
        if kind == "prompts":
            cur = {k: s[k] for k in PROMPT_KEYS if k in s}
            if patch is None:
                return cur, None
            _call("items.save_prompts", {"prompts": patch})
            return {k: v for k, v in _call("items.state")["spec"].items() if k in PROMPT_KEYS}, "items.save_prompts"
        if kind != "item" or not key:
            raise bad
        cur = s["items"].get(key)
        if cur is None and patch is None:
            raise Refused(f"no Workshop piece {key!r} (patch_spec with a full definition creates one)")
        if patch is None:
            return cur, None
        _call("items.save_item", {"item": key, "definition": merge_patch(cur or {}, patch)})
        return _call("items.state")["spec"]["items"].get(key), "items.save_item"
    if tool == "character":
        race = key or "human"
        read = {"paint": ("character.paint_spec", {}), "face": ("character.faces", {"race": race}),
                "races": ("character.race", {"race": race}), "template": ("character.template", {})}
        if kind not in read:
            raise bad
        cur = _call(*read[kind])["spec"]
        if patch is None:
            return cur, None
        save = {"paint": "character.save_paint_spec", "face": "character.save_face_spec",
                "races": "character.save_races", "template": "character.save_template"}[kind]
        args = {"spec": merge_patch(cur, patch), **({"race": race} if kind == "face" else {})}
        _call(save, args)
        return _call(*read[kind])["spec"], save
    if tool == "dungeon":
        if kind == "set" and key and "/" not in key:
            spec = _call("dungeon.state", {"set": key})["spec"]
            cur = {k: v for k, v in spec.items() if k != "pieces"}
            if patch is None:
                return cur, None
            out = _call("dungeon.update_set", {"set": key, "fields": patch})["spec"]
            return {k: v for k, v in out.items() if k != "pieces"}, "dungeon.update_set"
        if kind == "piece":
            set_, piece = _two(key, '"<set>/<piece>"')
            spec = _call("dungeon.state", {"set": set_})["spec"]
            cur = next((p for p in spec["pieces"] if p["id"] == piece), None)
            if cur is None and patch is None:
                raise Refused(f"no piece {piece!r} in set {set_!r}")
            if patch is None:
                return cur, None
            new = merge_patch(cur or {"id": piece}, patch)
            out = _call("dungeon.save_piece", {"set": set_, "piece": new,
                                               "old_id": piece if new.get("id") != piece and cur else None})["spec"]
            return next((p for p in out["pieces"] if p["id"] == new.get("id")), None), "dungeon.save_piece"
        raise bad
    if tool == "limner":
        doc = _call("limner.doc")
        if kind == "entry":
            cat, entry = _two(key, '"<cat>/<entry>"')
            e = _limner_entry(cat, entry)
            cur = {k: e.get(k) for k in ("subject", "style", "seed", "finish")}
            if patch is None:
                return cur, None
            _call("limner.save_entry", {"cat": cat, "entry": entry, "fields": patch})
            e = _limner_entry(cat, entry)
            return {k: e.get(k) for k in ("subject", "style", "seed", "finish")}, "limner.save_entry"
        if kind == "category":
            c = doc["categories"].get(key)
            if c is None:
                raise Refused(f"no Limner category {key!r}")
            pick = lambda c: {k: c.get(k) for k in ("style", "prefix", "suffix", "gen", "finish") if k in c}
            if patch is None:
                return pick(c), None
            _call("limner.save_category", {"cat": key, "fields": patch})
            return pick(_call("limner.doc")["categories"][key]), "limner.save_category"
        if kind == "style":
            if patch is None:
                if key not in doc["styles"]:
                    raise Refused(f"no Limner style {key!r}; styles: {', '.join(doc['styles'])}")
                return doc["styles"][key], None
            _call("limner.save_style", {"key": key, "fields": patch})
            return _call("limner.doc")["styles"].get(key), "limner.save_style"
        raise bad
    raise Refused(f"unknown tool {tool!r}")


def get_spec(tool: Tool, target: str) -> str:
    """Read one part of a tool's spec (the tunable JSON Eric edits in the Studio)."""
    return _j(_spec(tool, target)[0])


def patch_spec(tool: Tool, target: str, patch: dict) -> str:
    """Change one part of a tool's spec with a JSON merge patch (objects merge, null removes a key, anything else
    replaces; lists are replaced whole). The tool's save op validates it: a bad value is refused and nothing is
    saved. Returns the saved spec part."""
    if not isinstance(patch, dict) or not patch:
        raise Refused("patch must be a non-empty JSON object")
    spec, op = _spec(tool, target, patch)
    return _j({"op": op, "spec": spec})


# ----------------------------------------------------------------------------- jobs and export

def jobs_list(active_only: bool = True) -> str:
    """The Studio's job queue: queued and running jobs (active_only=false: plus the newest finished ones)."""
    return _j(_call("jobs.list", {"active_only": active_only}))


def jobs_wait(ids: list[int], timeout: float = 120.0) -> str:
    """Wait until every job in ids has finished, or timeout seconds (at most 600) pass. Returns the jobs and
    "done"; call again to keep waiting (waiting changes nothing)."""
    timeout = max(0.0, min(float(timeout), MAX_WAIT))
    return _j(_call("jobs.wait", {"ids": [int(i) for i in ids], "timeout": timeout}, timeout + 30))


def jobs_cancel(id: int) -> str:
    """Cancel a queued job, or stop the running one (ComfyUI is interrupted)."""
    return _j(_call("jobs.cancel", {"id": id}))


EXPORTS = {"items": "items.export", "character": "character.export", "dungeon": "dungeon.export",
           "limner": "limner.export"}


def export(tool: Tool, target: str = "") -> str:
    """Export a tool's approved art into the game project (each pack folder rebuilt whole). Follow with
    export_check."""
    if tool == "items":
        args = {"items": [target] if target else None}
    elif tool == "character":
        args = {"race": target or "human"}
    elif tool == "dungeon":
        if not target:
            raise Refused("dungeon export needs a set id as target")
        args = {"set": target}
    elif tool == "limner":
        args = {"cats": [target] if target else None}
    else:
        raise Refused(f"unknown tool {tool!r}")
    return _j({"op": EXPORTS[tool], "result": _call(EXPORTS[tool], args, 900)})


def export_check() -> str:
    """Validate what's exported into the game (manifests, files, index maps, index.json, intake files)."""
    return _j(_call("items.export_check"))


# ----------------------------------------------------------------------------- every op

def list_ops(prefix: str = "") -> str:
    """Every op the running Studio serves (name, summary, read_only, queues, returns), e.g. prefix "dungeon."."""
    try:
        got = sc.describe(prefix)
    except sc.StudioError as e:
        raise Refused(str(e)) from None
    return _j([{"name": o["name"], "summary": o["summary"], **({"read_only": True} if o["read_only"] else {}),
                **({"queues": o["queues"]} if o["queues"] else {}), **({"returns": "image"} if o["returns"] == "blob" else {}),
                "params": [p["name"] + ("" if p["required"] else "?") for p in o["params"]]} for o in got])


def describe_op(name: str) -> str:
    """One op in full: its doc and every parameter (type, required, default, description)."""
    try:
        got = [o for o in sc.describe(name) if o["name"] == name]
    except sc.StudioError as e:
        raise Refused(str(e)) from None
    if not got:
        raise Refused(f"unknown op {name!r} (list_ops lists them)")
    return _j(got[0])


def call_op(name: str, args: dict | None = None, max_size: int = 384):
    """Call any op by name with a JSON object of arguments (describe_op shows them). An image op's result comes
    back as an image, downscaled to fit max_size px."""
    try:
        out = sc.call(name, args or {}, 900)
    except sc.StudioError as e:
        raise Refused(str(e)) from None
    if isinstance(out, ops.Blob):
        if not out.content_type.startswith("image/"):
            return f"{name}: {len(out.data)} bytes of {out.content_type}"
        img, size = _image(out, max(32, min(int(max_size), 1024)), name.startswith("dungeon."))
        return [f"{name} · {size}", img]
    return _j(out)


# ----------------------------------------------------------------------------- the server

INSTRUCTIONS = """Hearthdelve's art tools (Character Workshop, Item Workshop, Dungeon Forge, Limner), run by the Studio
at {url}. Every tool here calls the running Studio; if it isn't running, ask Eric to start it (start-art-tools.cmd).

Typical flow per tool: state -> generate (queues GPU work; returns job ids) -> jobs_wait -> candidates ->
view_candidate -> approve or archive -> export -> export_check. get_spec / patch_spec read and change the tunable
specs (validated by the tool). list_ops / describe_op / call_op reach every op when no task tool fits.

ComfyUI (the GPU) is shared with Eric's own work: check jobs_list before queueing much, keep batches small, and only
cancel jobs you queued. Archiving never deletes anything; approvals are what the game gets."""

# verb -> {tool: the op(s) behind it}; their summaries go into the MCP tool's description
VERB_OPS = {
    "state": {"items": ["items.state"], "character": ["character.race", "character.paint_state", "character.faces"],
              "dungeon": ["dungeon.sets", "dungeon.state"], "limner": ["limner.doc"]},
    "candidates": {"items": ["items.candidates"], "character": ["character.paint_state", "character.faces"],
                   "dungeon": ["dungeon.state"], "limner": ["limner.doc"]},
    "view_candidate": {"items": ["character.image"], "character": ["character.image"],
                       "dungeon": ["dungeon.candidate_image", "dungeon.native_image"], "limner": ["limner.image"]},
    "generate": {"items": ["items.generate"], "character": ["character.generate_paint", "character.generate_face"],
                 "dungeon": ["dungeon.generate"], "limner": ["limner.generate"]},
    "approve": {"items": ["items.approve"], "character": ["character.approve_paint", "character.set_face"],
                "dungeon": ["dungeon.approve"], "limner": ["limner.approve"]},
    "archive": {"items": ["items.archive"], "character": ["character.archive_paint", "character.archive_face"],
                "dungeon": ["dungeon.discard"], "limner": ["limner.archive"]},
    "export": {t: [o] for t, o in EXPORTS.items()},
    "export_check": {"items": ["items.export_check"]},
    "jobs_list": {"jobs": ["jobs.list"]}, "jobs_wait": {"jobs": ["jobs.wait"]}, "jobs_cancel": {"jobs": ["jobs.cancel"]},
}

TARGETS = """Targets: items "<item>" (e.g. "item.iron_helm"); character "<race>.<sex>" (Paint, e.g. "human.male"),
"hair:<race>" or "face:<race>"; dungeon "<set>/<piece>" (e.g. "crypt/tile.crypt_floor"); limner "<cat>/<entry>"
(e.g. "items/item.bandage")."""

EXTRA = {
    "state": 'target: items: a race (default human); character: a race (default human); dungeon: a set id ("" lists '
             'the sets); limner: a category ("" = every category).',
    "candidates": TARGETS,
    "view_candidate": TARGETS + ' view ("" = a sensible default): items: "male_front", "female_back", "paint", '
                                '"held"...; character: a PNG in the candidate folder ("rest_front", "figure_back", '
                                '"prev_male_front"...); dungeon: "raw" or "native"; limner: "icon", "portrait", '
                                '"master", "thumb". candidates lists each candidate\'s views.',
    "generate": TARGETS + " options: extra arguments of the op behind it (items: build; character hair/face: sex; "
                          "dungeon: prompt, denoise, lora, variation, draft; limner: style).",
    "approve": TARGETS + ' key (items only): the fit key, "all" or a sex for per-sex pieces. Character hair and '
                         "faces: approving adds the candidate to the race's set (several allowed).",
    "archive": TARGETS,
    "export": 'target: items: one game item id ("" = every item with approved art); character: a race (default '
              'human); dungeon: the set id (required); limner: one category ("" = all).',
}


def _op_summaries(names: list) -> list:
    out = []
    for n in names:
        o = ops.OPS.get(n)
        if o:
            out.append(f"- {n}: {o.summary}")
    return out


def _description(fn, verb: str) -> str:
    lines = [(fn.__doc__ or "").strip()]
    if verb in EXTRA:
        lines += ["", EXTRA[verb]]
    if verb in ("get_spec", "patch_spec"):
        lines += ["", "Spec targets:"] + [f"- {tool} {t}: {what} (saved by {save}: {ops.OPS[save].summary if save in ops.OPS else ''})"
                                          for tool, rows in SPECS.items() for t, what, _, save in rows]
        lines += ['- character targets take an optional race for face / races, e.g. "face:halforc".']
    per = VERB_OPS.get(verb)
    if per:
        lines += ["", "Ops behind it:"]
        for tool, names in per.items():
            lines += _op_summaries(names)
    return "\n".join(lines)


def build_server():
    """The MCP server with every tool registered (descriptions from the local op registry)."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations
    import functools

    for m in API_MODULES:   # docs only: the registry's summaries; no tool code runs here
        importlib.import_module(f"hdart.{m}")
    server = MCPServer("hdart", instructions=INSTRUCTIONS.format(url=sc.url()))

    def add(fn, read_only: bool, destructive: bool = False):
        @functools.wraps(fn)
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except Refused as e:
                raise ToolError(str(e)) from None
        server.add_tool(run, name=fn.__name__, description=_description(fn, fn.__name__), structured_output=False,
                        annotations=ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                                                    openWorldHint=False))

    for fn in (state, candidates, view_candidate, get_spec, jobs_list, jobs_wait, export_check, list_ops, describe_op):
        add(fn, True)
    for fn in (generate, approve, archive, patch_spec, jobs_cancel, export, call_op):
        add(fn, False)
    return server


def main() -> None:
    """Run the MCP server on stdio (what `hdart mcp` and Claude Code start)."""
    build_server().run("stdio")
