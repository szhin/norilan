"""Truy cập dữ liệu: CRUD Deadline/Event/Attachment và các truy vấn cho lịch, sidebar, tìm kiếm, nhắc việc.

Mọi hàm nhận `now` từ bên ngoài (không tự gọi datetime.now()) để dễ test.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from app.countdown import warning_due
from app.db import fold_text
from app.i18n import tr
from app.models import (
    Attachment,
    AttachmentKind,
    CalendarItem,
    Event,
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
    "created_at, updated_at"
)
# Cùng giờ thì: deadline → study → event.
_KIND_ORDER_SQL = "CASE kind WHEN 'deadline' THEN 0 WHEN 'study' THEN 1 ELSE 2 END"
_ATT_COLS = "id, item_id, kind, target, display_name, position, created_at"

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
    return Event(
        starts_at=from_iso(row["starts_at"]),
        ends_at=from_iso(row["ends_at"]),
        location=row["location"],
        color=row["color"],
        **common,
    )


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
                       remind_before_minutes, completed_at, created_at, updated_at)
                   VALUES ('deadline', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deadline.title.strip(), deadline.notes, to_iso(deadline.due_at), str(deadline.status),
                    int(deadline.priority), deadline.subject.strip(), deadline.remind_before_minutes,
                    ts if deadline.status == DeadlineStatus.DONE else None, ts, ts,
                ),
            )
            deadline_id = cur.lastrowid
            self._sync_attachments(deadline_id, deadline.attachments)
        log.info("Added deadline #%s %r", deadline_id, deadline.title)
        return self._require_deadline(deadline_id)

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
                f"""UPDATE items SET {_RESET_REMINDER_SQL}, {_COMPLETED_SQL},
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
                f"""UPDATE items SET {_COMPLETED_SQL}, status = :status, updated_at = :now
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
        if study_times not in ("unlink", "delete"):
            raise ValueError(f"study_times phải là 'unlink' hoặc 'delete', không phải {study_times!r}")
        with self._conn:
            if study_times == "delete":
                self._conn.execute("DELETE FROM items WHERE kind = 'study' AND deadline_id = ?", (deadline_id,))
            cur = self._conn.execute("DELETE FROM items WHERE id = ? AND kind = 'deadline'", (deadline_id,))
        return cur.rowcount > 0  # còn Study time thì ON DELETE SET NULL tự gỡ liên kết

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
        counts = {"deadline": 0, "event": 0, "study": 0}
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
        """Tìm theo tiêu đề/ghi chú/môn học/địa điểm, không phân biệt dấu và hoa thường."""
        clauses: list[str] = []
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

    # ---------------------------------------------------------- Dọn dẹp
    def done_deadlines_completed_before(self, cutoff: datetime) -> list[Deadline]:
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE kind = 'deadline' AND status = 'done' AND completed_at IS NOT NULL AND completed_at < ?
                ORDER BY completed_at""",
            (to_iso(cutoff),),
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def purge_done_deadlines(self, cutoff: datetime) -> int:
        """Xoá vĩnh viễn deadline đã xong trước `cutoff` (đính kèm trong DB xoá theo). Trả về số đã xoá."""
        with self._conn:
            cur = self._conn.execute(
                """DELETE FROM items WHERE kind = 'deadline' AND status = 'done'
                   AND completed_at IS NOT NULL AND completed_at < ?""",
                (to_iso(cutoff),),
            )
        if cur.rowcount:
            log.info("Purged %d done deadlines completed before %s", cur.rowcount, cutoff)
        return cur.rowcount

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
        """Các môn học đã dùng, môn dùng gần nhất trước (gợi ý trong ô nhập)."""
        rows = self._conn.execute(
            """SELECT subject FROM items WHERE kind IN ('deadline', 'study') AND subject != ''
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
        """Chuyển row → model và nạp đính kèm cho mọi deadline bằng một truy vấn (tránh N+1)."""
        items = [_row_to_item(r) for r in rows]
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
