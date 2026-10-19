"""Màu sắc (không Qt): bảng màu kiểu Notion, màu gradient, màu tự gán cho môn học, màu nhấn của app."""
from __future__ import annotations

import re
import zlib
from collections import Counter
from collections.abc import Iterable

from app.db import fold_text
from app.i18n import tr
from app.models import CalendarItem, Deadline, Event, StudyTime

# tone → (nền chip, chữ chip) cho theme sáng / tối – theo bảng màu thẻ của Notion
LIGHT_TONES: dict[str, tuple[str, str]] = {
    "gray": ("#f1f1ef", "#5f5e5b"),
    "brown": ("#f4eeee", "#8a5a44"),
    "orange": ("#fbecdd", "#b5600a"),
    "yellow": ("#fbf3db", "#a17516"),
    "green": ("#edf3ec", "#3d7354"),
    "blue": ("#e7f3f8", "#2b6f97"),
    "purple": ("#f6f3f9", "#7d55a0"),
    "pink": ("#faf1f5", "#ad3f7a"),
    "red": ("#fdebec", "#c2403b"),
}
DARK_TONES: dict[str, tuple[str, str]] = {
    "gray": ("#373737", "#b4b4b0"),
    "brown": ("#4a3228", "#d1a08a"),
    "orange": ("#5c3b23", "#ec9a5e"),
    "yellow": ("#56452f", "#e3bb62"),
    "green": ("#243d30", "#6cc08f"),
    "blue": ("#143a4e", "#5db2e8"),
    "purple": ("#3c2d49", "#b88ae3"),
    "pink": ("#4e2c3c", "#e47aaf"),
    "red": ("#522e2a", "#f07a75"),
}
TONES: tuple[str, ...] = tuple(LIGHT_TONES)
# Màu tự gán cho môn học: bỏ xám (dành cho 'không có môn') và đỏ (dễ nhầm với 'quá hạn').
AUTO_TONES: tuple[str, ...] = ("blue", "green", "purple", "orange", "pink", "yellow", "brown")

DEFAULT_DEADLINE_TONE = "blue"
DEFAULT_EVENT_TONE = "orange"
DEFAULT_STUDY_TONE = "green"
NO_SUBJECT_TONE = "gray"
COLOR_BY = ("subject", "type")

