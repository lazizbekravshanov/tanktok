"""Local truck stop database provider — fast radius lookup with address resolution."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import TYPE_CHECKING, Optional

from app.config import Config
from app.providers.base import POIProvider, Station

if TYPE_CHECKING:
    from app.providers.geocode_google import GoogleGeocoder
    from app.providers.geocode_osm import NominatimGeoProvider

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "truckstops.json")


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class TruckStopDB(POIProvider):
    """
    Pre-loaded database of ~3,700+ truck stops across the US.
    Uses bounding-box pre-filter + haversine for radius search.
    """

    def __init__(
        self,
        config: Config,
        google_geocoder: Optional[GoogleGeocoder] = None,
        nominatim_geocoder: Optional[NominatimGeoProvider] = None,
        db_path: str = "",
    ) -> None:
        self._radius_mi = config.poi_radius_mi
        self._google = google_geocoder
        self._nominatim = nominatim_geocoder
        path = db_path or DATA_PATH
        self._stops: list[dict] = []
        self._load(path)

    def _load(self, path: str) -> None:
        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            logger.error("Truck stop database not found at %s", resolved)
            logger.error("Run: python3 scripts/build_truckstop_db.py")
            return
        with open(resolved) as f:
            self._stops = json.load(f)
        logger.info("Loaded %d truck stops from database", len(self._stops))

    async def nearby_stations(
        self, lat: float, lon: float, radius_m: int | None = None
    ) -> list[Station]:
        radius_mi = self._radius_mi
        if radius_m is not None:
            radius_mi = radius_m / 1609.34

        # Rough bounding box filter (1 degree lat ~ 69 mi)
        deg_margin = radius_mi / 69.0 * 1.15
        lat_min = lat - deg_margin
        lat_max = lat + deg_margin
        lon_min = lon - deg_margin / max(math.cos(math.radians(lat)), 0.01)
        lon_max = lon + deg_margin / max(math.cos(math.radians(lat)), 0.01)

        results: list[Station] = []
        for s in self._stops:
            slat = s["lat"]
            slon = s["lon"]

            if slat < lat_min or slat > lat_max or slon < lon_min or slon > lon_max:
                continue

            dist = _haversine_mi(lat, lon, slat, slon)
            if dist > radius_mi:
                continue

            results.append(
                Station(
                    name=s.get("name", "Truck Stop"),
                    lat=slat,
                    lon=slon,
                    address=s.get("address", ""),
                    brand=s.get("brand", ""),
                    distance_mi=round(dist, 1),
                )
            )

        results.sort(key=lambda st: st.distance_mi)
        top = results[:10]

        # Fast address resolution — 3 second timeout, don't block the response
        try:
            await asyncio.wait_for(self._fill_addresses(top), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("Address resolution timed out — using fallbacks")
            for s in top:
                if not s.address:
                    s.address = f"Near {s.lat:.4f}, {s.lon:.4f}"

        return top

    async def _fill_addresses(self, stations: list[Station]) -> None:
        """Reverse-geocode any station missing a street address."""
        needs_addr = [s for s in stations if not s.address]
        if not needs_addr:
            return

        # Google Maps — fast, parallel
        if self._google and self._google.is_configured:
            async with _shared_google_session() as session:
                tasks = [self._google.reverse(s.lat, s.lon) for s in needs_addr]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                still_missing = []
                for station, result in zip(needs_addr, results):
                    if isinstance(result, str) and result:
                        station.address = result
                    else:
                        still_missing.append(station)
                needs_addr = still_missing

        # For any still missing, just show coordinates (don't wait for slow Nominatim)
        for station in needs_addr:
            if not station.address:
                station.address = f"Near {station.lat:.4f}, {station.lon:.4f}"


class _shared_google_session:
    """Context manager placeholder — Google geocoder manages its own sessions."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
