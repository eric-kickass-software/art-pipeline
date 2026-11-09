"""The ops of the Dungeon Forge, Limner, Character Workshop and job queue (hdart/<tool>_api.py, hdart/core/jobs_api.py):
every op documented and queueing a real job kind, every route the pages call mapped onto an op, a few direct calls
on temp data (queued job args, renamed legacy arguments, refusals), and the Studio's HTTP surface end to end on a
throwaway server (GET reads, Blob images, aliases, structured errors). No GPU.
Run: uv run python tests/test_tool_ops.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import json, os, re, socket, tempfile, threading, time
import urllib.error, urllib.request
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
game, data = tmp / "game", tmp / "data"
os.environ.update(HD_DATA=str(data), HD_PROJECT=str(game), HD_COMFY_URL="http://127.0.0.1:9")
(game / "ArtContract").mkdir(parents=True)
(game / "ArtContract" / "requirements.json").write_text(json.dumps({"damageTypes": ["fire"], "wanted": {"icons": {
    "items": [{"id": "item.bandage", "name": "Bandage", "kind": "consumable"}]}}}), encoding="utf-8")

from hdart.core import jobs, ops
from hdart.server import studio
for m in studio.JOB_MODULES + studio.OP_MODULE_NAMES:
    __import__(m)
from hdart.tools.character import character_api
from hdart.tools.dungeon import dungeon_api
from hdart.core import jobs_api
from hdart.tools.limner import limner_api

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------- every op: documented, real job kinds

for prefix in ("dungeon.", "limner.", "character.", "jobs.", "studio."):
    got = ops.describe(prefix)
    assert len(got) >= {"dungeon.": 50, "limner.": 18, "character.": 35, "jobs.": 7, "studio.": 2}[prefix], (prefix, len(got))
    for o in got:
        assert o["summary"] and o["summary"][0].isupper() and o["summary"].rstrip()[-1] in ".)}", o["name"]
        assert o["queues"] is None or o["queues"] in jobs.KINDS, (o["name"], o["queues"])
        for p in o["params"]:
            assert p["type"] != "any", (o["name"], p["name"])
            assert p["doc"], (o["name"], p["name"], "undocumented parameter")
# every op is named "<tool>.<action>"
assert all("." in n for n in ops.OPS), [n for n in ops.OPS if "." not in n]
# every op (all tools) is a typed public function: a docstring, every parameter annotated and documented, a return type
import inspect
for n, o in ops.OPS.items():
    assert o.summary and inspect.signature(o.fn).return_annotation is not inspect.Signature.empty, (n, "needs a return type")
    assert all(p.type is not object and (p.doc or ops._shared_doc(n, p.name)) for p in o.params), (n, "untyped or undocumented parameter")


# ----------------------------------------------------------------------------- the pages call ops

def tables(*names):
    out = {}
    for t in names:
        for k, v in t.items():
            assert callable(v) or v[0] in ops.OPS, (k, v)
        out.update(t)
    return set(out)


# the older routes stay for scripts and agents; each still names a real op
tables(character_api.LEGACY, character_api.GET_LEGACY, jobs_api.LEGACY, jobs_api.GET_LEGACY, dungeon_api.LEGACY,
       dungeon_api.GET_LEGACY, dungeon_api.LIB_LEGACY, dungeon_api.LIB_GET_LEGACY, limner_api.LEGACY, limner_api.GET_LEGACY)

# Static scan of the page modules (studio/js): every op they call exists, GETs and image URLs only name read-only ops,
# and an inline object of arguments names only the op's parameters (ops refuse unknown ones). The scoped helpers add an
# argument: itGet / itPost the page's race (Item Workshop), dget / dpost the open set (Dungeon Forge).
STUDIO = ROOT / "studio"
CALL = re.compile(r'\b(get|post|url|itGet|itPost|dget|dpost)\(\s*"(\w+\.\w+)"\s*(,\s*)?')   # op names: "<tool>.<action>"
ADDED = {"itGet": {"race"}, "itPost": {"race"}, "dget": {"set"}, "dpost": {"set"}}


def object_keys(src, i):
    """Top-level keys of the object literal starting at src[i] == "{" (None if a key is computed or spread)."""
    depth, j, keys, part, quote = 0, i, [], "", None
    while True:
        c = src[j]
        if quote:
            if c == "\\":
                part += src[j:j + 2]; j += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
            if depth == 0:
                keys.append(part); break
        elif c == "," and depth == 1:
            keys.append(part); part = ""; j += 1; continue
        if depth >= 1 and not (depth == 1 and c == "{" and j == i):
            part += c
        j += 1
    out = set()
    for k in (x.strip() for x in keys):
        if not k:
            continue
        if k.startswith("..."):
            continue   # a spread: its keys are checked where it is built, or at run time
        m = re.match(r'^(\w+)\s*(:|$)', k)
        if not m:
            return None
        out.add(m.group(1))
    return out


modules = sorted((STUDIO / "js").rglob("*.js"))
assert len(modules) >= 20, modules
called, checked = set(), 0
for f in modules:
    src = f.read_text(encoding="utf-8")
    rel = f.relative_to(STUDIO).as_posix()
    assert rel == "js/api.js" or "/api/" not in src, (rel, "pages call ops through js/api.js, not routes")
    for m in CALL.finditer(src):
        fn, name = m.group(1), m.group(2)
        assert name in ops.OPS, (rel, fn, name, "no such op")
        called.add(name)
        if fn in ("get", "url", "itGet", "dget"):
            assert ops.OPS[name].read_only, (rel, fn, name, "GET of an op that changes things")
        if m.group(3) and src[m.end()] == "{":
            keys = object_keys(src, m.end())
            if keys is not None:
                allowed = {p.name for p in ops.OPS[name].params}
                assert keys - ADDED.get(fn, set()) <= allowed and not (ADDED.get(fn, set()) - allowed), (rel, name, sorted(keys - allowed))
                checked += 1
assert len(called) >= 100 and checked >= 100, (len(called), checked)
for page in ("workshop", "items", "editor", "dungeon", "limner"):   # no inline scripts: each page loads its module
    html = (STUDIO / f"{page}.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == [f'<script type="module" src="/js/{"workshop/" if page in ("workshop", "items", "editor") else ""}'
                                                   f'{ {"workshop": "character", "dungeon": "dungeon/main"}.get(page, page) }.js">'], page
    assert "/api/" not in html, page


# ----------------------------------------------------------------------------- direct calls (no Studio)

def refused(fn, status, has=""):
    try:
        fn()
    except ops.OpError as e:
        assert e.status == status and has in str(e), (e.status, str(e))
        return
    raise AssertionError("not refused")


# studio.*: this process isn't supervised, so a restart is refused (tests/test_supervisor.py covers the real one)
assert ops.call("studio.info")["pid"] == os.getpid() and ops.call("studio.info")["restart_pending"] is None
refused(lambda: ops.call("studio.restart", {}), 409)

# Data and specs are temp folders through HD_DATA / HD_SPECS
from hdart.tools.dungeon import sets as dsets
from hdart.tools.limner import limner as L
assert str(dsets.sets_dir()).startswith(str(fixture_specs.SPECS)) and str(dsets.dun_dir()).startswith(str(data))
assert str(L.spec_path()).startswith(str(fixture_specs.SPECS))

assert "crypt" in ops.call("dungeon.sets")
spec = ops.call_alias(dungeon_api.LEGACY, "set/new", {"set": "crypt", "id": "zz", "biome": "test", "from": "crypt"})["spec"]
assert spec["id"] == "zz" if "id" in spec else True
st = ops.call_alias(dungeon_api.GET_LEGACY, "state", {"set": ["zz"]}, query=True)
assert set(st) >= {"sets", "spec", "state", "orphans", "sheet", "families", "style"} and "zz" in st["sets"], list(st)
refused(lambda: ops.call_query("dungeon.approve", {"set": "zz"}), 405)
refused(lambda: ops.call_query("dungeon.family_layout", {"set": "zz", "piece": "x", "w": "wide"}), 400, "w must be integer")

jobs.QUEUE = jobs.Jobs(None, autostart=False)
piece = next(p["id"] for p in spec["pieces"])
j = jobs.QUEUE.get(ops.call_alias(dungeon_api.LEGACY, "generate", {"set": "zz", "piece": piece, "n": 2, "seed": 5,
                                                                    "denoise": "", "lora": "0.8"})["job"])
assert j["kind"] == "dun" and j["args"] == {"set": "zz", "piece": piece, "n": 2, "seed": 5, "lora": 0.8}, j["args"]
assert j["label"] == f"zz · {piece} ×2", j["label"]
j = jobs.QUEUE.get(ops.call("dungeon.rescale", {"set": "zz", "piece": piece, "rel": "r.png", "fx": 1.5, "fy": 1})["job"])
assert "fx" in j["args"] and "factor" not in j["args"] and "fit" not in j["args"] and j["label"].endswith("×1.5 wide ×1.0 tall")
refused(lambda: ops.call("dungeon.rescale", {"set": "zz", "piece": piece, "rel": "r.png"}), 400, "give factor")
refused(lambda: ops.call("dungeon.capture_image", {"f": "../state.json"}), 400, "not a capture")
assert isinstance(ops.call("dungeon.patterns"), dict)
assert ops.get("dungeon.cell_image").describe()["returns"] == "blob" and ops.get("dungeon.state").describe()["returns"] == "json"

# limner: planning reads, generation queues one job per entry, the page's id/cid arrive renamed
doc = ops.call("limner.doc")
assert "item.bandage" in [e["id"] for e in doc["categories"]["items"]["entries"]]
est = ops.call_alias(limner_api.LEGACY, "batch/plan", {"cat": "items", "ids": ["item.bandage"], "variants": ["", "x"],
                                                       "n": 2, "seeds": "sweep", "seed": 3})["estimate"]
assert est["jobs"] == 2 and est["images"] == 4, est
n0 = len(jobs.QUEUE.items)
r = ops.call_alias(limner_api.LEGACY, "generate", {"cat": "items", "ids": ["item.bandage", "item.bandage"], "n": "2"})
assert len(r["jobs"]) == 2 and len(jobs.QUEUE.items) == n0 + 2
assert jobs.QUEUE.get(r["jobs"][0])["args"] == {"cat": "items", "id": "item.bandage", "n": 2, "seed": None, "style": None}
(L.root() / "items" / "item.bandage" / "s1_1").mkdir(parents=True)
assert ops.call_alias(limner_api.LEGACY, "approve", {"cat": "items", "id": "item.bandage", "cid": "s1_1"}) == {"ok": True}
assert ops.call("limner.doc")["categories"]["items"]["entries"][0]["approved"] == "s1_1"
jobs.QUEUE.add("limner_gen", {}, "Limner batch b1 · items · x"); jobs.QUEUE.add("limner_gen", {}, "Limner batch b2 · items · x")
assert ops.call("limner.cancel_batch", {"batch": "b1"}) == {"cancelled": 1}

# character: the auto-queued back view moved out of the Studio into character.approve_mannequin
from hdart.tools.character import workshop as W
with W.edit_state() as s:
    s.setdefault("builds", {})["human.unisex"] = {"front": {"candidates": ["f.png"]}, "back": {"approved": "b.png"}}
r = ops.call_alias(character_api.LEGACY, "race/approve", {"build": "human.unisex", "facing": "front", "file": "f.png"})
assert r["front"]["approved"] == "f.png" and "approved" not in W.load_state()["builds"]["human.unisex"]["back"]
j = jobs.QUEUE.get(r["back_job"])
assert j["kind"] == "race" and j["args"] == {"race": "human", "sex": "unisex", "facing": "back", "n": 3, "seed": 11}
assert j["label"] == "human · unisex · back"
assert "back_job" not in ops.call("character.approve_mannequin", {"build": "human.unisex", "facing": "back", "file": "b.png"})
pal = ops.call_alias(character_api.LEGACY, "ramp/palettes", {})
size = pal.get("rampSize", 5)
pal = ops.call("character.save_ramp", {"group": "skin", "name": "zz_test", "colors": ["#000000"] * size})
assert "zz_test" in pal["groups"]["skin"]
assert "zz_test" not in ops.call("character.delete_ramp", {"group": "skin", "name": "zz_test"})["groups"]["skin"]
assert set(ops.call_alias(character_api.LEGACY, "paint/spec", {})) == {"spec"}
j = jobs.QUEUE.get(ops.call("character.generate_paint", {"build": "human.male", "n": 2})["job"])
assert j["kind"] == "paint" and j["args"] == {"build": "human.male", "n": 2, "seed": 1} and j["label"] == "human · male · paint"
refused(lambda: ops.call("character.generate_paint", {"build": "human"}), 400, "<race>.<sex>")

# jobs: queue (the page's raw form), wait, cancel, refusals
r = ops.call_alias(jobs_api.LEGACY, "jobs", {"jobs": [{"kind": "tpl_measure", "build": "human.unisex", "label": "t"}]})
jid = r["added"][0]
assert jobs.QUEUE.get(jid)["args"]["build"] == "human.unisex" and jobs.QUEUE.get(jid)["label"] == "t"
refused(lambda: ops.call("jobs.queue", {"kind": "nope"}), 400, "unknown job kind")
w = ops.call("jobs.wait", {"id": jid, "timeout": 0})
assert w["done"] is False and w["jobs"][0]["status"] == "queued" and "args" not in w["jobs"][0]
assert ops.call("jobs.cancel", {"id": jid}) == {"status": "cancelled"}
assert ops.call("jobs.wait", {"ids": [jid], "timeout": 5})["done"] is True
refused(lambda: ops.call("jobs.get", {"id": 99999}), 404)
refused(lambda: ops.call("jobs.wait", {}), 400, "give id or ids")
assert all(j["status"] in ("queued", "running") for j in ops.call("jobs.list", {"active_only": True})["jobs"])
jobs.QUEUE = None

# errors as the Studio sends them
assert ops.http_error(ops.OpError("x", 404)) == (404, {"error": "x", "type": "OpError"})
assert ops.http_error(KeyError("k"))[0] == 404 and ops.http_error(ValueError("v"))[0] == 400
assert ops.http_error(RuntimeError("r")) == (500, {"error": "RuntimeError('r')", "type": "RuntimeError"})


# ----------------------------------------------------------------------------- the Studio over HTTP

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
threading.Thread(target=studio.serve, kwargs={"port": port, "open_browser": False}, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def http(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


for _ in range(100):
    try:
        if http("/api/ws/jobs")[0] == 200:
            break
    except OSError:
        time.sleep(0.1)
code, ctype, body = http("/api/ops?prefix=dungeon.")
assert code == 200 and len(json.loads(body)["ops"]) >= 50
for page in ("/workshop", "/items", "/editor", "/dungeon", "/limner"):
    assert http(page)[:2] == (200, "text/html; charset=utf-8"), page
for f in [*modules, *(STUDIO / "css").glob("*.css")]:   # every page module and stylesheet is served
    code, ctype, body = http("/" + f.relative_to(STUDIO).as_posix())
    assert code == 200 and ctype.startswith("text/javascript" if f.suffix == ".js" else "text/css") and body == f.read_bytes(), f
for bad in ("/js/../../config.toml", "/js/nope.js", "/css/../workshop.html"):
    assert http(bad)[0] == 404, bad
assert http("/api/op/character.image?path=t.png&_=123")[0] in (200, 404)   # "_" cache busters are ignored
assert json.loads(http("/api/ws/jobs")[2]) == {"jobs": []}
assert "zz" in json.loads(http("/api/dun/sets")[2])
st = json.loads(http("/api/op/dungeon.state?set=zz")[2])
assert st["spec"]["pieces"] and json.loads(http("/api/dun/state?set=zz")[2])["spec"] == st["spec"]
code, _, body = http("/api/op/dungeon.approve?set=zz&piece=x&rel=y")
assert code == 405 and json.loads(body)["type"] == "OpError", body
code, _, body = http("/api/op/dungeon.state", {"set": "zz", "colour": 1})
assert code == 400 and "unknown argument" in json.loads(body)["error"], body
code, _, body = http("/api/dun/nope", {})
assert code == 404 and "unknown route" in json.loads(body)["error"]
code, _, body = http("/api/dun/state?set=missing")
assert code in (400, 404, 500) and json.loads(body)["error"]
assert "skin" in json.loads(http("/api/ws/ramp/palettes", {})[2])["groups"]
assert "categories" in json.loads(http("/api/limner/doc")[2])
img = data / "sprites" / "ai" / "t.png"; img.parent.mkdir(parents=True, exist_ok=True)
from PIL import Image
Image.new("RGB", (2, 2)).save(img)
code, ctype, body = http("/api/ws/img?path=t.png")
assert code == 200 and ctype == "image/png" and body[:4] == b"\x89PNG"
assert http("/api/op/character.image?path=t.png")[2] == body
assert http("/api/ws/img?path=../../x.png")[0] == 400
r = json.loads(http("/api/ws/items/state", {"race": "human"})[2])
assert "spec" in r, list(r)
print("ok")
