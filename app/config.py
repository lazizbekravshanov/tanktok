"""TankTok configuration — all secrets and tunables from env vars."""

import os
from dataclasses import dataclass, field


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

    # Optional prediction markets
    kalshi_api_token: str = field(
        default_factory=lambda: os.environ.get("KALSHI_API_TOKEN", "")
    )
    kalshi_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "KALSHI_API_BASE", "https://trading-api.kalshi.com"
        )
    )
    polymarket_api_token: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_API_TOKEN", "")
    )
    polymarket_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "POLYMARKET_API_BASE", "https://clob.polymarket.com"
        )
    )

    # Nominatim
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

    # POI search radius (meters)
    poi_radius: int = 25000  # 25 km

    # Database path
    db_path: str = field(
        default_factory=lambda: os.environ.get("TANKTOK_DB_PATH", "tanktok_cache.db")
    )


def load_config() -> Config:
    return Config()
