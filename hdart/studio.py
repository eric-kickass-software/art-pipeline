"""Sprite Studio: a local web page for working directly with the character sprite kit.

    uv run hdart studio            -> http://127.0.0.1:4321 (opens the browser)

Pick a body, a part per slot and a colour ramp per channel, play every animation row, switch the grid
resolution (32/64/128/256), look at the hero as a lit voxel sheet (three.js), and generate new parts with
FLUX ("Generate part": an edit of the base mannequin, extracted into a modular layer).
"""
import base64
import io
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

from .compose import Kit

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "studio" / "index.html"
RESOLUTIONS = (32, 64, 128, 256)


class Studio:
    def __init__(self, renders, spec, palettes, res=64):
        self.base_renders, self.spec, self.palettes = Path(renders), spec, palettes
        self.lock = threading.Lock()
        self.res = res
        self.kit = None
        self.ensure(res)

    def renders(self, res):
        return self.base_renders if res == 64 else self.base_renders.with_name(f"{self.base_renders.name}_r{res}")

    def ensure(self, res):
        out = self.renders(res)
        if not (out / "manifest.json").exists():
            self.bake(res)
        self.res = res
        self.kit = Kit(out, self.spec, self.palettes)

    kit_set = None  # name of the saved set the kit is baked from (None = every part with templates)

    def bake(self, res):
        from . import sprite_kit, workshop
        import importlib
        importlib.reload(sprite_kit)
        parts, bodies = None, None
        if self.kit_set:
            st = workshop.load_set(self.kit_set)
            if st:
                bodies = st["builds"] or None
                parts = {b: ["body.base"] + [p for p, bs in st["parts"].items() if b in bs] for b in (bodies or [])}
        sprite_kit.bake(self.spec, self.renders(res), res=res, parts=parts, bodies=bodies)

    def use_set(self, name):
        self.kit_set = name or None
        for r in RESOLUTIONS:
            (self.renders(r) / "manifest.json").unlink(missing_ok=True)
        self.rebuild()

    def rebuild(self, res=None):
        with self.lock:
            res = res or self.res
            self.bake(res)
            self.res = res
            self.kit = Kit(self.renders(res), self.spec, self.palettes)

    def generate(self, part, prompt, body, seed):
        from . import ai_kit
        import importlib
        importlib.reload(ai_kit)
        ai_kit.make_part(part, prompt, body, seed=seed)
        lib = json.loads((ROOT / "specs" / "ai_parts.json").read_text(encoding="utf-8"))
        lib["parts"][part] = {"prompt": prompt, "seed": seed}
        (ROOT / "specs" / "ai_parts.json").write_text(json.dumps(lib, indent=2) + "\n", encoding="utf-8")
        for r in RESOLUTIONS:  # other resolutions re-bake when next selected
            if r != self.res:
                (self.renders(r) / "manifest.json").unlink(missing_ok=True)
        self.rebuild()

    def meta(self):
        k = self.kit
        slots = []
        for s in k.spec["slots"]:
            slots.append({"name": s["name"], "required": s["required"],
                          "parts": {b: k.parts_for(s["name"], b) for b in k.manifest["bodies"]}})
        channels = []
        for c in k.spec["channels"]:
            if c["name"] == "none":
                continue
            opts = k.pal.options(c["name"]) if c.get("customizable") else [c["default"]]
            channels.append({"name": c["name"], "default": c["default"], "options": opts,
                             "swatch": {o: "#%02x%02x%02x" % tuple(int(v) for v in k.pal.ramps[o][2]) for o in opts
                                        if o in k.pal.ramps}})
        return {"bodies": k.manifest["bodies"], "slots": slots, "channels": channels, "views": k.manifest["views"],
                "res": self.res, "resolutions": RESOLUTIONS, "set": self.kit_set}

    def sheet(self, app, view):
        with self.lock:
            color, normal, depth = self.kit.compose_full(app, view)
        d = depth.clip(0, 255).astype(np.uint8)
        return {"color": png_url(color), "depth": png_url(np.dstack([d, d, d, np.full_like(d, 255)])),
                "view": self.kit.manifest["views"][view], "res": self.res}


