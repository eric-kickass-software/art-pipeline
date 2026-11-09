"""The Studio: one local web server for every art tool.

    uv run hdart studio            -> http://127.0.0.1:4321 (opens the Character Workshop)

`hdart studio` runs this server under the supervisor (hdart/server/supervisor.py), which restarts it when the code
changes (after the running job) and on `studio.restart`; nothing is reloaded in place. `--no-supervisor` runs it alone.

Pages (studio/*.html): /workshop (Character Workshop), /items (Item Workshop), /editor (Workshop Editor), /dungeon
(Dungeon Forge), /limner (Limner). Their scripts are native ES modules in studio/js/ and their styles in studio/css/,
served as static files (no build step); every page talks to the ops below through studio/js/api.js.

Every action is an op (hdart/core/ops.py), declared in the tool's api module (`<tool>_api.py`):
  GET  /api/ops[?prefix=dungeon.]     every op: name, summary, doc, typed params
  POST /api/op/<name>                 call one with a JSON object of arguments
  GET  /api/op/<name>?arg=value       call a read-only one
The older page routes (/api/ws/*, /api/dun/*, /api/lib/*, /api/limner/*) stay as aliases of ops for scripts and
agents that use them, through each api module's alias tables (ALIASES below). Long work (generation, renders, Unity captures) goes through the job queue
(hdart/core/jobs.py, ops jobs.*). This module only routes and serves; tool logic lives in the tools.
"""
import importlib
import json
import os
import webbrowser
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from hdart.core import config

PAGES = config.ROOT / "studio"   # the page files ship with the code
PAGE_FILES = {"/workshop": "workshop.html", "/items": "items.html", "/editor": "editor.html",
              "/dungeon": "dungeon.html", "/limner": "limner.html"}
# Static files next to the pages: URL prefix -> content type by extension. Served with Last-Modified and no-cache, so
# an edit shows on the next plain reload and an unchanged file is a cheap 304.
STATIC_DIRS = ("/js/", "/css/")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}

# Every module that registers Studio job kinds (hdart.core.jobs.kind); imported at start so the registry is complete
# (the saved queue names its jobs by kind, so a kind keeps its name when its module moves).
JOB_MODULES = ("hdart.tools.dungeon.generation", "hdart.tools.dungeon.capture", "hdart.tools.dungeon.library",
               "hdart.tools.character.races", "hdart.tools.character.puppet", "hdart.tools.character.puppet_template",
               "hdart.tools.character.puppet_paint", "hdart.tools.character.puppet_face", "hdart.tools.items.item_workshop",
               "hdart.tools.limner.limner")
# Every module that declares ops (hdart.core.ops.op); imported at start too.
OP_MODULE_NAMES = ("hdart.tools.items.items_api", "hdart.tools.character.character_api", "hdart.tools.dungeon.dungeon_api",
                   "hdart.tools.limner.limner_api", "hdart.core.jobs_api", "hdart.server.studio_api")
# Extra modules to load (comma-separated full names, e.g. a test's fake job kinds): env HD_STUDIO_MODULES.
EXTRA_ENV = "HD_STUDIO_MODULES"

# The pages' older routes: URL prefix -> [(op prefix, api module, POST table, GET table)]. The rest of the path is
# the alias; the first module whose table has it serves the request (ops.call_alias).
ALIASES = {
    "/api/ws/items/": [("items.", "hdart.tools.items.items_api", "LEGACY", None)],
    "/api/ws/": [("jobs.", "hdart.core.jobs_api", "LEGACY", "GET_LEGACY"),
                 ("character.", "hdart.tools.character.character_api", "LEGACY", "GET_LEGACY")],
    "/api/dun/": [("dungeon.", "hdart.tools.dungeon.dungeon_api", "LEGACY", "GET_LEGACY")],
    "/api/lib/": [("dungeon.", "hdart.tools.dungeon.dungeon_api", "LIB_LEGACY", "LIB_GET_LEGACY")],
    "/api/limner/": [("limner.", "hdart.tools.limner.limner_api", "LEGACY", "GET_LEGACY")],
}

JOBS = None


def load_modules() -> None:
    """Import every job and op module (complete registries), plus the HD_STUDIO_MODULES extras. The supervisor's
    import check runs this in a fresh process before a restart."""
    extra = [m.strip() for m in os.environ.get(EXTRA_ENV, "").split(",") if m.strip()]
    for name in [*JOB_MODULES, *OP_MODULE_NAMES, *extra]:
        importlib.import_module(name)


def make_jobs():
    """Load every job and op module and start the job queue."""
    from hdart.core import config, jobs
    load_modules()
    jobs.QUEUE = jobs.Jobs(config.data_dir() / "sprites" / "studio_jobs.json")
    return jobs.QUEUE


# ----------------------------------------------------------------------------- dispatch

def op_call(name: str, args: dict):
    """POST /api/op/<name>: run one op with a JSON object of arguments (strict: unknown arguments are refused)."""
    from hdart.core import ops
    if not isinstance(args, dict):
        raise ops.OpError("the body must be a JSON object of arguments")
    return ops.call(name, args)


def op_query(name: str, query: dict):
    """GET /api/op/<name>?...: run a read-only op from URL query arguments."""
    from hdart.core import ops
    return ops.call_query(name, query)


def alias_call(path: str, args: dict, get: bool = False):
    """An older page route (POST with a JSON body, or GET with a query) -> its op. None if no route prefix matches."""
    from hdart.core import ops
    prefix = max((p for p in ALIASES if path.startswith(p)), key=len, default=None)
    if prefix is None:
        return None
    action = path[len(prefix):]
    for op_prefix, module, post_table, get_table in ALIASES[prefix]:
        table_name = get_table if get else post_table
        if not table_name:
            continue
        table = getattr(importlib.import_module(module), table_name)
        if action in table:
            return ops.call_alias(table, action, args, query=get)
    raise ops.OpError(f"unknown route {'GET' if get else 'POST'} {path}", 404)


def static_file(path: str):
    """The file under studio/ for a static URL (/js/..., /css/...), or None if it isn't one we serve (unknown type,
    missing, or outside the folder)."""
    if not path.startswith(STATIC_DIRS):
        return None
    f = (PAGES / path.lstrip("/")).resolve()
    if f.suffix not in STATIC_TYPES or not f.is_file() or not f.is_relative_to(PAGES.resolve()):
        return None
    return f


# ----------------------------------------------------------------------------- server

def serve(port: int = 4321, open_browser: bool = True) -> None:
    """Run the server (blocks). Under the supervisor, `studio.restart` ends the process (hdart/server/supervisor.py)."""
    global JOBS
    JOBS = make_jobs()
    from hdart.core import ops

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body, cache="no-store", headers=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _static(self, f):
            """A page module or stylesheet: 304 when the browser's copy is current, else the file."""
            stamp = formatdate(f.stat().st_mtime, usegmt=True)
            if self.headers.get("If-Modified-Since") == stamp:
                self.send_response(304)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self._send(200, STATIC_TYPES[f.suffix], f.read_bytes(), "no-cache", (("Last-Modified", stamp),))

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
                elif (f := static_file(u.path)) is not None:
                    self._static(f)
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
