# Hearthdelve art pipeline (`hdart`)

Local, scriptable generation of the game's art: painted 2D cutout puppets (Character Workshop), equipment
layers for them (Item Workshop) and dungeon art sets (Dungeon Forge). Everything runs on this PC (RTX 4080); nothing goes to a cloud service.
This README is **how to use** the tools. What they are, their status, open items and decisions live in the vault:
`Game Dev/Art Pipeline/` (hub `Art Pipeline.md`, `Art Pipeline - Status.md` read first, `Art Pipeline - Roadmap.md`,
one note per tool, and per project the handoff contract and asset plan, e.g. `Hearthdelve/`). Requests between a
project and the tools go through that project's ledger (Hearthdelve: `Game Dev/Hearthdelve/Hearthdelve - Requirements.md`).

**Location:** `P:\Tools\art-pipeline`, its own git repo (moved out of the Hearthdelve repo's `Tools/ArtPipeline` on
2026-10-12; history before that is in the Hearthdelve repo). Output goes into the active project's profile in
`config.toml` (Hearthdelve: the root checkout `P:\Projects\Unity\Hearthdelve`). Set `HD_PROJECT` to point a run at a
worktree instead. Paths like `Assets/...` below are in that project. See **Config, tests, backups** below.
Git holds only the tool and its workflow (code, specs, docs). Generated art (`sprites/`, `textures/`, `kit/`, `work/`,
`archive/`) stays local: approved art is exported into the game project, and the rest can be thrown away or regenerated.


## Start here (picking this up in a new session)
1. `start-art-tools.cmd` (this folder) starts **ComfyUI** (:8188) and the **Studio** (:4321), each in its own minimized
   window. It's safe to run again: whatever is already up is left alone. Check: `curl http://127.0.0.1:4321/api/ws/jobs`.
   - The first time on a new PC, run `uv sync` here (the venv is git-ignored).
   - Browsing works without ComfyUI. Anything that generates needs it. The first generation after a start loads the models (~1 min).
   - Code changes to `comfy.py`, `ai_kit.py`, `workshop.py` and `dungeon.py` are reloaded per request and job. Changes
     to `studio.py` (routes, job kinds) need a Studio restart: close its window and run the script again.
2. The three web tools (one server, `hdart/studio.py`):

| URL | What it's for | Code | State / data |
|---|---|---|---|
| http://127.0.0.1:4321 | **Studio**: assemble a hero from kit parts and colours, play animations, front+back panes, double-sided voxel card (thickness/relief), save characters, switch part sets | `studio/index.html`, `hdart/studio.py` | `sprites/characters/*.json`, `sprites/sets/*.json`, baked kit in `work/sprite_render/` |
| /workshop | **Workshop**: curated AI character parts. Coverage board (parts × male/female/slim mannequins), generation queue, approve raw images, then the cut editor (brush/wand), native-grid QA, fix-region reprompts, card backs | `studio/workshop.html`, `hdart/workshop.py`, `hdart/ai_kit.py` | `sprites/ai/workshop.json` (board state), `sprites/ai/candidates/`, `sprites/ai/masks/`, `specs/ai_style.json` (descriptors, builds), `specs/ai_parts.json` (part library) |
| /items | **Item Workshop**: equipment as overlay layers on the painted puppets. Items (zone editor, generate, candidates, per-sex fits), Outfit (dress-up with ramps + walk) | `studio/workshop.html`, `hdart/puppet_items.py` | `specs/items.json`, `specs/item_zones/`, `sprites/ai/items/<race>/` |
| /dungeon | **Dungeon Forge**: dungeon sets. Pieces (walls, floors, doors, archways, wall decor, props, traps) → candidates → approve → room preview → export to Unity → Prototype V capture | `studio/dungeon.html`, `hdart/dungeon.py`, `hdart/iso.py`, `blender/mesh_template.py` | `specs/dungeon_sets/<set>.json` (pieces), `specs/dungeon_style.json` (descriptors + catalog), `sprites/dungeon/<set>/state.json` + `candidates/`, Blender mesh cache `sprites/dungeon/_meshes/` |

3. The generation **queue** (top right in Workshop/Dungeon) runs one job at a time, because ComfyUI is single-GPU anyway. The queue is saved
   (`sprites/studio_jobs.json`): after a restart the queued jobs run again; one that was running shows as failed
   ("interrupted"), because it may have written half its outputs. ✕ cancels a job (a running one interrupts ComfyUI).
   Before a GPU job the queue waits while ComfyUI is busy with someone else's work (a test, the CLI, a second Studio).
