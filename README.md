# Hearthdelve art pipeline (`hdart`)

Local, scriptable generation of the game's art: modular characters, environment tiles, monsters/props,
item and spell icons, and spell VFX. Everything runs on this PC (RTX 4080); nothing goes to a cloud service.
Design notes and decisions live in the vault: `Game Dev/Hearthdelve/Hearthdelve - Art Pipeline.md`.

**Location:** `P:\Tools\art-pipeline`, its own git repo (moved out of the Hearthdelve repo's `Tools/ArtPipeline` on
2026-10-12; history before that is in the Hearthdelve repo). Output goes into the Unity project at `HD_PROJECT`,
by default the root checkout `P:\Projects\Unity\Hearthdelve` (see `hdart/paths.py`). Set `HD_PROJECT` to point a run
at a worktree instead. Paths like `Assets/...` below are in that project.

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
- **Removing things = archiving** (nothing is deleted; `archive/` is git-ignored, empty it by hand when sure):
  - ✕ on a candidate, **Archive unapproved**, and **Remove piece** move images to `archive/dungeon/<set>/` (same relative paths).
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
