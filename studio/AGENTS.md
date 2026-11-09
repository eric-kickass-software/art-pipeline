# AGENTS.md: `studio/` (the pages)

HTML pages, their stylesheets (`css/`) and native ES modules (`js/`), served as static files by `hdart/server/studio.py`
(no build step, no framework; `/js/...` and `/css/...` with `Last-Modified` + `no-cache`, so an edit shows on the next
reload). Repo-wide rules: `../AGENTS.md`.

| Page | URL | Entry module | Modules |
|---|---|---|---|
| `workshop.html` | `/workshop` (Character Workshop; tab anchors `#template`, `#races`, `#face`, `#ramps`) | `js/workshop/character.js` | `template.js`, `races.js`, `face.js`, `ramps-tab.js` |
| `items.html` | `/items` (Item Workshop; `#items`, `#outfit`) | `js/workshop/items.js` | `zone.js`, `grip.js`, `outfit.js` |
| `editor.html` | `/editor` (Workshop Editor; `?build=&paint=`, `?race=&layer=&id=`, `?race=&item=&id=` open a candidate) | `js/workshop/editor.js` | |
| `dungeon.html` | `/dungeon` | `js/dungeon/main.js` | `families.js`, `image-panel.js`, `preview.js`, `group.js`, `library.js`, `util.js` |
| `limner.html` | `/limner` | `js/limner.js` | |

The three Workshop pages share `css/workshop.css` and `js/workshop/common.js` (error line, tabs, `img()` for character
images, the job panel and `queue()`, `openInEditor()`: the other pages' Edit buttons navigate to `/editor?...`).

## Shared modules (`js/`)
- `api.js`: the only way pages reach the server. `get(op, args)` (GET `/api/op/<op>`, read-only ops; long arguments go as
  POST), `post(op, args)`, `url(op, args, stamp)` for `<img src>` of image ops. Failures throw `ApiError` with the
  server's `{error, type}` text: one error path, show `e.message`. Ops are strict: send exactly the op's parameters
  (renamed ones: `candidate` not `id`, `entry` not `id` in Limner...). Cache busters are `_`-prefixed query arguments
  (the server ignores them).
- `jobs.js`: the job queue panel (button, list with ✕ cancel, Clear finished, polling); pages react in `onPoll`.
- `menu.js`: the ☰ tool switcher. `canvas.js`: canvas helpers (make, load an image, round brush, zoom about a point).
- `ramps.js`: ramp maths (must match `hdart/imaging/palette.py`; `tests/test_ramps.py` runs it under Node).
  `ramp-chips.js`: a ramp as a swatch strip or a gradient.

## How the modules fit
- Each page's modules share state through imports: a module lists what it exports in its `// <exports>` block at the
  end and what it takes from its sibling modules in its `// <imports>` block; keep both accurate when you add or move a
  function. Import cycles between a page's modules are fine as long as top-level code only touches imported names
  inside functions (handlers, callbacks).
- An ES module can't assign another module's variable: Dungeon Forge rebinds its page state (`D`, `SEL`, `TRY`, `FAM`)
  from other modules through `setState({...})` in `main.js`.
- Modules are strict mode: declare every variable. Inline `onclick="..."` can't see module functions: wire handlers in
  the module (event delegation for generated rows).
- Browser-only preferences (hidden layers, last race/sex/view, outfits) are in `localStorage`, inside `try/catch`.

## Invariants
- Pages call ops only (`/api/op/<name>`), never the older alias routes (those stay for scripts and agents).
  `tests/test_tool_ops.py` scans `js/`: every op called exists, GETs name read-only ops, inline argument objects name
  only the op's parameters; every page and module is served.
- Every prompt the tools send stays editable next to the button that uses it (Eric's rule).
- Check page changes on a throwaway Studio with temp data (`HD_DATA`, `HD_SPECS`, `HD_LIBRARY`, `HD_PROJECT`, a closed
  `HD_COMFY_URL`, another port), never by clicking through Eric's live one.
