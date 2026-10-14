"""Kết nối SQLite, schema và migration theo PRAGMA user_version."""
from __future__ import annotations

import logging
import sqlite3
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

# Mỗi phần tử là một migration; index i nâng DB từ version i lên i+1.
# KHÔNG sửa migration đã phát hành – chỉ thêm phần tử mới ở cuối.
MIGRATIONS: list[str] = [
    # v1 – schema khởi tạo
    """
    CREATE TABLE items (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        kind                  TEXT    NOT NULL CHECK (kind IN ('task', 'event')),
        title                 TEXT    NOT NULL,
        notes                 TEXT    NOT NULL DEFAULT '',
        starts_at             TEXT    NOT NULL,          -- task: hạn nộp; event: giờ bắt đầu
        ends_at               TEXT,                      -- chỉ event
        location              TEXT    NOT NULL DEFAULT '',
        status                TEXT    CHECK (status IN ('todo', 'in_progress', 'done')),
        priority              INTEGER NOT NULL DEFAULT 1,
        subject               TEXT    NOT NULL DEFAULT '',
        remind_before_minutes INTEGER CHECK (remind_before_minutes >= 0),
        reminder_sent_at      TEXT,
        open_file_on_click    INTEGER NOT NULL DEFAULT 0,  -- không còn dùng từ v0.3.1, giữ để tương thích
        created_at            TEXT    NOT NULL,
        updated_at            TEXT    NOT NULL,
        CHECK ((kind = 'task' AND status IS NOT NULL) OR (kind = 'event' AND ends_at IS NOT NULL))
    );
    CREATE INDEX idx_items_starts_at   ON items(starts_at);
    CREATE INDEX idx_items_kind_status ON items(kind, status);

    CREATE TABLE attachments (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        kind         TEXT    NOT NULL CHECK (kind IN ('file', 'folder', 'url')),
        target       TEXT    NOT NULL,
        display_name TEXT    NOT NULL DEFAULT '',
        position     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT    NOT NULL
    );
    CREATE INDEX idx_attachments_item ON attachments(item_id, position);
    """,
    # v2 – màu sắc và trạng thái app
    """
    ALTER TABLE items ADD COLUMN color TEXT;           -- màu riêng của event (tên tone hoặc #RRGGBB)
    CREATE TABLE subject_colors (
        subject TEXT PRIMARY KEY,
        color   TEXT NOT NULL
    );
    CREATE TABLE app_state (                         -- vd. ngày đã gửi toast tổng hợp
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # v3 – thời điểm hoàn thành (để tự xoá việc đã xong sau N ngày)
    """
    ALTER TABLE items ADD COLUMN completed_at TEXT;
    -- Việc đã xong từ trước chưa có mốc chính xác → tạm lấy lần sửa cuối.
    UPDATE items SET completed_at = updated_at WHERE kind = 'task' AND status = 'done';
    CREATE INDEX idx_items_completed ON items(kind, status, completed_at);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def fold_text(value: str | None) -> str:
    """Chuẩn hoá để tìm kiếm: bỏ dấu tiếng Việt, đ→d, chữ thường. 'Toán Đại' → 'toan dai'."""
    if not value:
        return ""
    text = value.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def connect(path: Path | str) -> sqlite3.Connection:
    """Mở DB, bật foreign keys, đăng ký hàm fold() và chạy migration còn thiếu."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("fold", 1, fold_text, deterministic=True)
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    """Áp dụng các migration còn thiếu, mỗi bước trong một transaction."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database ở phiên bản {current}, mới hơn app (hỗ trợ {SCHEMA_VERSION}). Hãy cập nhật app."
        )
    for version in range(current, SCHEMA_VERSION):
        log.info("Migrating database v%d → v%d", version, version + 1)
        script = f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.rollback()
            log.exception("Migration v%d thất bại", version + 1)
            raise