# Màu nhấn (nút chính, ô hôm nay, viền khi chọn…): key → (màu trên nền sáng, màu trên nền tối)
ACCENTS: dict[str, tuple[str, str]] = {
    "blue": ("#2383e2", "#3d95ef"),
    "purple": ("#8a56c2", "#a679dc"),
    "pink": ("#c4407f", "#df6aa3"),
    "red": ("#d2423c", "#ec6b65"),
    "orange": ("#d9730d", "#ee8a2e"),
    "green": ("#2f8a5b", "#4cb07c"),
    "brown": ("#8f5f45", "#b98a6f"),
    "gray": ("#5f5e5b", "#9b9a97"),
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Màu gradient: 'gradient:#RRGGBB,#RRGGBB' (màu đầu, màu cuối). Dùng được ở mọi chỗ một tone dùng được
# (màu môn học, màu sự kiện) và lưu chung cột với tên tone. Mẫu giống hệt models._COLOR_RE.
GRADIENT_PREFIX = "gradient:"
_GRADIENT_RE = re.compile(r"^gradient:(#[0-9a-fA-F]{6}),(#[0-9a-fA-F]{6})$")
GRADIENT_PRESETS: tuple[tuple[str, str], ...] = (
    ("#ff9a5a", "#e0457b"), ("#36d1dc", "#5b86e5"), ("#8e2de2", "#f06292"),
    ("#56ab2f", "#a8e063"), ("#f6d365", "#fda085"), ("#4568dc", "#b06ab3"),
)


def tone_label(tone: str) -> str:
    return tr("tone.gradient") if is_gradient(tone) else tr(f"tone.{tone}")


def accent_label(accent: str) -> str:
    return tr(f"tone.{accent}")


def color_by_label(key: str) -> str:
    return tr(f"color_by.{key}")


def is_hex(value: str) -> bool:
    return bool(_HEX_RE.match(value))


def is_gradient(value: str | None) -> bool:
    return isinstance(value, str) and bool(_GRADIENT_RE.match(value))


def make_gradient(start: str, end: str) -> str:
    if not (is_hex(start) and is_hex(end)):
        raise ValueError(f"Màu gradient không hợp lệ: {start!r}, {end!r}")
    return f"{GRADIENT_PREFIX}{start.lower()},{end.lower()}"


def gradient_stops(value: str) -> tuple[str, str]:
    """(màu đầu, màu cuối) của một chuỗi gradient."""
    match = _GRADIENT_RE.match(value)
    if match is None:
        raise ValueError(f"Không phải màu gradient: {value!r}")
    return match.group(1).lower(), match.group(2).lower()


def _luminance(hex_color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_text_color(*backgrounds: str) -> str:
    """Chữ trắng hay chữ tối dễ đọc hơn trên các màu nền này (xét màu nền kém thuận lợi nhất)."""
    levels = [_luminance(c) for c in backgrounds]

    def worst_contrast(text: str) -> float:
        lum = _luminance(text)
        return min((max(lum, other) + 0.05) / (min(lum, other) + 0.05) for other in levels)

    return max(("#ffffff", "#1f1f1f"), key=worst_contrast)


def is_valid_tone(value: str | None) -> bool:
    return value in LIGHT_TONES or is_gradient(value)


def auto_tone(subject: str) -> str:
    """Màu cố định cho một môn (cùng tên → cùng màu, không phân biệt dấu/hoa thường)."""
    key = fold_text(subject).strip()
    if not key:
        return NO_SUBJECT_TONE
    return AUTO_TONES[zlib.crc32(key.encode("utf-8")) % len(AUTO_TONES)]


def pick_tone(used: Iterable[str]) -> str:
    """Màu cho môn mới: màu tự gán đang ít được dùng nhất (7 môn đầu luôn khác màu nhau)."""
    counts = Counter(t for t in used if t in AUTO_TONES)
    return min(AUTO_TONES, key=lambda tone: (counts[tone], AUTO_TONES.index(tone)))


def assign_missing_tones(subjects: list[str], existing: dict[str, str]) -> dict[str, str]:
    """Gán màu cho các môn chưa có màu lưu sẵn. Trả về {môn: màu} mới cần lưu."""
    used = list(existing.values())
    assigned: dict[str, str] = {}
    for subject in subjects:
        if subject.strip() and subject not in existing:
            tone = pick_tone(used)
            assigned[subject] = tone
            used.append(tone)
    return assigned


class ColorResolver:
    """Quyết định tone hiển thị của từng mục theo cài đặt 'tô màu theo'."""

    def __init__(self, color_by: str = "subject", subject_colors: dict[str, str] | None = None) -> None:
        self.color_by = color_by if color_by in COLOR_BY else "subject"
        self._subjects = {k: v for k, v in (subject_colors or {}).items() if is_valid_tone(v)}

    def subject_tone(self, subject: str) -> str:
        subject = subject.strip()
        if not subject:
            return NO_SUBJECT_TONE
        return self._subjects.get(subject) or auto_tone(subject)

    def tone(self, item: CalendarItem) -> str:
        if isinstance(item, Event):
            return item.color if is_valid_tone(item.color) else DEFAULT_EVENT_TONE
        if isinstance(item, StudyTime):
            if self.color_by == "type" or not item.subject.strip():
                return DEFAULT_STUDY_TONE
            return self.subject_tone(item.subject)
        assert isinstance(item, Deadline)
        if self.color_by == "type":
            return DEFAULT_DEADLINE_TONE
        return self.subject_tone(item.subject)


# ------------------------------------------------------------------ Màu nhấn
def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Pha màu: t=0 → a, t=1 → b."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t)
    )


def accent_color(accent: str, *, dark: bool) -> str:
    """Màu nhấn thực tế: key trong ACCENTS hoặc mã #RRGGBB tuỳ chỉnh."""
    if accent in ACCENTS:
        return ACCENTS[accent][1 if dark else 0]
    return accent if is_hex(accent) else ACCENTS["blue"][1 if dark else 0]


def accent_tokens(accent: str, *, dark: bool, bg: str) -> dict[str, str]:
    """Các token suy ra từ màu nhấn: màu hover và nền ô ngày đang chọn."""
    color = accent_color(accent, dark=dark)
    return {
        "accent": color,
        "accent_hover": mix(color, "#ffffff" if dark else "#000000", 0.15),
        "selected_cell": mix(bg, color, 0.16 if dark else 0.09),
    }
