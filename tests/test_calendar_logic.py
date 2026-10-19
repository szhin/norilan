from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.calendar_logic import (
    ViewMode,
    add_months,
    chip_state,
    chip_text,
    format_minutes,
    group_label,
    item_meta,
    items_by_day,
    month_grid,
    period_title,
    shift,
    split_overflow,
    time_text,
    visible_days,
    week_dates,
)
from app.models import Attachment, Event, Priority, Deadline, DeadlineStatus

NOW = datetime(2026, 10, 13, 16, 0)


def test_month_grid_starts_monday_and_covers_month():
    grid = month_grid(2026, 10)
    assert len(grid) == 42
    assert grid[0] == date(2026, 9, 28) and grid[0].weekday() == 0
    assert date(2026, 10, 1) in grid and date(2026, 10, 31) in grid


def test_month_grid_month_starting_on_monday():
    assert month_grid(2026, 6)[0] == date(2026, 6, 1)


def test_week_dates():
    week = week_dates(date(2026, 10, 18))  # Chủ Nhật
    assert week[0] == date(2026, 10, 12) and week[-1] == date(2026, 10, 18)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [(date(2026, 1, 31), 1, date(2026, 2, 28)), (date(2026, 12, 15), 1, date(2027, 1, 15)),
     (date(2026, 1, 10), -1, date(2025, 12, 10)), (date(2028, 3, 31), -1, date(2028, 2, 29))],
)
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_shift_and_visible_days():
    d = date(2026, 10, 13)
    assert shift(ViewMode.MONTH, d, 1) == date(2026, 11, 13)
    assert shift(ViewMode.WEEK, d, -1) == date(2026, 10, 6)
    assert shift(ViewMode.DAY, d, 1) == date(2026, 10, 14)
    assert len(visible_days(ViewMode.WEEK, d)) == 7 and visible_days(ViewMode.DAY, d) == [d]


@pytest.mark.parametrize(
    ("view", "day", "expected"),
    [(ViewMode.MONTH, date(2026, 10, 13), "Tháng 10, 2026"),
     (ViewMode.WEEK, date(2026, 10, 13), "12/10 – 18/10/2026"),
     (ViewMode.WEEK, date(2026, 12, 31), "28/12/2026 – 03/01/2027"),
     (ViewMode.DAY, date(2026, 10, 14), "Thứ Tư, 14/10/2026")],
)
def test_period_title(view, day, expected):
    assert period_title(view, day) == expected


def test_group_label():
    today = date(2026, 10, 13)
    assert group_label(date(2026, 10, 14), today) == "Ngày mai · Thứ Tư, 14/10/2026"
    assert group_label(date(2026, 10, 20), today) == "Thứ Ba, 20/10/2026"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(None, "Không nhắc"), (0, "Đúng giờ"), (30, "30 phút"), (60, "1 giờ"),
     (90, "1 giờ 30 phút"), (1440, "1 ngày"), (1530, "1 ngày 1 giờ 30 phút")],
)
def test_format_minutes(minutes, expected):
    assert format_minutes(minutes) == expected


def test_items_by_day_spreads_multi_day_events():
    days = week_dates(date(2026, 10, 13))
    overnight = Event("Qua đêm", datetime(2026, 10, 13, 22), datetime(2026, 10, 14, 2))
    until_midnight = Event("Đến 0h", datetime(2026, 10, 14, 20), datetime(2026, 10, 15, 0))
    started_before = Event("Trại hè", datetime(2026, 10, 10, 8), datetime(2026, 10, 12, 18))
    task = Deadline("T", datetime(2026, 10, 13, 9))
    grouped = items_by_day([started_before, task, overnight, until_midnight], days)
    assert grouped[date(2026, 10, 12)] == [started_before]
    assert grouped[date(2026, 10, 13)] == [task, overnight]
    assert grouped[date(2026, 10, 14)] == [overnight, until_midnight]
    assert grouped[date(2026, 10, 15)] == []


def test_split_overflow():
    items = [Deadline(str(i), NOW) for i in range(5)]
    visible, hidden = split_overflow(items, 3)
    assert len(visible) == 3 and hidden == 2
    assert split_overflow(items[:2], 3) == (items[:2], 0)
    assert split_overflow(items, None) == (items, 0)


def test_chip_text_and_state():
    ev = Event("Họp", datetime(2026, 10, 13, 23), datetime(2026, 10, 14, 1))
    assert chip_text(ev, date(2026, 10, 13)) == "23:00 Họp"
    assert chip_text(ev, date(2026, 10, 14)) == "↳ Họp"
    assert chip_state(Deadline("a", NOW - timedelta(hours=1)), NOW) == "overdue"
    assert chip_state(Deadline("a", NOW - timedelta(hours=1), status=DeadlineStatus.DONE), NOW) == "done"
    assert chip_state(Deadline("a", NOW + timedelta(hours=1), priority=Priority.HIGH), NOW) == "soon"  # <24h thắng 'high'
    assert chip_state(Deadline("a", NOW + timedelta(hours=30), priority=Priority.HIGH), NOW) == "high"
    assert chip_state(Deadline("a", NOW + timedelta(hours=30)), NOW) == "normal"
    assert chip_state(Deadline("a", NOW + timedelta(hours=1), status=DeadlineStatus.DONE), NOW) == "done"
    assert chip_state(ev, NOW) == "normal"


def test_time_text_and_meta():
    task = Deadline("Lab", datetime(2026, 10, 14, 23, 59), subject="PPT", attachments=[Attachment(r"C:\a.docx")])
    assert time_text(task) == "Hạn 23:59"
    assert item_meta(task, NOW) == "Hạn 23:59 · PPT · Chưa làm · còn 1 ngày 8 giờ · 📎1"
    late = Deadline("Trễ", datetime(2026, 10, 12, 17))
    assert item_meta(late, NOW) == "Hạn 17:00 · quá hạn 23 giờ"
    ev = Event("Họp", datetime(2026, 10, 13, 19), datetime(2026, 10, 13, 20, 30), location="Thư viện")
    assert item_meta(ev, NOW) == "19:00 – 20:30 · 📍 Thư viện"
    long_ev = Event("Trại", datetime(2026, 10, 13, 23), datetime(2026, 10, 14, 1))
    assert time_text(long_ev) == "23:00 13/10 – 01:00 14/10"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(30, (30, 1)), (60, (1, 60)), (90, (90, 1)), (120, (2, 60)), (1440, (1, 1440)), (2880, (2, 1440)), (0, (0, 1))],
)
def test_split_minutes(minutes, expected):
    from app.calendar_logic import split_minutes

    assert split_minutes(minutes) == expected
