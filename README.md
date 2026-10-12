# Hearthdelve art pipeline (`hdart`)

Local, scriptable generation of the game's art: modular characters, environment tiles, monsters/props,
item and spell icons, and spell VFX. Everything runs on this PC (RTX 4080); nothing goes to a cloud service.
Design notes and decisions live in the vault: `Game Dev/Hearthdelve/Hearthdelve - Art Pipeline.md`.

**Location:** `P:\Tools\art-pipeline`, its own git repo (moved out of the Hearthdelve repo's `Tools/ArtPipeline` on
2026-10-12; history before that is in the Hearthdelve repo). Output goes into the Unity project at `HD_PROJECT`,
by default the root checkout `P:\Projects\Unity\Hearthdelve` (see `hdart/paths.py`). Set `HD_PROJECT` to point a run
at a worktree instead. Paths like `Assets/...` below are in that project.
Git holds only the tool and its workflow (code, specs, docs). Generated art (`sprites/`, `textures/`, `kit/`, `work/`,
`archive/`) stays local: approved art is exported into the game project, and the rest can be thrown away or regenerated.

```
ComfyUI (FLUX.2-klein 4B + pixel LoRA, TRELLIS.2, BiRefNet)      Blender 5.2 (headless, Cycles)
   textures ──────────────┐                                         character kit (.blend) ─► layer sheets
   icons ─► pixelize      ├─► textures/<set>/*.png ──────────────►  tiles  ─► iso tiles + normals
   concept ─► TRELLIS.2 ─► GLB ─────────────────────────────────►  mesh-render ─► monster/prop sprites
                                                   numpy: vfx flipbooks, compositor, previews
```

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
| /dungeon | **Dungeon Forge**: dungeon sets. Pieces (walls, floors, doors, archways, wall decor, props, traps) → candidates → approve → room preview → export to Unity → Prototype V capture | `studio/dungeon.html`, `hdart/dungeon.py`, `hdart/iso.py`, `blender/mesh_template.py` | `specs/dungeon_sets/<set>.json` (pieces), `specs/dungeon_style.json` (descriptors + catalog), `sprites/dungeon/<set>/state.json` + `candidates/`, Blender mesh cache `sprites/dungeon/_meshes/` |

3. The generation **queue** (top right in Workshop/Dungeon) runs one job at a time, because ComfyUI is single-GPU anyway. Jobs live in memory, so a restart
   clears the queue, but every result is already on disk.
