"""Telegram bot handlers for TankTok."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config import Config
from app.forecasting.model import generate_forecasts
from app.providers.base import (
    ForecastResult,
    GeoLocation,
    MarketQuote,
    PredictionContract,
    QueryResult,
    RetailPrices,
    Station,
)
from app.providers.geocode_google import GoogleGeocoder
from app.providers.geocode_osm import NominatimGeoProvider
from app.providers.markets_yfinance import YFinanceMarketProvider
from app.providers.pois_truckstops import TruckStopDB
from app.providers.prediction_base import DisabledPredictionProvider
from app.providers.prediction_kalshi import KalshiPredictionProvider
from app.providers.prediction_polymarket import PolymarketPredictionProvider
from app.providers.prices_loves import LovesPriceProvider
from app.providers.prices_pilot import PilotPriceProvider
from app.providers.prices_tapetro import TAPetroPriceProvider
from app.providers.retail_eia import EIARetailProvider
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


class BotHandlers:
    def __init__(self, config: Config, cache: Cache) -> None:
        self.config = config
        self.cache = cache

        # Core providers
        self.geo = NominatimGeoProvider(config, cache)
        self.google_geo = GoogleGeocoder(config, cache)
        self.pois = TruckStopDB(
            config,
            google_geocoder=self.google_geo,
            nominatim_geocoder=self.geo,
        )
        self.retail = EIARetailProvider(config, cache)
        self.markets = YFinanceMarketProvider(config, cache)

        # Station-level price providers
        self.pilot_prices = PilotPriceProvider(config, cache)
        self.loves_prices = LovesPriceProvider(config, cache)
        self.tapetro_prices = TAPetroPriceProvider(config, cache)

        # Prediction providers (optional)
        self.prediction_providers = []
        self.kalshi = KalshiPredictionProvider(config, cache)
        self.prediction_providers.append(self.kalshi)
        polymarket = PolymarketPredictionProvider(config)
        if polymarket.is_configured():
            self.prediction_providers.append(polymarket)

    async def startup(self) -> None:
        """Called once when the bot starts."""
        try:
            await self.kalshi.start()
        except Exception:
            logger.exception("Kalshi startup failed — will use REST fallback")

        # Pre-fetch all Pilot/Flying J prices (one bulk call)
        try:
            await self.pilot_prices.fetch_all()
            logger.info("Pilot prices pre-loaded")
        except Exception:
            logger.exception("Pilot price pre-fetch failed")

    async def shutdown(self) -> None:
        """Called on bot shutdown."""
        try:
            await self.kalshi.stop()
        except Exception:
            logger.exception("Kalshi shutdown error")

    # ---- Commands ----

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>TankTok</b>\n\n"
            "Send a <b>ZIP code</b> or <b>city name</b>\n"
            "and I'll find nearby truck stops with prices.\n\n"
            "<b>Try it:</b>\n"
            "  <code>45202</code>\n"
            "  <code>Dallas TX</code>\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>TankTok — Help</b>\n\n"
            "Type a US ZIP code or city name.\n"
            "You'll get truck stops nearby with diesel and gas prices.\n\n"
            "<b>Commands:</b>\n"
            "  /help — this message\n"
            "  /sources — data sources"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        n_stops = len(self.pois._stops) if hasattr(self.pois, '_stops') else 0
        lines = [
            "<b>Data Sources</b>\n",
            f"Truck stops: {n_stops:,} locations",
            "Prices: Pilot/FJ, Love's, TA/Petro (live)",
            "Area avg: U.S. EIA",
            "Markets: Yahoo Finance",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_setunits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Units: <b>$/gallon</b> (US gallons)",
            parse_mode=ParseMode.HTML,
        )

    # ---- Main message handler ----

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "").strip()
        if not text:
            return

        await update.message.chat.send_action("typing")

        query = text
        is_zip = bool(ZIP_RE.match(text))

        # 1. Geocode
        location = await self.geo.geocode(query)
        if location is None:
            await update.message.reply_text(
                "Could not find that location.\n"
                "Try a ZIP code or <code>City, ST</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # 2. Fetch data
        result = await self._fetch_all(location)

        # 3. Format and reply
        reply = self._format_reply(result, query)
        for chunk in _split_message(reply, 4096):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    async def _fetch_all(self, location: GeoLocation) -> QueryResult:
        result = QueryResult(location=location)

        # Fetch stations, retail avg, markets in parallel
        gathered = await asyncio.gather(
            self.pois.nearby_stations(location.lat, location.lon),
            self.retail.get_prices(location),
            self.markets.get_quotes(),
            *[p.get_fuel_contracts() for p in self.prediction_providers],
            return_exceptions=True,
        )

        pois_result = gathered[0]
        retail_result = gathered[1]
        markets_result = gathered[2]
        prediction_results = gathered[3:]

        if isinstance(pois_result, Exception):
            logger.exception("POI fetch failed", exc_info=pois_result)
            result.errors.append("Station lookup failed.")
        else:
            result.stations = pois_result or []

        if isinstance(retail_result, Exception):
            logger.exception("Retail fetch failed", exc_info=retail_result)
        else:
            result.retail_prices = retail_result

        if isinstance(markets_result, Exception):
            logger.exception("Market fetch failed", exc_info=markets_result)
        else:
            result.market_quotes = markets_result or []

        for pr in prediction_results:
            if isinstance(pr, Exception):
                logger.exception("Prediction fetch failed", exc_info=pr)
            elif pr:
                result.prediction_contracts.extend(pr)

        # Enrich stations with real prices from Pilot, Love's, TA/Petro
        if result.stations:
            try:
                await self.pilot_prices.enrich_prices(result.stations, location)
            except Exception:
                logger.exception("Pilot price enrichment failed")

            try:
                await self.loves_prices.enrich_prices(result.stations, location)
            except Exception:
                logger.exception("Love's price enrichment failed")

            try:
                await self.tapetro_prices.enrich_prices(result.stations, location)
            except Exception:
                logger.exception("TA/Petro price enrichment failed")

        # Forecasts
        result.forecasts = generate_forecasts(result.retail_prices, result.market_quotes)

        return result

    # ---- Formatting (simple, driver-friendly) ----

    def _format_reply(self, r: QueryResult, query: str) -> str:
        parts: list[str] = []

        loc = r.location
        loc_name = loc.display_name if loc else query
        # Shorten long names
        if len(loc_name) > 50:
            loc_name = loc_name[:47] + "..."
        parts.append(f"<b>Truck Stops near {_esc(loc_name)}</b>\n")

        # ---- Stations with prices ----
        if r.stations:
            for i, s in enumerate(r.stations[:10], 1):
                parts.append(self._fmt_station(i, s))
        else:
            parts.append("<i>No truck stops found in this area.</i>\n")

        # ---- Area average (one line) ----
        if r.retail_prices:
            rp = r.retail_prices
            avg_parts = []
            if rp.diesel is not None:
                avg_parts.append(f"Diesel ${rp.diesel:.2f}")
            if rp.regular_gas is not None:
                avg_parts.append(f"Gas ${rp.regular_gas:.2f}")
            if avg_parts:
                parts.append(f"\n<b>Area avg ({_esc(rp.region)}):</b> {' | '.join(avg_parts)}")

        # ---- Market (one line, WTI only) ----
        if r.market_quotes:
            wti = next((q for q in r.market_quotes if "WTI" in q.symbol.upper() or "CL" in q.symbol.upper()), None)
            if wti:
                arrow = "+" if wti.change_pct >= 0 else ""
                parts.append(f"<b>Oil (WTI):</b> ${wti.price:.2f} ({arrow}{wti.change_pct:.1f}%)")

        # ---- Forecast (compact) ----
        if r.forecasts:
            fc_parts = []
            for f in r.forecasts:
                label = "Diesel" if "diesel" in f.fuel_type.lower() else "Gas"
                fc_parts.append(f"{label} ${f.low:.2f}-${f.high:.2f}")
            if fc_parts:
                parts.append(f"<b>7-day forecast:</b> {' | '.join(fc_parts)}")

        return "\n".join(parts)

    @staticmethod
    def _fmt_station(idx: int, s: Station) -> str:
        """Format a single station — clean and scannable."""
        name = _esc(s.name)
        dist = f"{s.distance_mi:.1f} mi"
        addr = _esc(s.address) if s.address else "Address unavailable"

        # Price line
        prices = []
        if s.diesel_price is not None:
            prices.append(f"Diesel <b>${s.diesel_price:.2f}</b>")
        if s.gas_price is not None:
            prices.append(f"Gas <b>${s.gas_price:.2f}</b>")

        if prices:
            price_line = " | ".join(prices)
        else:
            price_line = "<i>prices unavailable</i>"

        return (
            f"{idx}. <b>{name}</b> — {dist}\n"
            f"   {addr}\n"
            f"   {price_line}\n"
        )


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _split_message(text: str, limit: int) -> list[str]:
    """Split a long message into chunks at newline boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        idx = text.rfind("\n", 0, limit)
        if idx == -1:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return chunks
