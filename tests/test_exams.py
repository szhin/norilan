"""Ca thi: lưu/đọc, truy vấn sắp tới, nhắc mỗi ngày trong 15 ngày, banner và giao diện nổi bật."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.calendar_logic import chip_label, chip_state, exam_banner_text, exam_when_text, item_meta
from app.colors import ColorResolver
from app.models import Deadline, Exam, ValidationError
from app.reminders import EXAM_ALERT_KEY, ReminderService
from app.settings import Settings

NOW = datetime(2026, 10, 14, 10, 0)
H = timedelta(hours=1)


def make(repo, title="Giải tích 1", days=5, hour=8, **kw):
    start = datetime.combine((NOW + timedelta(days=days)).date(), datetime.min.time()).replace(hour=hour)
    return repo.add_exam(Exam(title, start, exam_class="CNTT01", location="A2.301", **kw))


def test_exam_roundtrip_and_validation(repo):
    exam = make(repo, notes="Mang CCCD", remind_before_minutes=60)
    got = repo.get_exam(exam.id)
    assert (got.title, got.exam_class, got.location, got.notes, got.remind_before_minutes) == (
        "Giải tích 1", "CNTT01", "A2.301", "Mang CCCD", 60)
    got.location = "B1.101"
    assert repo.update_exam(got).location == "B1.101"
    assert repo.count_items()["exam"] == 1
    with pytest.raises(ValidationError):
        Exam(" ", NOW).validate()


def test_upcoming_exams_filters_by_days_and_order(repo):
    make(repo, "Gần", days=2)
    make(repo, "Ngày thứ 15", days=15, hour=23)
    make(repo, "Ngày thứ 16", days=16, hour=7)
    past = make(repo, "Đã thi", days=-1)
    assert [e.title for e in repo.upcoming_exams(NOW, days=15)] == ["Gần", "Ngày thứ 15"]
    assert [e.title for e in repo.upcoming_exams(NOW)] == ["Gần", "Ngày thứ 15", "Ngày thứ 16"]
    assert past.id not in [e.id for e in repo.upcoming_exams(NOW)]


def test_exam_sorts_first_at_same_time(repo):
    start = NOW + 5 * H
    repo.add_deadline(Deadline("Nộp bài", start))
    repo.add_exam(Exam("Thi", start))
    assert [i.title for i in repo.items_on(start.date())] == ["Thi", "Nộp bài"]


def test_exam_state_text_and_tone(repo):
    exam = make(repo, days=0, hour=14)
    assert chip_state(exam, NOW) == "exam"
    assert chip_state(exam, exam.starts_at + 3 * H) == "normal"           # thi xong → hết nổi bật
    assert ColorResolver().tone(exam) == "red"
    assert chip_label(exam, exam.starts_at.date()) == "🎓 14:00 THI: Giải tích 1"
    assert exam_when_text(exam, NOW) == "HÔM NAY THI"
    assert exam_when_text(make(repo, days=1), NOW) == "NGÀY MAI THI"
    assert exam_when_text(make(repo, days=5), NOW) == "THI SAU 5 NGÀY"
    assert "Lớp thi: CNTT01" in item_meta(exam, NOW) and "📍 A2.301" in item_meta(exam, NOW)
    assert exam_banner_text(exam, NOW) == "🚨 HÔM NAY THI · Giải tích 1 · 14:00 14/10 · Lớp thi: CNTT01 · 📍 A2.301"


# ------------------------------------------------------------------ Nhắc mỗi ngày
@pytest.fixture
def service(repo):
    return ReminderService(repo, lambda: Settings(daily_digest_enabled=False))


def alerts(service, now):
    return [n for n in service.check(now) if n.key.startswith("exam-alert")]


def test_daily_alert_for_15_days_before_exam(repo, service):
    exam = make(repo, days=15, hour=8)
    assert alerts(service, NOW - timedelta(days=1)) == []                  # còn 16 ngày: chưa nhắc
    first = alerts(service, NOW)
    assert len(first) == 1 and first[0].title == "🎓 Còn 15 ngày nữa thi Giải tích 1"
    assert first[0].url == f"norilan://item/{exam.id}"
    assert "Lớp thi: CNTT01" in first[0].body and "📍 A2.301" in first[0].body
    assert alerts(service, NOW + 3 * H) == []                              # mỗi ngày một lần
    for day in range(1, 15):                                               # 14 ngày kế tiếp, ngày nào cũng nhắc
        assert len(alerts(service, NOW + timedelta(days=day) - 3 * H)) == 1, day


def test_alert_titles_for_last_two_days_and_exam_day(repo, service):
    make(repo, days=2, hour=18)
    assert alerts(service, NOW)[0].title == "🎓 Còn 2 ngày nữa thi Giải tích 1"
    assert alerts(service, NOW + timedelta(days=1))[0].title == "🚨 NGÀY MAI THI: Giải tích 1"
    assert alerts(service, NOW + timedelta(days=2) - 2 * H)[0].title == "🚨 HÔM NAY THI: Giải tích 1"


def test_alert_waits_until_7am_and_survives_restart(repo, service):
    exam = make(repo, days=3)
    assert alerts(service, datetime(2026, 10, 14, 6, 59)) == []
    assert len(alerts(service, datetime(2026, 10, 14, 7, 0))) == 1
    restarted = ReminderService(repo, lambda: Settings(daily_digest_enabled=False))
    assert alerts(restarted, datetime(2026, 10, 14, 9, 0)) == []
    assert repo.get_state(EXAM_ALERT_KEY.format(id=exam.id)) == "2026-10-14"


def test_alert_is_independent_per_exam_and_ignores_finished_exam(repo, service):
    make(repo, "A", days=1)
    make(repo, "B", days=2)
    make(repo, "Đã thi", days=-1)
    assert sorted(n.title for n in alerts(service, NOW)) == [
        "🎓 Còn 2 ngày nữa thi B", "🚨 NGÀY MAI THI: A"]
    make(repo, "C", days=4)                                                 # thêm sau: vẫn nhận ngay hôm nay
    assert [n.title for n in alerts(service, NOW + H)] == ["🎓 Còn 4 ngày nữa thi C"]


# ------------------------------------------------------------------ Giao diện
pytest.importorskip("PySide6")


@pytest.fixture
def window(qapp, repo, launcher, prompts_stub):
    from app.files import FileService
    from app.ui.main_window import MainWindow

    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def test_banner_hidden_without_exam_then_shows_nearest_first(repo, window):
    assert window.exam_banner.isHidden() and window.exam_banner.texts() == []
    make(repo, "Xa", days=10)
    make(repo, "Mai", days=1)
    make(repo, "Ngoài 15 ngày", days=20)
    window.refresh()
    banner = window.exam_banner
    assert not banner.isHidden()
    texts = banner.texts()
    assert [t.split(" · ")[1] for t in texts] == ["Mai", "Xa", "Ngoài 15 ngày"]   # banner liệt kê mọi ca thi
    assert texts[0].startswith("🚨 NGÀY MAI THI")
    assert not banner.is_expanded() and banner._area.isHidden()                    # thu gọn: chỉ dòng gần nhất
    assert banner.toggle.text() == "Xem tất cả (3) ▾"
    banner.toggle.click()
    assert banner.is_expanded() and not banner._area.isHidden() and banner.toggle.text() == "Thu gọn ▴"
    window.refresh()                                                                # làm mới không tự thu gọn
    assert banner.is_expanded()
    banner.toggle.click()
    assert not banner.is_expanded() and banner._area.isHidden()


def test_banner_single_exam_has_no_toggle(repo, window):
    make(repo, days=3)
    window.refresh()
    assert window.exam_banner.toggle.isHidden()


def test_banner_row_opens_exam_detail(repo, window):
    exam = make(repo, days=3)
    window.refresh()
    window.exam_banner.rows[0].click()
    assert window.panel.detail_item_id() == exam.id
    values = window.panel.detail.field_values()
    assert values["Lớp thi"] == "CNTT01" and values["Địa điểm"] == "A2.301"


def test_exam_marks_calendar_cell_and_always_visible(repo, window):
    day = (NOW + timedelta(days=2)).date()
    for i in range(4):                                      # ô tháng chỉ hiện 3 chip: ca thi không được bị ẩn
        repo.add_deadline(Deadline(f"Việc {i}", datetime.combine(day, datetime.min.time()).replace(hour=6 + i)))
    make(repo, "Thi cuối kỳ", days=2, hour=15)
    window.refresh()
    cell = window.month_view.cell_for(day)
    assert cell.property("exam") is True
    assert cell.chip_texts()[0] == "🎓 15:00 THI: Thi cuối kỳ"


def test_sidebar_exams_nav_lists_all_upcoming(repo, window):
    make(repo, "A", days=3)
    make(repo, "B", days=40)
    window.refresh()
    assert window.sidebar.buttons["exams"].count_text() == "2"
    window.on_nav("exams")
    assert window.panel.title() == "Lịch thi" and window.panel.list.titles() == ["A", "B"]


def test_create_and_edit_exam_through_dialog(repo, window, monkeypatch):
    from app.ui.dialogs import ExamDialog

    def fill(dialog):
        dialog.title_edit.setText("Vật lý đại cương")
        dialog.exam_class.setText("K65A")
        dialog.location.setText("Hội trường C")
        return True

    monkeypatch.setattr(window, "_exec_dialog", fill)
    window.new_exam()
    exam = repo.upcoming_exams(NOW)[0]
    assert (exam.title, exam.exam_class, exam.location) == ("Vật lý đại cương", "K65A", "Hội trường C")
    assert exam.starts_at.hour == 7 and exam.starts_at.minute == 30 and exam.remind_before_minutes == 60
    assert window.panel.detail_item_id() == exam.id

    def rename(dialog):
        assert isinstance(dialog, ExamDialog) and dialog.title_edit.text() == "Vật lý đại cương"
        dialog.title_edit.setText("Vật lý 1")
        return True

    monkeypatch.setattr(window, "_exec_dialog", rename)
    window.edit_item(exam.id)
    assert repo.get_exam(exam.id).title == "Vật lý 1"


def test_delete_exam_goes_through_confirmation(repo, window):
    exam = make(repo)
    window.delete_item(exam.id)
    assert repo.get_exam(exam.id) is None
