# AGENTS.md: `hdart/tools/character/` (Character Workshop)

Pages `/workshop` and `/editor` (`studio/workshop.html`); ops `character.*` in `character_api.py`. Package rules:
`../../AGENTS.md`.

| Module | What |
|---|---|
| `workshop` | shared core: `ai_dir()` (`<data>/sprites/ai`), `workshop.json` (`load_state()`, `edit_state()`), style prompts, page images. |
| `rig` | rig space: `PX`, `S`, `N`, joints, pivots, waist, compose, head/figure boxes, face ellipse (also used by items and the puppet export). |
| `races` | Races tab: `races.json`, mannequins and their backs, ramp preview; job `race`. |
| `puppet` | the old detection puppet (kept for reference, `tests/test_puppet.py`) and the marionette walk; job `puppet`. |
| `puppet_template` | the rig template (`specs/puppet_template.json`); jobs `tpl`, `tpl_measure`. |
| `puppet_paint` | Paint, Parts, AI fix, skin bands; jobs `paint_fix`, `paint_preview`. |
| `puppet_face` | hair and faces; the `paint` job (paint, then face harvest), `face_gen`. |
| `mannequin_fit` | the template fitted onto the approved mannequin, bone by bone. |

Import order (no cycles): `workshop` / `rig` / `races` → `puppet` → `puppet_template` → `puppet_paint` →
`puppet_face`. Rest-pose canvas: 576 rig px / 1152 paint px, figure at `puppet.PAD`.
