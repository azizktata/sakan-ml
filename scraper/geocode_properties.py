"""
Geocoding pass: adds lat/lng to scraped Tunisie-Annonce JSON.

Reads a raw scrape file (e.g. data/tunisie_annonce/2026-05-19_properties.json),
resolves lat/lng for each entry that's missing them using Nominatim (OpenStreetMap),
and writes to a *_geo.json file (same name with _geo suffix before .json).

Run after tunisie_annonce_scraper.py:
  python geocode_properties.py
  python geocode_properties.py --input data/tunisie_annonce/2026-05-19_properties.json
  python geocode_properties.py --input FILE --output FILE_geo.json

Legacy usage (in-place update of properties_seed.json):
  python geocode_properties.py --input ../sakan-api/database/seeders/data/properties_seed.json --inplace
"""

import argparse
import json
import os
import sys
import time

import requests

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "sakan-geocoder/1.0 (github.com/sakan)"}
DELAY = 1.1  # Nominatim rate limit: max 1 req/sec

# City-center fallback coordinates (if Nominatim fails entirely)
CITY_CENTERS = {
    "tunis":     (36.8190, 10.1658),
    "ariana":    (36.8625, 10.1956),
    "ben-arous": (36.7535, 10.2282),
    "manouba":   (36.8089, 10.0983),
    "bizerte":   (37.2746, 9.8735),
    "monastir":  (35.7643, 10.8113),
    "nabeul":    (36.4513, 10.7357),
    "sousse":    (35.8245, 10.6346),
    "mahdia":    (35.5047, 11.0622),
    "sfax":      (34.7406, 10.7603),
    "kairouan":  (35.6784, 10.0966),
    "gabes":     (33.8831, 10.0982),
    "medenine":  (33.3535, 10.5053),
}


def geocode(query: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "tn"},
            headers=HEADERS,
            timeout=10,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"    [warn] geocode error for '{query}': {e}")
    return None


def resolve_coords(item: dict) -> tuple[float, float] | None:
    city_slug = item.get("city_slug", "")
    neighborhood = item.get("neighborhood") or ""
    gouvernorat = item.get("location_raw", "").split(",")[-1].strip() if "," in item.get("location_raw", "") else ""

    # Try 1: neighborhood + gouvernorat + Tunisia
    if neighborhood and gouvernorat:
        coords = geocode(f"{neighborhood}, {gouvernorat}, Tunisia")
        if coords:
            return coords
        time.sleep(DELAY)

    # Try 2: neighborhood + Tunisia
    if neighborhood:
        coords = geocode(f"{neighborhood}, Tunisia")
        if coords:
            return coords
        time.sleep(DELAY)

    # Try 3: city slug → name + Tunisia
    city_name = city_slug.replace("-", " ").title()
    if city_name:
        coords = geocode(f"{city_name}, Tunisia")
        if coords:
            return coords
        time.sleep(DELAY)

    # Fallback: hardcoded city center
    return CITY_CENTERS.get(city_slug)


def run(input_path: str, output_path: str, inplace: bool = False) -> None:
    with open(input_path, encoding="utf-8") as f:
        listings = json.load(f)

    total = len(listings)
    already = sum(1 for item in listings if item.get("lat") is not None)
    need = total - already
    print(f"Total: {total} | Already geocoded: {already} | To process: {need}")
    print(f"Output: {output_path}")

    updated = 0
    for i, item in enumerate(listings):
        if item.get("lat") is not None:
            continue

        print(f"[{i+1}/{total}] {item.get('ref','?')} — {item.get('location_raw', '?')}...", end=" ", flush=True)
        coords = resolve_coords(item)

        if coords:
            item["lat"], item["lng"] = coords[0], coords[1]
            print(f"→ {coords[0]:.4f}, {coords[1]:.4f}")
        else:
            print("→ no result")

        updated += 1
        time.sleep(DELAY)

        # Checkpoint every 10 items
        if updated % 10 == 0:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(listings, f, ensure_ascii=False, indent=2)
            print(f"  [checkpoint] saved {updated} updates → {output_path}")

    # Final save
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)

    geocoded = sum(1 for item in listings if item.get("lat") is not None)
    print(f"\nDone. {geocoded}/{total} listings have coordinates.")
    print(f"Saved → {output_path}")


if __name__ == "__main__":
    import glob as _glob

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    TA_DATA_DIR = os.path.join(SCRIPT_DIR, "data", "tunisie_annonce")

    # Default input: latest *_properties.json in data/tunisie_annonce/
    _raw_files = sorted(_glob.glob(os.path.join(TA_DATA_DIR, "*_properties.json")))
    _legacy_path = os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "sakan-api", "database", "seeders", "data", "properties_seed.json"
    ))
    _default_input = _raw_files[-1] if _raw_files else _legacy_path

    parser = argparse.ArgumentParser(description="Geocode scraped property JSON with Nominatim")
    parser.add_argument("--input",   default=_default_input, help="Raw scrape JSON")
    parser.add_argument("--output",  default=None,           help="Output path (default: <input>_geo.json or in-place for legacy)")
    parser.add_argument("--inplace", action="store_true",    help="Write back to the same file (legacy behavior)")
    args = parser.parse_args()

    in_path = os.path.normpath(args.input)

    if args.inplace or args.output:
        out_path = os.path.normpath(args.output) if args.output else in_path
    else:
        # Default: insert _geo before .json suffix
        base, ext = os.path.splitext(in_path)
        # Remove existing _geo suffix to avoid doubling
        if base.endswith("_geo"):
            base = base[:-4]
        out_path = base + "_geo" + ext

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    run(in_path, out_path, inplace=args.inplace)
