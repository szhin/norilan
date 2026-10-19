from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Event, Deadline, DeadlineStatus
from app.reminders import DIGEST_STATE_KEY, ReminderService, digest_notification, item_notification
from app.settings import Settings

TOMORROW_9 = datetime(2026, 10, 15, 9)


@pytest.fixture
def settings():
    return {"value": Settings(daily_digest_time="20:00")}


@pytest.fixture
def service(repo, settings):
    return ReminderService(repo, lambda: settings["value"])


def test_digest_waits_for_configured_time(service, repo):
    repo.add_deadline(Deadline("Lab 2", TOMORROW_9))
    assert service.check(datetime(2026, 10, 14, 19, 59)) == []
    notes = service.check(datetime(2026, 10, 14, 20, 0))
    assert [n.title for n in notes] == ["Ngày mai bạn có 1 việc"]
    assert notes[0].body == "• 09:00 Lab 2" and notes[0].url == "norilan://day/2026-10-15"


def test_digest_only_once_per_day_even_after_restart(service, repo, settings):
    repo.add_deadline(Deadline("Lab 2", TOMORROW_9))
    assert len(service.check(datetime(2026, 10, 14, 20, 1))) == 1
    assert service.check(datetime(2026, 10, 14, 20, 2)) == []
    restarted = ReminderService(repo, lambda: settings["value"])  # app tắt rồi mở lại
    assert restarted.check(datetime(2026, 10, 14, 21, 0)) == []
    assert repo.get_state(DIGEST_STATE_KEY) == "2026-10-14"


def test_digest_catch_up_when_app_opened_late(service, repo):
    repo.add_deadline(Deadline("Lab 2", TOMORROW_9))
    assert len(service.check(datetime(2026, 10, 14, 23, 30))) == 1


def test_digest_counts_tasks_and_events_but_not_done(service, repo):
    repo.add_deadline(Deadline("A", TOMORROW_9))
    done = repo.add_deadline(Deadline("B", TOMORROW_9 + timedelta(hours=1)))
    repo.set_deadline_status(done.id, DeadlineStatus.DONE)
    repo.add_event(Event("Họp", TOMORROW_9 + timedelta(hours=2), TOMORROW_9 + timedelta(hours=3)))
    note = service.check(datetime(2026, 10, 14, 20, 0))[0]
    assert note.title == "Ngày mai bạn có 2 việc"


def test_digest_skipped_when_nothing_or_disabled(service, repo, settings):
    assert service.check(datetime(2026, 10, 14, 20, 0)) == []
    repo.add_deadline(Deadline("A", datetime(2026, 10, 16, 9)))
    settings["value"] = Settings(daily_digest_enabled=False)
    assert service.check(datetime(2026, 10, 15, 20, 0)) == []


def test_digest_body_is_short():
    items = [Deadline(f"Việc {i}", TOMORROW_9 + timedelta(minutes=i)) for i in range(7)]
    lines = digest_notification(items, TOMORROW_9.date()).body.splitlines()
    assert len(lines) == 4 and lines[-1] == "… và 4 việc khác"


def test_item_reminder_sent_once(service, repo):
    task = repo.add_deadline(Deadline("Lab 2", datetime(2026, 10, 14, 23, 59), subject="PPT", remind_before_minutes=180))
    now = datetime(2026, 10, 14, 21, 0)
    notes = service.check(now)
    assert len(notes) == 1 and notes[0].url == f"norilan://item/{task.id}"
    assert notes[0].title == "⏰ Lab 2"
    assert "Hạn nộp 23:59 hôm nay (còn 3 giờ)" in notes[0].body and "PPT" in notes[0].body
    assert service.check(now + timedelta(minutes=1)) == []


@pytest.mark.parametrize("remind", [15, 2 * 60, 1440])  # phút / giờ / ngày
def test_item_reminder_fires_exactly_at_start_minus_remind(service, repo, remind):
    due = datetime(2026, 10, 20, 9)
    repo.add_deadline(Deadline("Lab 3", due, remind_before_minutes=remind))
    moment = due - timedelta(minutes=remind)
    assert service.check(moment - timedelta(seconds=1)) == []
    assert len(service.check(moment)) == 1
    assert service.check(moment + timedelta(seconds=20)) == []  # lần QTimer kế tiếp: không gửi lặp


def test_item_reminder_recomputed_after_rescheduling(service, repo):
    task = repo.add_deadline(Deadline("Lab 3", datetime(2026, 10, 20, 9), remind_before_minutes=30))
    assert len(service.check(datetime(2026, 10, 20, 8, 30))) == 1
    task.due_at = datetime(2026, 10, 20, 11)  # dời hạn sau khi đã nhắc → nhắc lại theo giờ mới
    repo.update_deadline(task)
    assert service.check(datetime(2026, 10, 20, 10, 29)) == []
    notes = service.check(datetime(2026, 10, 20, 10, 30))
    assert len(notes) == 1 and notes[0].body == "Hạn nộp 11:00 hôm nay (còn 30 phút)"
    assert service.check(datetime(2026, 10, 20, 10, 31)) == []