4. Scripted use (what earlier sessions did): the same HTTP API the pages use, e.g.
   `POST /api/dun/generate {"set","piece","n","seed","denoise?","lora?"}` → job id; `GET /api/ws/jobs`;
   `POST /api/dun/approve {"set","piece","rel","on"}`; `GET /api/dun/room?set=crypt&tile=64&scale=2` (PNG);
   `POST /api/dun/export {"set"}`; `POST /api/dun/capture {"set"}` (job). Or call `hdart.dungeon` / `hdart.workshop`
   directly from `uv run python` (no server needed, but don't run two generations against ComfyUI at once).
5. Unity: in the root project, Hearthdelve → **Scene Launcher** (Ctrl+Shift+L). Dungeon art shows in **Prototype V**;
   `hdart dungeon-capture crypt` exports and screenshots it in batch (needs the project *closed* in the editor,
   or point `HD_PROJECT` at a worktree checkout).

## Config, tests, backups
- **`config.toml`** (git) holds every setting: ComfyUI URL and timeout, model file names, the Blender exe, the data
  root (where `sprites/`, `work/`, `archive/` live; empty = this folder), the backup folder, and a **profile per
  project** (path, Unity exe, contract folder, content folder, export roots for art / puppets / dungeon / items /
  legacy). Machine-specific
  overrides go in `config.local.toml` (git-ignored, same keys). Env vars beat both: `HD_PROJECT`, `HD_PROFILE`,
  `HD_COMFY_URL`, `HD_DATA`, `HD_BLENDER`, `HD_UNITY`. Code reads it through `hdart/config.py` when it needs a value.
- **Tests:** `uv run python tests/run_all.py` (or one file: `uv run python tests/test_comfy.py`), ~10 min. No GPU:
  `hdart/fake_comfy.py` is a fake ComfyUI (same HTTP API, deterministic images) and the runner points `HD_COMFY_URL`
  at a closed port, so no test can queue behind the live Studio. `test_harmonize` reads the real crypt set: from a
  worktree, run it with `HD_DATA=P:\Tools\art-pipeline`.
- **One ComfyUI wrapper:** tools call `comfy.img2img(...)` / `comfy.repaint(...)` (PIL in, PIL out; temp files and
  unique upload names handled), or `comfy.image(build_*(...))` (the output as a PIL image, no scratch folder).
  Failures raise `comfy.ComfyError`, never `SystemExit`.
- **The store** (`hdart/store.py`) is how tools keep candidates and state. State files (`workshop.json`, the Item
  Workshop's `items/<race>/state.json`) change only inside `with store.State(path).edit() as s:` (or
  `workshop.edit_state()`), which holds a per-file lock (threads and other processes, a `<file>.lock` beside it) from
  the read to the write; every write is atomic. A GPU job generates first and edits the state after, never holding the
  lock while ComfyUI runs. Candidates (Paint, hair/face, items, grip hands): `store.new(root, seed)` makes
  `<root>/s<seed>_<time>/` (a suffix if two land in the same second), `store.write_meta()` writes `meta.json` with the
  standard fields (`tool`, `id`, `made`, `seed`, `prompts`, `inputs`, `params`, `git`) beside the tool's own, and
  `store.archive()` moves a candidate to `archive/workshop/<tool>/...` (never overwriting). Dungeon sets keep their
  own lock (`locks.DUNGEON`) and file-per-candidate layout; their `state.json` writes are atomic too.
- **Job kinds** are registered where the work lives (`@jobs.kind("dun")` in `dungeon.py`, `"item"` in
  `puppet_items.py`, ...; see `hdart/jobs.py`), so `studio.py` has no dispatch chain. A new module that registers
  kinds goes into `studio.JOB_MODULES`.
- **Backups:** `uv run hdart backup` (`--dry-run` to count) copies every tool's state and layers, the approved dungeon
  candidates and the specs to `P:\Archive\art-pipeline-backups\<date_time>\` (~0.5 GB; config `paths.backups`).
  Scratch (`tmp/`, `_tmp*`, the mesh cache), unapproved dungeon candidates, `archive/` and `work/` are left out. Old
  backups aren't pruned: delete them by hand. Run it before risky work (a big regenerate, a cleanup).

## Setup (once per machine)
| Piece | Where | Notes |
|---|---|---|
| Blender 5.2 LTS | `C:\Program Files\Blender Foundation\Blender 5.2\` | `winget install BlenderFoundation.Blender`. Override the path with `HD_BLENDER`. |
| ComfyUI v0.39 | `P:\Tools\ComfyUI` | uv venv (Python 3.12, torch 2.14.1+cu130). Start with `start-comfyui.cmd` → http://127.0.0.1:8188 |
| Models (~24 GB) | `P:\Tools\ComfyUI\models` | see table below |
| hdart | this folder | `uv sync`, then `uv run hdart --help` |

| Model file | Folder | Source | Licence |
|---|---|---|---|
| `flux-2-klein-4b.safetensors` | diffusion_models | Comfy-Org/vae-text-encorder-for-flux-klein-4b | Apache-2.0 |
| `qwen_3_4b.safetensors` | text_encoders | same | Apache-2.0 |
| `flux2-vae.safetensors` | vae | same | Apache-2.0 |
| `pixel-art-klein4b.safetensors` | loras | Limbicnation/pixel-art-lora (`pytorch_lora_weights.comfyui.safetensors`) | Apache-2.0 |
| `trellis_2_int8_convrot.safetensors` | diffusion_models | Comfy-Org/TRELLIS.2 | MIT |
| `trellis_2_shape_vae_bf16`, `trellis_2_texture_vae_bf16` | vae | Comfy-Org/TRELLIS.2 | MIT |
| `dino_v3_vit_l.safetensors` | clip_vision | Comfy-Org/TRELLIS.2 | check DINOv3 licence before shipping |
| `birefnet.safetensors` | background_removal | Comfy-Org/BiRefNet | MIT |

## Commands
| Command | Does | Output |
|---|---|---|
| `hdart kit-build [--force]` | Generates the modular character kit .blend from `specs/character_kit.json` | `kit/character_kit.blend` |
| `hdart kit-render [--bodies] [--parts] [--views]` | Renders every part into indexed layer sheets (~10 min for everything) | `Assets/Art/Characters/<body>/<part>.<view>.{data,normal}.png`, `manifest.json`, `palettes.json` |
| `hdart preview [--open]` | Random heroes: lineups, crowd grid, animated GIFs | `work/preview/` |
| `hdart compose <appearance.json>` | One hero's composed sheets | `work/preview/` |
| `hdart textures <set>` | Tileable textures + normal maps with FLUX.2 (`specs/textures.json`) | `textures/<set>/` |
| `hdart pixel-textures` then `hdart tiles` | Shrinks/posterizes the biome textures, then renders the 32×16 iso tile set (`specs/tiles_crypt.json`) | `Assets/Art/Tiles/<biome>/` |
| `hdart room` | Mock room with tiles, random heroes and torch light | `work/room_preview.png` |
| `hdart icons [--only id] [--variants n]` | Pixel icons for items/spells (`specs/icons.json`) | `Assets/Art/Icons/{Items,Spells}/` |
| `hdart mesh <name> --prompt "..."` / `--image x.png` | Concept image → TRELLIS.2 textured GLB | `work/meshes/` |
| `hdart mesh-render <glb> --name x` | GLB → battle / 4 iso / icon sprites + normals | `Assets/Art/Monsters/` |
| `hdart vfx` | Procedural spell flipbooks (`specs/spells.json`) | `Assets/Art/VFX/` |

| `hdart studio [--no-browser]` | The web tools above (or use `start-art-tools.cmd`) | http://127.0.0.1:4321 |
| `hdart backup [--dry-run] [--dest dir]` | Copies state, approved art and specs to a dated folder (see Config, tests, backups) | `P:\Archive\art-pipeline-backups\<date_time>\` |
| `hdart ai-base` / `ai-part` / `ai-kit` | AI character kit from the CLI (the Workshop is the curated way) | `sprites/ai/` |
| `hdart dungeon-export [set]` | Approved dungeon pieces → Unity (format 2, with normal maps) | `Assets/StreamingAssets/Art/Dungeon/<set>/` |
| `hdart puppet-export [race] [--size 512]` | A race's painted puppet (approved Paint per sex, else the newest), its hair and face sets and its skin/hair/eye ramps → Unity, each image with an index map (R = cel level, G = channel 1 skin / 2 hair / 3 eyes, A = mask) for recolouring (`hdart/unity_export.py`) | `Assets/StreamingAssets/Art/Puppets/<race>/` |
| `hdart item-export [ids] [--race human] [--size 512]` | Game items (Item Workshop pieces, approved) → art packs, `Art/index.json`, `ramps.json`, `channels.json`, and an intake file per id the game doesn't have yet (`hdart/item_export.py`; see "Game items and export") | `Assets/StreamingAssets/Art/Items/<id>/`, `ArtContract/intake/items/<id>.json` |
| `hdart export [--check]` | Puppet + every item; `--check` only validates `Art/` (manifests, files, index maps, `index.json` = the packs on disk) and the intake files, exit 1 on a problem | — |
| `hdart dungeon-capture [set]` | Export + Prototype V batch screenshots (`-lightTour` frames via the Unity method) | `work/protov/` |

`work/` is scratch (git-ignored). ComfyUI must be running for `textures`, `icons`, `mesh` and any generation.

## Dungeon Forge (dungeon sets)
A clean prompt, then downsample: FLUX paints at 8× the native size, and the result is area-averaged onto the 32/64 grid.
- **Piece kinds** (`specs/dungeon_sets/<set>.json`), all in exact iso geometry (`hdart/iso.py`, Prototype V conventions):
  - Floors: `shape: floor` (one top-down tile warped onto the diamond) or `method: atlas, atlas: N` (one seamless texture → N×N tiles, no visible repeats).
  - Thin walls: `shape: run, length: 1–4` (¼ tile thick, one image per run, cut into half-cell arms) plus a `shape: post` for junctions and ends.
  - Doors: `shape: run, opening: door`. Archways: `shape: mesh, mesh: archway` (Blender mesh → normal pass → template; GLB kept).
  - `kind: wall_decor` + `of: <wall>`: painted over that wall's approved image, so the stone matches (torches, banners, chains...).
  - `kind: prop`: clean prompt on white → BiRefNet → cropped to `footprint` tiles wide. Per-prop knobs: `view`, `biome: false`.
  - `board`: the content id the game asks for. Several pieces with the same `board` become variants: door styles, run lengths, floor atlases.
  - `retired: true` keeps old pieces out of export and preview.
- **Export format 2** (`set.json`): per key a list of variants with `layout` single/run/atlas, mirrored copies (`m`) for thin
  pieces, and `.n.png` normal maps (exact face normals + bump from the paint). Loader: `Prototypes/VisualStyle/DungeonArt.cs`.
  Board wiring: `PrototypeBoard.cs` (walls joined by neighbours, atlas floors, doors/arches, decor, flickering torches).
- **Axes:** piece +x = board −Y, piece +y = board −X (see `iso.py` / `DungeonArt.cs`).
- **Resolution / Pixelate:** FLUX paints at 8× the set's grid (`tile` 64 → 512 px per tile), with no pixel-art words
  in the prompt and the pixel LoRA off unless set per generation. The set's `pixel` setting (the **Pixelate** slider
  over the room preview) downsamples to that many px per tile for the pixel look; unset = full resolution.
  Candidate previews, the room and the Unity export all follow it. The export writes the same images to `r32/` and
  `r64/` and records `ppu` (px per tile = Unity pixels per unit) and `filter` (`point` / `bilinear`) in `set.json`,
  which both Unity loaders read. `crypt` is set to 64, its earlier look.
- **Floor families from one master (edge-compatible variants):** add piece → **Master floor**, generate, approve one.
  Then **Floor variant** pieces (method `repaint`, `from` = the master, size 1×1–4×4) repaint the master's interior and
  keep its edges, so any piece of the family fits next to any other, and the board mixes their sizes (FloorBlocks).
  - Master: txt2img → `flatten_light` (fits and divides out a quadratic brightness surface: vignette, side
    gradients) → `periodic_smooth` (Moisan periodic + smooth: opposite edges match) → the seam cross repainted with a
    soft mask (rolled to the middle). 512 px per tile.
  - Variant: the master tiled N×N (512 px per tile, 384 for 4×4: canvas ≤ 1536), `block_mask` keeps an outer band
    (`border`, default 0.08 tile) and fades in over 0.1 tile; repainted from the piece prompt with no reference image
    at denoise 0.85 (keeps the master's stone scale; ~0.6 subtle, 1.0 freer). The kept band is pasted back in pixel
    space, so the edges equal the master exactly.
  - `comfy.build_repaint`: FLUX.2 klein + `DifferentialDiffusion` + `SetLatentNoiseMask`; a grey mask = per-pixel
    change strength. `dungeon.repaint()` wraps it (upload, run, blend back).
  - Each master/variant candidate card has a **seam check** (master tiled 3×3, or the variant in a ring of master tiles).
  - Timing on the 4080 (model loaded): master ~5 s, 1×1 variant ~3 s, 3×3 block ~11 s. Tests: `tests/test_master.py`.
- **Layout (2026-10-12):** the sidebar is by area: **Floor families**, **Walls** (walls, doors, pillars, arches,
  windows: one board), **Older floors** (single pieces made before families). Props and wall decor will get their own
  Dungeon Forge area (wall decor via decals); until then they're reachable from the add-piece list and keep exporting.
  On a family the right column is the **live preview** (Output settings on top); elsewhere it's the room preview.
- **Scale (GDD 5.1):** 1 unit = half a tile width; **walls 3 units, doors 2, hero ~1.5**. New wall/door/arch/pillar
  pieces default to it (`WALL_UNITS` in the page); existing 2-unit wall art gets redone in the walls workflow.
  The preview's **scale guides** draw heroes (height adjustable) and a 1-tile block at wall height.
- **Image panel:** clicking any family image (master card, matrix cell, trial) opens it large: views *tiled 3×3 /
  among master tiles*, *image*, *in game* (the cut sprite); metadata (prompt, seed, stale, made from); **Approve** or
  **Use as master** / ★; **Archive**; **Edit** = repaint its interior from itself with a prompt + strength
  (`edit_candidate`, edges kept, same variation tag); masters also **Rescale** ×⅓ ×½ (exact repeats) ×¾ ×1.5 ×2
  (resampled + made seamless, optional re-detail) (`rescale_master`). ◀ ▶ / arrow keys, Esc closes.
  Masters also get **Even tone** (`even_master`): stone-sized colour differences (a dark slab, a stain) pulled to the tile's mean, detail and edges kept, wraps (`even_tone`, FFT blur). New floor masters get it automatically (family `even_tone`, default 0.8, 0 = off): a master repeats every tile, so a stone that stands out repeats too.
- **Wall masters: Rescale bricks** (image panel; `rescale_wall_master`). The kept cap and base (**keep top** / **bottom**, units; default 0.25 / 0.1, tinted in the **keep bands** view, `elevation_bands`) stay exactly as they are. The field between them is rescaled (`fx` = brick width, `fy` = height; target **rows** / **bricks across**, or ×½ … ×2 both ways), counted up from the base and snapped to whole courses (it starts on the field's lowest joint, stacks only whole courses, and fy moves slightly so none is cut under the cap). The joins are repainted, then the sideways seam is fixed only where pixels changed. `measure_elevation` counts the courses and the bricks per tile from edge strength (exact on templates, ≈ on painted walls). **Keep guides:** on a wall master's image (every view but in game) the kept bands are tinted, with a units ruler and the wall's horizontal joints as ticks (`elevation_joints`, route `elev_joints`); drag the yellow lines (they snap to joints and turn green; Shift = free). Opening a master sets them to the suggestion (where the regular courses stop: cap underside, plinth top); **fit to cap & base** resets to it. **Fit courses** (`fit_courses`, `fit_wall_master`; preview `elev_fit`): cut at the two lines and resample every joint-to-joint course to an even height (a course over a missed joint counts as two), vertical only. Mode **keep** leaves the cap and base and fills the space between; **crop** drops everything outside the lines and fills the whole wall. Shows the overall stretch and the range per course; warns past 15% overall or 30% on a course. Optional light re-detail. Templates for wall families also take **rows** (sets course).
- **Stages** (bar at the top of each family board; `family_stages`, in `family_info`): Template → Master → Variations → (walls: Doors → Built) → Exported, each done / part / stale / todo with a reason (draft master, targets met per size, variants from another master, build out of date, `export_status`: set.json older than the state or spec). Click a step to jump to it.
- **Recipes** (left column; `library/recipes/<id>.json`, in git, shared by every set; `save_recipe` / `list_recipes` / `apply_recipe`): **Save this family** or **Save set kit** (every family + the material sheet). **Use** makes new families (unique ids) with the recipe's prompts, variations, doors, template and switches; its sheet comes along when the set has none (or replaces it, if you say so). Board ids and images are never part of a recipe.
- **Fill gaps** (next to the stage bar; `fill_jobs`, `family/fill`): queues only what's missing: every variation × size cell (walls: run length, and every door) with no candidate from the active master yet (refused while the active master is a draft). **Review (N)** (`review_list`, `/api/dun/review`): steps through every candidate from the active master that isn't approved or archived, in the image panel: **A** approve (and on to the next), **X** archive, ← → skip. Wall approvals rebuild the walls.
- **Draft masters** (`generate(..., draft=True)`, `gen_master` / `gen_wall_master` `draft`): ~2 s instead of ~9-11 s (a quarter of the pixels, 4 steps, edges made periodic without the seam repaint), 3× the seeds per prompt version, DRAFT badge. **Finalize draft** (image panel; `finalize_draft`, job `dun_finalize`, ~10 s): the same composition redrawn at full resolution (img2img 0.4) and made seamless like any master (floors: even tone; walls: floor colour match).
- **Material sheet** (left column; spec `sheet`, `sheet()` / `update_sheet` / `sheet_from_floor`): what the set is made of, decided once: palette, floor stone size (tiles), wall course (units) and brick length (tiles), joint and bevel, material words (join the biome in every texture prompt), and the wall colour match (`match_to_floor`: new wall masters pulled toward the floor master's CIELAB colours, `wall_light` added to L). Family templates **linked to the sheet** (`template.link`, `link_family`, `sheet_template`) take its palette, module and joints, and follow it on every save. **Fill from floor master** takes the palette from the floor's active master (k-means) and the stone size from its template. **+ matching wall family** (floor family header; `matching_wall_family`) makes a linked running-bond wall family with the floor's material words.
- **Floor blocks (2×2-4×4) paint their feature once too** (family **multi-cell features: once**, the default; `gen_repaint`, `floor_zone`): two passes. Pass 1 redraws the whole block interior plainly (`PLAIN_REROLL`, 0.8: a new layout, so the block doesn't repeat the master; at 0.85 style words like "moonlit" turned into painted shapes), pass 2 paints the feature once inside a round zone that crosses tiles (1.0; lower sometimes painted nothing). About twice the time of one pass (~25 s for a 3×3). **spread** = the old single pass.
- **Multi-cell wall variations paint their feature once across the run** (family **multi-cell features**: once / spread; `feature_zone`): a 2-4 cell variation is placed as one piece, but FLUX repaints the master tiled N times and copies the feature into every cell. With **once** (default) the repaint is strong in one zone spanning cells (45-80% of the run, random per seed) and light (0.3) elsewhere, the prompt drops its "repeating" words and says "in a single place on this wall section". **spread** = the old behaviour (moss all over).
- **Preview status** (chip next to ⟳ refresh): updating layout / loading N sprites / rebuilding walls / ⚠ walls out of date (`wall_build_status`: built from another master, or approvals changed; click to build) / ⚠ approved variants from another master / ✓ up to date with the time. **⟳ refresh** clears the server's sprite and colour caches and reloads everything. Cut sprites are cached per sprite by what Harmonize makes them depend on (`harmony_key`), so an approval only re-cuts what it changes.
- **Breaking the every-tile repeat:** even tone, then **+ plain reroll** variations (the same surface redrawn, nothing added). Single cells cycle through the master and every approved 1×1 variant, and blocks (2×2 to 4×4) cover larger areas with no repeat.
- **Floor family board** (piece list → FLOOR FAMILIES → a family, or **+ New floor family**): the master workflow on
  one page. A family = a master piece + one repaint piece per size (`<master>_v<N>`, made on first Fill); its settings
  live on the master as `family` (prompt versions, variation phrases, per-size targets and planner weights).
  1. **Masters:** prompt versions (A, B, …) × seeds → cards tiled 3×3, grouped by version. ★ to compare,
     **Use as master** (= the only approved one), ✕ archives. **Variation test** repaints a 1×1 of a phrase from every
     ★ master (`trial`, saved under `candidates/<master>/trials/`) to see how each master takes variations.
  2. **Variations matrix:** rows = variation entries (`family.variations`: `{id, text}`; **+ variation**, ✕ removes
     the row, its images stay as "untagged"), columns = sizes (tick to enable, weight slider). ▶ on a row = that
     variation at every ticked size, ▶ on a column = that size for every variation, **Generate all** = everything
     (× "per cell"). Prompt = the active master's prompt + the variation text; candidates are tagged
     `meta.variation` (and `meta.master`: switching masters marks old ones **stale**, dashed). Click = approve.
  3. **Live preview:** a canvas drawing the planner's layout (`family_layout`) with the cut sprites the export ships
     (`/api/dun/cell`, cached in `locks.CELLS`; Pixelate/Harmonize applied, point filtering when pixelated). Drag to
     pan, wheel to zoom at the cursor, double-click / fit, full screen (Esc), board size, re-roll, block outlines.
  Size weights (% of anchors where a size that fits is used, default 70) feed the room preview and the export
  (`blockWeights` in set.json, read by the game's FloorBlocks).
- **Group boards:** click a group header in the piece list (FLOORS ▸, WALLS & DOORS ▸, ...) for every piece of that
  group on one page: edit prompts side by side, see all candidates (click = approve/unapprove, double-click = zoom),
  tick pieces and **Generate selected** (random seeds, one job per piece).
- **Style editing in place:** the piece editor and the group boards have an "Edit the shared style" box with just the
  fields that kind of piece uses (floors: medium/style/rendering/biome; walls: all six; props: the prop fields).
  Prompt previews update as you type; saves (there and on the Style tab) send only fields that changed.
- **Layout templates** (`hdart/patterns.py`; family board → *Layout template*): running / stack / English bond,
  basketweave, ashlar, flagstone, cobble, square and hex tiles, planks. Sizes in tile units (`across` = units per
  tile, a whole number; `course`, `mortar`, `bevel`, `jitter`), a palette (stone tones + joint), and every pattern
  repeats exactly once per tile (coordinates come from the pixel index within its tile), so it's seamless and to
  scale by construction. Masters with a template start from it (img2img at `strength`, default 0.7; FLUX keeps the
  layout and paints the surface), then the usual seamless pass. Tests: `tests/test_patterns.py`.
- **Blockout** (preview toolbar): the room with the families' templates instead of generated art: the floor template
  tile, the wall template projected onto runs/post/doorway with a flat door fill (`blockout_include`, cached files
  under `candidates/<piece>/_blockout/`). Judge layout, scale (scale guides) and colours before generating.
- **Materials library** (`hdart/library.py`; left panel → *Materials library*): CC0 materials from **ambientCG**
  (`ambientcg.com/api/v2`) and **Poly Haven** (`api.polyhaven.com`), no keys. *Import…* searches both; an import
  keeps the 1K colour + height maps in `library/cache/` (git-ignored, re-downloaded on demand) and writes
  `library/materials/<id>.json` (in git): source + licence, tags, editable descriptors, physical size, `repeats` per
  tile (1 tile ≈ 1.5 m, `TILE_METERS`), and a palette (k-means stone tones of the raised parts, joint = the lowest 15%
  of the height map). Use on a family: *texture as template* (`pattern: "image"`, the seamless map at `repeats` per
  tile), *palette* (on a procedural template), *words* (into the master prompt); or *New floor/wall family from it*.
  Floor and wall families can share a material. Downloads only from those hosts; from ambientCG zips only the colour
  and displacement maps are extracted, to fixed names. Findings: Poly Haven `dimensions` are mm but can be wrong
  (brick_4 says 0.5 m, measures ~2.6 m): adjust *repeats per tile* against the hero in the blockout. Fine materials
  (small setts) simplify at strength 0.65: use ~0.5 and the material's own words. Tests: `tests/test_library.py`.
- **Wall families** (sidebar → Wall families, **+ New wall family**): the floor family idea for walls.
  - **Master = a front-view elevation** (`kind: "elevation"`, method `wall_master`): 512 × 1248 px = one tile wide,
    3 units tall (1 unit = `iso._KZ` tiles), repeating sideways every tile, because the game drops runs anywhere
    and each starts its texture at its own edge. Generated at ~1 MP, then `flatten_light_x` + `periodic_smooth_x`
    + the vertical seam repainted (`make_seamless_x`).
  - **Variations per run length** (`<master>_var1..4`, method `wall_repaint`): the master tiled N times, everything
    but the kept edges, cap line and base repainted (`elev_keep_mask`), palette-locked to the master. Same matrix UI
    as floors, with run lengths as the sizes.
  - **Doors** (`<master>_doors`, method `door_repaint`, one row per door): a one-tile slice of the elevation with only
    the opening repainted (0.5 tile wide, 2 units tall, on the floor), pre-filled with dark wood so FLUX paints the
    door where the doorway geometry is (`DOOR_DENOISE` 0.8). Not palette-locked (wood isn't stone).
  - **Build** (`build_walls`; also runs by itself after approving or switching masters): `project_elevation` maps the
    elevations onto the exact thin-wall geometry (`iso.render_boxes(..., xyz=True)` gives every pixel's spot on the
    wall): runs 1–4 (master repeated + approved variations of that length), the post, the doorway (each approved
    door). The family's pieces get the GDD heights (walls 3, doors 2) and the set's board ids (`set_wall_boards`).
    Older wall pieces on the same boards are marked `retired` (reversible) so they don't ship next to the new ones.
  - Elevations are sources: never exported, cut or harmonized. Built walls are never harmonized either (the
    palette is locked at the elevation; per-piece shifts would break the joins).
- **Live preview = a room** once walls exist (or on a wall family): `room_layout` → `room_preview(..., grid=room_grid(w, h),
  floor_board=<floor family>, as_layout=True)` returns positioned sprites (the room preview's wall joining, posts,
  doors), drawn by the canvas; sprites come from the shared `cell_sprites` cache.
- **Left panel "Set & style":** set name and grid plus the shared descriptors and biome, saved in place (only changed
  fields). The Style & set tab is retired.
- **Floor family palette:** variants share the master's edges, so a whole-image colour shift would break them.
  For families everything is interior-only and toward the **master** (`match_palette`, weighted by the repaint mask:
  the kept border never changes): **palette lock** (family setting, on by default) matches each fresh repaint, edit
  and trial right after generation; **Harmonize** on a family pulls existing variants' interiors (masters unchanged:
  they are the palette). The board's **Palette** section shows a colour chip per image with its dE from the master
  (`family_palette`), worst first. Measured on `zz_master`: unlocked variants median dE 3.5 (max 6.9), locked 0.2.
  At 100% Harmonize also greys local accents (puddles); 50–70% keeps more of them.
- **Harmonize** (slider by Pixelate; set field `harmonize`, 0–1): pulls each floor/wall image's CIELAB mean and
  spread toward its family's reference (floors and walls separately; the approved images, or all candidates while
  none are approved). Props are left alone. Like Pixelate it's applied when cutting, so previews, the room and the
  export follow it. Tests: `tests/test_harmonize.py`.
- **Removing things = archiving** (nothing is deleted; `archive/` is git-ignored, empty it by hand when sure):
  - ✕ on a candidate, **Archive unapproved**, and **Remove piece** move images to `archive/dungeon/<set>/` (same relative paths).
  - **Delete family** (family board header) removes the master and every piece made from it (repaints, wall variations, doors, built walls) from the spec; their image folders and state go to `archive/families/<set>_<master>_<stamp>/` with a `family.json` to restore by hand. Older walls its builds retired come back unless another family builds on those boards.
  - **Archive set** moves the spec, images and state to `archive/sets/<set>_<stamp>/`. A Unity export stays in the project.
  - Every spec save keeps the previous version in `archive/history/<set>/` (last 40).
  - Pieces with images that aren't in the spec show under **Not in set** in the list, with Restore / Archive.
- **Concurrency:** spec and `state.json` writes go through one lock (`hdart/locks.py`, survives the per-request reloads).
  Generation merges its results into the state at the end, so approvals made while it runs aren't lost.
- **Prompting lessons:**
  - Don't say "tileset" for props (they get a floor slab), so props use `prop_medium`.
  - Small loose props need `biome: false`.
  - Floors and atlases ask for a "seamless tileable texture … no border" (otherwise FLUX draws a framed pit).
  - Decor needs denoise ~0.75 over the base wall.
  - Wall-run join edges and white outline bleed are cleaned up automatically when pieces are cut.

## Workshop (curated AI character parts)
**Three tools** (2026-10-25), switched with the ☰ menu top left: the **Character Workshop** (`/workshop`, tabs in process
order: **Template** (open on load) > **Race** > **Parts** > **Face** > **Ramps**; `#<tab>` in the URL opens a tab), the **Item
Workshop** (`/items`: player items: armour, weapons, other equipment; for now the **Board** and **Sets** of the older part
workflow and the Sprite Studio link; same page as the Character Workshop in "items" mode) and the **Dungeon Forge** (`/dungeon`). Every
prompt is editable next to the button that generates with it; wording shared by all races is in Race → Shared prompts (the Style
tab is gone: its descriptor groups are there, its mannequin clothing / builds / prompt preview are in Board → Board prompts).

0. **Race** tab (2026-10-22; was "Races"): a race = three puppet-ready **A-pose** mannequins, `<race>.unisex`, `<race>.male`,
   `<race>.female` (specs: `specs/races.json`; state in `sprites/ai/workshop.json` next to the older builds).
   - **Prompts are visible and editable** in the tab: per race `traits` + `description`; each gender's **body text** in its
     row (used by its mannequin and its Paint step); shared (Shared prompts card) the style descriptor groups
     (`specs/ai_style.json`), the mannequin `pose`, `base_body` and `style_groups` (medium/world are off because they add
     adventurer gear), and the paint wording. Each step shows the exact prompt it sends. The Mannequins tab is gone (2026-10-22).
   - **Base body (Eric):** unclothed and completely smooth, like a plastic toy doll, no anatomical detail, so clothing is
     generated per gender later. Kept through the male/female and back edits.
   - **Unisex front:** txt2img from the chosen style groups with the race pose, traits, description, unisex body and base body. A race
     with `from` (Elf → Human) instead edits that race's approved unisex with `from_edit`, so the races share proportions.
   - **Male / female:** no mannequin any more (2026-10-24): they're painted from the Template (Paint step). Their old
     front/back mannequins and Puppet outputs were archived to `archive/workshop/` (`work/ui_tests/archive_mf.py`);
     `race_needs` / `approve_race` refuse male/female mannequin steps (`MANNEQUIN_SEXES`).
   - **Backs:** FLUX edit of the approved front, a 3/4 back view in the same A-pose (perspective-correct; the mirrored
     silhouette only works for flat cards). Approving a front queues its back and drops the old back.
   - Each candidate records the image it was made from; when a newer source is approved it's flagged "made from an
     older source". Order is enforced (`race_needs`). ✕ archives like the Mannequins tab.
   - Skin tones: the race lists the `skin` ramps whose `races` include it (tag them in the Ramps tab).
   - The detection-based **Puppet** step (`hdart/puppet.py`, 2026-10-23: skeleton found in the mannequin art, rigid pieces
     cut from it) was removed from the tab on 2026-10-24: the clean template is the rig now. Its module and API stay for
     reference (`tests/test_puppet.py`).
   - **Clean puppet template** (2026-10-23, `hdart/puppet_template.py`, `specs/puppet_template.json`): the generic
     unisex body as **whole, separately drawn parts** (Klei symbol / Live2D template style), so nothing is cut from a
     figure. The spec (schemaVersion 2) is numbers in 512 px rig space per view: joints; per limb segment a bone +
     an outline of points in the bone's own frame (`[t, n]`: t = 0 at the start joint, 1 at the end joint; n = px to
     the side; `limb_world()`), joined by a smooth closed curve, so moving a joint carries the outline along. Measured:
     a round cap of 3 points around each joint (the overlap under the parent) and both sides of the limb at five
     stations (calf, bicep, knee), ~16 points per segment; the torso as ~13 landmarks (neck,
     shoulders, armpits, waist, hips, leg holes, crotch) joined by a Catmull-Rom curve, complete under the arms; the
     head as a smooth outline; seams (waistband; leg holes = the torso's lower edge); front **face guides**
     (`shapes.head.face`: eye centres, mouth, centre line; `measure_face()` takes them from the approved art's
     eyes and mouth, `add_face(spec)` adds them to an edited spec; draggable in the editor, orange). `measure(build)` takes the
     numbers from an approved build's puppet rig (hand-fixed joints included); `render(spec, view, s)` draws every
     part from the spec alone (clay shading lit from the top left, full outline, guide lines on a separate layer);
     `preview()` writes `sprites/ai/puppet_template/` (`front/`, `back/` parts + `.guides.png`, `parts_*.png`,
     `template_sheet.png`, `walk_*.gif` on the IK walk, all rigid). **The template is the rig**: `rig_from_spec`
     builds the walk's skeleton from the spec's joints (no detection on the art), so edits change the rig itself.
     **Template tab** in the Workshop (2026-10-24): front and back canvases draw the template live over the
     approved unisex art (art / parts opacity, size); **layers** panel (every piece on its own layer in draw order,
     plus joints, guides and art; hide any of them, click one to select it; remembered per browser); mouse-wheel
     **zoom** up to 8× around the cursor, pan with right/middle-drag or Space+drag, "Reset zoom"; drag joints
     (shoulder/hip, elbow/knee, wrist/ankle, fingertip/toe, neck/pelvis/crotch) and the waistband; click a part
     (selected in both views) for its outline points (every piece: limbs, torso, head; double-click the outline to add
     one, Alt-click to remove; limb points are stored in their bone's frame); Ctrl+Z undoes. Save validates and writes the spec
     (`tpl/save`); "Save & render preview" also queues job kind `tpl`; "Re-measure" queues `tpl_measure` (throws the
     edits away, measures again from human.unisex). Next: painting all parts of a view as one parts sheet in one
     FLUX pass. Test: `tests/test_puppet_template.py`.
   - **Paint** step per gender row (2026-10-24, `hdart/puppet_paint.py`): the clean template painted as the build,
     figure first. (1) The template's parts assembled in the rest pose, tinted with the race's skin ramp (briefs zone
     grey, eye/mouth marks from the face guides), are painted as one character (masked img2img, 0.8). (2) The parts
     laid out apart (exploded sheet) with their painted visible pixels; only the hidden areas (round ends under the
     parent, torso under the arms) are repainted (0.9). (3) Cleanup: near-white leftovers and detached strokes go.
     (4) One even outline (4 px at 1024), none where a part crosses the interior of the next part at a joint, always
     on the figure's silhouette. The back uses the painted front figure as reference. Prompts: race traits +
     description + the gender's body text (`specs/races.json`) + the gender's paint **face & body** words and **clothes**
     (`specs/paint.json`, edited in the Paint block, next to its button); the figure / completion / back-reference wording
     and the paint style groups are in Shared prompts, the AI fix wording next to the Parts tab's fix button. The Paint
     block shows the exact prompts it sends. Queue job `paint {build, n, seed}`
     (~1 min each). Candidates in `sprites/ai/paint/<build>/<id>/`: `parts/<view>/<part>.png` (1024 px, rest pose),
     `figure_*.png`, `sheet_*.png`, `rest_*.png`, `walk_*.gif`, `walkstrip_*.png`, `meta.json`, and the template they
     were painted on (`template.json`, `template/<view>/`); flagged when the template changed since. While it's being
     made a candidate has `progress.json` (step / stage / seed, or stage "failed" + the error; hair and face candidates
     too): its card shows the step and a progress bar instead of its images (✕ when it failed or stalled 10+ min), and
     it's never used as the build's paint. The Studio's job worker survives SystemExit from `comfy.run` (it used to
     kill the worker and stall the queue). Approve / Archive
     (to `archive/workshop/paint/`). API: `paint/state|spec|approve|archive|parts|save|revert` (`paint/spec {spec?, build?}`
     saves/returns the paint prompts and the composed ones of a build).
   - **Parts** tab (Parts editor): touch up a paint candidate part by part (Races → Paint → Edit parts). Every part
     is a layer in the rest pose (hide/show; the selected part drawn on top so its hidden ends can be painted, its
     template shape as a cyan ghost). Tools: brush (+ eyedropper, Alt-click), eraser, heal (fills the stroke from the
     colours around it), outline (the even outline's colour/weight), move (V: drag the selected layer, or only its marked
     pixels when there's an AI-fix mark; arrow keys nudge 1 px, Shift 10), AI fix area (mark, optional prompt, "Fix marked
     area": job `paint_fix` repaints only the marked pixels of the part, inside its template shape, with the figure
     as context); "keep inside the template shape" clamp; wheel zoom to 8×, pan; undo/redo; keys B E H O I M [ ].
     Save writes the parts (the painted originals are kept in `parts_orig/`, "Revert to painted" restores them) and
     queues `paint_preview` (walk re-render; scrub its frames beside the canvas). Tests: `tests/test_puppet_paint.py`
     (fake ComfyUI stub).
   - **Skin bands** (2026-10-31): each part's skin cel levels, made once from its paint and stored with the candidate
     (`bands/<view>/<part>.png`: R = level, 255 = not skin, A = the part's coverage; the skin key, the levels' RGB
     centres measured on both views, in `meta.json` `skin_key`). Made at the end of a paint job, or the first time an
     older candidate opens (~20 s). The level map is cut from the brightness blurred inside the skin (the pale 2 px rim
     left out), small patches merged and band edges smoothed (`workshop.skin_cut`, helpers in `hdart/levels.py`, shared
     with the Item Workshop); the front head is measured with its painted face removed. The **Skin bands** toggle shows
     them in display colours (one per level, darkest first) over each part: Brush paints the chosen level (1–5),
     Heal smooths, Eyedropper picks; only skin pixels change. Saving paint (or an AI fix) redoes that part's bands
     unless they were edited by hand (✎ in the layer list, `bands_edited` in meta); "Redo bands from paint" resets
     the selected part (Shift: every part, new skin key); Revert to painted resets them all. The Ramps tab and the
     Unity export read the bands. Actions `paint/bands_save {build, id, view, bands}`, `paint/bands_reset {build, id[, view, part]}`.
   - API: `race/state {race}`, `race/spec {spec}`, `race/approve {build, facing, file}`, `puppet/approve {build, on}`; queue job kind `race`
     `{race, sex, facing, n, seed}`. Tests: `tests/test_races.py` (fake ComfyUI stub).
1. **Mannequins** tab: generate and approve the male/female/slim base (front, then a back made from the mirrored front, clamped to the
   same silhouette so the card's two sides line up). How the back is made (`back_from_front`, tested 2026-10-20 on
   4 seeds, `tests/test_workshop_backs.py`):
   - The silhouette is `figure_mask`: the foreground plus enclosed holes **above the chin**. Eye whites are pure white,
     so the foreground test misses them; left as holes they came back as white glints on the back of the head.
   - Starting image: the mirrored front, blurred, with a 3 px dark **outline** on the silhouette edge. Sampling is
     **masked** to the silhouette (`comfy.build_img2img(mask_name=...)`, Differential Diffusion), so the outside stays
     white and FLUX fills the shape up to the line. Without the mask it drew its own straighter pose (and props), and the
     clamp smeared colour into the difference: silhouette fill 89% → 99.8%.
   - Holes FLUX still leaves inside the silhouette get one small masked repaint (`_gap_repaint`), then the clamp.
   - Tried and dropped: denoise 0.65 (blurry), a flat skin-tone fill (flatter, more glitches), filling the head with
     skin before the blur (flat heads, ears lost), a "fills the outlined shape" prompt (no measurable gain).
   - Front shadows: the style avoids the word "shadow" and says "floating in empty space" / "nothing beneath the feet"
     (shadows on 4 of 4 seeds before, 0 of 4 after). Removing them afterwards missed tinted shadows.
   ✕ on a candidate archives it; **Archive unapproved** (per front/back)
   archives every candidate that isn't approved, plus the older files in that build's `_base` folder the tab no longer lists
   (`archive_base`, `/api/ws/base/archive`). They move to `archive/workshop/` (same relative paths). The approved one never moves.
   Tests: `tests/test_workshop_archive.py`.
2. **Board**: each cell is part × build. Generate candidates, approve a raw image (its back is queued automatically), then **Cut**:
   auto threshold/speck, brush keep/remove, magic wand, undo, native-grid QA. **Fix region**: paint an area, prompt a
   correction, and FLUX redoes only that area. Approve → the kit rebuilds.
3. **Sets**: save the approved parts as a named set; the Studio can switch sets. (The Style tab's prompts moved to Race and Board.)
4. Cards: backs whose file name has `_card_` are silhouette-matched. Older backs show as `redo-back` on the board.
4b. **Face** tab (2026-10-24, `hdart/puppet_face.py`, `specs/face.json`): the race's **hair** and **faces**, painted on its
   painted puppets (approved Paint, else the newest; "Paint on" male or female). Male and female share the Template's head,
   so every hairstyle and face fits both; layers are 1024 px RGBA in the rest pose and ride on the head bone.
   - **Hair**, by **colour key**: a head-shaped guide cap (above the brows in front, the whole back of the head behind) is
     filled with the key green (`hair_key`) and FLUX paints the hair "one flat vivid pure green (a green-screen colour key)"
     in a band around the head (the face from the face guides and the rest of the body masked out); front first, then the
     back with the painted front as reference. Cut out by hue (+ the dark outline pixels touching it, only pieces touching
     the head); the green's brightness becomes 5 cel levels (`levels_<view>.png`: R = level, 254 = outline, A = hair),
     coloured by a **hair ramp** (palettes group `hair`). So the race's hairstyle text is the style only, no colour.
   - **Faces are their own layer (2026-10-25, Eric):** the Paint step paints faceless heads (`face_guide` = `faint`, the
     default again) and the Face tab paints each face as a separate layer: the **whole faceless puppet** as context, framed
     like the Paint step's figure pass (`_figure_box`; the scale its faces came out well at), starting from a **drawn face
     guide** (`face_guide()`: almond eye whites, thick upper lids, magenta-key irises with pupils and highlights, brows, a
     nose tick, a mouth) that FLUX refines (denoise 0.7), masked to the face area inside the head. The layer = what changed
     against the faceless head. Earlier variant, kept as an option:
   - **Faces from the paint (2026-10-24):** with `face_guide` `keyed`: the head gets its own face,
     painted with the whole character (eye marks with magenta-key irises + a mouth line), and every finished paint's face
     is **harvested** into the race's face candidates (`face_from_paint`: non-skin pixels of the face area kept off the
     outline = the layer, the magenta irises = eye-ramp levels; card "from paint …"). Eric: faces painted with the
     puppet beat the separate face pass, whose guide was too strong and not detailed enough.
   - **Faceless heads (2026-10-24):** the Paint step's `face_guide` (`specs/paint.json`, Race → Shared prompts) can be `faint`: thin guide lines a little darker than the skin + a "blank face" prompt, so the painted head stays faceless and
     every face comes from here (`marks` = the old solid eye/mouth marks, the head gets its own face).
   - **Back hair = AB2 (2026-10-25 test, `work/ui_tests/hair_back_test.py`):** (1) a FLUX *edit* of the just-painted front
     head into a back view (`hair_back_edit` prompt; keeps the hairstyle, e.g. braids), mapped onto the back head and cut
     by the key; (2) the guide = a drawn back cap (key green, crown, strands, darker underside, outline: coverage) with
     that hair on top (style); (3) one masked pass on the whole puppet (figure framing; mask = around the head + the upper
     back, never the arms; front as reference; denoise 0.7); (4) key cut. Hair levels come from a median-smoothed
     brightness (no speckles). About +20 s per candidate. Touch-ups: Edit opens front and back.
   - **Face** (front): the head's own painted face is removed first (`blank_face`: non-skin pixels in the face area filled
     from the skin around them, kept off the outline), then FLUX repaints the face area starting from **guide marks**
     (`key_marks`: eye ovals with a magenta-key iris, a mouth line, where the face guides say) inside the head shrunk by
     12 px (`inner_head`: the chin / jaw outline is never redrawn); the layer keeps the non-skin feature pixels, thin
     lashes and outlines included (no speck filter for faces). Irises are
     painted in the **magenta key** (`eye_key`) and become cel levels near the eye guides (`levels_front.png`), coloured by
     an **eye ramp** (group `eyes`). Candidates whose key wasn't painted are flagged "not keyed: can't be recoloured".
   - Per candidate `sprites/ai/face/<race>/<hair|face>/<id>/` (layers, levels, paints, previews on male and female, meta with
     the keyed pixel count). "Add to the set" builds the race's **hair set** / **face set** (state: `<race>.unisex.face_sets`);
     ✕ archives. A colour picker per card re-renders the previews with any hair / eye ramp (`face/preview`). Each card has
     the race's text next to its Queue button and the prompts it sends; shared wording + key colours in Shared prompts.
   - **Edit** on a candidate opens its layer in the Parts editor (2026-10-25): the puppet it was made on is read-only
     context (a face sits on the faceless head), the layer is drawn right after the head and is the only editable layer;
     brush / eyedropper / eraser / heal / outline, zoom, undo. Save keeps the original (`layer_orig_<view>.png`, Revert
     restores it) and recomputes the levels (`levels_from_layer`: hair = dark pixels as outline + 5 brightness levels, so
     any colour painted in still takes the hair ramps; face = the magenta-key irises) and the previews. The AI fix is for
     paint parts only. Actions `face/layer_doc|layer_save|layer_revert`.
   - Jobs `face_gen {race, what (hair|face), sex, n, seed}`; actions `face/state|spec|set|archive|preview|ramp_preview`.
     Tests: `tests/test_puppet_face.py` (on `hdart.fake_comfy`, with a respond hook that paints the keys).
