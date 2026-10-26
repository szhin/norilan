"""Tự xoá Deadline và Event cũ (không Qt). Không bao giờ đụng tới Study time, buổi học hay ca thi.

Hai quy tắc (mỗi quy tắc một công tắc trong Cài đặt, đều bật mặc định), cùng độ trễ AUTO_DELETE_DELAY:
  1. Đã xong:   now ≥ completed_at + 24h                      → xoá
  2. Chưa xong: now ≥ max(due, due_changed_at) + 24h          → xoá
     (due_changed_at = lần cuối đổi hạn hoặc mở lại, để deadline vừa sửa không bị xoá ngay)

Event: now ≥ ends_at + 12h → xoá (không có công tắc; luôn sao lưu trước khi xoá).

Quyết định chỉ dựa trên mốc thời gian so với giờ hệ thống – không có bộ đếm – nên app tắt nhiều ngày vẫn đúng.
Xoá đi qua cùng đường xoá thủ công của repository ('unlink': Study time được giữ, chỉ mất liên kết).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Collection, Iterable

from app.backup import create_backup, prune_backups
from app.files import remove_orphan_copies
from app.models import Deadline, Event
from app.repository import Repository
from app.settings import Settings

log = logging.getLogger(__name__)

AUTO_DELETE_DELAY = timedelta(hours=24)
EVENT_DELETE_DELAY = timedelta(hours=12)
BACKUP_STATE_KEY = "last_autoclean_backup_date"
BACKUP_PREFIX = "truoc-khi-tu-xoa"
BACKUPS_KEPT = 5


def expiry_time(deadline: Deadline, settings: Settings) -> datetime | None:
    """Thời điểm Deadline này bị tự xoá; None nếu không áp dụng (công tắc tắt hoặc chưa đủ dữ liệu)."""
    if deadline.is_done:
        if not settings.auto_delete_done or deadline.completed_at is None:
            return None   # thiếu mốc hoàn thành thì không bao giờ đoán mà xoá
        return deadline.completed_at + AUTO_DELETE_DELAY
    if not settings.auto_delete_overdue:
        return None
    reference = max(deadline.due_at, deadline.due_changed_at or deadline.due_at)
    return reference + AUTO_DELETE_DELAY


def select_expired(
    deadlines: Iterable[Deadline], now: datetime, settings: Settings, *, skip_ids: Collection[int] = (),
) -> list[Deadline]:
    """Các Deadline đã đến hạn bị xoá. `skip_ids`: Deadline đang mở trong hộp thoại sửa (bỏ qua lượt này)."""
    result = []
    for deadline in deadlines:
        if deadline.id in skip_ids:
            continue
        expires = expiry_time(deadline, settings)
        if expires is not None and now >= expires:
            result.append(deadline)
    return result


def select_expired_events(
    events: Iterable[Event], now: datetime, *, skip_ids: Collection[int] = (),
) -> list[Event]:
    """Các Event đã kết thúc quá 12 giờ. `skip_ids`: Event đang mở trong hộp thoại sửa (bỏ qua lượt này)."""
    return [e for e in events if e.id not in skip_ids and now >= e.ends_at + EVENT_DELETE_DELAY]


def time_until_delete(deadline: Deadline, now: datetime, settings: Settings) -> timedelta | None:
    """Còn bao lâu nữa thì bị tự xoá – chỉ trả về khi cần hiện cho người dùng: Deadline đã xong, hoặc đang trong
    24 giờ quá hạn cuối cùng. None = ẩn (công tắc tắt, hoặc chưa tới giai đoạn đó)."""
    expires = expiry_time(deadline, settings)
    if expires is None:
        return None
    remaining = expires - now
    if not deadline.is_done and remaining > AUTO_DELETE_DELAY:
        return None
    return max(remaining, timedelta(0))


def count_expired(repo: Repository, now: datetime, settings: Settings) -> int:
    """Số Deadline sẽ bị xoá ngay với cài đặt này (để cảnh báo trước khi lưu Cài đặt)."""
    return len(select_expired(repo.list_deadlines(), now, settings))


def _backup_once_a_day(repo: Repository, now: datetime, backups_dir: Path, data_dir: Path) -> None:
    today = now.date().isoformat()
    if repo.get_state(BACKUP_STATE_KEY) == today:
        return
    dest = backups_dir / f"{BACKUP_PREFIX}-{now:%Y%m%d-%H%M%S}.zip"
    create_backup(repo.connection, dest, data_dir=data_dir, now=now)
    repo.set_state(BACKUP_STATE_KEY, today)
    prune_backups(backups_dir, keep=BACKUPS_KEPT, pattern=f"{BACKUP_PREFIX}-*.zip")


def clean_expired(
    repo: Repository, settings: Settings, now: datetime, *, attachments_dir: Path, backups_dir: Path,
    data_dir: Path, skip_ids: Collection[int] = (),
) -> tuple[int, int]:
    """Xoá các Deadline và Event đã hết hạn lưu. Trả về (số Deadline, số Event) đã xoá.

    Có gì cần xoá thì sao lưu trước (tối đa một lần mỗi ngày; sao lưu lỗi ⇒ không xoá gì), rồi xoá trong một
    transaction và dọn bản sao đính kèm không còn ai dùng.
    """
    expired = select_expired(repo.list_deadlines(), now, settings, skip_ids=skip_ids)
    expired_events = select_expired_events(repo.list_events(), now, skip_ids=skip_ids)
    if not expired and not expired_events:
        return 0, 0
    try:
        _backup_once_a_day(repo, now, backups_dir, data_dir)
    except Exception:
        log.exception("Sao lưu trước khi tự xoá thất bại – bỏ qua lượt tự xoá này")
        return 0, 0
    removed = repo.delete_deadlines([d.id for d in expired], study_times="unlink")  # type: ignore[misc]
    removed_events = repo.delete_events([e.id for e in expired_events])  # type: ignore[misc]
    if removed:
        remove_orphan_copies(attachments_dir, repo.all_attachment_targets())
        log.info("Auto-delete: removed %d deadline(s)", removed)
    if removed_events:
        log.info("Auto-delete: removed %d event(s)", removed_events)
    return removed, removed_events


def auto_clean(
    repo: Repository, settings: Settings, now: datetime, *, attachments_dir: Path, backups_dir: Path,
    data_dir: Path, skip_ids: Collection[int] = (),
) -> int:
    """Như clean_expired nhưng chỉ trả về tổng số mục đã xoá."""
    return sum(clean_expired(
        repo, settings, now, attachments_dir=attachments_dir, backups_dir=backups_dir, data_dir=data_dir,
        skip_ids=skip_ids,
    ))
