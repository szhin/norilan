"""Các chế độ xem lịch: tháng (mặc định), tuần, ngày."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.calendar_logic import (
    day_heading,
    month_grid,
    relative_label,
    split_overflow,
    week_dates,
    weekday_short,
)
from app.colors import ColorResolver
from app.i18n import tr
from app.models import CalendarItem
from app.ui.item_list import ItemListWidget
from app.ui.theme import repolish
from app.ui.widgets import Chip, ClickableLabel

MONTH_MAX_CHIPS = 3
WEEK_MAX_CHIPS = 12


def _set_prop(widget: QWidget, name: str, value: bool) -> bool:
    """Đặt dynamic property; trả về True nếu giá trị thay đổi (cần repolish)."""
    if widget.property(name) == value:
        return False
    widget.setProperty(name, value)
    return True


class DayCell(QFrame):
    """Một ô ngày: số ngày + tối đa `max_chips` chip, dư thì hiện '+N mục'."""

    day_clicked = Signal(object)          # date
    day_double_clicked = Signal(object)   # date → tạo deadline mới
    item_clicked = Signal(int, object)    # item_id, date
    item_context = Signal(int, QPoint)    # chuột phải vào chip

    def __init__(self, max_chips: int, *, show_weekday: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DayCell")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._max = max_chips
        self._day: date | None = None
        self._chips: list[Chip] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._weekday: QLabel | None = None
        if show_weekday:
            self._weekday = QLabel(objectName="DayWeekday")
            head.addWidget(self._weekday)
        self._number = QLabel(objectName="DayNumber")
        self._number.setFixedSize(22, 22)
        self._number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._number)
        head.addStretch(1)
        # Nút '+' chỉ hiện khi rê chuột vào ô (giống lịch Notion) – thêm bài tập vào ngày này.
        self._add = QPushButton("+", objectName="CellAdd")
        self._add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add.setToolTip(tr("cell.add_tooltip"))
        self._add.clicked.connect(lambda: self._day and self.day_double_clicked.emit(self._day))
        self._add.hide()
        head.addWidget(self._add)
        layout.addLayout(head)

        self._chip_box = QVBoxLayout()
        self._chip_box.setSpacing(2)
        layout.addLayout(self._chip_box)

        self._more = ClickableLabel()
        self._more.setObjectName("MoreLabel")
        self._more.clicked.connect(lambda: self._day and self.day_clicked.emit(self._day))
        self._more.hide()
        layout.addWidget(self._more)
        layout.addStretch(1)

    @property
    def day(self) -> date | None:
        return self._day

    def set_day(
        self, day: date, items: list[CalendarItem], *,
        outside: bool, today: bool, selected: bool, now: datetime, colors: ColorResolver | None = None,
    ) -> None:
        colors = colors or ColorResolver()
        self._day = day
        self._number.setText(str(day.day))
        if self._weekday is not None:
            self._weekday.setText(weekday_short(day.weekday()))
            if _set_prop(self._weekday, "today", today):
                repolish(self._weekday)

        visible, hidden = split_overflow(items, self._max)
        while len(self._chips) < len(visible):
            chip = Chip(self)
            chip.clicked.connect(self._on_chip_clicked)
            chip.context_requested.connect(self.item_context)
            self._chip_box.addWidget(chip)
            self._chips.append(chip)
        for index, chip in enumerate(self._chips):
            if index < len(visible):
                chip.set_item(visible[index], day, now, colors.tone(visible[index]))
                chip.show()
            else:
                chip.hide()
        self._more.setText(tr("cell.more", count=hidden))
        self._more.setVisible(hidden > 0)

        if _set_prop(self, "outside", outside) | _set_prop(self, "selected", selected):
            repolish(self)
        if _set_prop(self._number, "outside", outside) | _set_prop(self._number, "today", today):
            repolish(self._number)

    # --- dùng cho test / debug
    def chip_texts(self) -> list[str]:
        return [c.fullText() for c in self._chips if not c.isHidden()]

    def chip_tones(self) -> list[str]:
        return [c.property("tone") for c in self._chips if not c.isHidden()]

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._add.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._add.hide()
        super().leaveEvent(event)

    def more_text(self) -> str:
        return self._more.text() if not self._more.isHidden() else ""

    def _on_chip_clicked(self, item_id: int) -> None:
        if self._day is not None:
            self.item_clicked.emit(item_id, self._day)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._day is not None:
            self.day_clicked.emit(self._day)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._day is not None:
            self.day_double_clicked.emit(self._day)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class _GridView(QWidget):
    """Cơ sở cho MonthView/WeekView: lưới DayCell, chuyển tiếp tín hiệu."""

    day_clicked = Signal(object)
    day_double_clicked = Signal(object)
    item_clicked = Signal(int, object)
    item_context = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CalendarArea")
        self._cells: list[DayCell] = []
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)

    def _add_cell(self, cell: DayCell, row: int, col: int) -> None:
        cell.day_clicked.connect(self.day_clicked)
        cell.day_double_clicked.connect(self.day_double_clicked)
        cell.item_clicked.connect(self.item_clicked)
        cell.item_context.connect(self.item_context)
        self._grid.addWidget(cell, row, col)
        self._cells.append(cell)

    def cell_for(self, day: date) -> DayCell | None:
        return next((c for c in self._cells if c.day == day), None)


class MonthView(_GridView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for col in range(7):
            header = QLabel(weekday_short(col), objectName="WeekdayHeader")
            self._grid.addWidget(header, 0, col)
            self._grid.setColumnStretch(col, 1)
        for row in range(6):
            self._grid.setRowStretch(row + 1, 1)
            for col in range(7):
                self._add_cell(DayCell(MONTH_MAX_CHIPS), row + 1, col)

    def set_data(
        self, selected: date, grouped: dict[date, list[CalendarItem]], *, today: date, now: datetime,
        colors: ColorResolver | None = None,
    ) -> None:
        for cell, day in zip(self._cells, month_grid(selected.year, selected.month)):
            cell.set_day(
                day, grouped.get(day, []),
                outside=day.month != selected.month, today=day == today,
                selected=day == selected, now=now, colors=colors,
            )


class WeekView(_GridView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for col in range(7):
            self._grid.setColumnStretch(col, 1)
            self._add_cell(DayCell(WEEK_MAX_CHIPS, show_weekday=True), 0, col)

    def set_data(
        self, selected: date, grouped: dict[date, list[CalendarItem]], *, today: date, now: datetime,
        colors: ColorResolver | None = None,
    ) -> None:
        for cell, day in zip(self._cells, week_dates(selected)):
            cell.set_day(
                day, grouped.get(day, []), outside=False,
                today=day == today, selected=day == selected, now=now, colors=colors,
            )


class DayView(QWidget):
    """Xem theo ngày: danh sách đầy đủ, thẻ lớn."""

    item_clicked = Signal(int, object)
    item_context = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CalendarArea")
        self._day: date | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 32, 12)
        layout.setSpacing(10)
        self._heading = QLabel(objectName="PanelTitle")
        layout.addWidget(self._heading)
        self._list = ItemListWidget()
        self._list.item_clicked.connect(lambda item_id: self._day and self.item_clicked.emit(item_id, self._day))
        self._list.item_context.connect(self.item_context)
        layout.addWidget(self._list, 1)

    def set_data(
        self, selected: date, grouped: dict[date, list[CalendarItem]], *, today: date, now: datetime,
        colors: ColorResolver | None = None,
    ) -> None:
        self._day = selected
        rel = relative_label(selected, today)
        self._heading.setText(f"{rel} · {day_heading(selected)}" if rel else day_heading(selected))
        self._list.set_items(grouped.get(selected, []), now=now, today=today, colors=colors,
                             empty_text=tr("dayview.empty", button=tr("toolbar.new_deadline")))

    def set_selected_item(self, item_id: int | None) -> None:
        self._list.set_selected(item_id)