5. **Ramps** tab (2026-10-24: painted puppets + the race's colour set): pick a **race**; the preview recolours its painted
   **male and female** puppets (approved Paint, else the newest) in a 2×2 grid (male front/back on top, female below).
   The skin levels are the parts' **skin bands** (Parts editor), composed in the rest pose (`puppet_paint.bands_composite`;
   action `paint/ramp_preview`). A race's **skin tone set** = every `skin` ramp tagged with it: listed as name + swatches
   (click one to edit), "Add this ramp to the set" / "Remove it from the set" (tags the race and saves), "Create
   starter set" (one ramp per chosen Monk tone, `<race>_monk<n>`, default sliders; existing names are kept). Human has
   a starter set (Monk 1, 3, 5, 7, 9). The puppets wear the race's hair and face (Face tab: first in the set, else the
   newest; `puppet_face.ramp_preview` returns skin, hair and iris level maps, what the hair and face layers cover
   excluded from the skin); the ramp being edited colours its own layer, the others take the race's first ramp of their
   group. The **<Race>'s colour set** card lists the race-tagged skin, hair and eye ramps (name + swatches).
   Editing: edit the colour ramps in `specs/palettes.json` (any group; skin tones are the `skin` group). A tone is a
   5-colour ramp, darkest first; the art keeps its cel levels and the game colours them, so one mannequin and one puppet rig
   serve every tone (decided 2026-10-22, see the vault's Art Pipeline note).
   - Base (midtone) colour with the **Monk Skin Tone Scale** (Google, 10 tones) as presets; sliders for saturation, hue
     offset, shadow depth / hue shift / hue target / saturation, highlight lift / hue shift / hue target / desaturation.
     At the default sliders the ramp equals `hdart.palette.ramp_from_base` (checked against the JS in `tests/test_ramps.py`).
   - Click a swatch to set that colour by hand, right-click to reset it. `races` tags which races may use the ramp.
   - Live preview: the skin bands coloured in the browser (`workshop.skin_key`: k-means on the skin colours, seeded
     from brightness percentiles, gives the level colours; `skin_cut` the smoothed level map). "Every ramp in the group" shows them all side by side.
   - Save writes `base`, `tune` and the resolved `colors` (the game export uses `colors`). API: `/api/ws/ramp/palettes`,
     `ramp/preview {build}`, `ramp/save {group, name, old_name, base, tune, colors, races}`, `ramp/delete`.
   - Raw Monk values are photographic and muted: game ramps need more saturation (~×1.4), and very dark tones a lifted
     shadow end so the cel bands stay readable against the outline (skin-tone test, 2026-10-22).

## Item Workshop: equipment layers (`/items`, 2026-10-25)
Code: `hdart/puppet_items.py` (all logic; the Studio routes `/api/ws/items/<action>` to `api()` and job kind `item` to
`job()`, both reloaded per request/job, so item changes need no Studio restart), page: the Items and Outfit tabs of
`studio/workshop.html`, spec: `specs/items.json`, painted zones: `specs/item_zones/<item>[.<sex>].<view>.png`,
state (approvals): `sprites/ai/items/<race>/state.json`.
An item is a set of overlay layers, one per Template part it covers. Every build of a race is painted on the one shared
Template, so an item fitted once is reused on every build of the race.
- **Tabs:** Items, Outfit, Layers.
- **Items tab:** the catalogue by slot (✓ = approved, ♂/♀ per sex; `→ x` = part of game item x); item settings (slot, game item, kind fitted/held, fit,
  description, materials, depth, bulk, clips hair, FLUX hidden pass; held: hand, length, tilt); the **zone editor**
  (brush / eraser / lasso add / lasso erase, size, view zoom, undo/redo, front/back, on the male or female puppet; keys
  B E L K, D = zone / details, Ctrl+Z/Y; a **Details** layer sketches the item in its material colours, see below); Generate (fit on a build, n, seed) with the composed prompts shown and the shared prompt templates
  editable underneath; candidates (FLUX's paint + the candidate on male/female front/back, Approve, Walk, Re-extract,
  Re-extract + FLUX hidden, Archive).
- **Outfit tab:** a build wearing every approved item (its fit for that build), a ramp per material, a hair from the
  race's hair set with a hair ramp; rest + walk, front and back.
- **Gender variants:** `fit: per_sex` items are approved per sex (fit on `human.male` and on `human.female`; the female
  fit uses the female body prompt); a build wears its sex's fit, else the shared one. A zone saved with "this sex only"
  (`<item>.<sex>.<view>.png`) overrides the shared zone for that sex.
- **Layers tab** (the Parts editor in item mode; a candidate's **Edit layers**): the puppet it was fitted on as read-only
  context and one editable layer per Template part (`<part>@item`, gold rows, each right above its body part; parts the
  item doesn't cover yet start empty). Brush / eraser / heal / outline / eyedropper / move as for paint parts. Save keeps
  the originals (`parts_orig/`, `plevels_orig/`; **Revert** brings them back) and recomputes the levels: key colours by
  material with the candidate's stored cel levels (`meta.cents`), dark = outline, any other colour = the first material
  by brightness.
- **Held items and grip hands:** a weapon rides its hand bone on a **grip line**. Without a grip hand: through the point
  40% from wrist to fingertip, angled so that with the arm hanging (the walk) the tip points up and `grip.wrist`° (40)
  forward, screen-left in the front view and screen-right in the back view (one rule can't do both: the hand is rigid).
  **Grip hands** (Grip card, per build, job `grip`): a close-up FLUX pass per hand and view (box ≈ 4.2 hand sizes; in the
  full figure a hand is ~40 px, too small for a fist; the forearm's end may be repainted so the fist joins the wrist)
  paints the fist around a magenta guide rod; the rod FLUX actually drew is measured (line fit) for the grip point
  (the fist's middle on it), axis and width; the hand splits into `under_<arm>_<view>` (palm, the rod's pixels filled
  in) and `over_<arm>_<view>` (the fingers in front: hand pixels inside the drawn rod's strip). Worn: under-hand <
  weapon < fingers. Settings per item: `hand` (armB = near hand reads best), `length`, `grip_angle`, `flip`, `grip_at`
  (click on the weapon). `regrip` re-splits from the saved `after_<view>.png` without the GPU. Output:
  `sprites/ai/items/<race>/_grip/<build>/<id>/`, approval in `state.json` → `grip`.
- **Back view (like the back hair, AB2):** the back pass used to take the painted front as its reference, so FLUX copied
  front anatomy onto the back (a chest on the back of a tunic). Now a FLUX edit (`back_edit`) turns the painted front
  around ("the same garment from behind: shoulder blades, spine, back seams; nothing from the front"), its key-coloured
  item pixels are mapped onto the back puppet over the zone tint (`guide_back.png`), and that edit (`edit_back.png`) is
  the reference of the masked back pass and of the back's hidden-area pass.
- **Smooth cel levels:** FLUX's soft gradients between its flat tones used to quantize into mottled islands (splotchy
  once a ramp colours them). The brightness is now blurred inside the item before it's cut into levels (`smooth_val`,
  outline excluded), patches smaller than `ISLAND` (400 px) merge into their surroundings and a small majority filter
  smooths band edges (`smooth_levels`, `hdart/levels.py`, shared with the skin bands). `relevel` (job) redoes an existing candidate's levels from its painted colours.
- **Painted zones** replace the preset `zones` once saved. A painted zone only reaches what shows in the rest pose, so
  each part's zone also floods into that part's hidden pixels from where it was painted (a vest continues under the
  near arm; a sleeve into its shoulder cap). Areas painted past the body are allowed (pauldron or skirt silhouettes).
- **Zones** (data, so an editor can draw them later): `part` (fnmatch, `arm*.upper`), `t` = range along the part's bone,
  `rows` / `ellipse` = fractions of the part's box, `seam` = above/below the waist, `view` = one view only. `bulk` = px
  the item may paint past its zones.
- **Details sketch** (zone editor → Paint: Details, key D): an RGBA sketch per item and view
  (`specs/item_zones/<item>[.<sex>].<view>.sketch.png`, saved with the zone; "this sex only" works as for zones) in the
  item's material keys plus a dark line colour (`LINE`): a leather belt on a tunic, a metal trim, seams, a neckline.
  Material strokes also add to the zone; lines don't. Saved with hard alpha, snapped to the palette. `generate` stamps it
  on the guide (shaded by the puppet's brightness like the zone tint; on top of the back guide too) and saves
  `guide_<view>.png`; `meta.sketch` lists the views that had one. Only new candidates use it (re-extract keeps the old
  paint). Lines aren't keyed: they steer FLUX's shapes, the outline pass draws the final edges. Only the item's own
  materials are offered: add leather to a tunic's materials to sketch a leather belt.
- **Fitted item:** the zones tinted in the item's first material key (full strength; weaker tints let FLUX shrink the
  item) → one masked FLUX pass on the whole puppet at denoise 0.85 (the mask = the zones grown by `bulk`, on the zone
  parts only) → the item = key-coloured pixels + the dark line next to them, touching the zones, small holes filled
  (glints) → **cover**: zone pixels it left bare are filled from its own levels → one even outline → split per part
  (topmost zone part; past the body = nearest zone part; each part's hidden zone pixels smear-filled from its own
  layer) → **FLUX hidden pass** (`complete_hidden`): the item's own pieces only (no body: with bare limbs around them
  FLUX paints the hidden ends as skin) laid out on one sheet, only the hidden areas masked, denoise 0.55; key-coloured
  results replace the smear → the back view with the painted front as reference.
- **Materials = colour keys** (`materials` in the spec: cloth green, leather blue, metal cyan, accent magenta). The
  levels map is R = cel level 0..4 / 254 outline, G = material channel (4..7), A, like the puppet export; any ramp of the
  material's palette group colours it (iron / bronze / gold plate from one generation).
- **Held item** (`kind: held`): txt2img on white (or redrawn from its design reference, below) + BiRefNet, keyed the same way, rotated onto the hand bone at its grip
  (the leather's middle) and drawn under the hand so the fingers wrap it; mirrored for the back view.
- Output: `sprites/ai/items/<race>/<item>/<cid>/` (`paint_<view>.png`, `layer_/levels_<view>.png`,
  `parts/<view>/<part>.png`, `plevels/<view>/<part>.png`, `meta.json`). `dressed(build, worn, view, hair)` /
  `render(...)` stack items by `depth` for previews; `clips: ["hair"]` cuts the hair layer under the item.
- Learned on the helm: a straight zone edge becomes a straight item edge (use curved zones); a soft guide or a high
  denoise lets FLUX draw the item smaller than the chibi head, so the bald skull shows around it.
- **Design reference (2026-11-01):** approved **Limner › Gear** art steers the item's design (Items tab › Design,
  spec `design`). Auto: the icon of the piece's game item, else the slot × material fallback (`slot.body.plate`;
  metal or the words steel / iron / plate → plate, else leather, else cloth), else the weapon type; pick any approved
  gear entry, or `none`. Fitted: the master (on white, saved as `design_ref.png`) rides the **front** pass as a
  ReferenceLatent, plus `prompt_design` ("copies the design ... but painted only in the colour keys"); the back still
  follows the front. Held: the master is redrawn upright in the keys (`build_edit` + `prompt_held_design`, BiRefNet)
  instead of txt2img. `meta.design` records entry, candidate and why. Tested 2026-11-01: belts, plate segments, straps
  and rivets carry over while the puppet's style, the zone and the description win on shape (a closed great-helm
  icon still gives an open-face helm). A blind "recolour the icon into keys" edit was tried and dropped: FLUX picks
  the materials at random. Details in a material the item doesn't list (a tunic's leather belt) are dropped by keying:
  add the material.

