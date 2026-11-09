# Hearthdelve art pipeline (`hdart`)

Local, scriptable art generation for the game: painted 2D cutout puppets (Character Workshop), equipment layers for
them (Item Workshop), dungeon art sets (Dungeon Forge) and icons + portraits (Limner). Everything runs on this PC
(ComfyUI with FLUX.2 klein on the GPU); nothing goes to a cloud service.

This README is **how to run and use** the tools. What each tool is, how it works, its status, open items and decisions
live in the vault, `Game Dev/Art Pipeline/`:
- `Art Pipeline.md` (hub), `Art Pipeline - Status.md` (read first), `Art Pipeline - Roadmap.md`
- one note per tool: `Art - Character Workshop.md`, `Art - Item Workshop.md`, `Art - Dungeon Forge.md`, `Art - Limner.md`
- `Art - Pipeline Architecture.md` (layers, store, jobs, ops, what was built when)
- per project the handoff contract and asset plan (`Hearthdelve/Art - Unity Handoff.md`, `Art - Asset Plan.md`);
  requests between the game and the tools go through `Game Dev/Hearthdelve/Hearthdelve - Requirements.md`
- the long pre-split design history: `Logs/Hearthdelve - Art Pipeline v1 (2026-10-25).md`

Working on the code: start with `AGENTS.md` (this folder) and the one in the area you touch.

Git holds the tool and its workflow only (code, specs, docs). Generated art (`sprites/`, `work/`, `archive/`) stays
local: approved art is exported into the game project, the rest can be thrown away or regenerated.

## Setup (once per machine)
| Piece | Where | Notes |
|---|---|---|
| hdart | this folder (`P:\Tools\art-pipeline`) | `uv sync`, then `uv run hdart --help` |
| ComfyUI v0.39 | `P:\Tools\ComfyUI` | uv venv (Python 3.12, torch 2.14.1+cu130), `start-comfyui.cmd` → http://127.0.0.1:8188 |
| Models (~24 GB) | `P:\Tools\ComfyUI\models` | table below; file names in `config.toml` `[models]` |
| Blender 5.2 LTS | `C:\Program Files\Blender Foundation\Blender 5.2\` | `winget install BlenderFoundation.Blender`; only for dungeon archway meshes. Override with `HD_BLENDER` |
| Node | on PATH | only for `tests/test_ramps.py` (checks the page's ramp JS against Python) |

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

## Start
`start-art-tools.cmd` starts **ComfyUI** (:8188) and the **Studio** (:4321), each in its own minimized window. It's
safe to run again: whatever is already up is left alone. Check: `curl http://127.0.0.1:4321/api/ws/jobs`.
- Browsing works without ComfyUI; generating needs it. The first generation after a start loads the models (~1 min).
- The Studio window runs a **supervisor**: when the code changes (an edit, a `git pull`) it restarts the server by
  itself, after the running job has finished; queued jobs carry on. A change that doesn't import is refused and the
  old server keeps running. Restart by hand: `uv run --no-sync hdart op call studio.restart`. Log: `work/studio.log`.
  `hdart studio --no-supervisor` runs the server alone.
- Overnight batches start the **watchdog** (`uv run python -m hdart.server.watchdog`): keeps Windows awake while jobs remain,
  restarts a dead Studio or ComfyUI, interrupts a job stuck > 15 min. Log: `work/watchdog.log`.

| URL | Tool | Code | Specs (git) | State / candidates (data root) |
|---|---|---|---|---|
| /workshop | **Character Workshop**: tabs Template → Race (mannequins, Paint per sex) → Face (hair, faces) → Ramps | `studio/workshop.html`, `studio/js/workshop/`, `hdart/tools/character/` | `races.json`, `paint.json`, `face.json`, `puppet_template.json`, `palettes.json`, `ai_style.json` | `sprites/ai/workshop.json`, `sprites/ai/paint/<build>/`, `sprites/ai/face/<race>/`, `sprites/ai/puppet_template/` |
| /items | **Item Workshop**: Items (settings, zone editor, Grip card, generate, candidates), Outfit | `studio/items.html`, `studio/js/workshop/`, `hdart/tools/items/` | `items.json`, `item_zones/` | `sprites/ai/items/<race>/state.json` + `<item>/<candidate>/` |
| /editor | **Workshop Editor**: every layer of a painted puppet and what it wears (paint, bands, AI fix) | `studio/editor.html`, `studio/js/workshop/editor.js` | | the candidates above |
| /dungeon | **Dungeon Forge**: sets of floors, walls, doors, props → room preview → export | `studio/dungeon.html`, `studio/js/dungeon/`, `hdart/tools/dungeon/`, `blender/mesh_template.py` | `dungeon_sets/<set>.json`, `dungeon_style.json`; `library/` | `sprites/dungeon/<set>/state.json` + `candidates/` |
| /limner | **Limner**: icons (items, gear, classes, statuses, damage, skills), portraits, monster/NPC tokens | `studio/limner.html`, `studio/js/limner.js`, `hdart/tools/limner/` | `limner.json`, `limner_refs/` | `sprites/limner/state.json` + `<cat>/<id>/<cid>/` |

