"""The Studio: one local web server for every art tool.

    uv run hdart studio            -> http://127.0.0.1:4321 (opens the Character Workshop)

Pages (studio/*.html): /workshop (Character Workshop), /items (Item Workshop), /editor (Workshop Editor; all three are
workshop.html), /dungeon (Dungeon Forge), /limner (Limner).

Every action is an op (hdart/ops.py), declared in the tool's api module (`<tool>_api.py`):
  GET  /api/ops[?prefix=dungeon.]     every op: name, summary, doc, typed params
  POST /api/op/<name>                 call one with a JSON object of arguments
  GET  /api/op/<name>?arg=value       call a read-only one
The pages' older routes (/api/ws/*, /api/dun/*, /api/lib/*, /api/limner/*) are aliases of ops, through each api
module's alias tables (ALIASES below). Long work (generation, renders, Unity captures) goes through the job queue
(hdart/jobs.py, ops jobs.*). This module only routes and serves; tool logic lives in the tools.
"""
import importlib
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGES = Path(__file__).resolve().parent.parent / "studio"
PAGE_FILES = {"/workshop": "workshop.html", "/items": "workshop.html", "/editor": "workshop.html",
              "/dungeon": "dungeon.html", "/limner": "limner.html"}

# Every module that registers Studio job kinds (hdart.jobs.kind); imported at start so the registry is complete.
JOB_MODULES = ("dungeon", "library", "workshop", "puppet", "puppet_template", "puppet_paint", "puppet_face", "puppet_items", "limner")
# Every module that declares ops (hdart.ops.op); imported at start too.
OP_MODULE_NAMES = ("items_api", "character_api", "dungeon_api", "limner_api", "jobs_api")
BASE_RELOAD = ("comfy", "ai_kit", "workshop", "dungeon")   # reloaded before every job (plus each kind's own list)

# Ops per tool: the modules to reload (in order) before a call when their files changed, so code edits apply without
# a restart. "config" is reloaded on every call of that tool (cheap; picks up config.toml edits).
OP_MODULES = {
    "items.": ("config", "puppet", "puppet_template", "puppet_paint", "puppet_face", "puppet_items", "contract",
               "unity_export", "item_export", "items_api"),
    "character.": ("comfy", "ai_kit", "workshop", "puppet", "puppet_template", "puppet_paint", "puppet_face",
                   "character_api"),
    "dungeon.": ("patterns", "library", "dungeon", "dungeon_api"),
    "limner.": ("limner", "limner_api"),
    "jobs.": ("jobs_api",),
}

# The pages' older routes: URL prefix -> [(op prefix, api module, POST table, GET table)]. The rest of the path is
# the alias; the first module whose table has it serves the request (ops.call_alias).
ALIASES = {
    "/api/ws/items/": [("items.", "items_api", "LEGACY", None)],
    "/api/ws/": [("jobs.", "jobs_api", "LEGACY", "GET_LEGACY"), ("character.", "character_api", "LEGACY", "GET_LEGACY")],
    "/api/dun/": [("dungeon.", "dungeon_api", "LEGACY", "GET_LEGACY")],
    "/api/lib/": [("dungeon.", "dungeon_api", "LIB_LEGACY", "LIB_GET_LEGACY")],
    "/api/limner/": [("limner.", "limner_api", "LEGACY", "GET_LEGACY")],
}

JOBS = None


def make_jobs():
    """Import every job and op module (complete registries) and start the job queue."""
    from . import config, jobs
    for name in JOB_MODULES + OP_MODULE_NAMES:
        importlib.import_module(f"hdart.{name}")
    jobs.QUEUE = jobs.Jobs(config.data_dir() / "sprites" / "studio_jobs.json", base_reload=BASE_RELOAD)
    return jobs.QUEUE


# ----------------------------------------------------------------------------- reloads

_SRC = {}   # module name -> source mtime when fresh() last saw it