### Game items and export (A2, 2026-10-26)
- **The game's vocabulary wins (R-001):** `hdart/contract.py` reads the active project's `ArtContract/requirements.json`
  (never edits it): slots and their `artSlots`, weapon types (`heldAs`, `hands`), item kinds, the id rule, its
  `revision`; and the game's item ids from its content (`Content/*/items/*.json`). A piece's **slot** is one of the
  game's art slots (`head, shirt, chest, shoulders, skirt, legs, feet, gloves, back, mainhand, offhand`); held items are
  held as `weapon`, `shield` or `focus` (tomes, orbs, holy symbols: face-on like a shield, own prompt
  `prompt_held_focus`). New ids follow the game's rule (`item.<lowercase_words>`, no slot or kind in the id; R-002).
- **Pieces and game items:** a Workshop entry is a **piece**; its `item` names the game item it belongs to (default:
  its own id), so several pieces make one game item (`item.breastplate` + `item.pauldrons` → `item.iron_breastplate`).
  The Items tab shows the game item under Slot ("in the game" = art only; "new" = the export writes an intake file).
  A new item's intake fields come from `game_items` in `specs/items.json` (name, kind, slot, weaponType, one-line text,
  materials → default ramps), else from its pieces; items the game has take them from the game's content.
- **Export** (`hdart item-export`, or **Export to game** / **Export all** on the item card): per game item whose every
  piece is approved for a build, `Art/Items/<id>/item.json` + layers (coloured with the default ramps) + `.idx.png`
  index maps (R = cel level 0..4 / 254 outline, G = channel 4 cloth / 5 leather / 6 metal / 7 accent, A). `fits` is
  keyed by build (`human.male`, ...), then view: layers `{piece, part, file, rect, depth, place, clips}`, `rect` in the
  puppet canvas (same space as `puppet.json`), `place` over (fitted: above its part) or under (held: below the hand,
  placed on that build's grip). `held` keeps each weapon's upright art and grip settings; `source` the candidates, the
  tool's git sha and the `requirementsRevision`. Every export (item, puppet, dungeon) rewrites `Art/index.json` (all
  packs on disk); item and puppet exports also write `Art/ramps.json` (every group, ids `<group>.<name>`) and
  `Art/channels.json`. The pack folder is rebuilt whole. Test: `tests/test_item_export.py`.

