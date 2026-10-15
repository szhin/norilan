"""Đa ngôn ngữ (không Qt): bảng dịch, `tr()` và ngôn ngữ đang dùng. Tiếng Việt là ngôn ngữ gốc.

    tr("sidebar.settings")                    → chuỗi theo ngôn ngữ hiện tại
    tr("unit.day", count=2, n=2)              → dạng số nhiều ("1 day" / "2 days") nếu bảng dịch có
    tr_seq("weekday.short")                   → tuple (tên thứ, tên tháng…)

Mỗi bảng dịch là một dict `STRINGS` trong app/i18n/<mã>.py. Giá trị là:
  - str                      : chuỗi, có thể có {tham_số} (str.format; dùng được cả {d:%d/%m/%Y} cho ngày)
  - {"one": ..., "other": ...}: dạng số nhiều, chọn theo tham số `count` (chỉ en/es cần)
  - tuple[str, ...]          : danh sách (thứ trong tuần, tên tháng) – lấy bằng tr_seq()
Thiếu khoá ở ngôn ngữ đang dùng thì rơi về tiếng Việt, rồi về chính khoá (kèm cảnh báo trong log).
"""
from __future__ import annotations

import logging
from typing import Any

from app.i18n import en, es, ja, vi, zh

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "vi"
LANGUAGES: tuple[str, ...] = ("vi", "en", "es", "zh", "ja")
# Tên mỗi ngôn ngữ viết bằng chính ngôn ngữ đó: người dùng nhận ra dù giao diện đang ở thứ tiếng nào.
LANGUAGE_NAMES: dict[str, str] = {
    "vi": "Tiếng Việt", "en": "English", "es": "Español", "zh": "中文", "ja": "日本語",
}

_CATALOGS: dict[str, dict[str, Any]] = {
    "vi": vi.STRINGS, "en": en.STRINGS, "es": es.STRINGS, "zh": zh.STRINGS, "ja": ja.STRINGS,
}
_current = DEFAULT_LANGUAGE
_reported: set[str] = set()


def is_language(code: object) -> bool:
    return code in _CATALOGS


def current_language() -> str:
    return _current


def set_language(code: str) -> str:
    """Đổi ngôn ngữ đang dùng; mã lạ ⇒ giữ tiếng Việt. Trả về mã thực sự được áp dụng."""
    global _current
    _current = code if code in _CATALOGS else DEFAULT_LANGUAGE
    return _current


def _lookup(key: str) -> Any:
    for code in (_current, DEFAULT_LANGUAGE):
        if key in _CATALOGS[code]:
            return _CATALOGS[code][key]
    if key not in _reported:
        _reported.add(key)
        log.warning("Thiếu bản dịch cho khoá %r", key)
    return key


def tr(key: str, /, **params: Any) -> str:
    """Chuỗi đã dịch. `count=` chọn dạng số nhiều; mọi tham số còn lại điền vào {…}."""
    text = _lookup(key)
    if isinstance(text, dict):
        text = text["one"] if params.get("count") == 1 else text["other"]
    return text.format(**params) if params else text


def tr_seq(key: str) -> tuple[str, ...]:
    return tuple(_lookup(key))
