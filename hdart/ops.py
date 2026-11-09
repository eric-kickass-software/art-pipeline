"""The op registry: each tool's public actions, declared once as typed Python functions where the tool lives.

    @ops.op("items.approve")
    def approve(item: str, candidate: str, key: str = "all", on: bool = True, race: str = "human") -> dict:
        \"\"\"Approve (or un-approve) an item candidate for one fit key.

        Args:
            item: the Workshop piece id, e.g. "item.iron_helm"
            candidate: the candidate id (a folder name, "s<seed>_<time>")
        \"\"\"

One declaration serves every surface:
  - the Studio's HTTP API: `GET /api/ops` lists every op, `POST /api/op/<name>` with a JSON object of arguments calls one
  - an MCP server and the CLI can be generated from the same list (describe())

Parameters come from the signature: the annotation gives the type (str, int, float, bool, list, dict, or `X | None`),
a default makes the parameter optional, and the docstring's `Args:` block describes them (param_docs() describes the
ones a tool's ops share). The first docstring line is
the summary. `read_only=True` marks ops that change nothing (safe to call any time). An op that starts long work
(generation) queues a job through `hdart.jobs.enqueue()` and returns `{"job": id}`; declare its kind with `queues=`.

Like hdart.jobs, this module is never reloaded (it holds the registry); reloading a tool module re-runs its decorators,
so the registry always holds the fresh functions.
"""
import inspect
import re
import types
import typing
from dataclasses import dataclass

OPS = {}
PARAM_DOCS = {}   # "<tool>." -> {param: text}: descriptions shared by a tool's ops (see param_docs())
TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


class OpError(ValueError):
    """A bad call: unknown op, unknown or missing argument, or an argument of the wrong type. `status` is the HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Param:
    name: str
    type: type          # the base Python type (str, int, ...); object when unannotated
    optional: bool      # None is allowed (annotation `X | None`)
    required: bool
    default: object
    doc: str


@dataclass(frozen=True)
class Op:
    name: str
    fn: object
    summary: str
    doc: str
    params: tuple
    read_only: bool
    queues: str | None  # the job kind it queues, if it starts long work

    def describe(self):
        """A JSON-ready description: name, summary, doc, params (with a JSON schema type), read_only, queues."""
        return {"name": self.name, "summary": self.summary, "doc": self.doc, "read_only": self.read_only,
                "queues": self.queues,
                "params": [{"name": p.name, "type": TYPES.get(p.type, "any"), "required": p.required,
                            **({} if p.required else {"default": p.default}), "nullable": p.optional,
                            "doc": p.doc or _shared_doc(self.name, p.name)}
                           for p in self.params]}


def op(name, *, read_only=False, queues=None):
    """Register the decorated function as op `name` ("<tool>.<action>")."""
    def deco(fn):
        OPS[name] = _build(name, fn, read_only, queues)
        return fn
    return deco


def param_docs(prefix, docs):
    """Describe parameters every op of a tool shares (item, candidate, build...) once, for every op whose name starts
    with `prefix`; an op's own `Args:` entry wins."""
    PARAM_DOCS.setdefault(prefix, {}).update(docs)


def get(name):
    if name not in OPS:
        raise OpError(f"unknown op {name!r}", 404)
    return OPS[name]


def describe(prefix=""):
    """Every registered op whose name starts with `prefix`, sorted by name."""
    return [o.describe() for n, o in sorted(OPS.items()) if n.startswith(prefix)]


def call(name, args=None, *, lenient=False):
    """Validate and coerce `args` (a dict) against op `name`'s parameters, then run it. `lenient`: ignore arguments the
    op doesn't take (the old HTTP routes send extra keys) instead of refusing them."""
    o = get(name)
    args = dict(args or {})
    known = {p.name for p in o.params}
    extra = set(args) - known
    if extra and not lenient:
        raise OpError(f"{name}: unknown argument(s) {', '.join(sorted(extra))}; takes {', '.join(sorted(known)) or 'none'}")
    kwargs = {}
    for p in o.params:
        if p.name in args:
            kwargs[p.name] = _coerce(name, p, args[p.name])
        elif p.required:
            raise OpError(f"{name}: missing argument {p.name!r}")
    return o.fn(**kwargs)


# ----------------------------------------------------------------------------- internals

def _shared_doc(op_name, param):
    return next((d[param] for prefix, d in PARAM_DOCS.items() if op_name.startswith(prefix) and param in d), "")


def _build(name, fn, read_only, queues):
    doc = inspect.getdoc(fn) or ""
    if not doc:
        raise TypeError(f"op {name}: {fn.__qualname__} needs a docstring (its first line is the op's summary)")
    arg_docs = _arg_docs(doc)
    hints = typing.get_type_hints(fn)
    params = []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            raise TypeError(f"op {name}: *args/**kwargs aren't allowed (agents need every parameter named)")
        base, optional = _unwrap(hints.get(p.name, object))
        params.append(Param(p.name, base, optional, p.default is p.empty, None if p.default is p.empty else p.default,
                            arg_docs.get(p.name, "")))
    summary = doc.split("\n", 1)[0].strip()
    body = re.split(r"\n\s*Args:\s*\n", doc, maxsplit=1)[0]
    return Op(name, fn, summary, body.strip(), tuple(params), read_only, queues)


def _arg_docs(doc):
    """{name: text} from a Google-style `Args:` block (continuation lines are indented further)."""
    m = re.search(r"\n\s*Args:\s*\n(.*?)(?:\n\s*\n|\Z)", doc, re.S)
    out, cur = {}, None
    for line in (m.group(1).split("\n") if m else []):
        hit = re.match(r"\s*(\w+)(?:\s*\([^)]*\))?:\s*(.*)", line)
        if hit and (cur is None or len(line) - len(line.lstrip()) <= out[cur][1]):
            cur = hit.group(1); out[cur] = [hit.group(2).strip(), len(line) - len(line.lstrip())]
        elif cur:
            out[cur][0] += " " + line.strip()
    return {k: v[0] for k, v in out.items()}


def _unwrap(t):
    """(base type, optional) from an annotation: `str` -> (str, False), `str | None` -> (str, True)."""
    if isinstance(t, types.UnionType) or typing.get_origin(t) is typing.Union:
        rest = [a for a in typing.get_args(t) if a is not type(None)]
        base, _ = _unwrap(rest[0]) if len(rest) == 1 else (object, False)
        return base, True
    origin = typing.get_origin(t)
    return (origin if origin in TYPES else t if t in TYPES else object), False


def _coerce(name, p, v):
    if v is None:
        if p.optional or (not p.required and p.default is None):
            return None
        raise OpError(f"{name}: {p.name} can't be null")
    t = p.type
    try:
        if t is bool:
            if isinstance(v, str):
                if v.lower() in ("true", "1", "yes", "on"):
                    return True
                if v.lower() in ("false", "0", "no", "off", ""):
                    return False
                raise ValueError(v)
            return bool(v)
        if t is int:
            if isinstance(v, float) and not v.is_integer():
                raise ValueError(v)
            return int(v)
        if t is float:
            return float(v)
        if t is str:
            if isinstance(v, (dict, list)):
                raise ValueError(v)
            return str(v)
        if t in (list, dict) and not isinstance(v, t):
            raise ValueError(v)
    except (TypeError, ValueError):
        raise OpError(f"{name}: {p.name} must be {TYPES.get(t, t.__name__)}, got {v!r:.60}") from None
    return v
