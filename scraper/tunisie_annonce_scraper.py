"""
Tunisie-Annonce property scraper -- standalone, outputs JSON.

Verified HTML structure (inspected 2026-05-15 via Chrome DevTools):
  - List page: tr.Tableau1 rows, link href="Details_Annonces_Immobilier.asp?cod_ann=XXXXXX"
  - Detail page: td labels (Categorie, Localisation, Adresse, Surface, Prix)
  - Images: img[id^="PhotoMin"] with src in upload2/.../photos/...jpg
  - Region codes from ajax/_region.asp?parent=TN (XML)

Run:
  python tunisie_annonce_scraper.py
  python tunisie_annonce_scraper.py --per-region 10 --transaction vente
  python tunisie_annonce_scraper.py --per-region 3 --transaction vente --output test.json
"""

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup

# Force UTF-8 output on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "http://www.tunisie-annonce.com"

# Real region codes from ajax/_region.asp?parent=TN (verified)
REGIONS = [
    {"code": 101, "slug": "tunis",       "name": "Tunis"},
    {"code": 102, "slug": "ariana",      "name": "Ariana"},
    {"code": 103, "slug": "ben-arous",   "name": "Ben Arous"},
    {"code": 114, "slug": "manouba",     "name": "Manouba"},
    {"code": 104, "slug": "bizerte",     "name": "Bizerte"},
    {"code": 116, "slug": "monastir",    "name": "Monastir"},
    {"code": 117, "slug": "nabeul",      "name": "Nabeul"},
    {"code": 121, "slug": "sousse",      "name": "Sousse"},
    {"code": 113, "slug": "mahdia",      "name": "Mahdia"},
    {"code": 118, "slug": "sfax",        "name": "Sfax"},
    {"code": 109, "slug": "kairouan",    "name": "Kairouan"},
    {"code": 106, "slug": "gabes",       "name": "Gabès"},
    {"code": 115, "slug": "medenine",    "name": "Médenine"},
]

# rech_cod_typ values
TX_CODES = {
    "vente":    "10102",
    "location": "10101",
}
TX_MAP = {
    "vente":    "sale",
    "location": "rent",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": BASE_URL,
}

DELAY_LIST   = (2.0, 4.0)
DELAY_DETAIL = (1.5, 3.0)
MAX_RETRIES  = 3
MIN_PRICE_SALE = 5_000    # DT — filter junk vente listings
MIN_PRICE_RENT = 300      # DT/month — filter junk location listings

# ── Property type inference ───────────────────────────────────────────────────

# Maps substrings in the Categorie field to property_type
TYPE_MAP = [
    ("appart",   "apartment"),
    ("studio",   "apartment"),
    ("duplex",   "apartment"),
    ("villa",    "villa"),
    ("maison",   "house"),
    ("terrain",  "land"),
    ("surface",  "land"),
    ("local",    "commercial"),
    ("boutique", "commercial"),
    ("usine",    "commercial"),
    ("entrepot", "commercial"),
    ("bureau",   "office"),
    ("ferme",    "house"),
    ("autre",    "commercial"),
]

# ── Amenity detection ─────────────────────────────────────────────────────────

AMENITY_KEYWORDS = {
    "garage":        ["parking", "garage", "box auto", "abri voiture", "abri de voiture", "place de parking"],
    "ascenseur":     ["ascenseur"],
    "jardin":        ["jardin", "espace vert", "espaces verts"],
    "terrasse":      ["terrasse", "toit-terrasse", "toit terrasse", "toiture terrasse"],
    "piscine":       ["piscine"],
    "meuble":        ["meublé", "meublee", "meuble", "équipé", "equipee", "équipée", "equipe", "tout équipé", "tout equipe"],
    "gardien":       ["gardien", "gardiennage", "sécurité", "securite", "interphone", "digicode", "visiophone"],
    "climatisation": ["climatisation", "climatisé", "climatise", "clim", "air conditionné", "air conditionne"],
}

# ── HTTP ──────────────────────────────────────────────────────────────────────

def fetch(url: str, params: dict | None = None) -> BeautifulSoup | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp.status_code == 200:
                # Pass raw bytes + explicit charset so BS4 decodes cleanly to unicode
                return BeautifulSoup(resp.content, "html.parser", from_encoding="iso-8859-1")
            print(f"    [warn] HTTP {resp.status_code} attempt {attempt}: {url[:80]}")
        except requests.RequestException as exc:
            print(f"    [error] attempt {attempt}: {exc}")
        time.sleep(2 ** attempt)
    return None

