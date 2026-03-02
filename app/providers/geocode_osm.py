"""Nominatim (OpenStreetMap) geocoding provider."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from app.config import Config
from app.providers.base import GeoLocation, GeoProvider
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

_last_request_time: float = 0.0
_lock = asyncio.Lock()


class NominatimGeoProvider(GeoProvider):
    BASE_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, config: Config, cache: Cache) -> None:
        self._ua = config.nominatim_user_agent
        self._rate_limit = config.nominatim_rate_limit
        self._cache = cache
        self._ttl = config.cache_geocode_ttl

    async def _throttle(self) -> None:
        global _last_request_time
        async with _lock:
            now = asyncio.get_event_loop().time()
            wait = self._rate_limit - (now - _last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request_time = asyncio.get_event_loop().time()

    async def geocode(self, query: str) -> Optional[GeoLocation]:
        cache_key = f"geo:{query.strip().lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        await self._throttle()

        params = {
            "q": query + ", United States",
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": "1",
            "countrycodes": "us",
        }
        headers = {"User-Agent": self._ua}

        retries = 3
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.BASE_URL, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 429:
                            delay = 2 ** attempt
                            logger.warning("Nominatim rate-limited, backing off %ds", delay)
                            await asyncio.sleep(delay)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
            except Exception:
                logger.exception("Nominatim request failed (attempt %d)", attempt + 1)
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

            if not data:
                return None

            hit = data[0]
            addr = hit.get("address", {})
            loc = GeoLocation(
                lat=float(hit["lat"]),
                lon=float(hit["lon"]),
                display_name=hit.get("display_name", query),
                state=addr.get("state", ""),
                zip_code=addr.get("postcode", ""),
            )
            self._cache.set(cache_key, loc, ttl=self._ttl)
            return loc

        return None
