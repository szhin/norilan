"""Cài đặt người dùng, lưu JSON. File hỏng ⇒ dùng mặc định, không crash."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import time
from pathlib import Path

from app.colors import ACCENTS, COLOR_BY, is_hex
from app.i18n import DEFAULT_LANGUAGE, LANGUAGES, tr

log = logging.getLogger(__name__)

THEMES = ("light", "dark", "system")
VIEWS = ("month", "week", "day")
AUTO_DELETE_CHOICES = (0, 7, 14, 30, 90)   # 0 = tắt


def theme_label(theme: str) -> str:
    return tr(f"theme.{theme}")


@dataclass(slots=True)
class Settings:
    daily_digest_enabled: bool = True
    daily_digest_time: str = "20:00"  # HH:MM, giờ gửi toast "Ngày mai bạn có N việc"
    theme: str = "light"
    language: str = DEFAULT_LANGUAGE  # một trong i18n.LANGUAGES
    accent: str = "blue"              # key trong colors.ACCENTS hoặc mã #RRGGBB
    color_by: str = "subject"         # tô màu lịch theo 'subject' (môn học) hoặc 'type' (loại mục)
    default_view: str = "month"
    autostart: bool = False
    copy_attachments_to_app_dir: bool = False
    minimize_to_tray: bool = True
    tray_hint_shown: bool = False     # đã báo 'Norilan vẫn chạy ở khay' lần đầu chưa
    background: str = ""              # tên file ảnh nền trong thư mục backgrounds của app ('' = không dùng)
    background_visibility: int = 40   # 10–90: ảnh nền hiện rõ tới mức nào (các ô lịch trong suốt theo)
    background_blur: bool = False
    auto_delete_done_days: int = 0    # tự xoá việc đã xong sau N ngày; 0 = tắt

    @property
    def digest_time(self) -> time:
        return time.fromisoformat(self.daily_digest_time)

    def validate(self) -> None:
        try:
            time.fromisoformat(self.daily_digest_time)
        except ValueError as exc:
            raise ValueError(f"Giờ nhắc không hợp lệ: {self.daily_digest_time!r}") from exc
        if self.theme not in THEMES:
            raise ValueError(f"Theme không hợp lệ: {self.theme!r}")
        if self.language not in LANGUAGES:
            raise ValueError(f"Ngôn ngữ không hợp lệ: {self.language!r}")
        if self.default_view not in VIEWS:
            raise ValueError(f"Chế độ xem không hợp lệ: {self.default_view!r}")
        if self.accent not in ACCENTS and not is_hex(self.accent):
            raise ValueError(f"Màu nhấn không hợp lệ: {self.accent!r}")
        if self.color_by not in COLOR_BY:
            raise ValueError(f"Kiểu tô màu không hợp lệ: {self.color_by!r}")
        if not 10 <= int(self.background_visibility) <= 90:
            raise ValueError("Độ hiện rõ ảnh nền phải từ 10 đến 90")
        if any(sep in self.background for sep in ("/", "\\", "..")):
            raise ValueError("Tên ảnh nền không hợp lệ")
        if not 0 <= int(self.auto_delete_done_days) <= 3650:
            raise ValueError("Số ngày tự xoá không hợp lệ")


def load_settings(path: Path) -> Settings:
    """Đọc settings; bỏ qua khoá lạ. Lỗi ⇒ giữ file hỏng thành .bak và trả về mặc định."""
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings.json phải là một object")
        known = {f.name for f in fields(Settings)}
        settings = Settings(**{k: v for k, v in raw.items() if k in known})
        settings.validate()
        return settings
    except (OSError, ValueError, TypeError) as exc:
        log.warning("settings.json lỗi (%s) – dùng cài đặt mặc định", exc)
        try:
            os.replace(path, path.with_suffix(".json.bak"))
        except OSError:
            log.exception("Không thể sao lưu settings.json hỏng")
        return Settings()


def save_settings(settings: Settings, path: Path) -> None:
    """Ghi atomic: ghi file tạm rồi os.replace để không bao giờ để lại file dở dang."""
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
