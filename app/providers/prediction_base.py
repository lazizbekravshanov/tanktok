"""Base prediction market connector — stub enabled only with user-provided credentials."""

from __future__ import annotations

import logging

from app.providers.base import PredictionContract, PredictionProvider

logger = logging.getLogger(__name__)


class DisabledPredictionProvider(PredictionProvider):
    """Returned when no prediction market credentials are configured."""

    def is_configured(self) -> bool:
        return False

    async def get_fuel_contracts(self) -> list[PredictionContract]:
        return []
