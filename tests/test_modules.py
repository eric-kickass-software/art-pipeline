"""hdart's module hygiene, by reading the source (AST, nothing is imported):
  - no private cross-module access: `other_module._name` or `from .other_module import _name` (a helper two modules
    need is public, typed and documented in a shared module: imageops, rig, levels, store, art_pack, ...)
  - no import cycles in the module import graph (function-local imports count too)
Run: uv run python tests/test_modules.py"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "hdart"
MODS = {p.stem: p for p in PKG.glob("*.py") if p.stem != "__init__"}


def imports_of(name, tree):
    """(module edges, {local alias: module}, private from-imports) of one module's source."""
    edges, alias, private = set(), {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level == 0 and base.startswith("hdart"):   # hdart.x / from hdart import x
                base, level = base[len("hdart"):].lstrip("."), 1
            else:
                level = node.level
            if level != 1:
                continue
            if not base:   # from . import x [as y]
                for a in node.names:
                    if a.name in MODS:
                        edges.add(a.name); alias[a.asname or a.name] = a.name
            elif base in MODS:   # from .x import name
                edges.add(base)
                private += [(base, a.name, node.lineno) for a in node.names if a.name.startswith("_") and a.name != "*"]
        elif isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[0] == "hdart" and len(parts) > 1 and parts[1] in MODS:
                    edges.add(parts[1])
                    if a.asname:
                        alias[a.asname] = parts[1]
    edges.discard(name)
    return edges, alias, private


graph, problems = {}, []
for name, path in sorted(MODS.items()):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    edges, alias, private = imports_of(name, tree)
    graph[name] = edges
    for mod, attr, line in private:
        problems.append(f"{name}.py:{line}: from .{mod} import {attr} (private)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in alias \
                and alias[node.value.id] != name and node.attr.startswith("_") and not node.attr.startswith("__"):
            problems.append(f"{name}.py:{node.lineno}: {node.value.id}.{node.attr} ({alias[node.value.id]}, private)")
assert not problems, "private cross-module access:\n  " + "\n  ".join(problems)

# cycles: strongly connected components of more than one module (Tarjan)
index, low, stack, on, cycles, counter = {}, {}, [], set(), [], [0]


def visit(v):
    index[v] = low[v] = counter[0]; counter[0] += 1; stack.append(v); on.add(v)
    for w in sorted(graph[v]):
        if w not in index:
            visit(w); low[v] = min(low[v], low[w])
        elif w in on:
            low[v] = min(low[v], index[w])
    if low[v] == index[v]:
        comp = []
        while True:
            w = stack.pop(); on.discard(w); comp.append(w)
            if w == v:
                break
        if len(comp) > 1:
            cycles.append(sorted(comp))


for v in sorted(graph):
    if v not in index:
        visit(v)
assert not cycles, f"import cycles: {cycles}"

# the scan sees the shared modules (a guard against a scan that silently finds nothing)
assert {"imageops", "rig", "levels"} <= graph["puppet_face"] | graph["item_workshop"], graph["puppet_face"]
print(f"{len(graph)} modules, {sum(map(len, graph.values()))} imports: no private cross-module access, no cycles")
print("ok")
