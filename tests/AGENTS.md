# AGENTS.md: `tests/`

Script-style tests: each `test_*.py` is a plain script that asserts and prints `ok` (no pytest). Repo-wide rules:
`../AGENTS.md`.

## Run
- All: `uv run python tests/run_all.py [name-filter]` (each file in its own process, ~10 min; the puppet paint, face,
  items and mannequin-fit tests take most of it). One: `uv run python tests/test_ops.py`.
- From a worktree, `test_harmonize` and `test_puppet` need the real data: `HD_DATA=P:\Tools\art-pipeline`
  (`test_puppet` skips without an approved `human.male` pair).

## Rules
- **No GPU.** `run_all.py` points `HD_COMFY_URL` at a closed port. A test that generates starts the fake ComfyUI
  (`hdart.fake_comfy.FakeComfy(respond).start()`, with a `respond` hook to paint keys or record prompts).
- **Frozen specs, never the live `specs/`.** Every test starts with `import fixture_specs` before any `hdart` import:
  it copies `tests/fixtures/specs/` to a temp folder and sets `HD_SPECS`, so Eric's Studio edits can't break a test
  and a test can't write into his specs. Don't assert on live prompt wording or catalogue contents; assert on the
  fixture or on what the code composes from the spec it read. Refresh a fixture file from `specs/` only on purpose.
  The exception is `test_harmonize`, which reads the real crypt set and its approved art (read-only).
- **Temp data.** Point data paths at a temp folder before working: `HD_DATA` / `HD_PROJECT` env, or the module
  attributes bound at import (`ws.AI_DIR`, `ws.STATE`, `ws.CAND`, `ws.ARCHIVE`, `iw.SPEC`, `d.SETS`, `d.DUN`,
  `L.SPEC`, ...). Never junction or copy Eric's data into the repo.
- Exports go into a temp game project (`HD_PROJECT`), never the real Hearthdelve checkout.
- New ops and new rules get a test here. `test_ops.py` covers the registry and the Item Workshop's ops;
  `test_tool_ops.py` the Dungeon, Limner, Character and jobs ops: every op and parameter documented, every route the
  pages call mapped to an op, direct calls on temp data, and a throwaway Studio over HTTP (on a free port, temp
  `HD_DATA` / `HD_SPECS`, no GPU). `test_mcp.py` runs `hdart op` and the MCP server (`hdart mcp` over stdio, as
  Claude Code starts it) against a throwaway Studio with hand-made candidates; a failed generate stands in for jobs.
- `test_modules.py` reads `hdart/` (AST): no private cross-module access, no import cycles.
