from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models import (
    Attachment,
    AttachmentKind,
    Event,
    Priority,
    Task,
    TaskStatus,
    ValidationError,
)
from app.repository import NotFoundError


def dt(day: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm)


DOCX = r"C:\Users\an\Documents\BaiTap\lab2.docx"


# ------------------------------------------------------------------ Task CRUD
def test_add_and_get_task_roundtrip(repo):
    task = repo.add_task(
        Task(
            "  Lab 2  ", dt(15, 23, 59), priority=Priority.HIGH, subject="Phương pháp tính",
            notes="Bài 2", remind_before_minutes=60,
            attachments=[Attachment(DOCX), Attachment("https://lms.edu/x", AttachmentKind.URL)],
        )
    )
    assert task.id is not None
    loaded = repo.get_task(task.id)
    assert loaded == task
    assert loaded.title == "Lab 2"  # đã strip
    assert loaded.due_at == dt(15, 23, 59)
    assert loaded.status is TaskStatus.TODO and loaded.priority is Priority.HIGH
    assert [a.name for a in loaded.attachments] == ["lab2.docx", "https://lms.edu/x"]
    assert [a.position for a in loaded.attachments] == [0, 1]
    assert loaded.created_at is not None


def test_empty_title_rejected(repo):
    with pytest.raises(ValidationError):
        repo.add_task(Task("   ", dt(15)))


def test_update_task_and_sync_attachments(repo):
    task = repo.add_task(
        Task("A", dt(15), attachments=[Attachment(DOCX), Attachment(r"C:\old.pdf"), Attachment(r"C:\drop.txt")])
    )
    keep, relink, _drop = task.attachments
    relink.target = r"D:\moved\old.pdf"
    task.attachments = [relink, keep, Attachment(r"C:\Folder", AttachmentKind.FOLDER)]
    task.title = "A (sửa)"
    task.status = TaskStatus.IN_PROGRESS

    updated = repo.update_task(task)
    assert updated.title == "A (sửa)" and updated.status is TaskStatus.IN_PROGRESS
    assert [a.target for a in updated.attachments] == [r"D:\moved\old.pdf", DOCX, r"C:\Folder"]
    assert updated.attachments[0].id == relink.id  # sửa tại chỗ, không tạo mới
    assert updated.attachments[2].kind is AttachmentKind.FOLDER


def test_update_missing_task_raises(repo):
    ghost = Task("x", dt(15), id=999)
    with pytest.raises(NotFoundError):
        repo.update_task(ghost)


def test_update_task_does_not_touch_event(repo):
    ev = repo.add_event(Event("E", dt(15, 9), dt(15, 10)))
    with pytest.raises(NotFoundError):
        repo.update_task(Task("x", dt(15), id=ev.id))


def test_set_task_status(repo):
    task = repo.add_task(Task("A", dt(15)))
    repo.set_task_status(task.id, TaskStatus.DONE)
    assert repo.get_task(task.id).is_done
    with pytest.raises(NotFoundError):
        repo.set_task_status(12345, TaskStatus.DONE)


def test_delete_cascades_attachments(repo, conn):
    task = repo.add_task(Task("A", dt(15), attachments=[Attachment(DOCX)]))
    assert repo.delete_item(task.id) is True
    assert repo.get_item(task.id) is None
    assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    assert repo.delete_item(task.id) is False


# ------------------------------------------------------------------ Attachment
def test_attachment_add_update_remove(repo):
    task = repo.add_task(Task("A", dt(15), attachments=[Attachment(DOCX)]))
    added = repo.add_attachment(task.id, Attachment(r"C:\b.pdf"))
    assert added.position == 1 and added.item_id == task.id

    added.target = r"E:\new\b.pdf"
    assert repo.update_attachment(added).target == r"E:\new\b.pdf"

    assert repo.remove_attachment(added.id) is True
    assert [a.target for a in repo.list_attachments(task.id)] == [DOCX]


def test_add_attachment_to_missing_task(repo):
    with pytest.raises(NotFoundError):
        repo.add_attachment(999, Attachment(DOCX))


def test_attachment_empty_target_rejected(repo):
    task = repo.add_task(Task("A", dt(15)))
    with pytest.raises(ValidationError):
        repo.add_attachment(task.id, Attachment("  "))


