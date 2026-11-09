"""A client for the running Studio's op API, for every surface outside the Studio (the MCP server, `hdart op`).

These surfaces never import the tools to do work: every call goes over HTTP to the Studio, so there is one job queue
and one owner of the state locks. The Studio's URL comes from config.toml `[studio] url` (or config.local.toml);
env HD_STUDIO_URL beats both.

    from hdart import studio_client as sc
    sc.describe("items.")                       # the ops, as GET /api/ops lists them
    sc.call("items.candidates", {"item": "item.iron_helm"})
    sc.call("character.image", {"path": "..."}) # an image op returns an ops.Blob
"""
import json
import os

import requests

from . import config, ops

DEFAULT_URL = "http://127.0.0.1:4321"


class StudioError(RuntimeError):
    """An op call the Studio refused or failed. `status` is the HTTP code, `type` the error's type name."""

    def __init__(self, message: str, status: int = 0, type: str = ""):
        super().__init__(message)
        self.status = status
        self.type = type


class StudioDown(StudioError):
    """The Studio isn't running (or not at the configured URL)."""


def url() -> str:
    """The Studio's base URL: env HD_STUDIO_URL, else config `[studio] url`, else http://127.0.0.1:4321."""
    u = os.environ.get("HD_STUDIO_URL") or config.get().get("studio", {}).get("url") or DEFAULT_URL
    return u.rstrip("/")


def _request(method: str, path: str, body: dict | None = None, timeout: float = 60.0) -> requests.Response:
    base = url()
    try:
        return requests.request(method, base + path, json=body, timeout=timeout)
    except requests.ConnectionError:
        raise StudioDown(f"The Studio isn't running at {base}. Start it with start-art-tools.cmd or "
                         f"`uv run hdart studio --no-browser` in P:\\Tools\\art-pipeline (or set HD_STUDIO_URL / "
                         f"config [studio] url).") from None
    except requests.Timeout:
        raise StudioError(f"The Studio at {base} didn't answer {method} {path} within {timeout:.0f} s.") from None


def _result(r: requests.Response, what: str):
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200:
        try:
            err = r.json()
            msg, typ = err.get("error") or r.text, err.get("type", "")
        except ValueError:
            msg, typ = r.text.strip()[:300] or r.reason, ""
        msg = msg if str(msg).startswith(f"{what}:") else f"{what}: {msg}"   # OpError texts often name the op already
        raise StudioError(f"{msg} (HTTP {r.status_code})", r.status_code, typ)
    if ctype.startswith("application/json"):
        return r.json()
    return ops.Blob(r.content, ctype.split(";")[0] or "application/octet-stream", r.headers.get("Cache-Control", ""))


def describe(prefix: str = "") -> list:
    """Every op the Studio serves whose name starts with `prefix` (name, summary, doc, params, read_only, queues,
    returns)."""
    r = _request("GET", "/api/ops", None, 30)
    ops_ = _result(r, "GET /api/ops")["ops"]
    return [o for o in ops_ if o["name"].startswith(prefix)]


def call(name: str, args: dict | None = None, timeout: float = 120.0):
    """Call op `name` with `args` (POST /api/op/<name>). Returns the op's JSON result, or an ops.Blob for an image.
    Raises StudioDown when the Studio isn't running, StudioError when the op refuses or fails."""
    r = _request("POST", f"/api/op/{name}", args or {}, timeout)
    return _result(r, name)


def parse_value(text: str):
    """A CLI `key=value` value: JSON when it parses (numbers, true/false, null, lists, objects), else the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text
