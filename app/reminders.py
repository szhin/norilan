"""Nhắc việc (không Qt): quyết định khi nào gửi thông báo và nội dung của nó.

Qt chỉ gọi `ReminderService.check(now)` định kỳ (QTimer) rồi đưa kết quả cho Notifier hiển thị.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from app import links
from app.calendar_logic import day_month_text, format_minutes, relative_label
from app.i18n import tr
from app.models import CalendarItem, Event, Task
from app.repository import Repository
from app.settings import Settings

log = logging.getLogger(__name__)

DIGEST_STATE_KEY = "last_digest_date"
DIGEST_MAX_LINES = 4


@dataclass(frozen=True, slots=True)
class Notification:
    key: str      # định danh duy nhất (tránh toast ghi đè nhau)
    title: str
    body: str
    url: str      # mở khi bấm vào toast


def _round_for_display(minutes: int) -> int:
    """Làm tròn cho dễ đọc ('còn 3 giờ' thay vì 'còn 2 giờ 59 phút')."""
    if minutes >= 1440:
        return round(minutes / 60) * 60
    if minutes >= 120:
        return round(minutes / 15) * 15
    if minutes >= 60:
        return round(minutes / 5) * 5
    return minutes


def _remaining(start: datetime, now: datetime) -> str:
    minutes = round((start - now).total_seconds() / 60)
    if minutes == 0:
        return tr("remind.now")
    if minutes < 0:  # app mở lại muộn, sau cả giờ của mục (vẫn trong khoảng grace của due_reminders)
        return tr("remind.elapsed", span=format_minutes(_round_for_display(-minutes)))
    return tr("remind.left", span=format_minutes(_round_for_display(minutes)))


def item_notification(item: CalendarItem, now: datetime) -> Notification:
    rel = relative_label(item.starts_at.date(), now.date())
    when_day = rel.lower() if rel else day_month_text(item.starts_at)
    if isinstance(item, Task):
        details = [tr("notify.task", time=f"{item.due_at:%H:%M}", day=when_day, remaining=_remaining(item.due_at, now))]
        if item.subject:
            details.append(item.subject)
        title = f"⏰ {item.title}"
    else:
        assert isinstance(item, Event)
        details = [tr("notify.event", time=f"{item.starts_at:%H:%M}", day=when_day,
                      remaining=_remaining(item.starts_at, now))]
        if item.location:
            details.append(f"📍 {item.location}")
        title = f"📅 {item.title}"
    return Notification(
        key=f"item-{item.id}-{item.starts_at:%Y%m%d%H%M}",
        title=title,
        body="\n".join(details),
        url=links.item_url(item.id),  # type: ignore[arg-type]
    )


def digest_notification(items: list[CalendarItem], day: date) -> Notification:
    lines = [f"• {item.starts_at:%H:%M} {item.title}" for item in items[:DIGEST_MAX_LINES]]
    if len(items) > DIGEST_MAX_LINES:
        lines[-1] = tr("notify.digest_more", n=len(items) - DIGEST_MAX_LINES + 1)
    return Notification(
        key=f"digest-{day.isoformat()}",
        title=tr("notify.digest_title", count=len(items)),
        body="\n".join(lines),
        url=links.day_url(day),
    )


class ReminderService:
    def __init__(self, repo: Repository, settings: Callable[[], Settings]) -> None:
        self._repo = repo
        self._settings = settings

    def check(self, now: datetime) -> list[Notification]:
        """Gọi định kỳ. Trả về các thông báo cần hiện ngay; mỗi thông báo chỉ trả về một lần."""
        notes = [self._item(item, now) for item in self._repo.due_reminders(now)]
        digest = self._digest(now)
        if digest is not None:
            notes.append(digest)
        return notes

    def _item(self, item: CalendarItem, now: datetime) -> Notification:
        self._repo.mark_reminder_sent(item.id, now)  # type: ignore[arg-type]
        log.info("Reminder for item #%s", item.id)
        return item_notification(item, now)

    def _digest(self, now: datetime) -> Notification | None:
        """Toast tổng hợp ngày mai, một lần mỗi ngày, từ giờ cấu hình trở đi (app mở muộn vẫn nhận)."""
        settings = self._settings()
        if not settings.daily_digest_enabled or now.time() < settings.digest_time:
            return None
        today = now.date().isoformat()
        if self._repo.get_state(DIGEST_STATE_KEY) == today:
            return None
        self._repo.set_state(DIGEST_STATE_KEY, today)
        tomorrow = now.date() + timedelta(days=1)
        items = self._repo.items_on(tomorrow, include_done=False)
        if not items:
            log.info("Digest: ngày mai không có việc, không gửi toast")
            return None
        log.info("Digest: %d việc ngày mai", len(items))
        return digest_notification(items, tomorrow)