class Jobs:
    """Sequential background queue for generation (ComfyUI runs one job at a time anyway)."""

    def __init__(self):
        self.items, self.lock, self.n = [], threading.Lock(), 0
        threading.Thread(target=self.run, daemon=True).start()

    def add(self, kind, args, label):
        with self.lock:
            self.n += 1
            job = {"id": self.n, "kind": kind, "args": args, "label": label, "status": "queued", "error": None,
                   "result": None}
            self.items.append(job)
        return job

    def snapshot(self):
        with self.lock:
            return [{k: v for k, v in j.items() if k != "args"} for j in self.items[-60:]]

    def clear_done(self):
        with self.lock:
            self.items = [j for j in self.items if j["status"] in ("queued", "running")]

    def run(self):
        import time as _t
        from . import workshop as ws
        while True:
            job = None
            with self.lock:
                job = next((j for j in self.items if j["status"] == "queued"), None)
                if job:
                    job["status"] = "running"
            if not job:
                _t.sleep(0.4)
                continue
            a = job["args"]
            try:
                import importlib
                from . import ai_kit, comfy, dungeon as dg
                for mod in (comfy, ai_kit, ws, dg):  # pick up code changes without restarting the Studio
                    importlib.reload(mod)
                if job["kind"] == "dun":
                    job["result"] = dg.generate(a["set"], a["piece"], int(a.get("n", 3)), int(a.get("seed", 1)),
                                                float(a["denoise"]) if a.get("denoise") not in (None, "") else None,
                                                a.get("prompt") or None,
                                                float(a["lora"]) if a.get("lora") not in (None, "") else None,
                                                a.get("variation"))
                elif job["kind"] == "dun_trial":
                    job["result"] = dg.trial(a["set"], a["piece"], a["rel"], a.get("variation", ""), int(a.get("seed", 1)))
                elif job["kind"] == "dun_edit":
                    job["result"] = dg.edit_candidate(a["set"], a["piece"], a["rel"], a.get("prompt", ""),
                                                      float(a.get("denoise", 0.5)), int(a.get("seed", 1)))
                elif job["kind"] == "dun_rescale":
                    job["result"] = dg.rescale_master(a["set"], a["piece"], a["rel"], float(a["factor"]),
                                                      bool(a.get("redetail")), int(a.get("seed", 1)))
                elif job["kind"] == "dun_capture":
                    job["result"] = dg.capture(a["set"])
                elif job["kind"] == "base":
                    job["result"] = ws.gen_base(a["build"], a["facing"], int(a.get("n", 4)), int(a.get("seed", 1)),
                                                a.get("description"))
                elif job["kind"] == "part":
                    job["result"] = ws.gen_part(a["part"], a["build"], a["facing"], a["text"], int(a.get("n", 4)),
                                                int(a.get("seed", 5)), float(a.get("lora", 0.6)))
                elif job["kind"] == "fix":
                    job["result"] = ws.fix_region(a["target"], a["build"], a["facing"], a["image"], a["mask"],
                                                  a["prompt"], int(a.get("seed", 7)), a.get("part"))
                job["status"] = "done"
            except Exception as e:
                job["status"], job["error"] = "failed", str(e)


JOBS = None


def workshop_post(st, action, b):
    """Workshop actions (see hdart/workshop.py). Long ones (generation) block until ComfyUI is done."""
    from . import ai_kit, comfy, workshop as ws
    import importlib
    for mod in (comfy, ai_kit, ws):
        importlib.reload(mod)
    if action == "style":
        return {"style": ws.save_style(b.get("groups"), b.get("base"), b.get("builds"))}
    if action == "base/generate":
        return {"candidates": ws.gen_base(b["build"], b["facing"], int(b.get("n", 4)), int(b.get("seed", 1)),
                                          b.get("description"))}
    if action == "base/approve":
        ws.approve_base(b["build"], b["facing"], b["file"])
        return {"ok": True}
    if action == "part/generate":
        return {"candidates": ws.gen_part(b["part"], b["build"], b["facing"], b["text"], int(b.get("n", 4)),
                                          int(b.get("seed", 5)), float(b.get("lora", 0.6)))}
    if action == "part/approve_raw":
        out = ws.approve_raw(b["part"], b["build"], b["facing"], b["file"])
        if b["facing"] == "front" and b.get("auto_back", True):  # queue the matching back view right away
            s = ws.load_state()
            pb = s["parts"][b["part"]]["builds"][b["build"]]
            if not pb["back"].get("candidates"):
                JOBS.add("part", {"part": b["part"], "build": b["build"], "facing": "back",
                                  "text": s["parts"][b["part"]]["text"], "n": b.get("n", 3), "seed": 5},
                         f"{b['part']} · {b['build']} · back")
        return out
    if action == "jobs":
        added = [JOBS.add(j["kind"], j, j.get("label", j["kind"]))["id"] for j in b["jobs"]]
        return {"added": added}
    if action == "jobs/clear":
        JOBS.clear_done()
        return {"ok": True}
    if action == "board":
        return ws.board()
    if action == "cut/data":
        return ws.cut_data(b["part"], b["build"], b["facing"])
    if action == "cut/save":
        return ws.save_edits(b["part"], b["build"], b["facing"], b["edits"], int(b.get("res", 64)))
    if action == "part/cut":
        return ws.cut(b["part"], b["build"], b["facing"], b.get("thresh"), b.get("speck"), int(b.get("res", 64)))
    if action == "part/approve":
        ws.approve_cut(b["part"], b["build"])
        st.rebuild()
        return {"ok": True}
    if action == "part/reject":
        ws.reject(b["part"], b["build"])
        st.rebuild()
        return {"ok": True}
    if action == "set/save":
        return ws.save_set(b["name"], bool(b.get("include_unreviewed")))
    if action == "import":
        return {"state": ws.import_existing(b.get("build", "slim"))}
    raise ValueError(f"unknown action {action}")


