"""Dữ liệu mẫu để chạy thử (python main.py --seed)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from app.models import Attachment, AttachmentKind, Deadline, DeadlineStatus, Event, Priority, StudyTime
from app.repository import Repository


def seed_demo(repo: Repository, today: datetime) -> int:
    """Thêm vài deadline/study time/event quanh ngày `today`. Trả về số mục đã thêm."""
    d0 = today.date()

    def at(days: int, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(d0 + timedelta(days=days), time(hh, mm))

    items = [
        Deadline("Bài tập Phương pháp tính – Lab 2", at(1, 23, 59), subject="Phương pháp tính",
                 priority=Priority.HIGH, remind_before_minutes=180,
                 attachments=[Attachment("https://lms.example.edu/lab2", AttachmentKind.URL, "Đề bài trên LMS")]),
        Deadline("Đọc chương 3 Giải tích", at(1, 9), subject="Toán cao cấp", notes="Làm bài 3.1 → 3.12"),
        Deadline("Nộp báo cáo nhóm", at(-1, 17), subject="Kỹ năng mềm", status=DeadlineStatus.IN_PROGRESS),
        Deadline("Ôn từ vựng tiếng Anh", at(3, 20), subject="Tiếng Anh", priority=Priority.LOW),
    ]
    lab = repo.add_deadline(items[0])
    for deadline in items[1:]:  # các deadline còn lại không có Study time → hiện cờ "chưa đặt thời gian"
        repo.add_deadline(deadline)
    studies = [
        StudyTime("Bài tập Phương pháp tính – Lab 2", at(0, 20), at(0, 22), subject="Phương pháp tính",
                  deadline_id=lab.id),
        StudyTime("Bài tập Phương pháp tính – Lab 2", at(1, 8), at(1, 10), subject="Phương pháp tính",
                  deadline_id=lab.id),
        StudyTime("Học Tiếng Anh", at(2, 19), at(2, 20, 30), subject="Tiếng Anh"),  # độc lập, không có deadline
    ]
    for study in studies:
        repo.add_study_time(study)
    repo.add_event(Event("Họp nhóm đồ án", at(0, 19), at(0, 20, 30), location="Thư viện tầng 3",
                         remind_before_minutes=30))
    repo.add_event(Event("Khám răng", at(2, 8, 30), at(2, 9, 30), location="Nha khoa Kim"))
    return len(items) + len(studies) + 2
