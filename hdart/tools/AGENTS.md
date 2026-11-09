# AGENTS.md: `hdart/tools/` (one package per art tool)

Each tool package has its modules (the how) and one `<tool>_api.py` with its ops (`character.*`, `items.*`,
`dungeon.*`, `limner.*`) plus `LEGACY` / `GET_LEGACY` tables that map the pages' older routes onto them. Package
rules: `../AGENTS.md`; per tool: `character/`, `items/`, `dungeon/`, `limner/` AGENTS.md.

- **New action = new op** in the tool's api module: typed, documented (every parameter), workflow (auto-queued
  backs, batch planning, job labels) in the api module, not in the server. Long work queues a job
  (`jobs.enqueue()`, kind declared with `@jobs.kind` in the tool module that does the work).
- **Api modules stay light:** they import their tool inside each op, so reading the op docs (`hdart mcp`) doesn't load
  numpy and the image stack.
- A new job module or api module goes into `server/studio.py` (`JOB_MODULES`, `OP_MODULE_NAMES`), and a new tool or
  verb into `server/mcp_server.py` and `tests/test_mcp.py`.
- Tools may use each other (the Item Workshop reads the Character Workshop's puppet and Limner's designs) and
  `hdart.export` (contract, pack files), without cycles.
