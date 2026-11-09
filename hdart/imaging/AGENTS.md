# AGENTS.md: `hdart/imaging/` (image helpers shared by the tools)

Pure image maths on numpy / PIL; imports only `core` (`palette` reads its spec through `config`). Package rules:
`../AGENTS.md`.

| Module | What |
|---|---|
| `imageops` | masks: foreground, clean, components, grow/erode, hsv, figure_mask, enforce_silhouette, retrying save. |
| `levels` | keying and cel levels, the one keying module: key hue, brightness levels, smoothing, skin keying, blank face, `colour_levels`, hand-edited bands. Hair, irises, item materials and skin all key through it. |
| `palette` | ramp maths + `specs/palettes.json` (`palettes_path()`); the page's ramp JS must match it (`tests/test_ramps.py`). |
| `pixel` | pixel-art passes (quantize, outline, pixelize, normals from height) for the Dungeon Forge and Limner. |

A helper two tools need goes here (public, typed, documented), not as a private import from a tool.
