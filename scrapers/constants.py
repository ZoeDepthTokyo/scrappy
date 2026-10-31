"""Shared constants for Scrappy scrapers.

Centralised here so scraper-level (first gate) and notify-level (second gate)
filters stay in sync from a single definition.
"""

# City markers that appear in listing URL slugs for non-CDMX cities.
# CDMX listings never contain these substrings in their URL path.
NON_CDMX_MARKERS: frozenset = frozenset(
    {
        "guadalajara",
        "jalisco",
        "monterrey",
        "nuevo-leon",
        "valle-oriente",       # Monterrey / San Pedro Garza García (NL)
        "san-pedro-garza",     # Monterrey metro (NL)
        "q-roo",               # Quintana Roo slug seen on inmuebles24 Cancún URLs
        "quintana-roo",
        "playa-del-carmen",
        "puebla",
        "queretaro",
        "juriquilla",  # Querétaro — was being tagged as Polanco on inmuebles24
        "jurica",      # Querétaro
        "tijuana",
        "baja-california",
        "cancun",
        "merida",
        "yucatan",
        "leon-gto",
        "guanajuato",
        "san-luis-potosi",
        "aguascalientes",
        "morelia",
        "michoacan",
        "toluca",
        # "estado-de-mexico" intentionally removed — Naucalpan is in EdoMex and is a
        # valid search area. Block only the specific EdoMex cities we don't want:
        "tlalnepantla",
        "ecatepec",
        "nezahualcoyotl",
        "huixquilucan",
        "atizapan",
        "chimalhuacan",
        "ixtapaluca",
        "veracruz",
        "hermosillo",
        "sonora",
        "chihuahua",
        "culiacan",
        "sinaloa",
        "saltillo",
        "coahuila",
        "torreon",
        "zapopan",
        "tlaquepaque",
        "tonala",
        "colinas-de-san-javier",  # Zapopan (GDL metro) — alclapin spam in Polanco results
        "cordillera",              # Querétaro developments (Via Cordillera, Torre Ámbar)
        "la-vista-bosque",         # Querétaro (La Vista by SiRENTA)
    }
)

# Neighborhood names that are too broad to be useful for filtering.
# If a scraper returns one of these, we prefer the search query neighborhood.
BROAD_LOCATION_NAMES: frozenset = frozenset(
    {
        "ciudad de mexico",
        "cdmx",
        "distrito federal",
        "df",
        "mexico",
        "estado de mexico",
        "edomex",
    }
)
