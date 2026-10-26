"""Truy cập dữ liệu: CRUD Deadline/Event/Attachment/Môn học và các truy vấn cho lịch, sidebar, tìm kiếm, nhắc việc.

Mọi hàm nhận `now` từ bên ngoài (không tự gọi datetime.now()) để dễ test.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from app.calendar_logic import EXAM_ACTIVE
from app.countdown import warning_due
from app.db import fold_text
from app.i18n import tr
from app.models import (
    Attachment,
    AttachmentKind,
    CalendarItem,
    ClassSession,
    Course,
    Event,
    Exam,
    Priority,
    Deadline,
    DeadlineStatus,
    StudyTime,
    ValidationError,
)
from app.timeutil import day_range, from_iso, now as _now, opt_from_iso, to_iso

log = logging.getLogger(__name__)

_ITEM_COLS = (
    "id, kind, title, notes, starts_at, ends_at, location, status, priority, subject, "
    "remind_before_minutes, reminder_sent_at, color, completed_at, deadline_id, end_warned_at, "
    "due_changed_at, course_id, exam_class, created_at, updated_at"
)
# Cùng giờ thì: ca thi → deadline → lớp học → study → event.
_KIND_ORDER_SQL = (
    "CASE kind WHEN 'exam' THEN 0 WHEN 'deadline' THEN 1 WHEN 'class' THEN 2 WHEN 'study' THEN 3 ELSE 4 END"
)

_ATT_COLS = "id, item_id, kind, target, display_name, position, created_at"
_COURSE_COLS = (
    "id, name, weekdays, start_time, end_time, first_day, weeks, room, location, notes, "
    "remind_before_minutes, skipped_dates, created_at, updated_at"
)

# Đổi lịch hoặc thời gian nhắc ⇒ phải nhắc lại. Trong UPDATE, cột bên phải '=' là giá trị CŨ.
_RESET_REMINDER_SQL = (
    "reminder_sent_at = CASE WHEN starts_at = :starts_at AND remind_before_minutes IS :remind "
    "THEN reminder_sent_at ELSE NULL END"
)


# Ghi/xoá thời điểm hoàn thành. Đã 'Xong' từ trước thì giữ mốc cũ (sửa tên không làm mốc thay đổi).
_COMPLETED_SQL = (
    "completed_at = CASE WHEN :status != 'done' THEN NULL "
    "WHEN status = 'done' THEN COALESCE(completed_at, :now) ELSE :now END"
)


# Mốc "đổi hạn / mở lại" để tính quá hạn khi tự xoá (cột bên phải '=' là giá trị CŨ).
_REOPENED_SQL = (
    "due_changed_at = CASE WHEN status = 'done' AND :status != 'done' THEN :now ELSE due_changed_at END"
)
_DUE_CHANGED_SQL = (
    "due_changed_at = CASE WHEN starts_at != :starts_at OR (status = 'done' AND :status != 'done') "
    "THEN :now ELSE due_changed_at END"
)


class NotFoundError(LookupError):
    """Không tìm thấy bản ghi cần sửa."""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_attachment(row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=row["id"],
        item_id=row["item_id"],
        kind=AttachmentKind(row["kind"]),
        target=row["target"],
        display_name=row["display_name"],
        position=row["position"],
        created_at=from_iso(row["created_at"]),
    )


def _row_to_item(row: sqlite3.Row) -> CalendarItem:
    common: dict[str, Any] = dict(
        id=row["id"],
        title=row["title"],
        notes=row["notes"],
        remind_before_minutes=row["remind_before_minutes"],
        reminder_sent_at=opt_from_iso(row["reminder_sent_at"]),
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )
    if row["kind"] == Deadline.KIND:
        return Deadline(
            due_at=from_iso(row["starts_at"]),
            status=DeadlineStatus(row["status"]),
            priority=Priority(row["priority"]),
            subject=row["subject"],
            completed_at=opt_from_iso(row["completed_at"]),
            due_changed_at=opt_from_iso(row["due_changed_at"]),
            **common,
        )
    if row["kind"] == Exam.KIND:
        return Exam(
            starts_at=from_iso(row["starts_at"]),
            exam_class=row["exam_class"],
            location=row["location"],
            **common,
        )
    if row["kind"] == StudyTime.KIND:
        return StudyTime(
            starts_at=from_iso(row["starts_at"]),
            ends_at=from_iso(row["ends_at"]),
            subject=row["subject"],
            deadline_id=row["deadline_id"],
            end_warned_at=opt_from_iso(row["end_warned_at"]),
            **common,
        )
    if row["kind"] == ClassSession.KIND:
        return ClassSession(
            starts_at=from_iso(row["starts_at"]),
            ends_at=from_iso(row["ends_at"]),
            course_id=row["course_id"],
            subject=row["subject"],
            location=row["location"],
            **common,
        )
    return Event(
        starts_at=from_iso(row["starts_at"]),
        ends_at=from_iso(row["ends_at"]),
        location=row["location"],
        color=row["color"],
        **common,
    )


def _row_to_course(row: sqlite3.Row) -> Course:
    return Course(
        id=row["id"],
        name=row["name"],
        weekdays=tuple(int(d) for d in row["weekdays"].split(",") if d),
        start_time=time.fromisoformat(row["start_time"]),
        end_time=time.fromisoformat(row["end_time"]),
        first_day=date.fromisoformat(row["first_day"]),
        weeks=row["weeks"],
        room=row["room"],
        location=row["location"],
        notes=row["notes"],
        remind_before_minutes=row["remind_before_minutes"],
        skipped=tuple(date.fromisoformat(d) for d in row["skipped_dates"].split(",") if d),
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )


def _course_params(course: Course) -> dict[str, Any]:
    return {
        "id": course.id, "name": course.name.strip(),
        "weekdays": ",".join(str(d) for d in sorted(set(course.weekdays))),
        "start_time": f"{course.start_time:%H:%M}", "end_time": f"{course.end_time:%H:%M}",
        "first_day": course.first_day.isoformat(), "weeks": course.weeks,
        "room": course.room.strip(), "location": course.location.strip(), "notes": course.notes,
        "remind": course.remind_before_minutes,
        "skipped": ",".join(sorted(d.isoformat() for d in set(course.skipped))),
        "now": to_iso(_now()),
    }


class Repository:
    """Lớp truy cập dữ liệu duy nhất mà services/UI dùng."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """Kết nối SQLite (dùng cho sao lưu/khôi phục)."""
        return self._conn

    # ------------------------------------------------------------------ Deadline
    def add_deadline(self, deadline: Deadline) -> Deadline:
        deadline.validate()
        ts = to_iso(_now())
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO items (kind, title, notes, starts_at, status, priority, subject,
                       remind_before_minutes, completed_at, due_changed_at, created_at, updated_at)
                   VALUES ('deadline', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deadline.title.strip(), deadline.notes, to_iso(deadline.due_at), str(deadline.status),
                    int(deadline.priority), deadline.subject.strip(), deadline.remind_before_minutes,
                    ts if deadline.status == DeadlineStatus.DONE else None, ts, ts, ts,
                ),
            )
            deadline_id = cur.lastrowid
            self._sync_attachments(deadline_id, deadline.attachments)
        log.info("Added deadline #%s %r", deadline_id, deadline.title)
        return self._require_deadline(deadline_id)

    def deadline_exists(self, title: str, due_at: datetime) -> bool:
        """Đã có deadline cùng tên (không phân biệt hoa/thường, dấu) và cùng hạn nộp chưa – để tránh nhập trùng."""
        wanted = fold_text(title.strip())
        rows = self._conn.execute(
            "SELECT title FROM items WHERE kind = 'deadline' AND starts_at = ?", (to_iso(due_at),)
        ).fetchall()
        return any(fold_text(r[0].strip()) == wanted for r in rows)

    def update_deadline(self, deadline: Deadline) -> Deadline:
        """Ghi đè mọi trường, đồng bộ danh sách đính kèm theo deadline.attachments."""
        if deadline.id is None:
            raise ValueError("Deadline chưa có id")
        deadline.validate()
        params = {
            "id": deadline.id, "title": deadline.title.strip(), "notes": deadline.notes,
            "starts_at": to_iso(deadline.due_at), "status": str(deadline.status),
            "priority": int(deadline.priority), "subject": deadline.subject.strip(),
            "remind": deadline.remind_before_minutes, "now": to_iso(_now()),
        }
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_RESET_REMINDER_SQL}, {_COMPLETED_SQL}, {_DUE_CHANGED_SQL},
                       title = :title, notes = :notes, starts_at = :starts_at, status = :status,
                       priority = :priority, subject = :subject, remind_before_minutes = :remind,
                       updated_at = :now
                   WHERE id = :id AND kind = 'deadline'""",
                params,
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"Không tìm thấy deadline #{deadline.id}")
            self._sync_attachments(deadline.id, deadline.attachments)
        return self._require_deadline(deadline.id)

    def set_deadline_status(self, deadline_id: int, status: DeadlineStatus, *, at: datetime | None = None) -> None:
        """Đổi trạng thái. Chuyển sang 'Xong' thì ghi thời điểm hoàn thành (`at`, mặc định bây giờ)."""
        status = DeadlineStatus(status)
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_COMPLETED_SQL}, {_REOPENED_SQL}, status = :status, updated_at = :now
                    WHERE id = :id AND kind = 'deadline'""",
                {"status": str(status), "now": to_iso(at or _now()), "id": deadline_id},
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy deadline #{deadline_id}")

    def get_deadline(self, deadline_id: int) -> Deadline | None:
        item = self.get_item(deadline_id)
        return item if isinstance(item, Deadline) else None

    def _require_deadline(self, deadline_id: int) -> Deadline:
        deadline = self.get_deadline(deadline_id)
        if deadline is None:
            raise NotFoundError(f"Không tìm thấy deadline #{deadline_id}")
        return deadline

    # ----------------------------------------------------------------- Event
    def add_event(self, event: Event) -> Event:
        event.validate()
        ts = to_iso(_now())
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO items (kind, title, notes, starts_at, ends_at, location,
                       remind_before_minutes, color, created_at, updated_at)
                   VALUES ('event', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.title.strip(), event.notes, to_iso(event.starts_at),
                    to_iso(event.ends_at), event.location.strip(),
                    event.remind_before_minutes, event.color, ts, ts,
                ),
            )
        log.info("Added event #%s %r", cur.lastrowid, event.title)
        return self._require_event(cur.lastrowid)

    def update_event(self, event: Event) -> Event:
        if event.id is None:
            raise ValueError("Event chưa có id")
        event.validate()
        params = {
            "id": event.id, "title": event.title.strip(), "notes": event.notes,
            "starts_at": to_iso(event.starts_at), "ends_at": to_iso(event.ends_at),
            "location": event.location.strip(), "remind": event.remind_before_minutes,
            "color": event.color, "now": to_iso(_now()),
        }
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_RESET_REMINDER_SQL},
                       title = :title, notes = :notes, starts_at = :starts_at, ends_at = :ends_at,
                       location = :location, remind_before_minutes = :remind, color = :color,
                       updated_at = :now
                   WHERE id = :id AND kind = 'event'""",
                params,
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy event #{event.id}")
        return self._require_event(event.id)

    def get_event(self, event_id: int) -> Event | None:
        item = self.get_item(event_id)
        return item if isinstance(item, Event) else None

    def _require_event(self, event_id: int) -> Event:
        event = self.get_event(event_id)
        if event is None:
            raise NotFoundError(f"Không tìm thấy event #{event_id}")
        return event

    # ------------------------------------------------------------------ Ca thi
    def add_exam(self, exam: Exam) -> Exam:
        exam.validate()
        ts = to_iso(_now())
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO items (kind, title, notes, starts_at, location, exam_class,
                       remind_before_minutes, created_at, updated_at)
                   VALUES ('exam', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exam.title.strip(), exam.notes, to_iso(exam.starts_at), exam.location.strip(),
                    exam.exam_class.strip(), exam.remind_before_minutes, ts, ts,
                ),
            )
        log.info("Added exam #%s %r", cur.lastrowid, exam.title)
        return self._require_exam(cur.lastrowid)

    def update_exam(self, exam: Exam) -> Exam:
        if exam.id is None:
            raise ValueError("Exam chưa có id")
        exam.validate()
        params = {
            "id": exam.id, "title": exam.title.strip(), "notes": exam.notes,
            "starts_at": to_iso(exam.starts_at), "location": exam.location.strip(),
            "exam_class": exam.exam_class.strip(), "remind": exam.remind_before_minutes, "now": to_iso(_now()),
        }
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_RESET_REMINDER_SQL},
                       title = :title, notes = :notes, starts_at = :starts_at, location = :location,
                       exam_class = :exam_class, remind_before_minutes = :remind, updated_at = :now
                   WHERE id = :id AND kind = 'exam'""",
                params,
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy ca thi #{exam.id}")
        return self._require_exam(exam.id)

    def get_exam(self, exam_id: int) -> Exam | None:
        item = self.get_item(exam_id)
        return item if isinstance(item, Exam) else None

    def _require_exam(self, exam_id: int) -> Exam:
        exam = self.get_exam(exam_id)
        if exam is None:
            raise NotFoundError(f"Không tìm thấy ca thi #{exam_id}")
        return exam

    def upcoming_exams(self, now: datetime, *, days: int | None = None) -> list[Exam]:
        """Ca thi chưa qua (đang thi cũng tính), gần nhất trước. `days`: chỉ lấy ca thi trong N ngày tới,
        tính theo ngày lịch (ca thi lúc 23:00 của ngày thứ N vẫn được tính)."""
        sql = f"SELECT {_ITEM_COLS} FROM items WHERE kind = 'exam' AND starts_at >= :since"
        params: dict[str, Any] = {"since": to_iso(now - EXAM_ACTIVE)}
        if days is not None:
            params["until"] = to_iso(day_range(now.date() + timedelta(days=days))[1])
            sql += " AND starts_at < :until"
        rows = self._conn.execute(sql + " ORDER BY starts_at, id", params).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    # ------------------------------------------------------------ Study time
    def _check_study_link(self, study: StudyTime) -> None:
        if study.deadline_id is not None and self.get_deadline(study.deadline_id) is None:
            raise ValidationError(tr("error.study_deadline_missing"))

    def add_study_time(self, study: StudyTime) -> StudyTime:
        study.validate()
        self._check_study_link(study)
        ts = to_iso(_now())
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO items (kind, title, notes, starts_at, ends_at, subject, deadline_id,
                       remind_before_minutes, created_at, updated_at)
                   VALUES ('study', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    study.title.strip(), study.notes, to_iso(study.starts_at), to_iso(study.ends_at),
                    study.subject.strip(), study.deadline_id, study.remind_before_minutes, ts, ts,
                ),
            )
        log.info("Added study time #%s %r", cur.lastrowid, study.title)
        return self._require_study_time(cur.lastrowid)

    def update_study_time(self, study: StudyTime) -> StudyTime:
        if study.id is None:
            raise ValueError("StudyTime chưa có id")
        study.validate()
        self._check_study_link(study)
        params = {
            "id": study.id, "title": study.title.strip(), "notes": study.notes,
            "starts_at": to_iso(study.starts_at), "ends_at": to_iso(study.ends_at),
            "subject": study.subject.strip(), "deadline_id": study.deadline_id,
            "remind": study.remind_before_minutes, "now": to_iso(_now()),
        }
        with self._conn:
            cur = self._conn.execute(
                # Đổi ends_at ⇒ được cảnh báo "sắp hết giờ" lại (cột bên phải '=' là giá trị CŨ)
                f"""UPDATE items SET {_RESET_REMINDER_SQL},
                       end_warned_at = CASE WHEN ends_at = :ends_at THEN end_warned_at ELSE NULL END,
                       title = :title, notes = :notes, starts_at = :starts_at, ends_at = :ends_at,
                       subject = :subject, deadline_id = :deadline_id, remind_before_minutes = :remind,
                       updated_at = :now
                   WHERE id = :id AND kind = 'study'""",
                params,
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy study time #{study.id}")
        return self._require_study_time(study.id)

    def get_study_time(self, study_id: int) -> StudyTime | None:
        item = self.get_item(study_id)
        return item if isinstance(item, StudyTime) else None

    def _require_study_time(self, study_id: int) -> StudyTime:
        study = self.get_study_time(study_id)
        if study is None:
            raise NotFoundError(f"Không tìm thấy study time #{study_id}")
        return study

    def study_times_for(self, deadline_id: int) -> list[StudyTime]:
        """Các Study time gắn với một Deadline, theo thời gian."""
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE kind = 'study' AND deadline_id = ? ORDER BY starts_at, id",
            (deadline_id,),
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def open_deadlines(self) -> list[Deadline]:
        """Deadline chưa xong (cho ô chọn trong hộp thoại Study time), hạn gần nhất trước."""
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE kind = 'deadline' AND status != 'done' "
            "ORDER BY starts_at, id"
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def delete_deadline(self, deadline_id: int, *, study_times: str = "unlink") -> bool:
        """Xoá Deadline. `study_times`: 'unlink' (mặc định) gỡ liên kết các Study time, 'delete' xoá luôn chúng."""
        return self.delete_deadlines([deadline_id], study_times=study_times) > 0

    def delete_deadlines(self, deadline_ids: Iterable[int], *, study_times: str = "unlink") -> int:
        """Xoá nhiều Deadline trong MỘT transaction (đường xoá duy nhất, dùng cho cả xoá thủ công lẫn tự xoá).

        Đính kèm xoá theo (ON DELETE CASCADE); Study time còn liên kết thì ON DELETE SET NULL tự gỡ liên kết.
        Trả về số Deadline đã xoá.
        """
        if study_times not in ("unlink", "delete"):
            raise ValueError(f"study_times phải là 'unlink' hoặc 'delete', không phải {study_times!r}")
        removed = 0
        with self._conn:
            for deadline_id in deadline_ids:
                if study_times == "delete":
                    self._conn.execute("DELETE FROM items WHERE kind = 'study' AND deadline_id = ?", (deadline_id,))
                removed += self._conn.execute(
                    "DELETE FROM items WHERE id = ? AND kind = 'deadline'", (deadline_id,)
                ).rowcount
        return removed

    def list_deadlines(self) -> list[Deadline]:
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE kind = 'deadline' ORDER BY starts_at, id"
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    # ------------------------------------------------------------ Tự xoá sự kiện cũ
    def list_events(self) -> list[Event]:
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE kind = 'event' ORDER BY starts_at, id"
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def delete_events(self, event_ids: Iterable[int]) -> int:
        """Xoá nhiều Event trong MỘT transaction (đường xoá của tự xoá). Trả về số đã xoá."""
        removed = 0
        with self._conn:
            for event_id in event_ids:
                removed += self._conn.execute(
                    "DELETE FROM items WHERE id = ? AND kind = 'event'", (event_id,)
                ).rowcount
        return removed

    # ------------------------------------------------------------ Môn học (thời khoá biểu)
    def _check_course_name(self, course: Course) -> None:
        clash = self._conn.execute(
            "SELECT 1 FROM courses WHERE fold(name) = fold(?) AND id IS NOT ?", (course.name.strip(), course.id)
        ).fetchone()
        if clash:
            raise ValidationError(tr("error.course_exists", name=course.name.strip()))

    def add_course(self, course: Course) -> Course:
        """Thêm môn và sinh mọi buổi học của nó lên lịch."""
        course.validate()
        self._check_course_name(course)
        params = _course_params(course)
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO courses (name, weekdays, start_time, end_time, first_day, weeks, room, location,
                       notes, remind_before_minutes, skipped_dates, created_at, updated_at)
                   VALUES (:name, :weekdays, :start_time, :end_time, :first_day, :weeks, :room, :location,
                       :notes, :remind, :skipped, :now, :now)""",
                params,
            )
            course_id = cur.lastrowid
            self._sync_sessions(self._require_course(course_id))
        log.info("Added course #%s %r", course_id, course.name)
        return self._require_course(course_id)

    def update_course(self, course: Course) -> Course:
        """Ghi đè môn và đồng bộ lại buổi học. Đổi tên môn thì Deadline/Study time cùng môn và màu môn đi theo."""
        if course.id is None:
            raise ValueError("Course chưa có id")
        course.validate()
        self._check_course_name(course)
        old = self._require_course(course.id)
        params = _course_params(course)
        with self._conn:
            self._conn.execute(
                """UPDATE courses SET name = :name, weekdays = :weekdays, start_time = :start_time,
                       end_time = :end_time, first_day = :first_day, weeks = :weeks, room = :room,
                       location = :location, notes = :notes, remind_before_minutes = :remind,
                       skipped_dates = :skipped, updated_at = :now
                   WHERE id = :id""",
                params,
            )
            if params["name"] != old.name:
                self._rename_subject(old.name, params["name"])
            self._sync_sessions(self._require_course(course.id))
        return self._require_course(course.id)

    def _rename_subject(self, old: str, new: str) -> None:
        """Đổi tên môn ở Deadline/Study time và chuyển màu môn sang tên mới (nếu tên mới chưa có màu). Gọi trong transaction."""
        self._conn.execute(
            "UPDATE items SET subject = ? WHERE kind IN ('deadline', 'study') AND subject = ?", (new, old)
        )
        self._conn.execute(
            "UPDATE OR IGNORE subject_colors SET subject = ? WHERE subject = ?", (new, old)
        )

    def _sync_sessions(self, course: Course) -> None:
        """Làm cho buổi học trên lịch khớp với môn. Buổi cùng giờ bắt đầu được giữ (giữ id và trạng thái
        đã nhắc), buổi mới được thêm, buổi không còn trong lịch bị xoá. Gọi trong transaction."""
        existing = {
            r["starts_at"]: r["id"]
            for r in self._conn.execute("SELECT id, starts_at FROM items WHERE course_id = ?", (course.id,))
        }
        kept: set[int] = set()
        now = to_iso(_now())
        for day in course.session_dates():
            params = {
                "starts_at": to_iso(datetime.combine(day, course.start_time)),
                "ends_at": to_iso(datetime.combine(day, course.end_time)),
                "title": course.name.strip(), "location": course.location.strip(), "notes": course.notes,
                "remind": course.remind_before_minutes, "course_id": course.id, "now": now,
            }
            if (item_id := existing.get(params["starts_at"])) is not None:
                self._conn.execute(
                    f"""UPDATE items SET {_RESET_REMINDER_SQL}, title = :title, subject = :title,
                           ends_at = :ends_at, location = :location, notes = :notes,
                           remind_before_minutes = :remind, updated_at = :now
                       WHERE id = :id""",
                    {**params, "id": item_id},
                )
                kept.add(item_id)
            else:
                self._conn.execute(
                    """INSERT INTO items (kind, title, subject, notes, starts_at, ends_at, location,
                           remind_before_minutes, course_id, created_at, updated_at)
                       VALUES ('class', :title, :title, :notes, :starts_at, :ends_at, :location,
                           :remind, :course_id, :now, :now)""",
                    params,
                )
        removed = set(existing.values()) - kept
        if removed:
            self._conn.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in removed])

    def skip_session(self, session_id: int) -> bool:
        """Nghỉ một buổi: ghi ngày đó vào danh sách buổi nghỉ của môn và gỡ buổi khỏi lịch."""
        session = self.get_item(session_id)
        if not isinstance(session, ClassSession) or session.course is None:
            return False
        course = session.course
        course.skipped = (*course.skipped, session.starts_at.date())
        with self._conn:
            self._conn.execute(
                "UPDATE courses SET skipped_dates = :skipped, updated_at = :now WHERE id = :id",
                _course_params(course),
            )
            self._conn.execute("DELETE FROM items WHERE id = ?", (session_id,))
        return True

    def delete_course(self, course_id: int) -> bool:
        """Xoá môn và mọi buổi học của nó (ON DELETE CASCADE). Deadline/Study time cùng môn giữ nguyên."""
        with self._conn:
            cur = self._conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        return cur.rowcount > 0

    def get_course(self, course_id: int) -> Course | None:
        row = self._conn.execute(f"SELECT {_COURSE_COLS} FROM courses WHERE id = ?", (course_id,)).fetchone()
        return _row_to_course(row) if row else None

    def _require_course(self, course_id: int) -> Course:
        course = self.get_course(course_id)
        if course is None:
            raise NotFoundError(f"Không tìm thấy môn học #{course_id}")
        return course

    def list_courses(self) -> list[Course]:
        """Mọi môn, theo thứ tự trong tuần rồi giờ học (như đọc một thời khoá biểu)."""
        rows = self._conn.execute(f"SELECT {_COURSE_COLS} FROM courses").fetchall()
        courses = [_row_to_course(r) for r in rows]
        return sorted(courses, key=lambda c: (min(c.weekdays), c.start_time, fold_text(c.name)))

    def next_session(self, course_id: int, now: datetime) -> ClassSession | None:
        """Buổi học tiếp theo (đang diễn ra cũng tính) của một môn."""
        row = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE course_id = ? AND ends_at > ? ORDER BY starts_at LIMIT 1",
            (course_id, to_iso(now)),
        ).fetchone()
        return self._hydrate([row])[0] if row else None  # type: ignore[return-value]

    def items_for_subject(self, subject: str, kind: str, *, since: datetime | None = None) -> list[CalendarItem]:
        """Deadline / Study time ghi cùng môn (liên kết với môn học theo tên). `since`: chỉ lấy mục chưa kết thúc."""
        sql = f"SELECT {_ITEM_COLS} FROM items WHERE kind = :kind AND subject = :subject"
        if since is not None:
            sql += " AND COALESCE(ends_at, starts_at) >= :since"
        rows = self._conn.execute(
            sql + " ORDER BY starts_at, id",
            {"kind": kind, "subject": subject.strip(), "since": to_iso(since) if since else None},
        ).fetchall()
        return self._hydrate(rows)

    # ------------------------------------------------------------ Chung
    def get_item(self, item_id: int) -> CalendarItem | None:
        row = self._conn.execute(f"SELECT {_ITEM_COLS} FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def delete_item(self, item_id: int) -> bool:
        """Xoá một mục (đính kèm bị xoá theo; Study time liên kết được gỡ liên kết). False nếu không tồn tại."""
        with self._conn:
            cur = self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return cur.rowcount > 0

    def count_items(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT kind, COUNT(*) AS n FROM items GROUP BY kind").fetchall()
        counts = {"deadline": 0, "event": 0, "study": 0, "class": 0, "exam": 0}
        counts.update({r["kind"]: r["n"] for r in rows})
        return counts

    # ------------------------------------------------------- Attachment
    def list_attachments(self, deadline_id: int) -> list[Attachment]:
        rows = self._conn.execute(
            f"SELECT {_ATT_COLS} FROM attachments WHERE item_id = ? ORDER BY position, id", (deadline_id,)
        ).fetchall()
        return [_row_to_attachment(r) for r in rows]

    def add_attachment(self, deadline_id: int, attachment: Attachment) -> Attachment:
        """Thêm một đính kèm vào cuối danh sách của deadline."""
        attachment.validate()
        if self.get_deadline(deadline_id) is None:
            raise NotFoundError(f"Không tìm thấy deadline #{deadline_id}")
        with self._conn:
            pos = self._conn.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM attachments WHERE item_id = ?", (deadline_id,)
            ).fetchone()[0]
            att_id = self._insert_attachment(deadline_id, attachment, pos)
        return self._get_attachment(att_id)

    def update_attachment(self, attachment: Attachment) -> Attachment:
        """Dùng khi chọn lại đường dẫn cho file bị di chuyển/xoá."""
        if attachment.id is None:
            raise ValueError("Attachment chưa có id")
        attachment.validate()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE attachments SET kind = ?, target = ?, display_name = ? WHERE id = ?",
                (str(attachment.kind), attachment.target, attachment.display_name, attachment.id),
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy đính kèm #{attachment.id}")
        return self._get_attachment(attachment.id)

    def remove_attachment(self, attachment_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        return cur.rowcount > 0

    def get_attachment(self, attachment_id: int) -> Attachment | None:
        row = self._conn.execute(
            f"SELECT {_ATT_COLS} FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        return _row_to_attachment(row) if row else None

    def _get_attachment(self, attachment_id: int) -> Attachment:
        row = self._conn.execute(
            f"SELECT {_ATT_COLS} FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Không tìm thấy đính kèm #{attachment_id}")
        return _row_to_attachment(row)

    def _insert_attachment(self, item_id: int, att: Attachment, position: int) -> int:
        cur = self._conn.execute(
            """INSERT INTO attachments (item_id, kind, target, display_name, position, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (item_id, str(att.kind), att.target, att.display_name, position, to_iso(_now())),
        )
        return cur.lastrowid

    def _sync_attachments(self, item_id: int, attachments: list[Attachment]) -> None:
        """Làm cho DB khớp danh sách: sửa cái có id, thêm cái mới, xoá cái bị bỏ. Gọi trong transaction."""
        existing = {
            r[0] for r in self._conn.execute("SELECT id FROM attachments WHERE item_id = ?", (item_id,))
        }
        kept: set[int] = set()
        for pos, att in enumerate(attachments):
            if att.id is not None and att.id in existing:
                self._conn.execute(
                    "UPDATE attachments SET kind = ?, target = ?, display_name = ?, position = ? WHERE id = ?",
                    (str(att.kind), att.target, att.display_name, pos, att.id),
                )
                kept.add(att.id)
            else:
                self._insert_attachment(item_id, att, pos)
        removed = existing - kept
        if removed:
            self._conn.executemany("DELETE FROM attachments WHERE id = ?", [(i,) for i in removed])

    # ---------------------------------------------------------- Truy vấn
    def items_between(
        self, start: datetime, end: datetime, *, include_done: bool = True
    ) -> list[CalendarItem]:
        """Mục giao với khoảng [start, end): deadline có hạn trong khoảng, event/study đang diễn ra trong khoảng."""
        sql = f"""SELECT {_ITEM_COLS} FROM items
                  WHERE starts_at < :end AND (starts_at >= :start OR ends_at > :start)"""
        if not include_done:
            sql += " AND (kind != 'deadline' OR status != 'done')"
        sql += f" ORDER BY starts_at, {_KIND_ORDER_SQL}, id"
        rows = self._conn.execute(sql, {"start": to_iso(start), "end": to_iso(end)}).fetchall()
        return self._hydrate(rows)

    def items_on(self, day: date, *, include_done: bool = True) -> list[CalendarItem]:
        start, end = day_range(day)
        return self.items_between(start, end, include_done=include_done)

    def deadlines_due_on(self, day: date, *, include_done: bool = False) -> list[Deadline]:
        """Dùng cho toast tổng hợp buổi tối: 'Ngày mai bạn có N việc'."""
        return [i for i in self.items_on(day, include_done=include_done) if isinstance(i, Deadline)]

    def overdue_deadlines(self, now: datetime) -> list[Deadline]:
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE kind = 'deadline' AND status != 'done' AND starts_at < ?
                ORDER BY starts_at, id""",
            (to_iso(now),),
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def upcoming(self, now: datetime, days: int = 7) -> list[CalendarItem]:
        """Mục chưa xong trong `days` ngày tới, tính từ thời điểm hiện tại."""
        return self.items_between(now, now + timedelta(days=days), include_done=False)

    def search(
        self,
        text: str = "",
        *,
        status: DeadlineStatus | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[CalendarItem]:
        """Tìm theo tiêu đề/ghi chú/môn học/địa điểm, không phân biệt dấu và hoa thường.

        Buổi học không nằm trong kết quả (mỗi môn có hàng chục buổi giống nhau – môn đã có sẵn ở sidebar).
        """
        clauses: list[str] = ["kind != 'class'"]
        params: dict[str, Any] = {"limit": limit}
        if text.strip():
            params["q"] = f"%{_escape_like(fold_text(text.strip()))}%"
            fields = ("title", "notes", "subject", "location")
            clauses.append("(" + " OR ".join(f"fold({f}) LIKE :q ESCAPE '\\'" for f in fields) + ")")
        if status is not None:
            clauses.append("kind = 'deadline' AND status = :status")
            params["status"] = str(DeadlineStatus(status))
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        where = " AND ".join(clauses) or "1 = 1"
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE {where} ORDER BY starts_at, id LIMIT :limit", params
        ).fetchall()
        return self._hydrate(rows)

    def all_attachment_targets(self) -> set[str]:
        """Mọi đường dẫn đang được đính kèm (để dọn bản sao không còn ai dùng)."""
        return {r[0] for r in self._conn.execute("SELECT target FROM attachments")}

    # ---------------------------------------------------------- Màu môn học
    def subject_colors(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self._conn.execute("SELECT subject, color FROM subject_colors")}

    def set_subject_color(self, subject: str, color: str | None) -> None:
        """Đặt màu cho một môn; None = bỏ màu riêng (quay về màu tự gán)."""
        subject = subject.strip()
        if not subject:
            return
        with self._conn:
            if color is None:
                self._conn.execute("DELETE FROM subject_colors WHERE subject = ?", (subject,))
            else:
                self._conn.execute(
                    "INSERT INTO subject_colors (subject, color) VALUES (?, ?) "
                    "ON CONFLICT(subject) DO UPDATE SET color = excluded.color",
                    (subject, color),
                )

    # ---------------------------------------------------------- Trạng thái app
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def subjects(self) -> list[str]:
        """Các môn học đã dùng (kể cả môn trong thời khoá biểu), môn dùng gần nhất trước (gợi ý trong ô nhập)."""
        rows = self._conn.execute(
            """SELECT subject FROM (
                   SELECT subject, updated_at FROM items WHERE kind IN ('deadline', 'study') AND subject != ''
                   UNION ALL SELECT name, updated_at FROM courses)
               GROUP BY subject ORDER BY MAX(updated_at) DESC, subject"""
        ).fetchall()
        return [r[0] for r in rows]

    # ---------------------------------------------------------- Nhắc việc
    def due_reminders(
        self, now: datetime, *, grace: timedelta = timedelta(hours=1)
    ) -> list[CalendarItem]:
        """Mục đã đến giờ nhắc mà chưa nhắc. Bỏ qua deadline đã xong và mục đã trôi qua quá `grace`."""
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE remind_before_minutes IS NOT NULL
                  AND reminder_sent_at IS NULL
                  AND (kind != 'deadline' OR status != 'done')
                  AND strftime('%Y-%m-%dT%H:%M:%S', starts_at,
                               printf('-%d minutes', remind_before_minutes)) <= :now
                  AND starts_at >= :earliest
                ORDER BY starts_at, id""",
            {"now": to_iso(now), "earliest": to_iso(now - grace)},
        ).fetchall()
        return self._hydrate(rows)

    def due_end_warnings(self, now: datetime) -> list[StudyTime]:
        """Study time đang chạy, chưa gửi toast 'sắp hết giờ' và đã tới mốc ends_at − 10 phút.

        Chỉ Study time (không bao giờ Deadline/Event). Thời lượng < 10 phút thì không bao giờ có trong danh sách.
        Mở app khi còn < 10 phút: vẫn đang chạy + chưa có cờ ⇒ có trong danh sách (gửi ngay một lần).
        """
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE kind = 'study' AND end_warned_at IS NULL AND starts_at <= :now AND ends_at > :now
                ORDER BY ends_at, id""",
            {"now": to_iso(now)},
        ).fetchall()
        return [s for s in self._hydrate(rows) if warning_due(now, s.starts_at, s.ends_at)]  # type: ignore[union-attr]

    def claim_end_warning(self, study_id: int, at: datetime) -> bool:
        """Gắn cờ 'đã cảnh báo' một cách atomic. True nếu lần gọi này giành được (mỗi Study time chỉ một toast)."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE items SET end_warned_at = ? WHERE id = ? AND kind = 'study' AND end_warned_at IS NULL",
                (to_iso(at), study_id),
            )
        return cur.rowcount == 1

    def mark_reminder_sent(self, item_id: int, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE items SET reminder_sent_at = ? WHERE id = ?", (to_iso(at), item_id)
            )

    def claim_reminder(self, item_id: int, at: datetime) -> bool:
        """Đánh dấu 'đã nhắc' một cách atomic. True nếu lần gọi này giành được (chưa ai nhắc mục này)."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE items SET reminder_sent_at = ? WHERE id = ? AND reminder_sent_at IS NULL",
                (to_iso(at), item_id),
            )
        return cur.rowcount == 1

    # ------------------------------------------------------------ Nội bộ
    def _hydrate(self, rows: Iterable[sqlite3.Row]) -> list[CalendarItem]:
        """Chuyển row → model và nạp đính kèm cho mọi deadline, môn học cho mọi buổi học (tránh N+1)."""
        items = [_row_to_item(r) for r in rows]
        if course_ids := {i.course_id for i in items if isinstance(i, ClassSession)}:
            marks = ",".join("?" * len(course_ids))
            courses = {
                r["id"]: _row_to_course(r)
                for r in self._conn.execute(f"SELECT {_COURSE_COLS} FROM courses WHERE id IN ({marks})", list(course_ids))
            }
            for item in items:
                if isinstance(item, ClassSession):
                    item.course = courses.get(item.course_id)
        deadlines = {i.id: i for i in items if isinstance(i, Deadline)}
        if deadlines:
            ids = list(deadlines)
            for chunk_start in range(0, len(ids), 500):
                chunk = ids[chunk_start : chunk_start + 500]
                marks = ",".join("?" * len(chunk))
                for row in self._conn.execute(
                    f"SELECT {_ATT_COLS} FROM attachments WHERE item_id IN ({marks}) "
                    "ORDER BY item_id, position, id",
                    chunk,
                ):
                    deadlines[row["item_id"]].attachments.append(_row_to_attachment(row))
                for row in self._conn.execute(
                    f"SELECT deadline_id, COUNT(*) AS n FROM items WHERE deadline_id IN ({marks}) "
                    "GROUP BY deadline_id",
                    chunk,
                ):
                    deadlines[row["deadline_id"]].study_count = row["n"]
        return items