# ── Parsing ───────────────────────────────────────────────────────────────────

_PRICE_RE    = re.compile(r"([\d][\d\s\xa0 ]*)\s*(?:Dinar|DT|TND)", re.IGNORECASE)
_SURFACE_RE  = re.compile(r"(\d+)\s*m[²2]?", re.IGNORECASE)

# Bedrooms: "3 chambres", "s+3", "s3", "3ch", "trois chambres"
_BEDROOM_RE  = re.compile(
    r"(?:s\s*[+\-]\s*(\d+)|s(\d+))\b"          # s+3, s3 (studio notation)
    r"|(\d+)\s*(?:chambre|ch\.?\b|bedroom)",
    re.IGNORECASE
)
_PIECE_RE    = re.compile(r"(\d+)\s*(?:pi[eè]ces?|rooms?|pce)", re.IGNORECASE)

# Bathrooms: "2 salles de bain", "salle d'eau", "douchette", "sdb", "wc", "toilette"
_BATH_RE     = re.compile(
    r"(\d+)\s*(?:salle[s]?\s*(?:de\s*)?bain|s\.?d\.?b\.?|sdb)"
    r"|(?:salle[s]?\s*d['\"]?\s*eau|douche(?:tte)?|wc|toilette)",
    re.IGNORECASE
)

# Floor: "2ème étage", "au 3e étage", "3eme", "rez-de-chaussée", "rdc"
_FLOOR_RE    = re.compile(
    r"(?:au\s+)?(\d+)\s*[eèé][mr]?[eé]?\s*[eé]tage"
    r"|rez[\s\-]+de[\s\-]+chauss[eé]e"
    r"|\brdc\b",
    re.IGNORECASE
)
_YEAR_RE     = re.compile(r"\b(19[5-9]\d|20[012]\d)\b")


def parse_price(text: str, min_price: int = MIN_PRICE_SALE) -> int | None:
    text = text.replace("\xa0", "").replace(" ", "")
    m = _PRICE_RE.search(text)
    if not m:
        return None
    val = int(re.sub(r"\D", "", m.group(1)))
    return val if val >= min_price else None


def parse_surface(text: str) -> int | None:
    for m in _SURFACE_RE.finditer(text):
        val = int(m.group(1))
        if 10 <= val <= 5000:
            return val
    return None


def parse_bedrooms(combined: str) -> int | None:
    """
    Parse bedroom count from Tunisian French property text.
    Handles: "3 chambres", "s+2" (salon + 2 chambres), "s2", "3ch"
    Falls back to pièces-1 if no explicit bedroom mention.
    """
    # Pass 1: explicit chambre keyword
    for m in _BEDROOM_RE.finditer(combined):
        # Group 1: s+N, Group 2: sN, Group 3: N chambres/ch
        val_str = m.group(1) or m.group(2) or m.group(3)
        if val_str:
            val = int(val_str)
            if 1 <= val <= 10:
                return val
    # Pass 2: pièces (total rooms) - subtract 1 for living room
    for m in _PIECE_RE.finditer(combined):
        val = int(m.group(1))
        if 2 <= val <= 12:
            return max(1, val - 1)
    return None


def parse_bathrooms(text: str) -> int | None:
    """
    Parse bathroom count. Returns count if explicit number, 1 if keyword found.
    Handles: "2 salles de bain", "salle d'eau", "douchette", "wc", "sdb".
    """
    for m in _BATH_RE.finditer(text):
        if m.group(1):  # explicit count e.g. "2 salles de bain"
            val = int(m.group(1))
            return val if 1 <= val <= 5 else 1
        return 1  # keyword found without explicit count
    return None


def parse_floor(text: str) -> int | None:
    """
    Parse floor number. Returns 0 for rez-de-chaussée/rdc, N for Nème étage.
    """
    for m in _FLOOR_RE.finditer(text):
        if m.group(1):
            return int(m.group(1))
        return 0  # rez-de-chaussée or rdc
    return None


def parse_year(text: str) -> int | None:
    matches = _YEAR_RE.findall(text)
    years = [int(y) for y in matches if 1950 <= int(y) <= 2026]
    return max(years) if years else None  # take most recent year mentioned


_TITLE_JUNK_RE = re.compile(
    r"\s+(?:wb|ref|#)\w+\s*$"          # trailing wb1334, ref05, #OF95...
    r"|\s+[a-z]{1,3}\d{3,}\s*$"        # trailing abbreviation+digits like ms123
    r"|\s{2,}",                         # double spaces anywhere
    re.IGNORECASE
)


