# AGENTS.md: `hdart/export/` (game side)

What the tools hand to the game project (`config.project()`). Package rules: `../AGENTS.md`.

| Module | What |
|---|---|
| `contract` | reads the project's `ArtContract/requirements.json` and content (vocabulary, wanted ids). Read, never written. |
| `art_pack` | the pack format shared by every export: pack table, `index.json`, `ramps.json` / `channels.json`, layer crop + save. |
| `unity_export` | `hdart puppet-export`: the painted puppets into `Art/Puppets/`. |
| `item_export` | `hdart item-export`, `hdart export [--check]`: game items into `Art/Items/` + intake files. |

- Exports write only into the active project's profile paths, whole pack folders at a time; `Art/index.json` is
  rewritten by every export, and `export --check` must stay clean after one.
- `item_export` and `unity_export` write a paragraph of their module docstring into the files they export (`_doc`):
  change those paragraphs only on purpose.
- The dungeon export lives with its tool (`hdart.tools.dungeon.export`) and uses `art_pack`.
