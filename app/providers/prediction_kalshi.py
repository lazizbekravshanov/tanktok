"""
Kalshi prediction market integration.

Supports two modes:
  1. REST polling — discover energy contracts and poll prices every N seconds.
  2. WebSocket streaming — subscribe to the `ticker` channel for real-time
     price updates (sub-second latency).

Authentication uses RSA-PSS signatures per Kalshi API v2.
Public endpoints (markets, events, trades) work without auth.
WebSocket and orderbook require auth.

Energy series tracked:
  - KXAAAGASM  — US average gas price (monthly contracts)
  - KXWTI      — WTI crude oil daily settle
  - KXWTIW     — WTI crude oil weekly range
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from app.config import Config
from app.providers.base import PredictionContract, PredictionProvider
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

# --------------- RSA-PSS Auth ---------------

def _load_private_key(path: str):
    """Load an RSA private key from PEM file."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError:
        logger.error(
            "cryptography package required for Kalshi auth. "
            "Install with: pip install cryptography"
        )
        return None

    pem_data = Path(path).read_bytes()
    return load_pem_private_key(pem_data, password=None)


def _sign_request(private_key, timestamp_ms: str, method: str, path: str) -> str:
    """
    Produce RSA-PSS signature for Kalshi API.
    Message = timestamp_ms + METHOD + path (no query string).
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    message = (timestamp_ms + method.upper() + path).encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return b64encode(signature).decode("ascii")


def _auth_headers(key_id: str, private_key, method: str, path: str) -> dict[str, str]:
    """Build the three Kalshi auth headers."""
    ts = str(int(time.time() * 1000))
    sig = _sign_request(private_key, ts, method, path)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig,
    }


# --------------- Category helpers ---------------

SERIES_CATEGORY = {
    "KXAAAGASM": "gas",
    "KXWTI": "oil_daily",
    "KXWTIW": "oil_weekly",
}

SERIES_DISPLAY = {
    "KXAAAGASM": "US Gas Price",
    "KXWTI": "WTI Oil (Daily)",
    "KXWTIW": "WTI Oil (Weekly)",
}


def _parse_strike(ticker: str) -> Optional[str]:
    """Extract strike from a market ticker like KXAAAGASM-26MAR31-B3.25 → '$3.25'."""
    parts = ticker.rsplit("-", 1)
    if len(parts) == 2 and parts[1].startswith("B"):
        try:
            val = parts[1][1:]
            float(val)  # validate
            return f"${val}"
        except ValueError:
            pass
    return None


# --------------- REST Client ---------------

class KalshiRestClient:
    """Thin async REST client for Kalshi API v2."""

    def __init__(self, config: Config) -> None:
        self._base = config.kalshi_api_base.rstrip("/")
        self._key_id = config.kalshi_key_id
        self._private_key = None
        self._series = config.kalshi_energy_series

        if config.kalshi_private_key_path:
            self._private_key = _load_private_key(config.kalshi_private_key_path)

    @property
    def has_auth(self) -> bool:
        return bool(self._key_id and self._private_key)

    def _headers(self, method: str = "GET", path: str = "") -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.has_auth:
            headers.update(_auth_headers(self._key_id, self._private_key, method, path))
        return headers

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base}{path}"
        headers = self._headers("GET", path)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def get_events(
        self, series_ticker: str, status: str = "open"
    ) -> list[dict]:
        """Fetch events for a series, with nested markets."""
        path = "/trade-api/v2/events"
        params = {
            "series_ticker": series_ticker,
            "status": status,
            "with_nested_markets": "true",
            "limit": "200",
        }
        data = await self._get(path, params)
        return data.get("events", [])

    async def get_markets(
        self, event_ticker: str | None = None,
        series_ticker: str | None = None,
        status: str = "open",
    ) -> list[dict]:
        """Fetch markets, optionally filtered."""
        path = "/trade-api/v2/markets"
        params: dict[str, str] = {"status": status, "limit": "200"}
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        data = await self._get(path, params)
        return data.get("markets", [])

    async def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        path = f"/trade-api/v2/markets/{ticker}"
        data = await self._get(path)
        return data.get("market", {})

    async def get_trades(
        self, ticker: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Fetch recent trades."""
        path = "/trade-api/v2/markets/trades"
        params: dict[str, str] = {"limit": str(limit)}
        if ticker:
            params["ticker"] = ticker
        data = await self._get(path, params)
        return data.get("trades", [])

    async def discover_energy_markets(self) -> list[dict]:
        """Discover all open energy-related markets across tracked series."""
        all_markets: list[dict] = []
        for series in self._series:
            try:
                events = await self.get_events(series)
                for event in events:
                    for market in event.get("markets", []):
                        market["_series"] = series
                        market["_event_title"] = event.get("title", "")
                        all_markets.append(market)
            except Exception:
                logger.exception("Failed to discover markets for series %s", series)
        return all_markets


