"""Tests for Kalshi prediction market integration."""

import asyncio
import json
from base64 import b64decode
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.prediction_kalshi import (
    KalshiPredictionProvider,
    KalshiRestClient,
    KalshiWebSocket,
    SERIES_CATEGORY,
    SERIES_DISPLAY,
    _parse_strike,
    _sign_request,
    _to_dollar,
    _to_float,
)


# --------------- Strike parsing ---------------

class TestParseStrike:
    def test_basic_strike(self):
        assert _parse_strike("KXAAAGASM-26MAR31-B3.25") == "$3.25"

    def test_integer_strike(self):
        assert _parse_strike("KXWTI-26MAR02-B68") == "$68"

    def test_high_precision(self):
        assert _parse_strike("KXAAAGASM-26APR30-B3.599") == "$3.599"

    def test_no_strike(self):
        assert _parse_strike("KXAAAGASM-26MAR31") is None

    def test_non_b_prefix(self):
        assert _parse_strike("KXWTI-26MAR02-A68") is None

    def test_empty(self):
        assert _parse_strike("") is None

    def test_invalid_number(self):
        assert _parse_strike("KXWTI-26MAR02-Babc") is None


# --------------- Dollar/float helpers ---------------

class TestValueParsing:
    def test_to_dollar_string(self):
        assert _to_dollar("0.45") == 0.45

    def test_to_dollar_int(self):
        assert _to_dollar(45) == 45.0

    def test_to_dollar_none(self):
        assert _to_dollar(None) is None

    def test_to_dollar_garbage(self):
        assert _to_dollar("abc") is None

    def test_to_float_valid(self):
        assert _to_float("1234.56") == 1234.56

    def test_to_float_none(self):
        assert _to_float(None) == 0.0

    def test_to_float_garbage(self):
        assert _to_float("abc") == 0.0


# --------------- RSA Signing ---------------

class TestRSASigning:
    @pytest.fixture
    def rsa_key(self):
        """Generate a throwaway RSA key for testing."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        return rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

    def test_sign_produces_base64(self, rsa_key):
        sig = _sign_request(rsa_key, "1709312345678", "GET", "/trade-api/v2/markets")
        # Should be valid base64
        decoded = b64decode(sig)
        assert len(decoded) > 0

    def test_sign_deterministic_key(self, rsa_key):
        """Same inputs with same key produce valid (but non-identical due to PSS) sigs."""
        sig1 = _sign_request(rsa_key, "123", "GET", "/path")
        sig2 = _sign_request(rsa_key, "123", "GET", "/path")
        # PSS is probabilistic — sigs will differ but both must be valid
        assert isinstance(sig1, str) and len(sig1) > 0
        assert isinstance(sig2, str) and len(sig2) > 0

    def test_sign_verifies(self, rsa_key):
        """Signature should verify against the public key."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = "1709312345678"
        method = "GET"
        path = "/trade-api/v2/markets"
        sig_b64 = _sign_request(rsa_key, ts, method, path)
        sig_bytes = b64decode(sig_b64)

        message = (ts + method + path).encode("utf-8")
        pub = rsa_key.public_key()
        # Should not raise
        pub.verify(
            sig_bytes,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )


# --------------- WebSocket message handling ---------------

