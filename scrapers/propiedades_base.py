"""Shared logic for Propiedades.com scrapers."""

class PropiedadesMixin:
    SOURCE_NAME = "propiedades"

    def _search_urls(self) -> list[str]:
        crit = self.config.get("criteria", {})
        lo = crit.get("price_min", 30000)
        hi = crit.get("price_max", 45000)
        # Propiedades uses 'cuartos' for bedrooms
        beds = crit.get("bedrooms", 2)
        params = f"?precio_min={lo}&precio_max={hi}&cuartos={beds}"
        # Try both URL patterns — /df/ (legacy) and /cdmx/ (current branding)
        return [
            f"https://propiedades.com/df/departamentos-en-renta{params}",
            f"https://propiedades.com/cdmx/departamentos-en-renta{params}",
        ]