def dungeon_post(action, b):
    """Studio > Dungeon actions (hdart/dungeon.py). Generation and Unity capture go through the job queue."""
    from . import dungeon as dg
    import importlib
    importlib.reload(dg)
    name = b.get("set")
    if action == "style":
        return {"style": dg.save_style(b.get("groups"), b.get("prop_view"), b.get("prop_framing"), b.get("prop_medium"))}
    if action == "set/new":
        return {"spec": dg.new_set(b["id"], b.get("name"), b.get("biome", ""), b.get("from", "crypt"))}
    if action == "set/update":
        return {"spec": dg.update_set(name, b["fields"])}
    if action == "piece/save":
        return {"spec": dg.upsert_piece(name, b["piece"], b.get("old_id"))}
    if action == "piece/delete":
        return {"spec": dg.delete_piece(name, b["piece"])}
    if action == "generate":
        job = JOBS.add("dun", b, f"{name} · {b['piece']} ×{b.get('n', 3)}")
        return {"job": job["id"]}
    if action == "approve":
        return {"piece": dg.approve(name, b["piece"], b["rel"], bool(b.get("on", True)))}
    if action == "discard":
        return {"piece": dg.discard(name, b["piece"], b["rel"])}
    if action == "star":
        return {"piece": dg.star(name, b["piece"], b["rel"], bool(b.get("on", True)))}
    if action == "promote":
        return {"piece": dg.promote(name, b["piece"], b["rel"])}
    if action == "edit":
        job = JOBS.add("dun_edit", b, f"{name} · {b['piece']} edit: {b.get('prompt', '')[:30]}")
        return {"job": job["id"]}
    if action == "rescale":
        job = JOBS.add("dun_rescale", b, f"{name} · {b['piece']} ×{b['factor']}")
        return {"job": job["id"]}
    if action == "trial":
        job = JOBS.add("dun_trial", b, f"{name} · {b['piece']} trial: {b.get('variation', '')[:30]}")
        return {"job": job["id"]}
    if action == "family/generate":  # variations x sizes x n
        jobs = dg.variant_jobs(name, b["piece"], b.get("variations"), b.get("sizes"), int(b.get("n", 1)))
        ids = [JOBS.add("dun", j, f"{name} · {j['piece']} · {j['variation']}")["id"] for j in jobs]
        return {"jobs": ids, "spec": dg.load_set(name)}
    if action == "archive_unapproved":
        ps, n = dg.archive_unapproved(name, b["piece"])
        return {"piece": ps, "count": n}
    if action == "piece/restore":
        return {"spec": dg.restore_piece(name, b["piece"])}
    if action == "orphan/archive":
        dg.archive_orphan(name, b["piece"])
        return {"ok": True}
    if action == "set/archive":
        return {"path": str(dg.archive_set(name)), "sets": dg.list_sets()}
    if action == "export":
        return {"path": str(dg.export(name))}
    if action == "capture":
        job = JOBS.add("dun_capture", b, f"{name} · Prototype V capture")
        return {"job": job["id"]}
    raise ValueError(f"unknown action {action}")


