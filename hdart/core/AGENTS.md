# AGENTS.md: `hdart/core/` (plumbing every tool uses)

Imports nothing from the other packages (`jobs` imports `comfy` lazily for the busy check). Package rules:
`../AGENTS.md`.

| Module | What |
|---|---|
| `config` | `config.toml` + `config.local.toml` + env (`HD_DATA`, `HD_SPECS`, `HD_LIBRARY`, `HD_PROJECT`, `HD_PROFILE`, `HD_COMFY_URL`, `HD_BLENDER`, `HD_UNITY`). Every value and path is a function read per call (`data_dir()`, `specs_dir()`, `archive_dir()`, `library_dir()`, `project()`); `ROOT` is the repo. |
| `store` | state files (`State(path).edit()`: lock + atomic write), candidates (`new`, `write_meta`, `read_meta`), `archive()`, `progress.json`. |
| `locks` | in-process locks and caches shared by modules (`DUNGEON`, `CELLS`, `HARMONY`, the store's per-file locks). |
| `jobs` | the queue (`Jobs`, saved to `sprites/studio_jobs.json`) and `@jobs.kind(name, gpu=)`; `enqueue()`, `pause()`. Saved jobs name their kind, so a kind's name never changes. |
| `jobs_api` | ops `jobs.*` (list, get, wait, cancel, cancel_label, clear, queue). |
| `ops` | the op registry: `@ops.op("tool.action")` on a typed, documented function; `call()`, `call_query()` (GET), `call_alias()` (old page routes), `describe()`, `Blob`, `http_error()`. |
| `comfy` | ComfyUI client and workflow builders; failures raise `ComfyError`. `fake_comfy`: the in-process fake the tests use. |
| `memo` | small in-memory caches (`LRU`, `stamp()` of file mtimes). |
| `backup` | `hdart backup`: approved candidates + state to a dated folder outside any worktree. |