`/` opens the Character Workshop. The ☰ menu top left switches tools. Every prompt is editable next to the button that
generates with it. The style groups in `specs/ai_style.json` have no editor: edit the file.

**The queue** (top right of each page) runs one job at a time (ComfyUI is single-GPU) and is saved
(`sprites/studio_jobs.json`): after a restart queued jobs run again, and a job that was running shows "interrupted"
(re-queued only if it had retries left). ✕ cancels a job (a running one interrupts ComfyUI). Before a GPU job the queue
waits while ComfyUI is busy with someone else's work.

**Removing = archiving.** ✕ on a candidate, Archive unapproved, Remove piece, Delete family, Archive set: everything
moves to `archive/` (same relative paths; git-ignored). Dungeon spec saves keep the last 40 versions in
`archive/history/<set>/`. Empty `archive/` by hand when sure.

## Using each tool (short)
Design, pipeline steps and findings are in the tool's vault note; this is the click path.
- **Character Workshop.** Template: drag joints and outline points over the approved unisex art, Save (validates the
  spec), *Save & render preview*. Race: generate and approve the unisex front (the back is queued from it), then
  **Paint** per sex (approve one per build). Face: hair and faces as layers, *Add to the set*. Ramps: skin / hair / eye
  ramps per race (Monk presets, sliders), *Add this ramp to the set*. Touch-ups: **Edit** on a candidate opens the
  Workshop Editor. Export: `hdart puppet-export human`.
- **Item Workshop.** Items tab: pick a piece, set slot / game item / kind (fitted or held) / materials, paint its zone
  (and Details sketch, key D), **Generate**, approve per fit (per-sex pieces per build). Held pieces: tune the grip in
  the Grip card. Batches: *Queue all Limner gear*, *Queue gear sets* (first click shows the plan, the second queues).
  Outfit tab: dress a build by the game's slots. **Export to game** / `hdart item-export`.
- **Dungeon Forge.** Pick or make a set, then per family: Template → Master (draft masters are fast; *Finalize draft*)
  → Variations (matrix; *Fill gaps*, *Review (N)*: A approve, X archive) → walls: Doors → Build → Export. Material sheet,
  recipes (`library/recipes/`) and the CC0 materials library are in the left column. `hdart dungeon-export <set>`;
  `hdart dungeon-capture <set>` screenshots Prototype V (Unity batch; needs the project closed in the editor, or
  `HD_PROJECT` at a worktree).
- **Limner.** Tabs per category; per entry the subject, style and finish; *Batch…* for overnight runs (entries × styles
  × variation lines); review new candidates (← → step, 1–9 approve, *Mark reviewed*); *Export* approved only.

