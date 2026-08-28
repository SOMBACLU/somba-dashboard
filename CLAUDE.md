# SOMBA Dashboard — project notes

Social-media stats site for **SOM** (Cal Lutheran School of Management).
Live site: https://somba-dashboard.onrender.com/

## What this is
A static site (`index.html`) whose numbers are refreshed by a Python script
that GitHub Actions runs every hour (commits only when a number changed).

## Key files
- `update_stats.py` — the stats refresh. **Instagram fetch needs a
  Googlebot User-Agent** (the identifier a script sends when it requests a page);
  using Googlebot's avoids being blocked.
- `index.html` — the dashboard page.
- `weekly-update.command` — a double-click helper (macOS) that forces an update now.
- `data/` — the stats data.
- `ENGAGEMENT-DATA.md`, `README.md` — notes / docs.

## Deploy
- Git remote: `github.com/danbrown-coder/somba-dashboard` → auto-deploys to Render.
- Update flow: run `update_stats.py` → commit → push → Render redeploys the site.
  GitHub Actions does this automatically every hour (`.github/workflows/update-stats.yml`).

## Design consistency contract (index.html)

These rules exist because the page had grown **six different implementations of
one list row**. If you are adding UI, follow them — a new pattern is a bug.

### Rows
- **There is one row component: `.row`, built by one renderer: `rowHTML()`.**
  Adding a list? Call `rowHTML()`. Never write a new row class.
- Slots: `.row-lead` · `.row-main` (`.row-t` + `.row-sub`) · `.row-val`
  (`.row-v` + `.row-vsub`) · optional `.row-track` · `.row-tail`.
- **Height comes from two fixed line boxes, never from content.** The sub-line
  is emitted even when empty. A row with a sub-label and one without must be
  the same height.
- Only four modifiers are sanctioned: `.row--meter`, `.row--nolead`,
  `.row--link`, plus the state `.is-top`.
- **A highlight may change background and colour only** — never padding,
  radius, border or height. (`.prow.best` changed padding *and* radius; that is
  the exact bug this contract exists to prevent.)
- **The gold rail means "best".** Only put `.is-top` on a list actually sorted
  by performance — never on a date-sorted list, where row 1 is just the newest.

### Siblings
- Anything produced by one `.map()` must have identical box metrics.
  **Reserve the slot, don't drop it** — emit the empty element rather than
  omitting it, or the sibling without it will be shorter.
- Variable-count grids use `repeat(auto-fit, minmax(<min>, 1fr))`, never a
  fixed track count — 3 tiles in a 4-column grid leaves a hole.
- Equal-height tiles come from `align-items:stretch` plus an internal
  `grid-template-rows` with the last row at `1fr`, never a hardcoded height.

### Widths and spacing
- **No inline `style="margin…"` / `style="padding…"`.** Use `.mt-2`…`.mt-5`,
  which map to the spacing scale.
- Column widths are tokens (`--row-name-w`, `--row-val-w`, `--row-track-w`).
  A narrow container overrides the *token*, it does not get a new class.
- **Two chart heights only**: `--chart-h` (full-width) and `--chart-h-sm`
  (a 3-up column, and the donut).

### Words and colour
- **One verdict vocabulary**: `VD_SHORT` — `▲ Above` / `● On par` / `▼ Below`.
  Every verdict renders from it, hero badge included.
- **One verdict threshold**: `benchVerdict()` (±20%). The hero must not use a
  different band from the rows beneath it.
- Verdict chips are *light* chips wherever they land, including on the dark
  hero, so their ink uses `--status-pos-ink` / `--status-neg-ink`, which the
  `.on-dark` scope deliberately does **not** remap.
- A measurement window belongs on the row it measures, not in a footnote.

### Verifying
Measure the rendered page, not the stylesheet. Each list must return exactly
one distinct height:

```js
const h = s => [...new Set([...document.querySelectorAll(s)].map(e => e.offsetHeight))];
h('#postlist .row'); h('#vidlist .row'); h('#er-meters .row');
h('#er-pace .row'); h('#mix-legend .row'); h('#loclist .row'); h('.stile');
```

Also check: no clipped labels (`scrollWidth > clientWidth` on `.row-t`/`.row-sub`),
zero horizontal overflow at 390/760/1440px, and **`node --check` on the extracted
inline `<script>` after every JS edit** — regex edits over JS have silently
broken this page twice.
