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
from app.providers.geocode_osm import NominatimGeoProvider
from app.providers.markets_yfinance import YFinanceMarketProvider
from app.providers.pois_truckstops import TruckStopDB
from app.providers.prediction_base import DisabledPredictionProvider
from app.providers.prediction_kalshi import KalshiPredictionProvider
from app.providers.prediction_polymarket import PolymarketPredictionProvider
from app.providers.retail_eia import EIARetailProvider
from app.storage.cache import Cache

logger = logging.getLogger(__name__)

ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_started_at = datetime.now(timezone.utc)


class BotHandlers:
    def __init__(self, config: Config, cache: Cache) -> None:
        self.config = config
        self.cache = cache

        # Core providers
        self.geo = NominatimGeoProvider(config, cache)
        self.pois = TruckStopDB(config)
        self.retail = EIARetailProvider(config, cache)
        self.markets = YFinanceMarketProvider(config, cache)

        # Prediction providers (optional)
        self.prediction_providers = []
        self.kalshi = KalshiPredictionProvider(config, cache)
        self.prediction_providers.append(self.kalshi)
        polymarket = PolymarketPredictionProvider(config)
        if polymarket.is_configured():
            self.prediction_providers.append(polymarket)

    async def startup(self) -> None:
        """Called once when the bot starts — initializes async providers."""
        try:
            await self.kalshi.start()
        except Exception:
            logger.exception("Kalshi startup failed — will use REST fallback")

    async def shutdown(self) -> None:
        """Called on bot shutdown."""
        try:
            await self.kalshi.stop()
        except Exception:
            logger.exception("Kalshi shutdown error")

    # ---- Commands ----

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Welcome to TankTok!</b>\n\n"
            "Send me a <b>US ZIP code</b> or <b>city name</b> and I'll return:\n"
            "  • Gas &amp; diesel prices for your area\n"
            "  • Nearby truck stops (Pilot, Love's, TA, etc.)\n"
            "  • Energy market snapshot\n"
            "  • 7-day price forecast\n\n"
            "<b>Examples:</b>\n"
            "  <code>45202</code>\n"
            "  <code>Cincinnati OH</code>\n"
            "  <code>Fort Mitchell, KY</code>\n\n"
            "<b>Commands:</b>\n"
            "  /help — usage &amp; sources\n"
            "  /sources — enabled data sources\n"
            "  /setunits — unit settings"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>TankTok — Help</b>\n\n"
            "<b>How to use:</b>\n"
            "Just type a US ZIP code (e.g. <code>45202</code>) or a city/state "
            "(e.g. <code>Miami, FL</code>) and hit send.\n\n"
            "<b>What you get:</b>\n"
            "1. Area-level retail prices (gas + diesel) from EIA\n"
            "2. Up to 10 nearby truck stops / travel centers from OpenStreetMap\n"
            "3. Energy futures snapshot (WTI, RBOB, Heating Oil)\n"
            "4. Simple 7-day price forecast\n\n"
            "<b>Data sources:</b>\n"
            "• Geocoding: Nominatim / OpenStreetMap\n"
            "• Stations: Overpass API (OSM)\n"
            "• Retail prices: U.S. EIA\n"
            "• Markets: Yahoo Finance\n"
            "• Station-level prices: requires optional API plugin\n\n"
            "<i>Station-level posted prices are only available when a "
            "crowd-sourced or commercial price feed is configured.</i>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines = ["<b>Enabled Data Sources</b>\n"]
        lines.append("• Geocoding: Nominatim (OSM) ✓")
        n_stops = len(self.pois._stops) if hasattr(self.pois, '_stops') else 0
        lines.append(f"• Truck stop database: {n_stops:,} locations ✓")

        if self.config.eia_api_key:
            lines.append("• Retail prices: U.S. EIA ✓")
        else:
            lines.append("• Retail prices: U.S. EIA ✗ (EIA_API_KEY not set)")

        lines.append("• Market data: Yahoo Finance ✓")

        if self.config.crowd_api_key:
            lines.append("• Station prices: Crowd-sourced API ✓")
        else:
            lines.append("• Station prices: Crowd-sourced API ✗ (not configured)")

        if self.config.commercial_feed_key:
            lines.append("• Station prices: Commercial feed ✓")
        else:
            lines.append("• Station prices: Commercial feed ✗ (not configured)")

        # Kalshi status
        if self.kalshi._ws.is_connected:
            lines.append("• Kalshi: ✓ WebSocket (live)")
            lines.append(f"  └ Tracking {len(self.kalshi._market_tickers)} contracts")
        elif self.kalshi._market_tickers:
            lines.append("• Kalshi: ✓ REST polling")
            lines.append(f"  └ Tracking {len(self.kalshi._market_tickers)} contracts")
        elif self.config.kalshi_key_id:
            lines.append("• Kalshi: ⚠ configured but no markets discovered")
        else:
            lines.append("• Kalshi: ✓ public mode (no auth)")

        # Other prediction providers
        has_polymarket = any(
            p.is_configured() for p in self.prediction_providers
            if not isinstance(p, KalshiPredictionProvider)
        )
        if has_polymarket:
            lines.append("• Polymarket: ✓")

        lines.append(f"\n<i>Bot started: {_started_at:%Y-%m-%d %H:%M UTC}</i>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_setunits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Units are currently set to <b>US gallons</b> ($/gal).\n"
            "Additional unit options coming soon.",
            parse_mode=ParseMode.HTML,
        )

    # ---- Main message handler ----

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "").strip()
        if not text:
            return

        # Send a "typing" indicator
        await update.message.chat.send_action("typing")

        query = text
        is_zip = bool(ZIP_RE.match(text))

        # 1. Geocode
        location = await self.geo.geocode(query)
        if location is None:
            await update.message.reply_text(
                "Could not find that location. "
                "Try a US ZIP code or <code>City, ST</code> format.",
                parse_mode=ParseMode.HTML,
            )
            return

        # 2. Fetch data in parallel
        result = await self._fetch_all(location)

        # 3. Format and reply
        reply = self._format_reply(result, query, is_zip)
        # Telegram has a 4096 char limit; split if needed
        for chunk in _split_message(reply, 4096):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    async def _fetch_all(self, location: GeoLocation) -> QueryResult:
        result = QueryResult(location=location)

        tasks = {
            "retail": self.retail.get_prices(location),
            "pois": self.pois.nearby_stations(location.lat, location.lon),
            "markets": self.markets.get_quotes(),
        }

        # Prediction markets
        prediction_tasks = [p.get_fuel_contracts() for p in self.prediction_providers]

        gathered = await asyncio.gather(
            tasks["retail"],
            tasks["pois"],
            tasks["markets"],
            *prediction_tasks,
            return_exceptions=True,
        )

        # Unpack
        retail_result = gathered[0]
        pois_result = gathered[1]
        markets_result = gathered[2]
        prediction_results = gathered[3:]

        if isinstance(retail_result, Exception):
            logger.exception("Retail fetch failed", exc_info=retail_result)
            result.errors.append("Retail prices temporarily unavailable.")
        else:
            result.retail_prices = retail_result

        if isinstance(pois_result, Exception):
            logger.exception("POI fetch failed", exc_info=pois_result)
            result.errors.append("Nearby stations lookup failed.")
        else:
            result.stations = pois_result if pois_result else []

        if isinstance(markets_result, Exception):
            logger.exception("Market fetch failed", exc_info=markets_result)
            result.errors.append("Market data temporarily unavailable.")
        else:
            result.market_quotes = markets_result if markets_result else []

        for pr in prediction_results:
            if isinstance(pr, Exception):
                logger.exception("Prediction fetch failed", exc_info=pr)
            elif pr:
                result.prediction_contracts.extend(pr)

        # Generate forecasts
        result.forecasts = generate_forecasts(result.retail_prices, result.market_quotes)

        return result

    # ---- Formatting ----

    def _format_reply(self, r: QueryResult, query: str, is_zip: bool) -> str:
        parts: list[str] = []

        loc = r.location
        header = loc.display_name if loc else query
        # Truncate long OSM display names
        if len(header) > 80:
            header = header[:77] + "…"
        parts.append(f"<b>⛽ TankTok — {_esc(header)}</b>\n")

        # ---- Retail Prices ----
        parts.append(self._fmt_retail(r.retail_prices))

        # ---- Nearby Stations ----
        parts.append(self._fmt_stations(r.stations[:10]))

        # ---- Market Snapshot ----
        parts.append(self._fmt_markets(r.market_quotes))

        # ---- Forecast ----
        parts.append(self._fmt_forecast(r.forecasts))

        # ---- Prediction Markets ----
        parts.append(self._fmt_predictions(r.prediction_contracts))

        # ---- Errors / footer ----
        if r.errors:
            parts.append("<i>⚠ " + " | ".join(r.errors) + "</i>\n")

        parts.append(f"<i>🕐 {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}</i>")

        return "\n".join(parts)

    @staticmethod
    def _fmt_retail(rp: Optional[RetailPrices]) -> str:
        if rp is None:
            return (
                "<b>📊 Area Prices</b>\n"
                "<i>Retail price data unavailable. "
                "Ensure EIA_API_KEY is set.</i>\n"
            )

        lines = [f"<b>📊 Area Prices — {_esc(rp.region)}</b>"]

        if rp.regular_gas is not None:
            delta = ""
            if rp.regular_gas_prev is not None:
                d = rp.regular_gas - rp.regular_gas_prev
                arrow = "▲" if d >= 0 else "▼"
                delta = f"  {arrow} ${abs(d):.3f} ({rp.period})"
            lines.append(f"  Regular Gas: <b>${rp.regular_gas:.3f}</b>/gal{delta}")

        if rp.diesel is not None:
            delta = ""
            if rp.diesel_prev is not None:
                d = rp.diesel - rp.diesel_prev
                arrow = "▲" if d >= 0 else "▼"
                delta = f"  {arrow} ${abs(d):.3f} ({rp.period})"
            lines.append(f"  Diesel: <b>${rp.diesel:.3f}</b>/gal{delta}")

        ts = ""
        if rp.timestamp:
            ts = f" ({rp.timestamp:%Y-%m-%d})"
        lines.append(f"  <i>Source: {rp.source}{ts}</i>\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_stations(stations: list[Station]) -> str:
        if not stations:
            return (
                "<b>🚛 Nearby Truck Stops</b>\n"
                "<i>No truck stops found in this area.</i>\n"
            )

        lines = ["<b>🚛 Nearby Truck Stops</b>"]
        for i, s in enumerate(stations, 1):
            name = _esc(s.name)
            addr = _esc(s.address)
            dist = f"{s.distance_mi:.1f} mi"

            price_info = ""
            if s.gas_price is not None and s.diesel_price is not None:
                price_info = (
                    f"  Gas ${s.gas_price:.3f} | Diesel ${s.diesel_price:.3f} "
                    f"[{s.price_source}]"
                )
            elif s.gas_price is not None:
                price_info = f"  Gas ${s.gas_price:.3f} [{s.price_source}]"
            elif s.diesel_price is not None:
                price_info = f"  Diesel ${s.diesel_price:.3f} [{s.price_source}]"
            else:
                price_info = "  <i>price unavailable</i>"

            lines.append(f"  {i}. <b>{name}</b> ({dist})")
            lines.append(f"     {addr}")
            lines.append(f"    {price_info}")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_markets(quotes: list[MarketQuote]) -> str:
        if not quotes:
            return (
                "<b>📈 Market Snapshot</b>\n"
                "<i>Market data unavailable.</i>\n"
            )

        lines = ["<b>📈 Market Snapshot</b>"]
        for q in quotes:
            arrow = "▲" if q.change_pct >= 0 else "▼"
            lines.append(
                f"  {_esc(q.name)}: <b>${q.price:.2f}</b>  "
                f"{arrow} {abs(q.change_pct):.2f}%"
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_forecast(forecasts: list[ForecastResult]) -> str:
        if not forecasts:
            return (
                "<b>🔮 7-Day Forecast</b>\n"
                "<i>Insufficient data for forecast.</i>\n"
            )

        lines = ["<b>🔮 7-Day Forecast</b>"]
        for f in forecasts:
            lines.append(
                f"  {f.fuel_type}: <b>${f.low:.3f} – ${f.high:.3f}</b>/gal"
            )
            if f.confidence:
                lines.append(f"    <i>Confidence: {f.confidence}</i>")
        if forecasts and forecasts[0].model_timestamp:
            lines.append(
                f"  <i>Model run: {forecasts[0].model_timestamp:%Y-%m-%d %H:%M UTC}</i>"
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _fmt_predictions(contracts: list[PredictionContract]) -> str:
        if not contracts:
            return (
                "<b>🎲 Prediction Markets</b>\n"
                "<i>No matching prediction market contracts found "
                "for fuel prices today.</i>\n"
            )

        lines = ["<b>🎲 Prediction Markets</b>"]

        # Group by category
        gas = [c for c in contracts if c.category == "gas"]
        oil = [c for c in contracts if c.category in ("oil_daily", "oil_weekly")]
        other = [c for c in contracts if c.category not in ("gas", "oil_daily", "oil_weekly")]

        for label, group in [("US Gas Price", gas), ("WTI Oil", oil), ("Other", other)]:
            if not group:
                continue
            lines.append(f"\n  <b>{label}</b>")
            for c in group[:8]:  # cap per group
                title = _esc(c.title)
                # Compact price display
                price_parts = []
                if c.yes_bid is not None and c.yes_ask is not None:
                    price_parts.append(f"Bid ${c.yes_bid:.2f} / Ask ${c.yes_ask:.2f}")
                elif c.yes_price > 0:
                    price_parts.append(f"Yes ${c.yes_price:.2f}")
                if c.last_price is not None and c.last_price > 0:
                    price_parts.append(f"Last ${c.last_price:.2f}")
                price_str = " | ".join(price_parts) if price_parts else "no price"

                vol_str = ""
                if c.volume > 0:
                    vol_str = f" vol:{c.volume:,.0f}"

                fresh = ""
                if c.freshness == "live":
                    fresh = " ⚡"
                elif c.freshness == "recent":
                    fresh = ""

                lines.append(f"  • {title}{fresh}")
                lines.append(f"    {price_str}{vol_str}")

        # Source note
        has_live = any(c.freshness == "live" for c in contracts)
        if has_live:
            lines.append("\n  <i>⚡ = live via Kalshi WebSocket</i>")

        lines.append("")
        return "\n".join(lines)


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
        # Find last newline before limit
        idx = text.rfind("\n", 0, limit)
        if idx == -1:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return chunks
