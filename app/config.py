"""TankTok configuration — all secrets and tunables from env vars."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # reads .env into os.environ


@dataclass(frozen=True)
class Config:
    # Telegram
    telegram_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )

    # EIA
    eia_api_key: str = field(
        default_factory=lambda: os.environ.get("EIA_API_KEY", "")
    )

    # Optional crowd-sourced station prices
    crowd_api_key: str = field(
        default_factory=lambda: os.environ.get("CROWD_API_KEY", "")
    )
    crowd_api_base: str = field(
        default_factory=lambda: os.environ.get("CROWD_API_BASE", "")
    )

    # Optional commercial feed
    commercial_feed_key: str = field(
        default_factory=lambda: os.environ.get("COMMERCIAL_FEED_KEY", "")
    )
    commercial_feed_base: str = field(
        default_factory=lambda: os.environ.get("COMMERCIAL_FEED_BASE", "")
    )

    # Kalshi prediction markets (RSA-PSS auth)
    kalshi_key_id: str = field(
        default_factory=lambda: os.environ.get("KALSHI_KEY_ID", "")
    )
    kalshi_private_key_path: str = field(
        default_factory=lambda: os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    )
    kalshi_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "KALSHI_API_BASE", "https://api.elections.kalshi.com"
        )
    )
    kalshi_ws_url: str = field(
        default_factory=lambda: os.environ.get(
            "KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2"
        )
    )
    kalshi_use_websocket: bool = field(
        default_factory=lambda: os.environ.get("KALSHI_USE_WEBSOCKET", "true").lower() == "true"
    )
    kalshi_poll_interval: int = field(
        default_factory=lambda: int(os.environ.get("KALSHI_POLL_INTERVAL", "45"))
    )

    # Kalshi energy series tickers to track
    kalshi_energy_series: tuple[str, ...] = ("KXAAAGASM", "KXWTI", "KXWTIW")
    polymarket_api_token: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_API_TOKEN", "")
    )
    polymarket_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "POLYMARKET_API_BASE", "https://clob.polymarket.com"
        )
    )

    # Google Maps (for reverse geocoding truck stop addresses)
    google_maps_api_key: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_MAPS_API_KEY", "")
    )

    # Nominatim (fallback geocoder)
    nominatim_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "NOMINATIM_USER_AGENT", "TankTok/1.0 (fuel-price-bot)"
        )
    )
    nominatim_rate_limit: float = 1.0  # seconds between requests

    # Cache TTLs (seconds)
    cache_geocode_ttl: int = 30 * 24 * 3600  # 30 days
    cache_poi_ttl: int = 24 * 3600            # 24 hours
    cache_retail_ttl: int = 6 * 3600          # 6 hours
    cache_market_ttl: int = 300               # 5 minutes

    # Truck stop search radius
    poi_radius: int = 80467  # 50 miles in meters (legacy compat)
    poi_radius_mi: int = 50  # 50 miles

    # Database path
    db_path: str = field(
        default_factory=lambda: os.environ.get("TANKTOK_DB_PATH", "tanktok_cache.db")
    )


def load_config() -> Config:
    return Config()