def fresh(*mods):
    """Reload `mods` (in order) when any of their source files changed since the last check, so code edits apply without
    a Studio restart while the modules' in-memory caches (painted puppet, item layers) survive the requests in between."""
    changed = False
    for m in mods:
        t = os.stat(m.__file__).st_mtime_ns
        if _SRC.get(m.__name__) != t:
            changed = True; _SRC[m.__name__] = t
    if changed:
        for m in mods:
            importlib.reload(m)


def fresh_ops(name: str):
    """Reload the modules behind op `name` (or a "<tool>." prefix) when their files changed."""
    mods = next((m for prefix, m in OP_MODULES.items() if name.startswith(prefix)), ())
    if "config" in mods:
        importlib.reload(importlib.import_module("hdart.config"))
    fresh(*(importlib.import_module(f"hdart.{m}") for m in mods if m != "config"))


# ----------------------------------------------------------------------------- dispatch

def op_call(name: str, args: dict):
    """POST /api/op/<name>: run one op with a JSON object of arguments (strict: unknown arguments are refused)."""
    from . import ops
    if not isinstance(args, dict):
        raise ops.OpError("the body must be a JSON object of arguments")
    fresh_ops(name)
    return ops.call(name, args)


def op_query(name: str, query: dict):
    """GET /api/op/<name>?...: run a read-only op from URL query arguments."""
    from . import ops
    fresh_ops(name)
    return ops.call_query(name, query)


def alias_call(path: str, args: dict, get: bool = False):
    """An older page route (POST with a JSON body, or GET with a query) -> its op. None if no route prefix matches."""
    from . import ops
    prefix = max((p for p in ALIASES if path.startswith(p)), key=len, default=None)
    if prefix is None:
        return None
    action = path[len(prefix):]
    for op_prefix, module, post_table, get_table in ALIASES[prefix]:
        table_name = get_table if get else post_table
        if not table_name:
            continue
        fresh_ops(op_prefix)
        table = getattr(importlib.import_module(f"hdart.{module}"), table_name)
        if action in table:
            return ops.call_alias(table, action, args, query=get)
    raise ops.OpError(f"unknown route {'GET' if get else 'POST'} {path}", 404)


# ----------------------------------------------------------------------------- server

def serve(port=4321, open_browser=True):
    global JOBS
    JOBS = make_jobs()
    from . import ops

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body, cache="no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _result(self, out):
            """An op's result: a Blob as its bytes, anything else as JSON."""
            if isinstance(out, ops.Blob):
                self._send(200, out.content_type, out.data, out.cache)
            else:
                self._send(200, "application/json", json.dumps(out).encode())

        def _error(self, e):   # keep the studio alive and show the error in the page
            code, body = ops.http_error(e)
            self._send(code, "application/json", json.dumps(body).encode())

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    self.send_response(302)
                    self.send_header("Location", "/workshop")
                    self.end_headers()
                elif u.path in PAGE_FILES:   # no-cache: a page edit shows on the next plain reload
                    self._send(200, "text/html; charset=utf-8", (PAGES / PAGE_FILES[u.path]).read_bytes(), "no-cache")
                elif u.path == "/api/ops":   # every op: name, summary, doc, typed params (?prefix=items.)
                    self._result({"ops": ops.describe(q.get("prefix", [""])[0])})
                elif u.path.startswith("/api/op/"):
                    self._result(op_query(u.path[8:], q))
                else:
                    out = alias_call(u.path, q, get=True)
                    if out is None:
                        self.send_error(404)
                    else:
                        self._result(out)
            except Exception as e:
                self._error(e)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads((self.rfile.read(n) or b"{}").decode("utf-8", errors="replace"))
                if u.path.startswith("/api/op/"):
                    self._result(op_call(u.path[8:], body))
                else:
                    if not isinstance(body, dict):
                        raise ops.OpError("the body must be a JSON object")
                    out = alias_call(u.path, body)
                    if out is None:
                        self.send_error(404)
                    else:
                        self._result(out)
            except Exception as e:
                self._error(e)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}"
    print(f"Studio: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url + "/workshop")
    srv.serve_forever()
