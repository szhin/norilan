"""Mô hình dữ liệu thuần Python – không phụ thuộc UI hay DB."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import ClassVar


class ValidationError(ValueError):
    """Dữ liệu không hợp lệ; message bằng tiếng Việt để UI hiển thị trực tiếp."""


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    TaskStatus.TODO: "Chưa làm",
    TaskStatus.IN_PROGRESS: "Đang làm",
    TaskStatus.DONE: "Xong",
}


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return _PRIORITY_LABELS[self]


_PRIORITY_LABELS = {Priority.LOW: "Thấp", Priority.NORMAL: "Trung bình", Priority.HIGH: "Cao"}


class AttachmentKind(StrEnum):
    FILE = "file"
    FOLDER = "folder"
    URL = "url"


_COLOR_RE = re.compile(r"^(?:[a-z]+|#[0-9a-fA-F]{6})$")


def _check_remind(minutes: int | None) -> None:
    if minutes is not None and minutes < 0:
        raise ValidationError("Thời gian nhắc trước phải ≥ 0 phút")


def _check_color(color: str | None) -> None:
    if color is not None and not _COLOR_RE.match(color):
        raise ValidationError(f"Màu không hợp lệ: {color!r}")


@dataclass(slots=True)
class Attachment:
    """Một đính kèm: đường dẫn tuyệt đối tới file/thư mục, hoặc URL."""

    target: str
    kind: AttachmentKind = AttachmentKind.FILE
    display_name: str = ""
    id: int | None = None
    item_id: int | None = None
    position: int = 0
    created_at: datetime | None = None

    @property
    def name(self) -> str:
        """Tên hiển thị: display_name, hoặc tên file cuối đường dẫn (hiểu cả \\ lẫn /)."""
        if self.display_name:
            return self.display_name
        if self.kind == AttachmentKind.URL:
            return self.target
        parts = re.split(r"[\\/]", self.target.rstrip("\\/"))
        return parts[-1] or self.target

    def validate(self) -> None:
        if not self.target or not self.target.strip():
            raise ValidationError("Đường dẫn đính kèm không được để trống")
        try:
            AttachmentKind(self.kind)
        except ValueError as exc:
            raise ValidationError(f"Loại đính kèm không hợp lệ: {self.kind!r}") from exc


@dataclass(slots=True)
class Task:
    """Bài tập / việc cần làm."""

    KIND: ClassVar[str] = "task"

    title: str
    due_at: datetime
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.NORMAL
    subject: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    attachments: list[Attachment] = field(default_factory=list)
    id: int | None = None
    reminder_sent_at: datetime | None = None
    completed_at: datetime | None = None  # do repository ghi khi chuyển sang 'Xong'
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def starts_at(self) -> datetime:
        """Mốc thời gian chung để sắp xếp cùng Event (= hạn nộp)."""
        return self.due_at

    @property
    def is_done(self) -> bool:
        return self.status == TaskStatus.DONE

    def is_overdue(self, now: datetime) -> bool:
        return not self.is_done and self.due_at < now

    @property
    def file_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.kind == AttachmentKind.FILE]

    def validate(self) -> None:
        if not self.title or not self.title.strip():
            raise ValidationError("Tiêu đề không được để trống")
        try:
            TaskStatus(self.status)
            Priority(self.priority)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        _check_remind(self.remind_before_minutes)
        for attachment in self.attachments:
            attachment.validate()


@dataclass(slots=True)
class Event:
    """Cuộc hẹn / sự kiện có giờ bắt đầu và kết thúc."""

    KIND: ClassVar[str] = "event"

    title: str
    starts_at: datetime
    ends_at: datetime
    location: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    color: str | None = None  # tên tone (vd. 'green') hoặc '#RRGGBB'; None = mặc định
    id: int | None = None
    reminder_sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate(self) -> None:
        if not self.title or not self.title.strip():
            raise ValidationError("Tiêu đề không được để trống")
        if self.ends_at < self.starts_at:
            raise ValidationError("Giờ kết thúc phải sau giờ bắt đầu")
        _check_remind(self.remind_before_minutes)
        _check_color(self.color)


CalendarItem = Task | Event
