# AGENTS.md: `hdart/` (the package)

Packages by layer; every module starts with a docstring that says what it does (read that first) and every package
has its own short AGENTS.md. Repo-wide rules: `../AGENTS.md`.

## Layout
| Package | Owns | AGENTS.md |
|---|---|---|
| `cli.py` | the `hdart` command (`hdart.cli:main`, `python -m hdart.cli`): `studio`, exports, `backup`, `op`, `mcp` | here |
| `core/` | settings and paths (`config`), state files (`store`, `locks`), the job queue (`jobs`, `jobs_api`), the op registry (`ops`), ComfyUI (`comfy`, `fake_comfy`), caches (`memo`), `backup` | `core/AGENTS.md` |
| `imaging/` | image helpers shared by the tools: masks (`imageops`), keying and cel levels (`levels`), ramps (`palette`), pixel-art passes (`pixel`) | `imaging/AGENTS.md` |
| `tools/<tool>/` | one package per tool (`character`, `items`, `dungeon`, `limner`): its modules (the how) and its `<tool>_api.py` (its ops) | `tools/AGENTS.md` + one per tool |
| `export/` | game side: the art contract (`contract`), the pack format (`art_pack`), puppet and item exports | `export/AGENTS.md` |
| `server/` | the Studio (`studio`, `studio_api`), its `supervisor` and `watchdog`, the clients (`studio_client`, `mcp_server`) | `server/AGENTS.md` |

Imports are absolute (`from hdart.core import config, store`, `from hdart.tools.dungeon import sets as dsets`).
Lower layers never import higher ones at module level: `core` → `imaging` → `tools` / `export` → `server` → `cli`.
`tools` and `export` use each other (an export reads a tool's state; a tool's export op calls `export`), always without
cycles. Module names are unique across packages (one `rig`, one `sets`), so a name in a docstring is enough to find it.

## Paths
Every data, spec and library path is a function, read when it is used, so `HD_DATA`, `HD_SPECS`, `HD_LIBRARY`,
`HD_PROJECT` (and `config.toml` edits) apply without a re-import:
- shared roots in `core/config.py`: `ROOT` (the repo: the code's own folder, a constant), `data_dir()`, `specs_dir()`,
  `archive_dir()`, `library_dir()`, `backups_dir()`, `project()`;
- each tool's own folders and spec files in the module that owns them: `workshop.ai_dir()` / `state_path()` /
  `candidates_dir()`, `item_workshop.spec_path()` / `zones_dir()`, `dungeon.sets.dun_dir()` / `sets_dir()` /
  `style_path()`, `limner.spec_path()`, `palette.palettes_path()`, `races.races_path()`, ...
- Never add an import-time path constant. Tests point paths at temp folders through the env vars, not by patching
  module attributes.

## State files (under `config.data_dir()`)
| File | Owner | Written through |
|---|---|---|
| `sprites/ai/workshop.json` | Workshop, Paint, Face, Template, Item Workshop (builds, approvals, sets) | `workshop.edit_state()` |
| `sprites/ai/items/<race>/state.json` | Item Workshop (approvals per fit key, grips) | `store.State(path).edit()` |
| `sprites/ai/paint/<build>/<cid>/`, `face/<race>/<hair\|face>/<cid>/`, `items/<race>/<item>/<cid>/` | candidates + `meta.json` | `store.new()`, `store.write_meta()` |
| `sprites/dungeon/<set>/state.json` + `candidates/` | Dungeon Forge | `dungeon.sets`, under `locks.DUNGEON` (atomic writes) |
| `sprites/limner/state.json` + `<cat>/<id>/<cid>/` | Limner | `store` |
| `sprites/studio_jobs.json` | the job queue (jobs named by kind, so kinds keep their names) | `jobs.Jobs` |
| `archive/` | every tool's ✕ / archive | `store.archive()`, `dungeon.sets` archive helpers |

## Invariants
- **State changes only inside the lock:** `with store.State(path).edit() as s:` (or `workshop.edit_state()`) from the
  read to the write; writes are atomic (temp + `os.replace`). A GPU job generates first and edits state after; never
  hold a lock while ComfyUI runs.
- **Api modules import their tool on first use** (inside each op), so `hdart mcp`, which imports them only to read the
  op docs, doesn't load numpy and the image stack.
- **Code changes apply through a restart, never a reload** (`server/supervisor.py` watches every package). Anything a
  module keeps in memory (`memo.LRU`s, `locks.CELLS`) is rebuilt after a restart; a cache that would be slow to rebuild
  goes on disk. Requests run on threads: never swap a module global to pass something to a call (pass an argument).
- **ComfyUI:** call `comfy.img2img()`, `comfy.repaint()` or `comfy.image(build_*())`; failures raise
  `comfy.ComfyError` (a `RuntimeError`), never `SystemExit`.
- **Exports:** `Art/index.json` is rewritten by every export; a pack folder is rebuilt whole. `export --check` must
  stay clean after an export.

## Module rules (`tests/test_modules.py` enforces them)
- **No private cross-module access** (`other._name`, `from hdart.x.other import _name`). A helper two modules need is
  public, typed and documented in the module that owns its subject: masks in `imageops`, rig geometry in `rig`,
  keying and levels in `levels`, candidate files in `store`, pack files in `art_pack`.
- **No import cycles**, function-local imports included. A step that needs a later module goes in that module or takes
  a callback (`puppet_paint.paint(face_harvest=)`; the `paint` job is `puppet_face.paint`).
- Every package has an AGENTS.md; no module sits at the top but `cli.py`.
- Some module docstrings are written into exported files (`item_export`, `unity_export`, `puppet_template` take a
  paragraph as `_doc`): change those paragraphs only on purpose.
