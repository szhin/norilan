from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.countdown import Phase, WARN_BEFORE_END, format_hms, state, warning_due

START = datetime(2026, 10, 14, 15, 30)
END = datetime(2026, 10, 14, 16, 30)


def test_running_examples_from_spec():
    at_start = state(START, START, END)
    assert at_start.phase == Phase.RUNNING and format_hms(at_start.seconds) == "1:00:00"
    opened_late = state(datetime(2026, 10, 14, 15, 45), START, END)
    assert opened_late.phase == Phase.RUNNING and format_hms(opened_late.seconds) == "0:45:00"
    assert state(datetime(2026, 10, 14, 16, 29, 59), START, END).seconds == 1


def test_end_boundary_is_ended_at_exactly_zero():
    ended = state(END, START, END)
    assert ended.phase == Phase.ENDED and format_hms(ended.seconds) == "0:00:00" and ended.fraction == 1.0
    assert state(END + timedelta(hours=3), START, END).phase == Phase.ENDED


def test_upcoming_counts_to_start():
    s = state(START - timedelta(hours=2, minutes=5, seconds=7), START, END)
    assert s.phase == Phase.UPCOMING and format_hms(s.seconds) == "2:05:07" and s.fraction == 0.0
    assert state(START - timedelta(seconds=1), START, END).phase == Phase.UPCOMING


def test_recomputed_from_clock_not_decremented():
    """Gọi không đều (máy ngủ, tick trễ) vẫn ra đúng số giây theo giờ hệ thống."""
    a = state(START + timedelta(minutes=1), START, END)
    b = state(START + timedelta(minutes=41), START, END)          # nhảy 40 phút
    assert a.seconds - b.seconds == 40 * 60
    assert state(START + timedelta(minutes=1), START, END) == a     # cùng đầu vào → cùng kết quả


def test_fraction_progress():
    assert state(START + timedelta(minutes=30), START, END).fraction == pytest.approx(0.5)


def test_warning_color_threshold_is_ten_minutes_inclusive():
    assert not state(END - timedelta(minutes=10, seconds=1), START, END).is_warning
    assert state(END - timedelta(minutes=10), START, END).is_warning
    assert state(END - timedelta(seconds=1), START, END).is_warning
    assert not state(END, START, END).is_warning                  # đã hết: không còn "cảnh báo"
    assert not state(START - timedelta(minutes=1), START, END).is_warning


def test_crossing_midnight():
    start, end = datetime(2026, 10, 14, 23, 30), datetime(2026, 10, 15, 0, 30)
    assert format_hms(state(datetime(2026, 10, 14, 23, 45), start, end).seconds) == "0:45:00"
    assert format_hms(state(datetime(2026, 10, 15, 0, 15), start, end).seconds) == "0:15:00"
    assert state(datetime(2026, 10, 15, 0, 20), start, end).is_warning
    assert state(datetime(2026, 10, 15, 0, 30), start, end).phase == Phase.ENDED
    assert warning_due(datetime(2026, 10, 15, 0, 20), start, end)


@pytest.mark.parametrize(("seconds", "text"), [(0, "0:00:00"), (59, "0:00:59"), (3600, "1:00:00"),
                                               (36000, "10:00:00"), (-5, "0:00:00")])
def test_format_hms(seconds, text):
    assert format_hms(seconds) == text


# ------------------------------------------------------------------ Mốc gửi toast 10 phút
def test_warning_due_boundary():
    assert not warning_due(END - WARN_BEFORE_END - timedelta(seconds=1), START, END)
    assert warning_due(END - WARN_BEFORE_END, START, END)
    assert warning_due(END - timedelta(seconds=1), START, END)
    assert not warning_due(END, START, END)                       # hết giờ rồi thì thôi


def test_warning_never_for_duration_under_ten_minutes():
    start = datetime(2026, 10, 14, 9, 0)
    end = start + timedelta(minutes=9, seconds=59)
    for offset in (0, 60, 300, 599):
        assert not warning_due(start + timedelta(seconds=offset), start, end)


def test_warning_duration_exactly_ten_minutes_fires_from_start():
    start = datetime(2026, 10, 14, 9, 0)
    end = start + timedelta(minutes=10)
    assert not warning_due(start - timedelta(seconds=1), start, end)
    assert warning_due(start, start, end)


def test_app_started_late_still_due_once_running():
    """Mở app khi còn 4 phút: vẫn đang chạy, chưa gắn cờ → đến hạn gửi ngay."""
    assert warning_due(END - timedelta(minutes=4), START, END)
