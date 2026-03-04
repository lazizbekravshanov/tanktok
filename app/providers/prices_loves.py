"""Love's Travel Stops fuel prices — scraped from individual store pages."""

from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp

from app.config import Config
from app.providers.base import StationPriceProvider, Station, GeoLocation
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

# Love's store pages have fuel prices in the HTML
STORE_URL = "https://www.loves.com/locations/{store_id}"

# Regex patterns for prices in Love's HTML
DIESEL_RE = re.compile(r'(?:Diesel|Auto Diesel)[^$]*\$(\d+\.\d{2,3})', re.IGNORECASE)
UNLEADED_RE = re.compile(r'Unleaded[^$]*\$(\d+\.\d{2,3})', re.IGNORECASE)


class LovesPriceProvider(StationPriceProvider):
    """Fetches Love's fuel prices from their website."""

    def __init__(self, config: Config, cache: Cache) -> None:
        self._cache = cache
        self._ttl = config.cache_retail_ttl

    async def _fetch_store_prices(self, store_id: str) -> Optional[dict]:
        """Fetch prices for a single Love's store."""
        cache_key = f"loves:store:{store_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = STORE_URL.format(store_id=store_id)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "TankTok/1.0"},
                ) as resp:
                    if resp.status == 404:
                        return None
                    resp.raise_for_status()
                    html = await resp.text()
        except Exception:
            logger.debug("Love's store %s fetch failed", store_id)
            return None

        diesel = None
        gas = None

        m = DIESEL_RE.search(html)
        if m:
            try:
                diesel = float(m.group(1))
            except ValueError:
                pass

        m = UNLEADED_RE.search(html)
        if m:
            try:
                gas = float(m.group(1))
            except ValueError:
                pass

        if diesel is None and gas is None:
            return None

        result = {"diesel": diesel, "gas": gas}
        self._cache.set(cache_key, result, ttl=self._ttl)
        return result

    async def enrich_prices(
        self, stations: list[Station], location: GeoLocation
    ) -> list[Station]:
        for station in stations:
            brand = (station.brand or station.name or "").lower()
            if "love" not in brand:
                continue
            if station.gas_price is not None or station.diesel_price is not None:
                continue

            # Extract store ID from name (Love's often has store # in OSM data)
            store_id = self._extract_store_id(station)
            if not store_id:
                continue

            prices = await self._fetch_store_prices(store_id)
            if prices:
                station.diesel_price = prices.get("diesel")
                station.gas_price = prices.get("gas")
                station.price_source = "posted"

        return stations

    @staticmethod
    def _extract_store_id(station: Station) -> Optional[str]:
        """Try to extract Love's store number from station name or data."""
        name = station.name or ""
        # Patterns: "Love's #368", "Love's Travel Stop #368", "Love's 368"
        m = re.search(r"#?(\d{2,4})", name)
        if m:
            return m.group(1)
        return None
