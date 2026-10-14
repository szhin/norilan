"""Biểu tượng ở khay hệ thống (góc phải thanh taskbar): Mở, Thêm nhanh, Cài đặt, Thoát."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app import APP_NAME


class Tray(QObject):
    open_requested = Signal()
    quick_add_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    message_clicked = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.icon = QSystemTrayIcon(icon, self)
        self.icon.setToolTip(APP_NAME)
        self.menu = QMenu()
        open_action = self.menu.addAction(f"Mở {APP_NAME}")
        font = open_action.font()
        font.setBold(True)
        open_action.setFont(font)
        open_action.triggered.connect(self.open_requested)
        self.menu.addAction("＋ Thêm nhanh bài tập…").triggered.connect(self.quick_add_requested)
        self.menu.addAction("⚙️ Cài đặt…").triggered.connect(self.settings_requested)
        self.menu.addSeparator()
        self.menu.addAction("Thoát").triggered.connect(self.quit_requested)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self.icon.messageClicked.connect(self.message_clicked)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_icon(self, icon: QIcon) -> None:
        self.icon.setIcon(icon)

    def set_tooltip(self, text: str) -> None:
        self.icon.setToolTip(text)

    def notify(self, title: str, body: str, msecs: int = 8000) -> None:
        self.icon.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, msecs)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()