4. Scripted use (what earlier sessions did): the same HTTP API the pages use, e.g.
   `POST /api/dun/generate {"set","piece","n","seed","denoise?","lora?"}` → job id; `GET /api/ws/jobs`;
   `POST /api/dun/approve {"set","piece","rel","on"}`; `GET /api/dun/room?set=crypt&tile=64&scale=2` (PNG);
   `POST /api/dun/export {"set"}`; `POST /api/dun/capture {"set"}` (job). Or call `hdart.dungeon` / `hdart.workshop`
   directly from `uv run python` (no server needed, but don't run two generations against ComfyUI at once).
5. Unity: in the root project, Hearthdelve → **Scene Launcher** (Ctrl+Shift+L). Dungeon art shows in **Prototype V**;
   `hdart dungeon-capture crypt` exports and screenshots it in batch (needs the project *closed* in the editor,
   or point `HD_PROJECT` at a worktree checkout).

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
| `hdart ai-base` / `ai-part` / `ai-kit` | AI character kit from the CLI (the Workshop is the curated way) | `sprites/ai/` |
| `hdart dungeon-export [set]` | Approved dungeon pieces → Unity (format 2, with normal maps) | `Assets/StreamingAssets/Art/Dungeon/<set>/` |
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
- **Wall masters: Rescale bricks** (image panel; `rescale_wall_master`). The kept cap and base (**keep top** / **bottom**, units; default 0.25 / 0.1, tinted in the **keep bands** view, `elevation_bands`) stay exactly as they are. The field between them is rescaled (`fx` = brick width, `fy` = height; target **rows** / **bricks across**, or ×½ … ×2 both ways), counted up from the base and snapped to whole courses (it starts on the field's lowest joint, stacks only whole courses, and fy moves slightly so none is cut under the cap). The joins are repainted, then the sideways seam is fixed only where pixels changed. `measure_elevation` counts the courses and the bricks per tile from edge strength (exact on templates, ≈ on painted walls). Templates for wall families also take **rows** (sets course).
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
1. **Mannequins** tab: generate and approve the male/female/slim base (front, then a back made from the mirrored front, clamped to the
   same silhouette so the card's two sides line up).
2. **Board**: each cell is part × build. Generate candidates, approve a raw image (its back is queued automatically), then **Cut**:
   auto threshold/speck, brush keep/remove, magic wand, undo, native-grid QA. **Fix region**: paint an area, prompt a
   correction, and FLUX redoes only that area. Approve → the kit rebuilds.
3. **Style** tab: the descriptor groups (`specs/ai_style.json`). **Sets**: save the approved parts as a named set; the Studio can switch sets.
4. Cards: backs whose file name has `_card_` are silhouette-matched. Older backs show as `redo-back` on the board.

## Status and next steps (2026-10-11, for whoever picks this up)
- **Dungeon:** crypt v2 approved and exported. Not done:
  - 2D shadow casters on thin walls;
  - archways split front/back so characters walk *through* them;
  - P2's real board (`Presentation/Board/BoardView.cs`) doesn't use the art yet (only Prototype V does);
  - rubble reads as plain floor;
  - more biomes (**+ New set** copies the crypt pieces with a `board` id so Prototype V can show them).
- **Characters:**
  - regenerate the female front (it has a ground shadow) and redo the backs flagged `redo-back`;
  - a relief/normal map so eyes sink and held items stand out;
  - true-3D weapons and shields;
  - a Unity composer for the AI kit / voxel cards (the witch test is a one-off: `Assets/Art/VisualTests/Witch/`).
- **Open decision:** 2D vs 3D renderer before P6. Normal-mapped 2D lights work now; the voxel card test uses a 3D renderer.
- Vault: `Game Dev/Hearthdelve/Hearthdelve - Art Pipeline.md` has the decision history and screenshots.

## Target look (pixel art, low native resolution)
Matches the reference art in Architecture §9.1 (Octopath/HD-2D sprites, GB-style characters, low-res iso rooms):
everything is rendered at **low native resolution** and shown at an integer upscale (×3–×6).

| Asset | Native size | Colour |
|---|---|---|
| Iso tiles | 32×16 diamond (canvas 32×64) | biome ramps (8 colours per material), gradient-mapped, inner outlines + top rims |
| Board tokens | 32×32 cell, hero ≈ 25 px tall | palette ramps per channel (5 colours), outline |
| Battle sprites | 48×48 cell, hero ≈ 36 px tall | same |
| Monsters / props | 48×48 | ~14 colours |
| Icons | 32×32 | 14 colours + outline |
| Spell VFX | 48×48 | element ramp |

Knobs: `views.*.cell` and `render.detailRange/coverage` in `character_kit.json`; `texturePixels`, `texColors`,
`ramps` in `tiles_crypt.json`; `hdart tiles --tile 64 --smooth` brings back the higher-res look for comparison.

## AI sprite kit (v3, current): FLUX draws the parts, the kit keeps them modular
1. `uv run hdart ai-base [--bodies slim]` generates the base mannequin (front 3/4 + back 3/4) with
   FLUX.2-klein + pixel LoRA (`sprites/ai/base/`).
2. Every part is an **edit** of that base (`specs/ai_parts.json`: what to add). The changed pixels (tolerant
   diff, slot area, speck cleanup) become the part, kept at 512 px in `sprites/ai/layers/`, then
   converted to index-palette templates for **every grid resolution** (`sprites/parts/r32 … r256/`).
   Colours are mapped to recolourable channels (hair, skin, eyes, cloth1/2, leather, metal, accent, wood,
   magic, dark) with 5 shade steps, so palettes still apply.
   - `uv run hdart ai-kit [--only hair.long,torso.robe]` (re)generates parts from the library
   - `uv run hdart ai-part torso.coat --prompt "a long green coat with brass buttons"` adds one
   - `uv run python -c "from hdart.ai_kit import reextract; reextract('slim')"` re-cuts from saved raw edits
3. Studio: grid dropdown (32/64/128/256) and a **Generate part** panel (slot, name, prompt, seed).
   `uv run hdart sprite-kit --res 128` bakes a resolution for Unity.
`specs/sprite_kit.json` → `templatesOnly: true` keeps the code-drawn parts out (they're 64 px only).

## Working on the sprites yourself (2D JRPG kit + Sprite Studio)
The current character direction (2026-10-11) is **2D pixel-art sprites** in an Octopath-like JRPG style
(64×64 cells, chibi, stubby 2 px legs that hobble), shown as a **voxel sheet**: every pixel is a voxel with a
1×1 front and 0.25 depth. References: vault `Game Dev/Hearthdelve/References/`.

1. **Studio**: `start-art-tools.cmd` (or `uv run hdart studio`) → http://127.0.0.1:4321. Pick build, parts and colours (or Randomize),
   choose view/row to play animations, orbit the live voxel sheet, tune thickness/relief/lights, Save PNG.
2. **Paint parts**: every part is a set of PNG layers in `sprites/parts/<body>/<part>/`, named
   `<facing>.<mainArm>-<offArm>.<layer>.png` (facing = front/back; arm poses = down/raise/forward).
   - Open one in Aseprite/Photoshop/GIMP, load the palette `sprites/index_palette.gpl` (or `.png`), and paint
     **only with palette colours**: each colour means "colour channel + shade step" (e.g. `hair 3`), so the
     hero can still be recoloured.
   - Keep the anchors: feet on row 61, head centred near x 31; parts must line up with `body.base`.
   - Click **Rebuild kit** in the Studio. Painted files override the code drawing; delete a file to fall back.
   - A pose file you don't paint falls back to the `down-down` one. Legs hobble automatically (left half / right half of the canvas).
3. `uv run hdart sprite-export [--bodies slim,broad] [--parts hair.long] [--overwrite]` regenerates templates
   from the code drawings (`hdart/sprite_kit.py`); it never overwrites painted files unless `--overwrite`.
4. `uv run hdart sprite-kit` bakes everything into `Assets/Art/Characters` for Unity.

## Modular characters
Every hero is a stack of **parts**, one per slot: body, face, hair, facial, head, torso, legs, feet, hands,
back, mainhand, offhand. All parts share one rig per body type (slim, broad, short), so any combination
works and animates together.

1. `blender/build_character_kit.py` builds bodies (skin-modifier tubes), clothing shells (the same skeleton,
   inflated), rigid parts (hair, hats, weapons bone-parented to head/hands) and procedural clips
   (token: idle, walk × 4 directions; battle: idle, attack, cast, hurt, dead).
2. `blender/render_character.py` renders each part **alone** with an emission-only "data" material:
   - `*.data.png`: R = shade (form light × material texture detail), G = colour channel id, B = depth, A = coverage
   - `*.normal.png`: camera-space normal for URP 2D lights
3. The compositor (`hdart/compose.py`, and `CharacterComposer.cs` in Unity) picks the nearest layer per pixel
   (depth test), maps shade onto the chosen **palette ramp** for that channel (skin, hair, eyes, cloth1,
   cloth2, leather, metal, accent, magic…), and adds outlines and inner depth lines.

So hair, faces, outfits and colours are all independent choices, and changing one is a recomposite
(milliseconds), not a re-render. `hides` in the spec handles conflicts (a hood hides hair, a robe hides legs).

**Adding a part by hand**: open `kit/character_kit.blend`, model on top of a `rig.<body>` (skin with an
Armature modifier, or bone-parent rigid pieces), set custom properties `hd_part` and `hd_body`, assign
materials named `hd.<channel>`, add the part to `specs/character_kit.json`, run `kit-render --parts <id>`.
Once the kit has hand edits, never run `kit-build --force` again on it.

**Adding colours**: edit `specs/palettes.json` (a `base` colour is auto-ramped; or give 5 explicit colours).

## Unity side
- `Assets/_Project/Editor/CharacterArtImport.cs`: import rules for `Assets/Art/**` (point filter, uncompressed;
  character sheets readable and linear) and **Hearthdelve → Art → Build Character Kit Asset**.
- `Presentation/Characters/CharacterKit.cs` + `CharacterComposer.cs`: compose a `HeroAppearance`
  (Rules/Content) into sprites with the normal map attached as `_NormalMap`.
- Prototype: **Hearthdelve → Prototype C → Set Up Character Creator Scene**, then Play.
  Batch screenshots: `Unity.exe -batchmode -projectPath <proj> -executeMethod Hearthdelve.Editor.CharacterCreatorSetup.Capture -reviewOut <dir> -quit`.

## Known limits / next steps
- Texture prompts sometimes produce a 2×2 repeat (visible cross); re-roll with `--seed` or reword.
- TRELLIS meshes are static: monsters get sprites without animation. Rigging (Blender auto-weights onto a
  generic rig, or hand rigging) is the next step for animated monsters.
- Floors map a little brighter than wall faces; tune the stone ramp or the light in `tiles_crypt.json`.