# --------------- WebSocket Stream ---------------

class KalshiWebSocket:
    """
    Persistent WebSocket connection to Kalshi ticker channel.
    Maintains an in-memory dict of latest prices keyed by market ticker.
    """

    def __init__(self, config: Config) -> None:
        self._ws_url = config.kalshi_ws_url
        self._key_id = config.kalshi_key_id
        self._private_key = None
        self._prices: dict[str, dict[str, Any]] = {}
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._subscribed_tickers: list[str] = []
        self._msg_id = 0

        if config.kalshi_private_key_path:
            self._private_key = _load_private_key(config.kalshi_private_key_path)

    @property
    def has_auth(self) -> bool:
        return bool(self._key_id and self._private_key)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_price(self, ticker: str) -> Optional[dict]:
        return self._prices.get(ticker)

    def get_all_prices(self) -> dict[str, dict]:
        return dict(self._prices)

    def subscribe(self, tickers: list[str]) -> None:
        """Register tickers to subscribe to (call before or after start)."""
        self._subscribed_tickers = list(set(self._subscribed_tickers + tickers))

    def start(self) -> None:
        """Start the WebSocket loop as a background task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_forever())
            logger.info("Kalshi WebSocket task started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("Kalshi WebSocket stopped")

    async def _run_forever(self) -> None:
        """Reconnect loop with exponential backoff."""
        backoff = 1
        while True:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Kalshi WS error, reconnecting in %ds", backoff)
            self._connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect_and_listen(self) -> None:
        if not self.has_auth:
            logger.warning("Kalshi WS requires auth — falling back to REST polling")
            # Sleep forever (caller will use REST instead)
            await asyncio.sleep(3600 * 24)
            return

        path = "/trade-api/ws/v2"
        headers = _auth_headers(self._key_id, self._private_key, "GET", path)

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._ws_url,
                headers=headers,
                heartbeat=30,
                timeout=aiohttp.ClientTimeout(total=None),
            ) as ws:
                self._connected = True
                logger.info("Kalshi WebSocket connected")

                # Subscribe to ticker channel
                if self._subscribed_tickers:
                    self._msg_id += 1
                    sub_msg = {
                        "id": self._msg_id,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker"],
                            "market_tickers": self._subscribed_tickers,
                        },
                    }
                    await ws.send_json(sub_msg)
                    logger.info(
                        "Subscribed to %d tickers", len(self._subscribed_tickers)
                    )

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_message(json.loads(msg.data))
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error("WS error: %s", ws.exception())
                        break
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        break

    def _handle_message(self, data: dict) -> None:
        msg_type = data.get("type", "")

        if msg_type == "ticker":
            msg = data.get("msg", {})
            ticker = msg.get("market_ticker", "")
            if ticker:
                self._prices[ticker] = {
                    "yes_bid": _to_dollar(msg.get("yes_bid_dollars")),
                    "yes_ask": _to_dollar(msg.get("yes_ask_dollars")),
                    "no_bid": _to_dollar(msg.get("no_bid_dollars")),
                    "no_ask": _to_dollar(msg.get("no_ask_dollars")),
                    "last_price": _to_dollar(msg.get("last_price_dollars")),
                    "volume": _to_float(msg.get("volume_fp")),
                    "open_interest": _to_float(msg.get("open_interest_fp")),
                    "ts": datetime.now(timezone.utc),
                }
        elif msg_type == "error":
            logger.error("Kalshi WS error message: %s", data)


def _to_dollar(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


# --------------- Main Provider ---------------

class KalshiPredictionProvider(PredictionProvider):
    """
    Full Kalshi integration with REST + optional WebSocket.

    Without auth: uses public endpoints only (markets, events, trades).
    With auth: also enables WebSocket streaming for real-time prices.
    """

    def __init__(self, config: Config, cache: Optional[Cache] = None) -> None:
        self._config = config
        self._rest = KalshiRestClient(config)
        self._ws = KalshiWebSocket(config)
        self._cache = cache
        self._use_ws = config.kalshi_use_websocket
        self._poll_interval = config.kalshi_poll_interval
        self._market_tickers: list[str] = []
        self._market_meta: dict[str, dict] = {}  # ticker → market metadata
        self._discovery_task: Optional[asyncio.Task] = None

    def is_configured(self) -> bool:
        # Public endpoints work without auth, just need a non-default base
        return bool(self._config.kalshi_key_id) or True  # Always try public endpoints

    async def start(self) -> None:
        """Initialize: discover markets and optionally start WebSocket."""
        await self._discover_and_subscribe()

        # Start periodic re-discovery
        self._discovery_task = asyncio.create_task(self._periodic_discovery())

        # Start WebSocket if auth is available and enabled
        if self._use_ws and self._ws.has_auth:
            self._ws.start()
            logger.info("Kalshi WebSocket streaming enabled")
        else:
            if not self._ws.has_auth:
                logger.info("Kalshi running in public-only mode (no auth for WebSocket)")
            logger.info("Kalshi using REST polling (interval: %ds)", self._poll_interval)

    async def stop(self) -> None:
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()
        await self._ws.stop()

    async def _discover_and_subscribe(self) -> None:
        """Discover energy markets and register tickers for WS subscription."""
        try:
            raw_markets = await self._rest.discover_energy_markets()
        except Exception:
            logger.exception("Kalshi market discovery failed")
            return

        tickers = []
        for m in raw_markets:
            ticker = m.get("ticker", "")
            if not ticker:
                continue
            tickers.append(ticker)
            self._market_meta[ticker] = m

        self._market_tickers = tickers
        self._ws.subscribe(tickers)
        logger.info("Discovered %d Kalshi energy markets", len(tickers))

    async def _periodic_discovery(self) -> None:
        """Re-discover markets periodically (contracts expire, new ones open)."""
        while True:
            await asyncio.sleep(3600)  # every hour
            try:
                await self._discover_and_subscribe()
                # Re-subscribe WS if connected
                if self._ws.is_connected and self._market_tickers:
                    self._ws.subscribe(self._market_tickers)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic Kalshi discovery failed")

    async def get_fuel_contracts(self) -> list[PredictionContract]:
        """Return current energy prediction contracts with latest prices."""
        contracts: list[PredictionContract] = []

        # If we haven't discovered yet, try now
        if not self._market_tickers:
            await self._discover_and_subscribe()

        for ticker in self._market_tickers:
            meta = self._market_meta.get(ticker, {})
            series = meta.get("_series", "")
            category = SERIES_CATEGORY.get(series, "")

            # Try WebSocket price first (freshest)
            ws_price = self._ws.get_price(ticker)
            if ws_price:
                contracts.append(self._build_contract(
                    ticker, meta, ws_price, freshness="live"
                ))
                continue

            # Fall back to REST market data (already in meta from discovery)
            contracts.append(self._build_contract_from_rest(ticker, meta))

        # Sort: gas contracts first, then oil, by expiration
        contracts.sort(key=lambda c: (
            0 if c.category == "gas" else 1,
            c.expiration or datetime.max.replace(tzinfo=timezone.utc),
        ))

        return contracts

    def _build_contract(
        self, ticker: str, meta: dict, price_data: dict, freshness: str
    ) -> PredictionContract:
        series = meta.get("_series", "")
        event_title = meta.get("_event_title", "") or meta.get("title", "")
        subtitle = meta.get("subtitle", "")
        title = subtitle if subtitle else event_title

        strike = _parse_strike(ticker)
        if strike and title and strike not in title:
            title = f"{title} (above {strike})"

        exp_ts = meta.get("expiration_time") or meta.get("close_time")
        expiration = None
        if exp_ts:
            try:
                expiration = datetime.fromisoformat(exp_ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return PredictionContract(
            market="Kalshi",
            title=title or ticker,
            yes_price=price_data.get("yes_bid") or 0.0,
            no_price=price_data.get("no_bid") or 0.0,
            url=f"https://kalshi.com/markets/{series.lower()}/{ticker.lower()}",
            ticker=ticker,
            volume=price_data.get("volume", 0.0),
            open_interest=price_data.get("open_interest", 0.0),
            last_price=price_data.get("last_price"),
            yes_bid=price_data.get("yes_bid"),
            yes_ask=price_data.get("yes_ask"),
            expiration=expiration,
            category=SERIES_CATEGORY.get(series, ""),
            strike=strike,
            freshness=freshness,
        )

    def _build_contract_from_rest(
        self, ticker: str, meta: dict
    ) -> PredictionContract:
        """Build contract from REST /events response data."""
        price_data = {
            "yes_bid": _to_dollar(
                meta.get("yes_bid_dollars") or meta.get("yes_bid", 0)
            ),
            "yes_ask": _to_dollar(
                meta.get("yes_ask_dollars") or meta.get("yes_ask", 0)
            ),
            "no_bid": _to_dollar(
                meta.get("no_bid_dollars") or meta.get("no_bid", 0)
            ),
            "last_price": _to_dollar(
                meta.get("last_price_dollars") or meta.get("last_price", 0)
            ),
            "volume": _to_float(
                meta.get("volume_fp") or meta.get("volume", 0)
            ),
            "open_interest": _to_float(
                meta.get("open_interest_fp") or meta.get("open_interest", 0)
            ),
        }
        return self._build_contract(ticker, meta, price_data, freshness="recent")

    async def get_market_snapshot(self, ticker: str) -> Optional[dict]:
        """Get fresh data for a single market (for on-demand refresh)."""
        ws_price = self._ws.get_price(ticker)
        if ws_price:
            return ws_price
        try:
            market = await self._rest.get_market(ticker)
            return {
                "yes_bid": _to_dollar(market.get("yes_bid_dollars")),
                "yes_ask": _to_dollar(market.get("yes_ask_dollars")),
                "no_bid": _to_dollar(market.get("no_bid_dollars")),
                "last_price": _to_dollar(market.get("last_price_dollars")),
                "volume": _to_float(market.get("volume_fp")),
                "open_interest": _to_float(market.get("open_interest_fp")),
            }
        except Exception:
            logger.exception("Failed to fetch market %s", ticker)
            return None

    async def get_recent_trades(self, ticker: str, limit: int = 5) -> list[dict]:
        """Fetch recent trades for a specific contract."""
        try:
            trades = await self._rest.get_trades(ticker=ticker, limit=limit)
            return [
                {
                    "price": _to_dollar(t.get("yes_price_dollars")),
                    "count": _to_float(t.get("count_fp")),
                    "side": t.get("taker_side"),
                    "time": t.get("created_time"),
                }
                for t in trades
            ]
        except Exception:
            logger.exception("Failed to fetch trades for %s", ticker)
            return []