def dungeon_get(path, q):
    """-> (content type, bytes) for the Dungeon page's images and state."""
    from . import dungeon as dg
    g = lambda k, d=None: q.get(k, [d])[0]
    if path in ("state", "room"):  # pick up dungeon.py edits without restarting
        import importlib
        importlib.reload(dg)
    name = g("set", "crypt")
    if path == "state":
        spec = dg.load_set(name)
        return "application/json", json.dumps({"sets": dg.list_sets(), "spec": spec, "state": dg.state(name),
                                               "orphans": dg.orphans(name),
                                               "output_tile": dg.output_tile(spec), "pixel_steps": dg.pixel_steps(spec),
                                               "families": {p["id"]: {"settings": dg.family_settings(p),
                                                                      "pieces": {n: q["id"] for n, q in dg.family_pieces(spec, p["id"]).items()},
                                                                      "material": dg.material_prompt(name, p)}
                                                            for p in spec["pieces"] if p.get("method") == "master"},
                                               "style": dg.style(), "set_style": dg.set_style(spec)["groups"]}).encode()
    if path == "sets":
        return "application/json", json.dumps(dg.list_sets()).encode()
    if path == "family_layout":  # live preview: which sprite goes where
        return "application/json", json.dumps(dg.family_layout(name, g("piece"), int(g("seed", 0)),
                                                               int(g("w", 16)), int(g("h", 12)))).encode()
    if path == "cell":  # one cut sprite (cached)
        return "image/png", dg.cell_png(name, g("piece"), g("rel"), g("suf", ""))
    if path == "family":  # planner-laid floor from a family (iso or top-down, optional block outlines)
        return "image/png", dg.png_bytes(dg.family_preview(name, g("piece"), int(g("seed", 0)), g("outline") == "1",
                                                           g("view", "iso")))
    if path == "seam":  # master / repaint candidate among master tiles
        return "image/png", dg.png_bytes(dg.seam_preview(name, g("piece"), g("rel")))
    if path == "img":  # raw 8x candidate
        return "image/png", dg.candidate_path(name, g("rel")).read_bytes()
    if path == "native":
        im = dg.native(name, g("piece"), g("rel"), int(g("tile", 0)) or None)
        return "image/png", dg.png_bytes(im, int(g("scale", 1)))
    if path == "room":
        inc = {g("piece"): g("rel")} if g("piece") and g("rel") else None
        im = dg.room_preview(name, int(g("tile", 0)) or None, int(g("scale", 2)), inc)
        return "image/png", dg.png_bytes(im)
    if path == "capture":
        f = (dg.CAPTURES / g("f")).resolve()
        if dg.CAPTURES.resolve() not in f.parents or f.suffix != ".png":
            raise ValueError("not a capture")
        return "image/png", f.read_bytes()
    if path == "captures":
        return "application/json", json.dumps(sorted(p.name for p in dg.CAPTURES.glob("v0*.png"))).encode()
    raise ValueError(f"unknown {path}")


def png_url(arr):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def serve(renders, spec, palettes, port=4321, open_browser=True):
    global JOBS
    st = Studio(renders, spec, palettes)
    JOBS = Jobs()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html", "/workshop", "/dungeon"):
                    page = {"/workshop": "workshop.html", "/dungeon": "dungeon.html"}.get(u.path, PAGE.name)
                    body = PAGE.with_name(page).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path.startswith("/api/dun/"):
                    ctype, data = dungeon_get(u.path[9:], q)
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store" if ctype != "image/png" or "room" in u.path
                                     or "capture" in u.path else "max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                elif u.path == "/api/ws/img":
                    from . import workshop
                    data = workshop.image(q["path"][0])
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                elif u.path == "/api/ws/state":
                    from . import workshop
                    self._json({"state": workshop.load_state(), "style": workshop.style(),
                                "library": json.loads((ROOT / "specs" / "ai_parts.json").read_text(encoding="utf-8"))["parts"],
                                "sets": workshop.list_sets(), "slots": [s["name"] for s in st.kit.spec["slots"]]})
                elif u.path == "/api/ws/jobs":
                    self._json({"jobs": JOBS.snapshot()})
                elif u.path == "/api/characters":
                    from . import workshop
                    self._json({"characters": workshop.list_characters(), "sets": workshop.list_sets(), "set": st.kit_set})
                elif u.path == "/api/meta":
                    self._json(st.meta())
                elif u.path == "/api/sheet":
                    self._json(st.sheet(json.loads(q["app"][0]), q.get("view", ["battle"])[0]))
                else:
                    self.send_error(404)
            except Exception as e:  # keep the studio alive and show the error in the page
                self._json({"error": repr(e)}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads((self.rfile.read(n) or b"{}").decode("utf-8", errors="replace"))
            try:
                if u.path == "/api/rebuild":
                    st.rebuild(int(q["res"][0]) if "res" in q else None)
                    self._json({"ok": True, "res": st.res})
                elif u.path == "/api/resolution":
                    with st.lock:
                        st.ensure(int(q["res"][0]))
                    self._json({"ok": True, "res": st.res})
                elif u.path == "/api/generate":
                    st.generate(body["part"], body["prompt"], body.get("body", "slim"), int(body.get("seed", 5)))
                    self._json({"ok": True})
                elif u.path.startswith("/api/ws/"):
                    self._json(workshop_post(st, u.path[8:], body))
                elif u.path.startswith("/api/dun/"):
                    self._json(dungeon_post(u.path[9:], body))
                elif u.path == "/api/characters/save":
                    from . import workshop
                    self._json(workshop.save_character(body["name"], body["appearance"], body.get("note", "")))
                elif u.path == "/api/characters/delete":
                    from . import workshop
                    workshop.delete_character(body["name"])
                    self._json({"ok": True})
                elif u.path == "/api/set/use":
                    st.use_set(body.get("name"))
                    self._json({"ok": True, "set": st.kit_set})
                else:
                    self.send_error(404)
            except Exception as e:
                self._json({"error": repr(e)}, 500)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}"
    print(f"Sprite Studio: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    srv.serve_forever()
