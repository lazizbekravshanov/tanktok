"""SQLite-backed cache with TTL support."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return {"__dataclass__": type(o).__qualname__, **asdict(o)}
        if isinstance(o, datetime):
            return {"__datetime__": o.isoformat()}
        return super().default(o)


_DC_REGISTRY: dict = {}


def _get_dc_registry() -> dict:
    """Lazy-load dataclass registry to avoid circular imports."""
    if not _DC_REGISTRY:
        from app.providers.base import (
            ForecastResult,
            GeoLocation,
            MarketQuote,
            PredictionContract,
            RetailPrices,
            Station,
        )
        _DC_REGISTRY.update({
            "GeoLocation": GeoLocation,
            "MarketQuote": MarketQuote,
            "RetailPrices": RetailPrices,
            "PredictionContract": PredictionContract,
            "ForecastResult": ForecastResult,
            "Station": Station,
        })
    return _DC_REGISTRY


def _decode_hook(d: dict) -> Any:
    if "__datetime__" in d:
        return datetime.fromisoformat(d["__datetime__"])
    if "__dataclass__" in d:
        cls_name = d.pop("__dataclass__")
        registry = _get_dc_registry()
        cls = registry.get(cls_name)
        if cls:
            try:
                return cls(**d)
            except TypeError:
                return d
        return d
    return d


class Cache:
    def __init__(self, db_path: str = "tanktok_cache.db") -> None:
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        conn.commit()

    def get(self, key: str) -> Optional[Any]:
        conn = self._conn()
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row[1] < time.time():
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None
        try:
            return json.loads(row[0], object_hook=_decode_hook)
        except Exception:
            logger.exception("Cache decode error for key %s", key)
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        conn = self._conn()
        expires_at = time.time() + ttl
        encoded = json.dumps(value, cls=_Encoder)
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, encoded, expires_at),
        )
        conn.commit()

    def clear_expired(self) -> int:
        conn = self._conn()
        cur = conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
        conn.commit()
        return cur.rowcount

    def flush(self) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM cache")
        conn.commit()
