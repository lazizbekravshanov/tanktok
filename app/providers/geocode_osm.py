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

    async def reverse(self, lat: float, lon: float) -> Optional[str]:
        """Reverse-geocode lat/lon into a street address string."""
        cache_key = f"rev:{round(lat, 5)}:{round(lon, 5)}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        await self._throttle()

        params = {
            "lat": str(lat),
            "lon": str(lon),
            "format": "jsonv2",
            "addressdetails": "1",
            "zoom": "18",
        }
        headers = {"User-Agent": self._ua}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 429:
                        return None
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception:
            logger.exception("Nominatim reverse geocode failed")
            return None

        addr = data.get("address", {})
        parts = []
        # Street address
        house = addr.get("house_number", "")
        road = addr.get("road", "")
        if house and road:
            parts.append(f"{house} {road}")
        elif road:
            parts.append(road)

        # City
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        state = addr.get("state", "")
        # Abbreviate state if possible
        state_abbr = self._state_abbrev(state) or state
        if city and state_abbr:
            parts.append(f"{city}, {state_abbr}")
        elif city:
            parts.append(city)

        # ZIP
        postcode = addr.get("postcode", "")
        if postcode:
            parts.append(postcode)

        result = ", ".join(parts) if parts else None
        if result:
            self._cache.set(cache_key, result, ttl=self._ttl)
        return result

    @staticmethod
    def _state_abbrev(state_name: str) -> str:
        mapping = {
            "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
            "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
            "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
            "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
            "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
            "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
            "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
            "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
            "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
            "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
            "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
            "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
            "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
        }
        return mapping.get(state_name.strip().lower(), "")

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
