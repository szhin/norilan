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
    # v4 – Deadline → Deadline, thêm Study time (kind 'study') liên kết tuỳ chọn tới Deadline.
    # CHECK của `kind` không sửa được tại chỗ → dựng lại bảng. migrate() tắt foreign_keys quanh bước này
    # (nếu không, DROP TABLE items sẽ xoá dây chuyền mọi đính kèm).
    """
    CREATE TABLE items_new (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        kind                  TEXT    NOT NULL CHECK (kind IN ('deadline', 'event', 'study')),
        title                 TEXT    NOT NULL,
        notes                 TEXT    NOT NULL DEFAULT '',
        starts_at             TEXT    NOT NULL,          -- deadline: hạn nộp; event/study: giờ bắt đầu
        ends_at               TEXT,                      -- event, study
        location              TEXT    NOT NULL DEFAULT '',
        status                TEXT    CHECK (status IN ('todo', 'in_progress', 'done')),
        priority              INTEGER NOT NULL DEFAULT 1,
        subject               TEXT    NOT NULL DEFAULT '',
        remind_before_minutes INTEGER CHECK (remind_before_minutes >= 0),
        reminder_sent_at      TEXT,
        open_file_on_click    INTEGER NOT NULL DEFAULT 0,
        created_at            TEXT    NOT NULL,
        updated_at            TEXT    NOT NULL,
        color                 TEXT,
        completed_at          TEXT,
        deadline_id           INTEGER REFERENCES items_new(id) ON DELETE SET NULL,  -- chỉ study
        CHECK ((kind = 'deadline' AND status IS NOT NULL)
            OR (kind = 'event' AND ends_at IS NOT NULL)
            OR (kind = 'study' AND ends_at IS NOT NULL)),
        CHECK (deadline_id IS NULL OR kind = 'study')
    );
    INSERT INTO items_new (id, kind, title, notes, starts_at, ends_at, location, status, priority, subject,
                           remind_before_minutes, reminder_sent_at, open_file_on_click, created_at,
                           updated_at, color, completed_at)
        SELECT id, CASE kind WHEN 'task' THEN 'deadline' ELSE kind END, title, notes, starts_at, ends_at,
               location, status, priority, subject, remind_before_minutes, reminder_sent_at,
               open_file_on_click, created_at, updated_at, color, completed_at
        FROM items;
    DROP TABLE items;
    ALTER TABLE items_new RENAME TO items;
    CREATE INDEX idx_items_starts_at   ON items(starts_at);
    CREATE INDEX idx_items_kind_status ON items(kind, status);
    CREATE INDEX idx_items_completed   ON items(kind, status, completed_at);
    CREATE INDEX idx_items_deadline    ON items(deadline_id);
    """,
    # v5 – cờ "đã gửi toast sắp hết giờ" của Study time (tách khỏi reminder_sent_at: đó là nhắc trước GIỜ BẮT ĐẦU)
    """
    ALTER TABLE items ADD COLUMN end_warned_at TEXT;
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
    """Mở DB, bật foreign keys, đăng ký hàm fold(); tự sao lưu rồi chạy migration còn thiếu."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("fold", 1, fold_text, deterministic=True)
    _backup_before_migrate(conn, path)
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


PRE_MIGRATE_PREFIX = "truoc-khi-nang-cap"
PRE_MIGRATE_KEPT = 5


def _backup_before_migrate(conn: sqlite3.Connection, path: Path | str) -> Path | None:
    """DB đã có dữ liệu mà sắp được nâng schema → ghi một bản sao lưu .zip vào <data>/backups trước.

    Dùng lại backup.create_backup. DB mới tinh (version 0) và DB trong bộ nhớ không cần sao lưu.
    """
    current = schema_version(conn)
    if str(path) == ":memory:" or current == 0 or current >= SCHEMA_VERSION:
        return None
    from datetime import datetime

    from app.backup import create_backup, prune_backups  # nhập muộn: backup.py nhập db.py

    data_dir = Path(path).resolve().parent
    now = datetime.now().replace(microsecond=0)
    dest = data_dir / "backups" / f"{PRE_MIGRATE_PREFIX}-v{current}-{now:%Y%m%d-%H%M%S}.zip"
    create_backup(conn, dest, data_dir=data_dir, now=now)
    prune_backups(dest.parent, keep=PRE_MIGRATE_KEPT, pattern=f"{PRE_MIGRATE_PREFIX}-*.zip")
    log.info("Đã sao lưu trước khi nâng database v%d → v%d: %s", current, SCHEMA_VERSION, dest)
    return dest


def migrate(conn: sqlite3.Connection) -> None:
    """Áp dụng các migration còn thiếu, mỗi bước trong một transaction (idempotent theo user_version).

    foreign_keys được tắt trong lúc migrate (SQLite chỉ cho đổi PRAGMA này ngoài transaction) để các bước
    dựng lại bảng không xoá dây chuyền dữ liệu con; mỗi bước phải qua `foreign_key_check` mới được commit.
    """
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database ở phiên bản {current}, mới hơn app (hỗ trợ {SCHEMA_VERSION}). Hãy cập nhật app."
        )
    if current == SCHEMA_VERSION:
        return
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for version in range(current, SCHEMA_VERSION):
            log.info("Migrating database v%d → v%d", version, version + 1)
            try:
                conn.executescript(f"BEGIN;\n{MIGRATIONS[version]}")
                if broken := conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise sqlite3.IntegrityError(f"foreign_key_check: {len(broken)} dòng vi phạm")
                conn.execute(f"PRAGMA user_version = {version + 1}")
                conn.execute("COMMIT")
            except sqlite3.Error:
                conn.rollback()
                log.exception("Migration v%d thất bại", version + 1)
                raise
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")
