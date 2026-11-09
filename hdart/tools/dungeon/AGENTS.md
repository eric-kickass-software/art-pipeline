# AGENTS.md: `hdart/tools/dungeon/` (Dungeon Forge)

Page `/dungeon` (`studio/dungeon.html`); ops `dungeon.*` in `dungeon_api.py` (incl. the materials library). Package
rules: `../../AGENTS.md`.

Modules, in import order (each imports only those above it; `tests/test_modules.py` checks there are no cycles):

| Module | What |
|---|---|
| `iso`, `patterns` | iso conventions and thin-wall/mesh geometry (piece +x = board −Y, +y = board −X); layout-template patterns. |
| `geometry` | flat-shaded templates, piece layout, atlas sizes, floor warp, downsampling, output resolution (`SCALE`, `output_tile`, `pixel_steps`); Blender normal pass (`meshes_dir()`, `blender/mesh_template.py`). |
| `sets` | the CRUD layer: paths (`dun_dir()`, `sets_dir()`, `style_path()`), specs and state, style, family and wall-family settings, approve, archive (nothing is deleted), candidate files. |
| `colour` | colour harmonizing (Lab stats, palette matching). |
| `generation` | everything that paints: tiles, props, floor masters + repaints, wall masters + variants + doors, the material sheet, recipes, candidate edits; job kinds `dun`, `dun_trial`, `dun_edit`, `dun_rescale`, `dun_finalize`, `dun_build`. |
| `cells` | a candidate cut into board cells (sprite + normal map), harmonized and downsampled. |
| `export` | the approved set into the game project (`config.project().export("dungeon")`). |
| `preview` | family stages and layouts, wall build status, cell images, the room preview (floor blocks = the game's FloorBlocks.cs). |
| `capture` | export + Prototype V's Unity batch capture; job `dun_capture`. |
| `library` | the materials library (`library/`, `config.library_dir()`); job `lib_import`. |

- Every read-modify-write of a set spec or a set's `state.json` holds `sets.LOCK` (`locks.DUNGEON`); writes are atomic.
- Callers import the module they need (`from hdart.tools.dungeon import sets as dsets, generation as gen`). A test
  that patches a function patches it in the module that calls it (`from x import name` binds the name there).
