"""Truy cập dữ liệu: CRUD Task/Event/Attachment và các truy vấn cho lịch, sidebar, tìm kiếm, nhắc việc.

Mọi hàm nhận `now` từ bên ngoài (không tự gọi datetime.now()) để dễ test.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from app.db import fold_text
from app.models import (
    Attachment,
    AttachmentKind,
    CalendarItem,
    Event,
    Priority,
    Task,
    TaskStatus,
)
from app.timeutil import day_range, from_iso, now as _now, opt_from_iso, to_iso

log = logging.getLogger(__name__)

_ITEM_COLS = (
    "id, kind, title, notes, starts_at, ends_at, location, status, priority, subject, "
    "remind_before_minutes, reminder_sent_at, color, completed_at, created_at, updated_at"
)
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
    if row["kind"] == Task.KIND:
        return Task(
            due_at=from_iso(row["starts_at"]),
            status=TaskStatus(row["status"]),
            priority=Priority(row["priority"]),
            subject=row["subject"],
            completed_at=opt_from_iso(row["completed_at"]),
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

    # ------------------------------------------------------------------ Task
    def add_task(self, task: Task) -> Task:
        task.validate()
        ts = to_iso(_now())
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO items (kind, title, notes, starts_at, status, priority, subject,
                       remind_before_minutes, completed_at, created_at, updated_at)
                   VALUES ('task', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.title.strip(), task.notes, to_iso(task.due_at), str(task.status),
                    int(task.priority), task.subject.strip(), task.remind_before_minutes,
                    ts if task.status == TaskStatus.DONE else None, ts, ts,
                ),
            )
            task_id = cur.lastrowid
            self._sync_attachments(task_id, task.attachments)
        log.info("Added task #%s %r", task_id, task.title)
        return self._require_task(task_id)

    def update_task(self, task: Task) -> Task:
        """Ghi đè mọi trường, đồng bộ danh sách đính kèm theo task.attachments."""
        if task.id is None:
            raise ValueError("Task chưa có id")
        task.validate()
        params = {
            "id": task.id, "title": task.title.strip(), "notes": task.notes,
            "starts_at": to_iso(task.due_at), "status": str(task.status),
            "priority": int(task.priority), "subject": task.subject.strip(),
            "remind": task.remind_before_minutes, "now": to_iso(_now()),
        }
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_RESET_REMINDER_SQL}, {_COMPLETED_SQL},
                       title = :title, notes = :notes, starts_at = :starts_at, status = :status,
                       priority = :priority, subject = :subject, remind_before_minutes = :remind,
                       updated_at = :now
                   WHERE id = :id AND kind = 'task'""",
                params,
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"Không tìm thấy task #{task.id}")
            self._sync_attachments(task.id, task.attachments)
        return self._require_task(task.id)

    def set_task_status(self, task_id: int, status: TaskStatus, *, at: datetime | None = None) -> None:
        """Đổi trạng thái. Chuyển sang 'Xong' thì ghi thời điểm hoàn thành (`at`, mặc định bây giờ)."""
        status = TaskStatus(status)
        with self._conn:
            cur = self._conn.execute(
                f"""UPDATE items SET {_COMPLETED_SQL}, status = :status, updated_at = :now
                    WHERE id = :id AND kind = 'task'""",
                {"status": str(status), "now": to_iso(at or _now()), "id": task_id},
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"Không tìm thấy task #{task_id}")

    def get_task(self, task_id: int) -> Task | None:
        item = self.get_item(task_id)
        return item if isinstance(item, Task) else None

    def _require_task(self, task_id: int) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise NotFoundError(f"Không tìm thấy task #{task_id}")
        return task

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

    # ------------------------------------------------------------ Chung
    def get_item(self, item_id: int) -> CalendarItem | None:
        row = self._conn.execute(f"SELECT {_ITEM_COLS} FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def delete_item(self, item_id: int) -> bool:
        """Xoá task/event (đính kèm bị xoá theo). Trả về False nếu không tồn tại."""
        with self._conn:
            cur = self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return cur.rowcount > 0

    def count_items(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT kind, COUNT(*) AS n FROM items GROUP BY kind").fetchall()
        counts = {"task": 0, "event": 0}
        counts.update({r["kind"]: r["n"] for r in rows})
        return counts

    # ------------------------------------------------------- Attachment
    def list_attachments(self, task_id: int) -> list[Attachment]:
        rows = self._conn.execute(
            f"SELECT {_ATT_COLS} FROM attachments WHERE item_id = ? ORDER BY position, id", (task_id,)
        ).fetchall()
        return [_row_to_attachment(r) for r in rows]

    def add_attachment(self, task_id: int, attachment: Attachment) -> Attachment:
        """Thêm một đính kèm vào cuối danh sách của task."""
        attachment.validate()
        if self.get_task(task_id) is None:
            raise NotFoundError(f"Không tìm thấy task #{task_id}")
        with self._conn:
            pos = self._conn.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM attachments WHERE item_id = ?", (task_id,)
            ).fetchone()[0]
            att_id = self._insert_attachment(task_id, attachment, pos)
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
        """Mục giao với khoảng [start, end): task có hạn trong khoảng, event đang diễn ra trong khoảng."""
        sql = f"""SELECT {_ITEM_COLS} FROM items
                  WHERE starts_at < :end AND (starts_at >= :start OR ends_at > :start)"""
        if not include_done:
            sql += " AND (kind = 'event' OR status != 'done')"
        sql += " ORDER BY starts_at, kind DESC, id"
        rows = self._conn.execute(sql, {"start": to_iso(start), "end": to_iso(end)}).fetchall()
        return self._hydrate(rows)

    def items_on(self, day: date, *, include_done: bool = True) -> list[CalendarItem]:
        start, end = day_range(day)
        return self.items_between(start, end, include_done=include_done)

    def tasks_due_on(self, day: date, *, include_done: bool = False) -> list[Task]:
        """Dùng cho toast tổng hợp buổi tối: 'Ngày mai bạn có N việc'."""
        return [i for i in self.items_on(day, include_done=include_done) if isinstance(i, Task)]

    def overdue_tasks(self, now: datetime) -> list[Task]:
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE kind = 'task' AND status != 'done' AND starts_at < ?
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
        status: TaskStatus | None = None,
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
            clauses.append("kind = 'task' AND status = :status")
            params["status"] = str(TaskStatus(status))
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
    def done_tasks_completed_before(self, cutoff: datetime) -> list[Task]:
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE kind = 'task' AND status = 'done' AND completed_at IS NOT NULL AND completed_at < ?
                ORDER BY completed_at""",
            (to_iso(cutoff),),
        ).fetchall()
        return self._hydrate(rows)  # type: ignore[return-value]

    def purge_done_tasks(self, cutoff: datetime) -> int:
        """Xoá vĩnh viễn bài tập đã xong trước `cutoff` (đính kèm trong DB xoá theo). Trả về số đã xoá."""
        with self._conn:
            cur = self._conn.execute(
                """DELETE FROM items WHERE kind = 'task' AND status = 'done'
                   AND completed_at IS NOT NULL AND completed_at < ?""",
                (to_iso(cutoff),),
            )
        if cur.rowcount:
            log.info("Purged %d done tasks completed before %s", cur.rowcount, cutoff)
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
            """SELECT subject FROM items WHERE kind = 'task' AND subject != ''
               GROUP BY subject ORDER BY MAX(updated_at) DESC, subject"""
        ).fetchall()
        return [r[0] for r in rows]

    # ---------------------------------------------------------- Nhắc việc
    def due_reminders(
        self, now: datetime, *, grace: timedelta = timedelta(hours=1)
    ) -> list[CalendarItem]:
        """Mục đã đến giờ nhắc mà chưa nhắc. Bỏ qua task đã xong và mục đã trôi qua quá `grace`."""
        rows = self._conn.execute(
            f"""SELECT {_ITEM_COLS} FROM items
                WHERE remind_before_minutes IS NOT NULL
                  AND reminder_sent_at IS NULL
                  AND (kind = 'event' OR status != 'done')
                  AND strftime('%Y-%m-%dT%H:%M:%S', starts_at,
                               printf('-%d minutes', remind_before_minutes)) <= :now
                  AND starts_at >= :earliest
                ORDER BY starts_at, id""",
            {"now": to_iso(now), "earliest": to_iso(now - grace)},
        ).fetchall()
        return self._hydrate(rows)

    def mark_reminder_sent(self, item_id: int, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE items SET reminder_sent_at = ? WHERE id = ?", (to_iso(at), item_id)
            )

    # ------------------------------------------------------------ Nội bộ
    def _hydrate(self, rows: Iterable[sqlite3.Row]) -> list[CalendarItem]:
        """Chuyển row → model và nạp đính kèm cho mọi task bằng một truy vấn (tránh N+1)."""
        items = [_row_to_item(r) for r in rows]
        tasks = {i.id: i for i in items if isinstance(i, Task)}
        if tasks:
            ids = list(tasks)
            for chunk_start in range(0, len(ids), 500):
                chunk = ids[chunk_start : chunk_start + 500]
                marks = ",".join("?" * len(chunk))
                for row in self._conn.execute(
                    f"SELECT {_ATT_COLS} FROM attachments WHERE item_id IN ({marks}) "
                    "ORDER BY item_id, position, id",
                    chunk,
                ):
                    tasks[row["item_id"]].attachments.append(_row_to_attachment(row))
        return items
