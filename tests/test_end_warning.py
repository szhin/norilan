"""Toast "Chuẩn bị hết thời gian làm bài" 10 phút trước ends_at của Study time + cờ lưu trong DB (migration v5)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from app import db
from app.db import SCHEMA_VERSION, connect, schema_version
from app.models import Deadline, Event, StudyTime
from app.reminders import ReminderService
from app.repository import Repository
from app.settings import Settings

START = datetime(2026, 10, 14, 15, 30)
END = datetime(2026, 10, 14, 16, 30)
WARN_AT = END - timedelta(minutes=10)
OLD_STUDY_SQL = (
    "INSERT INTO items (kind,title,starts_at,ends_at,created_at,updated_at) VALUES "
    "('study','Cũ','2026-10-14T15:30:00','2026-10-14T16:30:00','2026-10-01T08:00:00','2026-10-01T08:00:00')"
)


@pytest.fixture
def service(repo):
    # Tắt toast tổng hợp buổi tối để chỉ xét cảnh báo
    return ReminderService(repo, lambda: Settings(daily_digest_enabled=False))


def study(repo, title="Ôn thi", start=START, end=END, **kw):
    return repo.add_study_time(StudyTime(title, start, end, **kw))


def warnings(service, now):
    return [n for n in service.check(now) if n.key.startswith("study-end-")]


# ------------------------------------------------------------------ Nội dung & thời điểm
def test_fires_exactly_ten_minutes_before_end_with_link(repo, service):
    s = study(repo)
    assert warnings(service, WARN_AT - timedelta(seconds=1)) == []
    (note,) = warnings(service, WARN_AT)
    assert note.title == "Chuẩn bị hết thời gian làm bài: Ôn thi"
    assert note.url == f"norilan://item/{s.id}"
    assert "16:30" in note.body and "còn 10 phút" in note.body


def test_fires_only_once_and_flag_is_persisted(repo, service):
    s = study(repo)
    assert len(warnings(service, WARN_AT)) == 1
    assert warnings(service, WARN_AT + timedelta(seconds=20)) == []
    assert warnings(service, END - timedelta(seconds=1)) == []
    assert repo.get_study_time(s.id).end_warned_at == WARN_AT
    # Service mới (như mở lại app) vẫn không gửi lại vì cờ nằm trong DB
    again = ReminderService(repo, lambda: Settings(daily_digest_enabled=False))
    assert warnings(again, WARN_AT + timedelta(minutes=1)) == []


def test_not_sent_after_the_session_ended(repo, service):
    study(repo)
    assert warnings(service, END) == []
    assert warnings(service, END + timedelta(hours=1)) == []


def test_not_sent_before_the_boundary_even_if_started(repo, service):
    study(repo)
    assert warnings(service, START) == []
    assert warnings(service, START + timedelta(minutes=49)) == []


@pytest.mark.parametrize("minutes", [1, 5, 9])
def test_duration_under_ten_minutes_never_warns(repo, service, minutes):
    start = datetime(2026, 10, 14, 9, 0)
    study(repo, start=start, end=start + timedelta(minutes=minutes, seconds=59 if minutes == 9 else 0))
    for offset in range(0, minutes * 60 + 60, 20):
        assert warnings(service, start + timedelta(seconds=offset)) == []


def test_duration_exactly_ten_minutes_warns_at_start(repo, service):
    start = datetime(2026, 10, 14, 9, 0)
    study(repo, start=start, end=start + timedelta(minutes=10))
    assert warnings(service, start - timedelta(seconds=1)) == []
    assert len(warnings(service, start)) == 1


def test_app_started_with_less_than_ten_minutes_left_sends_once_immediately(repo, service):
    study(repo)
    late = END - timedelta(minutes=4)                      # app vừa mở: StudyTime vẫn đang chạy, chưa có cờ
    (note,) = warnings(service, late)
    assert "còn 4 phút" in note.body
    assert warnings(service, late + timedelta(seconds=20)) == []


def test_overlapping_study_times_each_get_their_own_toast(repo, service):
    a = study(repo, "Toán", end=datetime(2026, 10, 14, 16, 30))
    b = study(repo, "Lý", start=datetime(2026, 10, 14, 15, 45), end=datetime(2026, 10, 14, 16, 35))
    notes = warnings(service, datetime(2026, 10, 14, 16, 26))   # cả hai đều còn ≤ 10 phút
    assert sorted(n.title for n in notes) == ["Chuẩn bị hết thời gian làm bài: Lý",
                                              "Chuẩn bị hết thời gian làm bài: Toán"]
    assert len({n.key for n in notes}) == 2
    assert {n.url for n in notes} == {f"norilan://item/{a.id}", f"norilan://item/{b.id}"}


def test_only_study_time_never_deadline_or_event(repo, service):
    repo.add_deadline(Deadline("D", END))
    repo.add_event(Event("E", START, END))
    assert warnings(service, WARN_AT) == []
    assert not [n for n in service.check(WARN_AT) if n.title.startswith("Chuẩn bị")]


# ------------------------------------------------------------------ Cờ được đặt lại khi sửa ends_at
def test_flag_reset_only_when_ends_at_changes(repo, service):
    s = study(repo)
    assert len(warnings(service, WARN_AT)) == 1
    flagged = repo.get_study_time(s.id)
    flagged.title = "Đổi tên"                                  # sửa tên: giữ cờ
    repo.update_study_time(flagged)
    assert repo.get_study_time(s.id).end_warned_at is not None
    flagged = repo.get_study_time(s.id)
    flagged.notes = "ghi chú"
    flagged.starts_at = START - timedelta(minutes=5)           # đổi giờ bắt đầu: vẫn giữ cờ
    repo.update_study_time(flagged)
    assert repo.get_study_time(s.id).end_warned_at is not None

    edited = repo.get_study_time(s.id)
    edited.ends_at = END + timedelta(minutes=30)               # đổi ends_at: xoá cờ
    repo.update_study_time(edited)
    assert repo.get_study_time(s.id).end_warned_at is None
    new_warn = edited.ends_at - timedelta(minutes=10)
    assert warnings(service, WARN_AT + timedelta(minutes=1)) == []   # chưa tới mốc mới
    (note,) = warnings(service, new_warn)                      # tới mốc mới → cảnh báo lại
    assert "17:00" in note.body
    assert warnings(service, new_warn + timedelta(minutes=1)) == []


def test_claim_is_atomic(repo):
    s = study(repo)
    assert repo.claim_end_warning(s.id, WARN_AT) is True
    assert repo.claim_end_warning(s.id, WARN_AT) is False


# ------------------------------------------------------------------ Migration v5
def make_db_at(path, version: int) -> None:
    raw = sqlite3.connect(path)
    raw.executescript("\n".join(db.MIGRATIONS[:version]) + f"\nPRAGMA user_version = {version};")
    raw.commit()
    raw.close()


def test_migration_v5_adds_nullable_flag_and_keeps_study_data(tmp_path):
    path = tmp_path / "norilan.db"
    make_db_at(path, 4)
    raw = sqlite3.connect(path)
    raw.execute(OLD_STUDY_SQL)
    raw.commit()
    raw.close()
    conn = connect(path)
    assert schema_version(conn) == SCHEMA_VERSION == 8
    old = Repository(conn).get_study_time(1)
    assert old.title == "Cũ" and old.end_warned_at is None
    assert list((tmp_path / "backups").glob("truoc-khi-nang-cap-v4-*.zip"))   # tự sao lưu trước khi nâng
    conn.close()
    connect(path).close()                                                      # idempotent: không sao lưu thêm
    assert len(list((tmp_path / "backups").glob("*.zip"))) == 1


def test_old_study_time_already_running_is_warned_once_after_upgrade(tmp_path):
    path = tmp_path / "norilan.db"
    make_db_at(path, 4)
    raw = sqlite3.connect(path)
    raw.execute(OLD_STUDY_SQL)
    raw.commit()
    raw.close()
    repo = Repository(connect(path))
    service = ReminderService(repo, lambda: Settings(daily_digest_enabled=False))
    assert len(warnings(service, END - timedelta(minutes=3))) == 1
    assert warnings(service, END - timedelta(minutes=2)) == []


# ------------------------------------------------------------------ Chạy ở khay: không cần widget nào
def test_controller_delivers_toast_with_window_hidden_and_link_opens_study(qapp, repo, launcher, prompts_stub):
    from app import paths
    from app.files import FileService
    from app.notifier import RecordingNotifier
    from app.ui.app_controller import AppController

    s = study(repo)
    notifier = RecordingNotifier()
    c = AppController(qapp, repo, Settings(daily_digest_enabled=False), settings_path=paths.settings_path(),
                      files=FileService(launcher), notifier=notifier, clock=lambda: WARN_AT, use_tray=False)
    try:
        c.window.hide()                                        # như đang thu xuống khay
        assert not c.window.panel.is_showing_detail() and not c.window.overlay.is_open()
        c.check_reminders()
        (note,) = [n for n in notifier.shown if n.key.startswith("study-end-")]
        assert note.title == "Chuẩn bị hết thời gian làm bài: Ôn thi"
        c.check_reminders()
        assert len([n for n in notifier.shown if n.key.startswith("study-end-")]) == 1
        c.open_url(note.url)                                   # bấm toast → mở đúng Study time
        assert c.window.panel.detail_item_id() == s.id
    finally:
        c.shutdown()
        c.window.quit_app()
