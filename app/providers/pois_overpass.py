"""Overpass API provider — truck stops & major fuel networks only."""

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

# Major truck stop / travel center brands (lowercase for matching)
TRUCK_STOP_BRANDS: set[str] = {
    # Big 3
    "pilot", "pilot travel centers", "pilot flying j",
    "flying j", "flying j travel plaza",
    "love's", "love's travel stop", "love's travel stops",
    "loves", "loves travel stops",
    # TA / Petro
    "ta", "travelcenters of america", "travel centers of america",
    "ta travel center", "ta express",
    "petro", "petro stopping center", "petro stopping centers",
    # Ambest
    "ambest", "am best",
    # Road Ranger
    "road ranger",
    # Buc-ee's
    "buc-ee's", "buc-ees", "bucees", "buc-ee's",
    # Sapp Bros
    "sapp bros", "sapp bros.", "sapp brothers",
    # Kenly 95
    "kenly 95",
    # Little America
    "little america",
    # QuikTrip (large travel centers)
    "quiktrip", "qt",
    # Casey's
    "casey's", "casey's general store", "caseys",
    # Sheetz
    "sheetz",
    # Wawa
    "wawa",
    # Kwik Trip / Kwik Star
    "kwik trip", "kwik star",
    # RaceTrac / RaceWay
    "racetrac", "raceway",
    # Maverik
    "maverik",
    # Speedway
    "speedway",
    # Circle K (larger locations)
    "circle k",
    # Cenex
    "cenex",
    # Wally's
    "wally's", "wallys",
    # JHEP
    "iowa 80", "iowa 80 truckstop",
    # Others
    "truck stops of america", "big cat travel center",
}

# Keywords that indicate a truck stop in name/description
TRUCK_STOP_KEYWORDS: list[str] = [
    "truck stop", "truckstop", "travel center", "travel plaza",
    "travel stop", "truck plaza", "truck wash", "trucker",
    "travel centre", "rest stop", "truck haven",
]

# Overpass query: fuel stations that are either tagged as HGV-friendly
# OR are highway services, plus a general fuel query (we filter client-side by brand)
QUERY_TEMPLATE = """
[out:json][timeout:30];
(
  node["amenity"="fuel"]["hgv"="yes"](around:{radius},{lat},{lon});
  way["amenity"="fuel"]["hgv"="yes"](around:{radius},{lat},{lon});
  node["amenity"="fuel"]["fuel:HGV_diesel"="yes"](around:{radius},{lat},{lon});
  way["amenity"="fuel"]["fuel:HGV_diesel"="yes"](around:{radius},{lat},{lon});
  node["amenity"="fuel"]["fuel:diesel"="yes"](around:{radius},{lat},{lon});
  way["amenity"="fuel"]["fuel:diesel"="yes"](around:{radius},{lat},{lon});
  node["highway"="services"](around:{radius},{lat},{lon});
  way["highway"="services"](around:{radius},{lat},{lon});
  node["amenity"="fuel"](around:{radius},{lat},{lon});
  way["amenity"="fuel"](around:{radius},{lat},{lon});
);
out center body;
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


def _is_truck_stop(tags: dict[str, str]) -> bool:
    """Check if an OSM element is a truck stop / major fuel network."""
    # HGV-tagged stations
    if tags.get("hgv") == "yes":
        return True
    if tags.get("fuel:HGV_diesel") == "yes":
        return True

    # Highway services
    if tags.get("highway") == "services":
        return True

    # Check brand/operator/name against known truck stop chains
    for field in ("brand", "operator", "name", "brand:wikidata"):
        val = tags.get(field, "").lower().strip()
        if val and val in TRUCK_STOP_BRANDS:
            return True

    # Keyword match in name
    name = tags.get("name", "").lower()
    for kw in TRUCK_STOP_KEYWORDS:
        if kw in name:
            return True

    return False


class OverpassPOIProvider(POIProvider):
    def __init__(self, config: Config, cache: Cache) -> None:
        self._radius = config.poi_radius
        self._cache = cache
        self._ttl = config.cache_poi_ttl

    async def nearby_stations(
        self, lat: float, lon: float, radius_m: int | None = None
    ) -> list[Station]:
        radius_m = radius_m or self._radius

        cache_key = f"truckstop:{round(lat, 3)}:{round(lon, 3)}:{radius_m}"
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
                        if resp.status in (429, 504):
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

        # Deduplicate by OSM id
        seen_ids: set[int] = set()
        stations: list[Station] = []

        for el in data.get("elements", []):
            osm_id = el.get("id", 0)
            if osm_id in seen_ids:
                continue
            seen_ids.add(osm_id)

            tags = el.get("tags", {})

            # --- FILTER: truck stops & major networks only ---
            if not _is_truck_stop(tags):
                continue

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
                or "Truck Stop"
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
        result = stations[:50]
        self._cache.set(cache_key, result, ttl=self._ttl)
        return result
