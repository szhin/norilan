"""Widget nhỏ dùng chung: nhãn tự cắt '…', nhãn bấm được, chip trên ô lịch."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from app.calendar_logic import chip_state, chip_text
from app.models import CalendarItem
from app.ui.theme import repolish


def set_strike(label: QLabel, on: bool) -> None:
    font = label.font()
    if font.strikeOut() != on:
        font.setStrikeOut(on)
        label.setFont(font)


class ElidedLabel(QLabel):
    """QLabel một dòng, tự cắt chữ thành '…' theo bề rộng; tooltip giữ nguyên văn bản đầy đủ."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(12)
        self.setFullText(text)

    def fullText(self) -> str:
        return self._full

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        super().setText(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()

    def _elide(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            return
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width)
        if elided != self.text():
            super().setText(elided)


class ElidedButton(QPushButton):
    """Nút một dòng tự cắt chữ '…' (không kéo giãn layout theo độ dài tên file)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(40)
        super().setText(text)

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        width = self.contentsRect().width() - 8
        if width > 0:
            super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width))


class ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class Chip(ElidedLabel):
    """Một mục trên ô lịch. Màu theo loại (task/event) và trạng thái (done/overdue/high)."""

    clicked = Signal(int)
    context_requested = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("Chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._item_id: int | None = None

    def set_item(self, item: CalendarItem, day: date, now: datetime, tone: str = "blue") -> None:
        self._item_id = item.id
        state = chip_state(item, now)
        changed = self.property("tone") != tone or self.property("state") != state
        self.setProperty("tone", tone)
        self.setProperty("state", state)
        if changed:
            repolish(self)
        set_strike(self, state == "done")
        self.setFullText(chip_text(item, day))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._item_id is not None:
            self.clicked.emit(self._item_id)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()  # không để nhấp đúp chip lan ra ô ngày (tránh tạo bài tập mới)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self._item_id is not None:
            self.context_requested.emit(self._item_id, event.globalPos())
            event.accept()
