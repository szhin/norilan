from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import paths
from app.settings import Settings, load_settings, save_settings
from app.timeutil import day_range, from_iso, to_iso


# ------------------------------------------------------------------ Settings
def test_settings_default_when_missing(tmp_path):
    s = load_settings(tmp_path / "settings.json")
    assert s == Settings()
    assert s.digest_time == time(20, 0)


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(Settings(daily_digest_time="21:30", theme="dark", autostart=True), path)
    loaded = load_settings(path)
    assert loaded.daily_digest_time == "21:30" and loaded.theme == "dark" and loaded.autostart
    assert not path.with_suffix(".json.tmp").exists()


def test_settings_ignores_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "dark", "from_future_version": 1}), encoding="utf-8")
    assert load_settings(path).theme == "dark"


@pytest.mark.parametrize("content", ["{không phải json", '["list"]', '{"daily_digest_time": "25:99"}'])
def test_settings_corrupt_falls_back_and_keeps_backup(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    assert load_settings(path) == Settings()
    assert path.with_suffix(".json.bak").read_text(encoding="utf-8") == content


def test_save_invalid_settings_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_settings(Settings(theme="neon"), tmp_path / "s.json")


# ------------------------------------------------------------------ Paths
def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"
    assert paths.db_path().parent == tmp_path / "custom"
    assert paths.log_dir().is_dir()


def test_data_dir_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Norilan"


# ------------------------------------------------------------------ Thời gian
def test_iso_roundtrip_drops_microseconds():
    value = datetime(2026, 10, 15, 23, 59, 30, 123456)
    assert to_iso(value) == "2026-10-15T23:59:30"
    assert from_iso(to_iso(value)) == value.replace(microsecond=0)


def test_aware_datetime_converted_to_local_naive():
    aware = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    result = from_iso(to_iso(aware))
    assert result.tzinfo is None
    assert result == aware.astimezone().replace(tzinfo=None)


def test_day_range():
    start, end = day_range(date(2026, 10, 15))
    assert start == datetime(2026, 10, 15) and end - start == timedelta(days=1)


def test_new_appearance_settings(tmp_path):
    path = tmp_path / "s.json"
    save_settings(Settings(theme="system", accent="#12ab34", color_by="type"), path)
    loaded = load_settings(path)
    assert (loaded.theme, loaded.accent, loaded.color_by) == ("system", "#12ab34", "type")


@pytest.mark.parametrize("bad", [{"accent": "neon"}, {"color_by": "mood"}, {"theme": "hồng"}])
def test_invalid_appearance_settings_rejected(bad):
    with pytest.raises(ValueError):
        Settings(**bad).validate()
