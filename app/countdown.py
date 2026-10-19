"""Đếm ngược cho Study time (không Qt). Luôn tính lại từ giờ hệ thống, không bao giờ giảm một bộ đếm.

    state(now, start, end) → CountdownState(phase, seconds, fraction)
      upcoming: seconds = số giây tới lúc bắt đầu
      running : seconds = số giây tới lúc kết thúc (start ≤ now < end)
      ended   : seconds = 0
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Phase(StrEnum):
    UPCOMING = "upcoming"
    RUNNING = "running"
    ENDED = "ended"


# Còn ≤ ngưỡng này thì đổi màu cảnh báo và gửi toast "sắp hết giờ"
WARN_BEFORE_END = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class CountdownState:
    phase: Phase
    seconds: int          # xem docstring module
    fraction: float       # phần đã trôi qua của [start, end], 0.0 → 1.0 (để vẽ thanh tiến độ)

    @property
    def is_warning(self) -> bool:
        """Đang chạy và còn ≤ 10 phút."""
        return self.phase == Phase.RUNNING and self.seconds <= WARN_BEFORE_END.total_seconds()


def _ceil_seconds(delta: timedelta) -> int:
    return max(0, math.ceil(delta.total_seconds()))


def state(now: datetime, start: datetime, end: datetime) -> CountdownState:
    if now < start:
        return CountdownState(Phase.UPCOMING, _ceil_seconds(start - now), 0.0)
    if now >= end:
        return CountdownState(Phase.ENDED, 0, 1.0)
    total = (end - start).total_seconds()
    fraction = (now - start).total_seconds() / total if total > 0 else 1.0
    return CountdownState(Phase.RUNNING, _ceil_seconds(end - now), min(1.0, max(0.0, fraction)))


def format_hms(seconds: int) -> str:
    """3600 → '1:00:00', 2700 → '0:45:00', 0 → '0:00:00'. Quá 24 giờ vẫn dồn vào số giờ ('30:00:00')."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def warning_due(now: datetime, start: datetime, end: datetime) -> bool:
    """Đến lúc gửi toast 'sắp hết giờ'? Từ ends_at − 10 phút tới hết giờ; thời lượng < 10 phút thì không bao giờ."""
    if end - start < WARN_BEFORE_END:
        return False
    return max(start, end - WARN_BEFORE_END) <= now < end
