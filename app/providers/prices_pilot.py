"""Pilot Flying J fuel prices — bulk endpoint, all 876 locations in one call."""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from app.config import Config
from app.providers.base import StationPriceProvider, Station, GeoLocation
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

URL = "https://pilotcompany.com/fuel-prices/__data.json"


def _deref(arr: list, idx: int) -> Any:
    """Dereference a SvelteKit devalue index."""
    if idx >= len(arr):
        return None
    val = arr[idx]
    if isinstance(val, dict):
        return {k: _deref(arr, v) for k, v in val.items()}
    if isinstance(val, list):
        return [_deref(arr, i) for i in val]
    return val


def _parse_sveltekit(data: dict) -> list[dict]:
    """Parse SvelteKit devalue format into location dicts."""
    nodes = data.get("nodes", [])
    if len(nodes) < 2:
        return []
    arr = nodes[1].get("data", [])
    if not arr:
        return []

    root = _deref(arr, 0)
    if not root or "fuelPrices" not in root:
        return []

    return root["fuelPrices"]


class PilotPriceProvider(StationPriceProvider):
    """Fetches all Pilot/Flying J/One9 fuel prices in a single request."""

    def __init__(self, config: Config, cache: Cache) -> None:
        self._cache = cache
        self._ttl = config.cache_retail_ttl  # 6 hours
        self._prices: dict[str, dict] = {}  # city_state → {diesel, gas}

    async def fetch_all(self) -> dict[str, dict]:
        """Fetch and cache all Pilot prices. Returns {location_id: {diesel, gas, city, state}}."""
        cache_key = "pilot:all_prices"
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._prices = cached
            return cached

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    URL, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
        except Exception:
            logger.exception("Pilot fuel prices fetch failed")
            return {}

        locations = _parse_sveltekit(data)
        prices: dict[str, dict] = {}

        for loc in locations:
            if not isinstance(loc, dict):
                continue
            loc_id = str(loc.get("locationId", ""))
            city = loc.get("city", "")
            state = loc.get("state", "")
            fuel_list = loc.get("fuelPrices", [])
            if not isinstance(fuel_list, list):
                continue

            diesel = None
            gas = None
            for fp in fuel_list:
                if not isinstance(fp, dict):
                    continue
                desc = (fp.get("description") or "").lower()
                price = fp.get("price")
                if price is None:
                    continue
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                if "diesel #2" in desc or desc == "diesel":
                    diesel = price
                elif "unleaded" == desc or desc == "regular":
                    gas = price

            if diesel or gas:
                # Key by location_id AND by city+state for fuzzy matching
                entry = {"diesel": diesel, "gas": gas, "city": city, "state": state, "id": loc_id}
                prices[loc_id] = entry
                # Also key by normalized city_state for geo matching
                key = f"{city}:{state}".lower().strip()
                if key not in prices:
                    prices[key] = entry

        logger.info("Pilot: loaded prices for %d locations", len([p for p in prices.values() if p.get("id")]))
        self._prices = prices
        self._cache.set(cache_key, prices, ttl=self._ttl)
        return prices

    async def enrich_prices(
        self, stations: list[Station], location: GeoLocation
    ) -> list[Station]:
        if not self._prices:
            await self.fetch_all()

        for station in stations:
            brand = (station.brand or station.name or "").lower()
            if not any(kw in brand for kw in ("pilot", "flying j", "one9")):
                continue
            if station.gas_price is not None or station.diesel_price is not None:
                continue

            # Try matching by proximity — find closest Pilot in price data
            best = self._find_closest(station)
            if best:
                station.diesel_price = best.get("diesel")
                station.gas_price = best.get("gas")
                station.price_source = "posted"

        return stations

    def _find_closest(self, station: Station) -> Optional[dict]:
        """Find the closest Pilot location by name/city match."""
        import math

        name = (station.name or "").lower()
        best_dist = 5.0  # max 5 miles match threshold
        best = None

        for entry in self._prices.values():
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            city = (entry.get("city") or "").lower()
            state = (entry.get("state") or "").lower()
            # Simple city match from station address
            addr = (station.address or "").lower()
            if city and city in addr:
                return entry

        return best

    def get_price_by_id(self, location_id: str) -> Optional[dict]:
        return self._prices.get(location_id)
