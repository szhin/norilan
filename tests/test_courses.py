"""Thời khoá biểu: Course sinh buổi học lặp hằng tuần, liên kết Deadline/Study time theo tên môn, migration v7, giao diện."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

import pytest

from app import db
from app.calendar_logic import chip_label, course_schedule_text, item_meta
from app.colors import ColorResolver
from app.db import SCHEMA_VERSION, connect, schema_version
from app.models import ClassSession, Course, Deadline, StudyTime, ValidationError
from app.reminders import item_notification

NOW = datetime(2026, 10, 13, 16, 0)          # Thứ Ba
MONDAY = date(2026, 10, 12)


def course(**overrides) -> Course:
    values = dict(name="Giải tích 1", weekdays=(0, 2), start_time=time(7, 30), end_time=time(9, 30),
                  first_day=MONDAY, weeks=3, room="A2.301", location="Cơ sở 1")
    values.update(overrides)
    return Course(**values)


def sessions(repo) -> list[ClassSession]:
    return [i for i in repo.items_between(datetime(2026, 1, 1), datetime(2027, 1, 1)) if isinstance(i, ClassSession)]


# ------------------------------------------------------------------ Model
def test_session_dates_repeat_weekly_from_first_day():
    c = course(first_day=date(2026, 10, 14))     # bắt đầu Thứ Tư: Thứ Hai tuần đầu bị bỏ
    assert c.session_dates() == [date(2026, 10, 14), date(2026, 10, 19), date(2026, 10, 21),
                                 date(2026, 10, 26), date(2026, 10, 28)]
    assert c.last_day == date(2026, 10, 28)
    c.skipped = (date(2026, 10, 19),)
    assert date(2026, 10, 19) not in c.session_dates()
    assert c.last_day == date(2026, 10, 28)


@pytest.mark.parametrize("bad", [
    dict(name="  "), dict(weekdays=()), dict(weekdays=(7,)), dict(end_time=time(7, 30)), dict(weeks=0),
    dict(remind_before_minutes=-1),
])
def test_course_validation(bad):
    with pytest.raises(ValidationError):
        course(**bad).validate()


# ------------------------------------------------------------------ Repository
def test_add_course_fills_calendar(repo):
    saved = repo.add_course(course())
    found = sessions(repo)
    assert [s.starts_at for s in found] == [datetime.combine(d, time(7, 30)) for d in saved.session_dates()]
    first = found[0]
    assert (first.title, first.subject, first.location, first.room) == ("Giải tích 1", "Giải tích 1", "Cơ sở 1", "A2.301")
    assert first.ends_at == datetime(2026, 10, 12, 9, 30) and first.course_id == saved.id
    assert repo.count_items()["class"] == 6
    assert chip_label(first, first.starts_at.date()) == "🏫 07:30 Giải tích 1"
    assert item_meta(first, NOW) == "07:30 – 09:30 · 📍 A2.301 · Cơ sở 1"
    assert course_schedule_text(saved) == "T2, T4 · 07:30 – 09:30"


def test_update_course_syncs_sessions_and_keeps_ids(repo):
    saved = repo.add_course(course(remind_before_minutes=15))
    before = {s.starts_at: s.id for s in sessions(repo)}
    repo.claim_reminder(before[datetime(2026, 10, 14, 7, 30)], NOW)
    saved.weekdays = (2, 4)                      # bỏ Thứ Hai, thêm Thứ Sáu
    saved.notes = "Thầy An"
    repo.update_course(saved)
    after = sessions(repo)
    assert {s.starts_at.weekday() for s in after} == {2, 4}
    kept = next(s for s in after if s.starts_at == datetime(2026, 10, 14, 7, 30))
    assert kept.id == before[kept.starts_at]                 # cùng giờ → giữ id
    assert kept.reminder_sent_at is not None and kept.notes == "Thầy An"   # nhắc không đổi → không nhắc lại
    saved.remind_before_minutes = 30
    repo.update_course(saved)
    assert all(s.reminder_sent_at is None for s in sessions(repo))


def test_rename_course_moves_linked_subject_and_color(repo):
    saved = repo.add_course(course())
    repo.set_subject_color("Giải tích 1", "purple")
    deadline = repo.add_deadline(Deadline("Bài 1", NOW + timedelta(days=2), subject="Giải tích 1"))
    other = repo.add_deadline(Deadline("Bài khác", NOW + timedelta(days=2), subject="Vật lý"))
    saved.name = "Giải tích A"
    repo.update_course(saved)
    assert repo.get_deadline(deadline.id).subject == "Giải tích A"
    assert repo.get_deadline(other.id).subject == "Vật lý"
    assert repo.subject_colors()["Giải tích A"] == "purple"
    assert {s.title for s in sessions(repo)} == {"Giải tích A"}


def test_duplicate_course_name_rejected(repo):
    repo.add_course(course())
    with pytest.raises(ValidationError):
        repo.add_course(course(name="giai tich 1"))    # không phân biệt dấu / hoa thường


def test_skip_session_survives_later_edits(repo):
    saved = repo.add_course(course())
    target = sessions(repo)[1]
    assert repo.skip_session(target.id)
    assert repo.get_item(target.id) is None
    assert repo.get_course(saved.id).skipped == (target.starts_at.date(),)
    updated = repo.get_course(saved.id)
    updated.room = "B1"
    repo.update_course(updated)
    assert target.starts_at not in {s.starts_at for s in sessions(repo)}
    assert len(sessions(repo)) == 5


def test_delete_course_removes_sessions_only(repo):
    saved = repo.add_course(course())
    deadline = repo.add_deadline(Deadline("Bài 1", NOW + timedelta(days=2), subject="Giải tích 1"))
    assert repo.delete_course(saved.id)
    assert sessions(repo) == [] and repo.get_course(saved.id) is None
    assert repo.get_deadline(deadline.id) is not None


def test_subject_links_search_and_suggestions(repo):
    saved = repo.add_course(course())
    repo.add_deadline(Deadline("Bài 1", NOW + timedelta(days=2), subject="Giải tích 1"))
    repo.add_study_time(StudyTime("Ôn cũ", NOW - timedelta(days=2), NOW - timedelta(days=2, hours=-1), subject="Giải tích 1"))
    repo.add_study_time(StudyTime("Ôn", NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1), subject="Giải tích 1"))
    assert "Giải tích 1" in repo.subjects()
    assert [d.title for d in repo.items_for_subject("Giải tích 1", "deadline")] == ["Bài 1"]
    assert [s.title for s in repo.items_for_subject("Giải tích 1", "study", since=NOW)] == ["Ôn"]
    assert all(not isinstance(i, ClassSession) for i in repo.search("giai tich"))
    assert repo.next_session(saved.id, NOW).starts_at == datetime(2026, 10, 14, 7, 30)
    assert repo.next_session(saved.id, datetime(2026, 11, 1)) is None


def test_class_tone_and_notification(repo):
    repo.add_course(course())
    first = sessions(repo)[0]
    colors = ColorResolver("subject", {"Giải tích 1": "pink"})
    assert colors.tone(first) == "pink"
    assert ColorResolver("type").tone(first) == "purple"
    note = item_notification(first, first.starts_at - timedelta(minutes=15))
    assert note.title == "🏫 Giải tích 1" and "📍 A2.301 · Cơ sở 1" in note.body


def test_course_reminders_use_existing_reminder_flow(repo):
    repo.add_course(course(remind_before_minutes=10))
    at = datetime(2026, 10, 14, 7, 21)
    assert [i.starts_at for i in repo.due_reminders(at)] == [datetime(2026, 10, 14, 7, 30)]


# ------------------------------------------------------------------ Migration v6 → v7
def test_migration_v7_keeps_existing_items(tmp_path):
    path = tmp_path / "norilan.db"
    raw = sqlite3.connect(path)
    raw.executescript("\n".join(db.MIGRATIONS[:6]) + "\nPRAGMA user_version = 6;")
    raw.execute("INSERT INTO items (kind,title,starts_at,status,created_at,updated_at) "
                "VALUES ('deadline','Lab','2026-10-20T23:59:00','todo','2026-10-01T08:00:00','2026-10-01T08:00:00')")
    raw.execute("INSERT INTO items (kind,title,starts_at,ends_at,deadline_id,created_at,updated_at) "
                "VALUES ('study','Ôn','2026-10-19T09:00:00','2026-10-19T10:00:00',1,'2026-10-01T08:00:00','2026-10-01T08:00:00')")
    raw.execute("INSERT INTO attachments (item_id,kind,target,created_at) VALUES (1,'url','https://x.edu','2026-10-01T08:00:00')")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert schema_version(conn) == SCHEMA_VERSION == 7
    from app.repository import Repository

    repo = Repository(conn)
    assert [a.target for a in repo.get_deadline(1).attachments] == ["https://x.edu"]
    assert repo.get_study_time(2).deadline_id == 1
    saved = repo.add_course(course())
    repo.delete_deadline(1)
    assert repo.get_study_time(2).deadline_id is None          # ON DELETE SET NULL vẫn còn sau khi dựng lại bảng
    assert repo.delete_course(saved.id) and repo.count_items()["class"] == 0   # ON DELETE CASCADE
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


# ------------------------------------------------------------------ Giao diện
@pytest.fixture
def window(qapp, repo, launcher, prompts_stub):
    from app.files import FileService
    from app.settings import Settings
    from app.ui.main_window import MainWindow

    win = MainWindow(repo, Settings(), files=FileService(launcher), clock=lambda: NOW)
    yield win
    win.close()
    win.deleteLater()


def accept_with(fill):
    def run(dialog):
        fill(dialog)
        dialog.accept()
        return dialog.result() == 1

    return run


def test_new_course_from_sidebar_fills_calendar_and_opens_course_page(window, repo):
    from app.ui.dialogs import CourseDialog

    assert window.sidebar.add_course_button is not None       # chưa có môn: nút viền đứt

    def fill(dlg: CourseDialog):
        dlg.title_edit.setText("Vật lý")
        for button in dlg.day_buttons:
            button.setChecked(False)
        dlg.day_buttons[1].setChecked(True)
        dlg.room.setText("C3")
        dlg.weeks.setValue(2)

    window._exec_dialog = accept_with(fill)
    window.sidebar.new_course_requested.emit()
    (saved,) = repo.list_courses()
    assert (saved.name, saved.weekdays, saved.room, saved.weeks) == ("Vật lý", (1,), "C3", 2)
    assert [b.name.fullText() for b in window.sidebar.course_buttons] == ["Vật lý"]
    assert window.panel.course_id() == saved.id
    assert window.sidebar.course_buttons[0].isChecked()
    assert "Vật lý" in repo.subject_colors()
    assert window.month_view.cell_for(date(2026, 10, 13)).chip_texts() == ["🏫 07:30 Vật lý"]


def test_course_page_lists_linked_items_and_prefills_subject(window, repo):
    from app.ui.dialogs import DeadlineDialog

    saved = repo.add_course(course())
    repo.add_deadline(Deadline("Bài 1", NOW + timedelta(days=2), subject="Giải tích 1"))
    window.refresh()
    window.sidebar.course_buttons[0].click()
    view = window.panel.course_view
    assert view.field_values()["Phòng học"] == "A2.301"
    assert view.field_values()["Buổi tới"].startswith("07:30 – 09:30")
    assert [c.title.fullText() for c in view.cards] == ["Bài 1"]

    opened = []
    window._exec_dialog = lambda dlg: opened.append(dlg) or False
    view.add_deadline_requested.emit(saved.name)
    assert isinstance(opened[0], DeadlineDialog) and opened[0].subject.currentText() == "Giải tích 1"


def test_delete_session_asks_skip_or_whole_course(window, repo, prompts_stub):
    saved = repo.add_course(course())
    window.refresh()
    first = sessions(repo)[0]
    prompts_stub.class_action = "skip"
    window.delete_item(first.id)
    assert prompts_stub.class_asked == ["Giải tích 1"]
    assert repo.get_item(first.id) is None and repo.get_course(saved.id) is not None
    prompts_stub.class_action = "course"
    window.delete_item(sessions(repo)[0].id)
    assert repo.list_courses() == [] and sessions(repo) == []
    assert window.sidebar.add_course_button is not None


def test_session_detail_links_to_course(window, repo):
    saved = repo.add_course(course())
    window.refresh()
    window.open_item(sessions(repo)[0].id)
    detail = window.panel.detail
    assert detail.field_values()["Phòng học"] == "A2.301"
    detail.course_card.clicked.emit()
    assert window.panel.course_id() == saved.id
