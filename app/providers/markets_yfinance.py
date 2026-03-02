"""Market data provider using yfinance for energy futures."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import Config
from app.providers.base import MarketProvider, MarketQuote
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

SYMBOLS = {
    "CL=F": "WTI Crude Oil",
    "RB=F": "RBOB Gasoline",
    "HO=F": "Heating Oil (ULSD proxy)",
}


class YFinanceMarketProvider(MarketProvider):
    def __init__(self, config: Config, cache: Cache) -> None:
        self._cache = cache
        self._ttl = config.cache_market_ttl

    async def get_quotes(self) -> list[MarketQuote]:
        cache_key = "markets:energy"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # yfinance is synchronous — run in executor
        quotes = await asyncio.get_event_loop().run_in_executor(None, self._fetch_sync)
        if quotes:
            self._cache.set(cache_key, quotes, ttl=self._ttl)
        return quotes

    @staticmethod
    def _fetch_sync() -> list[MarketQuote]:
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed")
            return []

        quotes: list[MarketQuote] = []
        for symbol, name in SYMBOLS.items():
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.fast_info
                price: Optional[float] = getattr(info, "last_price", None)
                prev_close: Optional[float] = getattr(info, "previous_close", None)

                if price is None:
                    # Fallback: try history
                    hist = ticker.history(period="2d")
                    if len(hist) >= 1:
                        price = float(hist["Close"].iloc[-1])
                    if len(hist) >= 2:
                        prev_close = float(hist["Close"].iloc[-2])

                if price is None:
                    continue

                change_pct = 0.0
                if prev_close and prev_close != 0:
                    change_pct = round(((price - prev_close) / prev_close) * 100, 2)

                quotes.append(
                    MarketQuote(
                        symbol=symbol,
                        name=name,
                        price=round(price, 2),
                        change_pct=change_pct,
                        timestamp=datetime.now(timezone.utc),
                    )
                )
            except Exception:
                logger.exception("Failed to fetch %s", symbol)

        return quotes
