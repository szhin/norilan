"""Hiển thị thông báo. Trên Windows dùng winotify (toast); bấm vào toast sẽ mở link norilan://…"""
from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import zlib
from typing import Protocol

from app.reminders import Notification

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def show(self, note: Notification) -> None: ...


def winotify_available() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("winotify") is not None


def powershell_safe(text: str) -> str:
    """winotify nhúng nội dung vào here-string PowerShell + CDATA: thoát các ký tự đặc biệt.

    Không thoát thì tiêu đề như 'Giá $5' sẽ bị PowerShell hiểu là biến và mất chữ.
    """
    return (
        text.replace("`", "``")
        .replace("$", "`$")
        .replace("]]>", "]] >")
        .replace('"@', '" @')
    )


def toast_tag(key: str) -> str:
    """Tag ngắn, chỉ ký tự an toàn (Windows giới hạn độ dài tag). Mỗi key một tag → không ghi đè nhau."""
    return f"nl{zlib.crc32(key.encode('utf-8')):08x}"


class WinotifyNotifier:
    """Toast Windows 10/11. Chạy trong thread riêng để không chặn giao diện."""

    def __init__(self, app_id: str, icon_path: str = "") -> None:
        self._app_id = app_id
        self._icon = icon_path

    def show(self, note: Notification) -> None:
        threading.Thread(target=self._show, args=(note,), name="toast", daemon=True).start()

    def _show(self, note: Notification) -> None:
        try:
            from winotify import Notification as Toast, audio

            toast = Toast(
                app_id=self._app_id,
                title=powershell_safe(note.title),
                msg=powershell_safe(note.body),
                icon=self._icon,
                duration="long",
                launch=note.url,
            )
            toast.tag = toast_tag(note.key)
            toast.group = "norilan"
            toast.set_audio(audio.Reminder, loop=False)
            toast.show()
            log.info("Toast shown: %s", note.key)
        except Exception:
            log.exception("Không hiện được toast %s", note.key)


class RecordingNotifier:
    """Chỉ ghi lại – dùng cho test và khi không có cách hiển thị nào."""

    def __init__(self) -> None:
        self.shown: list[Notification] = []

    def show(self, note: Notification) -> None:
        self.shown.append(note)
