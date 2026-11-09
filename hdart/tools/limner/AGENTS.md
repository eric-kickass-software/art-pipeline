# AGENTS.md: `hdart/tools/limner/` (Limner)

Page `/limner` (`studio/limner.html`); ops `limner.*` in `limner_api.py`. Package rules: `../../AGENTS.md`.

- `limner`: icons, portraits and tokens: entries from the game's contract (`hdart.export.contract`) plus ＋ Add ones,
  prompt layers style > category > entry (`specs/limner.json`, `spec_path()`; refs in `specs/limner_refs/`),
  candidates in `sprites/limner/<cat>/<id>/<cid>/`, state `sprites/limner/state.json`, export to `Art/Icons/<cat>/`,
  `Art/Portraits/`, `Art/Monsters/`. Jobs `limner_gen`, `limner_vary`.
- Overnight batches (`limner.plan_batch`, `limner.queue_batch`) queue with retries and start the watchdog
  (`python -m hdart.server.watchdog`, `ensure_watchdog()`).