def test_attachment_name_handles_both_separators():
    assert Attachment(r"C:\a\b\c.docx").name == "c.docx"
    assert Attachment("/home/a/c.pdf").name == "c.pdf"
    assert Attachment(r"C:\Thu muc\\", AttachmentKind.FOLDER).name == "Thu muc"
    assert Attachment(DOCX, display_name="Đề bài").name == "Đề bài"


# ------------------------------------------------------------------ Event
def test_event_crud(repo):
    ev = repo.add_event(Event("Họp nhóm", dt(15, 19), dt(15, 20, 30), location="Thư viện"))
    assert repo.get_event(ev.id).location == "Thư viện"
    assert repo.get_task(ev.id) is None

    ev.location = "Phòng B2"
    ev.ends_at = dt(15, 21)
    updated = repo.update_event(ev)
    assert updated.location == "Phòng B2" and updated.ends_at == dt(15, 21)


def test_event_end_before_start_rejected(repo):
    with pytest.raises(ValidationError, match="kết thúc"):
        repo.add_event(Event("E", dt(15, 10), dt(15, 9)))


def test_negative_reminder_rejected(repo):
    with pytest.raises(ValidationError):
        repo.add_task(Task("A", dt(15), remind_before_minutes=-5))


# ------------------------------------------------------------------ Truy vấn lịch
def test_items_on_day_sorted_and_mixed(repo):
    repo.add_task(Task("Muộn", dt(15, 23, 59)))
    repo.add_event(Event("Sáng", dt(15, 8), dt(15, 9)))
    repo.add_task(Task("Trưa", dt(15, 12)))
    repo.add_task(Task("Hôm khác", dt(16, 0)))  # đúng 00:00 hôm sau → không thuộc ngày 15
    titles = [i.title for i in repo.items_on(date(2026, 10, 15))]
    assert titles == ["Sáng", "Trưa", "Muộn"]


def test_event_spanning_midnight_appears_on_both_days(repo):
    repo.add_event(Event("Qua đêm", dt(15, 23), dt(16, 1)))
    repo.add_event(Event("Hết lúc 0h", dt(15, 22), dt(16, 0)))
    assert {i.title for i in repo.items_on(date(2026, 10, 15))} == {"Qua đêm", "Hết lúc 0h"}
    assert [i.title for i in repo.items_on(date(2026, 10, 16))] == ["Qua đêm"]


def test_items_between_can_hide_done(repo):
    t = repo.add_task(Task("Xong rồi", dt(15, 9)))
    repo.set_task_status(t.id, TaskStatus.DONE)
    repo.add_task(Task("Chưa", dt(15, 10)))
    assert len(repo.items_on(date(2026, 10, 15))) == 2
    assert [i.title for i in repo.items_on(date(2026, 10, 15), include_done=False)] == ["Chưa"]


def test_tasks_due_on_excludes_events_and_done(repo):
    repo.add_task(Task("T1", dt(15, 9)))
    done = repo.add_task(Task("T2", dt(15, 10)))
    repo.set_task_status(done.id, TaskStatus.DONE)
    repo.add_event(Event("E", dt(15, 11), dt(15, 12)))
    assert [t.title for t in repo.tasks_due_on(date(2026, 10, 15))] == ["T1"]


def test_overdue_and_upcoming(repo, now):
    repo.add_task(Task("Trễ", now - timedelta(days=1)))
    finished = repo.add_task(Task("Trễ nhưng xong", now - timedelta(days=2)))
    repo.set_task_status(finished.id, TaskStatus.DONE)
    repo.add_task(Task("Tuần này", now + timedelta(days=3)))
    repo.add_task(Task("Xa", now + timedelta(days=10)))
    repo.add_event(Event("Đang họp", now - timedelta(minutes=30), now + timedelta(minutes=30)))

    assert [t.title for t in repo.overdue_tasks(now)] == ["Trễ"]
    assert [i.title for i in repo.upcoming(now)] == ["Đang họp", "Tuần này"]


# ------------------------------------------------------------------ Tìm kiếm
@pytest.fixture
def searchable(repo):
    repo.add_task(Task("Bài tập Giải tích", dt(15), subject="Toán cao cấp"))
    repo.add_task(Task("Essay", dt(16), subject="Tiếng Anh", notes="Viết 500 từ, 100% tự làm",
                       status=TaskStatus.IN_PROGRESS))
    repo.add_event(Event("Họp CLB", dt(17, 18), dt(17, 19), location="Nhà Văn hoá"))
    return repo


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("toan", ["Bài tập Giải tích"]),           # không dấu vẫn ra
        ("GIẢI TÍCH", ["Bài tập Giải tích"]),      # không phân biệt hoa thường
        ("500 từ", ["Essay"]),                     # tìm trong ghi chú
        ("van hoa", ["Họp CLB"]),                  # tìm trong địa điểm
        ("100%", ["Essay"]),                       # ký tự % được escape
        ("_", []),
        ("", ["Bài tập Giải tích", "Essay", "Họp CLB"]),
    ],
)
def test_search(searchable, query, expected):
    assert [i.title for i in searchable.search(query)] == expected


