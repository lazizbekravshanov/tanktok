"""Kalshi prediction market connector (optional, requires API credentials)."""

from __future__ import annotations

import logging

import aiohttp

from app.config import Config
from app.providers.base import PredictionContract, PredictionProvider

logger = logging.getLogger(__name__)


class KalshiPredictionProvider(PredictionProvider):
    def __init__(self, config: Config) -> None:
        self._token = config.kalshi_api_token
        self._base = config.kalshi_api_base.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._token)

    async def get_fuel_contracts(self) -> list[PredictionContract]:
        if not self.is_configured():
            return []

        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._base}/trade-api/v2/markets"
        params = {"status": "open", "limit": "50"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception:
            logger.exception("Kalshi request failed")
            return []

        results: list[PredictionContract] = []
        for market in data.get("markets", []):
            title = market.get("title", "").lower()
            # Filter for fuel/gas/oil related contracts
            if any(kw in title for kw in ("gas", "fuel", "oil", "gasoline", "diesel", "crude", "petroleum")):
                results.append(
                    PredictionContract(
                        market="Kalshi",
                        title=market.get("title", ""),
                        yes_price=market.get("yes_bid", 0) / 100,
                        no_price=market.get("no_bid", 0) / 100,
                        url=f"https://kalshi.com/markets/{market.get('ticker', '')}",
                    )
                )
        return results
