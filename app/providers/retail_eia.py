"""EIA (Energy Information Administration) retail price provider."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from app.config import Config
from app.providers.base import GeoLocation, RetailPriceProvider, RetailPrices
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

# PADD regions mapped from US states.
# EIA reports retail prices by PADD region (Petroleum Administration for Defense Districts).
STATE_TO_PADD: dict[str, str] = {}

# PADD 1 — East Coast
for _st in [
    "CT", "ME", "MA", "NH", "RI", "VT",  # 1A — New England
    "DE", "DC", "MD", "NJ", "NY", "PA",  # 1B — Central Atlantic
    "FL", "GA", "NC", "SC", "VA", "WV",  # 1C — Lower Atlantic
]:
    STATE_TO_PADD[_st] = "PADD 1"

# PADD 2 — Midwest
for _st in [
    "IL", "IN", "IA", "KS", "KY", "MI", "MN", "MO", "NE",
    "ND", "SD", "OH", "OK", "TN", "WI",
]:
    STATE_TO_PADD[_st] = "PADD 2"

# PADD 3 — Gulf Coast
for _st in ["AL", "AR", "LA", "MS", "NM", "TX"]:
    STATE_TO_PADD[_st] = "PADD 3"

# PADD 4 — Rocky Mountain
for _st in ["CO", "ID", "MT", "UT", "WY"]:
    STATE_TO_PADD[_st] = "PADD 4"

# PADD 5 — West Coast
for _st in ["AK", "AZ", "CA", "HI", "NV", "OR", "WA"]:
    STATE_TO_PADD[_st] = "PADD 5"


# EIA APIv2 series IDs for weekly retail prices ($/gal)
# Regular gasoline
GASOLINE_SERIES: dict[str, str] = {
    "US": "EMM_EPMR_PTE_NUS_DPG",
    "PADD 1": "EMM_EPMR_PTE_R10_DPG",
    "PADD 2": "EMM_EPMR_PTE_R20_DPG",
    "PADD 3": "EMM_EPMR_PTE_R30_DPG",
    "PADD 4": "EMM_EPMR_PTE_R40_DPG",
    "PADD 5": "EMM_EPMR_PTE_R50_DPG",
}

# On-highway diesel
DIESEL_SERIES: dict[str, str] = {
    "US": "EMD_EPD2D_PTE_NUS_DPG",
    "PADD 1": "EMD_EPD2D_PTE_R10_DPG",
    "PADD 2": "EMD_EPD2D_PTE_R20_DPG",
    "PADD 3": "EMD_EPD2D_PTE_R30_DPG",
    "PADD 4": "EMD_EPD2D_PTE_R40_DPG",
    "PADD 5": "EMD_EPD2D_PTE_R50_DPG",
}


def _state_abbrev(state_name: str) -> str:
    """Try to convert a full state name to its abbreviation."""
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
    key = state_name.strip().lower()
    if key in mapping:
        return mapping[key]
    # Already an abbreviation?
    upper = state_name.strip().upper()
    if upper in STATE_TO_PADD:
        return upper
    return ""


class EIARetailProvider(RetailPriceProvider):
    BASE_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"

    def __init__(self, config: Config, cache: Cache) -> None:
        self._api_key = config.eia_api_key
        self._cache = cache
        self._ttl = config.cache_retail_ttl

    def _resolve_padd(self, location: GeoLocation) -> str:
        abbr = _state_abbrev(location.state)
        return STATE_TO_PADD.get(abbr, "US")

    async def _fetch_series(
        self, series_id: str, product: str
    ) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
        """Fetch latest two data points for a series. Returns (latest, previous, date)."""
        if not self._api_key:
            logger.warning("EIA_API_KEY not set — skipping EIA retail prices")
            return None, None, None

        params = {
            "api_key": self._api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": series_id,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": "2",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    body = await resp.json()
        except Exception:
            logger.exception("EIA request failed for %s", series_id)
            return None, None, None

        rows = body.get("response", {}).get("data", [])
        if not rows:
            return None, None, None

        latest_val = rows[0].get("value")
        prev_val = rows[1].get("value") if len(rows) > 1 else None
        period_str = rows[0].get("period", "")

        ts = None
        if period_str:
            try:
                ts = datetime.strptime(period_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return _to_float(latest_val), _to_float(prev_val), ts

    async def get_prices(self, location: GeoLocation) -> Optional[RetailPrices]:
        padd = self._resolve_padd(location)

        cache_key = f"eia:{padd}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        gas_id = GASOLINE_SERIES.get(padd, GASOLINE_SERIES["US"])
        diesel_id = DIESEL_SERIES.get(padd, DIESEL_SERIES["US"])

        gas_latest, gas_prev, gas_ts = await self._fetch_series(gas_id, "gasoline")
        diesel_latest, diesel_prev, diesel_ts = await self._fetch_series(diesel_id, "diesel")

        if gas_latest is None and diesel_latest is None:
            return None

        ts = gas_ts or diesel_ts

        result = RetailPrices(
            region=padd,
            regular_gas=gas_latest,
            diesel=diesel_latest,
            regular_gas_prev=gas_prev,
            diesel_prev=diesel_prev,
            source="U.S. EIA",
            timestamp=ts,
            period="weekly",
        )
        self._cache.set(cache_key, result, ttl=self._ttl)
        return result
