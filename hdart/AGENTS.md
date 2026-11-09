# AGENTS.md: `hdart/` (the package)

Flat modules, each with a module docstring that says what it does. Read that first. Repo-wide rules: `../AGENTS.md`.

## Entry points
- `cli.py`: `hdart` commands (`studio`, `puppet-export`, `item-export`, `export [--check]`, `dungeon-export`,
  `dungeon-capture`, `backup`, `op`, `mcp`). Thin wrappers over the export and tool modules; `op` and `mcp` go
  through `studio_client`.
- `studio_client.py`: the HTTP client of the running Studio's ops (`describe()`, `call()` → JSON or `ops.Blob`;
  `StudioDown` / `StudioError` with the Studio's message). URL: env `HD_STUDIO_URL`, else config `[studio] url`.
- `mcp_server.py`: the MCP server (`hdart mcp`, stdio, the official `mcp` SDK's `MCPServer`). Task-level verbs over
  the ops (`state`, `candidates`, `view_candidate`, `generate`, `approve`, `archive`, `get_spec`, `patch_spec`, jobs,
  `export`, `export_check`) plus `list_ops` / `describe_op` / `call_op`. Each verb maps a `tool` + `target` onto the
  tool's ops (`VERB_OPS`, `SPECS`; their summaries are the MCP descriptions). It imports the api modules only to read
  the op docstrings; every call goes over HTTP. A new tool or verb: add its branch there and to `tests/test_mcp.py`.
- `studio.py`: the web server (`serve()`), routing only. Static pages from `studio/`; `GET /api/ops[?prefix=]`,
  `POST /api/op/<name>` (JSON arguments), `GET /api/op/<name>?arg=` (read-only ops). The pages' older routes
  (`/api/ws/*`, `/api/dun/*`, `/api/lib/*`, `/api/limner/*`) go through `ALIASES` to each api module's alias tables.
  `JOB_MODULES` and `OP_MODULE_NAMES` are imported at start so the registries are complete; `OP_MODULES` names each
  op prefix's reload group.
- `ops.py`: the op registry (`@ops.op("items.approve")` on a typed, documented function; `ops.call()`,
  `ops.call_query()` (GET), `ops.call_alias()` (old routes), `ops.describe()`, `ops.Blob` for image results,
  `ops.http_error()` for the JSON error body). Every tool action is an op, in the tool's api module:
  `items_api` (`items.*`), `character_api` (`character.*`: Template, Races, Paint, Face, Ramps, images, `character.export`),
  `dungeon_api` (`dungeon.*`, incl. the materials library), `limner_api` (`limner.*`), `jobs_api` (`jobs.*`: list,
  get, wait, cancel, cancel_label, clear, queue). Each has `LEGACY` (POST) / `GET_LEGACY` alias tables. New actions are
  ops there; workflow (auto-queued backs, batch planning, job labels) lives in the api module, not the server.
- `jobs.py`: the queue and `@jobs.kind(name, gpu=, reload=)`. Long work goes through `jobs.enqueue()`.
- `watchdog.py`: `python -m hdart.watchdog` (started by overnight batches).

## Modules by tool
| Tool | Modules |
|---|---|
| shared core | `config` (settings, data root, specs dir, project profiles), `store` (state files, candidates, `progress.json`, archive), `jobs`, `ops`, `locks`, `comfy` (ComfyUI client + workflow builders), `fake_comfy` (tests), `memo` (preview caches), `backup` |
| character shared | `imageops` (masks: foreground, clean, components, grow/erode, hsv, figure_mask, retrying save), `rig` (rig space: `PX`, `S`, `N`, joints, pivots, waist, compose, head/figure boxes, face ellipse), `levels` (keying + cel levels: key hue, brightness levels, smoothing, skin keying, blank face, colouring, hand-edited bands), `palette` (ramp maths + `specs/palettes.json`), `workshop` (`AI_DIR`, `workshop.json`, style prompts, page images) |
| Character Workshop | `character_api` (the ops), `races` (Races tab: mannequins, `races.json`, job `race`), `puppet_template` (the rig), `puppet_paint` (Paint, Parts, AI fix, skin bands), `puppet_face` (hair, faces; the `paint` job: paint + face harvest), `mannequin_fit`, `puppet` (the old detection puppet + the marionette walk) |
| Item Workshop | `items_api` (the ops), `item_workshop` (how: zones, keying, split, hidden pass, grips, game items), `item_export` |
| Dungeon Forge | `dungeon_api` (the ops), `dungeon` (3.3k lines: generation, geometry, preview, CRUD, export, capture), `iso`, `patterns`, `library`, `pixel` |
| Limner | `limner_api` (the ops), `limner` |
| Game side | `contract` (reads the project's `ArtContract/requirements.json` and content), `art_pack` (pack table, `index.json`, `ramps.json` / `channels.json`, layer crop + save), `unity_export` (puppets), `item_export` (items, `export --check`) |

## State files (under `config.data_dir()`)
| File | Owner | Written through |
|---|---|---|
| `sprites/ai/workshop.json` | Workshop, Paint, Face, Template, Item Workshop (builds, approvals, sets) | `workshop.edit_state()` |
| `sprites/ai/items/<race>/state.json` | Item Workshop (approvals per fit key, grips) | `store.State(path).edit()` |
| `sprites/ai/paint/<build>/<cid>/`, `face/<race>/<hair\|face>/<cid>/`, `items/<race>/<item>/<cid>/` | candidates + `meta.json` | `store.new()`, `store.write_meta()` |
| `sprites/dungeon/<set>/state.json` + `candidates/` | Dungeon Forge | under `locks.DUNGEON` (atomic writes) |
| `sprites/limner/state.json` + `<cat>/<id>/<cid>/` | Limner | `store` |
| `sprites/studio_jobs.json` | the job queue | `jobs.Jobs` |
| `archive/` | every tool's ✕ / archive | `store.archive()`, `dungeon` archive helpers |

## Invariants
- **State changes only inside the lock:** `with store.State(path).edit() as s:` (or `workshop.edit_state()`) from the
  read to the write; writes are atomic (temp + `os.replace`). A GPU job generates first and edits state after; never
  hold a lock while ComfyUI runs.
- **Api modules import their tool per call** (`_dg()`, `_pi()`...), so a reloaded tool module is picked up. Data and
  spec paths come from env (`HD_DATA`, `HD_SPECS`) in tests that start a Studio: a reload resets module attributes.
- **Never reloaded:** `jobs`, `ops`, `locks` hold registries, the queue, locks and caches. The Studio reloads tool
  modules when their files change (`studio.fresh()`, each kind's `reload=`), so don't keep live state in a tool
  module's globals unless losing it on reload is fine. Known wart: `item_workshop.spec_override` is a module global
  swapped during a request (A10 chunk 8 fixes it).
- **Paths:** data paths come from `config.data_dir()`, specs from `config.specs_dir()`, the game from
  `config.project()`. Many modules still bind them at import (`workshop.AI_DIR`, `workshop.STATE`, `*.SPEC`); tests
  override those attributes. Don't add new import-time path constants.
- **ComfyUI:** call `comfy.img2img()`, `comfy.repaint()` or `comfy.image(build_*())`; failures raise
  `comfy.ComfyError` (a `RuntimeError`), never `SystemExit`.
- **Rig space:** the rest-pose canvas is 576 rig px / 1152 paint px, figure at `puppet.PAD`. Iso conventions for the
  dungeon are in `iso.py` (piece +x = board −Y, +y = board −X).
- **Exports:** `Art/index.json` is rewritten by every export; a pack folder is rebuilt whole. `export --check` must
  stay clean after an export.

## Module rules (`tests/test_modules.py` enforces them)
- **No private cross-module access** (`other._name`, `from .other import _name`). A helper two modules need is public,
  typed and documented in the module that owns its subject: masks in `imageops`, rig geometry in `rig`, keying and
  levels in `levels`, candidate files in `store`, pack files in `art_pack`.
- **No import cycles**, function-local imports included. Each module imports only those listed before it:
  `imageops` / `rig` / `levels` / `palette` / `workshop` / `races` → `puppet` → `puppet_template` → `puppet_paint` →
  `puppet_face` → `item_workshop` → `art_pack` → `unity_export` → `item_export`. A step that needs a later module goes in that module or
  takes a callback (`puppet_paint.paint(face_harvest=)`; the `paint` job is `puppet_face.paint`).
- Some module docstrings are written into exported files (`item_export`, `unity_export`, `puppet_template` take a
  paragraph as `_doc`): change those paragraphs only on purpose.
- `ai_kit.py` and `puppet_items.py` are transition shims for a Studio started before the rename (its reload lists name
  them). Nothing may import them; delete both once the live Studio has restarted.

## Don't touch without a reason
- `puppet.py`'s detection puppet is kept for reference (`tests/test_puppet.py`); the template is the rig.
