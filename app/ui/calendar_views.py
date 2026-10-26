"""Các chế độ xem lịch: tháng (mặc định, lưới ô ngày), tuần và ngày (lưới giờ)."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.calendar_logic import (
    Placement,
    block_text,
    chip_label,
    chip_state,
    layout_day,
    month_grid,
    split_overflow,
    week_dates,
    weekday_short,
)
from app.colors import ColorResolver
from app.i18n import tr
from app.models import CalendarItem, Deadline, Exam
from app.ui.theme import STATE_OWNS_COLOR, apply_tone, current_tokens, repolish
from app.ui.widgets import Chip, ClickableLabel, set_strike

MONTH_MAX_CHIPS = 3


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

        items = sorted(items, key=lambda item: not isinstance(item, Exam))   # ca thi luôn nằm trên, không bị '+N'
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

        has_exam = any(isinstance(item, Exam) for item in items)   # ngày có ca thi: cả ô nền đỏ nhạt
        if (_set_prop(self, "outside", outside) | _set_prop(self, "selected", selected)
                | _set_prop(self, "exam", has_exam)):
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


# ------------------------------------------------------------------ Lưới giờ (tuần / ngày)
HOUR_HEIGHT = 48          # px cho một giờ
GUTTER = 56               # cột nhãn giờ bên trái
HEADER_HEIGHT = 40
DEFAULT_SCROLL_HOUR = 7   # đổi tuần/ngày thì cuộn tới mục sớm nhất; không có mục nào thì tới 7:00


class TimeBlock(QLabel):
    """Một mục trên lưới giờ: khối cao theo thời lượng, màu như chip của lịch tháng."""

    clicked = Signal(int)
    context_requested = Signal(int, QPoint)

    def __init__(self, item: CalendarItem, day: date, now: datetime, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Chip")
        self.setProperty("block", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.item_id: int = item.id  # type: ignore[assignment]
        state = chip_state(item, now)
        self.setProperty("state", state)
        self.setProperty("kind", item.KIND)
        self.setProperty("zero", isinstance(item, Deadline) and item.study_count == 0 and not item.is_done)
        self.setProperty("selected", False)
        apply_tone(self, tone, active=state not in STATE_OWNS_COLOR)
        repolish(self)
        set_strike(self, state == "done")
        self._full = block_text(item, day)
        # Deadline chỉ là một dòng trong khối thấp: cắt '…' thay vì xuống dòng (khối hẹp sẽ chỉ còn icon).
        self.setWordWrap(not isinstance(item, Deadline))
        self.setText(self._full)
        self.setToolTip(chip_label(item, day) if isinstance(item, Deadline) else self._full)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if not self.wordWrap() and (width := self.contentsRect().width()) > 0:
            self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width))

    def set_selected(self, selected: bool) -> None:
        if _set_prop(self, "selected", selected):
            repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_id)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()  # không để nhấp đúp lan ra cột ngày (tránh tạo deadline mới)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_requested.emit(self.item_id, event.globalPos())
        event.accept()


class DayColumn(QFrame):
    """Cột một ngày trên lưới giờ: vạch mỗi giờ, khối mục đặt theo giờ, vạch 'bây giờ' nếu là hôm nay."""

    day_clicked = Signal(object)
    day_double_clicked = Signal(object)
    item_clicked = Signal(int, object)
    item_context = Signal(int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DayCell")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._day: date | None = None
        self.blocks: list[TimeBlock] = []
        self.placements: list[Placement] = []
        self._now_minute: int | None = None

    @property
    def day(self) -> date | None:
        return self._day

    def set_day(
        self, day: date, items: list[CalendarItem], *, today: bool, selected: bool, now: datetime,
        colors: ColorResolver,
    ) -> None:
        self._day = day
        for block in self.blocks:
            block.hide()
            block.deleteLater()
        self.placements = layout_day(items, day)
        self.blocks = []
        for placement in self.placements:
            block = TimeBlock(placement.item, day, now, colors.tone(placement.item), self)
            block.clicked.connect(lambda item_id: self._day and self.item_clicked.emit(item_id, self._day))
            block.context_requested.connect(self.item_context)
            block.show()
            self.blocks.append(block)
        if _set_prop(self, "selected", selected) | _set_prop(self, "exam", any(isinstance(i, Exam) for i in items)):
            repolish(self)
        self._now_minute = now.hour * 60 + now.minute if today else None
        self._relayout()
        self.update()

    def set_now(self, now: datetime) -> None:
        if self._day is not None and self._day == now.date():
            self._now_minute = now.hour * 60 + now.minute
            self.update()

    def set_selected_item(self, item_id: int | None) -> None:
        for block in self.blocks:
            block.set_selected(block.item_id == item_id)

    def titles(self) -> list[str]:
        """Tiêu đề các mục trong cột theo thứ tự giờ (dùng cho test)."""
        return [p.item.title for p in self.placements]

    def _relayout(self) -> None:
        inner = self.width() - 4
        for block, p in zip(self.blocks, self.placements):
            width = inner / p.columns
            top = p.start * HOUR_HEIGHT / 60
            height = (p.end - p.start) * HOUR_HEIGHT / 60
            block.setGeometry(round(2 + p.column * width), round(top) + 1,
                              max(8, round(width) - 2), max(16, round(height) - 2))

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setPen(QPen(QColor(tokens["border"]), 1))
        for hour in range(1, 24):
            y = hour * HOUR_HEIGHT
            painter.drawLine(0, y, self.width(), y)
        if self._now_minute is not None:
            y = round(self._now_minute * HOUR_HEIGHT / 60)
            color = QColor(tokens["danger"])
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(color, 2))
            painter.drawLine(0, y, self.width(), y)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(4, y), 4, 4)
        painter.end()

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


class _Gutter(QFrame):
    """Cột nhãn giờ bên trái ('01:00' … '23:00'), nền giống ô lịch để đọc được trên ảnh nền."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridGutter")

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(current_tokens()["text_muted"]))
        for hour in range(1, 24):
            y = hour * HOUR_HEIGHT
            painter.drawText(QRect(0, y - 8, GUTTER - 8, 16),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), f"{hour:02d}:00")
        painter.end()