class TestWebSocketMessageHandling:
    def test_ticker_message(self):
        ws = KalshiWebSocket.__new__(KalshiWebSocket)
        ws._prices = {}

        msg = {
            "type": "ticker",
            "msg": {
                "market_ticker": "KXAAAGASM-26MAR31-B3.25",
                "yes_bid_dollars": "0.45",
                "yes_ask_dollars": "0.48",
                "no_bid_dollars": "0.52",
                "no_ask_dollars": "0.55",
                "last_price_dollars": "0.46",
                "volume_fp": "1234.00",
                "open_interest_fp": "890.00",
            },
        }
        ws._handle_message(msg)

        price = ws.get_price("KXAAAGASM-26MAR31-B3.25")
        assert price is not None
        assert price["yes_bid"] == 0.45
        assert price["yes_ask"] == 0.48
        assert price["last_price"] == 0.46
        assert price["volume"] == 1234.0
        assert price["open_interest"] == 890.0
        assert "ts" in price

    def test_unknown_message_ignored(self):
        ws = KalshiWebSocket.__new__(KalshiWebSocket)
        ws._prices = {}
        ws._handle_message({"type": "unknown", "data": {}})
        assert ws.get_all_prices() == {}

    def test_multiple_tickers(self):
        ws = KalshiWebSocket.__new__(KalshiWebSocket)
        ws._prices = {}

        for ticker in ["KXWTI-26MAR02-B68", "KXWTI-26MAR02-B70"]:
            ws._handle_message({
                "type": "ticker",
                "msg": {
                    "market_ticker": ticker,
                    "yes_bid_dollars": "0.50",
                    "yes_ask_dollars": "0.55",
                    "last_price_dollars": "0.52",
                    "volume_fp": "100",
                    "open_interest_fp": "50",
                },
            })

        assert len(ws.get_all_prices()) == 2

    def test_price_update_overwrites(self):
        ws = KalshiWebSocket.__new__(KalshiWebSocket)
        ws._prices = {}

        ticker = "KXAAAGASM-26MAR31-B3.25"
        ws._handle_message({
            "type": "ticker",
            "msg": {
                "market_ticker": ticker,
                "yes_bid_dollars": "0.40",
                "volume_fp": "100",
            },
        })
        ws._handle_message({
            "type": "ticker",
            "msg": {
                "market_ticker": ticker,
                "yes_bid_dollars": "0.60",
                "volume_fp": "200",
            },
        })

        price = ws.get_price(ticker)
        assert price["yes_bid"] == 0.60
        assert price["volume"] == 200.0


# --------------- Series mapping ---------------

class TestSeriesMapping:
    def test_all_series_have_category(self):
        for series in ("KXAAAGASM", "KXWTI", "KXWTIW"):
            assert series in SERIES_CATEGORY

    def test_all_series_have_display(self):
        for series in ("KXAAAGASM", "KXWTI", "KXWTIW"):
            assert series in SERIES_DISPLAY

    def test_gas_category(self):
        assert SERIES_CATEGORY["KXAAAGASM"] == "gas"

    def test_oil_categories(self):
        assert SERIES_CATEGORY["KXWTI"] == "oil_daily"
        assert SERIES_CATEGORY["KXWTIW"] == "oil_weekly"


# --------------- Contract building ---------------

class TestContractBuilding:
    def test_build_contract_from_ws_data(self):
        """Provider should build a PredictionContract from WS price data."""
        from app.config import Config

        config = Config()
        provider = KalshiPredictionProvider.__new__(KalshiPredictionProvider)
        provider._config = config

        meta = {
            "_series": "KXAAAGASM",
            "_event_title": "US Gas Price March 2026",
            "title": "Gas price above $3.25?",
            "subtitle": "Will gas exceed $3.25/gal?",
            "ticker": "KXAAAGASM-26MAR31-B3.25",
            "expiration_time": "2026-03-31T23:59:00Z",
        }
        price_data = {
            "yes_bid": 0.45,
            "yes_ask": 0.48,
            "no_bid": 0.52,
            "last_price": 0.46,
            "volume": 1234.0,
            "open_interest": 890.0,
        }

        contract = provider._build_contract(
            "KXAAAGASM-26MAR31-B3.25", meta, price_data, freshness="live"
        )

        assert contract.market == "Kalshi"
        assert contract.yes_bid == 0.45
        assert contract.yes_ask == 0.48
        assert contract.volume == 1234.0
        assert contract.freshness == "live"
        assert contract.category == "gas"
        assert contract.strike == "$3.25"
        assert contract.expiration is not None
        assert contract.expiration.year == 2026
