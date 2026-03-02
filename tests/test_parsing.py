"""Tests for input parsing (ZIP codes and city names)."""

import re

import pytest

from app.handlers import ZIP_RE


class TestZipParsing:
    def test_valid_5_digit_zip(self):
        assert ZIP_RE.match("45202")

    def test_valid_zip_plus4(self):
        assert ZIP_RE.match("45202-1234")

    def test_invalid_4_digits(self):
        assert ZIP_RE.match("4520") is None

    def test_invalid_6_digits(self):
        assert ZIP_RE.match("452021") is None

    def test_invalid_letters(self):
        assert ZIP_RE.match("4520a") is None

    def test_empty_string(self):
        assert ZIP_RE.match("") is None

    def test_zip_with_spaces_no_match(self):
        assert ZIP_RE.match("45202 ") is None  # trailing space

    def test_all_zeros(self):
        assert ZIP_RE.match("00000")

    def test_zip_plus4_bad_format(self):
        assert ZIP_RE.match("45202-12") is None


class TestCityParsing:
    """City input is anything that doesn't match ZIP_RE."""

    def test_city_state(self):
        assert not ZIP_RE.match("Cincinnati OH")

    def test_city_comma_state(self):
        assert not ZIP_RE.match("Miami, FL")

    def test_city_with_space_state(self):
        assert not ZIP_RE.match("Fort Mitchell KY")

    def test_city_only(self):
        assert not ZIP_RE.match("Chicago")

    def test_state_only(self):
        assert not ZIP_RE.match("Ohio")
