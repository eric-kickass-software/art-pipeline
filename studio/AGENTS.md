# AGENTS.md: `studio/` (the pages)

Single-file HTML pages with inline JS and CSS, served by `hdart/studio.py` (no build step, no framework). Repo-wide
rules: `../AGENTS.md`.

| Page | URLs | Talks to |
|---|---|---|
| `workshop.html` | `/workshop` (Character Workshop: `#template`, `#races`, `#face`, `#ramps`), `/items` (Item Workshop), `/editor` (Workshop Editor) | `/api/ws/<action>` (aliases of `character.*` ops, `/api/ws/items/<action>` of `items.*`), `/api/ws/img`, `/api/ws/jobs` (`jobs.*`) |
| `dungeon.html` | `/dungeon` | `/api/dun/<action>`, `/api/lib/<action>` (aliases of `dungeon.*` ops), `/api/ws/jobs` |
| `limner.html` | `/limner` | `/api/limner/<action>` (aliases of `limner.*` ops), `/api/ws/jobs` |

Every route is an alias of an op (`hdart/<tool>_api.py`, tables `LEGACY` / `GET_LEGACY`); `tests/test_tool_ops.py`
and `tests/test_ops.py` fail when a page calls a route without one. New page code can call `/api/op/<name>` directly.

- Each page has a small `api(path, body)` helper: GET without a body, POST JSON with one; errors come back as
  `{"error": ..., "type": ...}` with a 4xx/5xx status (400 bad call, 404 unknown thing, 405 GET on a changing op). The tool switcher (☰) is a separate `<script>` block at the top of `workshop.html` and
  `dungeon.html`.
- `workshop.html` holds three tools (about 2.7k lines): the Item Workshop's script starts at the
  `// Item Workshop (/items)` block; the Workshop Editor is the `PA` object.
- Browser-only preferences (hidden layers, last race/sex/view, outfits) are in `localStorage`, always inside
  `try/catch`. Real state is on the server.
- The page's ramp maths must match `hdart/palette.py` (`tests/test_ramps.py` runs the page's JS under Node).

## Invariants
- Keep old routes working: the page and agents may call either the route or the op.
- Every prompt the tools send stays editable next to the button that uses it (Eric's rule).
- Changes to a page take effect on reload (no Studio restart). Check them on a throwaway Studio with temp data, never
  by clicking through Eric's live one.

## Planned
A10 chunk 7 splits the pages into per-tool JS modules with a shared API client; don't start that here.