class _GridBody(QWidget):
    """Phần cuộn của lưới giờ: cột nhãn giờ bên trái + các cột ngày đặt cạnh nhau."""

    resized = Signal()

    def __init__(self, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(24 * HOUR_HEIGHT + 1)
        self.gutter = _Gutter(self)
        self.columns = [DayColumn(self) for _ in range(count)]

    def column_x(self, index: int) -> tuple[int, int]:
        """(x, bề rộng) của cột thứ `index` – header dùng chung để thẳng hàng với cột."""
        width = (self.width() - GUTTER) / len(self.columns)
        left = GUTTER + round(index * width)
        return left, GUTTER + round((index + 1) * width) - left

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.gutter.setGeometry(0, 0, GUTTER, self.height())
        for index, column in enumerate(self.columns):
            x, width = self.column_x(index)
            column.setGeometry(x, 0, width, self.height())
        self.resized.emit()


class _DayHeader(QFrame):
    """Đầu cột: 'T3  20' (hôm nay tô màu nhấn); bấm để chọn ngày."""

    clicked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridDayHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._day: date | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(6)
        self._weekday = QLabel(objectName="DayWeekday")
        self._number = QLabel(objectName="DayNumber")
        self._number.setFixedSize(22, 22)
        self._number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._weekday)
        layout.addWidget(self._number)
        layout.addStretch(1)

    def set_day(self, day: date, *, today: bool) -> None:
        self._day = day
        self._weekday.setText(weekday_short(day.weekday()))
        self._number.setText(str(day.day))
        if _set_prop(self._weekday, "today", today):
            repolish(self._weekday)
        if _set_prop(self._number, "today", today):
            repolish(self._number)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._day is not None:
            self.clicked.emit(self._day)
            event.accept()
        else:
            super().mousePressEvent(event)


class TimeGridView(QWidget):
    """Xem tuần / ngày dạng lưới giờ: nhãn giờ bên trái, mỗi mục nằm đúng khung giờ của nó."""

    day_clicked = Signal(object)
    day_double_clicked = Signal(object)
    item_clicked = Signal(int, object)
    item_context = Signal(int, QPoint)

    def __init__(self, days: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CalendarArea")
        self._count = days
        self._days: list[date] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QFrame(objectName="GridHeader")
        self._header.setFixedHeight(HEADER_HEIGHT)
        self._headers = [_DayHeader(self._header) for _ in range(days)]
        for header in self._headers:
            header.clicked.connect(self.day_clicked)
        layout.addWidget(self._header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = _GridBody(days)
        self._body.resized.connect(self._layout_header)
        self._scroll.setWidget(self._body)
        layout.addWidget(self._scroll, 1)
        for column in self._body.columns:
            column.day_clicked.connect(self.day_clicked)
            column.day_double_clicked.connect(self.day_double_clicked)
            column.item_clicked.connect(self.item_clicked)
            column.item_context.connect(self.item_context)

    def _layout_header(self) -> None:
        for index, header in enumerate(self._headers):
            x, width = self._body.column_x(index)
            header.setGeometry(x + 1, 4, width - 2, HEADER_HEIGHT - 8)

    def set_data(
        self, selected: date, grouped: dict[date, list[CalendarItem]], *, today: date, now: datetime,
        colors: ColorResolver | None = None,
    ) -> None:
        colors = colors or ColorResolver()
        days = week_dates(selected) if self._count == 7 else [selected]
        for column, header, day in zip(self._body.columns, self._headers, days):
            header.set_day(day, today=day == today)
            # Xem ngày chỉ có một cột: không tô nền "đang chọn" cho cả lưới.
            column.set_day(day, grouped.get(day, []), today=day == today,
                           selected=day == selected and self._count > 1, now=now, colors=colors)
        if days != self._days:  # chỉ cuộn khi đổi tuần/ngày, không phải mỗi lần làm mới
            self._days = days
            self.scroll_to_minute(self._first_minute())
            QTimer.singleShot(0, lambda: self.scroll_to_minute(self._first_minute()))

    def _first_minute(self) -> int:
        starts = [p.start for c in self._body.columns for p in c.placements]
        return max(0, (min(starts) if starts else DEFAULT_SCROLL_HOUR * 60) - 30)

    def scroll_to_minute(self, minute: int) -> None:
        self._scroll.verticalScrollBar().setValue(round(minute * HOUR_HEIGHT / 60))

    def scroll_value(self) -> int:
        return self._scroll.verticalScrollBar().value()

    def update_now(self, now: datetime) -> None:
        """Dời vạch 'bây giờ' (gọi định kỳ)."""
        for column in self._body.columns:
            column.set_now(now)

    def column_for(self, day: date) -> DayColumn | None:
        return next((c for c in self._body.columns if c.day == day), None)

    def set_selected_item(self, item_id: int | None) -> None:
        for column in self._body.columns:
            column.set_selected_item(item_id)


class WeekView(TimeGridView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(7, parent)


class DayView(TimeGridView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(1, parent)
