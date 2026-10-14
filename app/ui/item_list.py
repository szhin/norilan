"""Thẻ mục (task/event) và danh sách thẻ có cuộn, dùng cho panel phải và chế độ xem ngày."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app.calendar_logic import chip_state, group_label, item_meta
from app.colors import ColorResolver
from app.models import CalendarItem, Task
from app.ui.theme import repolish
from app.ui.widgets import ElidedLabel, set_strike


class ItemCard(QFrame):
    clicked = Signal(int)
    context_requested = Signal(int, QPoint)

    def __init__(
        self, item: CalendarItem, *, now: datetime, with_date: bool,
        colors: ColorResolver | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ItemCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.item_id: int = item.id  # type: ignore[assignment]
        state = chip_state(item, now)
        colors = colors or ColorResolver()
        tone = colors.tone(item)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        bar = QFrame(objectName="KindBar")
        bar.setFixedWidth(3)
        bar.setProperty("tone", tone)
        bar.setProperty("state", state)
        layout.addWidget(bar)

        column = QVBoxLayout()
        column.setSpacing(2)
        self.title = ElidedLabel(item.title)
        self.title.setObjectName("CardTitle")
        self.title.setProperty("state", state)
        set_strike(self.title, state == "done")
        meta = ElidedLabel(item_meta(item, now, with_date=with_date, show_subject=False))
        meta.setObjectName("CardMeta")
        meta.setProperty("state", state)
        column.addWidget(self.title)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        if isinstance(item, Task) and item.subject:
            # Môn học hiện như thẻ màu (Notion 'select').
            pill = QLabel(item.subject, objectName="Pill")
            pill.setProperty("tone", colors.subject_tone(item.subject))
            meta_row.addWidget(pill)
        meta_row.addWidget(meta, 1)
        column.addLayout(meta_row)
        layout.addLayout(column, 1)

    def set_selected(self, selected: bool) -> None:
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_id)
            event.accept()
        else:
            super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_requested.emit(self.item_id, event.globalPos())
        event.accept()


class ItemListWidget(QScrollArea):
    """Danh sách thẻ, có thể nhóm theo ngày (cho 'Sắp tới', 'Quá hạn')."""

    item_clicked = Signal(int)
    item_context = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(0, 0, 4, 0)
        self._layout.setSpacing(6)
        self.setWidget(container)
        self._cards: list[ItemCard] = []

    def set_items(
        self, items: list[CalendarItem], *, now: datetime, today: date,
        group_by_day: bool = False, empty_text: str = "Không có mục nào.",
        colors: ColorResolver | None = None,
    ) -> None:
        self._clear()
        if not items:
            empty = QLabel(empty_text, objectName="EmptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self._layout.addWidget(empty)
        last_day: date | None = None
        for item in items:
            if group_by_day and item.starts_at.date() != last_day:
                last_day = item.starts_at.date()
                self._layout.addWidget(QLabel(group_label(last_day, today), objectName="GroupHeader"))
            card = ItemCard(item, now=now, with_date=False, colors=colors)
            card.clicked.connect(self.item_clicked)
            card.context_requested.connect(self.item_context)
            self._layout.addWidget(card)
            self._cards.append(card)
        self._layout.addStretch(1)

    def set_selected(self, item_id: int | None) -> None:
        for card in self._cards:
            card.set_selected(card.item_id == item_id)

    def titles(self) -> list[str]:
        """Tiêu đề các thẻ đang hiển thị (dùng cho test)."""
        return [c.title.fullText() for c in self._cards]

    def _clear(self) -> None:
        self._cards.clear()
        while (child := self._layout.takeAt(0)) is not None:
            if (widget := child.widget()) is not None:
                widget.hide()
                widget.deleteLater()
