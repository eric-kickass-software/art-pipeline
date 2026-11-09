# AGENTS.md: art pipeline (`hdart`)

Local art tools for Hearthdelve: one Python package (`hdart/`), one web server (the Studio, :4321) with its pages
(`studio/`: HTML + native ES modules, no build step), ComfyUI (:8188) on the GPU. How to run it: `README.md`. Design and status: the vault,
`C:/Users/eric/Documents/Notes/Game Dev/Art Pipeline/` (`Art Pipeline - Status.md` first, `Art - Pipeline
Architecture.md` for the layers, one `Art - <tool>.md` per tool). Record decisions there, not here.

Per area: `hdart/AGENTS.md` (modules, state, invariants), `studio/AGENTS.md` (pages), `tests/AGENTS.md` (tests).

## Layout
| Path | Owns | Notes |
|---|---|---|
| `hdart/` | all code: `core/`, `imaging/`, `tools/{character,items,dungeon,limner}/`, `export/`, `server/`, `cli.py` | one AGENTS.md per package |
| `studio/` | one page per tool (`workshop.html`, `items.html`, `editor.html`, `dungeon.html`, `limner.html`), `js/` modules, `css/` | no build step; pages call ops via `js/api.js` |
| `specs/` | tunable specs, in git | Eric edits these through the Studio; see below |
| `library/` | `recipes/*.json`, `materials/*.json` (git), `cache/` (git-ignored downloads) | Dungeon Forge |
| `blender/mesh_template.py` | archway meshes + normal pass | run by `hdart/tools/dungeon/geometry.py` via `config.blender()` |
| `tests/` | script-style tests, `run_all.py`, `fixtures/` | no GPU |
| `.mcp.json` | the `hdart` MCP server for Claude Code (project scope) | `uv run hdart mcp`; needs the Studio running |
| `config.toml` / `config.local.toml` | settings / machine overrides (git-ignored) | read via `hdart/core/config.py` |
| `sprites/`, `work/`, `archive/` | generated data under the data root (`config.data_dir()`) | git-ignored, Eric's live data |

## Specs (`specs/`, in git, owned by Eric)
`races.json`, `paint.json`, `face.json`, `puppet_template.json`, `palettes.json`, `ai_style.json` (Character
Workshop); `items.json` + `item_zones/<item>[.<sex>].<view>[.sketch].png` (Item Workshop); `dungeon_sets/<set>.json`,
`dungeon_style.json` (Dungeon Forge); `limner.json`, `limner_refs/` (Limner).
- Eric edits them live in the Studio. Uncommitted changes in `specs/` are usually his: commit them separately with a
  message saying so, never revert them.
- Code reads them through `config.specs_dir()` (env `HD_SPECS` overrides; tests use it). `<spec>.lock` files beside
  them are the store's cross-process locks (git-ignored).
- Item zone rules (`items.json` → `zones`): `part` (fnmatch on part names), `t` (range along the bone), `rows` /
  `ellipse` (fractions of the part's box), `seam` (above/below the waist), `view`, `cut: true` (removes the area).

## Invariants (all areas)
- **Never touch Eric's live data** (`sprites/`, `work/`, `archive/`, `library/cache/`) except through the code paths the
  tools already use. Tests and experiments use temp folders (`HD_DATA`, `HD_SPECS`, module path overrides).
- **ComfyUI is shared** with Eric's live Studio. Don't queue GPU work from tests or scripts without checking
  `:8188/queue` and `:4321/api/ws/jobs`; a second Studio for checks runs on another port with a temp `HD_DATA` copy.
- **Worktrees** (`.claude/worktrees/<name>`, git-ignored): never junction or symlink real data into one;
  `git worktree remove` deletes through junctions.
- Generated output never goes into git (`.gitignore`); binaries in git (zones, refs) go through LFS (`.gitattributes`).
- Exports write only into the active project's profile paths (`config.project()`), whole pack folders at a time.
  The game's `ArtContract/requirements.json` is read, never written (`hdart/export/contract.py`).
- Removing = archiving to `archive/` (never delete candidates).
- Never write text files with PowerShell `Get/Set-Content` (mojibake + BOM). Use Python or an editor.

## Driving the tools (agents)
- **MCP:** the `hdart` server (`.mcp.json`; by hand: `claude mcp add --scope project hdart -- uv run --quiet
  --no-sync --directory P:/Tools/art-pipeline python -m hdart.cli mcp`; `--no-sync` because the running Studio holds
  `.venv/Scripts/hdart.exe`, so a sync fails until it stops: after a dependency change, `uv sync` with the Studio
  stopped, or `uv sync --no-install-project --inexact` while it runs). Task tools with a `tool` argument (character, items, dungeon,
  limner): `state` → `generate` → `jobs_wait` → `candidates` → `view_candidate` → `approve` / `archive` → `export` →
  `export_check`; `get_spec` / `patch_spec` for the specs; `list_ops` / `describe_op` / `call_op` reach every op.
- **CLI:** `uv run hdart op list [prefix]`, `hdart op show <name>`, `hdart op call <name> key=value ...`.
- Both call the **running Studio** over HTTP (`config.toml` `[studio] url`, env `HD_STUDIO_URL`); they never import a
  tool to do work, so there is one queue and one lock owner. Generating queues GPU work on the shared ComfyUI: check
  `jobs_list` first and only cancel your own jobs. To try them safely, point `HD_STUDIO_URL` at a throwaway Studio.

## Test and land
- `uv run python tests/run_all.py` (all pass, ~10 min, no GPU); `uv run python -c "from hdart.server import studio; studio.load_modules()"`.
- Route or page changes: smoke-test a throwaway Studio (`uv run hdart studio --no-browser --port <other>` with a temp
  `HD_DATA`; in a worktree it watches that worktree's `hdart/`).
- Prototyping phase: land on `main` (no PRs), fast-forward the root checkout `P:\Tools\art-pipeline`.
- **Restarting the live Studio after landing:** it runs under the supervisor (`hdart/server/supervisor.py`), which sees the
  fast-forward and restarts the server by itself once the running job has finished (debounced; queued jobs carry on
  after). To restart by hand: `uv run --no-sync hdart op call studio.restart reason="<why>"` (or `POST
  /api/op/studio.restart`); `studio.info` shows the pid and any pending restart. A change that doesn't import is
  refused and the old server keeps running: read `work/studio.log`. Never kill the Studio's window or processes.
  Nothing is reloaded in place, so any module may change. A Studio started with `--no-supervisor` (tests) refuses
  `studio.restart`.
- Style: typed signatures and docstrings on public functions and ops; comments say what and why, history goes in
  commits.
