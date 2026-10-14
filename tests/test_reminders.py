from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Event, Task, TaskStatus
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
    repo.add_task(Task("Lab 2", TOMORROW_9))
    assert service.check(datetime(2026, 10, 14, 19, 59)) == []
    notes = service.check(datetime(2026, 10, 14, 20, 0))
    assert [n.title for n in notes] == ["Ngày mai bạn có 1 việc"]
    assert notes[0].body == "• 09:00 Lab 2" and notes[0].url == "norilan://day/2026-10-15"


def test_digest_only_once_per_day_even_after_restart(service, repo, settings):
    repo.add_task(Task("Lab 2", TOMORROW_9))
    assert len(service.check(datetime(2026, 10, 14, 20, 1))) == 1
    assert service.check(datetime(2026, 10, 14, 20, 2)) == []
    restarted = ReminderService(repo, lambda: settings["value"])  # app tắt rồi mở lại
    assert restarted.check(datetime(2026, 10, 14, 21, 0)) == []
    assert repo.get_state(DIGEST_STATE_KEY) == "2026-10-14"


def test_digest_catch_up_when_app_opened_late(service, repo):
    repo.add_task(Task("Lab 2", TOMORROW_9))
    assert len(service.check(datetime(2026, 10, 14, 23, 30))) == 1


def test_digest_counts_tasks_and_events_but_not_done(service, repo):
    repo.add_task(Task("A", TOMORROW_9))
    done = repo.add_task(Task("B", TOMORROW_9 + timedelta(hours=1)))
    repo.set_task_status(done.id, TaskStatus.DONE)
    repo.add_event(Event("Họp", TOMORROW_9 + timedelta(hours=2), TOMORROW_9 + timedelta(hours=3)))
    note = service.check(datetime(2026, 10, 14, 20, 0))[0]
    assert note.title == "Ngày mai bạn có 2 việc"


def test_digest_skipped_when_nothing_or_disabled(service, repo, settings):
    assert service.check(datetime(2026, 10, 14, 20, 0)) == []
    repo.add_task(Task("A", datetime(2026, 10, 16, 9)))
    settings["value"] = Settings(daily_digest_enabled=False)
    assert service.check(datetime(2026, 10, 15, 20, 0)) == []


def test_digest_body_is_short():
    items = [Task(f"Việc {i}", TOMORROW_9 + timedelta(minutes=i)) for i in range(7)]
    lines = digest_notification(items, TOMORROW_9.date()).body.splitlines()
    assert len(lines) == 4 and lines[-1] == "… và 4 việc khác"


def test_item_reminder_sent_once(service, repo):
    task = repo.add_task(Task("Lab 2", datetime(2026, 10, 14, 23, 59), subject="PPT", remind_before_minutes=180))
    now = datetime(2026, 10, 14, 21, 0)
    notes = service.check(now)
    assert len(notes) == 1 and notes[0].url == f"norilan://item/{task.id}"
    assert notes[0].title == "⏰ Lab 2"
    assert "Hạn nộp 23:59 hôm nay (còn 3 giờ)" in notes[0].body and "PPT" in notes[0].body
    assert service.check(now + timedelta(minutes=1)) == []


def test_event_notification_text():
    event = Event("Họp nhóm", datetime(2026, 10, 15, 19), datetime(2026, 10, 15, 20), location="Thư viện", id=3)
    note = item_notification(event, datetime(2026, 10, 15, 18, 30))
    assert note.title == "📅 Họp nhóm"
    assert note.body == "Bắt đầu 19:00 hôm nay (còn 30 phút)\n📍 Thư viện"
    tomorrow = item_notification(event, datetime(2026, 10, 14, 19, 0))
    assert "ngày mai (còn 1 ngày)" in tomorrow.body
