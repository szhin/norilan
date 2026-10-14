"""Tiện ích thời gian. Quy ước: mọi datetime trong app là giờ local *naive*, chính xác đến giây.

Lưu vào DB dạng ISO 8601 'YYYY-MM-DDTHH:MM:SS' để so sánh chuỗi cũng đúng thứ tự thời gian.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta


def now() -> datetime:
    """Thời điểm hiện tại theo giờ local, bỏ phần micro giây."""
    return datetime.now().replace(microsecond=0)


def to_local_naive(dt: datetime) -> datetime:
    """Chuẩn hoá: datetime có tz → đổi sang giờ local rồi bỏ tz; bỏ micro giây."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return to_local_naive(dt).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    return to_local_naive(datetime.fromisoformat(value))


def opt_from_iso(value: str | None) -> datetime | None:
    return from_iso(value) if value else None


def day_range(day: date) -> tuple[datetime, datetime]:
    """Khoảng [00:00 của ngày, 00:00 ngày hôm sau)."""
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)
