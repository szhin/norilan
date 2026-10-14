"""Đường dẫn dữ liệu của app. Không hardcode: lấy từ %APPDATA% hoặc biến môi trường."""
from __future__ import annotations

import os
from pathlib import Path

from app import APP_NAME

ENV_DATA_DIR = "NORILAN_DATA_DIR"


def data_dir() -> Path:
    """Thư mục dữ liệu gốc, tự tạo nếu chưa có.

    Thứ tự ưu tiên: $NORILAN_DATA_DIR → %APPDATA%/Norilan → ~/.norilan
    (nhánh cuối chỉ để dev trên macOS/Linux).
    """
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        base = Path(override)
    elif appdata := os.environ.get("APPDATA"):
        base = Path(appdata) / APP_NAME
    else:
        base = Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _subdir(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "norilan.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def log_dir() -> Path:
    return _subdir("logs")


def attachments_dir() -> Path:
    """Nơi chứa file khi người dùng chọn 'copy vào thư mục app'."""
    return _subdir("attachments")


def backups_dir() -> Path:
    return _subdir("backups")


def backgrounds_dir() -> Path:
    """Ảnh nền người dùng chọn (đã chép vào app, thu nhỏ nếu quá lớn)."""
    return _subdir("backgrounds")