def clean_title(title: str) -> str:
    """Remove trailing agent reference codes and normalise whitespace."""
    t = _TITLE_JUNK_RE.sub(lambda m: " " if m.group(0).strip() == "" else "", title)
    # Remove trailing ref codes iteratively (they can chain: "title wb123 ref04")
    for _ in range(3):
        t2 = re.sub(r"\s+(?:wb|ref|#|\bms\b|\bvvm\b|\bvv\b)[a-z0-9]+\s*$", "", t, flags=re.IGNORECASE).strip()
        if t2 == t:
            break
        t = t2
    return t.strip()


def infer_type(categorie: str) -> str:
    low = categorie.lower()
    for kw, ptype in TYPE_MAP:
        if kw in low:
            return ptype
    return "apartment"


def detect_amenities(text: str) -> list[str]:
    low = text.lower()
    return [slug for slug, kws in AMENITY_KEYWORDS.items() if any(kw in low for kw in kws)]


def parse_location_parts(localisation: str):
    """
    'Tunisie > Tunis > Le Bardo > Le Bardo'
    returns (gouvernorat='Tunis', delegation='Le Bardo', localite='Le Bardo')
    """
    parts = [p.strip() for p in localisation.split(">")]
    # parts[0] = Tunisie, [1] = gouvernorat, [2] = délégation, [3] = localité
    gouvernorat = parts[1] if len(parts) > 1 else None
    delegation  = parts[2] if len(parts) > 2 else None
    localite    = parts[3] if len(parts) > 3 else delegation
    return gouvernorat, delegation, localite


def get_detail_field(soup: BeautifulSoup, label: str) -> str | None:
    """Find a da_label_field td whose text == label, return the da_field_text sibling."""
    # Prefer class-based lookup (more precise)
    for td in soup.find_all("td", class_="da_label_field"):
        if td.get_text(strip=True) == label:
            nxt = td.find_next_sibling("td")
            if nxt:
                return nxt.get_text(" ", strip=True)
    # Fallback: any td
    for td in soup.find_all("td"):
        if td.get_text(strip=True) == label:
            nxt = td.find_next_sibling("td")
            if nxt:
                return nxt.get_text(" ", strip=True)
    return None

# ── List page scraping ────────────────────────────────────────────────────────

