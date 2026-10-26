"""Mô hình dữ liệu thuần Python – không phụ thuộc UI hay DB."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from typing import ClassVar

from app.i18n import tr


class ValidationError(ValueError):
    """Dữ liệu không hợp lệ; message đã dịch theo ngôn ngữ hiện tại để UI hiển thị trực tiếp."""


class DeadlineStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"

    @property
    def label(self) -> str:
        return tr(f"status.{self.value}")


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return tr(f"priority.{self.name.lower()}")


class AttachmentKind(StrEnum):
    FILE = "file"
    FOLDER = "folder"
    URL = "url"


# Tên tone, '#RRGGBB' hoặc gradient (cùng định dạng với colors.GRADIENT_PREFIX – colors import models nên không dùng chung được)
_COLOR_RE = re.compile(r"^(?:[a-z]+|#[0-9a-fA-F]{6}|gradient:#[0-9a-fA-F]{6},#[0-9a-fA-F]{6})$")


def _check_remind(minutes: int | None) -> None:
    if minutes is not None and minutes < 0:
        raise ValidationError(tr("error.remind_negative"))


def _check_color(color: str | None) -> None:
    if color is not None and not _COLOR_RE.match(color):
        raise ValidationError(tr("error.color_invalid", color=repr(color)))


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
            raise ValidationError(tr("error.attachment_empty"))
        try:
            AttachmentKind(self.kind)
        except ValueError as exc:
            raise ValidationError(tr("error.attachment_kind", kind=repr(self.kind))) from exc


@dataclass(slots=True)
class Deadline:
    """Deadline: việc có hạn nộp (trước đây gọi là 'Bài tập')."""

    KIND: ClassVar[str] = "deadline"

    title: str
    due_at: datetime
    status: DeadlineStatus = DeadlineStatus.TODO
    priority: Priority = Priority.NORMAL
    subject: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    attachments: list[Attachment] = field(default_factory=list)
    id: int | None = None
    reminder_sent_at: datetime | None = None
    completed_at: datetime | None = None  # do repository ghi khi chuyển sang 'Xong'
    study_count: int = 0                  # số Study time liên kết – repository nạp khi đọc, không lưu
    due_changed_at: datetime | None = None  # lần cuối đổi hạn hoặc mở lại (mốc tính 'quá hạn' khi tự xoá)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def starts_at(self) -> datetime:
        """Mốc thời gian chung để sắp xếp cùng Event (= hạn nộp)."""
        return self.due_at

    @property
    def is_done(self) -> bool:
        return self.status == DeadlineStatus.DONE

    def is_overdue(self, now: datetime) -> bool:
        return not self.is_done and self.due_at < now

    @property
    def file_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.kind == AttachmentKind.FILE]

    def validate(self) -> None:
        if not self.title or not self.title.strip():
            raise ValidationError(tr("error.title_empty"))
        try:
            DeadlineStatus(self.status)
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
            raise ValidationError(tr("error.title_empty"))
        if self.ends_at < self.starts_at:
            raise ValidationError(tr("error.end_before_start"))
        _check_remind(self.remind_before_minutes)
        _check_color(self.color)


@dataclass(slots=True)
class StudyTime:
    """Khoảng thời gian dành để học / làm bài. Có thể độc lập hoặc gắn với một Deadline (`deadline_id`).

    Không có đính kèm. Tiêu đề của Study time chỉ được sao chép từ Deadline lúc tạo, không đồng bộ về sau.
    """

    KIND: ClassVar[str] = "study"

    title: str
    starts_at: datetime
    ends_at: datetime
    subject: str = ""
    notes: str = ""
    deadline_id: int | None = None
    remind_before_minutes: int | None = None  # chưa có UI; giữ để dùng chung cột/nhắc việc của Event
    id: int | None = None
    reminder_sent_at: datetime | None = None
    end_warned_at: datetime | None = None     # đã gửi toast "sắp hết giờ" lúc nào; repository xoá khi ends_at đổi
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate(self) -> None:
        if not self.title or not self.title.strip():
            raise ValidationError(tr("error.title_empty"))
        if self.ends_at <= self.starts_at:
            raise ValidationError(tr("error.end_before_start"))
        _check_remind(self.remind_before_minutes)


@dataclass(slots=True)
class Course:
    """Môn học trong thời khoá biểu: lặp lại mỗi tuần vào các thứ đã chọn, trong `weeks` tuần.

    Tuần 1 là tuần (Thứ Hai → Chủ Nhật) chứa `first_day`; buổi trước `first_day` trong tuần đó bị bỏ qua.
    Tên môn cũng là "môn học" của Deadline / Study time – cùng tên thì được coi là liên kết với môn này.
    """

    name: str
    weekdays: tuple[int, ...]   # 0 = Thứ Hai … 6 = Chủ Nhật
    start_time: time
    end_time: time
    first_day: date
    weeks: int = 15
    room: str = ""
    location: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    skipped: tuple[date, ...] = ()  # các buổi nghỉ (không hiện trên lịch)
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def all_dates(self) -> list[date]:
        """Mọi ngày có buổi học theo lịch lặp, kể cả buổi nghỉ."""
        monday = self.first_day - timedelta(days=self.first_day.weekday())
        days = (monday + timedelta(weeks=w, days=d) for w in range(self.weeks) for d in sorted(set(self.weekdays)))
        return [day for day in days if day >= self.first_day]

    def session_dates(self) -> list[date]:
        """Các ngày thực sự có buổi học (đã bỏ buổi nghỉ)."""
        skipped = set(self.skipped)
        return [day for day in self.all_dates() if day not in skipped]

    @property
    def last_day(self) -> date:
        """Ngày của buổi cuối theo lịch lặp (kể cả khi đó là buổi nghỉ)."""
        dates = self.all_dates()
        return dates[-1] if dates else self.first_day

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError(tr("error.course_name_empty"))
        if not self.weekdays or any(d not in range(7) for d in self.weekdays):
            raise ValidationError(tr("error.course_weekdays"))
        if self.end_time <= self.start_time:
            raise ValidationError(tr("error.end_before_start"))
        if self.weeks < 1:
            raise ValidationError(tr("error.course_weeks"))
        _check_remind(self.remind_before_minutes)


@dataclass(slots=True)
class ClassSession:
    """Một buổi học trên lịch, do repository sinh ra từ Course (không tạo/sửa trực tiếp).

    `subject` = tên môn (để tô màu theo môn); `course` được repository nạp kèm khi đọc.
    """

    KIND: ClassVar[str] = "class"

    title: str
    starts_at: datetime
    ends_at: datetime
    course_id: int
    subject: str = ""
    location: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    course: Course | None = None
    id: int | None = None
    reminder_sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def room(self) -> str:
        return self.course.room if self.course else ""


@dataclass(slots=True)
class Exam:
    """Ca thi: môn thi (`title`), giờ bắt đầu, lớp thi và địa điểm thi. Không có giờ kết thúc.

    Là loại mục ưu tiên cao nhất: giao diện luôn làm nổi bật và nhắc mỗi ngày trong 15 ngày trước ngày thi.
    """

    KIND: ClassVar[str] = "exam"

    title: str
    starts_at: datetime
    exam_class: str = ""
    location: str = ""
    notes: str = ""
    remind_before_minutes: int | None = None
    id: int | None = None
    reminder_sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def validate(self) -> None:
        if not self.title or not self.title.strip():
            raise ValidationError(tr("error.title_empty"))
        _check_remind(self.remind_before_minutes)


CalendarItem = Deadline | Event | StudyTime | ClassSession | Exam
