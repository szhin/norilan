from __future__ import annotations

import sqlite3

import pytest

from app import db
from app.db import SCHEMA_VERSION, connect, fold_text, schema_version


def test_schema_created_and_versioned(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"items", "attachments"} <= tables
    assert schema_version(conn) == SCHEMA_VERSION


def test_reopen_keeps_data_and_does_not_remigrate(tmp_path):
    path = tmp_path / "x.db"
    c1 = connect(path)
    c1.execute(
        "INSERT INTO items (kind,title,starts_at,status,created_at,updated_at) "
        "VALUES ('task','A','2026-10-15T09:00:00','todo','t','t')"
    )
    c1.commit()
    c1.close()
    c2 = connect(path)
    assert c2.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    c2.close()


def test_refuses_newer_database(tmp_path):
    path = tmp_path / "future.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    raw.close()
    with pytest.raises(RuntimeError, match="mới hơn app"):
        connect(path)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    good_versions = len(db.MIGRATIONS)
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, "CREATE TABLE ok_table(x); THIS IS NOT SQL;"])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db.MIGRATIONS))
    path = tmp_path / "m.db"
    with pytest.raises(sqlite3.Error):
        connect(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == good_versions
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    assert "ok_table" not in names
    raw.close()


def test_check_constraint_event_requires_end(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO items (kind,title,starts_at,created_at,updated_at) "
            "VALUES ('event','E','2026-10-15T09:00:00','t','t')"
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Toán Cao Cấp", "toan cao cap"), ("Đại số", "dai so"), ("Tiếng Việt", "tieng viet"), ("", ""), (None, "")],
)
def test_fold_text(raw, expected):
    assert fold_text(raw) == expected


def test_upgrade_v1_database_keeps_data(tmp_path):
    """DB tạo từ GĐ1–3 (schema v1) nâng lên bản mới mà không mất dữ liệu."""
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.MIGRATIONS[0] + "\nPRAGMA user_version = 1;")
    raw.execute(
        "INSERT INTO items (kind,title,starts_at,ends_at,created_at,updated_at) "
        "VALUES ('event','Họp cũ','2026-10-15T09:00:00','2026-10-15T10:00:00','t','t')"
    )
    raw.commit()
    raw.close()

    conn = connect(path)
    assert schema_version(conn) == SCHEMA_VERSION
    row = conn.execute("SELECT title, color FROM items").fetchone()
    assert tuple(row) == ("Họp cũ", None)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"subject_colors", "app_state"} <= tables
    conn.close()
