"""Logic lịch thuần Python (không Qt): lưới tháng/tuần, nhóm mục theo ngày, nhãn tiếng Việt."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.models import CalendarItem, Event, Priority, Task

WEEKDAY_SHORT = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
WEEKDAY_LONG = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")
_RELATIVE = {-1: "Hôm qua", 0: "Hôm nay", 1: "Ngày mai"}


class ViewMode(StrEnum):
    MONTH = "month"
    WEEK = "week"
    DAY = "day"

    @property
    def label(self) -> str:
        return {"month": "Tháng", "week": "Tuần", "day": "Ngày"}[self.value]


# ------------------------------------------------------------------ Lưới ngày
def add_months(day: date, months: int) -> date:
    """Cộng tháng, kẹp ngày về cuối tháng nếu cần (31/01 + 1 tháng = 28/02)."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_grid(year: int, month: int) -> list[date]:
    """42 ngày (6 tuần) bắt đầu từ Thứ Hai, chứa trọn tháng đã cho."""
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    return [start + timedelta(days=i) for i in range(42)]


def week_dates(day: date) -> list[date]:
    """7 ngày Thứ Hai → Chủ Nhật của tuần chứa `day`."""
    start = day - timedelta(days=day.weekday())
    return [start + timedelta(days=i) for i in range(7)]


def visible_days(view: ViewMode, selected: date) -> list[date]:
    if view == ViewMode.MONTH:
        return month_grid(selected.year, selected.month)
    if view == ViewMode.WEEK:
        return week_dates(selected)
    return [selected]


def shift(view: ViewMode, selected: date, step: int) -> date:
    """Lùi/tiến một kỳ (tháng/tuần/ngày) theo chế độ xem."""
    if view == ViewMode.MONTH:
        return add_months(selected, step)
    if view == ViewMode.WEEK:
        return selected + timedelta(weeks=step)
    return selected + timedelta(days=step)


# ------------------------------------------------------------------ Nhãn
def day_heading(day: date) -> str:
    """'Thứ Tư, 14/10/2026'."""
    return f"{WEEKDAY_LONG[day.weekday()]}, {day:%d/%m/%Y}"


def relative_label(day: date, today: date) -> str | None:
    """'Hôm qua' / 'Hôm nay' / 'Ngày mai', hoặc None."""
    return _RELATIVE.get((day - today).days)


def group_label(day: date, today: date) -> str:
    """Tiêu đề nhóm trong danh sách: 'Ngày mai · Thứ Tư, 14/10/2026'."""
    rel = relative_label(day, today)
    return f"{rel} · {day_heading(day)}" if rel else day_heading(day)


def period_title(view: ViewMode, selected: date) -> str:
    if view == ViewMode.MONTH:
        return f"Tháng {selected.month}, {selected.year}"
    if view == ViewMode.WEEK:
        first, last = week_dates(selected)[0], week_dates(selected)[-1]
        if first.year != last.year:
            return f"{first:%d/%m/%Y} – {last:%d/%m/%Y}"
        return f"{first:%d/%m} – {last:%d/%m/%Y}"
    return day_heading(selected)


def format_minutes(minutes: int | None) -> str:
    """60 → '1 giờ', 1530 → '1 ngày 1 giờ 30 phút', 0 → 'Đúng giờ', None → 'Không nhắc'."""
    if minutes is None:
        return "Không nhắc"
    if minutes == 0:
        return "Đúng giờ"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{n} {unit}" for n, unit in ((days, "ngày"), (hours, "giờ"), (mins, "phút")) if n]
    return " ".join(parts)


# ------------------------------------------------------------------ Mục trên lịch
def item_last_day(item: CalendarItem) -> date:
    """Ngày cuối cùng mục còn hiển thị. Event kết thúc đúng 00:00 không chiếm ngày hôm sau."""
    if isinstance(item, Event) and item.ends_at > item.starts_at:
        return (item.ends_at - timedelta(seconds=1)).date()
    return item.starts_at.date()


def items_by_day(items: list[CalendarItem], days: list[date]) -> dict[date, list[CalendarItem]]:
    """Gán mỗi mục vào mọi ngày nó chiếm trong `days` (event qua đêm xuất hiện ở nhiều ngày).

    Giữ thứ tự đầu vào (repository đã sắp theo thời gian).
    """
    result: dict[date, list[CalendarItem]] = {d: [] for d in days}
    if not days:
        return result
    first, last = days[0], days[-1]
    for item in items:
        current = max(item.starts_at.date(), first)
        end = min(item_last_day(item), last)
        while current <= end:
            if current in result:
                result[current].append(item)
            current += timedelta(days=1)
    return result


def split_overflow(items: list[CalendarItem], limit: int | None) -> tuple[list[CalendarItem], int]:
    """Cắt danh sách cho ô lịch: (mục hiển thị, số mục bị ẩn → '+N')."""
    if limit is None or len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def chip_text(item: CalendarItem, day: date) -> str:
    """Chữ trên chip: '09:00 Đọc chương 3'; ngày tiếp theo của event qua đêm: '↳ Họp'."""
    if item.starts_at.date() == day:
        return f"{item.starts_at:%H:%M} {item.title}"
    return f"↳ {item.title}"


def chip_state(item: CalendarItem, now: datetime) -> str:
    """Trạng thái hiển thị: 'done' | 'overdue' | 'high' | 'normal'."""
    if isinstance(item, Task):
        if item.is_done:
            return "done"
        if item.is_overdue(now):
            return "overdue"
        if item.priority == Priority.HIGH:
            return "high"
    return "normal"


def time_text(item: CalendarItem, *, with_date: bool = False) -> str:
    """'Hạn 23:59' | '19:00 – 20:30' | '23:00 13/10 – 01:00 14/10'."""
    if isinstance(item, Task):
        return f"Hạn {item.due_at:%H:%M}" + (f" {item.due_at:%d/%m}" if with_date else "")
    if item.starts_at.date() != item.ends_at.date():
        return f"{item.starts_at:%H:%M %d/%m} – {item.ends_at:%H:%M %d/%m}"
    text = f"{item.starts_at:%H:%M} – {item.ends_at:%H:%M}"
    return text + (f" {item.starts_at:%d/%m}" if with_date else "")


def item_meta(item: CalendarItem, now: datetime, *, with_date: bool = False, show_subject: bool = True) -> str:
    """Dòng phụ trên thẻ: 'Hạn 23:59 · Toán · Chưa làm · 📎1' hoặc '19:00 – 20:30 · 📍 Thư viện'."""
    parts = [time_text(item, with_date=with_date)]
    if isinstance(item, Task):
        if item.subject and show_subject:
            parts.append(item.subject)
        parts.append("Quá hạn" if item.is_overdue(now) else item.status.label)
        if item.attachments:
            parts.append(f"📎{len(item.attachments)}")
    elif item.location:
        parts.append(f"📍 {item.location}")
    return " · ".join(parts)


# ------------------------------------------------------------------ Nhắc trước
REMIND_UNITS: tuple[tuple[str, int], ...] = (("phút", 1), ("giờ", 60), ("ngày", 1440))


def split_minutes(minutes: int) -> tuple[int, int]:
    """Tách số phút thành (giá trị, đơn vị lớn nhất chia hết): 120 → (2, 60), 2880 → (2, 1440)."""
    for _, unit in reversed(REMIND_UNITS):
        if minutes > 0 and minutes % unit == 0:
            return minutes // unit, unit
    return minutes, 1
