"""Dữ liệu mẫu để chạy thử (python main.py --seed)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import Attachment, AttachmentKind, Event, Priority, Task, TaskStatus
from app.repository import Repository


def seed_demo(repo: Repository, today: datetime) -> int:
    """Thêm vài task/event quanh ngày `today`. Trả về số mục đã thêm."""
    d0 = today.date()

    def at(days: int, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(d0 + timedelta(days=days), time(hh, mm))

    items = [
        Task("Bài tập Phương pháp tính – Lab 2", at(1, 23, 59), subject="Phương pháp tính",
             priority=Priority.HIGH, remind_before_minutes=180,
             attachments=[Attachment("https://lms.example.edu/lab2", AttachmentKind.URL, "Đề bài trên LMS")]),
        Task("Đọc chương 3 Giải tích", at(1, 9), subject="Toán cao cấp", notes="Làm bài 3.1 → 3.12"),
        Task("Nộp báo cáo nhóm", at(-1, 17), subject="Kỹ năng mềm", status=TaskStatus.IN_PROGRESS),
        Task("Ôn từ vựng tiếng Anh", at(3, 20), subject="Tiếng Anh", priority=Priority.LOW),
    ]
    for task in items:
        repo.add_task(task)
    repo.add_event(Event("Họp nhóm đồ án", at(0, 19), at(0, 20, 30), location="Thư viện tầng 3",
                         remind_before_minutes=30))
    repo.add_event(Event("Khám răng", at(2, 8, 30), at(2, 9, 30), location="Nha khoa Kim"))
    return len(items) + 2
