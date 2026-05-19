"""
Tunisia geographic reference data.

Maps raw city/neighborhood strings → (city_slug, governorate, zone_score).

zone_score scale (1–5):
  5 = premium coastal / high-demand (La Marsa, Gammarth, Carthage, Yasmine Hammamet, Djerba resort)
  4 = upper-mid capital districts (Tunis centre, El Menzah, Ennasr, Hammamet, Khezama/Sahloul)
  3 = main regional cities (Ariana, Sousse, Sfax, Monastir, Bizerte, Nabeul)
  2 = secondary towns and periphery (Ben Arous, Manouba, Mahdia, Gabès, Tozeur)
  1 = interior / low-demand (Kairouan, Kasserine, Zaghouan, Siliana, Tataouine)
"""

import re
import unicodedata

# ── All 24 Tunisian governorates ──────────────────────────────────────────────

GOVERNORATES: list[str] = [
    # Greater Tunis
    "Tunis", "Ariana", "Ben Arous", "Manouba",
    # North-East
    "Nabeul", "Zaghouan", "Bizerte",
    # North-West
    "Béja", "Jendouba", "Le Kef", "Siliana",
    # Sahel
    "Sousse", "Monastir", "Mahdia",
    # Centre
    "Kairouan", "Kasserine", "Sidi Bouzid",
    # Centre-East
    "Sfax",
    # South-West
    "Gafsa", "Tozeur", "Kébili",
    # South-East
    "Gabès", "Médenine", "Tataouine",
]

# ── Geo map ───────────────────────────────────────────────────────────────────
#
# Keys are lowercase + accent-stripped (see _normalize_key).
# Ordering matters for substring scan: longer / more specific keys must come
# BEFORE shorter ambiguous ones to avoid "lac" shadowing "berges du lac".

