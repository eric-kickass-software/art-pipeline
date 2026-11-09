# AGENTS.md: `hdart/tools/items/` (Item Workshop)

Page `/items` (`studio/workshop.html`); ops `items.*` in `items_api.py`. Package rules: `../../AGENTS.md`.

- `item_workshop`: the how: zones (`specs/item_zones/`, `zones_dir()`), keying, the split per part, the hidden-area
  pass, grips, game items (`game_ids`, `game_info`); spec `specs/items.json` (`spec_path()`); job `item`.
- State: `sprites/ai/items/<race>/state.json` (approvals per fit key, grips) and candidates under
  `sprites/ai/items/<race>/<item>/<cid>/`.
- Builds on the Character Workshop (`workshop`, `rig`, `puppet`, `puppet_face`), keys through `imaging.levels`, and
  takes design references from Limner. The export is `hdart.export.item_export`.
- Item zone rules (`items.json` → `zones`): `part` (fnmatch on part names), `t` (range along the bone), `rows` /
  `ellipse` (fractions of the part's box), `seam` (above/below the waist), `view`, `cut: true` (removes the area).
- Threads: `held_preview` passes its unsaved spec as `render(doc=)`; never swap a module global per request.
