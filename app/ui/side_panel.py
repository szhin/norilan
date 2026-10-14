"""Panel bên phải: danh sách mục (của một ngày hoặc một mục nhanh) và trang chi tiết mục."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QSizePolicy,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.calendar_logic import day_heading, format_minutes, time_text
from app.colors import ColorResolver
from app.files import is_missing
from app.models import Attachment, AttachmentKind, CalendarItem, Task, TaskStatus
from app.ui.dialogs import ATTACHMENT_ICON
from app.ui.item_list import ItemListWidget
from app.ui.theme import repolish
from app.ui.widgets import ElidedButton


_FIELD_ICONS = {
    "Hạn nộp": "🗓", "Trạng thái": "◉", "Ưu tiên": "⚑", "Môn học": "🏷", "Nhắc trước": "⏰",
    "Ghi chú": "≡", "Đính kèm": "📎", "Thời gian": "🕒", "Địa điểm": "📍",
}


class AttachmentRow(QFrame):
    """Một đính kèm trong trang chi tiết: bấm tên để mở, nút 📂 để mở thư mục chứa."""

    open_requested = Signal(int)
    reveal_requested = Signal(int)

    def __init__(self, att: Attachment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Attachment")
        self.attachment_id: int = att.id  # type: ignore[assignment]
        self.missing = is_missing(att)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 4, 3)
        layout.setSpacing(4)

        text = f"{ATTACHMENT_ICON[att.kind]}  {att.name}" + ("  — không tìm thấy" if self.missing else "")
        self.open_button = ElidedButton(text)
        self.open_button.setObjectName("AttachmentOpen")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setProperty("missing", self.missing)
        self.open_button.setToolTip(
            f"Không tìm thấy – bấm để chọn lại đường dẫn\n{att.target}" if self.missing else f"Mở\n{att.target}"
        )
        self.open_button.clicked.connect(lambda: self.open_requested.emit(self.attachment_id))
        layout.addWidget(self.open_button, 1)

        if att.kind != AttachmentKind.URL:
            reveal = QPushButton("📂", objectName="IconButton")
            reveal.setCursor(Qt.CursorShape.PointingHandCursor)
            reveal.setToolTip("Mở thư mục chứa file")
            reveal.clicked.connect(lambda: self.reveal_requested.emit(self.attachment_id))
            layout.addWidget(reveal)


class DetailView(QWidget):
    """Chi tiết một Task/Event, kèm nút Sửa / Xoá / đổi trạng thái và đính kèm bấm để mở."""

    back_requested = Signal()
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    status_requested = Signal(int, object)      # item_id, TaskStatus
    open_attachment_requested = Signal(int)     # attachment_id
    reveal_attachment_requested = Signal(int)   # attachment_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        top = QHBoxLayout()
        back = QPushButton("← Quay lại", objectName="LinkButton")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setToolTip("Quay lại danh sách (Esc)")
        back.clicked.connect(self.back_requested)
        top.addWidget(back)
        top.addStretch(1)
        self.edit_button = QPushButton("✏️ Sửa")
        self.edit_button.setToolTip("Sửa (Ctrl+E)")
        self.edit_button.clicked.connect(lambda: self.item_id and self.edit_requested.emit(self.item_id))
        self.delete_button = QPushButton("🗑", objectName="DangerButton")
        self.delete_button.setToolTip("Xoá (Delete)")
        self.delete_button.clicked.connect(lambda: self.item_id and self.delete_requested.emit(self.item_id))
        top.addWidget(self.edit_button)
        top.addWidget(self.delete_button)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 4, 6, 0)
        self._body.setSpacing(10)

        self._pill = QLabel(objectName="Pill")
        self._title = QLabel(objectName="DetailTitle")
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignLeft)
        self._body.addWidget(self._title)

        form_host = QWidget()
        self._form = QFormLayout(form_host)
        self._form.setContentsMargins(0, 6, 0, 0)
        self._form.setHorizontalSpacing(14)
        self._form.setVerticalSpacing(10)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self._body.addWidget(form_host)
        self._body.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.attachment_rows: list[AttachmentRow] = []

    def show_item(self, item: CalendarItem, now: datetime, colors: ColorResolver | None = None) -> None:
        colors = colors or ColorResolver()
        self.item_id = item.id
        self._pill.setProperty("tone", colors.tone(item))
        self._pill.setText("Bài tập" if isinstance(item, Task) else "Sự kiện")
        repolish(self._pill)
        self._title.setText(item.title)
        self.attachment_rows = []
        while self._form.rowCount():
            self._form.removeRow(0)

        if isinstance(item, Task):
            self._row("Hạn nộp", f"{item.due_at:%H:%M} · {day_heading(item.due_at.date())}",
                      state="overdue" if item.is_overdue(now) else None)
            self._form.addRow(self._label("Trạng thái"), self._status_box(item))
            self._row("Ưu tiên", item.priority.label)
            if item.subject:
                pill = QLabel(item.subject, objectName="Pill")
                pill.setProperty("tone", colors.subject_tone(item.subject))
                pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self._form.addRow(self._label("Môn học"), pill)
            else:
                self._row("Môn học", "—")
            self._row("Nhắc trước", format_minutes(item.remind_before_minutes))
            self._row("Ghi chú", item.notes or "—")
            self._form.addRow(self._label("Đính kèm"), self._attachments(item))
        else:
            self._row("Thời gian", f"{time_text(item)} · {day_heading(item.starts_at.date())}")
            self._row("Địa điểm", item.location or "—")
            self._row("Nhắc trước", format_minutes(item.remind_before_minutes))
            self._row("Ghi chú", item.notes or "—")

    def field_values(self) -> dict[str, str]:
        """{nhãn: giá trị} của các dòng chữ đang hiển thị (dùng cho test)."""
        values: dict[str, str] = {}
        for row in range(self._form.rowCount()):
            label = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
            field = self._form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget()
            if isinstance(label, QLabel) and isinstance(field, QLabel):
                values[label.property("field")] = field.text()
        return values

    # --- dựng từng phần
    def _label(self, text: str) -> QLabel:
        """Nhãn thuộc tính có biểu tượng phía trước, như bảng thuộc tính của Notion."""
        label = QLabel(f"{_FIELD_ICONS.get(text, '•')}   {text}", objectName="FieldLabel")
        label.setProperty("field", text)
        return label

    def _row(self, label: str, value: str, *, state: str | None = None) -> None:
        field = QLabel(value, objectName="FieldValue")
        field.setWordWrap(True)
        field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if state:
            field.setProperty("state", state)
        self._form.addRow(self._label(label), field)

    def _status_box(self, task: Task) -> QWidget:
        box = QComboBox()
        for status in TaskStatus:
            box.addItem(status.label, status)
        box.setCurrentIndex(box.findData(task.status))
        task_id = task.id
        box.currentIndexChanged.connect(lambda _: self.status_requested.emit(task_id, box.currentData()))
        self.status_box = box
        return box

    def _attachments(self, task: Task) -> QWidget:
        if not task.attachments:
            return QLabel("Chưa có đính kèm", objectName="Muted")
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for att in task.attachments:
            row = AttachmentRow(att)
            row.open_requested.connect(self.open_attachment_requested)
            row.reveal_requested.connect(self.reveal_attachment_requested)
            layout.addWidget(row)
            self.attachment_rows.append(row)
        return box


class SidePanel(QFrame):
    """Trang 0: danh sách; trang 1: chi tiết."""

    item_clicked = Signal(int)
    item_context = Signal(int, QPoint)
    status_filter_changed = Signal(object)  # TaskStatus | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 12, 12)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        list_page = QWidget()
        lp = QVBoxLayout(list_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self._title = QLabel(objectName="PanelTitle")
        self._title.setWordWrap(True)
        lp.addWidget(self._title)
        self._subtitle = QLabel(objectName="PanelSubtitle")
        self._subtitle.setWordWrap(True)
        lp.addWidget(self._subtitle)
        # Bộ lọc trạng thái – chỉ hiện khi đang tìm kiếm.
        self.filter_row = QWidget(objectName="FilterRow")
        fr = QHBoxLayout(self.filter_row)
        fr.setContentsMargins(0, 6, 0, 0)
        fr.addWidget(QLabel("Trạng thái", objectName="Muted"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("Tất cả", None)
        for status in TaskStatus:
            self.status_filter.addItem(status.label, status)
        self.status_filter.currentIndexChanged.connect(
            lambda _: self.status_filter_changed.emit(self.status_filter.currentData()))
        fr.addWidget(self.status_filter, 1)
        self.filter_row.hide()
        lp.addWidget(self.filter_row)
        lp.addSpacing(8)
        self.list = ItemListWidget()
        self.list.item_clicked.connect(self.item_clicked)
        self.list.item_context.connect(self.item_context)
        lp.addWidget(self.list, 1)
        self._stack.addWidget(list_page)

        self.detail = DetailView()
        self.detail.back_requested.connect(self.show_list_page)
        self._stack.addWidget(self.detail)

    # --- API
    def show_items(
        self, title: str, subtitle: str, items: list[CalendarItem], *,
        now: datetime, today: date, group_by_day: bool = False, empty_text: str = "Không có mục nào.",
        colors: ColorResolver | None = None,
    ) -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self.list.set_items(items, now=now, today=today, group_by_day=group_by_day,
                            empty_text=empty_text, colors=colors)
        self.show_list_page()

    def show_detail(self, item: CalendarItem, now: datetime, colors: ColorResolver | None = None) -> None:
        self.detail.show_item(item, now, colors)
        self.list.set_selected(item.id)
        self._stack.setCurrentWidget(self.detail)

    def show_list_page(self) -> None:
        self.list.set_selected(None)
        self._stack.setCurrentIndex(0)

    def set_filter_visible(self, visible: bool) -> None:
        self.filter_row.setVisible(visible)

    def reset_filter(self) -> None:
        self.status_filter.blockSignals(True)
        self.status_filter.setCurrentIndex(0)
        self.status_filter.blockSignals(False)

    def subtitle(self) -> str:
        return self._subtitle.text()

    def is_showing_detail(self) -> bool:
        return self._stack.currentWidget() is self.detail

    def detail_item_id(self) -> int | None:
        return self.detail.item_id if self.is_showing_detail() else None

    def title(self) -> str:
        return self._title.text()