def scrape_list_page(region_code: int, page: int, tx_type: str) -> list[dict]:
    """Returns stub dicts for each listing row on a search-results page."""
    params = {
        "rech_cod_rub":     "101",
        "rech_cod_typ":     TX_CODES[tx_type],
        "rech_cod_reg":     str(region_code),
        "rech_page_num":    str(page),
        "rech_cod_pay":     "TN",
        "rech_cod_sou_typ": "",
        "rech_prix_min":    "",
        "rech_prix_max":    "",
        "rech_surf_min":    "",
        "rech_surf_max":    "",
        "rech_age_ann":     "",
        "rech_photo":       "",
    }
    soup = fetch(f"{BASE_URL}/AnnoncesImmobilier.asp", params=params)
    if not soup:
        return []

    stubs = []
    for row in soup.select("tr.Tableau1"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        link = row.find("a", href=lambda h: h and "Details_Annonces_Immobilier" in h)
        if not link:
            continue

        href  = link.get("href", "")
        m     = re.search(r"cod_ann=(\d+)", href)
        if not m:
            continue

        cod   = m.group(1)
        title = link.get_text(strip=True)

        # Price is in the 4th cell (0-indexed after separators)
        # Cell texts after stripping spacer images
        cell_texts = [c.get_text(" ", strip=True).replace("\xa0", "") for c in cells]
        price = None
        for ct in cell_texts:
            digits = re.sub(r"\D", "", ct)
            if digits and 4 <= len(digits) <= 9:
                val = int(digits)
                min_p = MIN_PRICE_RENT if tx_type == "location" else MIN_PRICE_SALE
                if val >= min_p:
                    price = val
                    break

        # Region/location text (first meaningful cell)
        location_raw = None
        for ct in cell_texts:
            if ct and len(ct) > 2 and not ct.isdigit() and "/" not in ct:
                location_raw = ct
                break

        # Property type hint from type cell
        type_raw = None
        for ct in cell_texts:
            if any(kw in ct.lower() for kw in ["app", "maison", "villa", "terrain", "surface", "local", "bureau", "duplex"]):
                type_raw = ct
                break

        # Camera icon present = has photos
        has_photo = bool(row.find("img", src=lambda s: s and "camera" in s.lower()))

        stubs.append({
            "cod_ann":     cod,
            "title":       title,
            "price_hint":  price,
            "location_raw": location_raw,
            "type_raw":    type_raw,
            "has_photo":   has_photo,
            "detail_url":  f"{BASE_URL}/{href}",
        })

    return stubs


# ── Detail page scraping ──────────────────────────────────────────────────────

def scrape_detail(stub: dict, region: dict, tx_type: str) -> dict | None:
    """
    Fetches the detail page.
    Returns None if listing doesn't qualify (no images or price < MIN_PRICE).
    """
    soup = fetch(stub["detail_url"])
    if not soup:
        return None

    # ── Images: img[id^="PhotoMin"] ──────────────────────────────────────────
    images = []
    for img in soup.find_all("img", id=re.compile(r"^PhotoMin_\d+")):
        src = img.get("src", "")
        if src and "upload" in src:
            images.append(src if src.startswith("http") else BASE_URL + src)
    # Deduplicate
    images = list(dict.fromkeys(images))[:6]

    if not images:
        return None  # must have at least 1 real photo

    # ── Full title from detail page (overrides truncated list-page title) ────
    detail_title = None
    ref_pat = re.compile(r"^\[Réf:\d+\]\s*(.+)", re.IGNORECASE)
    for td in soup.find_all("td"):
        txt = td.get_text(" ", strip=True)
        m = ref_pat.match(txt)
        if m and len(txt) < 200:
            detail_title = m.group(1).strip()
            break

    # ── Structured fields ────────────────────────────────────────────────────
    categorie    = get_detail_field(soup, "Catégorie") or ""
    localisation = get_detail_field(soup, "Localisation") or ""
    adresse      = get_detail_field(soup, "Adresse") or ""
    surface_raw  = get_detail_field(soup, "Surface") or ""
    prix_raw     = get_detail_field(soup, "Prix") or ""

    # ── Price (detail is authoritative) ─────────────────────────────────────
    min_price = MIN_PRICE_RENT if tx_type == "location" else MIN_PRICE_SALE
    price = parse_price(prix_raw, min_price)
    if not price:
        price = stub.get("price_hint")
    if not price or price < min_price:
        return None

    # ── Location ─────────────────────────────────────────────────────────────
    gouvernorat, delegation, localite = parse_location_parts(localisation)
    neighborhood = localite or delegation or adresse or None
    location_display = f"{neighborhood}, {gouvernorat}" if neighborhood and gouvernorat else (gouvernorat or region["name"])

    # ── Property type (from Catégorie) ───────────────────────────────────────
    property_type = infer_type(categorie or stub.get("type_raw", ""))

    # ── Description: label is "Texte" on Tunisie-Annonce detail pages ──────────
    description = None
    desc_field = get_detail_field(soup, "Texte")
    if desc_field and len(desc_field) > 10:
        description = desc_field[:1500]

    # ── Full enrichment text (title + description + categorie) ──────────────
    full_text = f"{detail_title or stub.get('title','')} {categorie} {description or ''} {surface_raw}"

    # ── Surface ──────────────────────────────────────────────────────────────
    surface = parse_surface(surface_raw) or parse_surface(description or "")

    # ── Bedrooms: categorie hint first ("Appart. 3 pièces"), then full text ──
    bedrooms = parse_bedrooms(full_text)

    # ── Bathrooms: search full text (description + title) ────────────────────
    bathrooms = parse_bathrooms(full_text)

    # ── Floor: search full text ───────────────────────────────────────────────
    floor = parse_floor(full_text)

    # ── Building age from year mentioned in description ───────────────────────
    build_year = parse_year(description or "")
    building_age = (2026 - build_year) if build_year else None

    # ── Amenities: detect from full text ─────────────────────────────────────
    amenities = detect_amenities(full_text)

    # ── Furnished: explicit keywords in title/description ────────────────────
    _furnished_re = re.compile(
        r"meublé|meublee|meublée|tout\s+équipé|tout\s+equipe|avec\s+meubles?",
        re.IGNORECASE
    )
    is_furnished = bool(_furnished_re.search(full_text))

    return {
        "ref":              f"TA-{stub['cod_ann']}",
        "title":            clean_title(detail_title or stub["title"] or f"{property_type.title()} - {neighborhood}"),
        "description":      description,
        "price":            price,
        "transaction_type": TX_MAP[tx_type],
        "property_type":    property_type,
        "city_slug":        region["slug"],
        "region_code":      region["code"],
        "location_raw":     location_display,
        "neighborhood":     neighborhood,
        "surface":          surface,
        "bedrooms":         bedrooms,
        "bathrooms":        bathrooms,
        "floor":            floor,
        "building_age":     building_age,
        "is_furnished":     is_furnished,
        "amenities":        amenities,
        "images":           images,
        "source_url":       stub["detail_url"],
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def scrape_region(region: dict, tx_type: str, per_region: int) -> list[dict]:
    print(f"\n=== [{region['name']}] {tx_type} (target: {per_region}) ===")
    collected: list[dict] = []
    seen: set[str] = set()
    page = 1

    while len(collected) < per_region:
        print(f"  Page {page}...", end=" ", flush=True)
        stubs = scrape_list_page(region["code"], page, tx_type)

        if not stubs:
            print("empty -- stopping")
            break
        print(f"{len(stubs)} rows")

        for stub in stubs:
            if len(collected) >= per_region:
                break
            cod = stub["cod_ann"]
            if cod in seen:
                continue
            seen.add(cod)

            # Skip rows with no camera icon when we have enough candidates
            # (still try them early on to avoid missing good listings)
            if not stub["has_photo"] and len(stubs) > 5:
                continue

            time.sleep(random.uniform(*DELAY_DETAIL))
            print(f"    {cod} '{stub['title'][:40]}'...", end=" ", flush=True)

            listing = scrape_detail(stub, region, tx_type)
            if listing:
                collected.append(listing)
                print(f"OK price={listing['price']:,} imgs={len(listing['images'])}")
            else:
                print("skip")

        if len(collected) < per_region:
            time.sleep(random.uniform(*DELAY_LIST))
            page += 1
            if page > 30:
                print("  [warn] hit page limit")
                break

    print(f"  Collected {len(collected)}/{per_region} for {region['name']}")
    return collected


def run(per_region: int, transaction_types: list[str], output_path: str) -> None:
    # Load existing output for incremental re-runs
    all_listings: list[dict] = []
    existing_refs: set[str] = set()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if os.path.exists(output_path):
        with open(output_path, encoding="utf-8") as f:
            try:
                existing = json.load(f)
                all_listings = existing
                existing_refs = {item["ref"] for item in existing}
                print(f"Loaded {len(existing)} existing listings from output file")
            except (json.JSONDecodeError, KeyError):
                pass

    for tx_type in transaction_types:
        for region in REGIONS:
            already = sum(
                1 for item in all_listings
                if item["city_slug"] == region["slug"]
                and item["transaction_type"] == TX_MAP[tx_type]
            )
            need = per_region - already
            if need <= 0:
                print(f"[skip] {region['name']} {tx_type} -- already have {already}")
                continue

            new_listings = scrape_region(region, tx_type, need)
            for listing in new_listings:
                if listing["ref"] not in existing_refs:
                    all_listings.append(listing)
                    existing_refs.add(listing["ref"])

            # Checkpoint save after each region
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(all_listings, f, ensure_ascii=False, indent=2)
            print(f"  [saved] {len(all_listings)} total -> {output_path}")

    # Summary
    print(f"\nDone. Total: {len(all_listings)} listings")
    counts = Counter(f"{i['city_slug']} ({i['transaction_type']})" for i in all_listings)
    for key, count in sorted(counts.items()):
        print(f"  {key}: {count}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date as _date

    _today     = _date.today().isoformat()  # e.g. "2026-05-19"
    _data_dir  = os.path.join(os.path.dirname(__file__), "data", "tunisie_annonce")
    _default_output = os.path.normpath(os.path.join(_data_dir, f"{_today}_properties.json"))

    parser = argparse.ArgumentParser(description="Tunisie-Annonce scraper -> JSON")
    parser.add_argument("--per-region", type=int, default=10)
    parser.add_argument("--transaction", choices=["vente", "location", "both"], default="both")
    parser.add_argument("--output", default=_default_output,
                        help="Output JSON path (default: data/tunisie_annonce/YYYY-MM-DD_properties.json)")
    args = parser.parse_args()

    tx_types = ["vente", "location"] if args.transaction == "both" else [args.transaction]
    output   = os.path.normpath(args.output)

    print(f"Config: per_region={args.per_region}, tx={tx_types}")
    print(f"Output: {output}\n")
    run(per_region=args.per_region, transaction_types=tx_types, output_path=output)
