"""Telegram bot handlers for TankTok — two-phase response for speed."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config import Config
from app.forecasting.model import generate_forecasts
from app.providers.base import (
    GeoLocation,
    MarketQuote,
    QueryResult,
    RetailPrices,
    Station,
)
from app.providers.geocode_google import GoogleGeocoder
from app.providers.geocode_osm import NominatimGeoProvider
from app.providers.markets_yfinance import YFinanceMarketProvider
from app.providers.pois_truckstops import TruckStopDB
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
        self.pois = TruckStopDB(config)  # no runtime geocoding — addresses are pre-baked
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

        # Background market data (refreshed every 5 min)
        self._cached_markets: list[MarketQuote] = []
        self._cached_retail: dict = {}  # state → RetailPrices
        self._bg_task: Optional[asyncio.Task] = None

    async def startup(self) -> None:
        """Called once when the bot starts."""
        try:
            await self.kalshi.start()
        except Exception:
            logger.exception("Kalshi startup failed")

        # Pre-fetch Pilot prices (one bulk call, all 876+ locations)
        try:
            await self.pilot_prices.fetch_all()
            logger.info("Pilot prices pre-loaded")
        except Exception:
            logger.exception("Pilot price pre-fetch failed")

        # Pre-warm market data
        try:
            self._cached_markets = await self.markets.get_quotes()
            logger.info("Market data pre-warmed")
        except Exception:
            logger.exception("Market data pre-warm failed")

        # Start background refresh loop
        self._bg_task = asyncio.create_task(self._background_refresh())

    async def shutdown(self) -> None:
        if self._bg_task:
            self._bg_task.cancel()
        try:
            await self.kalshi.stop()
        except Exception:
            logger.exception("Kalshi shutdown error")

    async def _background_refresh(self) -> None:
        """Refresh market data and Pilot prices every 5 minutes."""
        while True:
            await asyncio.sleep(300)  # 5 minutes
            try:
                self._cached_markets = await self.markets.get_quotes()
                await self.pilot_prices.fetch_all()
                logger.info("Background refresh done")
            except Exception:
                logger.exception("Background refresh failed")

    # ---- Commands ----

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "<b>TankTok</b>\n\n"
            "Send a <b>ZIP code</b> or <b>city name</b>\n"
            "to find truck stops with prices.\n\n"
            "<b>Try:</b>  <code>45202</code>  or  <code>Dallas TX</code>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "<b>TankTok</b> — Type a ZIP or city name.\n"
            "/help — this message\n"
            "/sources — data sources",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        n = len(self.pois._stops) if hasattr(self.pois, '_stops') else 0
        await update.message.reply_text(
            f"<b>Sources:</b> {n:,} truck stops | Pilot/FJ, Love's, TA/Petro prices | EIA | Yahoo Finance",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_setunits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Units: <b>$/gallon</b>", parse_mode=ParseMode.HTML)

    # ---- Main message handler — TWO-PHASE RESPONSE ----

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "").strip()
        if not text:
            return

        await update.message.chat.send_action("typing")

        # Phase 0: Geocode (uses cache after first call)
        location = await self.geo.geocode(text)
        if location is None:
            await update.message.reply_text(
                "Location not found. Try a ZIP or <code>City, ST</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Phase 1: INSTANT — station list from local DB (< 50ms)
        stations = await self.pois.nearby_stations(location.lat, location.lon)

        # Apply Pilot prices immediately (already in memory)
        if stations:
            try:
                await self.pilot_prices.enrich_prices(stations, location)
            except Exception:
                pass

        # Send Phase 1 immediately
        loc_name = location.display_name
        if len(loc_name) > 50:
            loc_name = loc_name[:47] + "..."

        phase1_text = _format_stations(loc_name, stations, self._cached_markets)
        msg = await update.message.reply_text(phase1_text, parse_mode=ParseMode.HTML)

        # Phase 2: BACKGROUND — fetch Love's + TA/Petro prices, then edit
        changed = await self._enrich_and_fetch(stations, location)

        if changed:
            phase2_text = _format_stations(loc_name, stations, self._cached_markets)
            try:
                await msg.edit_text(phase2_text, parse_mode=ParseMode.HTML)
            except Exception:
                pass  # message unchanged or edit failed — no big deal

    async def _enrich_and_fetch(self, stations: list[Station], location: GeoLocation) -> bool:
        """Fetch Love's + TA/Petro prices in parallel. Returns True if any prices were added."""
        if not stations:
            return False

        before = sum(1 for s in stations if s.diesel_price is not None or s.gas_price is not None)

        await asyncio.gather(
            self._safe_enrich(self.loves_prices, stations, location, timeout=4.0),
            self._safe_enrich(self.tapetro_prices, stations, location, timeout=4.0),
        )

        after = sum(1 for s in stations if s.diesel_price is not None or s.gas_price is not None)
        return after > before

    @staticmethod
    async def _safe_enrich(provider, stations, location, timeout=4.0):
        try:
            await asyncio.wait_for(
                provider.enrich_prices(stations, location),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("%s timed out", type(provider).__name__)
        except Exception:
            logger.exception("%s failed", type(provider).__name__)


# ---- Formatting (stateless functions) ----

def _format_stations(loc_name: str, stations: list[Station], markets: list[MarketQuote]) -> str:
    parts = [f"<b>Truck Stops near {_esc(loc_name)}</b>\n"]

    if stations:
        for i, s in enumerate(stations[:10], 1):
            name = _esc(s.name)
            dist = f"{s.distance_mi:.1f} mi"
            addr = _esc(s.address) if s.address else f"Near {s.lat:.4f}, {s.lon:.4f}"

            prices = []
            if s.diesel_price is not None:
                prices.append(f"D: <b>${s.diesel_price:.2f}</b>")
            if s.gas_price is not None:
                prices.append(f"G: <b>${s.gas_price:.2f}</b>")
            price_line = " | ".join(prices) if prices else "<i>no price</i>"

            parts.append(
                f"{i}. <b>{name}</b> — {dist}\n"
                f"   {addr}\n"
                f"   {price_line}\n"
            )
    else:
        parts.append("<i>No truck stops found nearby.</i>\n")

    # Market snapshot (from pre-cached data, instant)
    if markets:
        wti = next((q for q in markets if "CL" in q.symbol.upper() or "WTI" in q.symbol.upper()), None)
        if wti:
            arrow = "+" if wti.change_pct >= 0 else ""
            parts.append(f"<b>WTI:</b> ${wti.price:.2f} ({arrow}{wti.change_pct:.1f}%)")

    return "\n".join(parts)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
