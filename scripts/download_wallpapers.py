"""
Download NGA open-access paintings as exact 4K (3840x2160) wallpapers via IIIF.

Images are filtered to landscape-oriented source paintings, then center-cropped
to the exact 16:9 aspect ratio using the IIIF region API before download.

Usage:
    python scripts/download_wallpapers.py [options]

Options:
    --output-dir DIR    Output folder (default: wallpapers/)
    --limit N           Max images to download (default: 20)
    --terms TEXT        Comma-separated subject terms to match
                        (default: landscape,nature,seascape,forest,mountain,
                                  garden,river,pastoral,countryside,coastal,botanical,sky)
    --min-height PX     Minimum source height in pixels (default: 2160)
    --width PX          Target wallpaper width  (default: 3840)
    --height PX         Target wallpaper height (default: 2160)
    --no-duplicates     Keep only one image per artwork (primary view only)
    --classifications T Comma-separated artwork types to include, e.g.
                        "Print,Photograph". Default: all types.
"""

import argparse
import csv
import os
import re
import sys
import time

try:
    import pandas as pd
    import requests
except ImportError:
    print("Missing dependencies. Run: pip install pandas requests")
    sys.exit(1)

DEFAULT_TERMS = [
    "landscape", "nature", "seascape", "forest", "mountain",
    "garden", "river", "pastoral", "countryside", "coastal", "botanical", "sky",
]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

TARGET_RATIO = 16 / 9  # 3840 / 2160


def load_data():
    images = pd.read_csv(os.path.join(DATA_DIR, "published_images.csv"), low_memory=False)
    objects = pd.read_csv(os.path.join(DATA_DIR, "objects.csv"), low_memory=False)
    terms = pd.read_csv(os.path.join(DATA_DIR, "objects_terms.csv"), low_memory=False)
    return images, objects, terms


def filter_candidates(images, objects, terms_df, subject_terms, min_height, no_duplicates,
                      classifications=None):
    images = images.copy()
    images["src_w"] = pd.to_numeric(images["width"], errors="coerce").fillna(0).astype(int)
    images["src_h"] = pd.to_numeric(images["height"], errors="coerce").fillna(0).astype(int)

    # Must have known dimensions (width > 0, height > 0) and be landscape-oriented
    images = images[(images["src_w"] > 0) & (images["src_h"] > 0) & (images["src_w"] > images["src_h"])]

    # Open-access only
    images = images[images["openaccess"] == 1]

    # Primary view only if requested
    if no_duplicates:
        images = images[images["viewtype"] == "primary"]

    # Must be tall enough to crop to target height without upscaling
    images = images[images["src_h"] >= min_height]

    # Aspect ratio of source image
    images["src_ratio"] = images["src_w"] / images["src_h"]

    # Artwork type(s): restrict to the chosen classifications, or all types if none given.
    works = objects
    if classifications:
        works = works[works["classification"].isin(classifications)]
    works = works[["objectid", "title", "attribution", "displaydate", "medium", "accessionnum"]].copy()

    # Join selected works onto open-access landscape images.
    candidates = images.merge(works, left_on="depictstmsobjectid", right_on="objectid")

    # Narrow to subject-term matches only when terms were given (empty = browse all).
    if subject_terms:
        pattern = "|".join(re.escape(t) for t in subject_terms)
        matched_terms = terms_df[
            terms_df["termtype"].isin(["Keyword", "Theme"]) &
            terms_df["term"].str.contains(pattern, case=False, na=False)
        ][["objectid"]].drop_duplicates()
        candidates = candidates.merge(matched_terms, on="objectid")

    # How close is the source ratio to 16:9? (lower = closer match, less cropping)
    candidates["ratio_delta"] = abs(candidates["src_ratio"] - TARGET_RATIO)

    # Sort: prefer images close to 16:9, then by source quality (larger = better)
    candidates = candidates.sort_values(["ratio_delta", "src_h"], ascending=[True, False])

    # One image per object
    candidates = candidates.drop_duplicates(subset="objectid")

    return candidates.reset_index(drop=True)


