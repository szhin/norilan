"""Lưới giờ của xem tuần / ngày: vị trí khối theo giờ, chia cột khi trùng giờ, view Qt."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.calendar_logic import MIN_BLOCK_MINUTES, block_span, block_text, layout_day
from app.models import Deadline, Event, StudyTime

DAY = date(2026, 10, 14)


def at(h: int, m: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, h, m)


def event(title: str, start: datetime, end: datetime) -> Event:
    return Event(title, start, end, id=hash(title) % 10_000)


# ------------------------------------------------------------------ Vị trí khối
def test_block_span_follows_start_and_duration():
    assert block_span(event("Họp", at(9), at(10, 30)), DAY) == (540, 630)
    assert block_span(event("Ngắn", at(9), at(9, 10)), DAY) == (540, 540 + MIN_BLOCK_MINUTES)
    assert block_span(Deadline("Nộp", at(14)), DAY) == (840, 840 + MIN_BLOCK_MINUTES)


def test_block_span_clips_to_day_and_stays_visible_near_midnight():
    assert block_span(Deadline("Nộp", at(23, 59)), DAY) == (1410, 1440)
    overnight = event("Qua đêm", at(22), at(2, day=DAY + timedelta(days=1)))
    assert block_span(overnight, DAY) == (1320, 1440)
    assert block_span(overnight, DAY + timedelta(days=1)) == (0, 120)


def test_layout_splits_overlapping_items_into_columns():
    a = event("A", at(9), at(11))
    b = event("B", at(10), at(12))
    c = event("C", at(11), at(12))       # trùng B nhưng không trùng A → dùng lại cột của A
    d = event("D", at(13), at(14))       # cụm riêng: rộng hết cột
    placed = {p.item.title: (p.column, p.columns) for p in layout_day([d, c, b, a], DAY)}
    assert placed == {"A": (0, 2), "B": (1, 2), "C": (0, 2), "D": (0, 1)}


def test_block_text():
    study = StudyTime("Ôn tập", at(19), at(21), subject="Toán")
    assert block_text(study, DAY) == "📖 Ôn tập\n19:00 – 21:00"
    assert block_text(Event("Họp", at(9), at(10), location="P.3"), DAY) == "Họp\n09:00 – 10:00 · P.3"
    assert block_text(Deadline("Nộp", at(23, 59)), DAY) == "⏳ Nộp"


# ------------------------------------------------------------------ Giao diện
@pytest.fixture
def window(qapp, repo, launcher, prompts_stub):
    from app.files import FileService
    from app.settings import Settings
    from app.ui.main_window import MainWindow

    win = MainWindow(repo, Settings(default_view="week"), files=FileService(launcher), clock=lambda: at(16))
    win.resize(1320, 820)
    yield win
    win.close()
    win.deleteLater()


def test_week_view_places_blocks_by_time(window, repo):
    from app.ui.calendar_views import HOUR_HEIGHT

    saved = repo.add_event(Event("Họp", at(9), at(11)))
    repo.add_event(Event("Trùng", at(10), at(11)))
    window.refresh()
    window.show()
    column = window.week_view.column_for(DAY)
    assert column.titles() == ["Họp", "Trùng"]
    block = next(b for b in column.blocks if b.item_id == saved.id)
    assert block.y() == 9 * HOUR_HEIGHT + 1 and block.height() == 2 * HOUR_HEIGHT - 2
    assert block.width() < column.width() / 2 + 1         # chia đôi với mục trùng giờ
    assert window.week_view.scroll_value() == round(6.5 * HOUR_HEIGHT)   # lúc mở: tuần trống → 7:00
    window.go(1)
    window.go(-1)                                          # đổi tuần → cuộn tới trước mục sớm nhất
    assert window.week_view.scroll_value() == round(8.5 * HOUR_HEIGHT)
    window.refresh()                                       # làm mới không cuộn lại
    assert window.week_view.scroll_value() == round(8.5 * HOUR_HEIGHT)


def test_week_view_clicks_select_day_and_open_item(window, repo):
    saved = repo.add_event(Event("Họp", at(9, day=DAY + timedelta(days=1)), at(10, day=DAY + timedelta(days=1))))
    window.refresh()
    window.week_view.column_for(DAY).day_clicked.emit(DAY)
    assert window.selected_day == DAY
    window.week_view.column_for(DAY + timedelta(days=1)).blocks[0].clicked.emit(saved.id)
    assert window.selected_day == DAY + timedelta(days=1)
    assert window.panel.detail_item_id() == saved.id


def test_day_view_is_single_column_grid(window, repo):
    from app.calendar_logic import ViewMode

    saved = repo.add_event(Event("Họp", at(9), at(10)))
    window.on_day_clicked(DAY)
    window.set_view(ViewMode.DAY)
    assert window.day_view.column_for(DAY).titles() == ["Họp"]
    window.open_item(saved.id)
    assert window.day_view.column_for(DAY).blocks[0].property("selected") is True
