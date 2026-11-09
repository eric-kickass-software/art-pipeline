"""hdart's module hygiene, by reading the source (AST, nothing is imported):
  - no private cross-module access: `other_module._name` or `from hdart.x.other_module import _name` (a helper two
    modules need is public, typed and documented in the module that owns its subject: imageops, rig, levels, store,
    art_pack, ...)
  - no import cycles in the module import graph (function-local imports count too)
  - every package has an AGENTS.md
Modules are named by their dotted path under hdart (e.g. `core.config`, `tools.dungeon.sets`).
Run: uv run python tests/test_modules.py"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "hdart"
MODS = {".".join(p.relative_to(PKG).with_suffix("").parts): p for p in PKG.rglob("*.py")
        if p.stem != "__init__" and "__pycache__" not in p.parts}
PACKAGES = {".".join(p.parent.relative_to(PKG).parts) for p in PKG.rglob("__init__.py")}


def absolute(name, node):
    """The dotted module (under hdart, '' for hdart itself) an ImportFrom names, or None if it isn't hdart's."""
    if node.level:
        base = name.split(".")[:-1]
        base = base[:len(base) - (node.level - 1)]
        return ".".join(base + ([node.module] if node.module else []))
    mod = node.module or ""
    if mod == "hdart" or mod.startswith("hdart."):
        return mod[len("hdart"):].lstrip(".")
    return None


def imports_of(name, tree):
    """(module edges, {local alias: module}, private from-imports) of one module's source."""
    edges, alias, private = set(), {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = absolute(name, node)
            if base is None:
                continue
            for a in node.names:
                sub = f"{base}.{a.name}" if base else a.name
                if sub in MODS:   # from hdart.pkg import module [as x]
                    edges.add(sub); alias[a.asname or a.name] = sub
                elif base in MODS:   # from hdart.pkg.module import name
                    edges.add(base)
                    if a.name.startswith("_") and a.name != "*":
                        private.append((base, a.name, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                mod = a.name[len("hdart."):] if a.name.startswith("hdart.") else None
                if mod in MODS:
                    edges.add(mod)
                    if a.asname:
                        alias[a.asname] = mod
    edges.discard(name)
    return edges, alias, private


graph, problems = {}, []
for name, path in sorted(MODS.items()):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    edges, alias, private = imports_of(name, tree)
    graph[name] = edges
    for mod, attr, line in private:
        problems.append(f"{name}:{line}: from {mod} import {attr} (private)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in alias \
                and alias[node.value.id] != name and node.attr.startswith("_") and not node.attr.startswith("__"):
            problems.append(f"{name}:{node.lineno}: {node.value.id}.{node.attr} ({alias[node.value.id]}, private)")
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

# every package documents itself; no module left at the top but the CLI
missing = sorted(p or "hdart" for p in PACKAGES if not (PKG / p.replace(".", "/") / "AGENTS.md").exists())
assert not missing, f"packages without an AGENTS.md: {missing}"
top = sorted(m for m in MODS if "." not in m)
assert top == ["cli"], f"modules outside the packages: {top}"

# the scan sees the shared modules (a guard against a scan that silently finds nothing)
assert {"imaging.imageops", "tools.character.rig", "imaging.levels"} <= \
    graph["tools.character.puppet_face"] | graph["tools.items.item_workshop"], graph["tools.character.puppet_face"]
print(f"{len(graph)} modules, {sum(map(len, graph.values()))} imports: no private cross-module access, no cycles")
print("ok")
