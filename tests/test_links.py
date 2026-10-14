from __future__ import annotations

from datetime import date

import pytest

from app.links import day_url, item_url, parse


def test_roundtrip():
    assert parse(item_url(42)) == ("item", 42)
    assert parse(day_url(date(2026, 10, 15))) == ("day", date(2026, 10, 15))


@pytest.mark.parametrize("url", ["norilan://item/5/", '"norilan://item/5"', "NORILAN://Item/5"])
def test_tolerates_windows_variations(url):
    assert parse(url) == ("item", 5)


@pytest.mark.parametrize("url", ["", "https://item/5", "norilan://item/abc", "norilan://day/2026-13-01",
                                 "norilan://item", "norilan://other/5", "norilan://item/5/extra"])
def test_invalid(url):
    assert parse(url) is None