## Limner: icons and hero portraits (`/limner`, 2026-11-01)
Hamburger menu → **Limner**. Tabs: Items (every base item the game wants, prompted placeholders until real item art),
Gear (cloth/leather/plate per armour slot + one per weapon type), Classes, Statuses, Damage, Skills (placeholders per
damage type × active/passive until the class waves) and Portraits (archetype × look × male/female, the static set
players pick from). Lists come from `ArtContract/requirements.json` → `wanted.icons` + `specs/limner.json`.

- **Layers you can tune, each on its own** (`specs/limner.json`, in git): *styles* (prefix/suffix words, pixel LoRA;
  the portrait house style is `semireal`, reference `specs/limner_refs/semireal.jpg`), *categories* (default style,
  extra words, size, steps, the finish), *entries* (Eric's subject, style, seed, finish/crop overrides), *archetype
  looks* (each look = a male and a female portrait).
- **Candidates:** `sprites/limner/<cat>/<id>/<cid>/` with `master.png` (kept), `icon.png` or `portrait.png` +
  `thumb.png`, and `meta.json` (exact prompt, seed, style, batch, variation). Approvals in `sprites/limner/state.json`.
- **Finish without the GPU:** icon px, colours (0 = smooth), margin, outline; portrait width, thumb, face crop. Saving
  re-finishes every candidate. **Vary** = img2img from a pick with the current subject; **Seed** = keep the composition.
- **Overnight batches** (Batch…): entries × styles × variation lines, n candidates each, random seeds or a sweep. One
  small job each, `retries: 2`, a per-image timeout (a stuck render is dropped from ComfyUI and retried). Queuing a
  batch starts the **watchdog** (`hdart/watchdog.py`, also `uv run python -m hdart.watchdog`): keeps Windows awake
  while jobs remain, restarts a dead Studio or ComfyUI, interrupts a job stuck > 15 min, exits 10 min after the queue
  empties; log `work/watchdog.log`. A Studio restart re-queues the interrupted job when it has retries left.
- **Review:** tabs show `+N` new candidates; Show → *new*; in an entry ← → next/previous, 1–9 approve; *Mark
  reviewed* when done.
- **Export:** approved only → `Art/Icons/<cat>/<id>.png` + `index.json` (id → file); portraits →
  `Art/Portraits/<id>.png` + `<id>.thumb.png` + `index.json` (id → file, thumb, archetype, gender, look). Each
  folder is rebuilt whole.
- Code `hdart/limner.py`, page `studio/limner.html`, test `tests/test_limner.py`.

## Legacy (retired 2026-10-25)
The first character directions are retired: the Blender modular character kit, the AI sprite kit v3, the hand-drawn
2D JRPG kit with voxel sheets, and their Unity side (`CharacterComposer`, `CharacterKit`, `CharacterArtImport`,
Prototype C, removed in Hearthdelve `3ca39de`). Their CLI commands (`kit-build`, `kit-render`, `sprite-kit`,
`sprite-export`, `ai-base`, `ai-part`, `ai-kit`, `compose`) still exist, but they write into `Assets/Art/...`, which no
Unity code reads any more. Don't run them for the game. The same goes for `tiles`, `icons`, `mesh-render` and `vfx`
(`Assets/Art/Tiles|Icons|Monsters|VFX`), until they're moved onto the art packs (vault: `Art - Unity Handoff`).
Their old documentation is in this file's git history (before this change) and in the vault's
`Game Dev/Art Pipeline/Logs/Hearthdelve - Art Pipeline v1 (2026-10-25).md`.

## Known limits / next steps
- Texture prompts sometimes produce a 2×2 repeat (visible cross); re-roll with `--seed` or reword.
- TRELLIS meshes are static: monsters get sprites without animation. Rigging (Blender auto-weights onto a
  generic rig, or hand rigging) is the next step for animated monsters.
- Floors map a little brighter than wall faces; tune the stone ramp or the light in `tiles_crypt.json`.
