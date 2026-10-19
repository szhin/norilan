"""Logic lịch thuần Python (không Qt): lưới tháng/tuần, nhóm mục theo ngày, nhãn theo ngôn ngữ đang dùng."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.i18n import tr, tr_seq
from app.models import CalendarItem, Deadline, Event, Priority, StudyTime

_RELATIVE = {-1: "relative.yesterday", 0: "relative.today", 1: "relative.tomorrow"}


class ViewMode(StrEnum):
    MONTH = "month"
    WEEK = "week"
    DAY = "day"

    @property
    def label(self) -> str:
        return tr(f"view.{self.value}")


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
def weekday_short(index: int) -> str:
    """Tên thứ viết tắt (0 = Thứ Hai): 'T2', 'Mon'…"""
    return tr_seq("weekday.short")[index]


def weekday_long(index: int) -> str:
    return tr_seq("weekday.long")[index]


def date_text(day: date | datetime) -> str:
    """Ngày đầy đủ theo thứ tự của ngôn ngữ: '14/10/2026' (vi/en/es) hoặc '2026/10/14' (zh/ja)."""
    return tr("fmt.date", d=day)


def day_month_text(day: date | datetime) -> str:
    return tr("fmt.day_month", d=day)


def day_heading(day: date) -> str:
    """'Thứ Tư, 14/10/2026'."""
    return tr("fmt.day_heading", weekday=weekday_long(day.weekday()), d=day)


def relative_label(day: date, today: date) -> str | None:
    """'Hôm qua' / 'Hôm nay' / 'Ngày mai', hoặc None."""
    key = _RELATIVE.get((day - today).days)
    return tr(key) if key else None


def group_label(day: date, today: date) -> str:
    """Tiêu đề nhóm trong danh sách: 'Ngày mai · Thứ Tư, 14/10/2026'."""
    rel = relative_label(day, today)
    return f"{rel} · {day_heading(day)}" if rel else day_heading(day)


def period_title(view: ViewMode, selected: date) -> str:
    if view == ViewMode.MONTH:
        return tr("fmt.month_year", month=selected.month, year=selected.year,
                  month_name=tr_seq("months")[selected.month - 1])
    if view == ViewMode.WEEK:
        first, last = week_dates(selected)[0], week_dates(selected)[-1]
        return tr("fmt.week_range_years" if first.year != last.year else "fmt.week_range", first=first, last=last)
    return day_heading(selected)


def format_minutes(minutes: int | None) -> str:
    """60 → '1 giờ', 1530 → '1 ngày 1 giờ 30 phút', 0 → 'Đúng giờ', None → 'Không nhắc'."""
    if minutes is None:
        return tr("remind.none")
    if minutes == 0:
        return tr("remind.on_time")
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [tr(key, n=n, count=n) for n, key in ((days, "unit.day"), (hours, "unit.hour"), (mins, "unit.minute")) if n]
    return tr("fmt.join_units").join(parts)


# ------------------------------------------------------------------ Mục trên lịch
def item_last_day(item: CalendarItem) -> date:
    """Ngày cuối cùng mục còn hiển thị. Event kết thúc đúng 00:00 không chiếm ngày hôm sau."""
    if isinstance(item, (Event, StudyTime)) and item.ends_at > item.starts_at:
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
    """Trạng thái hiển thị: 'done' | 'overdue' | 'soon' (Deadline còn <24h) | 'high' | 'normal'."""
    if isinstance(item, Deadline):
        if item.is_done:
            return "done"
        if item.is_overdue(now):
            return "overdue"
        if is_due_soon(item, now):
            return "soon"
        if item.priority == Priority.HIGH:
            return "high"
    return "normal"


DUE_SOON = timedelta(hours=24)


def is_due_soon(deadline: Deadline, now: datetime) -> bool:
    """Chưa xong, chưa quá hạn và hạn trong vòng 24 giờ tới → tô màu cảnh báo."""
    return not deadline.is_done and timedelta(0) <= deadline.due_at - now < DUE_SOON


def round_for_display(minutes: int) -> int:
    """Làm tròn cho dễ đọc ('còn 3 giờ' thay vì 'còn 2 giờ 59 phút')."""
    if minutes >= 1440:
        return round(minutes / 60) * 60
    if minutes >= 120:
        return round(minutes / 15) * 15
    if minutes >= 60:
        return round(minutes / 5) * 5
    return minutes


def time_left_text(deadline: Deadline, now: datetime) -> str:
    """'còn 3 giờ' / 'quá hạn 2 ngày'; Deadline đã xong thì trả về chuỗi rỗng."""
    if deadline.is_done:
        return ""
    minutes = round((deadline.due_at - now).total_seconds() / 60)
    if minutes == 0:
        return tr("remind.now")
    span = format_minutes(round_for_display(abs(minutes)))
    return tr("left.left" if minutes > 0 else "left.overdue", span=span)


# Biểu tượng theo loại (Deadline nổi bật hơn Event/Study time: có icon, viền, đếm Study time)
KIND_ICONS = {Deadline.KIND: "⏳", StudyTime.KIND: "📖", Event.KIND: "📅"}


def kind_icon(item: CalendarItem) -> str:
    return KIND_ICONS[item.KIND]


def chip_label(item: CalendarItem, day: date) -> str:
    """Chữ trên chip lịch: '⏳ 09:00 Nộp báo cáo 📖2' (Deadline), '📖 19:00 Học Anh' (Study time), 'Họp' như cũ (Event)."""
    text = chip_text(item, day)
    if isinstance(item, Deadline):
        return f"{kind_icon(item)} {text} 📖{item.study_count}"
    if isinstance(item, StudyTime):
        return f"{kind_icon(item)} {text}"
    return text


def time_text(item: CalendarItem, *, with_date: bool = False) -> str:
    """'Hạn 23:59' | '19:00 – 20:30' | '23:00 13/10 – 01:00 14/10' (Event và Study time cùng dạng)."""
    if isinstance(item, Deadline):
        return tr("time.due", time=f"{item.due_at:%H:%M}") + (f" {day_month_text(item.due_at)}" if with_date else "")
    if item.starts_at.date() != item.ends_at.date():
        return (f"{item.starts_at:%H:%M} {day_month_text(item.starts_at)} – "
                f"{item.ends_at:%H:%M} {day_month_text(item.ends_at)}")
    text = f"{item.starts_at:%H:%M} – {item.ends_at:%H:%M}"
    return text + (f" {day_month_text(item.starts_at)}" if with_date else "")


def item_meta(item: CalendarItem, now: datetime, *, with_date: bool = False, show_subject: bool = True) -> str:
    """Dòng phụ trên thẻ: 'Hạn 23:59 · Toán · Chưa làm · còn 3 giờ · 📎1' hoặc '19:00 – 20:30 · 📍 Thư viện'."""
    parts = [time_text(item, with_date=with_date)]
    if isinstance(item, Deadline):
        if item.subject and show_subject:
            parts.append(item.subject)
        left = time_left_text(item, now)
        if item.is_overdue(now):
            parts.append(left or tr("status.overdue"))  # 'quá hạn 23 giờ' đã bao hàm trạng thái
        else:
            parts.append(item.status.label)
            if left:
                parts.append(left)
        if item.attachments:
            parts.append(f"📎{len(item.attachments)}")
    elif isinstance(item, StudyTime):
        if item.subject and show_subject:
            parts.append(item.subject)
    elif item.location:
        parts.append(f"📍 {item.location}")
    return " · ".join(parts)


# ------------------------------------------------------------------ Nhắc trước
# (khoá trong bảng dịch 'remind.unit.<khoá>', số phút của một đơn vị)
REMIND_UNITS: tuple[tuple[str, int], ...] = (("minute", 1), ("hour", 60), ("day", 1440))


def split_minutes(minutes: int) -> tuple[int, int]:
    """Tách số phút thành (giá trị, đơn vị lớn nhất chia hết): 120 → (2, 60), 2880 → (2, 1440)."""
    for _, unit in reversed(REMIND_UNITS):
        if minutes > 0 and minutes % unit == 0:
            return minutes // unit, unit
    return minutes, 1
