"""Tests for provider fallback logic and data models."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Config
from app.forecasting.model import generate_forecasts
from app.providers.base import (
    GeoLocation,
    MarketQuote,
    QueryResult,
    RetailPrices,
    Station,
)
from app.providers.prediction_base import DisabledPredictionProvider
from app.providers.retail_eia import _state_abbrev, STATE_TO_PADD


class TestStateMapping:
    def test_full_name(self):
        assert _state_abbrev("Kentucky") == "KY"

    def test_abbreviation(self):
        assert _state_abbrev("OH") == "OH"

    def test_lowercase(self):
        assert _state_abbrev("ohio") == "OH"

    def test_unknown(self):
        assert _state_abbrev("Atlantis") == ""

    def test_all_states_mapped(self):
        """Every abbreviation in STATE_TO_PADD should resolve to a PADD."""
        for abbr in STATE_TO_PADD:
            assert STATE_TO_PADD[abbr].startswith("PADD")


class TestForecast:
    def test_no_retail_returns_empty(self):
        assert generate_forecasts(None, []) == []

    def test_basic_forecast(self):
        rp = RetailPrices(
            region="PADD 2",
            regular_gas=3.50,
            diesel=4.00,
            regular_gas_prev=3.45,
            diesel_prev=3.95,
            source="EIA",
            timestamp=datetime.now(timezone.utc),
        )
        quotes = [
            MarketQuote(symbol="RB=F", name="RBOB", price=2.50, change_pct=1.5),
            MarketQuote(symbol="HO=F", name="HO", price=2.80, change_pct=-0.5),
        ]
        result = generate_forecasts(rp, quotes)
        assert len(result) == 2
        assert result[0].fuel_type == "Regular Gasoline"
        assert result[0].low < result[0].high
        assert result[1].fuel_type == "Diesel"

    def test_no_previous_price(self):
        rp = RetailPrices(
            region="US",
            regular_gas=3.50,
            diesel=None,
            source="EIA",
        )
        result = generate_forecasts(rp, [])
        assert len(result) == 1
        assert "insufficient" in result[0].confidence.lower() or "Low" in result[0].confidence


class TestDisabledPrediction:
    def test_not_configured(self):
        p = DisabledPredictionProvider()
        assert not p.is_configured()

    def test_returns_empty(self):
        p = DisabledPredictionProvider()
        result = asyncio.get_event_loop().run_until_complete(p.get_fuel_contracts())
        assert result == []


class TestQueryResultFallback:
    def test_partial_failure(self):
        """QueryResult should still be usable with partial data."""
        qr = QueryResult(
            location=GeoLocation(lat=39.1, lon=-84.5, display_name="Cincinnati"),
            retail_prices=None,  # EIA failed
            stations=[
                Station(name="Shell", lat=39.1, lon=-84.5, distance_mi=0.5),
            ],
            market_quotes=[
                MarketQuote(symbol="CL=F", name="WTI", price=72.0, change_pct=-1.2),
            ],
            errors=["Retail prices temporarily unavailable."],
        )
        assert qr.location is not None
        assert len(qr.stations) == 1
        assert len(qr.market_quotes) == 1
        assert qr.retail_prices is None
        assert len(qr.errors) == 1
