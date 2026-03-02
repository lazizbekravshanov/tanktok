"""Polymarket prediction market connector (optional, requires API credentials)."""

from __future__ import annotations

import logging

import aiohttp

from app.config import Config
from app.providers.base import PredictionContract, PredictionProvider

logger = logging.getLogger(__name__)


class PolymarketPredictionProvider(PredictionProvider):
    def __init__(self, config: Config) -> None:
        self._token = config.polymarket_api_token
        self._base = config.polymarket_api_base.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self._token)

    async def get_fuel_contracts(self) -> list[PredictionContract]:
        if not self.is_configured():
            return []

        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._base}/markets"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception:
            logger.exception("Polymarket request failed")
            return []

        results: list[PredictionContract] = []
        markets = data if isinstance(data, list) else data.get("data", [])
        for market in markets:
            question = market.get("question", "").lower()
            if any(kw in question for kw in ("gas", "fuel", "oil", "gasoline", "diesel", "crude", "petroleum")):
                tokens = market.get("tokens", [])
                yes_price = tokens[0].get("price", 0) if tokens else 0
                no_price = tokens[1].get("price", 0) if len(tokens) > 1 else 0
                results.append(
                    PredictionContract(
                        market="Polymarket",
                        title=market.get("question", ""),
                        yes_price=float(yes_price),
                        no_price=float(no_price),
                        url=market.get("url", ""),
                    )
                )
        return results
