# AGENTS.md: `hdart/server/` (the Studio and what drives it)

Package rules: `../AGENTS.md`; restarting the live Studio: `../../AGENTS.md`.

| Module | What |
|---|---|
| `supervisor` | what `hdart studio` (and `start-art-tools.cmd`) runs: a light parent (stdlib + `core.config`) that runs the server as a child (`hdart studio --no-supervisor`), polls `hdart/**/*.py` (every package) and restarts the child on code changes, debounced, through `studio.restart`: an import check in a fresh process first (a broken change is refused, the old server keeps serving), then the queue pauses, the running job finishes, the child exits with 75 and a new one starts. Log: `<data>/work/studio.log`. |
| `studio` | the web server, routing only: pages from `studio/`, `GET /api/ops`, `POST/GET /api/op/<name>`, the pages' older routes through `ALIASES`. `load_modules()` imports `JOB_MODULES` + `OP_MODULE_NAMES` (full module names) + env `HD_STUDIO_MODULES`. |
| `studio_api` | ops `studio.info` (pid, supervised, pending restart) and `studio.restart`. |
| `studio_client` | HTTP client of the running Studio's ops (`describe()`, `call()`; `StudioDown` / `StudioError`). URL: env `HD_STUDIO_URL`, else config `[studio] url`. |
| `mcp_server` | `hdart mcp` (stdio): task verbs over the ops (`state`, `candidates`, `view_candidate`, `generate`, `approve`, `archive`, `get_spec`, `patch_spec`, jobs, `export`, `export_check`) + `list_ops` / `describe_op` / `call_op`. Imports the api modules only for their docs; every call goes over HTTP. |
| `watchdog` | `python -m hdart.server.watchdog` (Limner starts it for overnight batches): keeps the PC awake, restarts a dead Studio or ComfyUI. |

- Op names, HTTP routes and job kind names are the public surface: keep them when code moves.
- Nothing is reloaded in place; a Studio started with `--no-supervisor` (tests) refuses `studio.restart`.
