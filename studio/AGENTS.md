# AGENTS.md: `studio/` (the pages)

Single-file HTML pages with inline JS and CSS, served by `hdart/studio.py` (no build step, no framework). Repo-wide
rules: `../AGENTS.md`.

| Page | URLs | Talks to |
|---|---|---|
| `workshop.html` | `/workshop` (Character Workshop: `#template`, `#races`, `#face`, `#ramps`), `/items` (Item Workshop), `/editor` (Workshop Editor) | `/api/ws/<action>` (Item Workshop actions under `/api/ws/items/<action>` are aliases of `items.*` ops), `/api/ws/img`, `/api/ws/jobs` |
| `dungeon.html` | `/dungeon` | `/api/dun/<action>`, `/api/lib/<action>`, `/api/ws/jobs` |
| `limner.html` | `/limner` | `/api/limner/<action>`, `/api/ws/jobs` |

- Each page has a small `api(path, body)` helper: GET without a body, POST JSON with one; errors come back as
  `{"error": ...}`. The tool switcher (☰) is a separate `<script>` block at the top of `workshop.html` and
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
