"""TA/Petro fuel prices — scraped from JSON-LD on location pages."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import aiohttp

from app.config import Config
from app.providers.base import StationPriceProvider, Station, GeoLocation
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

ALL_LOCATIONS_URL = "https://www.ta-petro.com/location/all-locations/"
LOCATION_URL = "https://www.ta-petro.com/location/{state}/{slug}/"

# Regex to extract JSON-LD blocks
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


class TAPetroPriceProvider(StationPriceProvider):
    """Fetches TA/Petro fuel prices from JSON-LD on their website."""

    def __init__(self, config: Config, cache: Cache) -> None:
        self._cache = cache
        self._ttl = config.cache_retail_ttl
        self._slug_map: dict[str, str] = {}  # "ta-porter" → "/location/in/ta-porter/"

    async def _fetch_store_prices(self, url: str, session: aiohttp.ClientSession) -> Optional[dict]:
        """Fetch prices from a single TA/Petro location page using a shared session."""
        cache_key = f"tapetro:{url}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=6),
                headers={"User-Agent": "TankTok/1.0"},
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                html = await resp.text()
        except Exception:
            logger.debug("TA/Petro fetch failed: %s", url)
            return None

        # Extract JSON-LD
        for match in JSONLD_RE.finditer(html):
            try:
                ld = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

            if ld.get("@type") != "GasStation":
                continue

            catalog = ld.get("hasOfferCatalog", {})
            items = catalog.get("itemListElement", [])

            diesel = None
            gas = None
            for item in items:
                name = (item.get("name") or "").lower()
                price_str = item.get("price")
                if price_str is None:
                    continue
                try:
                    price = float(price_str)
                except (TypeError, ValueError):
                    continue

                if any(kw in name for kw in ("diesel", "dsl", "ulsd")) and "def" not in name:
                    if diesel is None:  # take first diesel variant
                        diesel = price
                elif "unleaded" in name and "plus" not in name and "premium" not in name:
                    gas = price

            if diesel is not None or gas is not None:
                result = {"diesel": diesel, "gas": gas}
                self._cache.set(cache_key, result, ttl=self._ttl)
                return result

        return None

    async def enrich_prices(
        self, stations: list[Station], location: GeoLocation
    ) -> list[Station]:
        # Collect stations that need TA/Petro prices
        to_fetch: list[tuple[Station, str]] = []
        for station in stations:
            brand = (station.brand or station.name or "").lower()
            if not any(kw in brand for kw in ("ta ", "travel centers", "travelcenters", "petro")):
                if not any(kw in (station.name or "").lower() for kw in ("ta ", "petro", "travelcenter")):
                    continue
            if station.gas_price is not None or station.diesel_price is not None:
                continue

            url = self._build_url(station)
            if url:
                to_fetch.append((station, url))

        if not to_fetch:
            return stations

        # Fetch all TA/Petro stations in parallel with a shared session
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_store_prices(url, session) for _, url in to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for (station, _), result in zip(to_fetch, results):
            if isinstance(result, dict) and result:
                station.diesel_price = result.get("diesel")
                station.gas_price = result.get("gas")
                station.price_source = "posted"

        return stations

    @staticmethod
    def _build_url(station: Station) -> Optional[str]:
        """Try to build a ta-petro.com location URL from station data."""
        name = (station.name or "").strip()
        addr = (station.address or "").lower()

        # Extract state abbreviation from address
        state_match = re.search(r'\b([A-Z]{2})\b', station.address or "")
        if not state_match:
            return None
        state = state_match.group(1).lower()

        # Build slug from name: "TA Porter" → "ta-porter", "Petro Santa Nella" → "petro-santa-nella"
        slug = re.sub(r'[^a-z0-9\s-]', '', name.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        # Remove trailing numbers like store IDs
        slug = re.sub(r'-?\d{3,}$', '', slug).rstrip('-')

        if not slug or not state:
            return None

        return f"https://www.ta-petro.com/location/{state}/{slug}/"
