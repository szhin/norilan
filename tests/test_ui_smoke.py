"""Kiểm thử giao diện ở chế độ offscreen (không hiện cửa sổ)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

pytest.importorskip("PySide6")
from app.calendar_logic import ViewMode
from app.files import FileService
from app.models import Attachment, Event, Task, TaskStatus
from app.settings import Settings
from app.ui.main_window import MainWindow

NOW = datetime(2026, 10, 13, 16, 0)
TODAY = NOW.date()


pytestmark = pytest.mark.usefixtures("prompts_stub")


@pytest.fixture
def seeded(repo):
    ids = {}
    ids["meeting"] = repo.add_event(Event("Họp nhóm", datetime(2026, 10, 13, 19), datetime(2026, 10, 13, 20),
                                          location="Thư viện")).id
    ids["lab"] = repo.add_task(Task("Lab 2", datetime(2026, 10, 14, 23, 59), subject="PPT",
                                    attachments=[Attachment(r"C:\khong\ton\tai.docx")])).id
    ids["late"] = repo.add_task(Task("Báo cáo trễ", datetime(2026, 10, 12, 17))).id
    for hour in range(8, 13):
        repo.add_task(Task(f"Việc {hour}h", datetime(2026, 10, 15, hour)))
    done = repo.add_task(Task("Đã xong", datetime(2026, 10, 9, 8)))
    repo.set_task_status(done.id, TaskStatus.DONE)
    return ids


@pytest.fixture
def window(qapp, repo, seeded, launcher):
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def test_opens_on_today_in_month_view(window):
    assert window.view_mode == ViewMode.MONTH
    assert window.period_label.text() == "Tháng 10, 2026"
    assert window.panel.title() == "Thứ Ba, 13/10/2026"
    assert window.panel.list.titles() == ["Họp nhóm"]
    assert window.sidebar.buttons["today"].isChecked()


def test_month_cell_limits_chips_to_three(window):
    cell = window.month_view.cell_for(date(2026, 10, 15))
    assert cell.chip_texts() == ["08:00 Việc 8h", "09:00 Việc 9h", "10:00 Việc 10h"]
    assert cell.more_text() == "+2 mục khác"
    assert window.month_view.cell_for(date(2026, 10, 9)).chip_texts() == ["08:00 Đã xong"]


def test_sidebar_counts(window):
    counts = {k: b.count_text() for k, b in window.sidebar.buttons.items()}
    assert counts == {"calendar": "", "today": "1", "tomorrow": "1", "upcoming": "7", "overdue": "1"}


def test_navigation_and_views(window):
    window.go(1)
    assert window.period_label.text() == "Tháng 11, 2026"
    assert window.sidebar.buttons["calendar"].isChecked()
    window.go_today()
    window.set_view(ViewMode.WEEK)
    assert window.period_label.text() == "12/10 – 18/10/2026"
    assert window.week_view.cell_for(date(2026, 10, 15)).more_text() == ""  # tuần hiện đủ 5 mục
    window.set_view(ViewMode.DAY)
    window.go(1)
    assert window.selected_day == date(2026, 10, 14)
    assert window.sidebar.buttons["tomorrow"].isChecked()


def test_click_day_shows_its_items(window):
    window.on_day_clicked(date(2026, 10, 15))
    assert window.panel.list.titles() == [f"Việc {h}h" for h in range(8, 13)]
    assert window.sidebar.buttons["calendar"].isChecked()


def test_quick_lists(window):
    window.on_nav("overdue")
    assert window.panel.title() == "Quá hạn"
    assert window.panel.list.titles() == ["Báo cáo trễ"]
    window.on_nav("tomorrow")
    assert window.selected_day == date(2026, 10, 14)
    assert window.panel.list.titles() == ["Lab 2"]


def test_click_chip_opens_task_detail(window, seeded):
    window.on_calendar_item(seeded["lab"], date(2026, 10, 14))
    assert window.panel.is_showing_detail()
    fields = window.panel.detail.field_values()
    assert fields["Hạn nộp"] == "23:59 · Thứ Tư, 14/10/2026"
    assert fields["Môn học"] == "PPT"
    assert window.sidebar.buttons["tomorrow"].isChecked()
    window.panel.show_list_page()
    assert not window.panel.is_showing_detail()


def test_event_detail_shows_location(window, seeded):
    window.open_item(seeded["meeting"])
    fields = window.panel.detail.field_values()
    assert fields["Địa điểm"] == "Thư viện"
    assert fields["Thời gian"].startswith("19:00 – 20:00")


def test_opening_item_from_list_keeps_list(window, seeded):
    window.on_nav("overdue")
    window.open_item(seeded["late"])
    assert window.selected_day == date(2026, 10, 12)
    assert window.sidebar.buttons["overdue"].isChecked()
    window.panel.show_list_page()
    assert window.panel.title() == "Quá hạn"


def test_deleted_item_does_not_crash(window, repo, seeded):
    repo.delete_item(seeded["lab"])
    window.open_item(seeded["lab"])
    assert not window.panel.is_showing_detail()


def test_midnight_rollover(qapp, repo, seeded, launcher):
    clock = {"now": datetime(2026, 10, 13, 23, 59)}
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: clock["now"])
    clock["now"] = datetime(2026, 10, 14, 0, 1)
    win._on_tick()
    assert win.selected_day == date(2026, 10, 14)
    assert win.panel.list.titles() == ["Lab 2"]
    win.close()
