"""Provider interfaces for TankTok's plugin architecture."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# --------------- Data models ---------------

@dataclass
class GeoLocation:
    lat: float
    lon: float
    display_name: str
    state: str = ""
    zip_code: str = ""


@dataclass
class Station:
    name: str
    lat: float
    lon: float
    address: str = ""
    brand: str = ""
    distance_mi: float = 0.0
    gas_price: Optional[float] = None
    diesel_price: Optional[float] = None
    price_source: str = "unavailable"  # "posted" | "estimate" | "unavailable"


@dataclass
class RetailPrices:
    region: str
    regular_gas: Optional[float] = None
    diesel: Optional[float] = None
    regular_gas_prev: Optional[float] = None
    diesel_prev: Optional[float] = None
    source: str = ""
    timestamp: Optional[datetime] = None
    period: str = "weekly"  # "daily" or "weekly"


@dataclass
class MarketQuote:
    symbol: str
    name: str
    price: float
    change_pct: float
    timestamp: Optional[datetime] = None


@dataclass
class ForecastResult:
    fuel_type: str  # "regular_gas" or "diesel"
    low: float
    high: float
    confidence: str = ""
    model_timestamp: Optional[datetime] = None


@dataclass
class PredictionContract:
    market: str
    title: str
    yes_price: float
    no_price: float
    url: str = ""
    ticker: str = ""
    volume: float = 0.0
    open_interest: float = 0.0
    last_price: Optional[float] = None
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    expiration: Optional[datetime] = None
    category: str = ""  # "gas", "oil_daily", "oil_weekly"
    strike: Optional[str] = None  # e.g. "B3.25" for "above $3.25"
    freshness: str = ""  # "live", "recent", "cached"


@dataclass
class QueryResult:
    location: Optional[GeoLocation] = None
    retail_prices: Optional[RetailPrices] = None
    stations: list[Station] = field(default_factory=list)
    market_quotes: list[MarketQuote] = field(default_factory=list)
    forecasts: list[ForecastResult] = field(default_factory=list)
    prediction_contracts: list[PredictionContract] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --------------- Abstract providers ---------------

class GeoProvider(abc.ABC):
    @abc.abstractmethod
    async def geocode(self, query: str) -> Optional[GeoLocation]:
        ...


class POIProvider(abc.ABC):
    @abc.abstractmethod
    async def nearby_stations(
        self, lat: float, lon: float, radius_m: int = 25000
    ) -> list[Station]:
        ...


class RetailPriceProvider(abc.ABC):
    @abc.abstractmethod
    async def get_prices(self, location: GeoLocation) -> Optional[RetailPrices]:
        ...


class StationPriceProvider(abc.ABC):
    """Optional plugin: enriches Station objects with posted prices."""

    @abc.abstractmethod
    async def enrich_prices(
        self, stations: list[Station], location: GeoLocation
    ) -> list[Station]:
        ...


class MarketProvider(abc.ABC):
    @abc.abstractmethod
    async def get_quotes(self) -> list[MarketQuote]:
        ...


class PredictionProvider(abc.ABC):
    @abc.abstractmethod
    async def get_fuel_contracts(self) -> list[PredictionContract]:
        ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        ...