GEO_MAP: dict[str, tuple[str, str, int]] = {

    # ── Greater Tunis — premium zones ─────────────────────────────────────
    "les berges du lac":    ("tunis",      "Tunis",     5),
    "berges du lac":        ("tunis",      "Tunis",     5),
    "lac 2":                ("tunis",      "Tunis",     5),
    "lac 1":                ("tunis",      "Tunis",     5),
    "sidi bou said":        ("tunis",      "Tunis",     5),
    "la marsa plage":       ("la-marsa",   "Tunis",     5),
    "la marsa":             ("la-marsa",   "Tunis",     5),
    "la-marsa":             ("la-marsa",   "Tunis",     5),
    "marsa":                ("la-marsa",   "Tunis",     5),
    "gammarth":             ("la-marsa",   "Tunis",     5),
    "carthage":             ("tunis",      "Tunis",     5),

    # ── Greater Tunis — upper-mid ──────────────────────────────────────────
    "el menzeh 9":          ("ariana",     "Ariana",    4),
    "el menzah 9":          ("ariana",     "Ariana",    4),
    "el menzeh":            ("ariana",     "Ariana",    4),
    "el menzah":            ("ariana",     "Ariana",    4),
    "el manar 2":           ("tunis",      "Tunis",     4),
    "el manar 1":           ("tunis",      "Tunis",     4),
    "el manar":             ("tunis",      "Tunis",     4),
    "ennasr 2":             ("ariana",     "Ariana",    4),
    "ennasr 1":             ("ariana",     "Ariana",    4),
    "ennasr":               ("ariana",     "Ariana",    4),
    "cite el khadra":       ("tunis",      "Tunis",     4),
    "el khadra":            ("tunis",      "Tunis",     4),
    "montplaisir":          ("tunis",      "Tunis",     4),
    "mutuelleville":        ("tunis",      "Tunis",     4),
    "belvedere":            ("tunis",      "Tunis",     4),
    "el aouina":            ("tunis",      "Tunis",     4),
    "aouina":               ("tunis",      "Tunis",     4),
    "jardins de carthage":  ("tunis",      "Tunis",     4),
    "cite mahrajene":       ("tunis",      "Tunis",     4),
    "mahrajene":            ("tunis",      "Tunis",     4),
    "lafayette":            ("tunis",      "Tunis",     4),

    # ── Greater Tunis — mid zones ──────────────────────────────────────────
    "bab saadoun":          ("tunis",      "Tunis",     3),
    "bab souika":           ("tunis",      "Tunis",     3),
    "bab bhar":             ("tunis",      "Tunis",     3),
    "medina":               ("tunis",      "Tunis",     3),
    "la medina":            ("tunis",      "Tunis",     3),
    "el omrane superieur":  ("tunis",      "Tunis",     3),
    "el omrane":            ("tunis",      "Tunis",     3),
    "bardo":                ("tunis",      "Tunis",     3),
    "la goulette":          ("tunis",      "Tunis",     3),
    "kram":                 ("tunis",      "Tunis",     3),
    "le kram":              ("tunis",      "Tunis",     3),
    "ezzouhour":            ("tunis",      "Tunis",     3),
    "cite ibn khaldoun":    ("tunis",      "Tunis",     3),
    "cite sportive":        ("tunis",      "Tunis",     3),
    "tunis":                ("tunis",      "Tunis",     4),

    # ── Ariana ────────────────────────────────────────────────────────────
    "la soukra":            ("ariana",     "Ariana",    3),
    "raoued":               ("ariana",     "Ariana",    3),
    "ariana ville":         ("ariana",     "Ariana",    3),
    "ariana":               ("ariana",     "Ariana",    3),

    # ── Ben Arous ─────────────────────────────────────────────────────────
    "hammam lif":           ("ben-arous",  "Ben Arous", 3),
    "rades":                ("ben-arous",  "Ben Arous", 3),
    "megrine":              ("ben-arous",  "Ben Arous", 2),
    "ezzahra":              ("ben-arous",  "Ben Arous", 2),
    "fouchana":             ("ben-arous",  "Ben Arous", 2),
    "bou mhel":             ("ben-arous",  "Ben Arous", 2),
    "ben arous":            ("ben-arous",  "Ben Arous", 3),
    "ben-arous":            ("ben-arous",  "Ben Arous", 3),

    # ── Manouba ───────────────────────────────────────────────────────────
    "mohamedia":            ("manouba",    "Manouba",   2),
    "oued ellil":           ("manouba",    "Manouba",   2),
    "manouba":              ("manouba",    "Manouba",   2),

    # ── Cap Bon — Nabeul ──────────────────────────────────────────────────
    "yasmine hammamet":     ("hammamet",   "Nabeul",    5),
    "hammamet nord":        ("hammamet",   "Nabeul",    4),
    "hammamet sud":         ("hammamet",   "Nabeul",    4),
    "hammamet":             ("hammamet",   "Nabeul",    4),
    "nabeul":               ("nabeul",     "Nabeul",    3),
    "kelibia":              ("kelibia",    "Nabeul",    2),
    "korba":                ("nabeul",     "Nabeul",    2),
    "menzel temime":        ("nabeul",     "Nabeul",    2),
    "soliman":              ("nabeul",     "Nabeul",    2),
    "grombalia":            ("nabeul",     "Nabeul",    2),

    # ── Zaghouan ──────────────────────────────────────────────────────────
    "zaghouan":             ("zaghouan",   "Zaghouan",  1),

    # ── Bizerte / North ───────────────────────────────────────────────────
    "bizerte":              ("bizerte",    "Bizerte",   2),
    "menzel bourguiba":     ("bizerte",    "Bizerte",   2),
    "mateur":               ("bizerte",    "Bizerte",   1),

    # ── North-West ────────────────────────────────────────────────────────
    "beja":                 ("beja",       "Béja",      1),
    "jendouba":             ("jendouba",   "Jendouba",  1),
    "le kef":               ("le-kef",     "Le Kef",    1),
    "siliana":              ("siliana",    "Siliana",   1),

    # ── Sahel — Sousse ────────────────────────────────────────────────────
    "khezama est":          ("sousse",     "Sousse",    4),
    "khezama ouest":        ("sousse",     "Sousse",    4),
    "khezama":              ("sousse",     "Sousse",    4),
    "sahloul":              ("sousse",     "Sousse",    4),
    "hammam sousse":        ("sousse",     "Sousse",    3),
    "akouda":               ("sousse",     "Sousse",    3),
    "sousse ville":         ("sousse",     "Sousse",    3),
    "sousse":               ("sousse",     "Sousse",    3),
    "kalaa kebira":         ("sousse",     "Sousse",    2),
    "kalaa sghira":         ("sousse",     "Sousse",    2),
    "msaken":               ("sousse",     "Sousse",    2),

    # ── Sahel — Monastir ──────────────────────────────────────────────────
    "skanes":               ("monastir",   "Monastir",  3),
    "monastir":             ("monastir",   "Monastir",  3),
    "ksar hellal":          ("monastir",   "Monastir",  2),
    "moknine":              ("monastir",   "Monastir",  2),
    "jemmal":               ("monastir",   "Monastir",  2),

    # ── Sahel — Mahdia ────────────────────────────────────────────────────
    "mahdia":               ("mahdia",     "Mahdia",    2),
    "ksour essef":          ("mahdia",     "Mahdia",    1),
    "chebba":               ("mahdia",     "Mahdia",    1),

    # ── Centre ────────────────────────────────────────────────────────────
    "kairouan":             ("kairouan",   "Kairouan",  1),
    "kasserine":            ("kasserine",  "Kasserine", 1),
    "sidi bouzid":          ("sidi-bouzid","Sidi Bouzid",1),

    # ── Sfax ──────────────────────────────────────────────────────────────
    "sfax ville":           ("sfax",       "Sfax",      3),
    "sakiet ezzit":         ("sfax",       "Sfax",      3),
    "sakiet eddaier":       ("sfax",       "Sfax",      3),
    "sfax":                 ("sfax",       "Sfax",      3),
    "chihia":               ("sfax",       "Sfax",      2),
    "agareb":               ("sfax",       "Sfax",      2),
    "el ain":               ("sfax",       "Sfax",      2),

    # ── South-West ────────────────────────────────────────────────────────
    "gafsa":                ("gafsa",      "Gafsa",     1),
    "tozeur":               ("tozeur",     "Tozeur",    2),
    "kebili":               ("kebili",     "Kébili",    1),

    # ── South-East ────────────────────────────────────────────────────────
    "djerba":               ("djerba",     "Médenine",  4),
    "houmt souk":           ("djerba",     "Médenine",  3),
    "midoun":               ("djerba",     "Médenine",  3),
    "zarzis":               ("zarzis",     "Médenine",  2),
    "gabes":                ("gabes",      "Gabès",     2),
    "medenine":             ("medenine",   "Médenine",  1),
    "tataouine":            ("tataouine",  "Tataouine", 1),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_key(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_text).lower().strip()


# Pre-sort keys longest-first so longer/specific keys always win over short ones.
_SORTED_KEYS = sorted(GEO_MAP.keys(), key=len, reverse=True)


def lookup_city(raw_text: str | None) -> tuple[str, str, int] | None:
    """
    Given any raw location string, return (city_slug, governorate, zone_score).

    Lookup order:
      1. Exact match after normalization.
      2. Longest-first substring scan (prevents "lac" shadowing "berges du lac").
      3. Comma-split fallback — try each part individually.
    """
    if not raw_text:
        return None

    normalized = _normalize_key(raw_text)

    if normalized in GEO_MAP:
        return GEO_MAP[normalized]

    for key in _SORTED_KEYS:
        if key in normalized:
            return GEO_MAP[key]

    for part in raw_text.split(","):
        part = part.strip()
        if not part:
            continue
        norm_part = _normalize_key(part)
        if norm_part in GEO_MAP:
            return GEO_MAP[norm_part]
        for key in _SORTED_KEYS:
            if key in norm_part:
                return GEO_MAP[key]

    return None



# Keys in GEO_MAP that represent standalone cities (not sub-districts).
# A GEO_MAP entry is a "standalone city" when its key == its own city_slug
# (possibly with spaces converted to dashes).
# Built at import time from GEO_MAP itself — no hand-maintained list needed.
_STANDALONE_CITY_KEYS: frozenset[str] = frozenset(
    key for key, (slug, gov, score) in GEO_MAP.items()
    if key.replace(" ", "-") == slug or key == slug
)


def extract_neighborhood(raw_text: str | None) -> str | None:
    """
    Extract the sub-area (neighborhood/arrondissement) from a raw location string.

    Mubawab format: "<neighborhood>, <city>"
    Examples:
      "Khezama, Sousse"      → "Khezama"   (Khezama is a sub-district, not a city key)
      "Bab Saadoun, Tunis"   → "Bab Saadoun"
      "La Marsa, Tunis"      → None  (La Marsa is a standalone city key)
      "Tunis, Tunis"         → None  (left equals right)
      "Tunis"                → None  (no comma)

    Rule: the left part is a neighborhood UNLESS:
      - it equals the right part (redundant label), OR
      - its normalized form exists in _STANDALONE_CITY_KEYS (it IS a city).
    """
    if not raw_text or "," not in raw_text:
        return None

    left, _, right = raw_text.partition(",")
    candidate_hood = left.strip()
    if not candidate_hood:
        return None

    norm_hood = _normalize_key(candidate_hood)
    norm_city = _normalize_key(right.strip())

    # Redundant (e.g. "Tunis, Tunis")
    if norm_hood == norm_city:
        return None

    # Left part is itself a standalone city name → suppress
    if norm_hood in _STANDALONE_CITY_KEYS:
        return None

    return candidate_hood


def city_from_url_slug(url_slug: str | None) -> tuple[str, str, int] | None:
    """
    Derive city info from a Mubawab URL path segment.
    e.g. 'sousse-ville' → 'sousse ville' → lookup_city()
    """
    if not url_slug:
        return None
    return lookup_city(url_slug.replace("-", " "))