## Commands
| Command | Does | Output (in the active project) |
|---|---|---|
| `hdart studio [--no-browser]` | the web tools (or `start-art-tools.cmd`) | http://127.0.0.1:4321 |
| `hdart puppet-export [race] [--size 512]` | a race's painted puppet, hair and face sets, skin/hair/eye ramps, with index maps | `Assets/StreamingAssets/Art/Puppets/<race>/` |
| `hdart item-export [ids] [--race human] [--size 512]` | approved game items as art packs, plus an intake file per id the game doesn't have yet | `Assets/StreamingAssets/Art/Items/<id>/`, `ArtContract/intake/items/<id>.json` |
| `hdart export [--check]` | puppet + every item; `--check` only validates `Art/` and the intake files (exit 1 on a problem) | |
| `hdart dungeon-export [set]` | a set's approved pieces (format 2, normal maps) | `Assets/StreamingAssets/Art/Dungeon/<set>/` |
| `hdart dungeon-capture [set]` | export + Prototype V batch screenshots | `work/protov/` |
| `hdart op list [prefix]` / `op show <name>` / `op call <name> key=value ... [--out img.png]` | any op of the running Studio (values are JSON when they parse, else text) | |
| `hdart mcp` | the MCP server for agents (stdio), over the running Studio | |
| `hdart backup [--dry-run] [--dest dir]` | state, layers, approved dungeon candidates and specs to a dated folder | `P:\Archive\art-pipeline-backups\<date_time>\` |

Every export rewrites `Art/index.json` (all packs on disk); item and puppet exports also write `Art/ramps.json` and
`Art/channels.json`. Each pack folder is rebuilt whole. Formats: vault `Art - Unity Handoff.md`.

**Scripted use (HTTP).** Ops (`hdart/core/ops.py`) are the typed, documented API: `GET /api/ops[?prefix=items.]` lists them
with parameters, `POST /api/op/<name>` with a JSON object runs one (bad arguments → 400), and read-only ones also run
over `GET /api/op/<name>?arg=value`. Every tool is on ops: `items.*`, `character.*`, `dungeon.*`, `limner.*` (e.g.
`dungeon.generate` → `{"job": id}`, `dungeon.review`, `dungeon.approve`, `dungeon.export`), and `jobs.*` (`jobs.list`,
`jobs.wait`, `jobs.cancel`). Image ops return the PNG itself. The pages call the ops directly (`studio/js/api.js`); the older
routes (`/api/ws/<action>`, `/api/dun/<action>`, `/api/lib/<action>`, `/api/limner/<action>`) stay as aliases of ops. From a shell:
`uv run hdart op list dungeon.`, `uv run hdart op call dungeon.review set=crypt piece=tile.crypt_floor`.

**Agents (MCP).** `uv run hdart mcp` is an MCP server (stdio) with task-level tools over the same ops, the same verbs
for every tool (`tool` = character, items, dungeon, limner): `state`, `candidates`, `view_candidate` (a downscaled
image), `generate`, `approve`, `archive`, `get_spec` / `patch_spec` (a JSON merge patch, validated by the tool's save
op); `jobs_list`, `jobs_wait`, `jobs_cancel`, `export`, `export_check`; and `list_ops` / `describe_op` / `call_op` for
any other op. It calls the **running Studio** over HTTP (one queue, one lock owner), so start the Studio first; its URL
is `config.toml` `[studio] url` (env `HD_STUDIO_URL` beats it). Claude Code picks it up from `.mcp.json` in this repo
(project scope: approve it once when asked; it runs with `--no-sync` because the running Studio holds
`hdart.exe`, so after a dependency change run `uv sync --no-install-project --inexact`). To add it by hand instead:
`claude mcp add --scope project hdart -- uv run --quiet --no-sync --directory P:/Tools/art-pipeline python -m hdart.cli mcp`.

## Config, tests, backups
- **`config.toml`** (git): ComfyUI URL and timeout, model names, the Blender exe, the **data root** (`paths.data`,
  where `sprites/`, `work/`, `archive/` live; empty = this folder), the backup folder, and a **profile per project**
  (path, Unity exe, contract folder, export roots). Machine overrides go in `config.local.toml` (git-ignored, same
  keys). Env vars beat both: `HD_PROJECT`, `HD_PROFILE`, `HD_COMFY_URL`, `HD_DATA`, `HD_SPECS`, `HD_BLENDER`, `HD_UNITY`,
  `HD_STUDIO_URL` (the Studio `hdart op` and `hdart mcp` call).
- Output goes to the active profile's project (Hearthdelve: `P:\Projects\Unity\Hearthdelve`, the root checkout). Set
  `HD_PROJECT` to aim a run at a worktree.
- **Tests:** `uv run python tests/run_all.py [name-filter]` (each test in its own process, ~10 min), or one file:
  `uv run python tests/test_comfy.py`. No GPU: a fake ComfyUI (`hdart/core/fake_comfy.py`) and a closed ComfyUI port. Tests
  read a frozen copy of the specs (`tests/fixtures/specs/`), never the live `specs/`. `test_harmonize` and
  `test_puppet` read real approved art (they skip or need `HD_DATA=P:\Tools\art-pipeline` from a worktree).
- **Backups:** `uv run hdart backup` before risky work. Scratch, unapproved dungeon candidates, `archive/` and `work/`
  are left out. Old backups aren't pruned.

## Where things are
| Path | What |
|---|---|
| `hdart/` | the Python package: `core/`, `imaging/`, `tools/<tool>/`, `export/`, `server/`, `cli.py` (see `hdart/AGENTS.md`) |
| `studio/` | one page per tool, its ES modules (`js/`) and styles (`css/`); see `studio/AGENTS.md` |
| `specs/` | everything Eric tunes in the tools: prompts, races, ramps, items, zones, dungeon sets, Limner (git) |
| `library/` | dungeon recipes and CC0 materials (`materials/*.json` in git, `cache/` downloads git-ignored) |
| `blender/` | the archway mesh script `hdart/tools/dungeon/geometry.py` runs in Blender |
| `tests/` | script-style tests + `run_all.py`; `fixtures/` frozen spec copies |
| `sprites/`, `work/`, `archive/` | generated data (git-ignored; under `paths.data`) |

The retired tools (sprite kits, the part board, `Assets/Art` CLI writers, the old `/` page) were removed in A10; their
code is in git history and their design in the vault's v1 log. Local data they made (`kit/`, `textures/`,
`work/sprite_render*`, `sprites/parts/`, `sprites/sets/`, `sprites/characters/`) can be deleted by hand.
