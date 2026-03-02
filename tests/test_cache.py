"""Tests for the SQLite cache."""

import os
import tempfile
import time

import pytest

from app.storage.cache import Cache


@pytest.fixture
def cache(tmp_path):
    db_path = str(tmp_path / "test_cache.db")
    return Cache(db_path=db_path)


class TestCache:
    def test_set_and_get(self, cache):
        cache.set("key1", {"hello": "world"}, ttl=60)
        assert cache.get("key1") == {"hello": "world"}

    def test_missing_key(self, cache):
        assert cache.get("nonexistent") is None

    def test_expired_key(self, cache):
        cache.set("key2", "value", ttl=0)
        time.sleep(0.05)
        assert cache.get("key2") is None

    def test_overwrite(self, cache):
        cache.set("key3", "a", ttl=60)
        cache.set("key3", "b", ttl=60)
        assert cache.get("key3") == "b"

    def test_flush(self, cache):
        cache.set("k1", 1, ttl=60)
        cache.set("k2", 2, ttl=60)
        cache.flush()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_clear_expired(self, cache):
        cache.set("alive", "yes", ttl=3600)
        cache.set("dead", "no", ttl=0)
        time.sleep(0.05)
        removed = cache.clear_expired()
        assert removed >= 1
        assert cache.get("alive") == "yes"

    def test_complex_value(self, cache):
        data = {"list": [1, 2, 3], "nested": {"a": True}}
        cache.set("complex", data, ttl=60)
        assert cache.get("complex") == data
