"""Google Maps reverse geocoding for truck stop addresses."""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from app.config import Config
from app.storage.cache import Cache

logger = logging.getLogger(__name__)


class GoogleGeocoder:
    """Reverse-geocode lat/lon to street addresses via Google Geocoding API."""

    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, config: Config, cache: Cache) -> None:
        self._api_key = config.google_maps_api_key
        self._cache = cache
        self._ttl = config.cache_geocode_ttl  # 30 days

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def reverse(self, lat: float, lon: float) -> Optional[str]:
        """Reverse-geocode lat/lon → formatted street address."""
        cache_key = f"grev:{round(lat, 5)}:{round(lon, 5)}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not self._api_key:
            return None

        params = {
            "latlng": f"{lat},{lon}",
            "key": self._api_key,
            "result_type": "street_address|premise|point_of_interest",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception:
            logger.exception("Google geocode request failed")
            return None

        results = data.get("results", [])
        if not results:
            # Retry without result_type filter
            params.pop("result_type", None)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.BASE_URL,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        results = data.get("results", [])
            except Exception:
                return None

        if not results:
            return None

        # Use the first result's formatted address
        address = results[0].get("formatted_address", "")

        # Clean up — remove "USA" / "United States" suffix for brevity
        for suffix in (", USA", ", United States"):
            if address.endswith(suffix):
                address = address[: -len(suffix)]

        if address:
            self._cache.set(cache_key, address, ttl=self._ttl)

        return address or None
