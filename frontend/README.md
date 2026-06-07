# Wallpaper Browser — frontend

React + [Base UI](https://base-ui.com) (`@base-ui/react`) + Tailwind CSS v4, built with
Vite. Rendered inside a native desktop window by `scripts/wallpaper_web.py` (pywebview), which
exposes the Python backend as `window.pywebview.api`.

This is currently a **placeholder UI** that proves the data-load / find / download plumbing.
The real visuals will be rebuilt from the Figma design — replace the markup in
`src/App.tsx`; the bridge in `src/api.ts` stays the same.

## Develop

```bash
# 1) start the Vite dev server (hot reload)
npm install            # first time only
npm run dev            # http://localhost:5173

# 2a) iterate UI in a plain browser (uses the mock API in src/api.ts), OR
# 2b) run inside the real desktop window against the live Python backend:
python ../scripts/wallpaper_web.py --dev
```

When opened in a normal browser (no Python), `src/api.ts` falls back to a mock so you can
design freely. A "browser mock" badge appears in the header.

## Build (for packaging)

```bash
npm run build          # outputs to frontend/dist/
python ../scripts/wallpaper_web.py     # loads frontend/dist in the desktop window
```

## The bridge (`src/api.ts`)

| JS call | Python (`scripts/wallpaper_web.py` `Api`) |
| --- | --- |
| `getStatus()` | data-load state + defaults (terms, output dir, image count) |
| `find({terms, minHeight, noDuplicates, previewLimit})` | ranked candidates (incl. `thumb` + `iiif`) |
| `download({items, outDir, width, height})` | downloads 4K JPEGs, writes `wallpapers_index.csv`; progress via `onProgress()` |
| `chooseFolder()` / `openFolder(path)` | native folder dialog / open in Explorer |

## Notes for the Figma rebuild
- Use Figma **auto-layout** (maps to flexbox). Shadows/gradients/rounded corners/custom fonts
  all fine. Self-host or use Google Fonts so they bundle.
- Design the states: initial **data loading** (~290 MB parse), **empty results**, **thumbnail
  loading**, **download progress**, **error** (no network / missing `data/` folder).
- Keep real HTML controls (inputs/buttons/checkboxes) — easy to wire; use Base UI primitives
  (Checkbox, Select, Slider, Dialog…) for accessible behavior.
