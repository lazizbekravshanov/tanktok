"""Overpass API provider for nearby fuel stations / truck stops."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional

import aiohttp

from app.config import Config
from app.providers.base import POIProvider, Station
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY_TEMPLATE = """
[out:json][timeout:25];
(
  node["amenity"="fuel"](around:{radius},{lat},{lon});
  way["amenity"="fuel"](around:{radius},{lat},{lon});
  node["highway"="services"](around:{radius},{lat},{lon});
  way["highway"="services"](around:{radius},{lat},{lon});
);
out center body 50;
"""


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class OverpassPOIProvider(POIProvider):
    def __init__(self, config: Config, cache: Cache) -> None:
        self._radius = config.poi_radius
        self._cache = cache
        self._ttl = config.cache_poi_ttl

    async def nearby_stations(
        self, lat: float, lon: float, radius_m: int | None = None
    ) -> list[Station]:
        radius_m = radius_m or self._radius

        cache_key = f"poi:{round(lat, 3)}:{round(lon, 3)}:{radius_m}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        query = QUERY_TEMPLATE.format(radius=radius_m, lat=lat, lon=lon)

        retries = 3
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        OVERPASS_URL,
                        data={"data": query},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status == 429 or resp.status == 504:
                            delay = 2 ** attempt
                            logger.warning("Overpass %d, backing off %ds", resp.status, delay)
                            await asyncio.sleep(delay)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        break
            except Exception:
                logger.exception("Overpass request failed (attempt %d)", attempt + 1)
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return []
        else:
            return []

        stations: list[Station] = []
        for el in data.get("elements", []):
            tags = el.get("tags", {})

            # Get coordinates — nodes have lat/lon directly; ways use "center"
            el_lat: Optional[float] = el.get("lat")
            el_lon: Optional[float] = el.get("lon")
            if el_lat is None:
                center = el.get("center", {})
                el_lat = center.get("lat")
                el_lon = center.get("lon")
            if el_lat is None:
                continue

            name = (
                tags.get("name")
                or tags.get("brand")
                or tags.get("operator")
                or "Unknown Station"
            )

            addr_parts = [
                tags.get("addr:housenumber", ""),
                tags.get("addr:street", ""),
            ]
            addr = " ".join(p for p in addr_parts if p).strip()
            city = tags.get("addr:city", "")
            state = tags.get("addr:state", "")
            if city or state:
                addr += f", {city} {state}".strip(", ")

            dist = _haversine_mi(lat, lon, el_lat, el_lon)

            stations.append(
                Station(
                    name=name,
                    lat=el_lat,
                    lon=el_lon,
                    address=addr or "Address unavailable",
                    brand=tags.get("brand", ""),
                    distance_mi=round(dist, 1),
                )
            )

        stations.sort(key=lambda s: s.distance_mi)
        result = stations[:50]  # keep more for potential enrichment; handlers trim to 10
        self._cache.set(cache_key, result, ttl=self._ttl)
        return result