def test_search_filters(searchable):
    assert [i.title for i in searchable.search(status=TaskStatus.IN_PROGRESS)] == ["Essay"]
    assert [i.title for i in searchable.search(kind="event")] == ["Họp CLB"]
    assert searchable.search("toan", status=TaskStatus.DONE) == []


# ------------------------------------------------------------------ Nhắc việc
def test_due_reminders_window(repo, now):
    soon = repo.add_task(Task("Sắp đến hạn", now + timedelta(minutes=30), remind_before_minutes=60))
    repo.add_task(Task("Chưa tới giờ nhắc", now + timedelta(hours=3), remind_before_minutes=60))
    repo.add_task(Task("Không đặt nhắc", now + timedelta(minutes=10)))
    repo.add_task(Task("Quá cũ", now - timedelta(hours=5), remind_before_minutes=10))
    done = repo.add_task(Task("Đã xong", now + timedelta(minutes=5), remind_before_minutes=60))
    repo.set_task_status(done.id, TaskStatus.DONE)
    ev = repo.add_event(Event("Họp", now + timedelta(days=1), now + timedelta(days=1, hours=1),
                              remind_before_minutes=24 * 60))

    assert [i.id for i in repo.due_reminders(now)] == [soon.id, ev.id]

    repo.mark_reminder_sent(soon.id, now)
    assert [i.id for i in repo.due_reminders(now)] == [ev.id]


def test_reminder_resets_only_when_schedule_changes(repo, now):
    task = repo.add_task(Task("A", now + timedelta(minutes=30), remind_before_minutes=60))
    repo.mark_reminder_sent(task.id, now)

    task = repo.get_task(task.id)
    task.title = "A đổi tên"
    assert repo.update_task(task).reminder_sent_at == now  # chỉ đổi tên → giữ nguyên

    task.due_at = now + timedelta(minutes=45)
    assert repo.update_task(task).reminder_sent_at is None  # đổi hạn → nhắc lại

    repo.mark_reminder_sent(task.id, now)
    task = repo.get_task(task.id)
    task.remind_before_minutes = 120
    assert repo.update_task(task).reminder_sent_at is None  # đổi thời gian nhắc → nhắc lại


def test_subjects_most_recent_first(repo):
    repo.add_task(Task("a", dt(15), subject="Toán"))
    repo.add_task(Task("b", dt(16), subject="Lý"))
    repo.add_task(Task("c", dt(17)))
    repo.add_event(Event("e", dt(15, 9), dt(15, 10)))
    subjects = repo.subjects()
    assert set(subjects) == {"Toán", "Lý"} and "" not in subjects


def test_get_attachment(repo):
    task = repo.add_task(Task("A", dt(15), attachments=[Attachment(DOCX)]))
    att_id = task.attachments[0].id
    assert repo.get_attachment(att_id).target == DOCX
    assert repo.get_attachment(9999) is None


def test_event_color_roundtrip_and_validation(repo):
    ev = repo.add_event(Event("E", dt(15, 9), dt(15, 10), color="green"))
    assert repo.get_event(ev.id).color == "green"
    ev.color = "#12ab34"
    assert repo.update_event(ev).color == "#12ab34"
    with pytest.raises(ValidationError):
        repo.add_event(Event("E", dt(15, 9), dt(15, 10), color="xanh lá; DROP"))


def test_subject_colors_and_state(repo):
    repo.set_subject_color("Toán", "purple")
    repo.set_subject_color("Toán", "red")
    repo.set_subject_color("  ", "red")
    assert repo.subject_colors() == {"Toán": "red"}
    repo.set_subject_color("Toán", None)
    assert repo.subject_colors() == {}
    assert repo.get_state("x") is None and repo.get_state("x", "mặc định") == "mặc định"
    repo.set_state("x", "1")
    repo.set_state("x", "2")
    assert repo.get_state("x") == "2"
