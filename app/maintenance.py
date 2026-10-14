"""Dọn dẹp định kỳ (không Qt): tự xoá bài tập đã xong sau N ngày."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from app.files import remove_orphan_copies
from app.repository import Repository

log = logging.getLogger(__name__)

PURGE_STATE_KEY = "last_purge_date"


def purge_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def count_purgeable(repo: Repository, days: int, now: datetime) -> int:
    """Số bài tập sẽ bị xoá nếu đặt 'tự xoá sau `days` ngày' (để cảnh báo trước khi lưu)."""
    if days <= 0:
        return 0
    return len(repo.done_tasks_completed_before(purge_cutoff(now, days)))


def purge_done(repo: Repository, days: int, now: datetime, *, attachments_dir: Path, force: bool = False) -> int:
    """Xoá bài tập đã xong quá `days` ngày. Mặc định mỗi ngày chạy một lần; force=True để chạy ngay.

    Bản sao đính kèm của các bài bị xoá cũng được dọn. Trả về số bài đã xoá.
    """
    if days <= 0:
        return 0
    today = now.date().isoformat()
    if not force and repo.get_state(PURGE_STATE_KEY) == today:
        return 0
    repo.set_state(PURGE_STATE_KEY, today)
    removed = repo.purge_done_tasks(purge_cutoff(now, days))
    if removed:
        remove_orphan_copies(attachments_dir, repo.all_attachment_targets())
        log.info("Auto-delete: removed %d done tasks older than %d days", removed, days)
    return removed
