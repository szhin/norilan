"""Nhắc việc (không Qt): quyết định khi nào gửi thông báo và nội dung của nó.

Qt chỉ gọi `ReminderService.check(now)` định kỳ (QTimer) rồi đưa kết quả cho Notifier hiển thị.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

from app import links
from app.calendar_logic import (
    EXAM_ALERT_DAYS, day_month_text, exam_days_left, format_minutes, place_text, relative_label, round_for_display,
)
from app.i18n import tr
from app.models import CalendarItem, ClassSession, Deadline, Event, Exam, StudyTime
from app.repository import Repository
from app.settings import Settings

log = logging.getLogger(__name__)

DIGEST_STATE_KEY = "last_digest_date"
DIGEST_MAX_LINES = 4
EXAM_ALERT_KEY = "exam_alert_date_{id}"   # app_state: ngày gần nhất đã báo ca thi này
EXAM_ALERT_FROM = time(7, 0)               # báo từ giờ này trở đi (app mở muộn vẫn nhận)


@dataclass(frozen=True, slots=True)
class Notification:
    key: str      # định danh duy nhất (tránh toast ghi đè nhau)
    title: str
    body: str
    url: str      # mở khi bấm vào toast


def _remaining(start: datetime, now: datetime) -> str:
    minutes = round((start - now).total_seconds() / 60)
    if minutes == 0:
        return tr("remind.now")
    if minutes < 0:  # app mở lại muộn, sau cả giờ của mục (vẫn trong khoảng grace của due_reminders)
        return tr("remind.elapsed", span=format_minutes(round_for_display(-minutes)))
    return tr("remind.left", span=format_minutes(round_for_display(minutes)))


def item_notification(item: CalendarItem, now: datetime) -> Notification:
    rel = relative_label(item.starts_at.date(), now.date())
    when_day = rel.lower() if rel else day_month_text(item.starts_at)
    if isinstance(item, Deadline):
        details = [tr("notify.deadline", time=f"{item.due_at:%H:%M}", day=when_day, remaining=_remaining(item.due_at, now))]
        if item.subject:
            details.append(item.subject)
        title = f"⏰ {item.title}"
    elif isinstance(item, StudyTime):
        details = [tr("notify.study", time=f"{item.starts_at:%H:%M}", day=when_day,
                      remaining=_remaining(item.starts_at, now))]
        if item.subject:
            details.append(item.subject)
        title = f"📖 {item.title}"
    elif isinstance(item, Exam):
        details = [tr("notify.exam", time=f"{item.starts_at:%H:%M}", day=when_day,
                      remaining=_remaining(item.starts_at, now))]
        if item.exam_class:
            details.append(f"{tr('field.exam_class')}: {item.exam_class}")
        if item.location:
            details.append(f"📍 {item.location}")
        title = f"🎓 {tr('exam.chip', title=item.title)}"
    elif isinstance(item, ClassSession):
        details = [tr("notify.class", time=f"{item.starts_at:%H:%M}", day=when_day,
                      remaining=_remaining(item.starts_at, now))]
        if place := place_text(item.room, item.location):
            details.append(f"📍 {place}")
        title = f"🏫 {item.title}"
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


def exam_alert_notification(exam: Exam, now: datetime) -> Notification:
    """Toast nhắc ca thi hằng ngày (trong 15 ngày trước ngày thi): 'Còn 5 ngày nữa thi Toán'."""
    days = exam_days_left(exam, now)
    if days <= 0:
        title = tr("exam.alert_today", title=exam.title)
    elif days == 1:
        title = tr("exam.alert_tomorrow", title=exam.title)
    else:
        title = tr("exam.alert_days", count=days, days=days, title=exam.title)
    lines = [tr("exam.alert_body", title=exam.title, time=f"{exam.starts_at:%H:%M}",
                day=day_month_text(exam.starts_at))]
    if exam.exam_class:
        lines.append(f"{tr('field.exam_class')}: {exam.exam_class}")
    if exam.location:
        lines.append(f"📍 {exam.location}")
    return Notification(
        key=f"exam-alert-{exam.id}-{now.date().isoformat()}",
        title=title,
        body="\n".join(lines),
        url=links.item_url(exam.id),  # type: ignore[arg-type]
    )


def study_end_notification(study: StudyTime, now: datetime) -> Notification:
    """Toast 'Chuẩn bị hết thời gian làm bài' – bấm vào mở đúng Study time đó."""
    return Notification(
        key=f"study-end-{study.id}-{study.ends_at:%Y%m%d%H%M}",   # mỗi Study time một khoá → không ghi đè nhau
        title=tr("notify.study_end_title", title=study.title),
        body=tr("notify.study_end_body", time=f"{study.ends_at:%H:%M}", remaining=_remaining(study.ends_at, now)),
        url=links.item_url(study.id),  # type: ignore[arg-type]
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
        notes = [self._item(item, now) for item in self._repo.due_reminders(now)
                 if self._repo.claim_reminder(item.id, now)]  # type: ignore[arg-type]
        # Cảnh báo sắp hết giờ của Study time: nằm trong cùng vòng nhắc này nên chạy cả khi chỉ còn app ở khay.
        for study in self._repo.due_end_warnings(now):
            if self._repo.claim_end_warning(study.id, now):  # type: ignore[arg-type]
                log.info("End warning for study #%s", study.id)
                notes.append(study_end_notification(study, now))
        notes.extend(self._exam_alerts(now))
        digest = self._digest(now)
        if digest is not None:
            notes.append(digest)
        return notes

    def _item(self, item: CalendarItem, now: datetime) -> Notification:
        log.info("Reminder for item #%s", item.id)
        return item_notification(item, now)

    def _exam_alerts(self, now: datetime) -> list[Notification]:
        """Mỗi ngày một toast cho mỗi ca thi sắp tới (từ 15 ngày trước đến chính ngày thi). Không phụ thuộc công
        tắc tổng hợp buổi tối: ca thi luôn được nhắc."""
        if now.time() < EXAM_ALERT_FROM:
            return []
        today = now.date().isoformat()
        notes = []
        for exam in self._repo.upcoming_exams(now, days=EXAM_ALERT_DAYS):
            key = EXAM_ALERT_KEY.format(id=exam.id)
            if self._repo.get_state(key) == today:
                continue
            self._repo.set_state(key, today)
            log.info("Exam alert for #%s (%d days left)", exam.id, exam_days_left(exam, now))
            notes.append(exam_alert_notification(exam, now))
        return notes

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