def test_changing_remind_before_sending_uses_new_moment(service, repo):
    event = repo.add_event(Event("Họp", datetime(2026, 10, 20, 14), datetime(2026, 10, 20, 15),
                                 remind_before_minutes=30))
    event.remind_before_minutes = 120
    repo.update_event(event)
    assert service.check(datetime(2026, 10, 20, 11, 59)) == []
    assert len(service.check(datetime(2026, 10, 20, 12, 0))) == 1
    assert service.check(datetime(2026, 10, 20, 13, 30)) == []  # mốc cũ không còn tác dụng


def test_done_or_deleted_items_never_remind(service, repo):
    due = datetime(2026, 10, 20, 9)
    task = repo.add_deadline(Deadline("Đã nộp", due, remind_before_minutes=30))
    repo.set_deadline_status(task.id, DeadlineStatus.DONE)
    gone = repo.add_event(Event("Huỷ", due, due + timedelta(hours=1), remind_before_minutes=30))
    assert repo.delete_item(gone.id)
    assert service.check(datetime(2026, 10, 20, 8, 45)) == []
    repo.set_deadline_status(task.id, DeadlineStatus.TODO)  # mở lại task trước hạn → vẫn được nhắc
    assert [n.url for n in service.check(datetime(2026, 10, 20, 8, 46))] == [f"norilan://item/{task.id}"]


def test_app_reopened_after_reminder_moment(repo, settings):
    due = datetime(2026, 10, 20, 9)
    pending = repo.add_deadline(Deadline("Chưa tới hạn", due, remind_before_minutes=60))
    started = repo.add_event(Event("Đã bắt đầu", datetime(2026, 10, 20, 8, 10), due, remind_before_minutes=10))
    repo.add_deadline(Deadline("Quá cũ", datetime(2026, 10, 20, 7), remind_before_minutes=10))  # quá grace 1 giờ
    service = ReminderService(repo, lambda: settings["value"])  # app tắt từ trước 7:00, mở lại 8:40
    notes = {n.url: n.body for n in service.check(datetime(2026, 10, 20, 8, 40))}
    assert notes == {
        f"norilan://item/{pending.id}": "Hạn nộp 09:00 hôm nay (còn 20 phút)",
        f"norilan://item/{started.id}": "Bắt đầu 08:10 hôm nay (đã qua 30 phút)",
    }
    restarted = ReminderService(repo, lambda: settings["value"])
    assert restarted.check(datetime(2026, 10, 20, 8, 41)) == []


def test_notification_text_when_late():
    task = Deadline("Lab", datetime(2026, 10, 14, 10), id=1)
    assert item_notification(task, datetime(2026, 10, 14, 10, 0, 20)).body == "Hạn nộp 10:00 hôm nay (ngay bây giờ)"
    assert item_notification(task, datetime(2026, 10, 14, 11, 1)).body == "Hạn nộp 10:00 hôm nay (đã qua 1 giờ)"


def test_event_notification_text():
    event = Event("Họp nhóm", datetime(2026, 10, 15, 19), datetime(2026, 10, 15, 20), location="Thư viện", id=3)
    note = item_notification(event, datetime(2026, 10, 15, 18, 30))
    assert note.title == "📅 Họp nhóm"
    assert note.body == "Bắt đầu 19:00 hôm nay (còn 30 phút)\n📍 Thư viện"
    tomorrow = item_notification(event, datetime(2026, 10, 14, 19, 0))
    assert "ngày mai (còn 1 ngày)" in tomorrow.body


def test_two_processes_on_same_db_never_send_same_reminder_twice(tmp_path, settings):
    """GUI và tiến trình nền (hai kết nối cùng một file DB) không cùng bắn một lời nhắc."""
    from app.db import connect
    from app.repository import Repository

    path = tmp_path / "n.db"
    c1, c2 = connect(path), connect(path)
    r1, r2 = Repository(c1), Repository(c2)
    now = datetime(2026, 10, 14, 10, 0)
    r1.add_deadline(Deadline("Lab", now + timedelta(minutes=30), remind_before_minutes=60))

    s1, s2 = ReminderService(r1, lambda: settings["value"]), ReminderService(r2, lambda: settings["value"])
    first = [n for n in s1.check(now) if n.key.startswith("item-")]
    second = [n for n in s2.check(now) if n.key.startswith("item-")]
    assert len(first) == 1 and second == []
    c1.close(); c2.close()


def test_claim_reminder_is_atomic_and_reset_on_reschedule(repo, now):
    task = repo.add_deadline(Deadline("A", now + timedelta(minutes=30), remind_before_minutes=60))
    assert repo.claim_reminder(task.id, now) is True
    assert repo.claim_reminder(task.id, now) is False
    t = repo.get_deadline(task.id)
    t.due_at = now + timedelta(minutes=45)
    repo.update_deadline(t)
    assert repo.claim_reminder(task.id, now) is True  # đổi giờ → được nhắc lại
