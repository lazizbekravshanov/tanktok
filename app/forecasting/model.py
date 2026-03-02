"""Simple fuel-price forecast using retail history + futures trend."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.providers.base import ForecastResult, MarketQuote, RetailPrices

logger = logging.getLogger(__name__)


def generate_forecasts(
    retail: Optional[RetailPrices],
    market_quotes: list[MarketQuote],
) -> list[ForecastResult]:
    """
    Produce a naive 7-day forecast for regular gas and diesel.

    Approach:
    - Use the last retail price as the base.
    - Scale by the futures day-change % (RBOB for gas, HO for diesel) as a directional signal.
    - Apply a ±3 % band (widened if futures are volatile).
    - If insufficient data, return an empty list with a note.
    """
    if retail is None:
        return []

    now = datetime.now(timezone.utc)

    # Map futures change to fuel types
    rbob_pct = 0.0
    ho_pct = 0.0
    for q in market_quotes:
        if q.symbol == "RB=F":
            rbob_pct = q.change_pct
        elif q.symbol == "HO=F":
            ho_pct = q.change_pct

    results: list[ForecastResult] = []

    # --- Regular gasoline ---
    if retail.regular_gas is not None:
        base = retail.regular_gas
        # Directional shift from RBOB futures
        shift = base * (rbob_pct / 100) * 0.5  # dampen
        center = base + shift
        band = max(base * 0.03, 0.05)  # at least 5 cents

        # Widen band if recent change was large
        if retail.regular_gas_prev is not None:
            weekly_move = abs(base - retail.regular_gas_prev)
            band = max(band, weekly_move * 1.2)

        results.append(
            ForecastResult(
                fuel_type="Regular Gasoline",
                low=round(center - band, 3),
                high=round(center + band, 3),
                confidence=_confidence_note(retail, rbob_pct),
                model_timestamp=now,
            )
        )

    # --- Diesel ---
    if retail.diesel is not None:
        base = retail.diesel
        shift = base * (ho_pct / 100) * 0.5
        center = base + shift
        band = max(base * 0.03, 0.05)

        if retail.diesel_prev is not None:
            weekly_move = abs(base - retail.diesel_prev)
            band = max(band, weekly_move * 1.2)

        results.append(
            ForecastResult(
                fuel_type="Diesel",
                low=round(center - band, 3),
                high=round(center + band, 3),
                confidence=_confidence_note(retail, ho_pct),
                model_timestamp=now,
            )
        )

    return results


def _confidence_note(retail: RetailPrices, futures_pct: float) -> str:
    has_prev = retail.regular_gas_prev is not None or retail.diesel_prev is not None
    if not has_prev:
        return "Low — insufficient historical data; only latest retail + futures used."
    if abs(futures_pct) > 3:
        return "Medium — futures show significant movement; wider band applied."
    return "Medium — based on weekly EIA retail data + energy futures trend."
