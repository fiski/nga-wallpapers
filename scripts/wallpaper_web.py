"""
pywebview desktop shell for the NGA Wallpaper Browser.

Renders the React/Base UI/Tailwind frontend (frontend/dist) in a native WebView2
window and exposes a Python API bridge (window.pywebview.api.*) that reuses the
existing backend logic in download_wallpapers.py.

Run modes:
    python scripts/wallpaper_web.py            # load built frontend (frontend/dist)
    python scripts/wallpaper_web.py --dev      # load Vite dev server (http://localhost:5173)

Dev workflow:
    1) cd frontend && npm run dev      (starts Vite on :5173)
    2) python scripts/wallpaper_web.py --dev
"""

import csv
import json
import os
import sys
import threading

try:
    import webview
except ImportError:
    print("Missing dependency pywebview. Run: pip install pywebview")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_wallpapers as _dw  # noqa: E402
from download_wallpapers import (  # noqa: E402
    DEFAULT_TERMS,
    artist_last,
    build_iiif_url,
    download,
    filter_candidates,
    load_data,
)

DEV = "--dev" in sys.argv or os.environ.get("WP_DEV") == "1"
PREVIEW_LIMIT_MAX = 200


def app_base_dir():
    """Folder the app lives in: the exe's dir when frozen, else the repo root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_data_dir():
    """Find the data/ folder containing the collection CSVs (script or frozen)."""
    marker = "published_images.csv"
    base = app_base_dir()
    candidates = [
        os.path.join(base, "data"),
        os.path.join(base, "..", "data"),
        os.path.join(os.getcwd(), "data"),
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "data"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, marker)):
            return os.path.abspath(c)
    return None


def resolve_index():
    """Locate the built frontend entry (frontend/dist/index.html or bundled web/)."""
    base = app_base_dir()
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(os.path.join(meipass, "web", "index.html"))
    candidates += [
        os.path.join(base, "frontend", "dist", "index.html"),
        os.path.join(base, "web", "index.html"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def resolve_icon():
    """Locate the window icon (icon.ico), in the frozen bundle or the repo."""
    base = app_base_dir()
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(os.path.join(meipass, "icon.ico"))
    candidates += [
        os.path.join(base, "icon.ico"),
        os.path.join(base, "frontend", "public", "favicon.ico"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _default_out_dir():
    return os.path.join(app_base_dir(), "wallpapers")


class Api:
    """Methods here are callable from JS as window.pywebview.api.<name>(...)."""

    def __init__(self):
        self._window = None
        self._images = None
        self._objects = None
        self._terms = None
        self._loading = True
        self._ready = False
        self._error = None
        self._count = 0
        self._facets = {"classifications": [], "subjects": []}
        threading.Thread(target=self._load, daemon=True).start()

    def set_window(self, window):
        self._window = window

    # ---- startup ----

    def _load(self):
        try:
            data_dir = resolve_data_dir()
            if not data_dir:
                raise FileNotFoundError(
                    "Could not find the 'data' folder with the collection CSVs. "
                    "Keep this app next to the 'data' folder."
                )
            _dw.DATA_DIR = data_dir
            self._images, self._objects, self._terms = load_data()
            self._count = int(len(self._images))
            self._facets = self._compute_facets()
            self._ready = True
        except Exception as e:  # surfaced to the UI via get_status
            self._error = str(e)
        finally:
            self._loading = False

    def get_status(self):
        return {
            "ready": self._ready,
            "loading": self._loading,
            "error": self._error,
            "count": self._count,
            "defaultTerms": list(DEFAULT_TERMS),
            "defaultOutDir": _default_out_dir(),
        }

    # ---- facets (filter vocabulary) ----

    def _compute_facets(self):
        """Build the filter vocabulary once, after the CSVs are loaded.

        Counts are whole-collection (informational) — not downloadable-at-resolution
        counts, which would be too heavy to compute per keystroke and would depend on
        the chosen output resolution.
        """
        counts = self._objects["classification"].dropna().value_counts()
        classifications = [
            {"value": str(value), "count": int(count)} for value, count in counts.items()
        ]
        # The NGA "Subject" vocabulary — a clean, curated theme list for autocomplete.
        subjects = sorted(
            str(s) for s in self._terms["visualbrowsertheme"].dropna().unique() if str(s).strip()
        )
        return {"classifications": classifications, "subjects": subjects}

    def get_facets(self):
        if not self._ready:
            return {"classifications": [], "subjects": []}
        return self._facets

    def subject_counts(self, params):
        """Per-theme counts of downloadable works under the current structural filters.

        Reuses the search pipeline with empty terms (browse-all) so the counts reflect
        the same landscape / open-access / resolution / artwork-type constraints as a
        real search. Independent of the typed subject term, so callers only need to
        recompute when those structural filters change.
        """
        if not self._ready:
            return []
        out_w = int(params.get("width", 3840))
        out_h = int(params.get("height", 2160))
        no_dups = bool(params.get("noDuplicates", False))
        classifications = [
            str(c).strip() for c in (params.get("classifications") or []) if str(c).strip()
        ]
        candidates = filter_candidates(
            self._images, self._objects, self._terms, [], out_h, no_dups, classifications
        )
        candidates = candidates[candidates["src_w"] >= out_w]
        ids = candidates[["objectid"]].drop_duplicates()
        themed = self._terms[["objectid", "visualbrowsertheme"]].dropna(subset=["visualbrowsertheme"])
        themed = themed[themed["visualbrowsertheme"].astype(str).str.strip() != ""]
        counts = (
            themed.merge(ids, on="objectid")
            .groupby("visualbrowsertheme")["objectid"].nunique()
            .sort_values(ascending=False)
        )
        return [{"term": str(t), "count": int(c)} for t, c in counts.items()]

    # ---- search ----

    def find(self, params):
        if not self._ready:
            return []
        terms = [str(t).strip() for t in (params.get("terms") or []) if str(t).strip()]
        # Empty terms = browse all (still bounded by the structural filters below).
        # Quality floor is derived entirely from the chosen output resolution: a center-crop
        # to the output ratio avoids upscaling iff src_w >= out_w AND src_h >= out_h.
        out_w = int(params.get("width", 3840))
        out_h = int(params.get("height", 2160))
        no_dups = bool(params.get("noDuplicates", False))
        preview_limit = min(int(params.get("previewLimit", 60)), PREVIEW_LIMIT_MAX)
        classifications = [
            str(c).strip() for c in (params.get("classifications") or []) if str(c).strip()
        ]

        candidates = filter_candidates(
            self._images, self._objects, self._terms, terms, out_h, no_dups,
            classifications,
        )
        # filter_candidates already enforced src_h >= out_h; enforce the width half too.
        candidates = candidates[candidates["src_w"] >= out_w]

        out = []
        for i, row in enumerate(candidates.head(preview_limit).itertuples()):
            title = str(row.title) if str(row.title) != "nan" else "Untitled"
            out.append({
                "id": i,
                "title": title,
                "artist": "" if str(row.attribution) == "nan" else str(row.attribution),
                "artistLast": artist_last(row.attribution),
                "date": "" if str(row.displaydate) == "nan" else str(row.displaydate),
                "medium": "" if str(row.medium) == "nan" else str(row.medium),
                "accession": "" if str(row.accessionnum) == "nan" else str(row.accessionnum),
                "srcW": int(row.src_w),
                "srcH": int(row.src_h),
                "ratio": round(float(row.src_ratio), 3),
                "thumb": str(row.iiifthumburl),
                "iiif": str(row.iiifurl),
            })
        return out

    # ---- download ----

    def _emit_progress(self, current, total, label):
        if not self._window:
            return
        payload = json.dumps({"current": current, "total": total, "label": label})
        try:
            self._window.evaluate_js(
                f"window.__wallpaperProgress && window.__wallpaperProgress({payload})"
            )
        except Exception:
            pass

    def download(self, params):
        items = params.get("items") or []
        out_dir = params.get("outDir") or _default_out_dir()
        width = int(params.get("width", 3840))
        height = int(params.get("height", 2160))
        os.makedirs(out_dir, exist_ok=True)

        from download_wallpapers import slugify  # local import keeps top clean

        index_rows = []
        ok = 0
        total = len(items)
        for i, it in enumerate(items, start=1):
            title = it.get("title") or "Untitled"
            artist = it.get("artistLast") or artist_last(it.get("artist"))
            src_w, src_h = int(it["srcW"]), int(it["srcH"])
            filename = f"{i:03d}_{slugify(artist)}_{slugify(title)}.jpg"
            dest = os.path.join(out_dir, filename)
            url = build_iiif_url(it["iiif"], src_w, src_h, width, height)

            self._emit_progress(i, total, title[:60])

            saved = True if os.path.exists(dest) else download(url, dest)
            if saved:
                ok += 1
            else:
                filename = "FAILED"

            index_rows.append({
                "rank": i,
                "title": title,
                "artist": it.get("artist", ""),
                "date": it.get("date", ""),
                "medium": it.get("medium", ""),
                "source_size": f"{src_w}x{src_h}",
                "source_ratio": it.get("ratio", ""),
                "iiif_4k_url": url,
                "filename": filename,
            })

        if index_rows:
            try:
                with open(os.path.join(out_dir, "wallpapers_index.csv"), "w",
                          newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=index_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(index_rows)
            except Exception:
                pass

        return {"ok": ok, "total": total, "outDir": out_dir}

    # ---- folder helpers ----

    def choose_folder(self):
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            return result[0] if isinstance(result, (list, tuple)) else str(result)
        return None

    def open_folder(self, params):
        path = (params or {}).get("path") or _default_out_dir()
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)  # Windows
            return True
        except Exception:
            return False


def main():
    api = Api()

    if DEV:
        target = "http://localhost:5173"
    else:
        index = resolve_index()
        if not index:
            # No built frontend — show a helpful message instead of a blank window.
            html = (
                "<body style='font-family:sans-serif;padding:2rem'>"
                "<h2>Frontend not built</h2>"
                "<p>Run <code>npm run build --prefix frontend</code> first, "
                "or launch with <code>--dev</code> after starting the Vite dev server.</p>"
                "</body>"
            )
            window = webview.create_window("NGA Wallpaper Browser", html=html, js_api=api)
            api.set_window(window)
            webview.start(icon=resolve_icon())
            return
        target = index

    window = webview.create_window(
        "NGA Wallpaper Browser",
        url=target,
        js_api=api,
        width=1180,
        height=820,
        min_size=(760, 540),
    )
    api.set_window(window)
    webview.start(debug=DEV, icon=resolve_icon())


if __name__ == "__main__":
    main()
