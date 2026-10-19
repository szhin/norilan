"""Đồng hồ đếm ngược Study time: ô nhỏ trong panel, timer 1 giây dùng chung, overlay, toast 10 phút."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from PySide6.QtWidgets import QApplication

from app.files import FileService
from app.models import Deadline, Event, StudyTime
from app.settings import Settings
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.usefixtures("qapp")
START = datetime(2026, 10, 14, 15, 30)
END = datetime(2026, 10, 14, 16, 30)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 14, 15, 45))


@pytest.fixture
def window(repo, launcher, prompts_stub, clock):
    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=clock)
    win.show()
    yield win
    win.close()
    win.deleteLater()


def make_study(repo, title="Ôn thi", start=START, end=END, deadline_id=None):
    return repo.add_study_time(StudyTime(title, start, end, deadline_id=deadline_id))


def small_text(window) -> str:
    return window.panel.detail.countdown_widget._text.text()


# ------------------------------------------------------------------ Đồng hồ nhỏ
def test_small_timer_opened_mid_session_shows_remaining(window, repo, clock):
    study = make_study(repo)
    window.open_item(study.id)
    assert small_text(window) == "⏱ 0:45:00"                     # mở lúc 15:45 → còn 0:45:00
    widget = window.panel.detail.countdown_widget
    assert widget.toolTip() == "Bấm để phóng to" and widget.cursor().shape().name == "PointingHandCursor"
    assert widget.parent() is window.panel.detail.countdown_slot


def test_small_timer_follows_system_clock_not_a_counter(window, repo, clock):
    study = make_study(repo)
    window.open_item(study.id)
    clock.now = datetime(2026, 10, 14, 16, 0, 0)                  # nhảy 15 phút (như máy vừa thức dậy)
    window._on_second()
    assert small_text(window) == "⏱ 0:30:00"
    clock.now = END
    window._on_second()
    assert small_text(window) == "Đã kết thúc"


def test_small_timer_upcoming_and_warning_state(window, repo, clock):
    study = make_study(repo)
    clock.now = START - timedelta(hours=1, minutes=2, seconds=3)
    window.open_item(study.id)
    assert small_text(window) == "Bắt đầu sau 1:02:03"
    clock.now = END - timedelta(minutes=10)
    window._on_second()
    text = window.panel.detail.countdown_widget._text
    assert text.text() == "⏱ 0:10:00" and text.property("warn") is True
    clock.now = END - timedelta(minutes=10, seconds=1)
    window._on_second()
    assert text.property("warn") is False


def test_timer_runs_only_while_a_countdown_is_visible(window, repo, clock):
    study = make_study(repo)
    deadline = repo.add_deadline(Deadline("D", END + timedelta(days=1)))
    event = repo.add_event(Event("E", START, END))
    assert not window._second_timer.isActive()
    window.open_item(study.id)
    assert window._second_timer.isActive()
    window.open_item(deadline.id)                                 # Deadline: không có đồng hồ
    assert not window._second_timer.isActive() and window.panel.detail.countdown_widget is None
    window.open_item(event.id)                                    # Event: không có đồng hồ
    assert not window._second_timer.isActive() and window.panel.detail.countdown_widget is None
    window.open_item(study.id)
    assert window._second_timer.isActive()
    window.panel.show_list_page()                                 # quay lại danh sách → dừng
    assert not window._second_timer.isActive()
    window.open_item(study.id)
    window.hide()                                                 # ẩn xuống khay → dừng
    assert not window._second_timer.isActive()
    window.show()
    QApplication.processEvents()                                  # showEvent đồng bộ timer ở lượt sự kiện kế tiếp
    assert window._second_timer.isActive()


def test_single_shared_one_second_timer(window):
    assert window._second_timer.interval() == 1000
    assert len([t for t in window.findChildren(type(window._second_timer)) if t.interval() == 1000]) == 1


# ------------------------------------------------------------------ Overlay
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton


def open_overlay(window, study):
    window.open_item(study.id)
    window.panel.detail.countdown_widget.clicked.emit()
    return window.overlay


def test_overlay_never_opens_by_itself(window, repo):
    study = make_study(repo)
    window.open_item(study.id)
    window.refresh()
    window.focus_item(study.id)
    assert not window.overlay.is_open()


def test_click_small_timer_opens_overlay_with_all_info(window, repo, clock):
    deadline = repo.add_deadline(Deadline("Nộp báo cáo", END + timedelta(hours=5)))
    study = make_study(repo, "Làm báo cáo", deadline_id=deadline.id)
    overlay = open_overlay(window, study)
    assert overlay.is_open() and overlay.study_id == study.id
    assert overlay.digits.text() == "0:45:00" and overlay.caption.text() == "Còn lại"
    assert overlay.title.text() == "Làm báo cáo" and overlay.range.text() == "15:30 – 16:30"
    assert overlay.deadline.text() == "Deadline: Nộp báo cáo · còn 5 giờ 45 phút"
    assert overlay.progress.value() == 250                      # 15/60 phút đã trôi qua
    assert window._second_timer.isActive()
    assert overlay.geometry() == window.central.rect()          # phủ kín cửa sổ


def test_overlay_without_deadline_hides_deadline_line(window, repo):
    overlay = open_overlay(window, make_study(repo))
    assert overlay.deadline.isHidden()


def test_overlay_ticks_warning_and_stays_after_end(window, repo, clock):
    overlay = open_overlay(window, make_study(repo))
    assert overlay.digits.property("warn") is False
    clock.now = END - timedelta(minutes=10)
    window._on_second()
    assert overlay.digits.text() == "0:10:00" and overlay.digits.property("warn") is True
    assert overlay.progress.property("warn") is True
    clock.now = END
    window._on_second()
    assert overlay.digits.text() == "Đã kết thúc" and overlay.caption.isHidden()
    clock.now = END + timedelta(minutes=30)
    window._on_second()
    assert overlay.is_open() and overlay.digits.text() == "Đã kết thúc"   # ở lại đến khi đóng
    assert overlay.progress.value() == 1000


def test_overlay_upcoming_state(window, repo, clock):
    clock.now = START - timedelta(minutes=5)
    overlay = open_overlay(window, make_study(repo))
    assert overlay.caption.text() == "Bắt đầu sau" and overlay.digits.text() == "0:05:00"
    assert overlay.progress.value() == 0 and overlay.digits.property("warn") is False


def test_overlay_closes_with_x_button_and_stops_timer(window, repo):
    overlay = open_overlay(window, make_study(repo))
    overlay.close_button.click()
    assert not overlay.is_open() and overlay.isHidden()
    window.panel.show_list_page()
    assert not window._second_timer.isActive()


def test_overlay_closes_by_clicking_backdrop_but_not_card(window, repo):
    overlay = open_overlay(window, make_study(repo))
    QTest.mouseClick(overlay.card, Qt.MouseButton.LeftButton)
    assert overlay.is_open()                                     # bấm vào thẻ: không đóng
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
    assert not overlay.is_open()                                 # bấm nền: đóng


def esc_shortcut(window):
    # Ô tìm kiếm có Esc riêng (WidgetShortcut, chỉ khi đang gõ); ở đây chỉ xét Esc cấp cửa sổ.
    (sc,) = [s for s in window.findChildren(QShortcut)
             if s.key() == QKeySequence("Esc") and s.context() == Qt.ShortcutContext.WindowShortcut]
    return sc


def test_esc_closes_overlay_first_then_goes_back_to_list(window, repo):
    study = make_study(repo)
    overlay = open_overlay(window, study)
    esc_shortcut(window)   # đúng một Esc cấp cửa sổ (hai cái trùng phím thì Qt coi là mơ hồ và bỏ cả hai)
    esc_shortcut(window).activated.emit()
    assert not overlay.is_open() and window.panel.is_showing_detail()   # lần 1: chỉ đóng overlay
    esc_shortcut(window).activated.emit()
    assert not window.panel.is_showing_detail()                          # lần 2: "quay lại danh sách" như cũ


def test_esc_key_on_overlay_itself_closes_it(window, repo):
    overlay = open_overlay(window, make_study(repo))
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert not overlay.is_open()


def test_overlay_blocks_other_shortcuts(window, repo, monkeypatch):
    calls = []
    monkeypatch.setattr(MainWindow, "new_deadline", lambda self, day=None: calls.append("deadline"))
    window._build_shortcuts()
    overlay = open_overlay(window, make_study(repo))
    for sc in window.findChildren(QShortcut):
        if sc.key() == QKeySequence("Ctrl+N"):
            sc.activated.emit()
    assert calls == []                                           # đang mở overlay: Ctrl+N bị chặn
    overlay.close_overlay()
    for sc in window.findChildren(QShortcut):
        if sc.key() == QKeySequence("Ctrl+N"):
            sc.activated.emit()
    assert calls and set(calls) == {"deadline"}


def test_overlay_resizes_with_window(window, repo):
    overlay = open_overlay(window, make_study(repo))
    window.resize(1500, 900)
    QApplication.processEvents()
    assert overlay.geometry() == window.central.rect()
    assert overlay.card.width() <= 860 and overlay.card.geometry().center().x() == pytest.approx(overlay.width() / 2, abs=2)


def test_overlay_reloads_when_study_edited_and_closes_when_deleted(window, repo, clock, prompts_stub):
    study = make_study(repo)
    overlay = open_overlay(window, study)
    study.title = "Tên mới"
    study.ends_at = END + timedelta(minutes=30)
    repo.update_study_time(study)
    window.refresh()
    assert overlay.is_open() and overlay.title.text() == "Tên mới" and overlay.range.text() == "15:30 – 17:00"
    repo.delete_item(study.id)
    window.refresh()
    assert not overlay.is_open()


def test_overlay_theme_backdrop_follows_light_dark(window, repo, qapp):
    from app.ui import theme

    overlay = open_overlay(window, make_study(repo))
    theme.apply_theme(qapp, "light")
    light = overlay.grab().toImage().pixelColor(2, 2)
    theme.apply_theme(qapp, "dark")
    dark = overlay.grab().toImage().pixelColor(2, 2)
    theme.apply_theme(qapp, "light")
    assert dark.lightness() < light.lightness()                  # nền tối hơn ở theme tối