def center_crop_region(src_w, src_h, target_ratio=TARGET_RATIO):
    """
    Return the IIIF region string (x,y,w,h) for a center crop of src_w×src_h
    to the given aspect ratio.
    """
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        # Source is wider than target → crop width, keep full height
        crop_h = src_h
        crop_w = int(round(src_h * target_ratio))
        x = (src_w - crop_w) // 2
        y = 0
    else:
        # Source is taller (relative to width) than target → crop height, keep full width
        crop_w = src_w
        crop_h = int(round(src_w / target_ratio))
        x = 0
        y = (src_h - crop_h) // 2
    return f"{x},{y},{crop_w},{crop_h}"


def build_iiif_url(base_url, src_w, src_h, out_w, out_h):
    """
    Construct an IIIF URL that center-crops to the OUTPUT aspect ratio (out_w:out_h)
    then scales to out_w×out_h. Deriving the crop ratio from the requested output
    keeps 16:9 results identical while supporting other ratios (e.g. 21:9 ultrawide)
    without distortion.
    """
    base_url = str(base_url).rstrip("/")
    region = center_crop_region(src_w, src_h, out_w / out_h)
    return f"{base_url}/{region}/{out_w},{out_h}/0/default.jpg"


def slugify(text, maxlen=40):
    text = str(text or "")
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s]+", "_", text.strip())
    return text[:maxlen]


def artist_last(attribution):
    if not attribution or str(attribution) == "nan":
        return "Unknown"
    name = str(attribution).split(",")[0].strip()
    return name.split()[-1] if name else "Unknown"


def download(url, dest_path, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            return True
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  ERROR: {e}")
                return False


def main():
    parser = argparse.ArgumentParser(description="Download NGA landscape paintings as exact 4K wallpapers.")
    parser.add_argument("--output-dir", default="wallpapers")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--terms", default=",".join(DEFAULT_TERMS))
    parser.add_argument("--min-height", type=int, default=2160,
                        help="Minimum source image height (ensures no upscaling)")
    parser.add_argument("--width", type=int, default=3840)
    parser.add_argument("--height", type=int, default=2160)
    parser.add_argument("--no-duplicates", action="store_true")
    parser.add_argument("--classifications", default="",
                        help="Comma-separated artwork types to include (default: all)")
    args = parser.parse_args()

    subject_terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    classifications = [c.strip() for c in args.classifications.split(",") if c.strip()]
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    print("Loading data...")
    images, objects, terms_df = load_data()

    print("Filtering candidates (landscape-oriented, open-access, subject-matched)...")
    candidates = filter_candidates(
        images, objects, terms_df, subject_terms, args.min_height, args.no_duplicates,
        classifications,
    )

    total = min(len(candidates), args.limit)
    print(f"Found {len(candidates)} matching artworks. Downloading top {total}.\n")

    if total == 0:
        print("No candidates found. Try lowering --min-height or broadening --terms.")
        return

    index_rows = []
    for i, row in enumerate(candidates.head(args.limit).itertuples(), start=1):
        title = str(row.title) if str(row.title) != "nan" else "Untitled"
        artist = artist_last(row.attribution)
        filename = f"{i:03d}_{slugify(artist)}_{slugify(title)}.jpg"
        dest = os.path.join(output_dir, filename)
        url = build_iiif_url(row.iiifurl, row.src_w, row.src_h, args.width, args.height)

        src_ratio_str = f"{row.src_ratio:.2f}"
        print(f"[{i}/{total}] {title[:60]}")
        print(f"         {artist} · {row.displaydate}")
        print(f"         Source: {row.src_w}×{row.src_h} (ratio {src_ratio_str}, target {TARGET_RATIO:.2f})")

        if os.path.exists(dest):
            print(f"         Already exists, skipping.\n")
        elif download(url, dest):
            size_kb = os.path.getsize(dest) // 1024
            print(f"         Saved {filename} ({size_kb} KB)\n")
        else:
            print(f"         Failed — skipping.\n")
            filename = "FAILED"

        index_rows.append({
            "rank": i,
            "title": title,
            "artist": row.attribution,
            "date": row.displaydate,
            "medium": row.medium,
            "source_size": f"{row.src_w}x{row.src_h}",
            "source_ratio": round(row.src_ratio, 3),
            "iiif_4k_url": url,
            "filename": filename,
        })

    index_path = os.path.join(output_dir, "wallpapers_index.csv")
    with open(index_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=index_rows[0].keys())
        writer.writeheader()
        writer.writerows(index_rows)

    print(f"Done. Index saved to {index_path}")


if __name__ == "__main__":
    main()
