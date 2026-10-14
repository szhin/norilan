"""Sao lưu / khôi phục (không Qt): database + cài đặt + bản sao đính kèm + ảnh nền trong một file .zip."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from app import APP_NAME, __version__
from app.db import SCHEMA_VERSION, migrate, schema_version

log = logging.getLogger(__name__)

BACKUP_FORMAT = 1
MANIFEST = "manifest.json"
DB_NAME = "norilan.db"
SETTINGS_NAME = "settings.json"
FOLDERS = ("attachments", "backgrounds")


class BackupError(Exception):
    """File sao lưu không dùng được – message tiếng Việt để hiện cho người dùng."""


def default_backup_name(now: datetime) -> str:
    return f"{APP_NAME}-saoluu-{now:%Y-%m-%d_%H%M}.zip"


# ------------------------------------------------------------------ Sao lưu
def create_backup(conn: sqlite3.Connection, dest: Path, *, data_dir: Path, now: datetime) -> Path:
    """Ghi file sao lưu. Dùng SQLite backup API nên an toàn kể cả khi app đang chạy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="norilan-backup-") as tmp:
        db_copy = Path(tmp) / DB_NAME
        target = sqlite3.connect(db_copy)
        try:
            conn.backup(target)
        finally:
            target.close()
        manifest = {
            "app": APP_NAME,
            "backup_format": BACKUP_FORMAT,
            "app_version": __version__,
            "schema_version": schema_version(conn),
            "created_at": now.isoformat(timespec="seconds"),
            "data_dir": str(data_dir),
        }
        partial = dest.with_name(dest.name + ".tmp")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.write(db_copy, DB_NAME)
            settings = data_dir / SETTINGS_NAME
            if settings.is_file():
                archive.write(settings, SETTINGS_NAME)
            for folder in FOLDERS:
                root = data_dir / folder
                if root.is_dir():
                    for file in sorted(root.rglob("*")):
                        if file.is_file():
                            archive.write(file, f"{folder}/{file.relative_to(root).as_posix()}")
        os.replace(partial, dest)  # chỉ xuất hiện khi đã ghi xong
    log.info("Backup written to %s", dest)
    return dest


def prune_backups(folder: Path, keep: int = 5, pattern: str = "*.zip") -> None:
    """Giữ `keep` bản mới nhất (dùng cho bản tự sao lưu trước khi khôi phục)."""
    files = sorted(folder.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            log.warning("Không xoá được bản sao lưu cũ %s", old)


# ------------------------------------------------------------------ Khôi phục
def _safe_member(name: str) -> bool:
    """Chặn 'zip slip': chỉ nhận đường dẫn tương đối nằm trong các mục đã biết."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        return False
    return name in (MANIFEST, DB_NAME, SETTINGS_NAME) or (len(path.parts) >= 2 and path.parts[0] in FOLDERS)


def read_manifest(source: Path) -> dict:
    """Kiểm tra nhanh file sao lưu và trả về thông tin của nó."""
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if MANIFEST not in names or DB_NAME not in names:
                raise BackupError("File này không phải bản sao lưu của Norilan.")
            manifest = json.loads(archive.read(MANIFEST))
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError("Không đọc được file sao lưu (file hỏng hoặc không phải .zip).") from exc
    except ValueError as exc:
        raise BackupError("Thông tin trong file sao lưu bị hỏng.") from exc
    if not isinstance(manifest, dict) or manifest.get("app") != APP_NAME:
        raise BackupError("File này không phải bản sao lưu của Norilan.")
    if int(manifest.get("schema_version", 0)) > SCHEMA_VERSION:
        raise BackupError("Bản sao lưu được tạo bởi phiên bản Norilan mới hơn. Hãy cập nhật Norilan trước.")
    return manifest


def _rewrite_paths(conn: sqlite3.Connection, old_root: str, new_root: Path) -> int:
    """Bản sao đính kèm lưu đường dẫn tuyệt đối → đổi sang thư mục dữ liệu trên máy hiện tại."""
    if not old_root:
        return 0
    old_prefix = os.path.normcase(os.path.join(old_root, "attachments")) + os.sep
    old_prefix_alt = old_prefix.replace("\\", "/")
    changed = 0
    rows = conn.execute("SELECT id, target FROM attachments").fetchall()
    with conn:
        for row_id, target in rows:
            norm = os.path.normcase(target)
            for prefix in (old_prefix, old_prefix_alt):
                if norm.startswith(prefix):
                    rest = target[len(prefix):].replace("\\", "/")
                    new_target = str(new_root / "attachments" / Path(*rest.split("/")))
                    conn.execute("UPDATE attachments SET target = ? WHERE id = ?", (new_target, row_id))
                    changed += 1
                    break
    return changed


def restore_backup(conn: sqlite3.Connection, source: Path, *, data_dir: Path) -> dict:
    """Thay toàn bộ dữ liệu hiện tại bằng bản sao lưu. Ghi đè ngay trên kết nối đang mở."""
    manifest = read_manifest(source)
    with tempfile.TemporaryDirectory(prefix="norilan-restore-") as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if not _safe_member(member.filename):
                    log.warning("Bỏ qua mục lạ trong bản sao lưu: %r", member.filename)
                    continue
                archive.extract(member, tmp_dir)

        restored = sqlite3.connect(tmp_dir / DB_NAME)
        try:
            if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Database trong bản sao lưu bị hỏng.")
            restored.backup(conn)  # ghi đè database đang dùng
        except sqlite3.DatabaseError as exc:
            raise BackupError("Database trong bản sao lưu bị hỏng.") from exc
        finally:
            restored.close()
        migrate(conn)  # bản sao lưu cũ hơn → nâng cấp schema

        for folder in FOLDERS:
            target = data_dir / folder
            if target.exists():
                shutil.rmtree(target)
            if (tmp_dir / folder).is_dir():
                shutil.copytree(tmp_dir / folder, target)
            else:
                target.mkdir(parents=True)
        if (tmp_dir / SETTINGS_NAME).is_file():
            shutil.copy2(tmp_dir / SETTINGS_NAME, data_dir / SETTINGS_NAME)

    moved = _rewrite_paths(conn, manifest.get("data_dir", ""), data_dir)
    log.info("Restored backup from %s (%d attachment paths updated)", source, moved)
    return manifest
