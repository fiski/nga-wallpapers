"""
A simple Tkinter GUI for browsing and downloading NGA open-access paintings as
4K wallpapers.

Reuses the filtering/downloading logic from download_wallpapers.py: set filters,
click "Find matches" to browse thumbnails, hand-pick (or "Surprise me" to randomly
select), then download the selected images at exact 4K via the IIIF image API.

Usage:
    pip install Pillow      # one-time, for thumbnail previews
    python scripts/wallpaper_gui.py
"""

import os
import queue
import random
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

try:
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install pandas requests")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Missing dependency Pillow (needed for thumbnail previews). Run: pip install Pillow")
    sys.exit(1)

# Allow running directly: make download_wallpapers importable from this dir.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_wallpapers as _dw  # noqa: E402
from download_wallpapers import (  # noqa: E402
    DEFAULT_TERMS,
    TARGET_RATIO,
    artist_last,
    build_iiif_url,
    download,
    filter_candidates,
    load_data,
    slugify,
)

import csv  # noqa: E402


def app_base_dir():
    """Directory the app lives in: the exe's folder when frozen, else the repo root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # scripts/ -> repo root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_data_dir():
    """
    Find the `data/` folder containing the collection CSVs. Searches alongside the
    exe/script and a couple of parent levels so the .exe can sit in the project root.
    Returns the resolved path (or None if not found).
    """
    marker = "published_images.csv"
    candidates = []
    base = app_base_dir()
    candidates += [
        os.path.join(base, "data"),
        os.path.join(base, "..", "data"),
        os.path.join(os.getcwd(), "data"),
    ]
    # Bundled fallback (only if data was ever added to the build).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "data"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, marker)):
            return os.path.abspath(c)
    return None

# How many matching artworks to fetch thumbnails for per search (keeps UI snappy).
PREVIEW_LIMIT = 60
# Thumbnail display size (px); thumbnails are fit inside this box preserving ratio.
THUMB_W, THUMB_H = 220, 124
# Grid columns.
COLUMNS = 4


class WallpaperGUI:
    def __init__(self, root):
        self.root = root
        root.title("NGA Wallpaper Browser")
        root.geometry("1024x768")
        root.minsize(720, 520)

        # Loaded CSV data (set on background thread at startup).
        self.images = self.objects = self.terms_df = None
        # Candidate cells currently displayed: list of dicts with row/PhotoImage/var.
        self.cells = []
        # Cross-thread message queue drained on the main thread.
        self.queue = queue.Queue()
        self._busy = False

        self._build_controls()
        self._build_grid()
        self._build_footer()

        self._set_controls_state(False)
        self.set_status("Loading collection data…")
        threading.Thread(target=self._load_data_worker, daemon=True).start()
        self.root.after(100, self._drain_queue)

    # ---------- UI construction ----------

    def _build_controls(self):
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(side=tk.TOP, fill=tk.X)

        # Row 0: terms
        ttk.Label(bar, text="Subject terms:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        self.terms_var = tk.StringVar(value=", ".join(DEFAULT_TERMS))
        ttk.Entry(bar, textvariable=self.terms_var).grid(
            row=0, column=1, columnspan=5, sticky="we", pady=2
        )

        # Row 1: numeric controls
        ttk.Label(bar, text="Count:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        self.count_var = tk.IntVar(value=20)
        ttk.Spinbox(bar, from_=1, to=200, width=6, textvariable=self.count_var).grid(
            row=1, column=1, sticky="w", pady=2
        )

        ttk.Label(bar, text="Min source height:").grid(row=1, column=2, sticky="e", padx=6, pady=2)
        self.minh_var = tk.IntVar(value=2160)
        ttk.Spinbox(bar, from_=0, to=20000, increment=120, width=8, textvariable=self.minh_var).grid(
            row=1, column=3, sticky="w", pady=2
        )

        ttk.Label(bar, text="Output W×H:").grid(row=1, column=4, sticky="e", padx=6, pady=2)
        wh = ttk.Frame(bar)
        wh.grid(row=1, column=5, sticky="w", pady=2)
        self.width_var = tk.IntVar(value=3840)
        self.height_var = tk.IntVar(value=2160)
        ttk.Spinbox(wh, from_=1, to=16000, increment=10, width=7, textvariable=self.width_var).pack(side=tk.LEFT)
        ttk.Label(wh, text="×").pack(side=tk.LEFT, padx=2)
        ttk.Spinbox(wh, from_=1, to=16000, increment=10, width=7, textvariable=self.height_var).pack(side=tk.LEFT)

        # Row 2: output folder
        ttk.Label(bar, text="Save to:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=2)
        self.outdir_var = tk.StringVar(value=os.path.join(app_base_dir(), "wallpapers"))
        ttk.Entry(bar, textvariable=self.outdir_var).grid(row=2, column=1, columnspan=4, sticky="we", pady=2)
        ttk.Button(bar, text="Browse…", command=self._browse_folder).grid(row=2, column=5, sticky="w", pady=2)

        # Row 3: toggle + action buttons
        self.nodup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar, text="No duplicates (one primary image per artwork)", variable=self.nodup_var
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 2))

        btns = ttk.Frame(bar)
        btns.grid(row=3, column=3, columnspan=3, sticky="e", pady=(6, 2))
        self.find_btn = ttk.Button(btns, text="Find matches", command=self.on_find)
        self.find_btn.pack(side=tk.LEFT, padx=4)
        self.surprise_btn = ttk.Button(btns, text="Surprise me", command=self.on_surprise)
        self.surprise_btn.pack(side=tk.LEFT, padx=4)

        bar.columnconfigure(1, weight=1)

    def _build_grid(self):
        container = ttk.Frame(self.root, padding=(8, 0))
        container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_frame = ttk.Frame(self.canvas)
        self.grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")

        self.grid_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self.grid_window, width=e.width)
        )
        # Mouse wheel scrolling (Windows/Mac).
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _build_footer(self):
        footer = ttk.Frame(self.root, padding=8)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

        self.status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.open_btn = ttk.Button(footer, text="Open folder", command=self._open_folder)
        self.open_btn.pack(side=tk.RIGHT, padx=4)
        self.download_btn = ttk.Button(footer, text="Download selected", command=self.on_download)
        self.download_btn.pack(side=tk.RIGHT, padx=4)

    # ---------- helpers ----------

    def set_status(self, text):
        self.status_var.set(text)

    def _set_controls_state(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.find_btn, self.surprise_btn, self.download_btn, self.open_btn):
            btn.configure(state=state)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or os.getcwd())
        if d:
            self.outdir_var.set(d)

    def _open_folder(self):
        path = self.outdir_var.get()
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)  # Windows
        except AttributeError:
            messagebox.showinfo("Folder", path)

    def _clear_grid(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cells = []

    def _get_terms(self):
        return [t.strip() for t in self.terms_var.get().split(",") if t.strip()]

    # ---------- data load ----------

    def _load_data_worker(self):
        try:
            data_dir = resolve_data_dir()
            if not data_dir:
                self.queue.put((
                    "error",
                    "Could not find the 'data' folder with the collection CSVs.\n"
                    "Keep this program next to the 'data' folder "
                    "(published_images.csv, objects.csv, objects_terms.csv).",
                ))
                return
            _dw.DATA_DIR = data_dir  # point the loader at the resolved folder
            images, objects, terms_df = load_data()
            self.queue.put(("data_loaded", (images, objects, terms_df)))
        except Exception as e:
            self.queue.put(("error", f"Failed to load data: {e}"))

    # ---------- find / search ----------

    def on_find(self):
        if self._busy:
            return
        if self.images is None:
            self.set_status("Still loading data…")
            return
        terms = self._get_terms()
        if not terms:
            messagebox.showwarning("Terms", "Enter at least one subject term.")
            return

        self._busy = True
        self._set_controls_state(False)
        self._clear_grid()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.set_status("Searching…")
        threading.Thread(target=self._find_worker, args=(terms,), daemon=True).start()

    def _find_worker(self, terms):
        try:
            candidates = filter_candidates(
                self.images, self.objects, self.terms_df,
                terms, int(self.minh_var.get()), bool(self.nodup_var.get()),
            )
            total = len(candidates)
            preview = candidates.head(PREVIEW_LIMIT)
            self.queue.put(("search_done", (total, len(preview))))

            def fetch(row):
                try:
                    r = requests.get(str(row.iiifthumburl), timeout=30)
                    r.raise_for_status()
                    from io import BytesIO
                    img = Image.open(BytesIO(r.content))
                    img.thumbnail((THUMB_W, THUMB_H))
                    return (row, img)
                except Exception:
                    return (row, None)

            with ThreadPoolExecutor(max_workers=8) as pool:
                for row, img in pool.map(fetch, preview.itertuples()):
                    self.queue.put(("cell", (row, img)))
            self.queue.put(("find_complete", None))
        except Exception as e:
            self.queue.put(("error", f"Search failed: {e}"))

    def _add_cell(self, row, pil_img):
        idx = len(self.cells)
        r, c = divmod(idx, COLUMNS)
        cell = ttk.Frame(self.grid_frame, padding=6, relief="groove", borderwidth=1)
        cell.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        self.grid_frame.columnconfigure(c, weight=1)

        var = tk.BooleanVar(value=False)
        if pil_img is not None:
            photo = ImageTk.PhotoImage(pil_img)
        else:
            photo = None

        chk = ttk.Checkbutton(cell, variable=var)
        chk.grid(row=0, column=0, sticky="w")

        if photo is not None:
            lbl = ttk.Label(cell, image=photo)
            lbl.image = photo  # keep reference
            lbl.grid(row=1, column=0, sticky="n")
            # Click image to toggle selection.
            lbl.bind("<Button-1>", lambda e, v=var: v.set(not v.get()))
        else:
            ttk.Label(cell, text="(no preview)", width=28, anchor="center").grid(row=1, column=0)

        title = str(row.title) if str(row.title) != "nan" else "Untitled"
        artist = artist_last(row.attribution)
        date = str(row.displaydate) if str(row.displaydate) != "nan" else ""
        ttk.Label(cell, text=title[:42], wraplength=THUMB_W, font=("", 8, "bold")).grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(cell, text=f"{artist} · {date}", wraplength=THUMB_W, font=("", 7)).grid(
            row=3, column=0, sticky="w"
        )
        ttk.Label(cell, text=f"{row.src_w}×{row.src_h}", font=("", 7), foreground="#666").grid(
            row=4, column=0, sticky="w"
        )

        self.cells.append({"row": row, "var": var})

    # ---------- surprise ----------

    def on_surprise(self):
        if not self.cells:
            messagebox.showinfo("Surprise me", "Click “Find matches” first to load some images.")
            return
        n = min(int(self.count_var.get()), len(self.cells))
        for cell in self.cells:
            cell["var"].set(False)
        for cell in random.sample(self.cells, n):
            cell["var"].set(True)
        self.set_status(f"Randomly selected {n} of {len(self.cells)} images.")

    # ---------- download ----------

    def on_download(self):
        if self._busy:
            return
        selected = [c for c in self.cells if c["var"].get()]
        if not selected:
            messagebox.showinfo("Download", "Select at least one image (or use “Surprise me”).")
            return

        outdir = self.outdir_var.get()
        os.makedirs(outdir, exist_ok=True)
        w, h = int(self.width_var.get()), int(self.height_var.get())

        self._busy = True
        self._set_controls_state(False)
        self.progress.configure(mode="determinate", maximum=len(selected), value=0)
        self.set_status(f"Downloading {len(selected)} wallpaper(s)…")
        threading.Thread(
            target=self._download_worker, args=(selected, outdir, w, h), daemon=True
        ).start()

    def _download_worker(self, selected, outdir, w, h):
        index_rows = []
        ok = 0
        for i, cell in enumerate(selected, start=1):
            row = cell["row"]
            title = str(row.title) if str(row.title) != "nan" else "Untitled"
            artist = artist_last(row.attribution)
            filename = f"{i:03d}_{slugify(artist)}_{slugify(title)}.jpg"
            dest = os.path.join(outdir, filename)
            url = build_iiif_url(row.iiifurl, row.src_w, row.src_h, w, h)

            self.queue.put(("progress", (i, f"[{i}/{len(selected)}] {title[:50]}")))

            if os.path.exists(dest):
                saved = True
            else:
                saved = download(url, dest)
            if saved:
                ok += 1
            else:
                filename = "FAILED"

            index_rows.append({
                "rank": i,
                "title": title,
                "artist": row.attribution,
                "date": row.displaydate,
                "medium": row.medium,
                "source_size": f"{row.src_w}x{row.src_h}",
                "source_ratio": round(float(row.src_ratio), 3),
                "iiif_4k_url": url,
                "filename": filename,
            })

        if index_rows:
            index_path = os.path.join(outdir, "wallpapers_index.csv")
            try:
                with open(index_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=index_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(index_rows)
            except Exception:
                pass

        self.queue.put(("download_complete", (ok, len(selected))))

    # ---------- queue pump ----------

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "data_loaded":
                    self.images, self.objects, self.terms_df = payload
                    self._set_controls_state(True)
                    self.set_status("Ready. Set filters and click “Find matches”.")
                elif kind == "search_done":
                    total, shown = payload
                    self.set_status(f"{total} match(es) found. Loading {shown} preview(s)…")
                elif kind == "cell":
                    row, img = payload
                    self._add_cell(row, img)
                elif kind == "find_complete":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self._busy = False
                    self._set_controls_state(True)
                    self.set_status(
                        f"Loaded {len(self.cells)} preview(s). Pick images or use “Surprise me”."
                    )
                elif kind == "progress":
                    val, msg = payload
                    self.progress.configure(value=val)
                    self.set_status(msg)
                elif kind == "download_complete":
                    ok, total = payload
                    self._busy = False
                    self._set_controls_state(True)
                    self.set_status(f"Done. Saved {ok}/{total} wallpaper(s) to {self.outdir_var.get()}")
                    if ok:
                        messagebox.showinfo("Download complete", f"Saved {ok} of {total} wallpaper(s).")
                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self._busy = False
                    self._set_controls_state(True)
                    self.set_status(payload)
                    messagebox.showerror("Error", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)


def main():
    root = tk.Tk()
    WallpaperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
